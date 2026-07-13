# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Model-based tests: random op sequences against a plain-directory oracle."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Optional

# Standard libs
import os
import shutil
import tempfile
from pathlib import Path

# External libs
from pytest import mark
from hypothesis import settings, HealthCheck
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule

# Internal libs
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


DIRS = ['/d1', '/d2', '/d1/s1']
FILES = ['/a.dat', '/b.dat', '/d1/a.dat', '/d1/b.dat', '/d2/a.dat', '/d1/s1/a.dat']
PATHS = st.sampled_from(DIRS + FILES)
FILE_PATHS = st.sampled_from(FILES)
DIR_PATHS = st.sampled_from(DIRS)
DATA = st.binary(min_size=0, max_size=256)
OFFSETS = st.integers(min_value=0, max_value=64)


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
    pending rename) — the committed-source-path replay (PLAN.md -> Write-back ->
    Deferred-op replay) resolves those against where the source physically holds
    the data, so the source reconstructs the oracle exactly.
    """

    def __init__(self) -> None:
        super().__init__()
        self.root = Path(tempfile.mkdtemp(prefix='sosfs-model-'))
        seed(self.root / 'project', 'seeded.txt', b'initial-content')
        self.oracle = self.root / 'oracle'
        self.oracle.mkdir()
        (self.oracle / 'seeded.txt').write_bytes(b'initial-content')
        self.engine = OverlayEngine(build_spec(self.root, tiers=3), clock=Clock())
        self.engine.start()

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

    @rule()
    def quiesce_and_flush(self) -> None:
        self.engine.flush()
        assert snapshot(Path(self.engine.spec.tiers[0].path)) == snapshot(self.oracle)
        assert self.engine.journal.queued() == []
        assert self.engine.journal.pending() == []

    def teardown(self) -> None:
        try:
            if self.engine.started:
                self.engine.flush()
                source = snapshot(Path(self.engine.spec.tiers[0].path))
                assert source == snapshot(self.oracle)
                self.engine.stop()
        finally:
            shutil.rmtree(self.root, ignore_errors=True)


TestOverlayModel = OverlayMachine.TestCase
TestOverlayModel.settings = settings(
    max_examples=100, stateful_step_count=50, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much])
