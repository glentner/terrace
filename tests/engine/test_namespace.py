# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the engine namespace: lazy population and journaled namespace ops."""

# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.state.lifecycle import Marker, CrashDetected
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


def test_start_attaches_and_populates_root(engine: OverlayEngine) -> None:
    """Markers are written, the root entry exists, and the lifecycle flag reads mounted."""
    for spec in engine.spec.tiers:
        marker = Marker.read(spec.path)
        assert marker is not None and marker.tier == spec.name
    assert engine.catalog.get('/').kind == 'dir'
    assert engine.lifecycle.load().shutdown_state == 'mounted'


def test_stop_and_restart(tmp_path: Path, clock: Clock) -> None:
    """A clean stop settles the flag; a restart bumps the generation; a crash is detected."""
    seed(tmp_path / 'project', 'top.txt', b'data')
    spec = build_spec(tmp_path)
    with OverlayEngine(spec, clock=clock) as engine:
        first = engine.overlay.generation
    with OverlayEngine(spec, clock=clock) as engine:
        assert engine.overlay.generation == first + 1
        engine.lifecycle.end_mount('clean')                 # fake a death: leave 'mounted' behind
        engine.lifecycle.begin_mount('elsewhere', now=clock())
        engine.transfers.stop()
        engine.db.close()
        engine.started = False
    with raises(CrashDetected):
        OverlayEngine(spec, clock=clock).start()


def test_getattr_lazy_adoption(engine: OverlayEngine) -> None:
    """First touch pulls a source file into the catalog with its source residency."""
    entry = engine.getattr('/data/a.txt')
    assert (entry.kind, entry.size, entry.mode) == ('file', 5, 0o640)
    residency = engine.catalog.get_residency('/data/a.txt', 'project')
    assert residency is not None and residency.state == 'clean'
    with raises(FileNotFoundError):
        engine.getattr('/data/missing.txt')


def test_readdir_merges_and_marks_listed(engine: OverlayEngine) -> None:
    """readdir populates children once; afterward the catalog is authoritative."""
    names = [entry.path for entry in engine.readdir('/data')]
    assert names == ['/data/a.txt', '/data/b.txt']
    seed(Path(engine.spec.tiers[0].path), 'data/late.txt', b'sneaky')
    assert [entry.path for entry in engine.readdir('/data')] == names
    with raises(FileNotFoundError):
        engine.getattr('/data/late.txt')                    # authoritative miss: parent is listed


def test_readdir_of_file_fails(engine: OverlayEngine) -> None:
    """ENOTDIR surfaces naturally."""
    with raises(NotADirectoryError):
        engine.readdir('/top.txt')


def test_mkdir_queues_op(engine: OverlayEngine) -> None:
    """mkdir is durable in the catalog and queued toward the source; not yet physical there."""
    entry = engine.mkdir('/results', mode=0o750)
    assert (entry.kind, entry.mode, entry.listed) == ('dir', 0o750, True)
    assert [(row.op, row.to_tier) for row in engine.journal.queued()] == [('mkdir', 'project')]
    assert engine.source.stat(PurePosixPath('/results')) is None
    with raises(FileExistsError):
        engine.mkdir('/results')
    with raises(FileNotFoundError):
        engine.mkdir('/no/parent')


def test_create_lands_on_fastest_tier_dirty(engine: OverlayEngine) -> None:
    """create places an empty file on the fastest tier, dirty, with a queued data op."""
    engine.mkdir('/results')
    entry = engine.create('/results/out.dat', mode=0o600)
    assert entry.kind == 'file' and entry.size == 0
    residency = engine.catalog.get_residency('/results/out.dat', 'scratch')
    assert residency is not None and residency.state == 'dirty'
    assert engine.tiers['scratch'].stat(PurePosixPath('/results/out.dat')).mode == 0o600
    assert engine.source.stat(PurePosixPath('/results/out.dat')) is None
    ops = [(row.op, row.to_tier) for row in engine.journal.queued()]
    assert ('data', 'project') in ops
    assert engine.journal.pending() == []                   # intents all confirmed
    with raises(FileExistsError):
        engine.create('/results/out.dat')


def test_unlink_source_file_tombstones(engine: OverlayEngine) -> None:
    """Unlinking an adopted file masks it immediately; the source copy waits on the queue."""
    engine.getattr('/top.txt')
    engine.unlink('/top.txt')
    with raises(FileNotFoundError):
        engine.getattr('/top.txt')
    assert '/top.txt' not in [entry.path for entry in engine.readdir('/')]
    assert engine.source.stat(PurePosixPath('/top.txt')) is not None
    assert [(row.op, row.to_tier) for row in engine.journal.queued()] == [('unlink', 'project')]


