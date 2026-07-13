# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the ArchiveTier read skeleton: ZIP/TAR indexing cached in archive_index."""

# Type annotations
from __future__ import annotations
from typing import Iterator, Optional

# Standard libs
import io
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath as P

# External libs
from pytest import fixture, mark, raises, approx

# Internal libs
from sosfs.state.db import Database
from sosfs.state.registry import TierRegistry
from sosfs.tier.protocol import Tier, FileMeta, TierUsage
from sosfs.tier.archive import ArchiveTier, PosixInnerLocation, _zip_mtime


pytestmark = mark.unit

ZIP_DATE = (2026, 1, 2, 3, 4, 5)


@fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """An initialized state database."""
    database = Database(tmp_path / 'state.db')
    database.initialize()
    yield database
    database.close()


def write_zip(path: Path, members: list[tuple[str, Optional[bytes], int]]) -> None:
    """Build a ZIP from (name, data, mode) triples; data None makes a directory entry (name ends '/')."""
    with zipfile.ZipFile(path, mode='w') as archive:
        for name, data, mode in members:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.external_attr = mode << 16
            archive.writestr(info, b'' if data is None else data)


def write_tar(path: Path, members: list[tuple], *, compress: bool = False) -> None:
    """Build a TAR from member specs: ('file', name, data, mode) / ('dir', name) / ('symlink', name, target)."""
    with tarfile.open(path, mode='w:gz' if compress else 'w') as archive:
        for spec in members:
            if spec[0] == 'file':
                _, name, data, mode = spec
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), mode, 111.0
                archive.addfile(info, io.BytesIO(data))
            elif spec[0] == 'dir':
                info = tarfile.TarInfo(spec[1])
                info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, 111.0
                archive.addfile(info)
            else:
                info = tarfile.TarInfo(spec[1])
                info.type, info.linkname, info.mtime = tarfile.SYMTYPE, spec[2], 111.0
                archive.addfile(info)


def tier(db: Database, path: Path, fmt: str, name: str = 'arc') -> ArchiveTier:
    """An ArchiveTier over a POSIX inner location; the tier is registered so the index cache's FK holds."""
    TierRegistry(db).register(name, position=0, type=fmt, role='source', scope='shared')
    return ArchiveTier(name, PosixInnerLocation(path), db, fmt)


def test_satisfies_protocol(db: Database, tmp_path: Path) -> None:
    """ArchiveTier implements the full Tier contract (methods are present; writes are stubs for now)."""
    write_zip(tmp_path / 'a.zip', [('f', b'x', 0o644)])
    assert isinstance(tier(db, tmp_path / 'a.zip', 'zip'), Tier)


def test_zip_index_stat_list_read(db: Database, tmp_path: Path) -> None:
    """A ZIP is indexed from its central directory: stat, list, and whole-file reads all work."""
    write_zip(tmp_path / 'a.zip', [('top.txt', b'top-level', 0o644),
                                   ('data/', None, 0o755),
                                   ('data/a.txt', b'alpha', 0o640),
                                   ('data/b.txt', b'beta-content', 0o644)])
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    top = arc.stat(P('/top.txt'))
    assert (top.kind, top.size, top.mode) == ('file', 9, 0o644)
    assert top.mtime == approx(_zip_mtime(ZIP_DATE))
    assert arc.stat(P('/data')).kind == 'dir'
    assert [(e.name, e.kind) for e in arc.list(P('/data'))] == [('a.txt', 'file'), ('b.txt', 'file')]
    assert arc.stat(P('/data/a.txt')).mode == 0o640
    assert arc.read(P('/data/a.txt')) == b'alpha'
    assert arc.read(P('/data/b.txt'), offset=5) == b'content'
    assert arc.read(P('/data/b.txt'), offset=0, size=4) == b'beta'


def test_zip_offset_is_local_header(db: Database, tmp_path: Path) -> None:
    """The cached offset for a ZIP member is its local-header offset; the first entry sits at zero."""
    write_zip(tmp_path / 'a.zip', [('first', b'1', 0o644), ('second', b'2', 0o644)])
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    arc.index()
    rows = {row['member']: row['offset'] for row in
            db.execute(t'SELECT member, offset FROM archive_index WHERE tier = {"arc"}')}
    assert rows['/first'] == 0
    assert rows['/second'] > 0


