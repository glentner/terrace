# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Regression tests for write-back namespace-replay under deferred path mutation
(PLAN.md -> Write-back -> Deferred-op replay).

The write-back queue is entry-keyed and the source lags the catalog while a rename is queued
but uncommitted, so the source physically holds an entry's bytes at a path that differs from
its logical one. Each entry tracks that committed source path; deferred removals and renames
resolve against it, and a replayed op resolves the entry's current place rather than the paths
recorded when it was queued. The classes guarded here:

* removing or superseding an entry that still carries an uncommitted rename removes or moves the
  real source object instead of orphaning it at the pre-rename path;
* committing an entry's data to the source while its rename, or its directory's, is queued writes
  where the source already holds the entry (else at its target), so the queued rename stays the one
  thing that moves it: by a direct commit, `fsync` under the commit policy, the eviction manager, and
  a scheduler poll that reaches the data row first; a file created straight on the source inside a
  renamed directory lands at its target the same way;
* `flush` converges when later ops reused or removed a path an earlier queued op names (a
  directory moved out and back, a child moved out of a removed parent, a name reused by a file,
  a moved-out child removed after its old parent, whose queued removal runs first);
* one pass of `flush`, `drain` or a scheduler poll drains entries moved out of nested directories, or of sibling
  ones, five hundred chains side by side included, and removed after them: each row runs once, a directory's
  removal after the entries its object still holds (a removal queued while the replay runs included, under an id
  it reused too), also from a queue a restarted engine reads and for a directory a rename replaces, and a file
  the catalog never saw still stops a directory's removal with ENOTEMPTY. The replay a new name triggers runs
  the rows up to the last its name needs, at the outer directory's name or the moved entry's, beside the
  neighbouring shapes too; a row that waited inside it past that one stays queued, and one flush then drains it;
* a pass over namespace rows nothing stands in (no entry held where an `rmdir` or a rename needs a place) reads
  the queue a fixed number of times, however many rows it runs;
* re-creating a name, or renaming onto it, first runs the queued rows that still occupy its place
  on the source (a child's queued removal, another entry's queued move or removal);
* a scheduler snapshot made stale by a user op (its row run, cancelled, or its id reused by a newer
  row) changes nothing;
* a replay settles only the row it read: a removal landing during the act, which cancels that row and
  queues its own op under the same id, keeps its op queued, whether the scheduler, `flush`, a drain or
  a vacate ran the replay, and so does a removal landing while `flush` commits another file;
* a file whose bytes sit somewhere other than its logical path (created straight on the source inside
  a renamed directory) reports the size its object holds, while open, after `release` and after the
  queue drains;
* a file served off the source while its rename, or an ancestor's, is queued is cut by `truncate`
  without a handle where the source holds it, and reports its object's size when written through a
  handle on a stack whose stage it outgrows (`bypass_size`). The cut holds the source lock, whatever
  copy serves, and decides under it the entry at its path, its kind and the serving copy, which may be
  a faster one taken during the wait; it refuses a non-file or another entry found at the path, and
  never follows a symbolic link at the file itself (the last path component). A directory rename holds
  the source lock from its `_vacate` through its catalog move, so a cut of a file below it that the
  rename, stopped past its `_vacate`, would otherwise land inside (at its serving decision, a promotion
  and its copy, its dirty mark, its open or its recording) either raises ENOENT having cut nothing or
  records its cut where the rename moves the file, with every stage's counter agreeing; a rename that
  reaches the lock while the cut holds it waits; the rename moves its stage copies after releasing it;
* a file served off the source whose object a replay moves after `open` found its entry is opened, or
  promoted to a faster tier, where the replay left the object. The open of a copy served off the source and
  a promotion off the source look the entry up again under the source lock and open the object before
  releasing it, so neither reads the old place or another entry's object moved onto it. The lookup under the
  source lock raises ENOENT when the path then holds no entry, another entry, or a non-file; a rename or
  removal landing before it is otherwise outside what `open` guarantees, since its path lock does not cover
  an ancestor. A copy served off a stage, and a promotion from one, take no source lock;
* a file that took a purged entry's id (ids are reused) and was carried onto the path while the handle-less
  `truncate`, a source-served `open` or a promotion off the source waited for the source lock is refused with
  ENOENT, served off the source or off a stage, whether the entry was purged by a replay or by its own removal,
  and keeps its bytes; a purge of another entry, or a create that takes another purged id, refuses none of them;
  a file that took the id before the cut's first lookup read the path is the file the cut names;
* a rename row's tombstone purge leaves another entry's tombstone and queued removal alone;
* a directory whose target lies inside its own source object moves only once the entries placed
  there have moved out; directory renames that move into or out of each other's subtrees drain in the first
  pass, a rename reached inside the replay of one it needs waiting for it, and a user rename or mkdir that
  meets them succeeds.

The newer tests list the whole tree before acting and end in `assert_converged`: the queue drained,
the source tree equal to the overlay view, every reported size equal to its file's byte count, and a
fresh engine reading the same view.
"""

# Type annotations
from __future__ import annotations

# Standard libs
import os
import time
import errno
import threading
import dataclasses
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.tier.spec import QuotaSpec, WritebackSpec
from sosfs.engine.drain import drain
from sosfs.engine.evict import EvictionManager
from sosfs.engine.overlay import OverlayEngine
from sosfs.engine.writeback import WritebackScheduler
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


def test_unlink_of_pending_rename_removes_source_copy(engine: OverlayEngine) -> None:
    """Unlinking a file whose deferred rename has not committed removes its source copy, no orphan."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'payload')
    engine.getattr('/x.dat')                                 # adopt the source file
    engine.rename('/x.dat', '/y.dat')                        # deferred: the source still holds /x.dat
    engine.unlink('/y.dat')                                  # removes the entry that owns the pending rename
    engine.flush()
    assert not (root / 'y.dat').exists()
    assert not (root / 'x.dat').exists()                     # the pre-rename source copy is removed, not orphaned
    assert engine.journal.queued() == [] and engine.journal.pending() == []


def test_rmdir_of_pending_rename_removes_source_dir(engine: OverlayEngine) -> None:
    """rmdir of a directory whose deferred rename has not committed removes the source dir, no orphan."""
    root = Path(engine.spec.tiers[0].path)
    (root / 'd1').mkdir()                                    # an empty source directory
    engine.getattr('/d1')                                    # adopt it
    engine.rename('/d1', '/d2')                              # deferred: the source still holds /d1
    engine.rmdir('/d2')
    engine.flush()
    assert not (root / 'd2').exists()
    assert not (root / 'd1').exists()                        # the pre-rename source dir is removed, not orphaned
    assert engine.journal.queued() == [] and engine.journal.pending() == []


def test_rename_over_pending_target_removes_stale_source_copy(engine: OverlayEngine) -> None:
    """Renaming over a target that itself holds a pending rename removes the target's stale source copy."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'a.dat', b'aaa')
    seed(root, 'b.dat', b'bbb')
    engine.getattr('/a.dat')
    engine.getattr('/b.dat')
    engine.rename('/a.dat', '/c.dat')                        # deferred: the source still holds /a.dat
    engine.rename('/b.dat', '/c.dat')                        # supersedes the pending /c.dat target
    engine.flush()
    assert not (root / 'a.dat').exists()                     # the superseded source copy is removed, not orphaned
    assert not (root / 'b.dat').exists()
    assert (root / 'c.dat').read_bytes() == b'bbb'           # last writer wins
    assert engine.journal.queued() == [] and engine.journal.pending() == []


def test_double_rename_then_unlink_removes_source_copy(engine: OverlayEngine) -> None:
    """Chaining two deferred renames then unlinking removes the original source copy, no orphan."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'a.dat', b'aaa')
    engine.getattr('/a.dat')
    engine.rename('/a.dat', '/b.dat')                        # deferred: the source still holds /a.dat
    engine.rename('/b.dat', '/c.dat')                        # deferred again; both ops key the same entry
    engine.unlink('/c.dat')                                  # cancels both renames
    engine.flush()
    assert not (root / 'a.dat').exists()                     # the original source copy is removed, not orphaned
    assert not (root / 'b.dat').exists()
    assert not (root / 'c.dat').exists()
    assert engine.journal.queued() == [] and engine.journal.pending() == []


# -- convergence helpers --------------------------------------------------------------------------------------

def tree(root: Path) -> dict:
    """{relative path: bytes | 'DIR'} for a tier root, blind to '.sosfs'."""
    found = {}
    for item in sorted(root.rglob('*')):
        relative = item.relative_to(root).as_posix()
        if relative.split('/')[0] == '.sosfs':
            continue
        found[relative] = 'DIR' if item.is_dir() else item.read_bytes()
    return found


def view(engine: OverlayEngine, path: str = '/') -> dict:
    """The same shape read through the overlay (readdir, open, read)."""
    found = {}
    for entry in engine.readdir(path):
        relative = entry.path.lstrip('/')
        if entry.kind == 'dir':
            found[relative] = 'DIR'
            found.update(view(engine, entry.path))
        else:
            handle = engine.open(entry.path)
            try:
                found[relative] = engine.read(handle, 1 << 20, 0)
            finally:
                engine.release(handle)
    return found


def sizes(engine: OverlayEngine, path: str = '/') -> dict:
    """{relative path: every size reported for the file} -- `getattr`'s and each residency row's."""
    found = {}
    for entry in engine.readdir(path):
        if entry.kind == 'dir':
            found.update(sizes(engine, entry.path))
        else:
            reported = {engine.getattr(entry.path).size}
            reported |= {residency.size for residency in engine.catalog.residencies(entry.path)}
            found[entry.path.lstrip('/')] = reported
    return found


def walk(engine: OverlayEngine, path: str = '/') -> None:
    """List every directory, so the catalog knows each source object and no later `readdir` adopts one."""
    for entry in engine.readdir(path):
        if entry.kind == 'dir':
            walk(engine, entry.path)


def assert_converged(engine: OverlayEngine) -> None:
    """
    Queue drained; the catalog's live entries, the source tree and the overlay view are the same set of
    paths, with the same bytes; every size `getattr` and the residency rows report is the file's byte count;
    a fresh engine over the same roots reads that view, sizes included. The expected set comes
    from the catalog before any `readdir`, and every live directory must already be listed, because listing
    an unlisted directory adopts whatever the source holds there: an orphan would join the very view it is
    compared against.
    """
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    unlisted = [row['path'] for row in engine.db.execute(
        t"SELECT path FROM entry WHERE kind = 'dir' AND deleted = 0 AND listed = 0")]
    assert unlisted == [], f'unlisted directories {unlisted}: walk the tree before acting'
    live = {row['path'].lstrip('/') for row in engine.db.execute(
        t"SELECT path FROM entry WHERE deleted = 0 AND path != '/'")}
    source = tree(Path(engine.spec.tiers[0].path))
    assert set(source) == live
    expected = view(engine)
    assert source == expected
    counts = {path: {len(data)} for path, data in expected.items() if data != 'DIR'}
    assert sizes(engine) == counts
    spec, clock = engine.spec, engine.clock
    engine.stop()
    again = OverlayEngine(spec, clock=clock)
    again.start()
    try:
        assert view(again) == expected
        assert sizes(again) == counts
    finally:
        again.stop()


def rewrite(engine: OverlayEngine, path: str, data: bytes) -> None:
    """Replace the contents of `path` through a handle."""
    handle = engine.open(path, os.O_RDWR | os.O_TRUNC)
    try:
        engine.write(handle, data, 0)
    finally:
        engine.release(handle)


def directory_sequence(engine: OverlayEngine, ops: list) -> None:
    """Run engine calls given as (method name, *arguments)."""
    for name, *args in ops:
        getattr(engine, name)(*args)


def unlink_during_next(engine: OverlayEngine, method: str, path: str) -> list:
    """
    Make the source tier's next `method` call run a user `unlink` of `path` right after the real call: a
    FUSE thread's unlink, which takes no source lock, landing inside a replay's act or a commit's copy.
    Returns the queue as (id, op) pairs, filled in as the unlink left it.
    """
    source = engine.source
    real = getattr(source, method)
    seen = []

    def interleaved(*args, **kwargs):
        result = real(*args, **kwargs)
        delattr(source, method)                              # once: the tier's own method again
        engine.unlink(path)
        seen.extend((row.id, row.op) for row in engine.journal.queued())
        return result

    setattr(source, method, interleaved)
    return seen


# -- a data commit during the lag lands where the source holds the entry --------------------------------------

def test_commit_before_queued_rename_leaves_one_object(engine: OverlayEngine) -> None:
    """A commit while the entry's rename is queued writes at the pre-rename place; the rename then moves it."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    rewrite(engine, '/x.dat', b'NEWDATA!')
    engine.rename('/x.dat', '/y.dat')                        # deferred: the source still holds /x.dat
    engine.commit_upstream('/y.dat')
    assert tree(root)['x.dat'] == b'NEWDATA!' and 'y.dat' not in tree(root)
    assert [row.op for row in engine.journal.queued()] == ['rename']   # still the only mover
    engine.flush()
    assert tree(root)['y.dat'] == b'NEWDATA!' and 'x.dat' not in tree(root)
    assert_converged(engine)


def test_fsync_commit_policy_before_queued_rename(tmp_path: Path, clock: Clock) -> None:
    """`fsync` under the commit policy, with the entry's rename queued, commits to the pre-rename place."""
    root = tmp_path / 'project'
    seed(root, 'x.dat', b'original')
    engine = OverlayEngine(dataclasses.replace(build_spec(tmp_path, tiers=2), fsync_policy='commit'), clock=clock)
    engine.start()
    walk(engine)
    handle = engine.open('/x.dat', os.O_RDWR)
    engine.write(handle, b'NEWDATA!', 0)
    engine.rename('/x.dat', '/y.dat')
    engine.fsync(handle)
    engine.release(handle)
    assert tree(root) == {'x.dat': b'NEWDATA!'}
    engine.flush()
    assert tree(root) == {'y.dat': b'NEWDATA!'}
    assert_converged(engine)


def test_eviction_commit_before_queued_rename(tmp_path: Path, clock: Clock) -> None:
    """The eviction manager committing a dirty copy whose rename is queued commits to the pre-rename place."""
    root = tmp_path / 'project'
    seed(root, 'x.dat', b'original')
    engine = OverlayEngine(build_spec(tmp_path, tiers=2, scratch_quota=QuotaSpec(size=9)), clock=clock)
    engine.start()
    walk(engine)
    rewrite(engine, '/x.dat', b'NEWDATA!!')                  # 9 of 9 bytes: above the high watermark
    engine.rename('/x.dat', '/y.dat')
    assert EvictionManager(engine).poll() == 1
    assert tree(root) == {'x.dat': b'NEWDATA!!'}
    engine.flush()
    assert tree(root) == {'y.dat': b'NEWDATA!!'}
    assert_converged(engine)


def test_scheduler_poll_settles_data_before_rename(engine: OverlayEngine, clock: Clock) -> None:
    """A poll that reaches the data row before the rename row commits to the pre-rename place."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    rewrite(engine, '/x.dat', b'NEWDATA!')
    clock.now += 30
    engine.rename('/x.dat', '/y.dat')
    scheduler = WritebackScheduler(engine)
    clock.now += 40                                          # the data row is due, the rename row is not
    assert [row.op for row in scheduler.due(clock.now)] == ['data']
    scheduler.poll(clock.now)
    assert tree(root)['x.dat'] == b'NEWDATA!' and 'y.dat' not in tree(root)
    clock.now += 60
    scheduler.poll(clock.now)
    assert tree(root)['y.dat'] == b'NEWDATA!' and 'x.dat' not in tree(root)
    assert_converged(engine)


def test_commit_inside_renamed_directory(engine: OverlayEngine) -> None:
    """Commits inside a directory whose rename is queued land under its old place; the rename moves them all."""
    root = Path(engine.spec.tiers[0].path)
    walk(engine)
    engine.rename('/data', '/moved')                         # deferred: the source still holds /data
    rewrite(engine, '/moved/a.txt', b'ALPHA')
    engine.commit_upstream('/moved/a.txt')
    engine.create('/moved/c.txt')                            # new: no source place of its own yet
    rewrite(engine, '/moved/c.txt', b'GAMMA')
    engine.commit_upstream('/moved/c.txt')
    assert tree(root)['data/a.txt'] == b'ALPHA' and tree(root)['data/c.txt'] == b'GAMMA'
    assert 'moved' not in tree(root)
    assert view(engine)['moved/b.txt'] == b'beta-content'    # a sibling reads through the overlay meanwhile
    engine.flush()
    assert {k: v for k, v in tree(root).items() if k.startswith(('data', 'moved'))} == {
        'moved': 'DIR', 'moved/a.txt': b'ALPHA', 'moved/b.txt': b'beta-content', 'moved/c.txt': b'GAMMA'}
    assert_converged(engine)


def test_create_on_the_source_inside_renamed_directory(tmp_path: Path, clock: Clock) -> None:
    """A file created straight on the source inside a directory whose rename is queued lands under its old place."""
    root = tmp_path / 'project'
    seed(root, 'data/a.txt', b'alpha')
    engine = OverlayEngine(build_spec(tmp_path, tiers=1), clock=clock)
    engine.start()
    walk(engine)
    engine.rename('/data', '/moved')                         # deferred: the source still holds /data
    engine.create('/moved/c.txt')                            # no stage tier: the file goes straight to the source
    rewrite(engine, '/moved/c.txt', b'GAMMA')
    assert tree(root) == {'data': 'DIR', 'data/a.txt': b'alpha', 'data/c.txt': b'GAMMA'}
    engine.flush()
    assert tree(root) == {'moved': 'DIR', 'moved/a.txt': b'alpha', 'moved/c.txt': b'GAMMA'}
    assert_converged(engine)


def test_size_of_a_file_created_on_the_source_inside_renamed_directory(tmp_path: Path, clock: Clock) -> None:
    """A file created straight on the source under a queued directory rename reports its object's size throughout."""
    root = tmp_path / 'project'
    seed(root, 'data/a.txt', b'alpha')
    engine = OverlayEngine(build_spec(tmp_path, tiers=1), clock=clock)
    engine.start()
    walk(engine)
    engine.rename('/data', '/moved')                         # deferred: the source still holds /data
    engine.create('/moved/c.txt')                            # its bytes sit under /data, not at its logical path
    handle = engine.open('/moved/c.txt', os.O_WRONLY)
    engine.write(handle, b'GAMMA', 0)
    assert engine.getattr('/moved/c.txt').size == 5          # read live while the handle is open
    engine.release(handle)
    assert engine.getattr('/moved/c.txt').size == 5          # recorded at release
    assert engine.catalog.get_residency('/moved/c.txt', engine.source.name).size == 5
    engine.flush()
    assert engine.getattr('/moved/c.txt').size == 5
    assert_converged(engine)


