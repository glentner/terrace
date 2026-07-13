# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Archive tier (ZIP/TAR) over an inner location: member indexing cached in the state store."""

# Type annotations
from __future__ import annotations
from typing import TYPE_CHECKING, BinaryIO, Iterator, Optional, Protocol, Final, runtime_checkable

# Standard libs
import io
import json
import stat
import time
import errno
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from dataclasses import dataclass, field

# Internal libs
from sosfs.core.logging import Logger
from sosfs.state.lifecycle import MARKER_DIR
from sosfs.tier.protocol import TierStat, TierEntry, TierUsage, FileMeta

if TYPE_CHECKING:
    from sosfs.state.db import Database

# Public interface
__all__ = ['ArchiveTier', 'ArchiveMember', 'InnerLocation', 'PosixInnerLocation']

# Initialize logger
log = Logger.with_name(__name__)


ROOT = PurePosixPath('/')
FORMATS: Final[frozenset] = frozenset({'zip', 'tar', 'tar.gz'})
TAR_MODES: Final[dict] = {'tar': 'r:', 'tar.gz': 'r:gz'}
SYNTHETIC: Final[int] = -1                       # offset sentinel: a directory the archive never stored a header for


@runtime_checkable
class InnerLocation(Protocol):
    """
    The single archive blob an `ArchiveTier` layers over (PLAN.md -> ArchiveTier).

    An archive is one file at an inner location — a POSIX path first, an S3 object next.
    The tier only needs to read the whole blob (to index and to extract members) and to
    tell whether it exists and has changed; the write side that replaces the blob (staged
    re-sync) lands with that item. `signature` returns a cheap identity (size, mtime) or
    None when nothing exists yet.
    """

    identity: str

    def signature(self) -> Optional[tuple[int, float]]:
        """A cheap change-detection identity (size, mtime), or None when the archive is absent."""

    def open_read(self) -> BinaryIO:
        """A seekable binary handle over the whole archive blob; the caller closes it."""


