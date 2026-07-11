# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the FUSE adapter: callback-to-engine mapping, statfs, and xattrs (no mount)."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
import stat
import errno
from pathlib import Path

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.engine.overlay import OverlayEngine
from sosfs.fuse.adapter import OverlayOperations, XATTR_TIERS, XATTR_DIRTY, XATTRS, BLOCK_SIZE
from sosfs.tier.spec import OverlaySpec, TierSpec, QuotaSpec, WritebackSpec
from mfusepy import ENOATTR
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


# -- metadata ---------------------------------------------------------------------------------------------------

def test_getattr_root_is_directory(operations: OverlayOperations) -> None:
    """The root maps to a directory stat with the type bits set and link count 2."""
    attrs = operations.getattr('/')
    assert stat.S_ISDIR(attrs['st_mode'])
    assert attrs['st_nlink'] == 2


def test_getattr_file_shapes_stat(operations: OverlayOperations) -> None:
    """A file's stored permission bits join the regular-file type bit; size/owner carry through."""
    attrs = operations.getattr('/data/a.txt')
    assert stat.S_ISREG(attrs['st_mode'])
    assert stat.S_IMODE(attrs['st_mode']) == 0o640
    assert attrs['st_size'] == len(b'alpha')
    assert attrs['st_nlink'] == 1
    assert {'st_uid', 'st_gid', 'st_atime', 'st_mtime', 'st_ctime'} <= set(attrs)


def test_getattr_missing_is_enoent(operations: OverlayOperations) -> None:
    """A missing path raises ENOENT, which mfusepy turns into -errno."""
    with raises(OSError) as info:
        operations.getattr('/nope')
    assert info.value.errno == errno.ENOENT


def test_readdir_lists_children_with_dots(operations: OverlayOperations) -> None:
    """readdir prepends '.' and '..' and lists child basenames."""
    assert set(operations.readdir('/', 0)) == {'.', '..', 'data', 'top.txt'}
    assert set(operations.readdir('/data', 0)) == {'.', '..', 'a.txt', 'b.txt'}


def test_readlink_returns_target(operations: OverlayOperations) -> None:
    """A symlink seeded on the source is adopted lazily and read back through the adapter."""
    source = Path(operations.engine.spec.source.path)
    os.symlink('data/a.txt', source / 'link')
    assert operations.readlink('/link') == 'data/a.txt'
    attrs = operations.getattr('/link')
    assert stat.S_ISLNK(attrs['st_mode'])
    assert attrs['st_size'] == len('data/a.txt')


# -- data path --------------------------------------------------------------------------------------------------

def test_create_write_read_roundtrip(operations: OverlayOperations) -> None:
    """create returns a handle; write then read through it round-trips, and getattr sees the size live."""
    fh = operations.create('/new.txt', 0o644, os.O_RDWR | os.O_CREAT)
    assert operations.write('/new.txt', b'hello', 0, fh) == 5
    assert operations.read('/new.txt', 5, 0, fh) == b'hello'
    assert operations.getattr('/new.txt')['st_size'] == 5
    assert operations.release('/new.txt', fh) == 0
    assert operations.getattr('/new.txt')['st_size'] == 5


def test_open_read_existing(operations: OverlayOperations) -> None:
    """Opening an existing file yields a handle serving its bytes."""
    fh = operations.open('/top.txt', os.O_RDONLY)
    try:
        assert operations.read('/top.txt', 9, 0, fh) == b'top-level'
    finally:
        operations.release('/top.txt', fh)


def test_truncate_by_path(operations: OverlayOperations) -> None:
    """A path-form truncate (no open handle) shortens the file."""
    assert operations.truncate('/top.txt', 3) == 0
    assert operations.getattr('/top.txt')['st_size'] == 3


def test_truncate_by_handle(operations: OverlayOperations) -> None:
    """An ftruncate through an open handle shortens the file."""
    fh = operations.open('/top.txt', os.O_RDWR)
    try:
        assert operations.truncate('/top.txt', 2, fh) == 0
    finally:
        operations.release('/top.txt', fh)
    assert operations.getattr('/top.txt')['st_size'] == 2


def test_fsync_returns_zero(operations: OverlayOperations) -> None:
    """fsync on a written handle succeeds."""
    fh = operations.create('/f.txt', 0o644)
    try:
        operations.write('/f.txt', b'data', 0, fh)
        assert operations.fsync('/f.txt', 0, fh) == 0
    finally:
        operations.release('/f.txt', fh)


def test_unknown_handle_is_ebadf(operations: OverlayOperations) -> None:
    """Reading with a file handle the adapter never issued is EBADF."""
    with raises(OSError) as info:
        operations.read('/top.txt', 1, 0, 987654)
    assert info.value.errno == errno.EBADF


# -- namespace --------------------------------------------------------------------------------------------------

def test_mkdir_and_rmdir(operations: OverlayOperations) -> None:
    """mkdir then rmdir create and remove a directory."""
    assert operations.mkdir('/newdir', 0o755) == 0
    assert stat.S_ISDIR(operations.getattr('/newdir')['st_mode'])
    assert operations.rmdir('/newdir') == 0
    with raises(OSError) as info:
        operations.getattr('/newdir')
    assert info.value.errno == errno.ENOENT


def test_unlink(operations: OverlayOperations) -> None:
    """unlink removes a file from the namespace."""
    assert operations.unlink('/data/b.txt') == 0
    with raises(OSError) as info:
        operations.getattr('/data/b.txt')
    assert info.value.errno == errno.ENOENT


def test_rename(operations: OverlayOperations) -> None:
    """rename moves a file; the old name is gone and the new one resolves."""
    assert operations.rename('/top.txt', '/moved.txt') == 0
    assert operations.getattr('/moved.txt')['st_size'] == len(b'top-level')
    with raises(OSError) as info:
        operations.getattr('/top.txt')
    assert info.value.errno == errno.ENOENT