# -- flush converges over reused and removed paths ------------------------------------------------------------

def test_flush_after_directory_round_trip_and_reuse(engine: OverlayEngine) -> None:
    """A directory moved into another and back, its old parent removed and the name reused by a file."""
    walk(engine)
    directory_sequence(engine, [('mkdir', '/d1'), ('mkdir', '/d2'), ('rename', '/d2', '/d1/s1'),
                                ('rename', '/d1/s1', '/d2'), ('rmdir', '/d1'), ('create', '/b.dat'),
                                ('rename', '/b.dat', '/d1')])
    engine.flush()                                           # replaying the recorded paths raised EACCES
    assert_converged(engine)


def test_flush_after_directory_round_trip(engine: OverlayEngine) -> None:
    """A directory moved into another and back, then its old parent removed: no stray parent stays."""
    walk(engine)
    directory_sequence(engine, [('mkdir', '/d1'), ('mkdir', '/d2'), ('rename', '/d2', '/d1/s1'),
                                ('rename', '/d1/s1', '/d2'), ('rmdir', '/d1')])
    engine.flush()
    assert 'd1' not in tree(Path(engine.spec.tiers[0].path))
    assert_converged(engine)


def test_flush_after_child_moved_out_of_removed_parent(engine: OverlayEngine) -> None:
    """A child moved out of a directory that is then removed: no stray parent stays."""
    walk(engine)
    directory_sequence(engine, [('mkdir', '/d1'), ('mkdir', '/d1/s1'), ('rename', '/d1/s1', '/d2'),
                                ('rmdir', '/d1')])
    engine.flush()
    assert 'd1' not in tree(Path(engine.spec.tiers[0].path))
    assert_converged(engine)


def test_flush_removes_a_moved_out_child_before_its_old_parent(engine: OverlayEngine) -> None:
    """A child moved out of a directory that is then removed, and the child removed: its object leaves first."""
    walk(engine)
    directory_sequence(engine, [('mkdir', '/d2'), ('create', '/d2/a.dat'), ('flush',),
                                ('rename', '/d2/a.dat', '/a.dat'), ('rmdir', '/d2'), ('unlink', '/a.dat')])
    assert [row.op for row in engine.journal.queued()] == ['rmdir', 'unlink']   # the removal queued last
    engine.flush()                                           # the rmdir row ran first and raised ENOTEMPTY
    assert 'd2' not in tree(Path(engine.spec.tiers[0].path))
    assert_converged(engine)


# -- nested directories removed after a move-out drain in one pass --------------------------------------------

def nested_removal(tmp_path: Path, clock: Clock, *, depth: int = 2, moved: str = 'file', origin: str = 'adopted',
                   tiers: int = 1, order: str = 'after', **tier_kwargs) -> tuple[OverlayEngine, Path]:
    """
    A chain of `depth` nested directories, /d1 down, with entries below it, each moved out to the root; then the
    directories removed innermost first, and the moved entries removed `after` them, `before` them, `between` the
    innermost and the rest, or `kept`. `moved` is a 'file' or an empty 'dir' in the innermost directory, a 'tree'
    (a directory holding a file) there, 'levels' (a file there and one in /d1), or 'inplace' (a file there, removed
    where it is). `origin` puts the chain and its entries on the source by adoption or by a flush; 'unplaced'
    adopts the chain and leaves the entries on a stage, never flushed. The same calls run with plain `os` on a copy
    of the starting tree. Returns the started engine, every directory listed, and that copy's root.
    """
    chain = ['/' + '/'.join(f'd{level}' for level in range(1, top + 1)) for top in range(1, depth + 1)]
    inner = chain[-1]
    entries = {'file': [(inner + '/m.dat', 'file')], 'dir': [(inner + '/m', 'dir')], 'tree': [(inner + '/m', 'tree')],
               'levels': [(inner + '/m.dat', 'file'), ('/d1/n.dat', 'file')],
               'inplace': [(inner + '/m.dat', 'file')]}[moved]
    files = [(path, b'moved:' + path.encode()) for path, kind in entries if kind == 'file']
    files += [(path + '/f.dat', b'inside:' + path.encode()) for path, kind in entries if kind == 'tree']
    folders = chain + [path for path, kind in entries if kind != 'file']
    oracle = tmp_path / 'oracle'
    for path in folders:
        (oracle / path.lstrip('/')).mkdir(parents=True)
    for path, data in files:
        (oracle / path.lstrip('/')).write_bytes(data)
    project = tmp_path / 'project'
    for path in (folders if origin == 'adopted' else chain if origin == 'unplaced' else []):
        (project / path.lstrip('/')).mkdir(parents=True)
    if origin == 'adopted':
        for path, data in files:
            seed(project, path.lstrip('/'), data)
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers, **tier_kwargs), clock=clock)
    engine.start()
    walk(engine)
    for path in (folders if origin == 'flushed' else folders[len(chain):] if origin == 'unplaced' else []):
        engine.mkdir(path)
    for path, data in (files if origin != 'adopted' else []):
        engine.create(path)
        rewrite(engine, path, data)
    if origin == 'flushed':
        engine.flush()
    walk(engine)

    def both(name: str, *paths: str) -> None:
        getattr(engine, name)(*paths)
        getattr(os, name)(*(oracle / path.lstrip('/') for path in paths))

    def remove(path: str, kind: str) -> None:
        if kind == 'tree':
            both('unlink', path + '/f.dat')
        both('unlink' if kind == 'file' else 'rmdir', path)

    if moved == 'inplace':
        for path, kind in entries:
            remove(path, kind)
        out = []
    else:
        out = [('/' + PurePosixPath(path).name, kind) for path, kind in entries]
        for (path, _), (name, _) in zip(entries, out):
            both('rename', path, name)
    removals = [] if order == 'kept' else out
    if order == 'before':
        for path, kind in removals:
            remove(path, kind)
    both('rmdir', chain[-1])
    if order == 'between':
        for path, kind in removals:
            remove(path, kind)
    for path in reversed(chain[:-1]):
        both('rmdir', path)
    if order == 'after':
        for path, kind in removals:
            remove(path, kind)
    return engine, oracle


def source_tree(engine: OverlayEngine) -> dict:
    """The source tier's tree."""
    return tree(Path(engine.spec.tiers[0].path))


@mark.parametrize('origin', ['adopted', 'flushed'])
@mark.parametrize('moved', ['file', 'dir', 'tree', 'levels'])
@mark.parametrize('depth', [2, 3])
@mark.parametrize('tiers', [1, 3])
@mark.parametrize('call', ['flush', 'drain'])
def test_one_pass_drains_nested_directories_removed_before_their_moved_out_entries(
        tmp_path: Path, clock: Clock, call: str, tiers: int, depth: int, moved: str, origin: str) -> None:
    """
    An entry moved out of a directory nested in another, both directories removed, then the entry: one `flush` or
    one `drain` runs every queued row, the entry's removal ahead of the directories that held it, and the source
    holds what plain directories would.
    """
    engine, oracle = nested_removal(tmp_path, clock, depth=depth, moved=moved, origin=origin, tiers=tiers)
    if call == 'flush':
        engine.flush()
    else:
        assert drain(engine).complete
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


@mark.parametrize('moved', ['file', 'tree', 'levels'])
@mark.parametrize('depth', [2, 3])
def test_one_scheduler_poll_runs_every_namespace_row_of_nested_removals(tmp_path: Path, clock: Clock, depth: int,
                                                                         moved: str) -> None:
    """One poll with every row due leaves no namespace row of the nested removals queued."""
    engine, oracle = nested_removal(tmp_path, clock, depth=depth, moved=moved, tiers=3)
    WritebackScheduler(engine).poll(clock.now + 10_000)
    assert [row.op for row in engine.journal.queued() if row.op != 'data'] == []
    engine.flush()
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


def test_the_writeback_pump_outlives_nested_removals(tmp_path: Path, clock: Clock) -> None:
    """The scheduler's thread commits the nested removals, stays alive, and commits an op made after them."""
    passthrough = WritebackSpec(mode='passthrough', delay=0.0)
    engine, oracle = nested_removal(tmp_path, clock, tiers=3, scratch_writeback=passthrough)
    pump = WritebackScheduler(engine, tick=0.01)
    pump.start()
    try:
        deadline = time.monotonic() + 10
        while engine.journal.queued() and pump.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pump.is_alive() and engine.journal.queued() == []
        engine.mkdir('/after')
        os.mkdir(oracle / 'after')
        while 'after' not in source_tree(engine) and pump.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pump.is_alive() and source_tree(engine) == tree(oracle)
    finally:
        pump.stop(timeout=10)                                # re-raises whatever killed the thread
    assert not pump.is_alive()
    assert_converged(engine)


@mark.parametrize('tiers', [1, 3])
@mark.parametrize('target', ['/d1', '/m.dat'])
@mark.parametrize('call', ['mkdir', 'create', 'rename'])
def test_a_new_name_over_nested_removals(tmp_path: Path, clock: Clock, call: str, target: str, tiers: int) -> None:
    """A name the outer removed directory or the removed moved entry held takes a new entry; one flush then drains."""
    engine, oracle = nested_removal(tmp_path, clock, tiers=tiers)
    if call == 'mkdir':
        engine.mkdir(target)
        os.mkdir(oracle / target.lstrip('/'))
    else:
        made = target if call == 'create' else '/fresh.dat'
        engine.create(made)
        rewrite(engine, made, b'fresh')
        (oracle / made.lstrip('/')).write_bytes(b'fresh')
        if call == 'rename':
            engine.rename(made, target)
            os.rename(oracle / made.lstrip('/'), oracle / target.lstrip('/'))
    engine.flush()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


# The rows the base engine queues for nested_removal's default shape: (op, entry path, its committed source path).
STUCK = [('rmdir', '/d1/d2', '/d1/d2'), ('rmdir', '/d1', '/d1'), ('unlink', '/m.dat', '/d1/d2/m.dat')]


def queue_of(engine: OverlayEngine) -> list:
    """The queued rows as (op, entry path, committed source path)."""
    entries = [engine.catalog.by_id(row.entry) for row in engine.journal.queued()]
    return [(row.op, entry.path, entry.source_path) for row, entry in zip(engine.journal.queued(), entries)]


@mark.parametrize('raised', [False, True])
@mark.parametrize('call', ['flush', 'drain'])
def test_a_queue_left_by_nested_removals_drains_on_the_first_pass_after_a_restart(
        tmp_path: Path, clock: Clock, call: str, raised: bool) -> None:
    """
    The rows the nested removals queue, read by a new engine from the state directory, drain in its first pass,
    also after an earlier replay raised out of the first row's pre-replay before acting, as recursion did.
    """
    engine, oracle = nested_removal(tmp_path, clock)
    assert queue_of(engine) == STUCK
    if raised:
        def recursed(*args, **kwargs):
            delattr(engine, '_holder_rows')                  # once: the engine's own method again
            raise RecursionError('maximum recursion depth exceeded')

        engine._holder_rows = recursed
        with raises(RecursionError):
            engine.flush()
        assert queue_of(engine) == STUCK and engine.journal.pending() == []
    spec = engine.spec
    engine.stop()
    engine = OverlayEngine(spec, clock=clock)
    engine.start()
    assert queue_of(engine) == STUCK
    if call == 'flush':
        engine.flush()
    else:
        assert drain(engine).complete
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


NEIGHBOURS = {'one directory': dict(depth=1), 'entry kept': dict(order='kept'),
              'removed in place': dict(moved='inplace'), 'entry removed first': dict(order='before'),
              'entry removed between': dict(order='between'), 'entry never placed': dict(origin='unplaced', tiers=3)}


@mark.parametrize('call', ['flush', 'drain', 'poll', 'mkdir', 'create', 'rename'])
@mark.parametrize('shape', NEIGHBOURS)
def test_shapes_beside_nested_removals_still_drain(tmp_path: Path, clock: Clock, shape: str, call: str) -> None:
    """The neighbouring shapes drain as before under every route that replays them, a new outer name included."""
    engine, oracle = nested_removal(tmp_path, clock, **{'tiers': 3 if call == 'poll' else 1, **NEIGHBOURS[shape]})
    if call == 'drain':
        assert drain(engine).complete
    elif call == 'poll':
        WritebackScheduler(engine).poll(clock.now + 10_000)
        assert [row.op for row in engine.journal.queued() if row.op != 'data'] == []
    elif call == 'mkdir':
        engine.mkdir('/d1')
        os.mkdir(oracle / 'd1')
    elif call in ('create', 'rename'):
        made = '/d1' if call == 'create' else '/fresh.dat'
        engine.create(made)
        (oracle / made.lstrip('/')).write_bytes(b'')
        if call == 'rename':
            engine.rename(made, '/d1')
            os.rename(oracle / 'fresh.dat', oracle / 'd1')
    engine.flush()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


@mark.parametrize('call', ['mkdir', 'create', 'rename'])
@mark.parametrize('shape', NEIGHBOURS)
def test_shapes_beside_nested_removals_take_a_new_name_where_the_moved_entry_was(tmp_path: Path, clock: Clock,
                                                                                 shape: str, call: str) -> None:
    """
    The neighbouring shapes under a new name at the moved entry's name after its removal: the call succeeds (an entry
    kept live still raises EEXIST for `mkdir` and `create`, and is replaced by `rename`), and one flush then drains.
    """
    engine, oracle = nested_removal(tmp_path, clock, **{'tiers': 1, **NEIGHBOURS[shape]})
    if shape == 'entry kept' and call != 'rename':
        with raises(FileExistsError):
            getattr(engine, call)('/m.dat')
    elif call == 'mkdir':
        engine.mkdir('/m.dat')
        os.mkdir(oracle / 'm.dat')
    else:
        made = '/m.dat' if call == 'create' else '/fresh.dat'
        engine.create(made)
        rewrite(engine, made, b'fresh')
        (oracle / made.lstrip('/')).write_bytes(b'fresh')
        if call == 'rename':
            engine.rename(made, '/m.dat')
            os.rename(oracle / 'fresh.dat', oracle / 'm.dat')
    engine.flush()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


def test_a_new_name_over_a_kept_moved_entry_still_raises_eexist(tmp_path: Path, clock: Clock) -> None:
    """A moved entry kept live still holds its name: `create` there raises EEXIST, and one flush then drains."""
    engine, oracle = nested_removal(tmp_path, clock, order='kept')
    with raises(FileExistsError):
        engine.create('/m.dat')
    engine.flush()
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


@mark.parametrize('order', ['before', 'after'])
def test_a_child_the_catalog_never_saw_still_stops_the_rmdir_row_with_enotempty(tmp_path: Path, clock: Clock,
                                                                                  order: str) -> None:
    """A file put into a removed directory's object behind the engine's back makes its rmdir row raise ENOTEMPTY."""
    engine, oracle = nested_removal(tmp_path, clock, order=order)
    stray = Path(engine.spec.tiers[0].path) / 'd1' / 'd2' / 'stray.dat'
    stray.write_bytes(b'out of band')
    with raises(OSError) as error:
        engine.flush()
    assert error.value.errno == errno.ENOTEMPTY
    os.unlink(stray)
    engine.flush()
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


