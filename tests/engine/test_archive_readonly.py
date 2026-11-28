# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Engine suite: every call that would change an entry over an archive source fails EROFS, and the archive stays put."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Iterator, List, Set, Tuple

# Standard libs
import io
import os
import gzip
import stat
import errno
import hashlib
import tarfile
import zipfile
from pathlib import Path

# External libs
from pytest import fixture, mark, raises

# Internal libs
from terrace.state.journal import Writeback
from terrace.tier.spec import OverlaySpec, TierSpec, LocationSpec, WritebackSpec
from terrace.engine.overlay import OverlayEngine, Handle
from terrace.engine.writeback import WritebackScheduler
from terrace.engine.drain import drain, dirty_copies
from tests.engine.conftest import Clock


pytestmark = mark.unit

FORMATS = ['zip', 'tar', 'tar.gz']
ZIP_DATE = (2026, 1, 2, 3, 4, 5)
FILES: Dict[str, bytes] = {
    'top.txt': b'top-level',
    'data/a.txt': b'member a',
    'data/b.txt': b'member b',
}
DIRECTORIES = ['data', 'empty']                     # `empty` has no members, so a writable engine could remove it


def build_archive(directory: Path, fmt: str) -> Path:
    """The fixture archive: three files and two directories with headers, one of them empty."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'space.{fmt}'
    if fmt == 'zip':
        with zipfile.ZipFile(path, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in DIRECTORIES:
                info = zipfile.ZipInfo(name + '/', date_time=ZIP_DATE)
                info.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
                archive.writestr(info, b'')
            for name, data in FILES.items():
                info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    else:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            for name in DIRECTORIES:
                info = tarfile.TarInfo(name)
                info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, 111.0
                archive.addfile(info)
            for name, data in FILES.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), 0o644, 111.0
                archive.addfile(info, io.BytesIO(data))
        content = buffer.getvalue()
        path.write_bytes(gzip.compress(content, mtime=0) if fmt == 'tar.gz' else content)
    return path


def archive_spec(tmp_path: Path, archive: Path, fmt: str) -> OverlaySpec:
    """An archive source over a POSIX location, above one shared POSIX stage tier."""
    location = LocationSpec(type='posix', path=str(archive))
    source = TierSpec(name='archive', type=fmt, path=str(archive), location=location, role='source')
    scratch = TierSpec(name='scratch', type='posix', path=str(tmp_path / 'scratch'), role='stage',
                       scope='shared', writeback=WritebackSpec())
    return OverlaySpec(name='archived', state=tmp_path / 'state', tiers=(source, scratch))


def snapshot(archive: Path) -> Tuple[str, int, int, int, Set[str]]:
    """The archive's sha256, size, mtime and inode, and its directory's listing."""
    info = archive.stat()
    return (hashlib.sha256(archive.read_bytes()).hexdigest(), info.st_size, info.st_mtime_ns, info.st_ino,
            {path.name for path in archive.parent.iterdir()})


def read_all(engine: OverlayEngine, path: str) -> bytes:
    """Open read-only, read the whole file, and release."""
    handle = engine.open(path, os.O_RDONLY)
    try:
        return engine.read(handle, engine.getattr(path).size + 1, 0)
    finally:
        engine.release(handle)


def reader(engine: OverlayEngine, held: List[Handle], path: str) -> Handle:
    """A read-only handle on `path`, released by the caller."""
    handle = engine.open(path, os.O_RDONLY)
    held.append(handle)
    return handle


def opener(flags: int) -> Callable[[OverlayEngine, List[Handle]], object]:
    """An open of `/top.txt` with `flags`, its handle (if any) released by the caller."""
    return lambda engine, held: held.append(engine.open('/top.txt', flags))


# Every refused call, with the path its error names. Each would succeed, or fail with another errno, on a writable
# source: `rename-over` reaches the source's delete synchronously, and a write or cut through a read-only handle
# would mark the copy dirty before the OS refused it.
CALLS: Dict[str, Tuple[str, Callable[[OverlayEngine, List[Handle]], object]]] = {
    'mkdir': ('/new', lambda engine, held: engine.mkdir('/new', 0o755)),
    'create': ('/new.txt', lambda engine, held: engine.create('/new.txt', 0o644)),
    'unlink': ('/top.txt', lambda engine, held: engine.unlink('/top.txt')),
    'rmdir': ('/empty', lambda engine, held: engine.rmdir('/empty')),
    'rename': ('/top.txt', lambda engine, held: engine.rename('/top.txt', '/moved.txt')),
    'rename-over': ('/data/a.txt', lambda engine, held: engine.rename('/data/a.txt', '/top.txt')),
    'chmod': ('/top.txt', lambda engine, held: engine.chmod('/top.txt', 0o600)),
    'chown': ('/top.txt', lambda engine, held: engine.chown('/top.txt', os.getuid(), os.getgid())),
    'utimens': ('/top.txt', lambda engine, held: engine.utimens('/top.txt', 1.0, 2.0)),
    'truncate': ('/top.txt', lambda engine, held: engine.truncate('/top.txt', 0)),
    'open-wronly': ('/top.txt', opener(os.O_WRONLY)),
    'open-rdwr': ('/top.txt', opener(os.O_RDWR)),
    'open-rdonly-trunc': ('/top.txt', opener(os.O_RDONLY | os.O_TRUNC)),
    'open-wronly-append': ('/top.txt', opener(os.O_WRONLY | os.O_APPEND)),
    'write-rdonly-handle': ('/data/a.txt', lambda engine, held: engine.write(reader(engine, held, '/data/a.txt'),
                                                                             b'x', 0)),
    'truncate-handle': ('/data/a.txt', lambda engine, held: engine.truncate('/data/a.txt', 0,
                                                                            handle=reader(engine, held, '/data/a.txt'))),
}
THROUGH_HANDLE = {'write-rdonly-handle', 'truncate-handle'}       # these open read-only first, which promotes

# The public engine API, by whether a call changes an entry's name, bytes or metadata. The calls left out of the
# mutating set act only on admitted writes (commit, flush, eviction), or close and record a handle; with every write
# refused they have nothing to act on, and the drain test below runs them.
MUTATING = {'mkdir', 'create', 'unlink', 'rmdir', 'rename', 'chmod', 'chown', 'utimens', 'open', 'write', 'truncate'}
NON_MUTATING = {'start', 'stop', 'source', 'upstream_of', 'eligible_tier', 'getattr', 'residencies', 'readdir',
                'readlink', 'read', 'fsync', 'release', 'commit_row', 'commit_upstream', 'commit_to_shared', 'flush',
                'evict_copy'}


@fixture
def stopped() -> Iterator[list]:
    """Engines a test starts, stopped at teardown if still running."""
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


def refused(engine: OverlayEngine, call: str) -> None:
    """Make `call` and require EROFS naming its path; any handle it opened or used is released."""
    path, make = CALLS[call]
    held: List[Handle] = []
    try:
        with raises(OSError) as caught:
            make(engine, held)
    finally:
        for handle in held:
            engine.release(handle)
    assert caught.value.errno == errno.EROFS, f'{call}: {caught.value!r}'
    assert caught.value.filename == path


def dirty_residencies(engine: OverlayEngine) -> int:
    return engine.db.execute(t"SELECT COUNT(*) FROM residency WHERE state = 'dirty'").fetchone()[0]


@mark.parametrize('call', list(CALLS))
@mark.parametrize('fmt', FORMATS)
def test_mutating_call_fails_erofs(tmp_path: Path, stopped: list, fmt: str, call: str) -> None:
    """
    Each call that would change an entry fails EROFS and leaves nothing queued and nothing dirty. A call made by path
    is refused before its lookup, so it promotes nothing to the stage either.
    """
    archive = build_archive(tmp_path / 'data', fmt)
    engine = started(archive_spec(tmp_path, archive, fmt), stopped)
    assert engine.read_only
    assert engine.getattr('/empty').kind == 'dir' and engine.readdir('/empty') == []
    refused(engine, call)
    assert engine.journal.queued() == []
    assert dirty_residencies(engine) == 0
    if call not in THROUGH_HANDLE:
        staged = [name for name in FILES if any(copy.tier == 'scratch' for copy in engine.residencies('/' + name))]
        assert staged == []


@mark.parametrize('fmt', FORMATS)
def test_archive_unchanged_after_refusals_and_drain(tmp_path: Path, stopped: list, fmt: str) -> None:
    """
    After every refused call, the pumps and the calls left unguarded (fsync, release, the commits, a flush, a
    scheduler poll, a drain, eviction), a stop and a restart, the archive's bytes, inode and directory are as built.
    """
    archive = build_archive(tmp_path / 'data', fmt)
    before = snapshot(archive)
    spec = archive_spec(tmp_path, archive, fmt)
    engine = started(spec, stopped)
    for call in CALLS:
        refused(engine, call)
    assert engine.journal.queued() == []
    assert dirty_residencies(engine) == 0
    assert dirty_copies(engine) == []
    for name, data in FILES.items():
        assert read_all(engine, '/' + name) == data
    handle = engine.open('/top.txt', os.O_RDONLY)
    engine.fsync(handle)
    engine.release(handle)
    for name in FILES:
        engine.commit_upstream('/' + name)
        assert engine.commit_to_shared('/' + name) is False
    moot = Writeback(id=0, entry=-1, op='data', from_tier='scratch', to_tier='archive', detail=None,
                     state='queued', first_dirty=0.0, last_write=0.0, last_commit=None)
    engine.commit_row(moot)                         # the queue holds no row; one naming no entry settles as moot
    engine.flush()
    assert WritebackScheduler(engine).poll(now=1e12) == 0
    drain(engine)
    assert engine.journal.queued() == []
    assert engine.evict_copy('/top.txt', 'scratch') is True
    assert engine.evict_copy('/top.txt', 'archive') is False
    engine.stop()
    again = started(spec, stopped)
    assert again.read_only
    assert read_all(again, '/data/b.txt') == b'member b'
    again.stop()
    assert snapshot(archive) == before
    assert before[4] == {archive.name}


def test_every_public_engine_method_is_classified() -> None:
    """A new public engine call fails here until it is classified, and so until the read-only rule is decided for it."""
    public = {name for name in vars(OverlayEngine) if not name.startswith('_')}
    assert not MUTATING & NON_MUTATING
    assert MUTATING | NON_MUTATING == public
