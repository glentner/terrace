# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for PosixTier semantics against tmpdir roots."""

# Type annotations
from __future__ import annotations

# Standard libs
import io
import os
import stat
from pathlib import Path, PurePosixPath

# External libs
from pytest import fixture, mark, raises

# Internal libs
from terrace.state.journal import PARTIAL_DIR, is_temp_name
from terrace.state.lifecycle import Marker
from terrace.tier.protocol import Tier, FileMeta
from terrace.tier.posix import PosixTier


pytestmark = mark.unit

UUID = 'a0000000-0000-0000-0000-000000000001'


@fixture
def tier(tmp_path: Path) -> PosixTier:
    """A PosixTier over a fresh root."""
    return PosixTier('scratch', tmp_path)


def put(tier: PosixTier, path: str, data: bytes, mode: int = 0o644, mtime: float = 100.0) -> None:
    """Store `data` at `path` with simple metadata."""
    tier.put(PurePosixPath(path), io.BytesIO(data), meta=FileMeta(size=len(data), mtime=mtime, mode=mode))


def test_satisfies_protocol(tier: PosixTier) -> None:
    """PosixTier implements the full Tier contract."""
    assert isinstance(tier, Tier)


def test_stat_missing_is_none(tier: PosixTier) -> None:
    """Absent paths and paths through non-directories read as absent."""
    assert tier.stat(PurePosixPath('/missing')) is None
    put(tier, '/file', b'data')
    assert tier.stat(PurePosixPath('/file/below')) is None


def test_put_and_stat_applies_meta(tier: PosixTier) -> None:
    """Content lands whole; mode and mtime from FileMeta are applied, and ownership is what a plain create gets."""
    reference = tier.root / 'reference'
    reference.touch()
    put(tier, '/data.h5', b'hello world', mode=0o640, mtime=1234.0)
    result = tier.stat(PurePosixPath('/data.h5'))
    assert result is not None
    assert (result.kind, result.size, result.mode, result.mtime) == ('file', 11, 0o640, 1234.0)
    # A new file takes its directory's group on BSD and under set-gid, so the process's gid is not the expectation.
    assert (result.uid, result.gid) == (os.getuid(), reference.stat().st_gid)


def test_put_replaces(tier: PosixTier) -> None:
    """A second put atomically replaces the first."""
    put(tier, '/file', b'old contents')
    put(tier, '/file', b'new')
    assert tier.read(PurePosixPath('/file')) == b'new'


def test_put_from_path_source(tier: PosixTier, tmp_path_factory) -> None:
    """A filesystem Path works as the put source."""
    source = tmp_path_factory.mktemp('source') / 'input'
    source.write_bytes(b'from a file')
    tier.put(PurePosixPath('/copy'), source, meta=FileMeta(size=11, mtime=100.0))
    assert tier.read(PurePosixPath('/copy')) == b'from a file'


def test_put_failure_leaves_no_partial(tier: PosixTier) -> None:
    """A failing source never surfaces a partial file at the final path (or a leftover temp)."""

    class Exploding(io.RawIOBase):
        def read(self, size: int = -1) -> bytes:
            raise IOError('source went away')

    with raises(IOError):
        tier.put(PurePosixPath('/victim'), Exploding(), meta=FileMeta(size=0, mtime=100.0))
    assert tier.stat(PurePosixPath('/victim')) is None
    assert list((Path(tier.root) / PARTIAL_DIR).iterdir()) == []


def test_put_temps_are_recognizable(tier: PosixTier, monkeypatch) -> None:
    """The temp naming matches the journal convention the audit discards."""
    recorded = []
    original = os.replace

    def spy(src, dst):
        recorded.append(Path(src).relative_to(tier.root).as_posix())
        original(src, dst)

    monkeypatch.setattr(os, 'replace', spy)
    put(tier, '/file', b'data')
    assert len(recorded) == 1 and is_temp_name(recorded[0])