def sibling_chains(tmp_path: Path, clock: Clock, *, count: int, depth: int, order: str,
                   tiers: int) -> tuple[OverlayEngine, Path]:
    """
    `count` chains side by side, /c0000 down `depth` nested directories, each with a file in its innermost one
    moved out to the root; then every directory removed, innermost first, chain after chain; then every moved file,
    in chain order (`ascending`) or the reverse. The same calls run with plain `os` on a copy of the starting tree.
    """
    oracle = tmp_path / 'oracle'
    chains = [['/' + '/'.join([f'c{index:04d}'] + [f'd{level}' for level in range(1, top)]) for top in
               range(1, depth + 1)] for index in range(count)]
    for index, chain in enumerate(chains):
        data = f'chain {index}'.encode()
        seed(tmp_path / 'project', chain[-1].lstrip('/') + '/m.dat', data)
        (oracle / chain[-1].lstrip('/')).mkdir(parents=True)
        (oracle / chain[-1].lstrip('/') / 'm.dat').write_bytes(data)
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers), clock=clock)
    engine.start()
    walk(engine)

    def both(name: str, *paths: str) -> None:
        getattr(engine, name)(*paths)
        getattr(os, name)(*(oracle / path.lstrip('/') for path in paths))

    for index, chain in enumerate(chains):
        both('rename', chain[-1] + '/m.dat', f'/m{index:04d}.dat')
    for chain in chains:
        for path in reversed(chain):
            both('rmdir', path)
    for index in (range(count) if order == 'ascending' else reversed(range(count))):
        both('unlink', f'/m{index:04d}.dat')
    return engine, oracle


# Each call and stack at each depth, and each order at each depth: eight runs, not sixteen, at a few seconds each.
CHAINS = [('flush', 1, 1, 'ascending'), ('flush', 3, 1, 'descending'), ('drain', 1, 1, 'descending'),
          ('drain', 3, 1, 'ascending'), ('flush', 1, 2, 'descending'), ('flush', 3, 2, 'ascending'),
          ('drain', 1, 2, 'ascending'), ('drain', 3, 2, 'descending')]


@mark.parametrize('call, tiers, depth, order', CHAINS)
def test_one_pass_drains_hundreds_of_chains_removed_before_their_moved_out_entries(
        tmp_path: Path, clock: Clock, call: str, tiers: int, depth: int, order: str) -> None:
    """
    Five hundred chains side by side, each a directory (or two, nested) whose file moved out, every directory
    removed and then every file: each directory's row reaches the next chain's in its replay, five hundred deep,
    and one pass still runs every row once.
    """
    engine, oracle = sibling_chains(tmp_path, clock, count=500, depth=depth, order=order, tiers=tiers)
    if call == 'flush':
        engine.flush()
    else:
        assert drain(engine).complete
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


def crossing(tmp_path: Path, clock: Clock, paths: list, ops: list, tiers: int = 1) -> tuple[OverlayEngine, Path]:
    """
    Seed `paths` (a trailing '/' marks an empty directory) on the source and on a plain copy, list the tree, and
    run `ops` on the engine and the copy.
    """
    oracle = tmp_path / 'oracle'
    for path in paths:
        if path.endswith('/'):
            (tmp_path / 'project' / path).mkdir(parents=True, exist_ok=True)
            (oracle / path).mkdir(parents=True, exist_ok=True)
            continue
        seed(tmp_path / 'project', path, b'held:' + path.encode())
        (oracle / path).parent.mkdir(parents=True, exist_ok=True)
        (oracle / path).write_bytes(b'held:' + path.encode())
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers), clock=clock)
    engine.start()
    walk(engine)
    for name, *args in ops:
        getattr(engine, name)(*args)
        getattr(os, name)(*(oracle / path.lstrip('/') for path in args))
    return engine, oracle


# Queued directory renames whose targets lie inside objects other renames have yet to move. 'listed': a rename
# moves a directory into the one it held; 'growing': the first move leaves the target inside, and the rows newly
# between must run too; 'nested': each rename's replay reaches the other's; 'inverted': a chain turned inside out,
# where a rename reached inside another's replay frees nothing on its first pass and must wait for a later visit.
CROSSINGS = {
    'listed': (['c/a/a/f.dat', 'c/a/d/b/g.dat'], [
        ('rename', '/c/a', '/c/d'), ('rename', '/c/d/d/b', '/c/d/a/c'), ('rename', '/c/d/a', '/a'),
        ('rename', '/c/d', '/a/c/c')]),
    'growing': (['a/b/a/a/f.dat', 'a/b/a/d/g.dat', 'a/b/e/h.dat'], [
        ('rename', '/a/b', '/e'), ('rename', '/e/e', '/a/a'), ('rename', '/a', '/e/a/d/a'),
        ('rename', '/e/a/d', '/e/a/e'), ('rename', '/e/a/e/a/a', '/e/a/a/e'), ('mkdir', '/e/a/a/a'),
        ('rename', '/e/a/a/a', '/b'), ('rename', '/e/a/a', '/b/a'), ('rename', '/e', '/b/a/e/b')]),
    'nested': (['a/b/c/f.dat', 'x/m.dat', 'b/n.dat'], [
        ('mkdir', '/x/x'), ('rename', '/a', '/b/y'), ('rename', '/b', '/x/a'), ('rename', '/x/a', '/x/x/c'),
        ('rename', '/x/x/c/y/b/c', '/x/b'), ('rename', '/x/x', '/x/b/a')]),
    'inverted': (['y/l1/l2/l3/f.dat'], [
        ('rename', '/y/l1/l2', '/l2'), ('rename', '/y/l1', '/l2/l1'), ('rename', '/y', '/l2/l1/y'),
        ('rename', '/l2/l3', '/l3'), ('rename', '/l2', '/l3/l2')]),
}


@mark.parametrize('call, tiers', [('flush', 1), ('flush', 3), ('drain', 1), ('drain', 3), ('poll', 3)])
@mark.parametrize('case', CROSSINGS)
def test_one_pass_drains_renames_crossing_each_others_subtrees(tmp_path: Path, clock: Clock, case: str, call: str,
                                                               tiers: int) -> None:
    """Directory renames that move into or out of each other's subtrees drain in the first pass, as plain renames."""
    engine, oracle = crossing(tmp_path, clock, *CROSSINGS[case], tiers=tiers)
    if call == 'flush':
        engine.flush()
    elif call == 'drain':
        assert drain(engine).complete
    else:
        WritebackScheduler(engine).poll(clock.now + 10_000)
        assert [row.op for row in engine.journal.queued() if row.op != 'data'] == []
        engine.flush()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


# A user call whose vacate replays such renames: 'rename' meets a crossing in its own replay; 'mkdir' reaches a
# rename inside the rename it needs, which waits for it.
CROSSED_NAMES = {
    'rename': (['d/d/c/', 'd/c/e/', 'd/c/d/d/', 'd/c/d/e/', 'e/b/'], [
        ('rename', '/d/c', '/d/d/c/d'), ('rename', '/d/d', '/e/b/b'), ('rename', '/e/b/b/c/d/e', '/e/b/b/c/d/d/d/b'),
        ('mkdir', '/e/b/b/c/d/d/e/d'), ('rename', '/e/b/b/c/d/d/d', '/e/b/b/a'),
        ('rename', '/e/b/b/c/d', '/e/b/b/a/b/e'), ('rename', '/e/b/b/a/b/e/d/e/d', '/e/b/b/a/b/e/d/d')]),
    'mkdir': (['a/b/g.dat', 'x/y/k.dat', 'b/n.dat'], [
        ('mkdir', '/y'), ('rename', '/y', '/b/c'), ('rename', '/a', '/b/c/b'), ('rename', '/x/y', '/b/c/b/b/b'),
        ('rename', '/b/c/b', '/b/y'), ('rename', '/x', '/b/y/a'), ('rename', '/b/y/b', '/b/a'),
        ('rename', '/b/y', '/b/a/b/a'), ('mkdir', '/b/a/b/a/a/y')]),
}


@mark.parametrize('case', CROSSED_NAMES)
def test_a_new_name_over_renames_crossing_each_others_subtrees(tmp_path: Path, clock: Clock, case: str) -> None:
    """A user rename or mkdir whose name such renames free succeeds, and one flush then drains."""
    engine, oracle = crossing(tmp_path, clock, *CROSSED_NAMES[case])
    engine.flush()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


@mark.parametrize('reused', [False, True])
def test_a_removal_queued_during_a_replay_runs_before_the_directory_it_holds(engine: OverlayEngine,
                                                                             reused: bool) -> None:
    """
    Two files moved out of a directory, which is removed, then re-created: while the vacate's replay moves the
    first file, a user removes the second, cancelling its queued move (and, `reused`, its later `chmod`, the newest
    row, whose id its removal then takes) and queuing its removal after the replay read the queue. That removal
    still runs before the directory's own, and the new directory takes the name.
    """
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'd/b.dat', b'B')
    seed(root, 'd/c.dat', b'C')
    walk(engine)
    directory_sequence(engine, [('rename', '/d/b.dat', '/b.dat'), ('rename', '/d/c.dat', '/c.dat'), ('rmdir', '/d')]
                       + ([('chmod', '/c.dat', 0o600)] if reused else []))
    newest = engine.journal.queued()[-1].id
    queue = unlink_during_next(engine, 'rename', '/c.dat')
    engine.mkdir('/d')
    assert [op for _, op in queue] == ['rename', 'rmdir', 'unlink']   # the removal queued behind the replay's read
    assert (queue[-1][0] == newest) is reused                          # it took the cancelled newest row's id
    engine.flush()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert {k: v for k, v in tree(root).items() if k.split('/')[0] in ('b.dat', 'c.dat', 'd')} == {
        'b.dat': b'B', 'd': 'DIR'}
    assert_converged(engine)


@mark.parametrize('tiers', [1, 3])
def test_a_rename_over_a_directory_whose_object_holds_nested_removals(tmp_path: Path, clock: Clock,
                                                                      tiers: int) -> None:
    """
    A directory renamed over an empty one whose object still holds nested removals (a file moved out of /t/d1/d2,
    both directories removed, then the file): the replaced directory's removal first runs those rows, in one pass.
    """
    engine, oracle = crossing(tmp_path, clock, ['t/d1/d2/m.dat'], [
        ('rename', '/t/d1/d2/m.dat', '/m.dat'), ('rmdir', '/t/d1/d2'), ('rmdir', '/t/d1'), ('unlink', '/m.dat'),
        ('mkdir', '/fresh'), ('rename', '/fresh', '/t')], tiers=tiers)
    engine.flush()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


@mark.parametrize('tiers', [1, 3])
def test_a_row_waiting_inside_a_new_names_replay_past_its_last_need_stays_queued_for_the_next_flush(
        tmp_path: Path, clock: Clock, tiers: int) -> None:
    """
    A file moved out of /q/p, then /q/p, /z and /q removed and the file after them, then /z made again: its vacate
    runs the queue through /z's removal, starting with /q/p's, inside whose replay /q's removal waits for it. /q's
    row lies past /z's, the last the vacate needs, so it stays queued when `mkdir` returns; one flush then drains it.
    """
    engine, oracle = crossing(tmp_path, clock, ['q/p/c.dat', 'z/'], [
        ('rename', '/q/p/c.dat', '/c.dat'), ('rmdir', '/q/p'), ('rmdir', '/z'), ('rmdir', '/q'), ('unlink', '/c.dat'),
        ('mkdir', '/z')], tiers=tiers)
    assert [(op, path) for op, path, _ in queue_of(engine)] == [('rmdir', '/q'), ('mkdir', '/z')]
    assert source_tree(engine) == {'q': 'DIR'}
    engine.flush()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
    assert source_tree(engine) == tree(oracle)
    assert_converged(engine)


def plain_rows(tmp_path: Path, clock: Clock, count: int, tiers: int) -> tuple[OverlayEngine, Path]:
    """
    `count` of each namespace row nothing stands in, on adopted entries: a file renamed, a directory renamed, a file
    removed, an empty directory removed, a directory made and its mode changed. The same calls run on a plain copy.
    """
    oracle = tmp_path / 'oracle'
    for index in range(count):
        for name in (f'f{index:03d}', f'u{index:03d}'):
            seed(tmp_path / 'project', name, name.encode())
            (oracle / name).parent.mkdir(parents=True, exist_ok=True)
            (oracle / name).write_bytes(name.encode())
        for name in (f'd{index:03d}', f'k{index:03d}'):
            (tmp_path / 'project' / name).mkdir(parents=True)
            (oracle / name).mkdir(parents=True)
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers), clock=clock)
    engine.start()
    walk(engine)
    for index in range(count):
        for name, *args in (('rename', f'/f{index:03d}', f'/g{index:03d}'),
                            ('rename', f'/d{index:03d}', f'/e{index:03d}'), ('unlink', f'/u{index:03d}'),
                            ('rmdir', f'/k{index:03d}'), ('mkdir', f'/n{index:03d}'),
                            ('chmod', f'/n{index:03d}', 0o700)):
            getattr(engine, name)(*args)
            getattr(os, name)(*(oracle / path.lstrip('/') if isinstance(path, str) else path for path in args))
    return engine, oracle


def queue_passes(engine: OverlayEngine) -> list:
    """
    Record each statement the engine runs from now on that reads every queued row (no entry or id narrows it), as
    `Journal.queued` and `Journal.queued_marks` do, by its SQL text.
    """
    passes = []
    real = engine.db.execute

    def counted(template):
        text = ''.join(template.strings)
        if "FROM writeback WHERE state = 'queued'" in text:
            passes.append(text)
        return real(template)

    engine.db.execute = counted
    return passes


@mark.parametrize('call', ['flush', 'drain', 'poll'])
def test_a_pass_over_rows_nothing_stands_in_reads_the_queue_a_fixed_number_of_times(tmp_path: Path, clock: Clock,
                                                                                     call: str) -> None:
    """
    One `flush`, `drain` or poll over namespace rows nothing stands in makes as many full reads of the queue for
    sixty rows as for six: no row's replay reads it, so the reads grow with the queue only linearly.
    """
    counted = {}
    for count in (1, 10):
        engine, oracle = plain_rows(tmp_path / f'rows{count}', clock, count, tiers=3 if call == 'poll' else 1)
        passes = queue_passes(engine)
        if call == 'flush':
            engine.flush()
        elif call == 'drain':
            assert drain(engine).complete
        else:
            WritebackScheduler(engine).poll(clock.now + 10_000)
        counted[count] = len(passes)
        assert engine.journal.queued() == [] and engine.journal.pending() == []
        assert source_tree(engine) == tree(oracle)
        if count == 1:
            assert_converged(engine)
    assert counted[10] == counted[1], f'queue passes grew with the queue: {counted}'
    assert_converged(engine)


# -- a new name first runs what still occupies its place ------------------------------------------------------

def test_mkdir_over_removed_directory_with_queued_child_removal(engine: OverlayEngine) -> None:
    """Re-creating a removed directory whose child's removal is still queued succeeds (no ENOTEMPTY)."""
    walk(engine)
    directory_sequence(engine, [('mkdir', '/d1'), ('mkdir', '/d1/s1'), ('flush',), ('rmdir', '/d1/s1'),
                                ('rmdir', '/d1'), ('mkdir', '/d1')])
    engine.flush()
    assert tree(Path(engine.spec.tiers[0].path))['d1'] == 'DIR'
    assert_converged(engine)


def test_rename_over_empty_directory_with_queued_child_removal(engine: OverlayEngine) -> None:
    """Renaming onto a directory that is empty only in the catalog first runs its child's queued removal."""
    walk(engine)
    directory_sequence(engine, [('mkdir', '/d1'), ('mkdir', '/d1/s1'), ('flush',), ('rmdir', '/d1/s1'),
                                ('mkdir', '/d2'), ('rename', '/d2', '/d1')])
    engine.flush()
    assert_converged(engine)


def test_new_file_at_a_renamed_away_path(engine: OverlayEngine) -> None:
    """A new file at a name whose object's move is still queued neither loses the moved bytes nor its own."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')                        # deferred: the source still holds /x.dat
    engine.create('/x.dat')
    rewrite(engine, '/x.dat', b'fresh')
    engine.commit_upstream('/x.dat')
    engine.flush()
    assert tree(root)['x.dat'] == b'fresh' and tree(root)['y.dat'] == b'original'
    assert_converged(engine)


def test_new_file_where_a_removed_file_still_sits(engine: OverlayEngine, clock: Clock) -> None:
    """A new file where a renamed-then-removed object still sits survives that object's queued removal."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')
    engine.unlink('/y.dat')                                  # its removal targets the object still at /x.dat
    engine.create('/x.dat')
    rewrite(engine, '/x.dat', b'fresh')
    engine.commit_upstream('/x.dat')
    WritebackScheduler(engine).poll(clock.now + 10_000)
    assert tree(root)['x.dat'] == b'fresh' and 'y.dat' not in tree(root)
    assert_converged(engine)


