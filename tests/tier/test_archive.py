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
import warnings
from dataclasses import replace
from pathlib import Path, PurePosixPath as P

# External libs
from pytest import fixture, mark, raises, approx

# Internal libs
from terrace.core.units import parse_rate
from terrace.state.db import Database
from terrace.state.registry import TierRegistry
from terrace.tier.protocol import Tier, FileMeta, TierUsage
from terrace.tier.spec import S3Spec, LocationSpec
from terrace.tier.archive import (ArchiveTier, InnerLocation, PosixInnerLocation, S3InnerLocation,
                                inner_location, _zip_mtime)


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


def test_tar_offset_is_data_block(db: Database, tmp_path: Path) -> None:
    """The cached offset for a TAR member is its data-block offset (past the 512-byte header)."""
    write_tar(tmp_path / 'a.tar', [('file', 'first', b'1', 0o644), ('file', 'second', b'2', 0o644)])
    arc = tier(db, tmp_path / 'a.tar', 'tar')
    arc.index()
    rows = {row['member']: row['offset'] for row in
            db.execute(t'SELECT member, offset FROM archive_index WHERE tier = {"arc"}')}
    assert rows['/first'] >= 512                          # data follows the first member's (>=512-byte) header
    assert rows['/second'] > rows['/first']


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
    """A '.terrace' member inside the archive is excluded from the index, stats, and listings."""
    write_zip(tmp_path / 'a.zip', [('.terrace/marker', b'stowaway', 0o644), ('visible', b'x', 0o644)])
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    assert arc.stat(P('/.terrace')) is None
    assert arc.stat(P('/.terrace/marker')) is None
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


def staged(db: Database, source: Path, work: Path, *, max_frequency=None, name: str = 'arc') -> ArchiveTier:
    """A staged-resync (tar.gz) ArchiveTier over an S3-like inner source with a durable working `.tar`."""
    TierRegistry(db).register(name, position=0, type='tar.gz', role='source', scope='shared')
    return ArchiveTier(name, PosixInnerLocation(source), db, 'tar.gz', bundle=work, max_frequency=max_frequency)


def test_staged_requires_staging(db: Database, tmp_path: Path) -> None:
    """A tar.gz archive is staged re-sync and refuses construction without a bundle path."""
    with raises(ValueError, match='requires a `bundle` path'):
        ArchiveTier('arc', PosixInnerLocation(tmp_path / 'a.tar.gz'), db, 'tar.gz')


def test_staged_populate_and_offset_read(db: Database, tmp_path: Path) -> None:
    """First access pulls + decompresses the source into the staging `.tar`; members read back by offset."""
    source = tmp_path / 'src.tar.gz'
    work = tmp_path / 'work.tar'
    write_tar(source, [('file', 'a.txt', b'compressed payload', 0o644), ('dir', 'sub'),
                       ('file', 'sub/b.txt', b'nested', 0o640)], compress=True)
    arc = staged(db, source, work)
    assert arc.read(P('/a.txt')) == b'compressed payload'
    assert work.exists()                                 # decompressed working copy materialized on demand
    assert arc.read(P('/sub/b.txt')) == b'nested'
    assert arc.stat(P('/sub')).kind == 'dir'


def test_staged_write_defers_source_until_resync(db: Database, tmp_path: Path) -> None:
    """Writes land in the staging `.tar`; the compressed source is untouched until resync pushes it."""
    source = tmp_path / 'src.tar.gz'
    work = tmp_path / 'work.tar'
    write_tar(source, [('file', 'keep.txt', b'orig', 0o644)], compress=True)
    arc = staged(db, source, work)
    before = source.stat().st_mtime_ns
    put(arc, '/added.txt', b'new content')
    assert arc.read(P('/added.txt')) == b'new content'   # visible in the working copy immediately
    assert source.stat().st_mtime_ns == before           # source not rewritten by the write

    assert arc.resync(now=100.0) is True                 # explicit push reconstructs + recompresses + swaps
    with tarfile.open(source, mode='r:gz') as pushed:     # the source .tar.gz now carries both members
        assert set(pushed.getnames()) == {'added.txt', 'keep.txt'}
        assert pushed.extractfile('added.txt').read() == b'new content'
        assert pushed.extractfile('keep.txt').read() == b'orig'