def test_tar_index_and_read(db: Database, tmp_path: Path) -> None:
    """An uncompressed TAR is indexed by a single sequential scan; members read back."""
    write_tar(tmp_path / 'a.tar', [('dir', 'data'),
                                   ('file', 'data/a.txt', b'alpha', 0o640),
                                   ('file', 'top.txt', b'top', 0o644)])
    arc = tier(db, tmp_path / 'a.tar', 'tar')
    assert arc.stat(P('/data')).kind == 'dir'
    hit = arc.stat(P('/data/a.txt'))
    assert (hit.kind, hit.size, hit.mode, hit.mtime) == ('file', 5, 0o640, 111.0)
    assert arc.read(P('/data/a.txt')) == b'alpha'
    assert arc.read(P('/top.txt')) == b'top'


def test_tar_offset_is_header_block(db: Database, tmp_path: Path) -> None:
    """The cached offset for a TAR member is its header-block offset; the first member sits at zero."""
    write_tar(tmp_path / 'a.tar', [('file', 'first', b'1', 0o644), ('file', 'second', b'2', 0o644)])
    arc = tier(db, tmp_path / 'a.tar', 'tar')
    arc.index()
    rows = {row['member']: row['offset'] for row in
            db.execute(t'SELECT member, offset FROM archive_index WHERE tier = {"arc"}')}
    assert rows['/first'] == 0
    assert rows['/second'] > 0


def test_targz_sequential_read(db: Database, tmp_path: Path) -> None:
    """A tar.gz indexes and reads (sequential decompress); the format flag drives the codec."""
    write_tar(tmp_path / 'a.tar.gz', [('file', 'a.txt', b'compressed payload', 0o644)], compress=True)
    arc = tier(db, tmp_path / 'a.tar.gz', 'tar.gz')
    assert arc.read(P('/a.txt')) == b'compressed payload'
    assert arc.stat(P('/a.txt')).size == len(b'compressed payload')


def test_synthetic_ancestor_dirs(db: Database, tmp_path: Path) -> None:
    """Ancestor directories the archive omitted are synthesized so stat/list see a complete tree."""
    write_zip(tmp_path / 'a.zip', [('deep/nested/leaf.txt', b'x', 0o644)])   # no 'deep/' or 'deep/nested/' entries
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    assert arc.stat(P('/deep')).kind == 'dir'
    assert arc.stat(P('/deep/nested')).kind == 'dir'
    assert [e.name for e in arc.list(P('/'))] == ['deep']
    assert [e.name for e in arc.list(P('/deep/nested'))] == ['leaf.txt']


def test_tar_symlink_member(db: Database, tmp_path: Path) -> None:
    """A TAR symlink indexes as a symlink with its target exposed on the stat."""
    write_tar(tmp_path / 'a.tar', [('file', 'top.txt', b'top', 0o644), ('symlink', 'link', 'top.txt')])
    arc = tier(db, tmp_path / 'a.tar', 'tar')
    link = arc.stat(P('/link'))
    assert (link.kind, link.target) == ('symlink', 'top.txt')
    with raises(OSError):
        arc.read(P('/link'))


def test_zip_symlink_member(db: Database, tmp_path: Path) -> None:
    """A ZIP symlink (S_IFLNK in the external attr; content is the target) indexes as a symlink."""
    write_zip(tmp_path / 'a.zip', [('top.txt', b'top', 0o644),
                                   ('link', b'top.txt', stat.S_IFLNK | 0o777)])
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    link = arc.stat(P('/link'))
    assert (link.kind, link.target) == ('symlink', 'top.txt')


def test_usage_counts_files_only(db: Database, tmp_path: Path) -> None:
    """usage sums file member sizes and counts, ignoring directories."""
    write_tar(tmp_path / 'a.tar', [('dir', 'd'), ('file', 'd/a', b'12345', 0o644), ('file', 'b', b'123', 0o644)])
    assert tier(db, tmp_path / 'a.tar', 'tar').usage() == TierUsage(bytes=8, files=2)


def test_index_is_cached_and_reindex_refreshes(db: Database, tmp_path: Path) -> None:
    """The index persists to archive_index; a fresh tier reuses the cache until reindex rescans."""
    path = tmp_path / 'a.zip'
    write_zip(path, [('original.txt', b'v1', 0o644)])
    tier(db, path, 'zip').index()
    assert [row['member'] for row in db.execute(t'SELECT member FROM archive_index WHERE tier = {"arc"}')] \
        == ['/original.txt']

    write_zip(path, [('replaced.txt', b'v2', 0o644)])            # the backing archive changes out from under us
    reused = tier(db, path, 'zip')                               # a fresh instance loads from the cache, no rescan
    assert reused.stat(P('/original.txt')).kind == 'file'
    assert reused.stat(P('/replaced.txt')) is None

    reused.reindex()                                             # explicit rebuild picks up the new archive
    assert reused.stat(P('/original.txt')) is None
    assert reused.stat(P('/replaced.txt')).kind == 'file'


