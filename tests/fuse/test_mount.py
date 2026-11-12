# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the mount lifecycle: pidfile, exclusive lock, and the drain/detach/force shutdowns."""

# Type annotations
from __future__ import annotations
from typing import Optional, Tuple

# Standard libs
import os
import json
import signal
import socket
import threading
from pathlib import Path

# External libs
from pytest import mark, raises, fixture

# Internal libs
from terrace.state.db import Database, DatabaseLocked
from terrace.state.lifecycle import Lifecycle
from terrace.tier.spec import WritebackSpec
from terrace.engine.overlay import DB_NAME
from terrace.fuse.mount import Mount, Shutdown, MountError, read_pidfile, request_shutdown, PIDFILE_NAME
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


def test_sigint_selects_drain_and_counts_repeats(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """SIGINT requests a drain; repeats bump the counter that a mid-drain cancel reads, mode unchanged."""
    mount, _ = make_mount(tmp_path, mountpoint, clock)
    mount._on_sigint()
    assert mount._mode is Shutdown.DRAIN and mount._sigint == 1 and mount._stop.is_set()
    mount._on_sigint()
    assert mount._mode is Shutdown.DRAIN and mount._sigint == 2


def test_sigterm_selects_detach(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """SIGTERM requests a detach; a later SIGINT does not steal the mode but is still counted."""
    mount, _ = make_mount(tmp_path, mountpoint, clock)
    mount._on_sigterm()
    assert mount._mode is Shutdown.DETACH and mount._stop.is_set()
    mount._on_sigint()
    assert mount._mode is Shutdown.DETACH and mount._sigint == 1


def test_second_sigint_mid_drain_downgrades_to_detach(tmp_path: Path, mountpoint: Path, clock: Clock,
                                                      monkeypatch) -> None:
    """A second SIGINT during the drain cancels it after the most-volatile file, settling `detached`."""
    mount, _ = make_mount(tmp_path, mountpoint, clock, tiers=3)
    mount.start()
    dirty_new(mount.engine, '/a.dat', b'aaaa')
    dirty_new(mount.engine, '/b.dat', b'bbbb')             # two node-local dirty files -> two drain commits
    mount._on_sigint()                                     # first SIGINT: mode=DRAIN

    real = mount.engine.commit_upstream
    committed = []

    def wrapped(path: str) -> None:
        committed.append(path)
        real(path)
        mount._on_sigint()                                 # a second SIGINT arrives during the first commit

    monkeypatch.setattr(mount.engine, 'commit_upstream', wrapped)
    mount.shutdown(mount._mode)
    assert mount._sigint == 2
    assert len(committed) == 1                             # cancel fired before the second file
    assert settled_state(mount.spec.state) == 'detached'
    at_source = [name for name in ('a.dat', 'b.dat') if (tmp_path / 'project' / name).exists()]
    assert len(at_source) == 1                             # only the volatile-first file reached the source


def test_serve_surfaces_a_loop_failure(tmp_path: Path, mountpoint: Path, clock: Clock) -> None:
    """A FUSE session that dies is reported as a `MountError` and force-stops the overlay."""
    def boom() -> None:
        raise RuntimeError('libfuse said no')

    mount, _ = make_mount(tmp_path, mountpoint, clock, tiers=2, runner=boom, breaker=lambda: None)
    with raises(MountError):
        mount.serve()
    assert not mount.pidfile.exists() and not mount.lock.held
    assert settled_state(mount.spec.state) == 'detached'


# -- umount command (request_shutdown) ------------------------------------------------------------------------


def put_pidfile(state: Path, *, pid: int = 424242, host: object = None, mountpoint: str = '/mnt/x') -> None:
    """Drop a pidfile as a running daemon would, for the umount-resolution tests."""
    Path(state).mkdir(parents=True, exist_ok=True)
    record = {'pid': pid, 'host': socket.gethostname() if host is None else host,
              'mountpoint': mountpoint, 'started': 1.0}
    (Path(state) / PIDFILE_NAME).write_text(json.dumps(record) + '\n')


@fixture
def spec(tmp_path: Path):
    """A parsed spec (no engine started) whose state directory hosts the pidfile."""
    seed(tmp_path / 'project', 'seed.txt', b'x')
    return build_spec(tmp_path, tiers=2)


def test_request_shutdown_drain_sends_sigint(spec, monkeypatch) -> None:
    """A plain umount signals the daemon to drain (SIGINT)."""
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    put_pidfile(spec.state, pid=999)
    request_shutdown(spec, wait=False)
    assert sent == [(999, signal.SIGINT)]


def test_request_shutdown_detach_sends_sigterm(spec, monkeypatch) -> None:
    """`--detach` signals the daemon to detach (SIGTERM)."""
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    put_pidfile(spec.state, pid=1001)
    request_shutdown(spec, detach=True, wait=False)
    assert sent == [(1001, signal.SIGTERM)]


def test_request_shutdown_force_unmounts_directly(spec, monkeypatch) -> None:
    """`--force` unmounts the recorded mount point rather than signalling the daemon."""
    unmounted, killed = [], []
    monkeypatch.setattr('terrace.fuse.mount.unmount', lambda mp: unmounted.append(str(mp)))
    monkeypatch.setattr(os, 'kill', lambda pid, sig: killed.append(pid))
    put_pidfile(spec.state, mountpoint='/mnt/space')
    request_shutdown(spec, force=True, wait=False)
    assert unmounted == ['/mnt/space'] and killed == []


def test_request_shutdown_missing_pidfile_errors(spec) -> None:
    """Umount of an overlay with no running mount is a clear error, not a crash."""
    with raises(MountError, match='No running mount'):
        request_shutdown(spec, wait=False)


def test_request_shutdown_stale_pid_errors(spec, monkeypatch) -> None:
    """A pidfile whose process is gone is reported as stale rather than signalling a reused pid."""
    def dead(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, 'kill', dead)
    put_pidfile(spec.state, pid=777)
    with raises(MountError, match='stale pidfile'):
        request_shutdown(spec, wait=False)


def test_request_shutdown_wrong_host_errors(spec, monkeypatch) -> None:
    """A mount recorded on another host is not signalled from here."""
    monkeypatch.setattr(os, 'kill', lambda pid, sig: (_ for _ in ()).throw(AssertionError('must not signal')))
    put_pidfile(spec.state, host='some-other-host')
    with raises(MountError, match='mounted on'):
        request_shutdown(spec, wait=False)


def test_request_shutdown_detach_and_force_conflict(spec) -> None:
    """`--detach` and `--force` are mutually exclusive."""
    with raises(MountError, match='at most one'):
        request_shutdown(spec, detach=True, force=True, wait=False)
