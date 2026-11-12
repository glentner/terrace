# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the adapter's POSIX surface: owner and group of created entries, `link`, permissions (no mount)."""

# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Tuple

# Standard libs
import stat
import errno
from pathlib import Path

# External libs
from pytest import MonkeyPatch, fixture, mark, raises

# Internal libs
from tests.engine.conftest import Clock, build_spec

# Without libfuse these names stay unbound and tests/fuse/conftest.py skips every test here by name.
try:
    import mfusepy
    import terrace.fuse.adapter
    from terrace.fuse.adapter import OverlayOperations
    from terrace.fuse.mount import Mount
except OSError:
    pass


pytestmark = mark.unit

CALLER_UID = 4242
CALLER_GID = 4343
OTHER_GID = 5555


class Caller:
    """A stand-in for `fuse_get_context`: the identity of the process making the current request."""

    def __init__(self, uid: int, gid: int, pid: int = 1) -> None:
        self.uid, self.gid, self.pid = uid, gid, pid

    def __call__(self) -> Tuple[int, int, int]:
        return self.uid, self.gid, self.pid


@fixture
def caller(monkeypatch: MonkeyPatch) -> Caller:
    """Every request the adapter serves comes from `CALLER_UID`:`CALLER_GID` until a test changes it."""
    context = Caller(CALLER_UID, CALLER_GID)
    monkeypatch.setattr(terrace.fuse.adapter, 'fuse_get_context', context, raising=False)
    return context


def create_file(operations: OverlayOperations, path: str, mode: int = 0o644) -> Dict[str, Any]:
    """Create a file through the adapter, release its handle, and return its stat dict."""
    operations.release(path, operations.create(path, mode))
    return operations.getattr(path)


# -- owner and group ----------------------------------------------------------------------------------------------

def test_created_file_belongs_to_caller(operations: OverlayOperations, caller: Caller) -> None:
    """A file created under a plain parent records the caller's uid and gid."""
    attrs = create_file(operations, '/mine.txt')
    assert (attrs['st_uid'], attrs['st_gid']) == (CALLER_UID, CALLER_GID)


def test_created_directory_belongs_to_caller(operations: OverlayOperations, caller: Caller) -> None:
    """A directory created under a plain parent records the caller's uid and gid, and no setgid bit."""
    operations.mkdir('/mine', 0o755)
    attrs = operations.getattr('/mine')
    assert (attrs['st_uid'], attrs['st_gid']) == (CALLER_UID, CALLER_GID)
    assert not attrs['st_mode'] & stat.S_ISGID


def test_setgid_parent_gives_its_group(operations: OverlayOperations, caller: Caller) -> None:
    """
    Under a setgid parent a new file and directory take the parent's group, not the caller's; the
    directory carries the setgid bit and the file does not, so the group holds one level further down.
    """
    operations.mkdir('/project', 0o775)
    operations.chmod('/project', 0o2775)
    caller.gid = OTHER_GID
    attrs = create_file(operations, '/project/data.txt')
    assert (attrs['st_uid'], attrs['st_gid']) == (CALLER_UID, CALLER_GID)
    assert not attrs['st_mode'] & stat.S_ISGID
    operations.mkdir('/project/run', 0o755)
    attrs = operations.getattr('/project/run')
    assert (attrs['st_uid'], attrs['st_gid']) == (CALLER_UID, CALLER_GID)
    assert attrs['st_mode'] & stat.S_ISGID
    assert stat.S_IMODE(attrs['st_mode']) == 0o2755
    attrs = create_file(operations, '/project/run/out.txt')
    assert attrs['st_gid'] == CALLER_GID


# -- unsupported surfaces -----------------------------------------------------------------------------------------

def test_link_fails_with_eperm(operations: OverlayOperations, caller: Caller) -> None:
    """`link` is registered with libfuse and refuses every call with the same errno."""
    assert not getattr(OverlayOperations.link, 'libfuse_ignore', False)
    for target, source in (('/top-link.txt', '/top.txt'), ('/data/a-link.txt', '/data/a.txt')):
        with raises(OSError) as info:
            operations.link(target, source)
        assert info.value.errno == errno.EPERM
        with raises(FileNotFoundError):
            operations.getattr(target)


def test_permissions_are_not_enforced_by_the_adapter() -> None:
    """The adapter keeps the binding's permissive `access`: permission bits are recorded, not enforced."""
    assert OverlayOperations.access is mfusepy.Operations.access


def test_mount_passes_no_default_permissions(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """The real runner hands `FUSE` no `default_permissions`, so the kernel does not enforce the bits."""
    calls: List[Tuple[tuple, Dict[str, Any]]] = []
    monkeypatch.setattr(mfusepy, 'FUSE', lambda *args, **kwargs: calls.append((args, kwargs)))
    point = tmp_path / 'mnt'
    point.mkdir()
    Mount(build_spec(tmp_path, tiers=2), point, clock=Clock())._default_runner()
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert isinstance(args[0], OverlayOperations)
    assert 'default_permissions' not in kwargs