def test_missing_archive_indexes_empty(db: Database, tmp_path: Path) -> None:
    """An absent inner archive indexes as empty rather than raising — the write items create it."""
    arc = tier(db, tmp_path / 'absent.zip', 'zip')
    assert arc.stat(P('/')).kind == 'dir'
    assert list(arc.list(P('/'))) == []


def test_managed_internals_hidden(db: Database, tmp_path: Path) -> None:
    """A '.sosfs' member inside the archive is excluded from the index, stats, and listings."""
    write_zip(tmp_path / 'a.zip', [('.sosfs/marker', b'stowaway', 0o644), ('visible', b'x', 0o644)])
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    assert arc.stat(P('/.sosfs')) is None
    assert arc.stat(P('/.sosfs/marker')) is None
    assert [e.name for e in arc.list(P('/'))] == ['visible']


def test_list_and_read_error_mapping(db: Database, tmp_path: Path) -> None:
    """Missing paths, non-directories, and directory reads map to the natural OSError subclasses."""
    write_zip(tmp_path / 'a.zip', [('data/a.txt', b'alpha', 0o644)])
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    with raises(FileNotFoundError):
        list(arc.list(P('/missing')))
    with raises(NotADirectoryError):
        list(arc.list(P('/data/a.txt')))
    with raises(FileNotFoundError):
        arc.read(P('/missing'))
    with raises(IsADirectoryError):
        arc.read(P('/data'))


def test_escape_refused(db: Database, tmp_path: Path) -> None:
    """Parent-directory components never resolve outside the tier."""
    write_zip(tmp_path / 'a.zip', [('f', b'x', 0o644)])
    with raises(ValueError):
        tier(db, tmp_path / 'a.zip', 'zip').stat(P('/../outside'))


def test_bad_format_rejected(db: Database, tmp_path: Path) -> None:
    """An unknown archive format is refused at construction."""
    with raises(ValueError):
        ArchiveTier('arc', PosixInnerLocation(tmp_path / 'x'), db, 'rar')


def put(arc: ArchiveTier, path: str, data: bytes, *, mode: int = 0o644, mtime: float = 200.0) -> None:
    """Store `data` at `path` through the direct-update write path."""
    arc.put(P(path), io.BytesIO(data), meta=FileMeta(size=len(data), mtime=mtime, mode=mode))


def raw_names(path: Path, fmt: str) -> list[str]:
    """The member names physically present in the archive (to prove compaction has no duplicates)."""
    if fmt == 'zip':
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, mode='r:') as archive:
        return archive.getnames()


def test_put_creates_archive_and_reads_back(db: Database, tmp_path: Path) -> None:
    """A put against a non-existent archive creates it atomically; content and metadata read back."""
    path = tmp_path / 'a.zip'
    arc = tier(db, path, 'zip')
    put(arc, '/data/a.txt', b'alpha', mode=0o640, mtime=200.0)
    assert path.exists()
    assert arc.read(P('/data/a.txt')) == b'alpha'
    assert arc.stat(P('/data/a.txt')).mode == 0o640
    assert arc.stat(P('/data')).kind == 'dir'            # ancestor synthesized after the re-pack
    assert arc.usage() == TierUsage(bytes=5, files=1)


def test_put_replaces_and_stays_compact(db: Database, tmp_path: Path) -> None:
    """Re-putting a member supersedes it with no physical duplicate — the re-pack is implicitly compact."""
    path = tmp_path / 'a.zip'
    arc = tier(db, path, 'zip')
    put(arc, '/f', b'v1')
    put(arc, '/f', b'v2-longer')
    assert arc.read(P('/f')) == b'v2-longer'
    assert raw_names(path, 'zip') == ['f']               # exactly one physical entry, not two
    assert arc.usage() == TierUsage(bytes=9, files=1)


def test_put_leaves_no_temp_and_survives_new_instance(db: Database, tmp_path: Path) -> None:
    """A committed put leaves no orphan temp; a fresh tier loads the updated index from the cache."""
    path = tmp_path / 'a.zip'
    put(tier(db, path, 'zip'), '/keep.txt', b'kept')
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith('.tmp')] == []  # no orphan temp left behind
    assert tier(db, path, 'zip').read(P('/keep.txt')) == b'kept'


