# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Integration tests: POSIX tooling, xattr residency, metadata propagation, and lifecycle over a real mount."""

# Type annotations
from __future__ import annotations
from typing import Optional, Sequence, Tuple

# Standard libs
import os
import stat
import time
import platform
import threading
from pathlib import Path

# External libs
from pytest import mark, skip

# Internal libs
from terrace.state.db import Database
from terrace.state.lifecycle import Lifecycle
from terrace.tier.spec import OverlaySpec, WritebackSpec
from terrace.engine.overlay import DB_NAME
from terrace.fuse.mount import Mount, Shutdown, unmount, request_shutdown, MountError
from tests.engine.conftest import build_spec, seed


def _linux_fuse() -> bool:
    """Whether this host can run a real FUSE mount (Linux with libfuse and /dev/fuse)."""
    if platform.system() != 'Linux' or not os.path.exists('/dev/fuse'):
        return False
    try:
        import mfusepy                                     # noqa: F401 — availability probe only
    except Exception:
        return False
    return True


pytestmark = [mark.integration,
              mark.skipif(not _linux_fuse(), reason='requires Linux with libfuse and /dev/fuse')]

FROZEN = WritebackSpec(delay=99_999.0, max_interval=99_999.0)   # keep the pumps quiet; drain/detach do the moves
MOUNT_TIMEOUT = 15.0
JOIN_TIMEOUT = 30.0


def settled_state(state_dir: Path) -> Optional[str]:
    """Read the overlay's settled `shutdown_state` after the engine has closed."""
    db = Database(Path(state_dir) / DB_NAME)
    try:
        overlay = Lifecycle(db).load()
        return None if overlay is None else overlay.shutdown_state
    finally:
        db.close()


def start_mount(tmp_path: Path, *, tiers: int = 2,
                seeds: Sequence[Tuple[str, bytes]] = ()) -> Tuple[Mount, OverlaySpec, Path, threading.Thread]:
    """Bring up a real FUSE mount on a worker thread; returns once the mount is live."""
    (tmp_path / 'project').mkdir(exist_ok=True)
    for relpath, data in seeds:
        seed(tmp_path / 'project', relpath, data)
    point = tmp_path / 'mnt'
    point.mkdir()
    overrides = {'scratch_writeback': FROZEN, 'tmp_writeback': FROZEN}
    spec = build_spec(tmp_path, tiers=tiers, **overrides)
    mount = Mount(spec, point, foreground=True, writeback_tick=0.2, evict_tick=0.5)
    server = threading.Thread(target=mount.serve, name='terrace-serve', daemon=True)
    server.start()
    deadline = time.time() + MOUNT_TIMEOUT
    while time.time() < deadline and not os.path.ismount(point):
        if mount._loop_error is not None:
            server.join(JOIN_TIMEOUT)
            skip(f'FUSE mount unavailable: {mount._loop_error}')
        time.sleep(0.1)
    if not os.path.ismount(point):
        stop_mount(mount, server, point, mode=Shutdown.FORCE)
        skip('FUSE mount did not come up within the timeout')
    return mount, spec, point, server


def stop_mount(mount: Mount, server: threading.Thread, point: Path, mode: Shutdown = Shutdown.DRAIN) -> None:
    """Tear a live mount down under `mode`, then make sure the mount point is clear."""
    if server.is_alive():
        mount._mode = mode
        mount._stop.set()
        server.join(JOIN_TIMEOUT)
    if os.path.ismount(point):
        try:
            unmount(point)
        except MountError:
            pass


def test_posix_tooling_roundtrip(tmp_path: Path) -> None:
    """Standard file operations work through the mount: read seeded, write new, mkdir, list, stat."""
    mount, spec, point, server = start_mount(tmp_path, seeds=[('existing.txt', b'seeded')])
    try:
        assert (point / 'existing.txt').read_bytes() == b'seeded'
        (point / 'new.txt').write_bytes(b'hello world')
        assert (point / 'new.txt').read_bytes() == b'hello world'
        (point / 'sub').mkdir()
        (point / 'sub' / 'a.bin').write_bytes(b'\x00\x01\x02')
        listing = sorted(child.name for child in point.iterdir())
        assert listing == ['existing.txt', 'new.txt', 'sub']
        assert (point / 'new.txt').stat().st_size == 11
        assert (point / 'sub' / 'a.bin').read_bytes() == b'\x00\x01\x02'
    finally:
        stop_mount(mount, server, point)


