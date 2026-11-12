# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""FUSE adapter: map mfusepy's high-level path callbacks onto the overlay engine."""

# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Standard libs
import os
import stat
import errno
import threading
from pathlib import PurePosixPath

# External libs
from mfusepy import Operations, FuseOSError, ENOATTR

# Internal libs
from terrace.core.logging import Logger
from terrace.engine.overlay import OverlayEngine, Handle
from terrace.tier.spec import TierSpec

# Public interface
__all__ = ['OverlayOperations', 'XATTR_TIERS', 'XATTR_DIRTY']

# Initialize logger
log = Logger.with_name(__name__)


XATTR_TIERS = 'user.sosfs.tiers'                 # comma-separated tiers holding a copy, source-first
XATTR_DIRTY = 'user.sosfs.dirty'                 # '1' when a copy is uncommitted upstream, else '0'
XATTRS = (XATTR_TIERS, XATTR_DIRTY)

TYPE_BITS: Dict[str, int] = {'file': stat.S_IFREG, 'dir': stat.S_IFDIR, 'symlink': stat.S_IFLNK}

BLOCK_SIZE = 4096                                # synthetic statvfs block for quota-only sources


class OverlayOperations(Operations):
    """
    mfusepy `Operations` over a started `OverlayEngine` (PLAN.md -> FUSE adapter).

    Every callback is a thin translation to an engine VFS-op; the engine raises
    standard `OSError` subclasses, which mfusepy turns into `-errno`, so error
    mapping needs no code here. The adapter owns only FUSE-specific bookkeeping:
    allocating integer file handles over the engine's `Handle` objects, shaping
    `stat`/`statvfs` dicts, and serving the read-only observability xattrs. It
    knows nothing about mounting — the `mount` command owns the engine lifecycle.
    """

    def __init__(self, engine: OverlayEngine) -> None:
        """Wrap a started engine; no I/O happens until libfuse drives a callback."""
        self.engine = engine
        self._handles: Dict[int, Handle] = {}
        self._handles_mutex = threading.Lock()
        self._next_fh = 1                        # 0 is avoided so a set fh is never ambiguous

    # -- file-handle registry -----------------------------------------------------------------------------------

    def _register(self, handle: Handle) -> int:
        """Assign an integer handle for libfuse's `fi.fh` over an engine `Handle`."""
        with self._handles_mutex:
            fh = self._next_fh
            self._next_fh += 1
            self._handles[fh] = handle
        return fh

    def _handle(self, fh: int) -> Handle:
        """Resolve an open file handle, or EBADF if the kernel hands back an unknown one."""
        with self._handles_mutex:
            handle = self._handles.get(fh)
        if handle is None:
            raise FuseOSError(errno.EBADF)
        return handle

    # -- metadata -----------------------------------------------------------------------------------------------

    def getattr(self, path: str, fh: Optional[int] = None) -> Dict[str, Any]:
        """Shape an engine `Entry` into a stat(2) dict; the type bits join the stored mode."""
        entry = self.engine.getattr(path)
        mode = TYPE_BITS.get(entry.kind, stat.S_IFREG) | (entry.mode & 0o7777)
        size = len((entry.target or '').encode()) if entry.kind == 'symlink' else entry.size
        return {'st_mode': mode,
                'st_nlink': 2 if entry.kind == 'dir' else 1,
                'st_size': size,
                'st_uid': entry.uid,
                'st_gid': entry.gid,
                'st_atime': entry.atime,
                'st_mtime': entry.mtime,
                'st_ctime': entry.ctime}

    def readdir(self, path: str, fh: int) -> List[str]:
        """Directory names, with '.' and '..' the kernel expects prepended."""
        names = ['.', '..']
        names.extend(PurePosixPath(child.path).name for child in self.engine.readdir(path))
        return names

    def readlink(self, path: str) -> str:
        """Target of a symlink."""
        return self.engine.readlink(path)

    def statfs(self, path: str) -> Dict[str, int]:
        """Report the source tier's capacity so `df` on the mount is meaningful."""
        source = self.engine.spec.source
        try:
            vfs = os.statvfs(source.path)
        except OSError:
            return self._quota_statvfs(source)   # unbounded backend (e.g. S3): fall back to the quota
        return {'f_bsize': vfs.f_bsize, 'f_frsize': vfs.f_frsize,
                'f_blocks': vfs.f_blocks, 'f_bfree': vfs.f_bfree, 'f_bavail': vfs.f_bavail,
                'f_files': vfs.f_files, 'f_ffree': vfs.f_ffree, 'f_favail': vfs.f_favail,
                'f_namemax': vfs.f_namemax}

    def _quota_statvfs(self, source: TierSpec) -> Dict[str, int]:
        """Synthesize statvfs numbers from a configured quota (a source with no filesystem)."""
        usage = self.engine.registry.usage(source.name)
        total = source.quota.size or 0
        free = max(total - usage.bytes, 0)
        files_total = source.quota.files or 0
        files_free = max(files_total - usage.files, 0)
        return {'f_bsize': BLOCK_SIZE, 'f_frsize': BLOCK_SIZE,
                'f_blocks': total // BLOCK_SIZE, 'f_bfree': free // BLOCK_SIZE, 'f_bavail': free // BLOCK_SIZE,
                'f_files': files_total, 'f_ffree': files_free, 'f_favail': files_free,
                'f_namemax': 255}

    # -- extended attributes (read-only observability) ----------------------------------------------------------

    def listxattr(self, path: str) -> Iterable[str]:
        """The overlay's read-only tiering xattrs (ENOENT first for a missing path)."""
        self.engine.getattr(path)
        return list(XATTRS)

    def getxattr(self, path: str, name: str, position: int = 0) -> bytes:
        """Serve a tiering xattr; ENOENT for a missing path, ENOATTR for any other name."""
        residencies = self.engine.residencies(path)
        if name == XATTR_TIERS:
            return ','.join(residency.tier for residency in residencies).encode()
        if name == XATTR_DIRTY:
            return b'1' if any(residency.state == 'dirty' for residency in residencies) else b'0'
        raise FuseOSError(ENOATTR)

    # -- namespace ----------------------------------------------------------------------------------------------

    def mkdir(self, path: str, mode: int) -> int:
        """Create a directory."""
        self.engine.mkdir(path, mode)
        return 0

    def rmdir(self, path: str) -> int:
        """Remove an empty directory."""
        self.engine.rmdir(path)
        return 0

    def unlink(self, path: str) -> int:
        """Remove a file or symlink."""
        self.engine.unlink(path)
        return 0

    def rename(self, old: str, new: str) -> int:
        """Rename, replacing any file (or empty directory) at the destination."""
        self.engine.rename(old, new)
        return 0

    # -- metadata (setattr) -------------------------------------------------------------------------------------

    def chmod(self, path: str, mode: int) -> int:
        """Change permission bits (propagated to the source on the write-back clock)."""
        self.engine.chmod(path, mode)
        return 0

    def chown(self, path: str, uid: int, gid: int) -> int:
        """Change ownership; `-1` leaves a field unchanged, application is best-effort under privilege."""
        self.engine.chown(path, uid, gid)
        return 0

    def utimens(self, path: str, times: Optional[Tuple[float, float]] = None) -> int:
        """Set access and modification times; `None` means now (the engine clock)."""
        if times is None:
            atime = mtime = self.engine.clock()
        else:
            atime, mtime = times
        self.engine.utimens(path, atime, mtime)
        return 0

    # -- data path ----------------------------------------------------------------------------------------------

    def create(self, path: str, mode: int, flags: int = os.O_WRONLY | os.O_CREAT) -> int:
        """Create an empty file and return an open handle on its serving tier."""
        self.engine.create(path, mode)
        return self._register(self.engine.open(path, flags))

    def open(self, path: str, flags: int) -> int:
        """Promote-on-open and return an integer handle over the engine's `Handle`."""
        return self._register(self.engine.open(path, flags))

    def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
        """Read through the passthrough handle."""
        return self.engine.read(self._handle(fh), size, offset)

    def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
        """Write through the passthrough handle."""
        return self.engine.write(self._handle(fh), data, offset)

    def truncate(self, path: str, length: int, fh: Optional[int] = None) -> int:
        """Truncate by handle (ftruncate) when the kernel supplies one, else by path."""
        if fh is not None:
            self.engine.truncate(path, length, handle=self._handle(fh))
        else:
            self.engine.truncate(path, length)
        return 0

    def fsync(self, path: str, datasync: int, fh: int) -> int:
        """Make the handle durable in its resident tier (and commit upstream under `commit` policy)."""
        self.engine.fsync(self._handle(fh), bool(datasync))
        return 0

    def release(self, path: str, fh: int) -> int:
        """Close the handle and forget its mapping (the kernel never reuses it)."""
        with self._handles_mutex:
            handle = self._handles.pop(fh, None)
        if handle is not None:
            self.engine.release(handle)
        return 0