# -- stale scheduler snapshots --------------------------------------------------------------------------------

def test_scheduler_rows_made_moot_before_they_run(engine: OverlayEngine, clock: Clock) -> None:
    """Rows in the pump's snapshot that a user op since cancelled or ran change nothing when they come up."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    seed(root, 'z.dat', b'zzz')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')
    engine.unlink('/z.dat')
    due = WritebackScheduler(engine).due(clock.now + 10_000)  # the pump's snapshot, taken before the ops below
    assert {row.op for row in due} == {'rename', 'unlink'}
    engine.unlink('/y.dat')                                  # cancels the rename row the snapshot holds
    engine.create('/z.dat')                                  # runs the unlink row the snapshot holds
    for row in due:
        engine.commit_row(row)
    engine.flush()
    assert 'x.dat' not in tree(root) and 'y.dat' not in tree(root)
    assert_converged(engine)


def test_scheduler_row_id_reused_after_cancel(engine: OverlayEngine, clock: Clock) -> None:
    """A cancelled rename row whose id the replacing unlink row took does not run, nor settle that unlink."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')
    due = WritebackScheduler(engine).due(clock.now + 10_000)  # the pump's snapshot holds the rename row
    engine.unlink('/y.dat')                                  # cancels it; the queued unlink takes its id
    for row in due:
        engine.commit_row(row)
    engine.flush()
    assert 'x.dat' not in tree(root) and 'y.dat' not in tree(root)
    assert_converged(engine)


def test_scheduler_data_row_id_reused_by_next_hop(tmp_path: Path, clock: Clock) -> None:
    """A committed data row whose id the next hop's row took does not settle that row as moot."""
    engine = OverlayEngine(build_spec(tmp_path, tiers=3), clock=clock)
    engine.start()
    walk(engine)
    engine.create('/a.dat')
    rewrite(engine, '/a.dat', b'payload')
    scheduler = WritebackScheduler(engine)
    later = clock.now + 10_000
    due = scheduler.due(later)                               # the pump's snapshot holds the first hop's row
    scheduler.poll(later)                                    # that hop commits; the next hop's row takes its id
    for row in due:
        engine.commit_row(row)
    engine.flush()
    assert tree(tmp_path / 'project') == {'a.dat': b'payload'}
    assert_converged(engine)


# -- a replay settles only the row it read -------------------------------------------------------------------

def test_scheduler_replay_settles_only_its_own_row(engine: OverlayEngine, clock: Clock) -> None:
    """A removal landing during a scheduled rename's act keeps its queued op, though it took the rename row's id."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')
    (row,) = WritebackScheduler(engine).due(clock.now + 10_000)
    queue = unlink_during_next(engine, 'rename', '/y.dat')
    engine.commit_row(row)
    assert queue == [(row.id, 'unlink')]                     # the removal ran mid-act and took the row's id
    assert [r.op for r in engine.journal.queued()] == ['unlink']
    engine.flush()
    assert 'x.dat' not in tree(root) and 'y.dat' not in tree(root)
    assert_converged(engine)


def test_flush_replay_settles_only_its_own_row(engine: OverlayEngine) -> None:
    """A removal landing during a rename `flush` replays keeps its queued op for the next flush."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')
    (row,) = engine.journal.queued()
    queue = unlink_during_next(engine, 'rename', '/y.dat')
    engine.flush()
    assert queue == [(row.id, 'unlink')]
    assert [r.op for r in engine.journal.queued()] == ['unlink']   # queued after the pass that replayed the rename
    engine.flush()
    assert 'x.dat' not in tree(root) and 'y.dat' not in tree(root)
    assert_converged(engine)


def test_drain_replay_settles_only_its_own_row(engine: OverlayEngine) -> None:
    """A removal landing during a rename a drain replays keeps its queued op for the next drain."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')
    (row,) = engine.journal.queued()
    queue = unlink_during_next(engine, 'rename', '/y.dat')
    drain(engine)
    assert queue == [(row.id, 'unlink')]
    assert [r.op for r in engine.journal.queued()] == ['unlink']
    drain(engine)
    assert 'x.dat' not in tree(root) and 'y.dat' not in tree(root)
    assert_converged(engine)


def test_vacate_replay_settles_only_its_own_row(engine: OverlayEngine) -> None:
    """A removal landing during a rename that `create` replays to free its name keeps its queued op."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'original')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')                        # the source still holds /x.dat
    (row,) = engine.journal.queued()
    queue = unlink_during_next(engine, 'rename', '/y.dat')
    engine.create('/x.dat')                                  # the queued rename runs first, to free the place
    assert queue == [(row.id, 'unlink')]
    assert [r.op for r in engine.journal.queued()] == ['unlink', 'data']
    rewrite(engine, '/x.dat', b'fresh')
    engine.flush()
    assert tree(root)['x.dat'] == b'fresh' and 'y.dat' not in tree(root)
    assert_converged(engine)


def test_flush_settles_only_the_data_row_it_read(engine: OverlayEngine) -> None:
    """A file removed while `flush` commits another keeps its queued removal, though it took a stale row's id."""
    root = Path(engine.spec.tiers[0].path)
    walk(engine)
    engine.create('/a.dat')
    rewrite(engine, '/a.dat', b'A')
    rewrite(engine, '/top.txt', b'TOP')                      # held on the source, now dirty on the stage
    rows = engine.journal.queued()
    assert [r.op for r in rows] == ['data', 'data']
    queue = unlink_during_next(engine, 'put', '/top.txt')    # lands while /a.dat's commit writes the source
    engine.flush()
    assert queue == [(rows[0].id, 'data'), (rows[-1].id, 'unlink')]   # the removal took /top.txt's row id
    assert [r.op for r in engine.journal.queued()] == ['unlink']
    engine.flush()
    assert 'top.txt' not in tree(root) and tree(root)['a.dat'] == b'A'
    assert_converged(engine)


# -- what a replayed rename may purge and where it may move ----------------------------------------------------

def test_flush_purges_only_the_tombstone_a_rename_left(engine: OverlayEngine) -> None:
    """A rename row purges its own tombstone only, never another entry's tombstone with a queued removal."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'X')
    walk(engine)
    engine.rename('/x.dat', '/y.dat')
    engine.rename('/y.dat', '/z.dat')
    engine.create('/y.dat')
    rewrite(engine, '/y.dat', b'N')
    engine.commit_upstream('/y.dat')
    engine.unlink('/y.dat')                                  # a tombstone at /y.dat with its own queued unlink
    engine.flush()
    assert tree(root)['z.dat'] == b'X' and 'y.dat' not in tree(root) and 'x.dat' not in tree(root)
    assert_converged(engine)


def test_flush_when_a_directory_target_lies_inside_its_own_source_object(engine: OverlayEngine) -> None:
    """A directory whose target was placed inside its own source object moves once the entries between leave."""
    root = Path(engine.spec.tiers[0].path)
    walk(engine)
    directory_sequence(engine, [('mkdir', '/e'), ('mkdir', '/e/p'), ('flush',), ('mkdir', '/x'),
                                ('rename', '/e', '/x/e'), ('rename', '/x/e/p', '/p'), ('rename', '/x', '/p/x')])
    engine.flush()
    assert {k: v for k, v in tree(root).items() if k.split('/')[0] in ('e', 'p', 'x')} == {
        'p': 'DIR', 'p/x': 'DIR', 'p/x/e': 'DIR'}
    assert_converged(engine)


# -- a file served off the source is cut and sized where the source holds it ----------------------------------

def source_served(tmp_path: Path, clock: Clock, files: dict, tiers: int = 2) -> OverlayEngine:
    """
    A started, walked engine over the seeded `files`. With a stage, its `bypass_size` (100 bytes) leaves the
    larger files served off the source.
    """
    for relative, data in files.items():
        seed(tmp_path / 'project', relative, data)
    quota = {'scratch_quota': QuotaSpec(bypass_size=100)} if tiers > 1 else {}
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers, **quota), clock=clock)
    engine.start()
    walk(engine)
    return engine


def assert_cut(engine: OverlayEngine, path: str, original: bytes, length: int) -> None:
    """`getattr` reports `length`, and a fresh handle reads `original` cut to it, or padded with zero bytes."""
    assert engine.getattr(path).size == length
    handle = engine.open(path)
    try:
        assert engine.read(handle, length + 1, 0) == original[:length].ljust(length, b'\0')
    finally:
        engine.release(handle)


class Interleaved:
    """
    Stands in for the source lock. The first time the test's thread takes it, another thread runs `action` to
    completion first: a replay or a user op landing after the cut resolved the entry and before it took the
    lock. The action's exception, if any, is raised in the test's thread, and `ran` is set only on success.
    """

    def __init__(self, engine: OverlayEngine, action) -> None:
        self.real, self.action, self.caller = engine._source_lock, action, threading.get_ident()
        self.started, self.ran, self.error = False, False, None

    def run(self) -> None:
        try:
            self.action()
        except BaseException as error:                       # raised again in the test's thread
            self.error = error

    def __enter__(self):
        if not self.started and threading.get_ident() == self.caller:
            self.started = True
            helper = threading.Thread(target=self.run)
            helper.start()
            helper.join()
            if self.error is not None:
                raise self.error
            self.ran = True
        return self.real.__enter__()

    def __exit__(self, *exc):
        return self.real.__exit__(*exc)


def concurrent_removal(engine: OverlayEngine, recreate: bytes | None = None):
    """An action: rename the file's directory, remove the file and drain; optionally create a new file there."""
    def action() -> None:
        engine.rename('/data', '/moved')
        engine.unlink('/moved/big.bin')
        engine.flush()                                       # purges the tombstone, which frees its entry id
        if recreate is not None:
            engine.create('/moved/big.bin')
            rewrite(engine, '/moved/big.bin', recreate)
            engine.flush()
    return action


def lock_probe(engine: OverlayEngine, free: list):
    """
    A function that records, from another thread, whether that thread could take the source lock (the engine's
    own, as it is when the probe is made, so a test may stand `Interleaved` in for it afterwards).
    """
    lock = engine._source_lock

    def try_lock() -> None:
        taken = lock.acquire(blocking=False)
        if taken:
            lock.release()
        free.append(taken)

    def probe() -> None:
        helper = threading.Thread(target=try_lock)
        helper.start()
        helper.join()
    return probe


def test_truncate_without_a_handle_behind_its_own_rename(tmp_path: Path, clock: Clock) -> None:
    """A file over the stage's bypass_size is cut at its pre-rename place while its own rename is queued."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine.rename('/big.bin', '/moved.bin')                  # deferred: the source still holds /big.bin
    counted = engine.registry.usage(engine.source.name)
    engine.truncate('/moved.bin', 4)
    assert engine.registry.usage(engine.source.name) == counted     # source counters track pushes, not I/O
    assert tree(root) == {'big.bin': b'xxxx'}
    assert_cut(engine, '/moved.bin', b'x' * 1000, 4)
    engine.flush()
    assert tree(root) == {'moved.bin': b'xxxx'}
    assert_converged(engine)


def test_truncate_without_a_handle_grows_behind_its_own_rename(tmp_path: Path, clock: Clock) -> None:
    """Growing past the old end pads with zero bytes, at the pre-rename place."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine.rename('/big.bin', '/moved.bin')
    engine.truncate('/moved.bin', 2000)
    assert tree(root) == {'big.bin': b'x' * 1000 + bytes(1000)}
    assert_cut(engine, '/moved.bin', b'x' * 1000, 2000)
    engine.flush()
    assert tree(root) == {'moved.bin': b'x' * 1000 + bytes(1000)}
    assert_converged(engine)


def test_truncate_without_a_handle_inside_a_renamed_directory(tmp_path: Path, clock: Clock) -> None:
    """A file over the stage's bypass_size is cut under its directory's old place while that rename is queued."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine.rename('/data', '/moved')
    engine.truncate('/moved/big.bin', 4)
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'xxxx'}
    assert_cut(engine, '/moved/big.bin', b'x' * 1000, 4)
    engine.flush()
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'xxxx'}
    assert_converged(engine)


def test_truncate_without_a_handle_of_an_adopted_file_inside_a_renamed_directory(tmp_path: Path, clock: Clock) -> None:
    """With no stage, an adopted file is cut under its directory's old place."""
    engine = source_served(tmp_path, clock, {'data/a.txt': b'alpha'}, tiers=1)
    root = tmp_path / 'project'
    engine.rename('/data', '/moved')
    engine.truncate('/moved/a.txt', 0)
    assert tree(root) == {'data': 'DIR', 'data/a.txt': b''}
    assert_cut(engine, '/moved/a.txt', b'alpha', 0)
    engine.flush()
    assert tree(root) == {'moved': 'DIR', 'moved/a.txt': b''}
    assert_converged(engine)


def test_truncate_without_a_handle_of_a_file_created_inside_a_renamed_directory(tmp_path: Path, clock: Clock) -> None:
    """With no stage, a file created under a renamed directory sits under its old place and is cut there."""
    engine = source_served(tmp_path, clock, {'data/a.txt': b'alpha'}, tiers=1)
    root = tmp_path / 'project'
    engine.rename('/data', '/moved')
    engine.create('/moved/c.txt')
    rewrite(engine, '/moved/c.txt', b'GAMMA')
    engine.truncate('/moved/c.txt', 0)
    assert tree(root) == {'data': 'DIR', 'data/a.txt': b'alpha', 'data/c.txt': b''}
    assert_cut(engine, '/moved/c.txt', b'GAMMA', 0)
    engine.flush()
    assert tree(root) == {'moved': 'DIR', 'moved/a.txt': b'alpha', 'moved/c.txt': b''}
    assert_converged(engine)


def test_truncate_without_a_handle_of_a_committed_file_behind_its_own_rename(tmp_path: Path, clock: Clock) -> None:
    """With no stage, a file created and flushed, then renamed, is cut at its pre-rename place."""
    engine = source_served(tmp_path, clock, {}, tiers=1)
    root = tmp_path / 'project'
    engine.create('/n.txt')
    rewrite(engine, '/n.txt', b'hello')
    engine.flush()
    engine.rename('/n.txt', '/m.txt')
    engine.truncate('/m.txt', 1)
    assert tree(root) == {'n.txt': b'h'}
    assert_cut(engine, '/m.txt', b'hello', 1)
    engine.flush()
    assert tree(root) == {'m.txt': b'h'}
    assert_converged(engine)


def test_truncate_without_a_handle_after_a_queued_swap(tmp_path: Path, clock: Clock) -> None:
    """After a swap through a temporary name, the cut lands on the renamed file's own object and no other."""
    engine = source_served(tmp_path, clock, {'a.bin': b'a' * 1000, 'b.bin': b'b' * 1000})
    root = tmp_path / 'project'
    engine.rename('/a.bin', '/t.bin')
    engine.rename('/b.bin', '/a.bin')                        # its vacate runs the first rename: /a.bin is free
    engine.truncate('/a.bin', 4)
    assert tree(root) == {'b.bin': b'bbbb', 't.bin': b'a' * 1000}
    assert_cut(engine, '/a.bin', b'b' * 1000, 4)
    engine.flush()
    assert tree(root) == {'a.bin': b'bbbb', 't.bin': b'a' * 1000}
    assert_converged(engine)


def test_truncate_without_a_handle_cuts_under_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """The cut opens a copy served off the source with the source lock held, and releases it on return."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    engine.rename('/big.bin', '/moved.bin')
    source = engine.source
    real = source.open_direct
    free = []
    probe = lock_probe(engine, free)

    def probed(*args, **kwargs):
        delattr(source, 'open_direct')                       # once: the tier's own method again
        probe()
        return real(*args, **kwargs)

    source.open_direct = probed
    engine.truncate('/moved.bin', 4)
    assert free == [False]                                   # another thread could not take the lock
    probe()
    assert free == [False, True]                             # and can once the cut returns
    assert_cut(engine, '/moved.bin', b'x' * 1000, 4)
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_resolves_inside_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """A replay that moves the object before the cut takes the lock leaves the cut at the object's new place."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine.rename('/big.bin', '/moved.bin')
    engine._source_lock = Interleaved(engine, engine.flush)
    engine.truncate('/moved.bin', 4)
    assert engine._source_lock.ran                           # the replay ran inside the call
    assert tree(root) == {'moved.bin': b'xxxx'}
    assert_cut(engine, '/moved.bin', b'x' * 1000, 4)
    assert_converged(engine)


