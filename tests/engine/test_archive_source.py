# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Engine suite: an archive source (ZIP, TAR) built from a hand-made spec, registered with no marker and read."""

# Type annotations
from __future__ import annotations
from typing import Dict, Iterator, Set, Tuple

# Standard libs
import io
import os
import stat
import shutil
import hashlib
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

# External libs
from pytest import fixture, mark, raises

# Internal libs
from terrace.state.registry import SpecDrift
from terrace.tier.archive import ArchiveTier, PosixInnerLocation
from terrace.tier.spec import OverlaySpec, TierSpec, LocationSpec, WritebackSpec
from terrace.engine.overlay import OverlayEngine, EngineError
from tests.engine.conftest import Clock


pytestmark = mark.unit

FORMATS = ['zip', 'tar']
ZIP_DATE = (2026, 1, 2, 3, 4, 5)
BIG = bytes(range(256)) * 4096 + b'past one mebibyte'                    # a member of more than 1 MiB
FILES: Dict[str, bytes] = {
    'top.txt': b'top-level',
    'empty.txt': b'',
    'headed/two.txt': b'depth two',
    'headed/big.bin': BIG,
    'bare/deep/three.txt': b'depth three',
}


def build_archive(directory: Path, fmt: str) -> Path:
    """
    The fixture archive. `headed/` is stored with a header and `bare/` and `bare/deep/` without one. The ZIP's
    one directory entry carries no unix mode, so none of its directories has a mode; the TAR adds a symlink.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'space.{fmt}'
    if fmt == 'zip':
        with zipfile.ZipFile(path, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo('headed/', date_time=ZIP_DATE)
            info.external_attr = 0x10                                     # the MS-DOS directory bit only
            archive.writestr(info, b'')
            for name, data in FILES.items():
                info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    else:
        with tarfile.open(path, mode='w') as archive:
            info = tarfile.TarInfo('headed')
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, 111.0
            archive.addfile(info)
            for name, data in FILES.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), 0o644, 111.0
                archive.addfile(info, io.BytesIO(data))
            info = tarfile.TarInfo('link')
            info.type, info.linkname, info.mtime = tarfile.SYMTYPE, 'top.txt', 111.0
            archive.addfile(info)
    return path


def archive_spec(tmp_path: Path, archive: Path, fmt: str) -> OverlaySpec:
    """An archive source over a POSIX location, above one shared POSIX stage tier."""
    location = LocationSpec(type='posix', path=str(archive))
    source = TierSpec(name='archive', type=fmt, path=str(archive), location=location, role='source')
    scratch = TierSpec(name='scratch', type='posix', path=str(tmp_path / 'scratch'), role='stage',
                       scope='shared', writeback=WritebackSpec())
    return OverlaySpec(name='archived', state=tmp_path / 'state', tiers=(source, scratch))


def expected_tree(archive: Path, fmt: str) -> Tuple[Dict[str, str], Dict[str, bytes]]:
    """Every member path with its kind, and every file's bytes, read with the stdlib (ancestors included)."""
    kinds: Dict[str, str] = {}
    data: Dict[str, bytes] = {}
    if fmt == 'zip':
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                name = '/' + info.filename.strip('/')
                kinds[name] = 'dir' if info.is_dir() else 'file'
                if not info.is_dir():
                    data[name] = handle.read(info)
    else:
        with tarfile.open(archive, mode='r:') as handle:
            for info in handle.getmembers():
                name = '/' + info.name.strip('/')
                kinds[name] = 'dir' if info.isdir() else 'symlink' if info.issym() else 'file'
                if info.isreg():
                    data[name] = handle.extractfile(info).read()
    for name in list(kinds):
        for parent in PurePosixPath(name).parents:
            if str(parent) != '/':
                kinds.setdefault(str(parent), 'dir')
    return kinds, data


def walk(engine: OverlayEngine, path: str = '/') -> Iterator[Tuple[str, str]]:
    """Every entry below `path`, by recursive `readdir`."""
    for entry in engine.readdir(path):
        yield entry.path, entry.kind
        if entry.kind == 'dir':
            yield from walk(engine, entry.path)


def read_all(engine: OverlayEngine, path: str) -> bytes:
    """Open, read the whole file, and release."""
    size = engine.getattr(path).size
    handle = engine.open(path, os.O_RDONLY)
    try:
        return engine.read(handle, size + 1, 0)
    finally:
        engine.release(handle)


def snapshot(archive: Path) -> Tuple[str, int, int, int, Set[str]]:
    """The archive's sha256, size, mtime and inode, and its directory's listing."""
    info = archive.stat()
    return (hashlib.sha256(archive.read_bytes()).hexdigest(), info.st_size, info.st_mtime_ns, info.st_ino,
            {path.name for path in archive.parent.iterdir()})


@fixture
def stopped() -> Iterator[list]:
    """Engines a test starts, stopped at teardown if still running (a refused start leaves its database open)."""
    engines: list = []
    yield engines
    for engine in engines:
        if engine.started:
            engine.stop()
        else:
            engine.db.close()