def test_read_offset_and_size(tier: PosixTier) -> None:
    """Range reads honor offset and size; -1 reads through end-of-file."""
    put(tier, '/file', b'hello world')
    assert tier.read(PurePosixPath('/file')) == b'hello world'
    assert tier.read(PurePosixPath('/file'), offset=6) == b'world'
    assert tier.read(PurePosixPath('/file'), offset=6, size=3) == b'wor'
    with raises(FileNotFoundError):
        tier.read(PurePosixPath('/missing'))


def test_open_read_streams(tier: PosixTier) -> None:
    """open_read returns a seekable binary handle."""
    put(tier, '/file', b'streaming')
    with tier.open_read(PurePosixPath('/file')) as stream:
        assert stream.read(6) == b'stream'
        assert stream.read() == b'ing'


def test_list_sorted_with_stats(tier: PosixTier) -> None:
    """Listings are name-sorted and carry full stats."""
    tier.mkdir(PurePosixPath('/sub'))
    put(tier, '/beta', b'bb')
    put(tier, '/alpha', b'a')
    listing = list(tier.list(PurePosixPath('/')))
    assert [(entry.name, entry.kind) for entry in listing] == [('alpha', 'file'), ('beta', 'file'), ('sub', 'dir')]
    assert [entry.stat.size for entry in listing if entry.kind == 'file'] == [1, 2]
    with raises(FileNotFoundError):
        list(tier.list(PurePosixPath('/missing')))
    with raises(NotADirectoryError):
        list(tier.list(PurePosixPath('/alpha')))


def test_symlinks_stat_and_list(tier: PosixTier) -> None:
    """Symlinks read as kind='symlink' with their target, never followed."""
    put(tier, '/real', b'data')
    os.symlink('real', tier.resolve('/link'))
    result = tier.stat(PurePosixPath('/link'))
    assert result is not None and (result.kind, result.target) == ('symlink', 'real')
    kinds = {entry.name: entry.kind for entry in tier.list(PurePosixPath('/'))}
    assert kinds == {'real': 'file', 'link': 'symlink'}


def test_special_files_invisible(tier: PosixTier) -> None:
    """Devices, sockets, and fifos are out of scope: absent from stat and listings."""
    os.mkfifo(tier.resolve('/pipe'))
    put(tier, '/file', b'data')
    assert tier.stat(PurePosixPath('/pipe')) is None
    assert [entry.name for entry in tier.list(PurePosixPath('/'))] == ['file']


def test_mkdir_rmdir(tier: PosixTier) -> None:
    """mkdir applies exact permission bits; rmdir requires empty; parents are not created."""
    tier.mkdir(PurePosixPath('/sub'), mode=0o750)
    result = tier.stat(PurePosixPath('/sub'))
    assert result is not None and (result.kind, result.mode) == ('dir', 0o750)
    with raises(FileNotFoundError):
        tier.mkdir(PurePosixPath('/a/b/c'))
    with raises(FileExistsError):
        tier.mkdir(PurePosixPath('/sub'))
    put(tier, '/sub/file', b'data')
    with raises(OSError):
        tier.rmdir(PurePosixPath('/sub'))
    tier.delete(PurePosixPath('/sub/file'))
    tier.rmdir(PurePosixPath('/sub'))
    assert tier.stat(PurePosixPath('/sub')) is None


def test_rename_replaces(tier: PosixTier) -> None:
    """rename moves atomically and replaces an existing file."""
    put(tier, '/old', b'payload')
    put(tier, '/existing', b'stale')
    tier.rename(PurePosixPath('/old'), PurePosixPath('/existing'))
    assert tier.stat(PurePosixPath('/old')) is None
    assert tier.read(PurePosixPath('/existing')) == b'payload'


def test_delete(tier: PosixTier) -> None:
    """delete removes files; missing raises FileNotFoundError."""
    put(tier, '/file', b'data')
    tier.delete(PurePosixPath('/file'))
    assert tier.stat(PurePosixPath('/file')) is None
    with raises(FileNotFoundError):
        tier.delete(PurePosixPath('/file'))