class PosixInnerLocation:
    """An inner location backed by a filesystem path (the first archive backing, PLAN.md -> ArchiveTier)."""

    def __init__(self, path: str | Path) -> None:
        """Initialize over filesystem `path` (the archive file need not exist yet)."""
        self.path = Path(path)
        self.identity = str(self.path)

    def signature(self) -> Optional[tuple[int, float]]:
        """Size and mtime of the archive file, or None when it does not exist."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return st.st_size, st.st_mtime

    def open_read(self) -> BinaryIO:
        """A seekable read handle over the archive file."""
        return open(self.path, mode='rb')


@dataclass(frozen=True)
class ArchiveMember:
    """One indexed member of an archive, as cached in `archive_index`."""

    member: str                                  # overlay-absolute path within the archive ('/x/y')
    kind: str                                    # 'file' | 'dir' | 'symlink'
    offset: int                                  # TAR: header block offset; ZIP: local header offset; SYNTHETIC for dirs
    size: int
    mtime: float
    mode: Optional[int]
    extra: dict = field(default_factory=dict)    # format-specific: {'name': <archive member name>, 'target': <symlink>}


def _zip_mtime(date_time: tuple) -> float:
    """Convert a ZIP DOS date-time 6-tuple to epoch seconds (local time; ZIP carries no zone)."""
    try:
        return time.mktime((*date_time, 0, 0, -1))
    except (ValueError, OverflowError):
        return 0.0


class ArchiveTier:
    """
    Tier over a ZIP or TAR archive at an inner location (PLAN.md -> ArchiveTier).

    The archive is indexed once — ZIP from the central directory, TAR by a single
    sequential scan — and the member map is cached in `archive_index` keyed by tier
    name, so a remount reuses it with no eager rescan. Ancestor directories the archive
    omitted are synthesized so `stat`/`list` see a complete tree. Reads extract a whole
    member through the stdlib (`zipfile`/`tarfile`); offset-seeked and streaming reads,
    and indexed random access into compressed archives, are future work (PLAN.md ->
    Future work). This is the read skeleton: the write side (ZIP append-supersede with
    compaction, TAR append, staged re-sync) lands with the direct-update and staged
    re-sync items, so the mutating operations raise `NotImplementedError` for now.
    Callers invalidate a rebuilt archive with `reindex`; within a mount session the
    archive is immutable to readers. The index cache is FK-bound to the tier registry,
    so the tier must be registered (the engine's attach step) before it is indexed.
    """

    def __init__(self, name: str, inner: InnerLocation, db: Database, fmt: str) -> None:
        """Initialize over `inner` with member cache in `db`; `fmt` is one of zip/tar/tar.gz."""
        if fmt not in FORMATS:
            raise ValueError(f'Unsupported archive format: {fmt!r} (expected one of {", ".join(sorted(FORMATS))})')
        self.name = name
        self.inner = inner
        self.db = db
        self.fmt = fmt
        self._members: Optional[dict[str, ArchiveMember]] = None
        self._children: Optional[dict[str, list[str]]] = None

    # -- path helpers -----------------------------------------------------------------------------------------------

    @staticmethod
    def _pure(path: PurePosixPath | str) -> PurePosixPath:
        """Normalize to overlay-absolute form, refusing parent-directory escapes."""
        pure = ROOT / path
        if '..' in pure.parts:
            raise ValueError(f'Path escapes tier root: {path}')
        return pure

    @staticmethod
    def _internal(pure: PurePosixPath) -> bool:
        """Whether `pure` is the managed '.sosfs' directory at the tier root (or within it)."""
        return pure.parts[1:2] == (MARKER_DIR, )

    @classmethod
    def _member_path(cls, name: str) -> Optional[PurePosixPath]:
        """Overlay-absolute path for an archive member name, or None to skip (root, escape, internal)."""
        rel = name.strip('/')
        if not rel or rel == '.':
            return None
        pure = ROOT / rel
        if '..' in pure.parts or cls._internal(pure):
            return None
        return pure

    # -- indexing ---------------------------------------------------------------------------------------------------

    def index(self) -> None:
        """Ensure the member map is loaded: from the cache when present, otherwise scan and persist."""
        if self._members is not None:
            return
        cached = self._load_cache()
        if not cached:
            cached = self._scan()
            self._persist(cached)
            log.debug(f'Indexed archive {self.inner.identity} ({len(cached)} members, tier={self.name})')
        self._build_maps(cached)

    def reindex(self) -> None:
        """Rescan the archive and replace the cache — the seam for a rebuilt (re-synced/compacted) archive."""
        members = self._scan()
        self._persist(members)
        self._build_maps(members)
        log.debug(f'Reindexed archive {self.inner.identity} ({len(members)} members, tier={self.name})')

    def _build_maps(self, members: list[ArchiveMember]) -> None:
        """Populate the in-memory member and children maps from an index."""
        self._members = {member.member: member for member in members}
        children: dict[str, list[str]] = {}
        for member in members:
            children.setdefault(str(PurePosixPath(member.member).parent), []).append(member.member)
        self._children = children

    def _load_cache(self) -> list[ArchiveMember]:
        """Read the cached member index for this tier from `archive_index`."""
        rows = self.db.execute(t'SELECT * FROM archive_index WHERE tier = {self.name} ORDER BY member')
        return [ArchiveMember(member=row['member'], kind=row['kind'], offset=row['offset'], size=row['size'],
                              mtime=row['mtime'], mode=row['mode'],
                              extra=json.loads(row['extra']) if row['extra'] else {})
                for row in rows]

    def _persist(self, members: list[ArchiveMember]) -> None:
        """Replace the cached index for this tier in one transaction."""
        with self.db.transaction():
            self.db.execute(t'DELETE FROM archive_index WHERE tier = {self.name}')
            for member in members:
                self.db.execute(
                    t'INSERT INTO archive_index (tier, member, kind, offset, size, mtime, mode, extra) '
                    t'VALUES ({self.name}, {member.member}, {member.kind}, {member.offset}, {member.size}, '
                    t'        {member.mtime}, {member.mode}, {json.dumps(member.extra)})')

    def _scan(self) -> list[ArchiveMember]:
        """Read every member from the archive; an absent archive indexes as empty."""
        if self.inner.signature() is None:
            return []
        with self.inner.open_read() as handle:
            raw = self._scan_zip(handle) if self.fmt == 'zip' else self._scan_tar(handle)
        return self._with_ancestors(raw)

    def _scan_zip(self, handle: BinaryIO) -> list[ArchiveMember]:
        """Index a ZIP from its central directory (offset is each member's local-header offset)."""
        members: list[ArchiveMember] = []
        with zipfile.ZipFile(handle) as archive:
            for info in archive.infolist():
                pure = self._member_path(info.filename)
                if pure is None:
                    continue
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                extra = {'name': info.filename}
                if info.is_dir():
                    kind, size, mode = 'dir', 0, (stat.S_IMODE(unix_mode) if unix_mode else None)
                elif unix_mode and stat.S_ISLNK(unix_mode):
                    kind, size, mode = 'symlink', info.file_size, stat.S_IMODE(unix_mode)
                    extra['target'] = archive.read(info).decode('utf-8', 'surrogateescape')
                else:
                    kind, size = 'file', info.file_size
                    mode = stat.S_IMODE(unix_mode) if unix_mode else None
                members.append(ArchiveMember(member=str(pure), kind=kind, offset=info.header_offset,
                                             size=size, mtime=_zip_mtime(info.date_time), mode=mode, extra=extra))
        return members

    def _scan_tar(self, handle: BinaryIO) -> list[ArchiveMember]:
        """Index a TAR by one sequential scan (offset is each member's header-block offset)."""
        members: list[ArchiveMember] = []
        with tarfile.open(fileobj=handle, mode=TAR_MODES[self.fmt]) as archive:
            for info in archive.getmembers():
                pure = self._member_path(info.name)
                if pure is None:
                    continue
                extra = {'name': info.name}
                if info.isdir():
                    kind, size = 'dir', 0
                elif info.issym():
                    kind, size = 'symlink', 0
                    extra['target'] = info.linkname
                elif info.isreg():
                    kind, size = 'file', info.size
                else:
                    continue                     # devices, fifos, hard links: out of scope for v1
                members.append(ArchiveMember(member=str(pure), kind=kind, offset=info.offset,
                                             size=size, mtime=float(info.mtime), mode=stat.S_IMODE(info.mode),
                                             extra=extra))
        return members

    @staticmethod
    def _with_ancestors(members: list[ArchiveMember]) -> list[ArchiveMember]:
        """Synthesize directory members for any ancestor the archive omitted, so the tree is complete."""
        have = {member.member for member in members}
        synthetic: list[ArchiveMember] = []
        for member in members:
            parent = PurePosixPath(member.member).parent
            while str(parent) != '/' and str(parent) not in have:
                have.add(str(parent))
                synthetic.append(ArchiveMember(member=str(parent), kind='dir', offset=SYNTHETIC,
                                               size=0, mtime=member.mtime, mode=None))
                parent = parent.parent
        return members + synthetic

    def _extract(self, member: ArchiveMember) -> bytes:
        """Read a file member's whole content from the archive (streaming/seek is future work)."""
        name = member.extra['name']
        with self.inner.open_read() as handle:
            if self.fmt == 'zip':
                with zipfile.ZipFile(handle) as archive:
                    return archive.read(name)
            with tarfile.open(fileobj=handle, mode=TAR_MODES[self.fmt]) as archive:
                extracted = archive.extractfile(name)
                if extracted is None:
                    return b''
                with extracted:
                    return extracted.read()

    # -- read-only Tier contract ------------------------------------------------------------------------------------

    def _stat_of(self, member: ArchiveMember) -> TierStat:
        """Map an indexed member onto a TierStat."""
        return TierStat(kind=member.kind, size=member.size, mtime=member.mtime,
                        mode=member.mode, target=member.extra.get('target'))

    def stat(self, path: PurePosixPath) -> Optional[TierStat]:
        """Metadata for `path`, or None when missing; the root is always a directory."""
        self.index()
        pure = self._pure(path)
        if pure == ROOT:
            return TierStat(kind='dir', size=0, mtime=0.0)
        if self._internal(pure):
            return None
        member = self._members.get(str(pure))
        return None if member is None else self._stat_of(member)

    def list(self, path: PurePosixPath) -> Iterator[TierEntry]:
        """Entries of the directory at `path`, name-sorted, excluding managed internals."""
        self.index()
        pure = self._pure(path)
        if pure != ROOT:
            member = self._members.get(str(pure))
            if member is None:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure))
            if member.kind != 'dir':
                raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', str(pure))
        for child in sorted(self._children.get(str(pure), [])):
            member = self._members[child]
            yield TierEntry(name=PurePosixPath(child).name, kind=member.kind, stat=self._stat_of(member))

    def read(self, path: PurePosixPath, offset: int = 0, size: int = -1) -> bytes:
        """Bytes of the file at `path`; the whole member is extracted, then offset/size is applied."""
        self.index()
        pure = self._pure(path)
        if self._internal(pure):
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure))
        member = self._members.get(str(pure))
        if member is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure))
        if member.kind == 'dir':
            raise IsADirectoryError(errno.EISDIR, 'Is a directory', str(pure))
        if member.kind == 'symlink':
            raise OSError(errno.EINVAL, 'Cannot read a symlink as a file', str(pure))
        data = self._extract(member)
        if size < 0:
            return data[offset:] if offset else data
        return data[offset:offset + size]

    def open_read(self, path: PurePosixPath) -> BinaryIO:
        """A binary handle over the file at `path` (buffered whole; streaming is future work)."""
        return io.BytesIO(self.read(path))

    def usage(self) -> TierUsage:
        """Stored totals from the index: summed file sizes and file count."""
        self.index()
        total = count = 0
        for member in self._members.values():
            if member.kind == 'file':
                total += member.size
                count += 1
        return TierUsage(bytes=total, files=count)

    # -- write contract (direct-update / staged re-sync items) ------------------------------------------------------

    def put(self, path: PurePosixPath, source: BinaryIO | Path, *, meta: FileMeta) -> None:
        """Not yet implemented — archive writes land with the direct-update / staged re-sync items."""
        raise NotImplementedError('Archive writes land in the direct-update / staged re-sync items (PLAN.md)')

    def set_meta(self, path: PurePosixPath, meta: FileMeta) -> None:
        """Not yet implemented — archive metadata mutation lands with the write items."""
        raise NotImplementedError('Archive writes land in the direct-update / staged re-sync items (PLAN.md)')

    def delete(self, path: PurePosixPath) -> None:
        """Not yet implemented — archive deletions (tombstone + compaction) land with the write items."""
        raise NotImplementedError('Archive writes land in the direct-update / staged re-sync items (PLAN.md)')

    def mkdir(self, path: PurePosixPath, mode: int = 0o777) -> None:
        """Not yet implemented — archive namespace writes land with the write items."""
        raise NotImplementedError('Archive writes land in the direct-update / staged re-sync items (PLAN.md)')

    def rmdir(self, path: PurePosixPath) -> None:
        """Not yet implemented — archive namespace writes land with the write items."""
        raise NotImplementedError('Archive writes land in the direct-update / staged re-sync items (PLAN.md)')

    def rename(self, old: PurePosixPath, new: PurePosixPath) -> None:
        """Not yet implemented — archive renames land with the write items."""
        raise NotImplementedError('Archive writes land in the direct-update / staged re-sync items (PLAN.md)')
