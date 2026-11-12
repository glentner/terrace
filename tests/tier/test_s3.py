# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for S3Tier semantics against a moto S3 server."""

# Type annotations
from __future__ import annotations

# Standard libs
import io
from pathlib import Path, PurePosixPath as P

# External libs
from pytest import mark, raises

# Internal libs
from terrace.state.lifecycle import Marker
from terrace.tier.protocol import Tier, FileMeta
from terrace.tier.s3 import S3Tier
from terrace.tier.spec import S3Spec


pytestmark = mark.unit

UUID = 'a0000000-0000-0000-0000-000000000001'


def put(tier: S3Tier, path: str, data: bytes, mtime: float = 100.0) -> None:
    """Store `data` at `path` (S3 keeps no POSIX mode/mtime, so those are informational)."""
    tier.put(P(path), io.BytesIO(data), meta=FileMeta(size=len(data), mtime=mtime))


def test_satisfies_protocol(tier: S3Tier) -> None:
    """S3Tier implements the full Tier contract."""
    assert isinstance(tier, Tier)


def test_stat_missing_is_none(tier: S3Tier) -> None:
    """Absent paths and paths through a file read as absent."""
    assert tier.stat(P('/missing')) is None
    put(tier, '/file', b'data')
    assert tier.stat(P('/file/below')) is None


def test_put_and_stat(tier: S3Tier) -> None:
    """Content lands whole; object storage exposes size and a server mtime but no POSIX bits."""
    put(tier, '/data.h5', b'hello world')
    result = tier.stat(P('/data.h5'))
    assert result is not None
    assert (result.kind, result.size) == ('file', 11)
    assert (result.mode, result.uid, result.gid) == (None, None, None)
    assert result.mtime > 0


def test_put_replaces(tier: S3Tier) -> None:
    """A second put replaces the first (atomic per key)."""
    put(tier, '/file', b'old contents')
    put(tier, '/file', b'new')
    assert tier.read(P('/file')) == b'new'


def test_put_from_path_source(tier: S3Tier, tmp_path: Path) -> None:
    """A filesystem Path works as the put source."""
    source = tmp_path / 'input'
    source.write_bytes(b'from a file')
    tier.put(P('/copy'), source, meta=FileMeta(size=11, mtime=100.0))
    assert tier.read(P('/copy')) == b'from a file'