def test_usage_excludes_internals(tier: PosixTier) -> None:
    """usage sums regular files and symlinks, blind to the '.sosfs' directory."""
    Marker(uuid=UUID, tier='scratch', generation=0, written=100.0).write(tier.root)
    put(tier, '/a', b'12345')
    tier.mkdir(PurePosixPath('/sub'))
    put(tier, '/sub/b', b'123')
    os.symlink('a', tier.resolve('/link'))
    usage = tier.usage()
    assert usage.files == 3 and usage.bytes == 8 + os.lstat(tier.resolve('/link')).st_size


def test_managed_internals_hidden_and_guarded(tier: PosixTier) -> None:
    """'.sosfs' at the root reads as absent, is excluded from listings, and refuses mutation."""
    Marker(uuid=UUID, tier='scratch', generation=0, written=100.0).write(tier.root)
    assert tier.stat(PurePosixPath('/.sosfs')) is None
    assert tier.stat(PurePosixPath('/.sosfs/marker')) is None
    assert [entry.name for entry in tier.list(PurePosixPath('/'))] == []
    for operation in (lambda: put(tier, '/.sosfs/evil', b'x'),
                      lambda: tier.delete(PurePosixPath('/.sosfs/marker')),
                      lambda: tier.mkdir(PurePosixPath('/.sosfs/sub')),
                      lambda: tier.rmdir(PurePosixPath('/.sosfs')),
                      lambda: tier.rename(PurePosixPath('/.sosfs/marker'), PurePosixPath('/stolen')),
                      lambda: tier.rename(PurePosixPath('/file'), PurePosixPath('/.sosfs/marker'))):
        with raises(PermissionError):
            operation()


def test_set_meta_changes_mode_and_mtime(tier: PosixTier) -> None:
    """set_meta applies mode and mtime to an existing file without touching its content."""
    put(tier, '/f', b'payload', mode=0o644, mtime=100.0)
    tier.set_meta(PurePosixPath('/f'), FileMeta(size=0, mtime=4242.0, mode=0o600))
    result = tier.stat(PurePosixPath('/f'))
    assert (result.mode, result.mtime) == (0o600, 4242.0)
    assert tier.read(PurePosixPath('/f')) == b'payload'      # content untouched


def test_set_meta_mode_none_leaves_mode(tier: PosixTier) -> None:
    """A None mode leaves the permission bits alone (only mtime moves)."""
    put(tier, '/f', b'x', mode=0o640, mtime=100.0)
    tier.set_meta(PurePosixPath('/f'), FileMeta(size=0, mtime=200.0, mode=None))
    result = tier.stat(PurePosixPath('/f'))
    assert (result.mode, result.mtime) == (0o640, 200.0)


def test_set_meta_missing_raises(tier: PosixTier) -> None:
    """set_meta on an absent path raises the natural FileNotFoundError."""
    with raises(FileNotFoundError):
        tier.set_meta(PurePosixPath('/nope'), FileMeta(size=0, mtime=1.0, mode=0o644))


def test_set_meta_refuses_internal(tier: PosixTier) -> None:
    """The managed '.sosfs' tree refuses metadata mutation like every other write."""
    with raises(PermissionError):
        tier.set_meta(PurePosixPath('/.sosfs/marker'), FileMeta(size=0, mtime=1.0, mode=0o600))


def test_escape_refused(tier: PosixTier) -> None:
    """Parent-directory components never resolve outside the root."""
    with raises(ValueError):
        tier.stat(PurePosixPath('/../outside'))
    with raises(ValueError):
        tier.resolve('/a/../../outside')


def test_open_direct_passthrough(tier: PosixTier) -> None:
    """open_direct hands out a real OS fd for hot I/O on a resident copy."""
    put(tier, '/hot', b'0000')
    fd = tier.open_direct(PurePosixPath('/hot'), os.O_RDWR)
    try:
        assert os.pread(fd, 4, 0) == b'0000'
        os.pwrite(fd, b'11', 1)
    finally:
        os.close(fd)
    assert tier.read(PurePosixPath('/hot')) == b'0110'
    fd = tier.open_direct(PurePosixPath('/new'), os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    assert stat.S_IMODE(os.lstat(tier.resolve('/new')).st_mode) == 0o600