# -- metadata (setattr) -----------------------------------------------------------------------------------------

def test_chmod_maps_to_engine(operations: OverlayOperations) -> None:
    """chmod changes the permission bits seen through getattr (no longer ENOSYS)."""
    assert operations.chmod('/data/a.txt', 0o600) == 0
    assert stat.S_IMODE(operations.getattr('/data/a.txt')['st_mode']) == 0o600


def test_chown_maps_to_engine(operations: OverlayOperations) -> None:
    """chown records ownership; -1 leaves a field unchanged."""
    assert operations.chown('/data/a.txt', os.getuid(), os.getgid()) == 0
    attrs = operations.getattr('/data/a.txt')
    assert (attrs['st_uid'], attrs['st_gid']) == (os.getuid(), os.getgid())


def test_utimens_explicit_times(operations: OverlayOperations) -> None:
    """utimens with an explicit (atime, mtime) tuple sets both."""
    assert operations.utimens('/data/a.txt', (555.0, 777.0)) == 0
    attrs = operations.getattr('/data/a.txt')
    assert (attrs['st_atime'], attrs['st_mtime']) == (555.0, 777.0)


def test_utimens_now_uses_clock(operations: OverlayOperations) -> None:
    """utimens with no times stamps the current (engine-clock) time over the seeded mtime."""
    assert operations.getattr('/top.txt')['st_mtime'] == 50.0
    assert operations.utimens('/top.txt', None) == 0
    assert operations.getattr('/top.txt')['st_mtime'] > 50.0


def test_chmod_missing_is_enoent(operations: OverlayOperations) -> None:
    """chmod on a missing path is ENOENT."""
    with raises(OSError) as info:
        operations.chmod('/nope', 0o600)
    assert info.value.errno == errno.ENOENT


# -- statfs -----------------------------------------------------------------------------------------------------

def test_statfs_reports_source_capacity(operations: OverlayOperations) -> None:
    """statfs on a POSIX source reports real filesystem capacity (a meaningful `df`)."""
    stv = operations.statfs('/')
    assert stv['f_bsize'] > 0
    assert stv['f_blocks'] > 0
    assert stv['f_namemax'] > 0
    assert stv['f_bavail'] <= stv['f_blocks']


def test_statfs_quota_fallback(tmp_path: Path, clock: Clock, monkeypatch) -> None:
    """When the source has no filesystem, statfs synthesizes numbers from the configured quota."""
    (tmp_path / 'project').mkdir()
    source = TierSpec(name='project', type='posix', path=str(tmp_path / 'project'),
                      role='source', quota=QuotaSpec(size=8 * BLOCK_SIZE, files=10))
    scratch = TierSpec(name='scratch', type='posix', path=str(tmp_path / 'scratch'),
                       role='stage', writeback=WritebackSpec())
    spec = OverlaySpec(name='space', state=tmp_path / 'state', tiers=(source, scratch))
    engine = OverlayEngine(spec, clock=clock)
    engine.start()
    try:
        operations = OverlayOperations(engine)

        def _unbounded(_path: str):
            raise OSError('no filesystem behind this source')

        monkeypatch.setattr('sosfs.fuse.adapter.os.statvfs', _unbounded)
        stv = operations.statfs('/')
        assert stv['f_bsize'] == BLOCK_SIZE
        assert stv['f_blocks'] == 8
        assert stv['f_bavail'] == 8            # nothing pushed to the source yet
        assert stv['f_files'] == 10
    finally:
        engine.stop()


# -- extended attributes ----------------------------------------------------------------------------------------

def test_listxattr_returns_tiering_attrs(operations: OverlayOperations) -> None:
    """listxattr advertises exactly the two read-only tiering attributes."""
    assert set(operations.listxattr('/top.txt')) == set(XATTRS)


def test_listxattr_missing_is_enoent(operations: OverlayOperations) -> None:
    """listxattr on a missing path is ENOENT."""
    with raises(OSError) as info:
        operations.listxattr('/nope')
    assert info.value.errno == errno.ENOENT


def test_getxattr_tiers_tracks_residency(operations: OverlayOperations) -> None:
    """The tiers xattr lists holders source-first; opening promotes a copy into scratch."""
    assert operations.getxattr('/data/a.txt', XATTR_TIERS) == b'project'
    fh = operations.open('/data/a.txt', os.O_RDONLY)
    try:
        assert operations.getxattr('/data/a.txt', XATTR_TIERS) == b'project,scratch'
    finally:
        operations.release('/data/a.txt', fh)


def test_getxattr_dirty_flag(operations: OverlayOperations) -> None:
    """The dirty xattr reads '0' for a clean source copy and '1' once a copy is uncommitted."""
    assert operations.getxattr('/data/a.txt', XATTR_DIRTY) == b'0'
    fh = operations.create('/dirty.txt', 0o644)
    try:
        operations.write('/dirty.txt', b'x', 0, fh)
        assert operations.getxattr('/dirty.txt', XATTR_DIRTY) == b'1'
    finally:
        operations.release('/dirty.txt', fh)


def test_getxattr_unknown_name_is_enoattr(operations: OverlayOperations) -> None:
    """An unrecognized xattr name on an existing file is ENOATTR."""
    with raises(OSError) as info:
        operations.getxattr('/top.txt', 'user.something.else')
    assert info.value.errno == ENOATTR


def test_getxattr_missing_path_is_enoent(operations: OverlayOperations) -> None:
    """An xattr read on a missing path is ENOENT (checked before the name)."""
    with raises(OSError) as info:
        operations.getxattr('/nope', XATTR_TIERS)
    assert info.value.errno == errno.ENOENT