def test_put_is_atomic_on_failure(tier: S3Tier) -> None:
    """A failing source raises and never leaves a partially written key visible."""

    class Exploding(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def readinto(self, buffer) -> int:
            raise IOError('source went away')

    with raises(OSError):
        tier.put(P('/victim'), Exploding(), meta=FileMeta(size=0, mtime=100.0))
    assert tier.stat(P('/victim')) is None


def test_read_offset_and_size(tier: S3Tier) -> None:
    """Range reads honor offset and size; -1 reads through end-of-file; missing raises."""
    put(tier, '/file', b'hello world')
    assert tier.read(P('/file')) == b'hello world'
    assert tier.read(P('/file'), offset=6) == b'world'
    assert tier.read(P('/file'), offset=6, size=3) == b'wor'
    with raises(FileNotFoundError):
        tier.read(P('/missing'))


def test_open_read_streams(tier: S3Tier) -> None:
    """open_read returns a seekable binary handle over the object."""
    put(tier, '/file', b'streaming')
    with tier.open_read(P('/file')) as stream:
        assert stream.read(6) == b'stream'
        assert stream.read() == b'ing'


def test_list_sorted_with_stats(tier: S3Tier) -> None:
    """Listings are name-sorted, mix files and directories, and carry stats."""
    tier.mkdir(P('/sub'))
    put(tier, '/beta', b'bb')
    put(tier, '/alpha', b'a')
    listing = list(tier.list(P('/')))
    assert [(entry.name, entry.kind) for entry in listing] == [('alpha', 'file'), ('beta', 'file'), ('sub', 'dir')]
    assert [entry.stat.size for entry in listing if entry.kind == 'file'] == [1, 2]
    with raises(FileNotFoundError):
        list(tier.list(P('/missing')))
    with raises(NotADirectoryError):
        list(tier.list(P('/alpha')))


def test_mkdir_rmdir(tier: S3Tier) -> None:
    """An empty directory is visible; parents are not created; rmdir requires empty."""
    tier.mkdir(P('/sub'))
    assert tier.stat(P('/sub')).kind == 'dir'
    with raises(FileNotFoundError):
        tier.mkdir(P('/a/b/c'))
    with raises(FileExistsError):
        tier.mkdir(P('/sub'))
    put(tier, '/sub/file', b'data')
    with raises(OSError):
        tier.rmdir(P('/sub'))
    tier.delete(P('/sub/file'))
    tier.rmdir(P('/sub'))
    assert tier.stat(P('/sub')) is None


def test_nested_directories(tier: S3Tier) -> None:
    """Directories nest, and a deep file resolves through them."""
    tier.mkdir(P('/a'))
    tier.mkdir(P('/a/b'))
    put(tier, '/a/b/c', b'deep')
    assert tier.stat(P('/a/b')).kind == 'dir'
    assert [entry.name for entry in tier.list(P('/a'))] == ['b']
    assert tier.read(P('/a/b/c')) == b'deep'


def test_rename_file_replaces(tier: S3Tier) -> None:
    """rename moves a file and replaces an existing one at the target."""
    put(tier, '/old', b'payload')
    put(tier, '/existing', b'stale')
    tier.rename(P('/old'), P('/existing'))
    assert tier.stat(P('/old')) is None
    assert tier.read(P('/existing')) == b'payload'


def test_rename_directory_moves_subtree(tier: S3Tier) -> None:
    """Renaming a directory relocates its whole prefix (marker and descendants)."""
    tier.mkdir(P('/src'))
    put(tier, '/src/a', b'1')
    tier.mkdir(P('/src/deep'))
    put(tier, '/src/deep/b', b'2')
    tier.rename(P('/src'), P('/dst'))
    assert tier.stat(P('/src')) is None
    assert tier.stat(P('/dst')).kind == 'dir'
    assert tier.read(P('/dst/a')) == b'1'
    assert tier.read(P('/dst/deep/b')) == b'2'


def test_delete_is_idempotent(tier: S3Tier) -> None:
    """delete removes an object; re-deleting a missing one never fails (a committed unlink can re-run)."""
    put(tier, '/file', b'data')
    tier.delete(P('/file'))
    assert tier.stat(P('/file')) is None
    tier.delete(P('/file'))


def test_usage_excludes_internals(tier: S3Tier) -> None:
    """usage sums real objects, blind to the marker and directory-keep sentinels."""
    tier.write_marker(Marker(uuid=UUID, tier='cloud', generation=0, written=100.0))
    put(tier, '/a', b'12345')
    tier.mkdir(P('/sub'))
    put(tier, '/sub/b', b'123')
    usage = tier.usage()
    assert usage.files == 2 and usage.bytes == 8


def test_managed_internals_hidden_and_guarded(tier: S3Tier) -> None:
    """'.sosfs' reads as absent, is excluded from listings, and refuses mutation."""
    tier.write_marker(Marker(uuid=UUID, tier='cloud', generation=0, written=100.0))
    assert tier.stat(P('/.sosfs')) is None
    assert tier.stat(P('/.sosfs/marker')) is None
    put(tier, '/visible', b'x')
    assert [entry.name for entry in tier.list(P('/'))] == ['visible']
    for operation in (lambda: put(tier, '/.sosfs/evil', b'x'),
                      lambda: tier.delete(P('/.sosfs/marker')),
                      lambda: tier.mkdir(P('/.sosfs/sub')),
                      lambda: tier.rmdir(P('/.sosfs')),
                      lambda: tier.rename(P('/.sosfs/marker'), P('/stolen')),
                      lambda: tier.rename(P('/visible'), P('/.sosfs/marker'))):
        with raises(PermissionError):
            operation()


def test_set_meta_verifies_existence(tier: S3Tier) -> None:
    """set_meta leaves content untouched (S3 cannot apply POSIX metadata) but requires the object to exist."""
    put(tier, '/f', b'payload')
    tier.set_meta(P('/f'), FileMeta(size=0, mtime=4242.0, mode=0o600))
    assert tier.read(P('/f')) == b'payload'
    with raises(FileNotFoundError):
        tier.set_meta(P('/nope'), FileMeta(size=0, mtime=1.0, mode=0o644))


def test_set_meta_refuses_internal(tier: S3Tier) -> None:
    """The managed '.sosfs' tree refuses metadata mutation like every other write."""
    with raises(PermissionError):
        tier.set_meta(P('/.sosfs/marker'), FileMeta(size=0, mtime=1.0, mode=0o600))


def test_escape_refused(tier: S3Tier) -> None:
    """Parent-directory components never resolve outside the tier."""
    with raises(ValueError):
        tier.stat(P('/../outside'))


def test_marker_round_trip(tier: S3Tier) -> None:
    """The managed-location marker writes and reads back through object I/O."""
    assert tier.read_marker() is None
    tier.write_marker(Marker(uuid=UUID, tier='cloud', generation=2, written=5.0))
    marker = tier.read_marker()
    assert (marker.uuid, marker.tier, marker.generation) == (UUID, 'cloud', 2)


def test_from_spec_round_trip(s3_spec: S3Spec) -> None:
    """S3Tier.from_spec builds a working store from parsed connection settings."""
    tier = S3Tier.from_spec('cloud', s3_spec)
    tier.put(P('/hello'), io.BytesIO(b'world'), meta=FileMeta(size=5, mtime=1.0))
    assert tier.read(P('/hello')) == b'world'
