# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""POSIX directory tier: the first Tier implementation, with passthrough handles for hot I/O."""

# Type annotations
from __future__ import annotations
from typing import BinaryIO, Iterator, Optional

# Standard libs
import os
import stat
import shutil
from uuid import uuid4
from pathlib import Path, PurePosixPath

# Internal libs
from terrace.core.logging import Logger
from terrace.state.journal import PARTIAL_DIR
from terrace.state.lifecycle import MARKER_DIR
from terrace.tier.protocol import TierStat, TierEntry, TierUsage, FileMeta

# Public interface
__all__ = ['PosixTier']

# Initialize logger
log = Logger.with_name(__name__)


ROOT = PurePosixPath('/')


class PosixTier:
    """
    Tier over a POSIX directory root.

    Implements the full `Tier` contract with `os`/`pathlib`, and additionally exposes
    `resolve` and `open_direct` so the engine can hand out passthrough file handles
    for hot I/O on resident copies (reads and writes on an already-resident file do
    not funnel through `read`/`put`). `put` is atomic: content lands in a temp under
    '.terrace/partial' (the journal's recognizable convention), is fsynced, and renames
    into place — a crash never leaves a partial file at the final path, only an
    orphan temp the audit discards. Managed internals ('.terrace' at the root) read as
    absent and refuse mutation; special files (devices, sockets, fifos) read as
    absent (out of scope for v1).
    """

    def __init__(self, name: str, root: str | Path) -> None:
        """Initialize over directory `root`; the directory must exist."""
        self.name = name
        self.root = Path(root)

    @staticmethod
    def _pure(path: PurePosixPath | str) -> PurePosixPath:
        """Normalize to overlay-absolute form, refusing parent-directory escapes."""
        pure = ROOT / path
        if '..' in pure.parts:
            raise ValueError(f'Path escapes tier root: {path}')
        return pure

    @staticmethod
    def _internal(pure: PurePosixPath) -> bool:
        """Whether `pure` is the managed '.terrace' directory at the tier root (or within it)."""
        return pure.parts[1:2] == (MARKER_DIR, )

    def resolve(self, path: PurePosixPath | str) -> Path:
        """Real filesystem path for overlay-absolute `path` (the passthrough mapping)."""
        return self.root / self._pure(path).relative_to(ROOT)

    def open_direct(self, path: PurePosixPath | str, flags: int, mode: int = 0o644) -> int:
        """OS-level passthrough handle for hot I/O on a resident copy; the caller owns the fd."""
        pure = self._pure(path)
        if self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        return os.open(self.resolve(pure), flags, mode)

    def _tier_stat(self, st: os.stat_result, real: Path) -> Optional[TierStat]:
        """Map an lstat result onto TierStat; None for special files (invisible in v1)."""
        if stat.S_ISREG(st.st_mode):
            kind, target = 'file', None
        elif stat.S_ISDIR(st.st_mode):
            kind, target = 'dir', None
        elif stat.S_ISLNK(st.st_mode):
            kind, target = 'symlink', os.readlink(real)
        else:
            return None
        return TierStat(kind=kind, size=st.st_size, mtime=st.st_mtime, mode=stat.S_IMODE(st.st_mode),
                        uid=st.st_uid, gid=st.st_gid, atime=st.st_atime, ctime=st.st_ctime, target=target)

    def stat(self, path: PurePosixPath) -> Optional[TierStat]:
        """Metadata for `path`, or None when missing."""
        pure = self._pure(path)
        if self._internal(pure):
            return None
        real = self.resolve(pure)
        try:
            return self._tier_stat(os.lstat(real), real)
        except (FileNotFoundError, NotADirectoryError):
            return None

    def list(self, path: PurePosixPath) -> Iterator[TierEntry]:
        """Entries of the directory at `path`, sorted by name."""
        pure = self._pure(path)
        directory = self.resolve(pure)
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if pure == ROOT and entry.name == MARKER_DIR:
                    continue
                tier_stat = self._tier_stat(entry.stat(follow_symlinks=False), Path(entry.path))
                if tier_stat is not None:
                    yield TierEntry(name=entry.name, kind=tier_stat.kind, stat=tier_stat)

    def read(self, path: PurePosixPath, offset: int = 0, size: int = -1) -> bytes:
        """Bytes of the file at `path`."""
        with self.open_read(path) as stream:
            if offset:
                stream.seek(offset)
            return stream.read(size)

    def open_read(self, path: PurePosixPath) -> BinaryIO:
        """A streaming binary handle over the file at `path`; the caller closes it."""
        pure = self._pure(path)
        if self._internal(pure):
            raise FileNotFoundError(f'No such file: {pure}')
        return open(self.resolve(pure), mode='rb')

    def put(self, path: PurePosixPath, source: BinaryIO | Path, *, meta: FileMeta) -> None:
        """Store the full content of `source` at `path` via temp-name + fsync + rename."""
        pure = self._pure(path)
        if self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        final = self.resolve(pure)
        partial = self.root / PARTIAL_DIR
        partial.mkdir(parents=True, exist_ok=True)
        temp = partial / f'put-{uuid4().hex}-{pure.name}'
        try:
            with open(temp, mode='wb') as stream:
                if isinstance(source, Path):
                    with open(source, mode='rb') as content:
                        shutil.copyfileobj(content, stream)
                else:
                    shutil.copyfileobj(source, stream)
                stream.flush()
                os.fsync(stream.fileno())
            self._apply_meta(temp, meta)
            os.replace(temp, final)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        log.trace(f'Put {pure} (tier={self.name}, size={meta.size})')

    def set_meta(self, path: PurePosixPath, meta: FileMeta) -> None:
        """Apply mode/ownership/mtime to the existing object at `path` (content untouched)."""
        pure = self._pure(path)
        if self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        self._apply_meta(self.resolve(pure), meta)
        log.trace(f'Set meta {pure} (tier={self.name}, mode={meta.mode}, mtime={meta.mtime})')

    @staticmethod
    def _apply_meta(real: Path, meta: FileMeta) -> None:
        """Apply what the backend can and what `meta` sets: mode/mtime when given, ownership with privilege."""
        if meta.mode is not None:
            os.chmod(real, stat.S_IMODE(meta.mode))
        if meta.uid is not None or meta.gid is not None:
            try:
                os.chown(real, meta.uid if meta.uid is not None else -1,
                         meta.gid if meta.gid is not None else -1)
            except PermissionError:
                log.trace(f'Ownership not applied (unprivileged): {real}')
        if meta.mtime is not None:
            os.utime(real, (meta.mtime, meta.mtime))

    def delete(self, path: PurePosixPath) -> None:
        """Remove the file at `path`."""
        pure = self._pure(path)
        if self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        os.unlink(self.resolve(pure))
        log.trace(f'Deleted {pure} (tier={self.name})')

    def mkdir(self, path: PurePosixPath, mode: int = 0o777, *,
              uid: Optional[int] = None, gid: Optional[int] = None) -> None:
        """Create the directory at `path`; the parent must already exist. Ownership is best-effort."""
        pure = self._pure(path)
        if self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        real = self.resolve(pure)
        os.mkdir(real, mode)
        # Owner before the exact bits: a chown that changes the group can clear the setgid bit.
        self._apply_meta(real, FileMeta(size=0, mtime=None, uid=uid, gid=gid))
        os.chmod(real, stat.S_IMODE(mode))  # exact bits, unaffected by the process umask
        log.trace(f'Mkdir {pure} (tier={self.name})')

    def rmdir(self, path: PurePosixPath) -> None:
        """Remove the directory at `path`; it must be empty."""
        pure = self._pure(path)
        if pure == ROOT or self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        os.rmdir(self.resolve(pure))
        log.trace(f'Rmdir {pure} (tier={self.name})')

    def rename(self, old: PurePosixPath, new: PurePosixPath) -> None:
        """Atomically move `old` to `new`, replacing any existing file at `new`."""
        pure_old, pure_new = self._pure(old), self._pure(new)
        if pure_old == ROOT or self._internal(pure_old) or self._internal(pure_new):
            raise PermissionError(f'Managed internal: {pure_old} -> {pure_new}')
        os.replace(self.resolve(pure_old), self.resolve(pure_new))
        log.trace(f'Renamed {pure_old} -> {pure_new} (tier={self.name})')

    def usage(self) -> TierUsage:
        """Walk the root summing regular files and symlinks, excluding managed internals."""
        total, count = 0, 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            if Path(dirpath) == self.root and MARKER_DIR in dirnames:
                dirnames.remove(MARKER_DIR)
            for name in filenames:
                st = os.lstat(os.path.join(dirpath, name))
                if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                    total += st.st_size
                    count += 1
        return TierUsage(bytes=total, files=count)