def test_truncate_without_a_handle_after_a_concurrent_rename_moves_the_file_away(tmp_path: Path, clock: Clock) -> None:
    """A directory rename landing before the cut takes the lock takes the file off the path: ENOENT, nothing cut."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine._source_lock = Interleaved(engine, lambda: engine.rename('/data', '/moved'))
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)                  # the path lock covers the file, not its directory
    assert engine._source_lock.ran
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'x' * 1000}
    assert engine.getattr('/moved/big.bin').size == 1000
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_after_a_concurrent_removal(tmp_path: Path, clock: Clock) -> None:
    """A removal landing before the cut takes the lock raises ENOENT, not an error on the missing entry."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine._source_lock = Interleaved(engine, concurrent_removal(engine))
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert tree(root) == {'moved': 'DIR'}
    assert_converged(engine)


def test_truncate_without_a_handle_leaves_a_file_that_reused_the_entry_id_alone(tmp_path: Path, clock: Clock) -> None:
    """A new file that took the removed entry's id under another path is not found at the path: it keeps its bytes."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id
    engine._source_lock = Interleaved(engine, concurrent_removal(engine, recreate=b'N' * 500))
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert engine.getattr('/moved/big.bin').id == old        # reused, but off the path: the path check rejects it
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'N' * 500}
    assert engine.getattr('/moved/big.bin').size == 500
    assert_converged(engine)


def test_truncate_without_a_handle_of_a_staged_copy_inside_a_renamed_directory(tmp_path: Path, clock: Clock) -> None:
    """A file the stage admits is marked dirty, then cut on the stage at its logical path; the source gets the cut."""
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    root = tmp_path / 'project'
    engine.rename('/data', '/moved')
    stage = engine.tiers['scratch']
    real = stage.open_direct
    seen = []

    def probed(*args, **kwargs):
        residency = engine.catalog.get_residency('/moved/s.txt', 'scratch')
        seen.append(None if residency is None else (residency.state, residency.size))
        return real(*args, **kwargs)

    stage.open_direct = probed
    engine.truncate('/moved/s.txt', 2)
    del stage.open_direct
    assert seen[-1] == ('dirty', 5)                          # marked dirty before the cut, recorded after it
    assert engine.registry.usage('scratch').bytes == 2       # the promotion's 5 bytes, less the 3 cut
    assert {r.tier for r in engine.catalog.residencies('/moved/s.txt')} == {'project', 'scratch'}
    assert engine.catalog.get_residency('/moved/s.txt', 'scratch').size == 2
    assert_cut(engine, '/moved/s.txt', b'small', 2)
    engine.flush()
    assert tree(root) == {'moved': 'DIR', 'moved/s.txt': b'sm'}
    assert_converged(engine)


def test_release_of_a_source_served_copy_stats_under_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """Recording a copy served off the source stats it with the source lock held, so no replay moves it between."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    engine.rename('/big.bin', '/moved.bin')
    handle = engine.open('/moved.bin', os.O_RDWR)
    engine.write(handle, b'tiny', 0)
    source = engine.source
    real = source.stat
    free = []
    probe = lock_probe(engine, free)

    def probed(*args, **kwargs):
        probe()
        return real(*args, **kwargs)

    source.stat = probed
    engine.release(handle)
    del source.stat
    assert free and not any(free)                            # every stat the release made held the lock
    assert engine.getattr('/moved.bin').size == 1000
    engine.flush()
    assert_converged(engine)


def test_size_through_a_bypass_stage_behind_its_own_rename(tmp_path: Path, clock: Clock) -> None:
    """A file over the stage's bypass_size, cut and written through a handle, reports its object's size throughout."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    source = engine.source.name
    engine.rename('/big.bin', '/moved.bin')
    handle = engine.open('/moved.bin', os.O_RDWR)
    assert handle.tier == source                             # served off the source, at /big.bin
    engine.truncate('/moved.bin', 0, handle=handle)
    engine.write(handle, b'tiny', 0)
    assert engine.getattr('/moved.bin').size == len(tree(root)['big.bin']) == 4     # read live while open
    engine.fsync(handle)
    assert engine.getattr('/moved.bin').size == engine.catalog.get_residency('/moved.bin', source).size == 4
    engine.release(handle)
    assert engine.getattr('/moved.bin').size == engine.catalog.get_residency('/moved.bin', source).size == 4
    engine.flush()
    assert engine.getattr('/moved.bin').size == engine.catalog.get_residency('/moved.bin', source).size == 4
    assert_converged(engine)


def test_size_through_a_bypass_stage_inside_a_renamed_directory(tmp_path: Path, clock: Clock) -> None:
    """The same, under a directory whose rename is queued."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    source = engine.source.name
    engine.rename('/data', '/moved')
    handle = engine.open('/moved/big.bin', os.O_RDWR)
    assert handle.tier == source                             # served off the source, at /data/big.bin
    engine.truncate('/moved/big.bin', 0, handle=handle)
    engine.write(handle, b'tiny', 0)
    assert engine.getattr('/moved/big.bin').size == len(tree(root)['data/big.bin']) == 4
    engine.fsync(handle)
    assert engine.getattr('/moved/big.bin').size == engine.catalog.get_residency('/moved/big.bin', source).size == 4
    engine.release(handle)
    assert engine.getattr('/moved/big.bin').size == engine.catalog.get_residency('/moved/big.bin', source).size == 4
    engine.flush()
    assert engine.getattr('/moved/big.bin').size == engine.catalog.get_residency('/moved/big.bin', source).size == 4
    assert_converged(engine)


def test_truncate_without_a_handle_cuts_a_faster_copy_made_during_the_wait(tmp_path: Path, clock: Clock) -> None:
    """
    A copy the stage takes while the cut waits for the lock, through a renamed directory the path lock does not
    cover, is the copy that serves: it is marked dirty and cut, and the source keeps a clean, true copy.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'

    def action() -> None:
        engine.rename('/data', '/moved')
        handle = engine.open('/moved/big.bin', os.O_RDWR)
        engine.truncate('/moved/big.bin', 0, handle=handle)
        engine.write(handle, b'0123456789', 0)
        engine.release(handle)
        handle = engine.open('/moved/big.bin')                # 10 bytes: the stage admits it and takes a copy
        assert handle.tier == 'scratch'
        engine.release(handle)
        engine.rename('/moved', '/data')

    engine._source_lock = Interleaved(engine, action)
    engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    residencies = {r.tier: (r.state, r.size) for r in engine.catalog.residencies('/data/big.bin')}
    assert residencies == {'project': ('clean', 10), 'scratch': ('dirty', 4)}
    assert dataclasses.astuple(engine.registry.usage('scratch')) == (4, 1)    # the promotion's 10 bytes, less 6
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'0123456789'}     # clean, and true to its residency
    assert_cut(engine, '/data/big.bin', b'0123456789', 4)
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'0123'}
    assert engine.evict_copy('/data/big.bin', 'scratch')
    assert dataclasses.astuple(engine.registry.usage('scratch')) == (0, 0)    # nothing held, nothing counted
    assert_cut(engine, '/data/big.bin', b'0123456789', 4)   # the same bytes once the stage copy is gone
    assert_converged(engine)


def test_truncate_without_a_handle_leaves_another_file_carried_onto_the_path_alone(tmp_path: Path,
                                                                                   clock: Clock) -> None:
    """A different file a directory rename carries onto the path during the wait is found there, and not cut."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id

    def action() -> None:
        engine.rename('/data', '/moved')
        engine.mkdir('/other')
        engine.create('/other/big.bin')
        rewrite(engine, '/other/big.bin', b'N' * 500)
        engine.rename('/other', '/data')

    engine._source_lock = Interleaved(engine, action)
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert engine.getattr('/data/big.bin').id != old         # a file is at the path: only the id check rejects it
    assert engine.getattr('/data/big.bin').size == 500
    assert engine.getattr('/moved/big.bin').size == 1000
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'N' * 500, 'moved': 'DIR', 'moved/big.bin': b'x' * 1000}
    assert_converged(engine)


def test_truncate_without_a_handle_of_a_missing_path_or_a_directory(tmp_path: Path, clock: Clock) -> None:
    """A path with no entry raises ENOENT and a directory raises EISDIR, and neither changes anything."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    with raises(FileNotFoundError):
        engine.truncate('/data/none.bin', 4)
    with raises(IsADirectoryError):
        engine.truncate('/data', 4)
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'x' * 1000}
    assert_converged(engine)


def test_truncate_without_a_handle_of_a_staged_copy_holds_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """
    A copy the stage serves is cut with its path lock and the source lock held, so a directory rename that reaches
    its `_vacate` during the cut waits until the cut is recorded.
    """
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    stage = engine.tiers['scratch']
    real = stage.open_direct
    free = []
    probe = lock_probe(engine, free)
    held = []

    def probed(*args, **kwargs):
        delattr(stage, 'open_direct')                        # once: the tier's own method again
        probe()
        held.append('/data/s.txt' in engine.locks._locks)    # the registry keeps a path while it is held
        return real(*args, **kwargs)

    stage.open_direct = probed
    engine.truncate('/data/s.txt', 2)
    assert free == [False]                                   # another thread could not take the source lock
    assert held == [True]
    assert engine.catalog.get_residency('/data/s.txt', 'scratch').size == 2
    assert_cut(engine, '/data/s.txt', b'small', 2)
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_after_the_serving_copy_flips_to_the_source(tmp_path: Path, clock: Clock) -> None:
    """
    The stage copy evicted during the wait, through a directory renamed away and back, leaves only the source copy.
    The serving copy is decided under the source lock, so the cut promotes again and cuts the new stage copy with
    that lock held; the source is never cut outside it.
    """
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    root = tmp_path / 'project'
    handle = engine.open('/data/s.txt')                      # the stage admits 5 bytes and takes a clean copy
    assert handle.tier == 'scratch'
    engine.release(handle)
    cuts = []
    free = []
    probe = lock_probe(engine, free)
    for name, tier in engine.tiers.items():
        def probed(*args, real=tier.open_direct, name=name, **kwargs):
            probe()
            cuts.append((name, free[-1]))
            return real(*args, **kwargs)
        tier.open_direct = probed

    def action() -> None:
        engine.rename('/data', '/d2')
        assert engine.evict_copy('/d2/s.txt', 'scratch')
        engine.rename('/d2', '/data')

    engine._source_lock = Interleaved(engine, action)
    engine.truncate('/data/s.txt', 2)
    for tier in engine.tiers.values():
        del tier.open_direct
    assert engine._source_lock.ran
    assert cuts == [('scratch', False)]                      # one open, on the stage, with the lock held
    residencies = {r.tier: (r.state, r.size) for r in engine.catalog.residencies('/data/s.txt')}
    assert residencies == {'project': ('clean', 5), 'scratch': ('dirty', 2)}
    assert dataclasses.astuple(engine.registry.usage('scratch')) == (2, 1)
    assert tree(root) == {'data': 'DIR', 'data/s.txt': b'small'}
    assert_cut(engine, '/data/s.txt', b'small', 2)
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/s.txt': b'sm'}
    assert_converged(engine)


def test_truncate_without_a_handle_leaves_a_file_moved_onto_the_old_source_place_alone(tmp_path: Path,
                                                                                      clock: Clock) -> None:
    """
    During the wait the entry's directory moves away on the source and another file takes the place the source held
    the entry's bytes at, and the path. The cut finds that file at the path, refuses it, and leaves it whole.
    """
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    root = tmp_path / 'project'
    handle = engine.open('/data/s.txt')
    engine.release(handle)
    old = engine.getattr('/data/s.txt').id

    def action() -> None:
        engine.rename('/data', '/d2')
        assert engine.evict_copy('/d2/s.txt', 'scratch')
        engine.mkdir('/other')
        engine.create('/other/s.txt')
        rewrite(engine, '/other/s.txt', b'OTHER')
        engine.flush()                                       # the source moves /data to /d2
        engine.rename('/other', '/data')
        engine.flush()                                       # and the other file onto /data/s.txt

    engine._source_lock = Interleaved(engine, action)
    with raises(FileNotFoundError):
        engine.truncate('/data/s.txt', 2)
    assert engine._source_lock.ran
    assert engine.getattr('/data/s.txt').id != old
    assert tree(root) == {'d2': 'DIR', 'd2/s.txt': b'small', 'data': 'DIR', 'data/s.txt': b'OTHER'}
    assert engine.getattr('/data/s.txt').size == engine.getattr('/d2/s.txt').size == 5
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_refuses_a_symlink_that_took_the_entry_id(tmp_path: Path, clock: Clock) -> None:
    """
    A symbolic link that took the entry's id after a purge, carried onto the path during the wait, is refused with
    ENOENT: the kind is decided again under the source lock. The link's target keeps its bytes.
    """
    root = tmp_path / 'project'
    seed(root, 'data/big.bin', b'x' * 1000)
    seed(root, 'other.bin', b'O' * 50)
    (root / 'deep').mkdir()
    engine = OverlayEngine(build_spec(tmp_path, tiers=1), clock=clock)
    engine.start()
    engine.readdir('/')                                      # /deep is cataloged but not listed
    walk(engine, '/data')
    old = engine.getattr('/data/big.bin').id

    def action() -> None:
        concurrent_removal(engine)()                         # /data renamed to /moved, the file removed and purged
        os.symlink('../other.bin', root / 'deep' / 'link')
        assert engine.getattr('/deep/link').id == old        # adopted from the unlisted directory: the id is reused
        engine.rename('/deep/link', '/moved/big.bin')
        engine.rename('/moved', '/data')

    engine._source_lock = Interleaved(engine, action)
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert engine.getattr('/data/big.bin').kind == 'symlink'
    assert (root / 'other.bin').read_bytes() == b'O' * 50
    engine.unlink('/data/big.bin')
    walk(engine)
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_refuses_a_directory_that_took_the_entry_id(tmp_path: Path, clock: Clock) -> None:
    """A directory that took the entry's id after a purge, carried onto the path during the wait, raises ENOENT."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id

    def action() -> None:
        concurrent_removal(engine)()
        engine.mkdir('/moved/big.bin')
        assert engine.getattr('/moved/big.bin').id == old    # the id is reused
        engine.rename('/moved', '/data')

    engine._source_lock = Interleaved(engine, action)
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert engine.getattr('/data/big.bin').kind == 'dir'
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': 'DIR'}
    assert_converged(engine)


def test_truncate_without_a_handle_of_a_symlink(tmp_path: Path, clock: Clock) -> None:
    """A symbolic link is refused (EISDIR, as for any entry that is not a file), and its target keeps its bytes."""
    root = tmp_path / 'project'
    seed(root, 'other.bin', b'O' * 50)
    os.symlink('other.bin', root / 'link')
    engine = source_served(tmp_path, clock, {}, tiers=1)
    assert engine.getattr('/link').kind == 'symlink'
    with raises(IsADirectoryError):
        engine.truncate('/link', 0)
    assert (root / 'other.bin').read_bytes() == b'O' * 50
    assert engine.getattr('/other.bin').size == 50
    engine.unlink('/link')
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_never_follows_a_link_at_the_held_place(tmp_path: Path, clock: Clock) -> None:
    """
    A symbolic link put out of band where the source holds the file is not followed: the open fails with ELOOP and
    the link's target keeps its bytes.
    """
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000, 'other.bin': b'O' * 50}, tiers=1)
    root = tmp_path / 'project'
    engine.rename('/big.bin', '/moved.bin')                  # deferred: the source still holds /big.bin
    (root / 'big.bin').rename(tmp_path / 'aside.bin')
    os.symlink('other.bin', root / 'big.bin')
    with raises(OSError) as raised:
        engine.truncate('/moved.bin', 4)
    assert raised.value.errno == errno.ELOOP
    assert (root / 'other.bin').read_bytes() == b'O' * 50
    (root / 'big.bin').unlink()                              # put the file back where the source holds it
    (tmp_path / 'aside.bin').rename(root / 'big.bin')
    assert_cut(engine, '/moved.bin', b'x' * 1000, 1000)      # nothing was cut or recorded
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_promotes_from_where_a_replay_during_the_wait_moved_the_file(tmp_path: Path,
                                                                                              clock: Clock) -> None:
    """
    The file's own queued rename replays while the cut waits for the source lock, moving the source object. The
    promotion the cut then makes reads the entry found under the lock, so it copies from the object's new place.
    """
    engine = source_served(tmp_path, clock, {'a.txt': b'small'})
    root = tmp_path / 'project'
    engine.rename('/a.txt', '/s.txt')                        # deferred: the source still holds /a.txt
    assert tree(root) == {'a.txt': b'small'}
    engine._source_lock = Interleaved(engine, engine.flush)
    engine.truncate('/s.txt', 2)
    assert engine._source_lock.ran
    residencies = {r.tier: (r.state, r.size) for r in engine.catalog.residencies('/s.txt')}
    assert residencies == {'project': ('clean', 5), 'scratch': ('dirty', 2)}
    assert tree(root) == {'s.txt': b'small'}                 # moved by the replay, promoted from, left clean
    assert_cut(engine, '/s.txt', b'small', 2)
    engine.flush()
    assert tree(root) == {'s.txt': b'sm'}
    assert_converged(engine)