def test_delete_removes_and_is_idempotent(db: Database, tmp_path: Path) -> None:
    """delete drops the member from the archive; re-deleting an absent one is a no-op; a dir raises."""
    path = tmp_path / 'a.zip'
    arc = tier(db, path, 'zip')
    put(arc, '/a', b'1')
    put(arc, '/b', b'2')
    arc.delete(P('/a'))
    assert arc.stat(P('/a')) is None
    assert raw_names(path, 'zip') == ['b']
    arc.delete(P('/a'))                                  # idempotent
    arc.mkdir(P('/d'))
    with raises(IsADirectoryError):
        arc.delete(P('/d'))


def test_mkdir_and_rmdir(db: Database, tmp_path: Path) -> None:
    """mkdir needs an existing parent and a free name; rmdir needs an existing, empty directory."""
    path = tmp_path / 'a.tar'
    arc = tier(db, path, 'tar')
    arc.mkdir(P('/sub'))
    assert arc.stat(P('/sub')).kind == 'dir'
    with raises(FileNotFoundError):
        arc.mkdir(P('/missing/child'))
    with raises(FileExistsError):
        arc.mkdir(P('/sub'))
    put(arc, '/sub/file', b'data')
    with raises(OSError):
        arc.rmdir(P('/sub'))                            # not empty
    arc.delete(P('/sub/file'))
    arc.rmdir(P('/sub'))
    assert arc.stat(P('/sub')) is None


def test_rename_file_and_subtree(db: Database, tmp_path: Path) -> None:
    """rename moves a file (replacing any target) and moves a directory's whole subtree."""
    path = tmp_path / 'a.zip'
    arc = tier(db, path, 'zip')
    put(arc, '/old', b'payload')
    put(arc, '/existing', b'stale')
    arc.rename(P('/old'), P('/existing'))
    assert arc.stat(P('/old')) is None
    assert arc.read(P('/existing')) == b'payload'

    arc.mkdir(P('/src'))
    put(arc, '/src/a', b'1')
    put(arc, '/src/deep/b', b'2')
    arc.rename(P('/src'), P('/dst'))
    assert arc.stat(P('/src')) is None
    assert arc.read(P('/dst/a')) == b'1'
    assert arc.read(P('/dst/deep/b')) == b'2'
    with raises(OSError):
        arc.rename(P('/dst'), P('/dst/inside'))          # into own subtree


def test_set_meta_preserves_content(db: Database, tmp_path: Path) -> None:
    """set_meta rewrites mode/mtime while preserving bytes; a missing member raises."""
    path = tmp_path / 'a.tar'
    arc = tier(db, path, 'tar')
    put(arc, '/f', b'payload', mode=0o644, mtime=200.0)
    arc.set_meta(P('/f'), FileMeta(size=0, mtime=4242.0, mode=0o600))
    assert arc.read(P('/f')) == b'payload'
    hit = arc.stat(P('/f'))
    assert (hit.mode, hit.mtime) == (0o600, 4242.0)
    with raises(FileNotFoundError):
        arc.set_meta(P('/nope'), FileMeta(size=0, mtime=1.0, mode=0o644))


def test_tar_direct_update_round_trip(db: Database, tmp_path: Path) -> None:
    """Uncompressed TAR is a direct-update tier: put/replace/delete round-trip through the archive."""
    path = tmp_path / 'a.tar'
    arc = tier(db, path, 'tar')
    put(arc, '/x', b'one')
    put(arc, '/y', b'two')
    put(arc, '/x', b'one-updated')
    arc.delete(P('/y'))
    assert arc.read(P('/x')) == b'one-updated'
    assert arc.stat(P('/y')) is None
    assert raw_names(path, 'tar') == ['x']


def test_targz_writes_are_deferred(db: Database, tmp_path: Path) -> None:
    """tar.gz is staged re-sync, not direct-update — its mutating ops raise until that item lands."""
    write_tar(tmp_path / 'a.tar.gz', [('file', 'f', b'x', 0o644)], compress=True)
    arc = tier(db, tmp_path / 'a.tar.gz', 'tar.gz')
    assert arc.read(P('/f')) == b'x'                    # reads still work
    for operation in (lambda: put(arc, '/g', b'y'),
                      lambda: arc.delete(P('/f')),
                      lambda: arc.mkdir(P('/d')),
                      lambda: arc.rename(P('/f'), P('/g'))):
        with raises(NotImplementedError):
            operation()


def test_write_refuses_internal(db: Database, tmp_path: Path) -> None:
    """The managed '.sosfs' tree refuses mutation like every other tier."""
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    put(arc, '/visible', b'x')
    with raises(PermissionError):
        put(arc, '/.sosfs/evil', b'x')
    with raises(PermissionError):
        arc.rename(P('/visible'), P('/.sosfs/marker'))
