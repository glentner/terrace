# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the owner and group of created entries: the setgid rule, and agreement with the source."""

# Type annotations
from __future__ import annotations
from typing import Iterator, Optional

# Standard libs
import os
from pathlib import Path

# External libs
from pytest import fixture, mark, skip

# Internal libs
from terrace.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit

CALLER_UID = 4242
CALLER_GID = 4343
OTHER_GID = 5555


@fixture
def single(tmp_path: Path, clock: Clock) -> Iterator[OverlayEngine]:
    """A started one-tier engine: every file is created straight onto the source."""
    seed(tmp_path / 'project', 'top.txt', b'top-level')
    instance = OverlayEngine(build_spec(tmp_path, tiers=1), clock=clock)
    instance.start()
    yield instance
    if instance.started:
        instance.stop()


def assert_source_agrees(engine: OverlayEngine, root: Path, path: str) -> None:
    """The committed source object of `path` has the catalog's uid and gid."""
    entry = engine.catalog.get(path)
    assert entry is not None and entry.source_path is not None
    info = os.stat(root / entry.source_path.lstrip('/'))
    assert (info.st_uid, info.st_gid) == (entry.uid, entry.gid)


def spare_group(root: Path) -> Optional[int]:
    """A group of this process other than its own and the source root's, if it has one."""
    taken = {os.getgid(), root.stat().st_gid}
    spare = sorted(set(os.getgroups()) - taken)
    return spare[0] if spare else None


# -- the group rule -----------------------------------------------------------------------------------------------

def test_defaults_are_the_process_identity(engine: OverlayEngine) -> None:
    """With no identity given, a created file and directory record the engine process's own."""
    entry = engine.create('/new.dat')
    assert (entry.uid, entry.gid) == (os.getuid(), os.getgid())
    entry = engine.mkdir('/new')
    assert (entry.uid, entry.gid) == (os.getuid(), os.getgid())


def test_plain_parent_keeps_caller_group(engine: OverlayEngine) -> None:
    """Under a parent without the setgid bit the caller's identity is recorded as given."""
    entry = engine.create('/data/c.dat', uid=CALLER_UID, gid=OTHER_GID)
    assert (entry.uid, entry.gid) == (CALLER_UID, OTHER_GID)
    entry = engine.mkdir('/data/sub', 0o755, uid=CALLER_UID, gid=OTHER_GID)
    assert (entry.uid, entry.gid) == (CALLER_UID, OTHER_GID)
    assert entry.mode == 0o755


def test_setgid_parent_gives_its_group_and_bit(engine: OverlayEngine) -> None:
    """Under a setgid parent a file and directory take its group; only the directory takes the bit."""
    engine.mkdir('/project', 0o775, uid=CALLER_UID, gid=CALLER_GID)
    engine.chmod('/project', 0o2775)
    entry = engine.create('/project/a.dat', 0o644, uid=CALLER_UID, gid=OTHER_GID)
    assert (entry.uid, entry.gid) == (CALLER_UID, CALLER_GID)
    assert entry.mode == 0o644
    entry = engine.mkdir('/project/run', 0o755, uid=CALLER_UID, gid=OTHER_GID)
    assert (entry.uid, entry.gid) == (CALLER_UID, CALLER_GID)
    assert entry.mode == 0o2755
    entry = engine.create('/project/run/b.dat', 0o644, uid=CALLER_UID, gid=OTHER_GID)
    assert entry.gid == CALLER_GID


def test_setgid_parent_mode_reaches_the_queue(engine: OverlayEngine) -> None:
    """The setgid bit a new directory inherits is in the mode its queued source `mkdir` carries."""
    engine.mkdir('/project', 0o2775)
    engine.mkdir('/project/run', 0o755)
    rows = [row for row in engine.journal.queued_for('/project/run') if row.op == 'mkdir']
    assert len(rows) == 1
    assert rows[0].detail['mode'] == 0o2755


# -- agreement with the source (unprivileged) ---------------------------------------------------------------------

def test_committed_entries_match_catalog(engine: OverlayEngine, tmp_path: Path) -> None:
    """Files and directories created with the defaults and flushed have the catalog's owner on the source."""
    engine.create('/fresh.dat')
    engine.mkdir('/outer')
    engine.mkdir('/outer/inner')
    engine.create('/outer/inner/deep.dat')
    engine.flush()
    for path in ('/fresh.dat', '/outer', '/outer/inner', '/outer/inner/deep.dat'):
        assert_source_agrees(engine, tmp_path / 'project', path)


def test_file_created_on_source_matches_catalog(single: OverlayEngine, tmp_path: Path) -> None:
    """A file created straight onto a one-tier source has the catalog's owner there at once."""
    single.create('/direct.dat')
    assert_source_agrees(single, tmp_path / 'project', '/direct.dat')


def test_explicit_group_reaches_the_source(tmp_path: Path, clock: Clock) -> None:
    """
    A directory committed from a stack and a file created straight onto a source with an explicit
    group of this process (not its default, not the source root's) carry that group on the source.
    """
    root = tmp_path / 'project'
    seed(root, 'top.txt', b'top-level')
    group = spare_group(root)
    if group is None:
        skip('this process belongs to no group other than its own and the source root\'s')
    seed(tmp_path / 'two' / 'project', 'top.txt', b'top-level')
    stacked = OverlayEngine(build_spec(tmp_path / 'two', tiers=2), clock=clock)
    stacked.start()
    try:
        stacked.mkdir('/grouped', gid=group)
        stacked.flush()
        assert stacked.catalog.get('/grouped').gid == group
        assert_source_agrees(stacked, tmp_path / 'two' / 'project', '/grouped')
    finally:
        stacked.stop()
    direct = OverlayEngine(build_spec(tmp_path, tiers=1), clock=clock)
    direct.start()
    try:
        direct.create('/grouped.dat', gid=group)
        assert direct.catalog.get('/grouped.dat').gid == group
        assert_source_agrees(direct, root, '/grouped.dat')
    finally:
        direct.stop()