def test_truncate_without_a_handle_counts_the_stage_copy_it_cuts(tmp_path: Path, clock: Clock) -> None:
    """
    A dirty stage copy whose size differs from the source copy's moves the stage's counter by its own size change,
    so the counter falls to zero when the copy is evicted.
    """
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    root = tmp_path / 'project'
    rewrite(engine, '/data/s.txt', b'12345678')              # the stage holds 8 bytes, the source still 5
    residencies = {r.tier: (r.state, r.size) for r in engine.catalog.residencies('/data/s.txt')}
    assert residencies == {'project': ('clean', 5), 'scratch': ('dirty', 8)}
    assert dataclasses.astuple(engine.registry.usage('scratch')) == (8, 1)
    engine.truncate('/data/s.txt', 2)
    assert dataclasses.astuple(engine.registry.usage('scratch')) == (2, 1)
    assert tree(root) == {'data': 'DIR', 'data/s.txt': b'small'}
    assert_cut(engine, '/data/s.txt', b'12345678', 2)
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/s.txt': b'12'}
    assert engine.evict_copy('/data/s.txt', 'scratch')
    assert dataclasses.astuple(engine.registry.usage('scratch')) == (0, 0)    # nothing held, nothing counted
    assert_converged(engine)


# -- open finds a source-served file where a replay moved it ---------------------------------------------------

class AfterLookup:
    """
    Wraps the engine's `_ensure` for one path. The first time the test's thread looks that path up, another thread
    runs `action` to completion once the lookup returns: a replay landing after `open` found its entry and before
    it opens or promotes the file. The action's exception, if any, is raised in the test's thread, and `ran` is set
    only on success.
    """

    def __init__(self, engine: OverlayEngine, path: str, action) -> None:
        self.engine, self.path, self.action = engine, path, action
        self.real, self.caller = engine._ensure, threading.get_ident()
        self.started, self.ran, self.error = False, False, None
        engine._ensure = self

    def run(self) -> None:
        try:
            self.action()
        except BaseException as error:                       # raised again in the test's thread
            self.error = error

    def __call__(self, path: str):
        entry = self.real(path)
        if not self.started and threading.get_ident() == self.caller and path == self.path:
            self.started = True
            del self.engine._ensure                          # once: the engine's own method again
            helper = threading.Thread(target=self.run)
            helper.start()
            helper.join()
            if self.error is not None:
                raise self.error
            self.ran = True
        return entry


def assert_reads(engine: OverlayEngine, path: str, tier: str, data: bytes) -> None:
    """A fresh handle on `path` is served from `tier` and reads `data`."""
    handle = engine.open(path)
    try:
        assert handle.tier == tier
        assert engine.read(handle, len(data) + 1, 0) == data
    finally:
        engine.release(handle)


def swap_onto(engine: OverlayEngine, old: str, new: str):
    """An action: rename `old` onto `new` (its vacate runs the queued rows holding that place), then flush."""
    def action() -> None:
        engine.rename(old, new)
        engine.flush()
    return action


def test_open_of_a_source_served_file_after_a_replay_commits_its_own_rename(tmp_path: Path, clock: Clock) -> None:
    """A replay of the file's own rename after the lookup leaves the open at the object's new place."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine.rename('/big.bin', '/moved.bin')                  # deferred: the source still holds /big.bin
    after = AfterLookup(engine, '/moved.bin', engine.flush)
    assert_reads(engine, '/moved.bin', engine.source.name, b'x' * 1000)
    assert after.ran                                         # the replay ran inside the call, after the lookup
    assert tree(root) == {'moved.bin': b'x' * 1000}
    assert_converged(engine)


def test_open_of_a_source_served_file_after_a_replay_commits_its_directory_rename(tmp_path: Path,
                                                                                  clock: Clock) -> None:
    """The same, with the rename of the file's directory replayed after the lookup."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine.rename('/data', '/moved')
    after = AfterLookup(engine, '/moved/big.bin', engine.flush)
    assert_reads(engine, '/moved/big.bin', engine.source.name, b'x' * 1000)
    assert after.ran
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'x' * 1000}
    assert_converged(engine)


def test_open_on_a_one_tier_stack_after_a_replay_commits_its_directory_rename(tmp_path: Path, clock: Clock) -> None:
    """With no stage, the rename of the file's directory replayed after the lookup leaves the open at the new place."""
    engine = source_served(tmp_path, clock, {'data/a.txt': b'alpha'}, tiers=1)
    root = tmp_path / 'project'
    engine.rename('/data', '/moved')
    after = AfterLookup(engine, '/moved/a.txt', engine.flush)
    assert_reads(engine, '/moved/a.txt', engine.source.name, b'alpha')
    assert after.ran
    assert tree(root) == {'moved': 'DIR', 'moved/a.txt': b'alpha'}
    assert_converged(engine)


def test_open_promotes_after_a_replay_commits_its_own_rename(tmp_path: Path, clock: Clock) -> None:
    """
    A file the stage admits, held only on the source behind its own queued rename, is promoted from the place a
    replay after the lookup moved it to: the stage serves it, no intent is left, and both copies are clean.
    """
    engine = source_served(tmp_path, clock, {'a.txt': b'small'})
    root = tmp_path / 'project'
    engine.rename('/a.txt', '/s.txt')                        # deferred: the source still holds /a.txt
    after = AfterLookup(engine, '/s.txt', engine.flush)
    assert_reads(engine, '/s.txt', 'scratch', b'small')
    assert after.ran
    assert engine.journal.pending() == []
    residencies = {r.tier: (r.state, r.size) for r in engine.catalog.residencies('/s.txt')}
    assert residencies == {'project': ('clean', 5), 'scratch': ('clean', 5)}
    assert tree(root) == {'s.txt': b'small'}
    assert_converged(engine)


def test_open_promotes_after_a_replay_commits_its_directory_rename(tmp_path: Path, clock: Clock) -> None:
    """The same, with the rename of the file's directory replayed after the lookup."""
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    root = tmp_path / 'project'
    engine.rename('/data', '/moved')
    after = AfterLookup(engine, '/moved/s.txt', engine.flush)
    assert_reads(engine, '/moved/s.txt', 'scratch', b'small')
    assert after.ran
    assert engine.journal.pending() == []
    residencies = {r.tier: (r.state, r.size) for r in engine.catalog.residencies('/moved/s.txt')}
    assert residencies == {'project': ('clean', 5), 'scratch': ('clean', 5)}
    assert tree(root) == {'moved': 'DIR', 'moved/s.txt': b'small'}
    assert_converged(engine)


def test_open_after_a_replay_moves_another_file_onto_the_old_source_place(tmp_path: Path, clock: Clock) -> None:
    """
    After the lookup, a rename onto the place the source held the file at runs the file's queued rename (its
    vacate), and a flush moves the other file's object onto that place. The open serves the file's own bytes.
    """
    engine = source_served(tmp_path, clock, {'a.bin': b'a' * 1000, 'b.bin': b'b' * 1000})
    root = tmp_path / 'project'
    engine.rename('/a.bin', '/t.bin')                        # deferred: the source still holds /a.bin
    after = AfterLookup(engine, '/t.bin', swap_onto(engine, '/b.bin', '/a.bin'))
    assert_reads(engine, '/t.bin', engine.source.name, b'a' * 1000)
    assert after.ran
    assert tree(root) == {'a.bin': b'b' * 1000, 't.bin': b'a' * 1000}
    assert_converged(engine)


def test_open_promotes_nothing_from_another_file_moved_onto_the_old_source_place(tmp_path: Path,
                                                                                 clock: Clock) -> None:
    """The same swap, on files the stage admits: the promotion copies the file's own bytes, not the other file's."""
    engine = source_served(tmp_path, clock, {'a.bin': b'apple', 'b.bin': b'berry'})
    root = tmp_path / 'project'
    engine.rename('/a.bin', '/t.bin')
    after = AfterLookup(engine, '/t.bin', swap_onto(engine, '/b.bin', '/a.bin'))
    assert_reads(engine, '/t.bin', 'scratch', b'apple')
    assert after.ran
    assert (tmp_path / 'scratch' / 't.bin').read_bytes() == b'apple'
    assert tree(root) == {'a.bin': b'berry', 't.bin': b'apple'}
    assert_converged(engine)


def test_open_of_a_source_served_file_opens_under_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """The open of a copy served off the source runs with the source lock held, and releases it on return."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    engine.rename('/big.bin', '/moved.bin')                  # deferred: the source still holds /big.bin
    source = engine.source
    real = source.open_direct
    free = []
    probe = lock_probe(engine, free)

    def probed(*args, **kwargs):
        delattr(source, 'open_direct')                       # once: the tier's own method again
        probe()
        return real(*args, **kwargs)

    source.open_direct = probed
    handle = engine.open('/moved.bin')
    engine.release(handle)
    assert free == [False]                                   # another thread could not take the lock
    probe()
    assert free == [False, True]                             # and can once the open returns
    engine.flush()
    assert_converged(engine)


def test_open_promotes_off_the_source_under_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """A promotion off the source reads the source object with the source lock held, and releases it on return."""
    engine = source_served(tmp_path, clock, {'a.txt': b'small'})
    engine.rename('/a.txt', '/s.txt')
    source = engine.source
    real = source.open_read
    free = []
    probe = lock_probe(engine, free)

    def probed(*args, **kwargs):
        delattr(source, 'open_read')                         # once: the tier's own method again
        probe()
        return real(*args, **kwargs)

    source.open_read = probed
    assert_reads(engine, '/s.txt', 'scratch', b'small')
    assert free == [False]
    probe()
    assert free == [False, True]
    engine.flush()
    assert_converged(engine)


def test_open_of_a_source_served_file_resolves_inside_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """A replay that moves the object before the open takes the lock leaves the open at the object's new place."""
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine.rename('/big.bin', '/moved.bin')
    engine._source_lock = Interleaved(engine, engine.flush)
    handle = engine.open('/moved.bin')
    try:
        assert engine._source_lock.ran                       # the replay ran inside the open, before its release
        assert engine.read(handle, 1001, 0) == b'x' * 1000
    finally:
        engine.release(handle)
    assert tree(root) == {'moved.bin': b'x' * 1000}
    assert_converged(engine)


def test_open_promotes_from_where_a_replay_during_the_wait_moved_the_file(tmp_path: Path, clock: Clock) -> None:
    """A replay that moves the object before the promotion takes the lock leaves the copy reading the new place."""
    engine = source_served(tmp_path, clock, {'a.txt': b'small'})
    root = tmp_path / 'project'
    engine.rename('/a.txt', '/s.txt')
    engine._source_lock = Interleaved(engine, engine.flush)
    handle = engine.open('/s.txt')
    try:
        assert engine._source_lock.ran
        assert handle.tier == 'scratch'
        assert engine.read(handle, 6, 0) == b'small'
    finally:
        engine.release(handle)
    assert engine.journal.pending() == []
    assert tree(root) == {'s.txt': b'small'}
    assert_converged(engine)


def test_open_after_a_concurrent_rename_moves_the_file_away(tmp_path: Path, clock: Clock) -> None:
    """A directory rename landing before the open takes the lock takes the file off the path: ENOENT."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    engine._source_lock = Interleaved(engine, lambda: engine.rename('/data', '/moved'))
    with raises(FileNotFoundError):
        engine.open('/data/big.bin')                         # the path lock covers the file, not its directory
    assert engine._source_lock.ran
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'x' * 1000}
    assert engine.getattr('/moved/big.bin').size == 1000
    engine.flush()
    assert_converged(engine)


def test_open_leaves_a_file_that_reused_the_entry_id_alone(tmp_path: Path, clock: Clock) -> None:
    """A new file that took the removed entry's id under another path is not found at the path: ENOENT."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id
    engine._source_lock = Interleaved(engine, concurrent_removal(engine, recreate=b'N' * 500))
    with raises(FileNotFoundError):
        engine.open('/data/big.bin')
    assert engine._source_lock.ran
    assert engine.getattr('/moved/big.bin').id == old        # reused, but off the path: the path check rejects it
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'N' * 500}
    assert_converged(engine)


def test_open_leaves_another_file_carried_onto_the_path_alone(tmp_path: Path, clock: Clock) -> None:
    """
    With no stage, a different file whose bytes sit on the source, carried onto the path by a directory rename
    before the open takes the lock, is found there and refused: ENOENT.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000}, tiers=1)
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id

    def action() -> None:
        engine.rename('/data', '/moved')
        engine.mkdir('/other')
        engine.create('/other/big.bin')
        rewrite(engine, '/other/big.bin', b'N' * 500)
        engine.rename('/other', '/data')

    engine._source_lock = Interleaved(engine, action)
    with raises(FileNotFoundError):
        engine.open('/data/big.bin')
    assert engine._source_lock.ran
    assert engine.getattr('/data/big.bin').id != old         # a file is at the path: only the id check rejects it
    assert engine.getattr('/data/big.bin').size == 500
    assert engine.getattr('/moved/big.bin').size == 1000
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'N' * 500, 'moved': 'DIR', 'moved/big.bin': b'x' * 1000}
    assert_converged(engine)


def test_open_refuses_a_symlink_that_took_the_entry_id(tmp_path: Path, clock: Clock) -> None:
    """
    A symbolic link that took the entry's id after a purge, carried onto the path before the open takes the lock,
    is refused with ENOENT: the kind is decided again under the source lock. The link's target keeps its bytes.
    """
    root = tmp_path / 'project'
    seed(root, 'data/big.bin', b'x' * 1000)
    seed(root, 'other.bin', b'O' * 50)
    (root / 'deep').mkdir()
    engine = OverlayEngine(build_spec(tmp_path, tiers=1), clock=clock)
    engine.start()
    engine.readdir('/')                                      # /deep is cataloged but not listed
    walk(engine, '/data')
    old = engine.getattr('/data/big.bin').id

    def action() -> None:
        concurrent_removal(engine)()                         # /data renamed to /moved, the file removed and purged
        os.symlink('../other.bin', root / 'deep' / 'link')
        assert engine.getattr('/deep/link').id == old        # adopted from the unlisted directory: the id is reused
        engine.rename('/deep/link', '/moved/big.bin')
        engine.rename('/moved', '/data')

    engine._source_lock = Interleaved(engine, action)
    with raises(FileNotFoundError):
        engine.open('/data/big.bin')
    assert engine._source_lock.ran
    assert engine.getattr('/data/big.bin').kind == 'symlink'
    assert (root / 'other.bin').read_bytes() == b'O' * 50
    engine.unlink('/data/big.bin')
    walk(engine)
    engine.flush()
    assert_converged(engine)


def test_open_of_a_staged_copy_takes_no_source_lock(tmp_path: Path, clock: Clock) -> None:
    """The open of a copy the stage serves waits for no source commit: it runs without the source lock."""
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    stage = engine.tiers['scratch']
    real = stage.open_direct
    free = []
    probe = lock_probe(engine, free)

    def probed(*args, **kwargs):
        delattr(stage, 'open_direct')                        # once: the tier's own method again
        probe()
        return real(*args, **kwargs)

    stage.open_direct = probed
    assert_reads(engine, '/data/s.txt', 'scratch', b'small')
    assert free == [True]                                    # another thread could take the lock meanwhile
    assert_converged(engine)


def test_open_promotes_from_where_the_source_holds_a_file_behind_its_queued_rename(tmp_path: Path,
                                                                                   clock: Clock) -> None:
    """With no replay, a promotion reads the pre-rename place the source still holds, which stays until the flush."""
    engine = source_served(tmp_path, clock, {'a.txt': b'small'})
    root = tmp_path / 'project'
    engine.rename('/a.txt', '/s.txt')                        # deferred: the source still holds /a.txt
    assert_reads(engine, '/s.txt', 'scratch', b'small')
    assert tree(root) == {'a.txt': b'small'}
    engine.flush()
    assert tree(root) == {'s.txt': b'small'}
    assert_converged(engine)


def test_open_takes_no_path_lock_and_no_lookup_under_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """
    Through a source-served open and a promotion off the source, nothing the calling thread runs with the source
    lock held takes a path lock or calls `_ensure`, `getattr` or `readdir`.
    """
    engine = source_served(tmp_path, clock, {'big.bin': b'x' * 1000, 'a.txt': b'small'})
    engine.rename('/big.bin', '/moved.bin')
    engine.rename('/a.txt', '/s.txt')
    owned = []
    wrapped = [(engine, '_ensure'), (engine, 'getattr'), (engine, 'readdir'), (engine.locks, 'hold')]
    for owner, name in wrapped:
        def recorded(*args, real=getattr(owner, name), **kwargs):
            owned.append(engine._source_lock._is_owned())
            return real(*args, **kwargs)
        setattr(owner, name, recorded)
    assert_reads(engine, '/moved.bin', engine.source.name, b'x' * 1000)
    assert_reads(engine, '/s.txt', 'scratch', b'small')
    for owner, name in wrapped:
        delattr(owner, name)
    assert owned and not any(owned)                          # called, and never with the source lock held
    engine.flush()
    assert_converged(engine)


def test_open_promotes_from_a_staged_copy_without_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """A promotion from one stage to a faster one reads the slower stage without the source lock."""
    root = tmp_path / 'project'
    seed(root, 'filler.txt', b'fill')
    seed(root, 's.txt', b'small')
    engine = OverlayEngine(build_spec(tmp_path, tiers=3, tmp_quota=QuotaSpec(files=1)), clock=clock)
    engine.start()
    walk(engine)
    assert_reads(engine, '/filler.txt', 'tmp', b'fill')      # the fastest stage takes its one file
    assert_reads(engine, '/s.txt', 'scratch', b'small')      # so the middle stage takes this one
    assert engine.evict_copy('/filler.txt', 'tmp')
    stage = engine.tiers['scratch']
    real = stage.open_read
    free = []
    probe = lock_probe(engine, free)

    def probed(*args, **kwargs):
        delattr(stage, 'open_read')                          # once: the tier's own method again
        probe()
        return real(*args, **kwargs)

    stage.open_read = probed
    assert_reads(engine, '/s.txt', 'tmp', b'small')          # promoted from the middle stage
    assert free == [True]                                    # another thread could take the source lock meanwhile
    assert_converged(engine)


# -- an ancestor's rename lands in no window of the cut: the cut records where it moves the file, or refuses ----

JOIN = 10.0                     # seconds any wait may take; a deadlock fails the test instead of hanging it


class Watched:
    """
    Stands in for the source lock. When a thread in `waits` finds it held and has to wait for it, that thread's
    event is set first. The real lock is re-entrant and is taken and released exactly as the engine asks.
    """

    def __init__(self, real) -> None:
        self.real, self.waits = real, {}

    def acquire(self, blocking: bool = True) -> bool:
        if self.real.acquire(blocking=False):
            return True
        if (event := self.waits.get(threading.current_thread())) is not None:
            event.set()
        return self.real.acquire(blocking=blocking)

    def release(self) -> None:
        self.real.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class WaitSignal:
    """Set by a `Watched` lock when its thread must wait: records that, and wakes the driver's `event`."""

    def __init__(self, event: threading.Event) -> None:
        self.event, self.seen = event, False

    def set(self) -> None:
        self.seen = True
        self.event.set()