def test_unlink_local_file_purges(engine: OverlayEngine) -> None:
    """Unlinking a never-committed file leaves no trace — bytes, rows, or queued ops."""
    engine.mkdir('/results')
    engine.create('/results/tmp.dat')
    engine.unlink('/results/tmp.dat')
    assert engine.tiers['scratch'].stat(PurePosixPath('/results/tmp.dat')) is None
    assert engine.catalog.get('/results/tmp.dat', include_deleted=True) is None
    assert [(row.op, ) for row in engine.journal.queued()] == [('mkdir', )]
    assert engine.registry.usage('scratch').files == 0


def test_rmdir(engine: OverlayEngine) -> None:
    """rmdir refuses non-empty; a local-only dir purges outright with its queued mkdir."""
    engine.mkdir('/results')
    engine.rmdir('/results')
    assert engine.journal.queued() == []                    # mkdir + rmdir cancel out
    with raises(OSError):
        engine.rmdir('/data')                               # ENOTEMPTY
    with raises(NotADirectoryError):
        engine.rmdir('/top.txt')


def test_rmdir_source_dir_tombstones(engine: OverlayEngine) -> None:
    """An empty source directory is masked now and queued for removal."""
    seed(Path(engine.spec.tiers[0].path), 'empty/.keep', b'')
    engine.unlink('/empty/.keep')
    engine.rmdir('/empty')
    with raises(FileNotFoundError):
        engine.getattr('/empty')
    ops = [row.op for row in engine.journal.queued()]
    assert ops == ['unlink', 'rmdir']


def test_rename_file_moves_staging_copy(engine: OverlayEngine) -> None:
    """A renamed created file moves physically on staging and queues one rename op."""
    engine.create('/a.dat')
    engine.rename('/a.dat', '/b.dat')
    assert engine.tiers['scratch'].stat(PurePosixPath('/a.dat')) is None
    assert engine.tiers['scratch'].stat(PurePosixPath('/b.dat')) is not None
    with raises(FileNotFoundError):
        engine.getattr('/a.dat')
    assert engine.getattr('/b.dat').kind == 'file'
    ops = [(row.op, row.detail) for row in engine.journal.queued() if row.op == 'rename']
    # `path` (the queue-time location) is recorded so the op replays historically at flush.
    assert ops == [('rename', {'old': '/a.dat', 'new': '/b.dat', 'path': '/b.dat'})]


def test_flush_replays_renames_at_historical_paths(engine: OverlayEngine) -> None:
    """
    A rename chain that reuses a directory name must replay on the source at the paths each op
    was queued against, not the entry's final path — otherwise the flush reconstructs a wrong
    tree (or attempts a move-into-self). Regression for the write-back replay fix.
    """
    engine.mkdir('/d1')
    engine.rename('/d1', '/d2')
    engine.mkdir('/d1')                                      # reuse the freed name for a new entry
    engine.rename('/d2', '/d1/s1')
    engine.flush()
    source = engine.tiers['project']
    assert source.stat(PurePosixPath('/d1')).kind == 'dir'
    assert source.stat(PurePosixPath('/d1/s1')).kind == 'dir'
    assert source.stat(PurePosixPath('/d2')) is None
    assert engine.journal.queued() == [] and engine.journal.pending() == []


def test_rename_replaces_live_file(engine: OverlayEngine) -> None:
    """rename over an existing file replaces it (the queued source rename overwrites)."""
    engine.getattr('/top.txt')
    engine.create('/new.dat')
    engine.rename('/new.dat', '/top.txt')
    entry = engine.getattr('/top.txt')
    assert entry.size == 0                                  # the replacement, not the old content
    assert engine.journal.pending() == []


def test_rename_directory_subtree(engine: OverlayEngine) -> None:
    """Renaming a directory rewrites the cataloged subtree and keeps content reachable."""
    engine.readdir('/data')
    engine.rename('/data', '/moved')
    assert [entry.path for entry in engine.readdir('/moved')] == ['/moved/a.txt', '/moved/b.txt']
    with raises(FileNotFoundError):
        engine.getattr('/data')
    assert engine.getattr('/moved/a.txt').size == 5


def test_rename_into_own_subtree_refused(engine: OverlayEngine) -> None:
    """EINVAL for moving a directory into itself."""
    engine.mkdir('/d')
    with raises(OSError):
        engine.rename('/d', '/d/child')


def test_create_over_tombstone_vacates(engine: OverlayEngine) -> None:
    """Re-creating a deleted path executes the pending source removal and starts fresh."""
    engine.getattr('/top.txt')
    engine.unlink('/top.txt')
    entry = engine.create('/top.txt', mode=0o600)
    assert entry.size == 0 and entry.mode == 0o600
    assert engine.source.stat(PurePosixPath('/top.txt')) is None    # vacate removed the old copy
    ops = [row.op for row in engine.journal.queued()]
    assert ops == ['data']                                  # the stale unlink is gone
    assert engine.journal.pending() == []
