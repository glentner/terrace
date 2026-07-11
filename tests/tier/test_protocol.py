# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Tier protocol types and structural contract."""

# Type annotations
from __future__ import annotations
from typing import BinaryIO, Dict, Iterator, Optional, Set, Tuple

# Standard libs
import io
import dataclasses
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.core.exceptions import SOSFSError
from sosfs.tier.protocol import Tier, TierStat, TierEntry, TierUsage, FileMeta, TierError


pytestmark = mark.unit


class MemoryTier:
    """Minimal in-memory implementation of the full contract, for structural tests."""

    name = 'memory'

    def __init__(self) -> None:
        self.files: Dict[str, Tuple[bytes, FileMeta]] = {}
        self.dirs: Set[str] = {'/'}

    def stat(self, path: PurePosixPath) -> Optional[TierStat]:
        key = str(path)
        if key in self.dirs:
            return TierStat(kind='dir', size=0, mtime=0.0)
        if key in self.files:
            data, meta = self.files[key]
            return TierStat(kind='file', size=len(data), mtime=meta.mtime, mode=meta.mode)
        return None

    def list(self, path: PurePosixPath) -> Iterator[TierEntry]:
        prefix = str(path).rstrip('/') + '/'
        for key in sorted(self.dirs | set(self.files)):
            if key != '/' and key.startswith(prefix) and '/' not in key[len(prefix):]:
                yield TierEntry(name=key[len(prefix):], kind='dir' if key in self.dirs else 'file')

    def read(self, path: PurePosixPath, offset: int = 0, size: int = -1) -> bytes:
        data, _ = self.files[str(path)]
        return data[offset:] if size < 0 else data[offset:offset + size]

    def open_read(self, path: PurePosixPath) -> BinaryIO:
        return io.BytesIO(self.files[str(path)][0])

    def put(self, path: PurePosixPath, source: BinaryIO | Path, *, meta: FileMeta) -> None:
        data = source.read_bytes() if isinstance(source, Path) else source.read()
        self.files[str(path)] = (data, meta)

    def set_meta(self, path: PurePosixPath, meta: FileMeta) -> None:
        data, previous = self.files[str(path)]
        self.files[str(path)] = (data, dataclasses.replace(previous, mtime=meta.mtime, mode=meta.mode))

    def delete(self, path: PurePosixPath) -> None:
        del self.files[str(path)]

    def mkdir(self, path: PurePosixPath, mode: int = 0o777) -> None:
        self.dirs.add(str(path))

    def rmdir(self, path: PurePosixPath) -> None:
        self.dirs.remove(str(path))

    def rename(self, old: PurePosixPath, new: PurePosixPath) -> None:
        self.files[str(new)] = self.files.pop(str(old))

    def usage(self) -> TierUsage:
        return TierUsage(bytes=sum(len(data) for data, _ in self.files.values()), files=len(self.files))


def test_types_construct_with_defaults() -> None:
    """POSIX-only fields default to None; listings may omit the stat."""
    stat = TierStat(kind='file', size=42, mtime=100.0)
    assert (stat.mode, stat.uid, stat.gid, stat.atime, stat.ctime, stat.target) == (None,) * 6
    entry = TierEntry(name='data.h5', kind='file')
    assert entry.stat is None
    meta = FileMeta(size=42, mtime=100.0)
    assert (meta.mode, meta.uid, meta.gid, meta.checksum) == (None,) * 4
    assert TierUsage(bytes=42, files=1) == TierUsage(bytes=42, files=1)


def test_types_are_frozen() -> None:
    """Value types are immutable."""
    stat = TierStat(kind='file', size=42, mtime=100.0)
    with raises(dataclasses.FrozenInstanceError):
        stat.size = 0


def test_tier_error_is_domain_error() -> None:
    """TierError participates in the shared exception mapping."""
    assert issubclass(TierError, SOSFSError)


def test_structural_conformance() -> None:
    """A complete implementation satisfies the runtime-checkable protocol; an incomplete one does not."""
    assert isinstance(MemoryTier(), Tier)

    class Incomplete:
        name = 'incomplete'

        def stat(self, path: PurePosixPath) -> Optional[TierStat]:
            return None

    assert not isinstance(Incomplete(), Tier)


def test_contract_round_trip() -> None:
    """The contract as specified is implementable: put, stat, read, list, rename, delete, usage."""
    tier: Tier = MemoryTier()
    path = PurePosixPath('/a/data.h5')
    tier.mkdir(PurePosixPath('/a'))
    tier.put(path, io.BytesIO(b'hello world'), meta=FileMeta(size=11, mtime=100.0, mode=0o644))

    stat = tier.stat(path)
    assert stat is not None and (stat.kind, stat.size, stat.mode) == ('file', 11, 0o644)
    assert tier.stat(PurePosixPath('/a/missing')) is None

    assert tier.read(path) == b'hello world'
    assert tier.read(path, offset=6, size=5) == b'world'
    with tier.open_read(path) as stream:
        assert stream.read() == b'hello world'

    assert [(entry.name, entry.kind) for entry in tier.list(PurePosixPath('/a'))] == [('data.h5', 'file')]
    assert tier.usage() == TierUsage(bytes=11, files=1)

    tier.rename(path, PurePosixPath('/a/renamed.h5'))
    assert tier.stat(path) is None
    tier.delete(PurePosixPath('/a/renamed.h5'))
    tier.rmdir(PurePosixPath('/a'))
    assert tier.usage() == TierUsage(bytes=0, files=0)
