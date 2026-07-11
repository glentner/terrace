# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The Tier protocol: storage contract and value types shared by all tier adapters."""

# Type annotations
from __future__ import annotations
from typing import Protocol, BinaryIO, Iterator, Optional, runtime_checkable

# Standard libs
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Internal libs
from sosfs.core.exceptions import SOSFSError

# Public interface
__all__ = ['Tier', 'TierStat', 'TierEntry', 'TierUsage', 'FileMeta', 'TierError']


class TierError(SOSFSError):
    """A tier backend failure with no standard `OSError` shape (connectivity, protocol, corruption)."""


@dataclass(frozen=True)
class TierStat:
    """
    What a tier knows about one stored object.

    Backends without POSIX semantics leave `mode`/`uid`/`gid` (and any timestamp they
    do not track) as None; `target` is set only for symlinks.
    """

    kind: str                        # 'file' | 'dir' | 'symlink' (matches the catalog)
    size: int
    mtime: float
    mode: Optional[int] = None
    uid: Optional[int] = None
    gid: Optional[int] = None
    atime: Optional[float] = None
    ctime: Optional[float] = None
    target: Optional[str] = None


@dataclass(frozen=True)
class TierEntry:
    """One name in a directory listing, with a full stat when the backend provides it cheaply."""

    name: str
    kind: str                        # 'file' | 'dir' | 'symlink'
    stat: Optional[TierStat] = None


@dataclass(frozen=True)
class TierUsage:
    """Stored totals for quota accounting."""

    bytes: int
    files: int


@dataclass(frozen=True)
class FileMeta:
    """Source-file metadata carried alongside content on `put`; adapters apply what their backend can."""

    size: int
    mtime: float
    mode: Optional[int] = None
    uid: Optional[int] = None
    gid: Optional[int] = None
    checksum: Optional[str] = None


@runtime_checkable
class Tier(Protocol):
    """
    The storage contract every tier adapter implements (PLAN.md -> Tier protocol).

    Paths are overlay-absolute POSIX paths ('/x/y'); each adapter maps them beneath
    its own root and keeps managed internals (the '.sosfs' marker directory) out of
    stats and listings. Path-shaped failures raise standard `OSError` subclasses
    (`FileNotFoundError`, `FileExistsError`, `NotADirectoryError`, ...) so callers
    map errno directly; failures with no OSError shape raise `TierError`. Adapters
    own their atomicity: `put` and `rename` must be all-or-nothing (temp-name +
    fsync + rename on POSIX, atomic PUT on S3) so a crash never leaves a partial
    object visible at the final path.
    """

    name: str

    def stat(self, path: PurePosixPath) -> Optional[TierStat]:
        """Metadata for `path`, or None when it does not exist."""

    def list(self, path: PurePosixPath) -> Iterator[TierEntry]:
        """Entries of the directory at `path`, excluding '.', '..', and managed internals."""

    def read(self, path: PurePosixPath, offset: int = 0, size: int = -1) -> bytes:
        """Bytes of the file at `path`; `size = -1` reads through end-of-file (range-ready)."""

    def open_read(self, path: PurePosixPath) -> BinaryIO:
        """A streaming binary handle over the file at `path`; the caller closes it."""

    def put(self, path: PurePosixPath, source: BinaryIO | Path, *, meta: FileMeta) -> None:
        """Store the full content of `source` at `path` atomically, applying `meta` where possible."""

    def set_meta(self, path: PurePosixPath, meta: FileMeta) -> None:
        """
        Apply `meta` (mode, ownership, mtime) to the existing object at `path`, as far as
        the backend allows — the content is untouched. `size` and `checksum` are ignored;
        ownership is best-effort where the process lacks privilege. Raises the natural
        `OSError` (e.g. `FileNotFoundError`) when nothing exists at `path`.
        """

    def delete(self, path: PurePosixPath) -> None:
        """Remove the file at `path`."""

    def mkdir(self, path: PurePosixPath, mode: int = 0o777) -> None:
        """Create the directory at `path` with permission bits `mode`; the parent must already exist."""

    def rmdir(self, path: PurePosixPath) -> None:
        """Remove the directory at `path`; it must be empty."""

    def rename(self, old: PurePosixPath, new: PurePosixPath) -> None:
        """Atomically move `old` to `new` within this tier, replacing any existing file at `new`."""

    def usage(self) -> TierUsage:
        """Current stored totals for quota accounting."""