def test_resync_rate_limited(db: Database, tmp_path: Path) -> None:
    """max_frequency bounds pushes: a second push inside the interval is skipped unless forced."""
    source = tmp_path / 'src.tar.gz'
    write_tar(source, [('file', 'f', b'x', 0o644)], compress=True)
    arc = staged(db, source, tmp_path / 'work.tar', max_frequency=parse_rate('1/60s'))
    assert arc.resync(now=100.0) is True                 # first push always allowed
    assert arc.resync(now=130.0) is False                # 30s < 60s min interval — rate-limited
    assert arc.resync(now=130.0, force=True) is True     # force bypasses the limiter (drain/commit)
    assert arc.resync(now=200.0) is True                 # 100s >= 60s since the forced push


def test_resync_noop_for_direct_update(db: Database, tmp_path: Path) -> None:
    """A direct-update tier has nothing to push — resync is a no-op."""
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    put(arc, '/f', b'x')
    assert arc.resync(now=100.0) is False


def test_write_refuses_internal(db: Database, tmp_path: Path) -> None:
    """The managed '.terrace' tree refuses mutation like every other tier."""
    arc = tier(db, tmp_path / 'a.zip', 'zip')
    put(arc, '/visible', b'x')
    with raises(PermissionError):
        put(arc, '/.terrace/evil', b'x')
    with raises(PermissionError):
        arc.rename(P('/visible'), P('/.terrace/marker'))


# -- S3 inner-location backing (moto): an archive source that lives on S3 ----------------------------------------

def s3_inner(s3_spec: S3Spec, key: str) -> S3InnerLocation:
    """An S3InnerLocation over the per-test moto bucket, addressing object `key`."""
    return S3InnerLocation.from_spec(replace(s3_spec, prefix=key))