def started(spec: OverlaySpec, stopped: list) -> OverlayEngine:
    """A started engine over `spec`."""
    engine = OverlayEngine(spec, clock=Clock())
    stopped.append(engine)
    engine.start()
    return engine


@mark.parametrize('fmt', FORMATS)
def test_start_builds_an_archive_tier_over_the_location(tmp_path: Path, stopped: list, fmt: str) -> None:
    """The source is an ArchiveTier over the spec's location, registered by type with its identity recorded."""
    archive = build_archive(tmp_path / 'data', fmt)
    engine = started(archive_spec(tmp_path, archive, fmt), stopped)
    assert engine.started
    assert isinstance(engine.source, ArchiveTier)
    assert isinstance(engine.source.inner, PosixInnerLocation) and engine.source.inner.path == archive
    assert engine.source.fmt == fmt
    record = engine.registry.get('archive')
    assert (record.position, record.type, record.role, record.scope) == (0, fmt, 'source', 'shared')
    assert record.meta['location'] == str(archive)
    assert record.meta['signature'] == [archive.stat().st_size, archive.stat().st_mtime]
    assert engine.catalog.get('/').kind == 'dir'


@mark.parametrize('fmt', FORMATS)
def test_restart_reads_ok_and_writes_nothing_beside_the_archive(tmp_path: Path, stopped: list, fmt: str) -> None:
    """A start, a clean stop and a second start over one state directory all succeed; the archive is untouched."""
    archive = build_archive(tmp_path / 'data', fmt)
    before = snapshot(archive)
    spec = archive_spec(tmp_path, archive, fmt)
    first = started(spec, stopped)
    assert {name for name, _ in walk(first)} >= {'/top.txt', '/headed', '/bare'}
    first.stop()
    second = started(spec, stopped)
    assert second.started and isinstance(second.source, ArchiveTier)
    assert read_all(second, '/headed/two.txt') == b'depth two'
    second.stop()
    assert snapshot(archive) == before
    assert before[4] == {archive.name}


@mark.parametrize('fmt', FORMATS)
def test_listing_and_reads_match_the_archive(tmp_path: Path, stopped: list, fmt: str) -> None:
    """A recursive listing names every member; every file reads its archive bytes; promoted directories are 0o755."""
    archive = build_archive(tmp_path / 'data', fmt)
    kinds, data = expected_tree(archive, fmt)
    assert len(data) == len(FILES) and max(len(content) for content in data.values()) > 1 << 20
    engine = started(archive_spec(tmp_path, archive, fmt), stopped)
    assert dict(walk(engine)) == kinds
    for path, content in data.items():
        assert read_all(engine, path) == content
    if fmt == 'tar':
        assert engine.readlink('/link') == 'top.txt'
    scratch = tmp_path / 'scratch'
    for directory in ('headed', 'bare', 'bare/deep'):
        assert stat.S_IMODE((scratch / directory).stat().st_mode) == 0o755
        assert engine.getattr('/' + directory).mode == 0o755
    assert (scratch / 'bare' / 'deep' / 'three.txt').read_bytes() == b'depth three'


def test_missing_archive_is_refused_at_start(tmp_path: Path, stopped: list) -> None:
    """A spec naming an archive that does not exist is refused at start, and nothing is made at its location."""
    archive = tmp_path / 'data' / 'absent.zip'
    with raises(EngineError, match='Source archive does not exist: ') as caught:
        started(archive_spec(tmp_path, archive, 'zip'), stopped)
    assert str(archive) in str(caught.value)
    assert not archive.parent.exists()


def test_moved_location_is_refused_at_restart(tmp_path: Path, stopped: list) -> None:
    """The tier's name pointed at another archive file, even a byte-identical copy, is refused at restart."""
    archive = build_archive(tmp_path / 'data', 'zip')
    started(archive_spec(tmp_path, archive, 'zip'), stopped).stop()
    moved = tmp_path / 'elsewhere' / 'space.zip'
    moved.parent.mkdir()
    shutil.copy2(archive, moved)
    with raises(SpecDrift) as caught:
        started(archive_spec(tmp_path, moved, 'zip'), stopped)
    message = str(caught.value)
    assert "'archive'" in message and str(moved) in message
    assert 'changed since this state directory recorded it' in message


def test_rewritten_archive_is_refused_at_restart(tmp_path: Path, stopped: list) -> None:
    """The same archive path rewritten between mounts is refused; the message names the tier and the archive."""
    archive = build_archive(tmp_path / 'data', 'zip')
    spec = archive_spec(tmp_path, archive, 'zip')
    started(spec, stopped).stop()
    with zipfile.ZipFile(archive, mode='a') as handle:
        handle.writestr('added.txt', b'written out of band')
    with raises(SpecDrift) as caught:
        started(spec, stopped)
    message = str(caught.value)
    assert "'archive'" in message and str(archive) in message
    assert 'changed since this state directory recorded it' in message
