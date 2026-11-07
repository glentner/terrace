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
* a rename row's tombstone purge leaves another entry's tombstone and queued removal alone;
* a directory whose target lies inside its own source object moves only once the entries placed
  there have moved out.

The newer tests list the whole tree before acting and end in `assert_converged`: the queue drained,
the source tree equal to the overlay view, every reported size equal to its file's byte count, and a
fresh engine reading the same view.
"""

# Type annotations
from __future__ import annotations

# Standard libs
import os
import dataclasses
from pathlib import Path

# External libs
from pytest import mark

# Internal libs
from sosfs.tier.spec import QuotaSpec
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