def stop_at(owner, name: str, pause, after: bool = False) -> None:
    """Wrap `owner.name` so that `pause()` runs before (or after) the real call."""
    real = getattr(owner, name)

    def stopped(*args, **kwargs):
        if not after:
            pause()
        result = real(*args, **kwargs)
        if after:
            pause()
        return result

    setattr(owner, name, stopped)


def race(engine: OverlayEngine, old: str, new: str, cut, stop, rename_first: bool = True) -> dict:
    """
    Run `engine.rename(old, new)` and `cut()` on two threads of their own, and return each one's exception (None
    on success) under 'rename' and 'cut', whether the cut reached its stop under 'stopped', and the names of the
    threads that had to wait for the source lock under 'waited'. `stop(engine, pause)` installs `pause` where the
    cut is to stop, once.

    With `rename_first`, the rename stops just after its `_vacate` returns: it has cleared its target and has not
    yet moved the catalog. The cut then starts and runs until it reaches its stop or has to wait for the source
    lock. The rename goes on until it finishes or has to wait for the source lock; then the cut goes on. Without
    it, the cut starts first and stops, the rename runs until it finishes or has to wait for the source lock, and
    the cut goes on. Either way, no step waits on a thread that waits on the other, whatever the engine locks.
    """
    cut_stopped, rename_stopped, past, go_rename, go_cut = (threading.Event() for _ in range(5))
    outcome: dict = {}
    lock = Watched(engine._source_lock)

    def run(name: str, call, done: threading.Event | None = None) -> None:
        try:
            call()
        except BaseException as error:                       # read in the test's thread
            outcome[name] = error
        else:
            outcome[name] = None
        finally:
            if done is not None:
                done.set()

    renamer = threading.Thread(target=run, args=('rename', lambda: engine.rename(old, new), rename_stopped),
                               daemon=True)
    cutter = threading.Thread(target=run, args=('cut', cut, cut_stopped), daemon=True)
    lock.waits = {renamer: WaitSignal(rename_stopped), cutter: WaitSignal(cut_stopped)}
    real_vacate, paused = engine._vacate, []

    def vacate(*args, **kwargs) -> None:
        real_vacate(*args, **kwargs)
        if rename_first and threading.current_thread() is renamer:
            past.set()
            assert go_rename.wait(JOIN)

    def pause() -> None:
        if threading.current_thread() is cutter and not paused:
            paused.append(True)
            cut_stopped.set()
            assert go_cut.wait(JOIN)

    engine._source_lock, engine._vacate = lock, vacate
    stop(engine, pause)
    try:
        if rename_first:
            renamer.start()
            assert past.wait(JOIN)
            cutter.start()
            assert cut_stopped.wait(JOIN)
            go_rename.set()
            assert rename_stopped.wait(JOIN)
        else:
            cutter.start()
            assert cut_stopped.wait(JOIN)
            renamer.start()
            assert rename_stopped.wait(JOIN)
        go_cut.set()
        cutter.join(JOIN)
        renamer.join(JOIN)
        assert not renamer.is_alive() and not cutter.is_alive()
    finally:
        go_rename.set()
        go_cut.set()
        engine._source_lock = lock.real
        del engine._vacate
    outcome['stopped'] = bool(paused)
    outcome['waited'] = {name for name, thread in (('rename', renamer), ('cut', cutter)) if lock.waits[thread].seen}
    return outcome


def assert_counted(engine: OverlayEngine) -> None:
    """Every stage's counter is the sum of its residency rows' sizes and their count, and neither is negative."""
    for spec in engine.spec.tiers[1:]:
        rows = list(engine.db.execute(t'SELECT size FROM residency WHERE tier = {spec.name}'))
        usage = dataclasses.astuple(engine.registry.usage(spec.name))
        assert usage == (sum(row['size'] for row in rows), len(rows)) and min(usage) >= 0, spec.name


def assert_recorded_or_refused(engine: OverlayEngine, outcome: dict, path: str, original: bytes, length: int) -> bool:
    """
    The rename succeeded; the cut either returned, with `getattr` at the file's new `path`, the fastest copy's
    residency and a read all at `length`, or raised ENOENT with the file whole on every tier that holds it. No
    intent is left unresolved and every stage's counter agrees. Returns whether the cut returned.
    """
    returned = cut_returned(outcome)
    assert engine.journal.pending() == []
    assert_counted(engine)
    expected = length if returned else len(original)
    assert_cut(engine, path, original, expected)
    assert engine.catalog.fastest_copy(path).size == expected
    if not returned:
        assert {r.size for r in engine.catalog.residencies(path)} == {len(original)}
    return returned


def cut_returned(outcome: dict) -> bool:
    """The rename succeeded, and the cut either returned or raised ENOENT; whether it returned."""
    assert outcome['rename'] is None, repr(outcome['rename'])
    error = outcome['cut']
    assert error is None or (isinstance(error, FileNotFoundError) and error.errno == errno.ENOENT), repr(error)
    return error is None


def assert_counted_through(engine: OverlayEngine, path: str) -> None:
    """Counters agree after the queue drains, after each stage copy's eviction, and after an open and release."""
    engine.flush()
    assert_counted(engine)
    for residency in engine.catalog.residencies(path):
        if residency.tier != engine.source.name:
            assert engine.evict_copy(path, residency.tier)
    assert_counted(engine)
    engine.release(engine.open(path))
    assert_counted(engine)


def staged(tmp_path: Path, clock: Clock) -> OverlayEngine:
    """A 5-byte `/data/s.txt` the stage serves (`bypass_size` 100), opened once to promote it."""
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    engine.release(engine.open('/data/s.txt'))
    assert {r.tier: (r.state, r.size) for r in engine.catalog.residencies('/data/s.txt')} == {
        'project': ('clean', 5), 'scratch': ('clean', 5)}
    return engine


def cut_staged_past_vacate(tmp_path: Path, clock: Clock, stop) -> None:
    """The staged file is cut to 2 bytes while its directory's rename, past its `_vacate`, races it."""
    engine = staged(tmp_path, clock)
    outcome = race(engine, '/data', '/moved', lambda: engine.truncate('/data/s.txt', 2), stop)
    assert_recorded_or_refused(engine, outcome, '/moved/s.txt', b'small', 2)
    assert_counted_through(engine, '/moved/s.txt')
    assert_converged(engine)


def test_truncate_without_a_handle_of_a_staged_copy_meets_a_directory_rename_past_its_vacate(
        tmp_path: Path, clock: Clock) -> None:
    """Racing the cut once the stage copy is open, the rename's catalog move lands outside the cut."""
    cut_staged_past_vacate(tmp_path, clock,
                           lambda e, pause: stop_at(e.tiers['scratch'], 'open_direct', pause, after=True))


def test_truncate_without_a_handle_counts_its_cut_under_a_directory_rename_past_its_vacate(
        tmp_path: Path, clock: Clock) -> None:
    """The same, once the cut has read the residency its counter change is computed from."""
    cut_staged_past_vacate(tmp_path, clock,
                           lambda e, pause: stop_at(e.catalog, 'get_residency', pause, after=True))


def test_truncate_without_a_handle_decides_its_serving_copy_outside_a_directory_rename_past_its_vacate(
        tmp_path: Path, clock: Clock) -> None:
    """The same, at the serving decision."""
    cut_staged_past_vacate(tmp_path, clock, lambda e, pause: stop_at(e.catalog, 'fastest_copy', pause))


def test_truncate_without_a_handle_marks_dirty_outside_a_directory_rename_past_its_vacate(
        tmp_path: Path, clock: Clock) -> None:
    """The same, at the dirty mark."""
    cut_staged_past_vacate(tmp_path, clock, lambda e, pause: stop_at(e.journal, 'mark_dirty', pause))


def test_truncate_without_a_handle_of_a_source_served_file_meets_an_ancestor_rename_past_its_vacate(
        tmp_path: Path, clock: Clock) -> None:
    """A file served off the source, two levels below the renamed directory, once the source object is open."""
    data = bytes(range(200))
    engine = source_served(tmp_path, clock, {'data/deep/big.bin': data})
    outcome = race(engine, '/data', '/moved', lambda: engine.truncate('/data/deep/big.bin', 2),
                   lambda e, pause: stop_at(e.tiers['project'], 'open_direct', pause, after=True))
    assert_recorded_or_refused(engine, outcome, '/moved/deep/big.bin', data, 2)
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_promotes_outside_a_directory_rename_past_its_vacate(
        tmp_path: Path, clock: Clock) -> None:
    """
    A promotion off the source the cut triggers, once the stage holds the copied bytes: no `promote` intent is
    left, and the stage holds the file's bytes only where a residency row says.
    """
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    outcome = race(engine, '/data', '/moved', lambda: engine.truncate('/data/s.txt', 2),
                   lambda e, pause: stop_at(e.tiers['scratch'], 'put', pause, after=True))
    returned = cut_returned(outcome)                         # read before a check's own open promotes the file
    assert tree(tmp_path / 'scratch') == ({'moved': 'DIR', 'moved/s.txt': b'sm'} if returned else {})
    assert ('scratch' in {r.tier for r in engine.catalog.residencies('/moved/s.txt')}) == returned
    assert assert_recorded_or_refused(engine, outcome, '/moved/s.txt', b'small', 2) == returned
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_promotion_looks_up_outside_a_directory_rename_past_its_vacate(
        tmp_path: Path, clock: Clock) -> None:
    """The same where the promotion looks the entry up again, before it opens an intent."""
    engine = source_served(tmp_path, clock, {'data/s.txt': b'small'})
    outcome = race(engine, '/data', '/moved', lambda: engine.truncate('/data/s.txt', 2),
                   lambda e, pause: stop_at(e, '_lookup_again', pause))
    returned = cut_returned(outcome)
    assert tree(tmp_path / 'scratch') == ({'moved': 'DIR', 'moved/s.txt': b'sm'} if returned else {})
    assert ('scratch' in {r.tier for r in engine.catalog.residencies('/moved/s.txt')}) == returned
    assert assert_recorded_or_refused(engine, outcome, '/moved/s.txt', b'small', 2) == returned
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_records_its_cut_before_a_waiting_directory_rename_moves_it(
        tmp_path: Path, clock: Clock) -> None:
    """
    A directory rename that reaches the source lock while the cut holds it waits, and the cut is recorded where
    the rename then moves the file: its size, its stage copy's size and the stage's counter agree.
    """
    engine = staged(tmp_path, clock)
    outcome = race(engine, '/data', '/moved', lambda: engine.truncate('/data/s.txt', 2),
                   lambda e, pause: stop_at(e.tiers['scratch'], 'open_direct', pause, after=True),
                   rename_first=False)
    assert outcome['stopped'] and outcome['waited'] == {'rename'}    # the cut held the lock at its stop
    assert assert_recorded_or_refused(engine, outcome, '/moved/s.txt', b'small', 2)
    assert {r.tier: (r.state, r.size) for r in engine.catalog.residencies('/moved/s.txt')} == {
        'project': ('clean', 5), 'scratch': ('dirty', 2)}
    assert_counted_through(engine, '/moved/s.txt')
    assert_converged(engine)


def test_rename_moves_stage_copies_outside_the_source_lock(tmp_path: Path, clock: Clock) -> None:
    """A rename holds the source lock through its catalog move, and moves the stage copies after releasing it."""
    engine = staged(tmp_path, clock)
    stage = engine.tiers['scratch']
    real = stage.rename
    free = []
    probe = lock_probe(engine, free)

    def probed(*args, **kwargs):
        delattr(stage, 'rename')                             # once: the tier's own method again
        probe()
        return real(*args, **kwargs)

    stage.rename = probed
    engine.rename('/data', '/moved')
    assert free == [True]
    assert_reads(engine, '/moved/s.txt', 'scratch', b'small')
    engine.flush()
    assert_converged(engine)


# -- a file that took a purged entry's id is told from that entry ------------------------------------------------

def reuse_route(engine: OverlayEngine, name: str, data: bytes, served: str) -> object:
    """
    An action: rename `/data` to `/moved`, remove `/moved/{name}` and drain, which purges its entry and frees its
    id; create a file there, which takes the id, holding `data`, drained to the source and kept on the stage
    (`served='stage'`) or evicted from it (`served='source'`); then rename `/moved` back to `/data`.
    """
    def action() -> None:
        concurrent_removal(engine, recreate=data)()
        if served == 'source':
            assert engine.evict_copy(f'/moved/{name}', 'scratch')
        engine.rename('/moved', '/data')
    return action


def other_purged(engine: OverlayEngine) -> object:
    """An action: remove `/other.bin` and drain, which purges its tombstone and records its id."""
    def action() -> None:
        engine.unlink('/other.bin')
        engine.flush()
    return action


def other_created(engine: OverlayEngine) -> object:
    """
    An action: remove `/other.bin`, then create a file at its path, which first runs its queued removal (purging
    its tombstone) and then takes the id it freed.
    """
    def action() -> None:
        old = engine.getattr('/other.bin').id
        engine.unlink('/other.bin')
        engine.create('/other.bin')
        assert engine.getattr('/other.bin').id == old        # the id is reused
    return action


def with_other(engine: OverlayEngine) -> OverlayEngine:
    """Add `/other.bin`, on the source, holding the largest entry id, so its purge frees an id a create takes."""
    engine.create('/other.bin')
    rewrite(engine, '/other.bin', b'O' * 10)
    engine.flush()
    return engine


def first_call(caller: int, action):
    """A pause for `stop_at`: the first time `caller`'s thread reaches it, `action` runs to completion on another."""
    state = {'done': False, 'error': None}

    def run() -> None:
        try:
            action()
        except BaseException as error:                       # raised again in the caller's thread
            state['error'] = error

    def pause() -> None:
        if state['done'] or threading.get_ident() != caller:
            return
        state['done'] = True
        helper = threading.Thread(target=run, daemon=True)
        helper.start()
        helper.join(JOIN)
        assert not helper.is_alive()
        if state['error'] is not None:
            raise state['error']
    return pause


