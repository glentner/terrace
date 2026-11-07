# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Model-based tests: random op sequences against a plain-directory oracle."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Optional

# Standard libs
import os
import shutil
import dataclasses
import tempfile
from pathlib import Path

# External libs
from pytest import mark
from hypothesis import settings, HealthCheck
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant

# Internal libs
from sosfs.engine.overlay import OverlayEngine
from sosfs.engine.writeback import WritebackScheduler
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


DIRS = ['/d1', '/d2', '/d1/s1']
FILES = ['/a.dat', '/b.dat', '/d1/a.dat', '/d1/b.dat', '/d2/a.dat', '/d1/s1/a.dat']
PATHS = st.sampled_from(DIRS + FILES)
FILE_PATHS = st.sampled_from(FILES)
DIR_PATHS = st.sampled_from(DIRS)
DATA = st.binary(min_size=0, max_size=256)
OFFSETS = st.integers(min_value=0, max_value=64)
ADVANCE = st.sampled_from([0, 1, 30, 59, 60, 61, 90, 120])


def snapshot(root: Path) -> Dict[str, object]:
    """{relative-posix-path: bytes | 'DIR'} for a tree, blind to '.sosfs'."""
    state: Dict[str, object] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != '.sosfs')
        relative = os.path.relpath(dirpath, root)
        prefix = '' if relative == '.' else relative + '/'
        for name in dirnames:
            state[prefix + name] = 'DIR'
        for name in sorted(filenames):
            state[prefix + name] = (Path(dirpath) / name).read_bytes()
    return state


