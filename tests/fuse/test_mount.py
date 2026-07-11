# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the mount lifecycle: pidfile, exclusive lock, and the drain/detach/force shutdowns."""

# Type annotations
from __future__ import annotations
from typing import Optional, Tuple

# Standard libs
import os
import threading
from pathlib import Path

# External libs
from pytest import mark, raises, fixture

# Internal libs
from sosfs.state.db import Database, DatabaseLocked
from sosfs.state.lifecycle import Lifecycle
from sosfs.tier.spec import WritebackSpec
from sosfs.engine.overlay import DB_NAME
from sosfs.fuse.mount import Mount, Shutdown, MountError, read_pidfile
from tests.engine.conftest import Clock, build_spec, seed
from tests.engine.test_drain import dirty_new


pytestmark = mark.unit


class BlockingRunner:
    """A fake FUSE loop: signals it started, then blocks until the breaker releases it."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.released = threading.Event()

    def __call__(self) -> None:
        self.started.set()
        self.released.wait(timeout=5.0)

    def release(self) -> None:
        self.released.set()


@fixture
def mountpoint(tmp_path: Path) -> Path:
    """An existing directory to bind the overlay over (never a real FUSE mount in unit tests)."""
    point = tmp_path / 'mnt'
    point.mkdir()
    return point


def make_mount(tmp_path: Path, mountpoint: Path, clock: Clock, *, tiers: int = 3,
               runner=None, breaker: object = 'auto') -> Tuple[Mount, BlockingRunner]:
    """A mount over a seeded source with policy clocks frozen so the pumps never commit under test."""
    seed(tmp_path / 'project', 'seed.txt', b'x')
    frozen = WritebackSpec(delay=99_999.0, max_interval=99_999.0)
    spec = build_spec(tmp_path, tiers=tiers, scratch_writeback=frozen, tmp_writeback=frozen)
    if runner is None:
        runner = BlockingRunner()
    if breaker == 'auto':
        breaker = getattr(runner, 'release', None)
    mount = Mount(spec, mountpoint, clock=clock, runner=runner, breaker=breaker,
                  writeback_tick=0.05, evict_tick=0.05)
    return mount, runner


def settled_state(state_dir: Path) -> Optional[str]:
    """Read the overlay's `shutdown_state` directly, without re-mounting (which would begin_mount)."""
    db = Database(Path(state_dir) / DB_NAME)
    try:
        overlay = Lifecycle(db).load()
        return None if overlay is None else overlay.shutdown_state
    finally:
        db.close()


def test_start_establishes_pidfile_lock_and_mounted_state(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """`start` records the daemon, takes the lock, sets the crash bit, and runs the pumps."""
    mount, _ = make_mount(tmp_path, mountpoint, clock)
    mount.start()
    try:
        record = read_pidfile(mount.spec.state)
        assert record is not None
        assert record['pid'] == os.getpid()
        assert record['mountpoint'] == str(mountpoint)
        assert mount.lock.held
        assert mount.engine.started
        assert mount.engine.lifecycle.load().shutdown_state == 'mounted'
        assert mount.writeback.is_alive() and mount.eviction.is_alive()
    finally:
        mount.shutdown(Shutdown.FORCE)
    assert not mount.pidfile.exists()
    assert not mount.lock.held
    assert not mount.engine.started


def test_second_mount_is_locked_out(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """The exclusive lock keeps a second mount of the same overlay from starting on this host."""
    first, _ = make_mount(tmp_path, mountpoint, clock)
    first.start()
    try:
        second, _ = make_mount(tmp_path, mountpoint, Clock())
        with raises(DatabaseLocked):
            second.start()
        assert not second.engine.started
    finally:
        first.shutdown(Shutdown.FORCE)


def test_shutdown_drain_settles_clean_and_finalizes_source(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """A drain commits every dirty file to the source and settles `clean`."""
    mount, _ = make_mount(tmp_path, mountpoint, clock, tiers=2)
    mount.start()
    dirty_new(mount.engine, '/out.dat', b'payload')
    mount.shutdown(Shutdown.DRAIN)
    assert settled_state(mount.spec.state) == 'clean'
    assert (tmp_path / 'project' / 'out.dat').read_bytes() == b'payload'
    assert not mount.pidfile.exists() and not mount.lock.held


def test_shutdown_detach_rescues_node_local_and_settles_detached(tmp_path: Path, mountpoint: Path,
                                                                 clock: Clock) -> None:
    """A detach hops node-local dirty data to the nearest shared tier and settles `detached`."""
    mount, _ = make_mount(tmp_path, mountpoint, clock, tiers=3)
    mount.start()
    dirty_new(mount.engine, '/hot.dat', b'volatile')       # lands on tmp (node scope), the fastest tier
    mount.shutdown(Shutdown.DETACH)
    assert settled_state(mount.spec.state) == 'detached'
    assert (tmp_path / 'scratch' / 'hot.dat').exists()     # rescued one hop to the shared durable tier
    assert not (tmp_path / 'project' / 'hot.dat').exists()  # but not finalized to the source


def test_shutdown_force_skips_rescue_and_settles_detached(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """A force teardown skips even the rescue: node-local dirty data stays put, still `detached`."""
    mount, _ = make_mount(tmp_path, mountpoint, clock, tiers=3)
    mount.start()
    dirty_new(mount.engine, '/hot.dat', b'volatile')
    mount.shutdown(Shutdown.FORCE)
    assert settled_state(mount.spec.state) == 'detached'
    assert not (tmp_path / 'scratch' / 'hot.dat').exists()  # no rescue hop happened
    assert (tmp_path / 'tmp' / 'hot.dat').exists()          # dirty copy left on the node-local tier


def test_serve_runs_the_loop_and_tears_down_on_request(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """`serve` brings the overlay up, runs the loop, and shuts down cleanly when asked to stop."""
    mount, runner = make_mount(tmp_path, mountpoint, clock, tiers=2)
    server = threading.Thread(target=mount.serve, name='serve', daemon=True)
    server.start()
    assert runner.started.wait(2.0)                        # loop is up => start() has already run
    assert mount.pidfile.exists()
    mount._mode = Shutdown.DRAIN
    mount._stop.set()                                      # stand in for a shutdown signal
    server.join(5.0)
    assert not server.is_alive()
    assert not mount.pidfile.exists()
    assert settled_state(mount.spec.state) == 'clean'


def test_serve_surfaces_a_loop_failure(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """A FUSE session that dies is reported as a `MountError` and force-stops the overlay."""
    def boom() -> None:
        raise RuntimeError('libfuse said no')

    mount, _ = make_mount(tmp_path, mountpoint, clock, tiers=2, runner=boom, breaker=lambda: None)
    with raises(MountError):
        mount.serve()
    assert not mount.pidfile.exists() and not mount.lock.held
    assert settled_state(mount.spec.state) == 'detached'