def test_truncate_without_a_handle_refuses_a_source_served_file_that_took_the_entry_id(tmp_path: Path,
                                                                                     clock: Clock) -> None:
    """A file served off the source that took the entry's id after its purge, carried onto the path: ENOENT."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id
    engine._source_lock = Interleaved(engine, reuse_route(engine, 'big.bin', b'N' * 500, 'source'))
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert engine._watches == []
    assert engine.getattr('/data/big.bin').id == old         # the file at the path took the id
    assert engine.getattr('/data/big.bin').size == 500
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'N' * 500}      # the rename back is queued
    assert {r.tier: r.state for r in engine.catalog.residencies('/data/big.bin')} == {'project': 'clean'}
    assert engine.journal.pending() == []
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'N' * 500}
    assert_converged(engine)


def test_truncate_without_a_handle_refuses_a_staged_file_that_took_the_entry_id(tmp_path: Path,
                                                                              clock: Clock) -> None:
    """The same where a stage serves the file that took the id: nothing is marked dirty or cut on the stage."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id
    engine._source_lock = Interleaved(engine, reuse_route(engine, 'big.bin', b'N' * 500, 'stage'))
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert engine._watches == []
    assert engine.getattr('/data/big.bin').id == old
    assert engine.getattr('/data/big.bin').size == 500
    assert tree(tmp_path / 'scratch') == {'data': 'DIR', 'data/big.bin': b'N' * 500}
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'N' * 500}
    assert {r.tier: r.state for r in engine.catalog.residencies('/data/big.bin')} == {
        'project': 'clean', 'scratch': 'clean'}
    assert engine.journal.pending() == []
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'N' * 500}
    assert_converged(engine)


def test_open_refuses_a_source_served_file_that_took_the_entry_id(tmp_path: Path, clock: Clock) -> None:
    """A source-served open finds a file that took the entry's id on the path: ENOENT, and no handle is kept."""
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id
    engine._source_lock = Interleaved(engine, reuse_route(engine, 'big.bin', b'N' * 500, 'source'))
    with raises(FileNotFoundError):
        engine.open('/data/big.bin')
    assert engine._source_lock.ran
    assert engine._watches == []
    assert engine.getattr('/data/big.bin').id == old
    assert old not in engine._handles
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'N' * 500}
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'N' * 500}
    assert_converged(engine)


def test_open_that_promotes_refuses_a_file_that_took_the_entry_id(tmp_path: Path, clock: Clock) -> None:
    """
    An open whose file a stage admits finds, as its promotion takes the source lock, a file that took the entry's
    id on the path: ENOENT before any intent opens; the stage holds no bytes and no residency for it.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 50})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id
    engine._source_lock = Interleaved(engine, reuse_route(engine, 'big.bin', b'N' * 30, 'source'))
    with raises(FileNotFoundError):
        engine.open('/data/big.bin')
    assert engine._source_lock.ran
    assert engine._watches == []
    assert engine.getattr('/data/big.bin').id == old
    assert old not in engine._handles
    assert engine.journal.pending() == []
    assert tree(tmp_path / 'scratch') == {'data': 'DIR'}
    assert engine.catalog.get_residency('/data/big.bin', 'scratch') is None
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'N' * 30}
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'N' * 30}
    assert_converged(engine)


def test_truncate_without_a_handle_cuts_when_another_entry_is_purged_during_the_wait(tmp_path: Path,
                                                                                    clock: Clock) -> None:
    """A flush during the wait that purges another entry's tombstone does not refuse the cut."""
    engine = with_other(source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000}))
    engine._source_lock = Interleaved(engine, other_purged(engine))
    engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert_cut(engine, '/data/big.bin', b'x' * 1000, 4)
    assert tree(tmp_path / 'project') == {'data': 'DIR', 'data/big.bin': b'xxxx'}
    assert_converged(engine)


def test_truncate_without_a_handle_cuts_when_another_file_is_created_during_the_wait(tmp_path: Path,
                                                                                    clock: Clock) -> None:
    """A create of another file during the wait, which purges a tombstone and takes its id, does not refuse."""
    engine = with_other(source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000}))
    engine._source_lock = Interleaved(engine, other_created(engine))
    engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran
    assert_cut(engine, '/data/big.bin', b'x' * 1000, 4)
    engine.flush()
    assert tree(tmp_path / 'project') == {'data': 'DIR', 'data/big.bin': b'xxxx', 'other.bin': b''}
    assert_converged(engine)


def test_open_of_a_source_served_file_opens_when_another_entry_is_purged_during_the_wait(tmp_path: Path,
                                                                                       clock: Clock) -> None:
    """A flush during a source-served open's wait that purges another entry's tombstone does not refuse it."""
    engine = with_other(source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000}))
    engine._source_lock = Interleaved(engine, other_purged(engine))
    handle = engine.open('/data/big.bin')
    try:
        assert engine._source_lock.ran
        assert handle.tier == 'project'
        assert engine.read(handle, 2000, 0) == b'x' * 1000
    finally:
        engine.release(handle)
    assert_converged(engine)


def test_open_of_a_source_served_file_opens_when_another_file_is_created_during_the_wait(tmp_path: Path,
                                                                                       clock: Clock) -> None:
    """A create of another file during a source-served open's wait, taking a purged id, does not refuse it."""
    engine = with_other(source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000}))
    engine._source_lock = Interleaved(engine, other_created(engine))
    handle = engine.open('/data/big.bin')
    try:
        assert engine._source_lock.ran
        assert handle.tier == 'project'
        assert engine.read(handle, 2000, 0) == b'x' * 1000
    finally:
        engine.release(handle)
    engine.flush()
    assert_converged(engine)


def test_open_that_promotes_promotes_when_another_entry_is_purged_during_the_wait(tmp_path: Path,
                                                                                 clock: Clock) -> None:
    """A flush during a promotion's wait that purges another entry's tombstone does not refuse the promotion."""
    engine = with_other(source_served(tmp_path, clock, {'data/s.bin': b'x' * 50}))
    engine._source_lock = Interleaved(engine, other_purged(engine))
    handle = engine.open('/data/s.bin')
    try:
        assert engine._source_lock.ran
        assert handle.tier == 'scratch'
        assert engine.read(handle, 100, 0) == b'x' * 50
    finally:
        engine.release(handle)
    assert tree(tmp_path / 'scratch') == {'data': 'DIR', 'data/s.bin': b'x' * 50}
    assert engine.journal.pending() == []
    assert_converged(engine)


def test_open_that_promotes_promotes_when_another_file_is_created_during_the_wait(tmp_path: Path,
                                                                                 clock: Clock) -> None:
    """A create of another file during a promotion's wait, taking a purged id, does not refuse the promotion."""
    engine = with_other(source_served(tmp_path, clock, {'data/s.bin': b'x' * 50}))
    engine._source_lock = Interleaved(engine, other_created(engine))
    handle = engine.open('/data/s.bin')
    try:
        assert engine._source_lock.ran
        assert handle.tier == 'scratch'
        assert engine.read(handle, 100, 0) == b'x' * 50
    finally:
        engine.release(handle)
    assert engine.journal.pending() == []
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_names_a_file_that_took_a_purged_id_before_its_first_lookup(
        tmp_path: Path, clock: Clock) -> None:
    """
    A file that took a purged entry's id and was carried onto the path before the cut's first lookup read it is the
    file the cut names, though the purge landed after the cut opened its watch: the lookup clears the watch as it
    reads, and the cut cuts that file.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    old = engine.getattr('/data/big.bin').id
    stop_at(engine, '_ensure', first_call(threading.get_ident(),
                                          reuse_route(engine, 'big.bin', b'N' * 500, 'source')))
    engine.truncate('/data/big.bin', 4)
    assert engine.getattr('/data/big.bin').id == old
    assert_cut(engine, '/data/big.bin', b'N' * 500, 4)
    assert tree(tmp_path / 'project') == {'moved': 'DIR', 'moved/big.bin': b'NNNN'}
    engine.flush()
    assert tree(tmp_path / 'project') == {'data': 'DIR', 'data/big.bin': b'NNNN'}
    assert_converged(engine)


def test_truncate_without_a_handle_refuses_once_its_entry_is_purged_after_its_first_lookup(tmp_path: Path,
                                                                                         clock: Clock) -> None:
    """
    The entry the cut's first lookup returned is moved away, removed and purged before the cut has finished naming
    it, and a file that took its id is carried onto the path while the cut waits for the source lock: ENOENT.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    root = tmp_path / 'project'
    old = engine.getattr('/data/big.bin').id

    def purged() -> None:
        concurrent_removal(engine, recreate=b'N' * 500)()
        assert engine.evict_copy('/moved/big.bin', 'scratch')

    stop_at(engine, '_ensure', first_call(threading.get_ident(), purged), after=True)
    engine._source_lock = Interleaved(engine, lambda: engine.rename('/moved', '/data'))
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._source_lock.ran                           # the carry landed while the cut waited for the lock
    assert engine._watches == []
    assert engine.getattr('/data/big.bin').id == old
    assert engine.getattr('/data/big.bin').size == 500
    assert tree(root) == {'moved': 'DIR', 'moved/big.bin': b'N' * 500}
    engine.flush()
    assert tree(root) == {'data': 'DIR', 'data/big.bin': b'N' * 500}
    assert_converged(engine)


def test_truncate_without_a_handle_refuses_a_file_that_took_the_id_of_an_entry_removed_with_no_source_copy(
        tmp_path: Path, clock: Clock) -> None:
    """
    An entry the stage alone holds is purged by its removal itself, with no replay; a file that took its id,
    carried onto the path during the cut's wait, is refused with ENOENT and keeps its bytes.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    engine.create('/data/new.bin')                          # on the stage only, holding the largest id
    rewrite(engine, '/data/new.bin', b'y' * 10)
    old = engine.getattr('/data/new.bin').id

    def action() -> None:
        engine.rename('/data', '/moved')
        for row in engine.journal.queued(engine.source.name):
            if row.op == 'rename':
                engine.commit_row(row)                       # purges the rename's tombstone, which held a larger id
        assert engine.catalog.get_residency('/moved/new.bin', engine.source.name) is None
        engine.unlink('/moved/new.bin')                      # no source copy: purged here, freeing its id
        engine.create('/moved/new.bin')
        rewrite(engine, '/moved/new.bin', b'N' * 20)
        assert engine.getattr('/moved/new.bin').id == old    # the id is reused
        engine.rename('/moved', '/data')

    engine._source_lock = Interleaved(engine, action)
    with raises(FileNotFoundError):
        engine.truncate('/data/new.bin', 4)
    assert engine._source_lock.ran
    assert engine._watches == []
    assert engine.getattr('/data/new.bin').id == old
    assert engine.getattr('/data/new.bin').size == 20
    assert tree(tmp_path / 'scratch')['data/new.bin'] == b'N' * 20
    engine.flush()
    assert_converged(engine)


def test_truncate_without_a_handle_refuses_a_file_that_took_its_id_right_after_its_first_lookup(
        tmp_path: Path, clock: Clock) -> None:
    """
    The whole route, the file that took the id carried onto the path included, lands just after the cut's first
    lookup returned its entry and before the cut takes the source lock: ENOENT, and the new file keeps its bytes.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    old = engine.getattr('/data/big.bin').id
    stop_at(engine, '_ensure', first_call(threading.get_ident(),
                                          reuse_route(engine, 'big.bin', b'N' * 500, 'source')), after=True)
    with raises(FileNotFoundError):
        engine.truncate('/data/big.bin', 4)
    assert engine._watches == []
    assert engine.getattr('/data/big.bin').id == old
    assert engine.getattr('/data/big.bin').size == 500
    assert tree(tmp_path / 'project') == {'moved': 'DIR', 'moved/big.bin': b'N' * 500}
    engine.flush()
    assert tree(tmp_path / 'project') == {'data': 'DIR', 'data/big.bin': b'N' * 500}
    assert_converged(engine)


def test_truncate_without_a_handle_cuts_a_file_it_adopts_with_an_id_another_purge_freed(tmp_path: Path,
                                                                                      clock: Clock) -> None:
    """
    The cut's first lookup adopts its file from the source, and another entry's purge, landing after the lookup
    found the path uncataloged and before the adoption, frees the id the adoption takes: the cut is not refused.
    """
    root = tmp_path / 'project'
    seed(root, 'deep/f.bin', b'x' * 1000)
    engine = OverlayEngine(build_spec(tmp_path, tiers=2, scratch_quota=QuotaSpec(bypass_size=100)), clock=clock)
    engine.start()
    engine.readdir('/')                                      # /deep is cataloged but not listed
    with_other(engine)
    other = engine.getattr('/other.bin').id
    stop_at(engine.tiers['project'], 'stat', first_call(threading.get_ident(), other_purged(engine)))
    engine.truncate('/deep/f.bin', 4)
    assert engine.getattr('/deep/f.bin').id == other         # the adoption took the purged id
    assert_cut(engine, '/deep/f.bin', b'x' * 1000, 4)
    walk(engine)
    engine.flush()
    assert_converged(engine)


def test_open_refuses_a_source_served_file_that_took_its_id_right_after_its_first_lookup(tmp_path: Path,
                                                                                          clock: Clock) -> None:
    """
    The whole route, the file that took the id carried onto the path included, lands just after a source-served
    open's first lookup returned its entry: ENOENT, and no handle is kept for the id.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 1000})
    old = engine.getattr('/data/big.bin').id
    stop_at(engine, '_ensure', first_call(threading.get_ident(),
                                          reuse_route(engine, 'big.bin', b'N' * 500, 'source')), after=True)
    with raises(FileNotFoundError):
        engine.open('/data/big.bin')
    assert engine._watches == []
    assert engine.getattr('/data/big.bin').id == old
    assert old not in engine._handles
    engine.flush()
    assert tree(tmp_path / 'project') == {'data': 'DIR', 'data/big.bin': b'N' * 500}
    assert_converged(engine)


def test_open_that_promotes_refuses_a_file_that_took_its_id_right_after_its_first_lookup(tmp_path: Path,
                                                                                         clock: Clock) -> None:
    """
    The same just after the first lookup of an open whose file a stage admits: ENOENT before any intent opens, and
    the stage holds no bytes and no residency for the file that took the id.
    """
    engine = source_served(tmp_path, clock, {'data/big.bin': b'x' * 50})
    old = engine.getattr('/data/big.bin').id
    stop_at(engine, '_ensure', first_call(threading.get_ident(),
                                          reuse_route(engine, 'big.bin', b'N' * 30, 'source')), after=True)
    with raises(FileNotFoundError):
        engine.open('/data/big.bin')
    assert engine._watches == []
    assert engine.getattr('/data/big.bin').id == old
    assert engine.journal.pending() == []
    assert engine.catalog.get_residency('/data/big.bin', 'scratch') is None
    engine.flush()
    assert tree(tmp_path / 'project') == {'data': 'DIR', 'data/big.bin': b'N' * 30}
    assert_converged(engine)


def test_truncate_without_a_handle_is_not_refused_by_a_purge_that_committed_before_it_named_its_file(
        tmp_path: Path, clock: Clock) -> None:
    """
    A removal purges the entry holding the largest id. Just after its transaction commits, another thread adopts a
    file that takes the freed id, and a cut names that file and waits. A purge records before it commits, so the
    cut's watch never holds the id, and the cut goes ahead.
    """
    root = tmp_path / 'project'
    seed(root, 'deep/f.bin', b'x' * 1000)
    engine = OverlayEngine(build_spec(tmp_path, tiers=2, scratch_quota=QuotaSpec(bypass_size=100)), clock=clock)
    engine.start()
    engine.readdir('/')                                      # /deep is cataloged but not listed: f.bin adopts lazily
    engine.create('/s.bin')                                  # on the stage only, holding the largest id
    freed = engine.getattr('/s.bin').id
    remover = threading.get_ident()
    purged_here = {'flag': False}
    real_purge = engine.catalog.purge

    def purge(path):
        if threading.get_ident() == remover:
            purged_here['flag'] = True
        return real_purge(path)
    engine.catalog.purge = purge

    named, go = threading.Event(), threading.Event()
    outcome = {}
    real_ensure = engine._ensure

    def ensure(path):
        entry = real_ensure(path)
        if path == '/deep/f.bin' and threading.get_ident() != remover:
            named.set()
            assert go.wait(JOIN)
        return entry

    def cut() -> None:
        try:
            engine.truncate('/deep/f.bin', 4)
            outcome['cut'] = None
        except BaseException as error:                       # read in the test's thread
            outcome['cut'] = error

    cutter = threading.Thread(target=cut, daemon=True)
    real_transaction = engine.db.transaction
    from contextlib import contextmanager                    # local: an import above would shift the cited lines

    @contextmanager
    def transaction():
        with real_transaction() as conn:
            yield conn
        if threading.get_ident() == remover and purged_here['flag']:    # after the removal's commit
            purged_here['flag'] = False
            adopt = threading.Thread(target=engine.getattr, args=('/deep/f.bin',), daemon=True)
            adopt.start()
            adopt.join(JOIN)
            assert engine.catalog.get('/deep/f.bin').id == freed           # the adoption took the freed id
            engine._ensure = ensure
            cutter.start()
            assert named.wait(JOIN)                                         # the cut has named its file

    engine.db.transaction = transaction
    engine.unlink('/s.bin')
    del engine.db.transaction
    go.set()
    cutter.join(JOIN)
    assert not cutter.is_alive()
    assert outcome['cut'] is None
    del engine._ensure
    assert_cut(engine, '/deep/f.bin', b'x' * 1000, 4)
    walk(engine)
    engine.flush()
    assert_converged(engine)