class OverlayMachine(RuleBasedStateMachine):
    """
    Random VFS op sequences applied to both the engine (3-tier stack) and a
    plain directory; every op must agree on success/failure, reads must agree
    on bytes, and after quiesce + flush the source tree must equal the oracle
    (PLAN.md -> Testing strategy).

    The full op mix runs unrestricted, including structural mutations of entries
    that still carry unflushed deferred source ops (unlink/rmdir/rename over a
    pending rename) — the deferred-op replay (PLAN.md -> Write-back -> Deferred-op
    replay) resolves each queued row against the entry's current place on the
    source, so the source reconstructs the oracle exactly.

    The model also drives what reaches the source while namespace rows are still
    queued, the window `flush` hides by running namespace rows first: one entry
    committed (directly, by `fsync` under the commit policy, and before an
    eviction), one scheduler poll after a clock jump or at the next deadline, the
    pump's scan and a later commit of what it scanned with user ops between them,
    and three composites: a commit while a rename is queued, a commit at a name
    whose old object's move is queued, and a pump snapshot committed after a user
    op cancelled the rename it holds and a newer row took its id. A drain that
    polls deadline by deadline must leave the source equal to the oracle; after
    every step each source object but the never-adopted seed file has exactly one
    catalog holder; and at teardown a fresh engine over the same roots reads the
    oracle's tree.
    """

    def __init__(self) -> None:
        super().__init__()
        self.root = Path(tempfile.mkdtemp(prefix='sosfs-model-'))
        seed(self.root / 'project', 'seeded.txt', b'initial-content')
        self.oracle = self.root / 'oracle'
        self.oracle.mkdir()
        (self.oracle / 'seeded.txt').write_bytes(b'initial-content')
        self.clock = Clock()
        self.spec = dataclasses.replace(build_spec(self.root, tiers=3), fsync_policy='commit')
        self.engine = OverlayEngine(self.spec, clock=self.clock)
        self.engine.start()
        self.scheduler = WritebackScheduler(self.engine)        # never started: poll() runs synchronously
        self.scanned = []

    def real(self, path: str) -> Path:
        """Oracle location for an overlay path."""
        return self.oracle / path.lstrip('/')

    def parity(self, engine_call: Callable, oracle_call: Callable) -> Optional[object]:
        """Run both sides; they must agree on success vs. OSError."""
        engine_error = oracle_error = None
        result = None
        try:
            result = engine_call()
        except OSError as error:
            engine_error = error
        try:
            oracle_call()
        except OSError as error:
            oracle_error = error
        assert (engine_error is None) == (oracle_error is None), \
            f'diverged: engine={engine_error!r} oracle={oracle_error!r}'
        return result if engine_error is None else None

    # -- operations ---------------------------------------------------------------------------------------------

    @rule(path=FILE_PATHS, data=DATA)
    def create_with_content(self, path: str, data: bytes) -> None:
        def engine_side():
            self.engine.create(path)
            handle = self.engine.open(path, os.O_WRONLY)
            try:
                self.engine.write(handle, data, 0)
            finally:
                self.engine.release(handle)

        def oracle_side():
            fd = os.open(self.real(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.pwrite(fd, data, 0)
            finally:
                os.close(fd)

        self.parity(engine_side, oracle_side)

    @rule(path=FILE_PATHS, data=DATA, offset=OFFSETS)
    def overwrite_at_offset(self, path: str, data: bytes, offset: int) -> None:
        def engine_side():
            handle = self.engine.open(path, os.O_RDWR)
            try:
                self.engine.write(handle, data, offset)
            finally:
                self.engine.release(handle)

        def oracle_side():
            fd = os.open(self.real(path), os.O_RDWR)
            try:
                os.pwrite(fd, data, offset)
            finally:
                os.close(fd)

        self.parity(engine_side, oracle_side)

    @rule(path=FILE_PATHS, length=OFFSETS)
    def truncate(self, path: str, length: int) -> None:
        self.parity(lambda: self.engine.truncate(path, length),
                    lambda: os.truncate(self.real(path), length))

    @rule(path=PATHS)
    def read_and_compare(self, path: str) -> None:
        def engine_side():
            handle = self.engine.open(path, os.O_RDONLY)
            try:
                return self.engine.read(handle, 1 << 20, 0)
            finally:
                self.engine.release(handle)

        content = self.parity(engine_side, lambda: self.real(path).read_bytes())
        if content is not None:
            assert content == self.real(path).read_bytes()

    @rule(path=PATHS)
    def stat_and_compare(self, path: str) -> None:
        entry = self.parity(lambda: self.engine.getattr(path),
                            lambda: os.stat(self.real(path)))
        if entry is not None and entry.kind == 'file':
            assert entry.size == os.stat(self.real(path)).st_size

    @rule(path=DIR_PATHS)
    def mkdir(self, path: str) -> None:
        self.parity(lambda: self.engine.mkdir(path), lambda: os.mkdir(self.real(path)))

    @rule(path=DIR_PATHS)
    def rmdir(self, path: str) -> None:
        self.parity(lambda: self.engine.rmdir(path), lambda: os.rmdir(self.real(path)))

    @rule(path=FILE_PATHS)
    def unlink(self, path: str) -> None:
        self.parity(lambda: self.engine.unlink(path), lambda: os.unlink(self.real(path)))

    @rule(old=PATHS, new=PATHS)
    def rename(self, old: str, new: str) -> None:
        self.parity(lambda: self.engine.rename(old, new),
                    lambda: os.rename(self.real(old), self.real(new)))

    @rule(path=DIR_PATHS)
    def listdir_and_compare(self, path: str) -> None:
        entries = self.parity(lambda: self.engine.readdir(path),
                              lambda: os.listdir(self.real(path)))
        if entries is not None:
            names = sorted(entry.path.rsplit('/', 1)[-1] for entry in entries)
            assert names == sorted(os.listdir(self.real(path)))

    @rule(path=FILE_PATHS, data=DATA, offset=OFFSETS)
    def write_and_fsync(self, path: str, data: bytes, offset: int) -> None:
        """A write made durable by `fsync`, which commits the entry to the source under the commit policy."""
        def engine_side():
            handle = self.engine.open(path, os.O_RDWR)
            try:
                self.engine.write(handle, data, offset)
                self.engine.fsync(handle)
            finally:
                self.engine.release(handle)

        def oracle_side():
            fd = os.open(self.real(path), os.O_RDWR)
            try:
                os.pwrite(fd, data, offset)
                os.fsync(fd)
            finally:
                os.close(fd)

        self.parity(engine_side, oracle_side)

    @rule(old=FILE_PATHS, new=FILE_PATHS, data=DATA)
    def rewrite_rename_commit(self, old: str, new: str, data: bytes) -> None:
        """Commit, rewrite, rename, commit again: a data commit while the entry's rename is still queued."""
        def engine_side():
            self.engine.commit_upstream(old)
            handle = self.engine.open(old, os.O_RDWR | os.O_TRUNC)
            try:
                self.engine.write(handle, data, 0)
            finally:
                self.engine.release(handle)
            self.engine.rename(old, new)
            self.engine.commit_upstream(new)

        def oracle_side():
            fd = os.open(self.real(old), os.O_RDWR | os.O_TRUNC)
            try:
                os.pwrite(fd, data, 0)
            finally:
                os.close(fd)
            os.rename(self.real(old), self.real(new))

        self.parity(engine_side, oracle_side)

    @rule(old=FILE_PATHS, new=FILE_PATHS, data=DATA)
    def rename_reuse_commit(self, old: str, new: str, data: bytes) -> None:
        """Commit, rename, then create and commit a file at the old name while the rename is still queued."""
        def engine_side():
            self.engine.commit_upstream(old)
            self.engine.rename(old, new)
            self.engine.create(old)
            handle = self.engine.open(old, os.O_WRONLY)
            try:
                self.engine.write(handle, data, 0)
            finally:
                self.engine.release(handle)
            self.engine.commit_upstream(old)

        def oracle_side():
            os.rename(self.real(old), self.real(new))
            fd = os.open(self.real(old), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.pwrite(fd, data, 0)
            finally:
                os.close(fd)

        self.parity(engine_side, oracle_side)

    @rule(old=FILE_PATHS, new=FILE_PATHS, advance=ADVANCE)
    def rename_scan_unlink_commit(self, old: str, new: str, advance: int) -> None:
        """
        Commit, rename, let the pump scan, unlink the new name, then commit the pump's snapshot: the
        unlink cancels the scanned rename row, and its own queued removal can take that row's id.
        """
        def engine_side():
            self.engine.commit_upstream(old)
            self.engine.rename(old, new)
            self.clock.now += advance
            rows = self.scheduler.due(self.clock.now)
            self.engine.unlink(new)
            for row in rows:
                self.engine.commit_row(row)

        def oracle_side():
            os.rename(self.real(old), self.real(new))
            os.unlink(self.real(new))

        self.parity(engine_side, oracle_side)

    @rule(path=FILE_PATHS)
    def commit_entry(self, path: str) -> None:
        """One entry's data to the source; absent, clean and directory paths are a quiet no-op."""
        self.engine.commit_upstream(path)

    @rule(advance=ADVANCE)
    def poll_once(self, advance: int) -> None:
        """One scheduler poll after an arbitrary clock jump."""
        self.clock.now += advance
        self.scheduler.poll()

    @rule()
    def poll_next_due(self) -> None:
        """
        One poll at the earliest deadline, so exactly the rows due first commit. Data overtakes a
        rename only inside a window as narrow as one tick, which a uniform jump rarely lands in.
        """
        rows = self.engine.journal.queued()
        if rows:
            self.clock.now = max(self.clock.now, min(self.scheduler.deadline(row) for row in rows))
        self.scheduler.poll(self.clock.now)

    @rule(advance=ADVANCE)
    def scan_writeback(self, advance: int) -> None:
        """The pump's scan: a snapshot of the due rows, committed by a later rule after other ops."""
        self.clock.now += advance
        self.scanned = self.scheduler.due(self.clock.now)

    @rule()
    def commit_scanned(self) -> None:
        """The pump's commit of its last snapshot, whose rows a user op may since have run or cancelled."""
        rows, self.scanned = self.scanned, []
        for row in rows:
            self.engine.commit_row(row)

    @rule(path=FILE_PATHS)
    def evict(self, path: str) -> None:
        """What the eviction manager does to each staged copy: commit it if dirty, then drop it."""
        for tier in ('tmp', 'scratch'):
            residency = self.engine.catalog.get_residency(path, tier)
            if residency is not None and residency.state == 'dirty':
                self.engine.commit_upstream(path)
            self.engine.evict_copy(path, tier)

    @rule()
    def drain_by_polls(self) -> None:
        """
        Poll deadline by deadline until the queue is empty, then compare the source with the oracle.
        `flush` runs every namespace row first; this drains in the order the scheduler would.
        """
        for _ in range(200):
            rows = self.engine.journal.queued()
            if not rows:
                break
            self.clock.now = max(self.clock.now, min(self.scheduler.deadline(row) for row in rows))
            self.scheduler.poll(self.clock.now)
        assert self.engine.journal.queued() == []
        assert self.engine.journal.pending() == []
        assert snapshot(Path(self.engine.spec.tiers[0].path)) == snapshot(self.oracle)

    @invariant()
    def source_objects_have_one_holder(self) -> None:
        """
        Every source object is some entry's committed source path, and no two entries share one. An
        object nothing holds is an orphan a remount would adopt. `/seeded.txt` is exempt by name: it is
        never adopted, because no rule names or lists it.
        """
        root = Path(self.engine.spec.tiers[0].path)
        held = {row[0] for row in self.engine.db.execute(
            t'SELECT source_path FROM entry WHERE source_path IS NOT NULL')}
        unheld = sorted(physical for physical in ('/' + relative for relative in snapshot(root))
                        if physical not in held and physical != '/seeded.txt')
        assert not unheld, f'unheld source objects {unheld}'
        shared = [row[0] for row in self.engine.db.execute(
            t'SELECT source_path FROM entry WHERE source_path IS NOT NULL '
            t'GROUP BY source_path HAVING count(*) > 1')]
        assert not shared, f'shared source paths {shared}'

    @rule()
    def quiesce_and_flush(self) -> None:
        self.engine.flush()
        assert snapshot(Path(self.engine.spec.tiers[0].path)) == snapshot(self.oracle)
        assert self.engine.journal.queued() == []
        assert self.engine.journal.pending() == []

    def overlay_view(self, engine: OverlayEngine, path: str = '/') -> Dict[str, object]:
        """The snapshot shape read through an engine: readdir from `path`, each file through open and read."""
        state: Dict[str, object] = {}
        for entry in engine.readdir(path):
            relative = entry.path.lstrip('/')
            if entry.kind == 'dir':
                state[relative] = 'DIR'
                state.update(self.overlay_view(engine, entry.path))
            else:
                handle = engine.open(entry.path, os.O_RDONLY)
                try:
                    state[relative] = engine.read(handle, 1 << 20, 0)
                finally:
                    engine.release(handle)
        return state

    def teardown(self) -> None:
        try:
            if self.engine.started:
                self.engine.flush()
                source = snapshot(Path(self.engine.spec.tiers[0].path))
                assert source == snapshot(self.oracle)
                self.engine.stop()
                remount = OverlayEngine(self.spec, clock=self.clock)
                remount.start()
                try:
                    assert self.overlay_view(remount) == snapshot(self.oracle)
                finally:
                    remount.stop()
        finally:
            shutil.rmtree(self.root, ignore_errors=True)


TestOverlayModel = OverlayMachine.TestCase
TestOverlayModel.settings = settings(
    max_examples=1000, stateful_step_count=50, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much])