def test_xattr_exposes_tier_residency(tmp_path: Path) -> None:
    """The read-only tiering xattrs report where a file lives and whether it is dirty."""
    mount, spec, point, server = start_mount(tmp_path)
    try:
        target = point / 'data.txt'
        target.write_bytes(b'payload')
        assert 'user.sosfs.tiers' in os.listxattr(str(target))
        assert b'scratch' in os.getxattr(str(target), 'user.sosfs.tiers')   # resident on the fast tier
        assert os.getxattr(str(target), 'user.sosfs.dirty') == b'1'         # written, not yet committed
    finally:
        stop_mount(mount, server, point)


def test_metadata_propagates_to_source_after_flush(tmp_path: Path) -> None:
    """chmod and touch on the mount reach the source once the overlay drains."""
    mount, spec, point, server = start_mount(tmp_path, seeds=[('cfg.txt', b'data')])
    target = point / 'cfg.txt'
    os.chmod(str(target), 0o640)
    os.utime(str(target), (1_000_000, 2_000_000))
    stop_mount(mount, server, point, mode=Shutdown.DRAIN)       # drain flushes the meta op to the source
    source = tmp_path / 'project' / 'cfg.txt'
    info = source.stat()
    assert stat.S_IMODE(info.st_mode) == 0o640
    assert int(info.st_mtime) == 2_000_000


@mark.skipif(os.getuid() != 0, reason='chown to another owner requires privilege')
def test_chown_propagates_to_source_after_flush(tmp_path: Path) -> None:
    """chown on the mount reaches the source once the overlay drains (privileged)."""
    mount, spec, point, server = start_mount(tmp_path, seeds=[('own.txt', b'data')])
    target = point / 'own.txt'
    os.chown(str(target), 1, 1)
    stop_mount(mount, server, point, mode=Shutdown.DRAIN)
    info = (tmp_path / 'project' / 'own.txt').stat()
    assert (info.st_uid, info.st_gid) == (1, 1)


def test_drain_umount_finalizes_source(tmp_path: Path) -> None:
    """A default umount drains dirty data to the source and settles clean."""
    mount, spec, point, server = start_mount(tmp_path)
    (point / 'result.dat').write_bytes(b'computed')
    stop_mount(mount, server, point, mode=Shutdown.DRAIN)
    assert settled_state(spec.state) == 'clean'
    assert (tmp_path / 'project' / 'result.dat').read_bytes() == b'computed'


def test_detach_rescues_node_local_dirty(tmp_path: Path) -> None:
    """A detach hops node-local dirty data to the nearest shared tier and settles detached."""
    mount, spec, point, server = start_mount(tmp_path, tiers=3)
    (point / 'hot.bin').write_bytes(b'volatile')           # lands on tmp (node scope), the fastest tier
    stop_mount(mount, server, point, mode=Shutdown.DETACH)
    assert settled_state(spec.state) == 'detached'
    assert (tmp_path / 'scratch' / 'hot.bin').exists()     # rescued one hop to the shared durable tier
    assert not (tmp_path / 'project' / 'hot.bin').exists()  # not finalized to the source


def test_force_umount_skips_rescue(tmp_path: Path) -> None:
    """A force umount unmounts directly and settles detached without draining or rescuing."""
    mount, spec, point, server = start_mount(tmp_path)
    (point / 'x.dat').write_bytes(b'data')
    request_shutdown(spec, force=True, wait=False)          # plain unmount breaks the loop into a force teardown
    server.join(JOIN_TIMEOUT)
    try:
        assert not server.is_alive()
        assert settled_state(spec.state) == 'detached'
        assert not (tmp_path / 'project' / 'x.dat').exists()   # force skipped the drain
    finally:
        stop_mount(mount, server, point, mode=Shutdown.FORCE)