def gzip_tar(members: list[tuple]) -> bytes:
    """Serialize a list of ('file', name, data) members into an in-memory .tar.gz blob."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for _, name, data in members:
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), 111.0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_s3_inner_satisfies_protocol(s3_spec: S3Spec) -> None:
    """S3InnerLocation implements the InnerLocation protocol."""
    assert isinstance(s3_inner(s3_spec, 'a.zip'), InnerLocation)


def test_s3_inner_absent_signature_is_none(s3_spec: S3Spec) -> None:
    """A missing object has no signature."""
    assert s3_inner(s3_spec, 'missing.tar').signature() is None


def test_s3_inner_write_read_and_replace(s3_spec: S3Spec) -> None:
    """atomic_write lands one PUT on clean exit; a second write replaces it whole."""
    inner = s3_inner(s3_spec, 'blob.bin')
    with inner.atomic_write() as out:
        out.write(b'archive bytes')
    assert inner.signature()[0] == len(b'archive bytes')
    with inner.open_read() as handle:
        assert handle.read() == b'archive bytes'
    with inner.atomic_write() as out:
        out.write(b'second, longer payload')
    with inner.open_read() as handle:
        assert handle.read() == b'second, longer payload'


def test_s3_inner_write_skips_put_on_error(s3_spec: S3Spec) -> None:
    """A body that raises leaves the object untouched — nothing is put on failure."""
    inner = s3_inner(s3_spec, 'blob.bin')
    with inner.atomic_write() as out:
        out.write(b'original')
    with raises(RuntimeError):
        with inner.atomic_write() as out:
            out.write(b'partial')
            raise RuntimeError('boom')
    with inner.open_read() as handle:
        assert handle.read() == b'original'


def test_zip_direct_update_over_s3(db: Database, s3_spec: S3Spec) -> None:
    """A ZIP whose backing object lives on S3 round-trips put/replace/delete through single-PUT re-packs."""
    TierRegistry(db).register('arc', position=0, type='zip', role='source', scope='shared')
    arc = ArchiveTier('arc', s3_inner(s3_spec, 'store.zip'), db, 'zip')
    put(arc, '/x', b'one')
    put(arc, '/y', b'two')
    put(arc, '/x', b'one-updated')
    arc.delete(P('/y'))
    assert arc.read(P('/x')) == b'one-updated'
    assert arc.stat(P('/y')) is None
    assert raw_names_s3(s3_inner(s3_spec, 'store.zip')) == ['x']    # re-pack stays compact on S3 too


def raw_names_s3(inner: S3InnerLocation) -> list[str]:
    """The ZIP member names physically present in the S3-backed archive object."""
    with inner.open_read() as handle:
        with zipfile.ZipFile(handle) as archive:
            return archive.namelist()


def test_staged_source_on_s3(db: Database, s3_spec: S3Spec, tmp_path: Path) -> None:
    """A tar.gz source on S3: populate pulls + decompresses to a local .tar; resync recompresses + pushes back."""
    source = s3_inner(s3_spec, 'space.tar.gz')
    with source.atomic_write() as out:
        out.write(gzip_tar([('file', 'keep.txt', b'orig')]))
    work = tmp_path / 'work.tar'
    TierRegistry(db).register('arc', position=0, type='tar.gz', role='source', scope='shared')
    arc = ArchiveTier('arc', source, db, 'tar.gz', bundle=work)
    assert arc.read(P('/keep.txt')) == b'orig'           # populate pulled + decompressed from S3
    assert work.exists()                                 # the working .tar materialized on the local durable tier

    put(arc, '/added.txt', b'new content')
    signature_before = source.signature()
    assert arc.read(P('/added.txt')) == b'new content'   # visible in the working copy immediately
    assert source.signature() == signature_before        # the S3 source is untouched until resync

    assert arc.resync(now=100.0) is True                 # recompress + push the reconstructed archive to S3
    with source.open_read() as pushed:
        with tarfile.open(fileobj=pushed, mode='r:gz') as archive:
            assert set(archive.getnames()) == {'added.txt', 'keep.txt'}
            assert archive.extractfile('added.txt').read() == b'new content'
            assert archive.extractfile('keep.txt').read() == b'orig'


def test_inner_location_factory_selects_backing(s3_spec: S3Spec, tmp_path: Path) -> None:
    """inner_location dispatches a parsed location spec to the right InnerLocation, both usable end-to-end."""
    posix = inner_location(LocationSpec(type='posix', path=str(tmp_path / 'a.zip')))
    assert isinstance(posix, PosixInnerLocation)

    location = LocationSpec.from_data(
        {'type': 's3', 'url': f's3://{s3_spec.bucket}/space.tar.gz',
         's3': {'region': s3_spec.region, 'endpoint': s3_spec.endpoint,
                'access_key_id': 'test', 'secret_access_key': 'test', 'allow_http': True}},
        'tier "cold"')
    cloud = inner_location(location)
    assert isinstance(cloud, S3InnerLocation)
    with cloud.atomic_write() as out:
        out.write(b'hello from the factory')
    with cloud.open_read() as handle:
        assert handle.read() == b'hello from the factory'


# -- member edge cases the engine serves: mode-less directories, duplicates, hard links, sparse members -----------

@mark.parametrize('fmt', ['zip', 'tar'])
def test_directory_without_mode_is_searchable(db: Database, tmp_path: Path, fmt: str) -> None:
    """A directory the archive gives no mode (synthesized, or a ZIP entry without unix bits) reads 0o755."""
    path = tmp_path / f'a.{fmt}'
    if fmt == 'zip':
        write_zip(path, [('headed/a.txt', b'alpha', 0o644), ('bare/deep/b.txt', b'beta', 0o644)])
        with zipfile.ZipFile(path, mode='a') as archive:
            info = zipfile.ZipInfo('headed/', date_time=ZIP_DATE)
            info.external_attr = 0x10                                     # the MS-DOS directory bit, no unix mode
            archive.writestr(info, b'')
        with zipfile.ZipFile(path) as archive:
            assert archive.getinfo('headed/').external_attr >> 16 == 0
        modeless = ['/headed', '/bare', '/bare/deep']
    else:
        write_tar(path, [('file', 'bare/deep/b.txt', b'beta', 0o644)])
        modeless = ['/bare', '/bare/deep']
    arc = tier(db, path, fmt)
    for directory in modeless:
        found = arc.stat(P(directory))
        assert (found.kind, found.mode) == ('dir', 0o755)
    assert {entry.name: entry.stat.mode for entry in arc.list(P('/')) if entry.kind == 'dir'} \
        == {P(directory).name: 0o755 for directory in modeless if P(directory).parent == P('/')}
    assert arc.stat(P('/bare/deep/b.txt')).mode == 0o644              # a file keeps its own mode


@mark.parametrize('fmt', ['zip', 'tar'])
def test_duplicate_member_last_wins(db: Database, tmp_path: Path, fmt: str) -> None:
    """A name stored twice indexes once, as its later member, which is what the stdlib reads by that name."""
    path = tmp_path / f'a.{fmt}'
    if fmt == 'zip':
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')                               # zipfile warns on a duplicate name
            write_zip(path, [('dup.txt', b'first', 0o644), ('other', b'x', 0o644),
                             ('dup.txt', b'second, longer', 0o600)])
        with zipfile.ZipFile(path) as archive:
            expected = archive.read('dup.txt')
    else:
        write_tar(path, [('file', 'dup.txt', b'first', 0o644), ('file', 'other', b'x', 0o644),
                         ('file', 'dup.txt', b'second, longer', 0o600)])
        with tarfile.open(path, mode='r:') as archive:
            expected = archive.extractfile('dup.txt').read()
    assert expected == b'second, longer'
    arc = tier(db, path, fmt)
    assert [entry.name for entry in arc.list(P('/'))] == ['dup.txt', 'other']
    found = arc.stat(P('/dup.txt'))
    assert (found.size, found.mode) == (len(expected), 0o600)
    assert arc.read(P('/dup.txt')) == expected
    assert [row['member'] for row in db.execute(t'SELECT member FROM archive_index WHERE tier = {"arc"}')] \
        == ['/dup.txt', '/other']


def test_hard_link_reads_its_target(db: Database, tmp_path: Path) -> None:
    """A TAR hard link is a regular file carrying its target's bytes."""
    path = tmp_path / 'a.tar'
    with tarfile.open(path, mode='w') as archive:
        info = tarfile.TarInfo('data/a.txt')
        info.size, info.mode, info.mtime = 11, 0o640, 111.0
        archive.addfile(info, io.BytesIO(b'alpha-bytes'))
        link = tarfile.TarInfo('link.txt')
        link.type, link.linkname, link.mode, link.mtime = tarfile.LNKTYPE, 'data/a.txt', 0o640, 111.0
        archive.addfile(link)
    with tarfile.open(path, mode='r:') as archive:
        assert archive.getmember('link.txt').islnk()
        expected = archive.extractfile('link.txt').read()
    arc = tier(db, path, 'tar')
    assert [(entry.name, entry.kind) for entry in arc.list(P('/'))] == [('data', 'dir'), ('link.txt', 'file')]
    found = arc.stat(P('/link.txt'))
    assert (found.kind, found.size, found.mode) == ('file', 11, 0o640)
    assert arc.read(P('/link.txt')) == expected == b'alpha-bytes'
    assert arc.read(P('/data/a.txt')) == b'alpha-bytes'
    assert arc.usage() == TierUsage(bytes=22, files=2)


def test_sparse_member_reads_its_bytes(db: Database, tmp_path: Path) -> None:
    """A sparse TAR member reads as its whole content, holes filled, not as its stored data blocks."""
    path = tmp_path / 'a.tar'
    stored = b'hello' + b'world'
    with tarfile.open(path, mode='w', format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo('holes.bin')                               # GNU sparse 0.1, carried in PAX headers
        info.size, info.mode, info.mtime = len(stored), 0o644, 111.0
        info.pax_headers = {'GNU.sparse.map': '0,5,8192,5', 'GNU.sparse.size': '8197'}
        archive.addfile(info, io.BytesIO(stored))
        after = tarfile.TarInfo('after.txt')
        after.size, after.mode, after.mtime = 3, 0o644, 111.0
        archive.addfile(after, io.BytesIO(b'end'))
    expected = b'hello' + bytes(8192 - 5) + b'world'
    with tarfile.open(path, mode='r:') as archive:
        assert archive.getmember('holes.bin').issparse()
        assert archive.extractfile('holes.bin').read() == expected
    arc = tier(db, path, 'tar')
    found = arc.stat(P('/holes.bin'))
    assert (found.kind, found.size) == ('file', len(expected))
    assert arc.read(P('/holes.bin')) == expected
    assert arc.read(P('/holes.bin'), offset=8190, size=4) == b'\x00\x00wo'
    assert arc.read(P('/after.txt')) == b'end'
    assert tier(db, path, 'tar', name='arc').read(P('/holes.bin')) == expected     # from the cached index too
