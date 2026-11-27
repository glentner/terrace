# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""S3 object-store tier via obstore: whole-object I/O with atomic single-PUT semantics."""

# Type annotations
from __future__ import annotations
from typing import TYPE_CHECKING, BinaryIO, Iterator, Optional

# Standard libs
import io
import errno
from pathlib import Path, PurePosixPath
from contextlib import contextmanager

# External libs
import obstore
from obstore.store import S3Store
from obstore.exceptions import NotFoundError, AlreadyExistsError, BaseError

# Internal libs
from terrace.core.logging import Logger
from terrace.state.lifecycle import MARKER_DIR, MARKER_NAME, Marker
from terrace.tier.protocol import TierStat, TierEntry, TierUsage, FileMeta, TierError

if TYPE_CHECKING:
    from terrace.tier.spec import S3Spec

# Public interface
__all__ = ['S3Tier', 's3_store_options']

# Initialize logger
log = Logger.with_name(__name__)


ROOT = PurePosixPath('/')
MARKER_KEY = f'{MARKER_DIR}/{MARKER_NAME}'
KEEP = '.terrace.keep'                            # reserved zero-byte sentinel that makes an empty prefix a directory


@contextmanager
def _s3_errors(pure: PurePosixPath) -> Iterator[None]:
    """Map obstore's error types onto the protocol's: path-shaped -> OSError, else TierError."""
    try:
        yield
    except NotFoundError:
        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure)) from None
    except AlreadyExistsError:
        raise FileExistsError(errno.EEXIST, 'File exists', str(pure)) from None
    except BaseError as error:
        raise TierError(f'S3 backend error at {pure}: {error}') from error


def s3_store_options(spec: S3Spec) -> dict:
    """Translate an `S3Spec`'s connection settings into obstore `S3Store` keyword options."""
    options = {'region': spec.region, 'endpoint': spec.endpoint, 'access_key_id': spec.access_key_id,
               'secret_access_key': spec.secret_access_key, 'session_token': spec.session_token}
    options = {key: value for key, value in options.items() if value is not None}
    if spec.anonymous:
        options['skip_signature'] = True
    if spec.virtual_hosted:
        options['virtual_hosted_style_request'] = True
    if spec.allow_http:
        options['client_options'] = {'allow_http': True}
    return options


class S3Tier:
    """
    Tier over an S3 bucket (and optional key prefix) via obstore.

    Overlay-absolute paths map to bucket keys beneath the configured prefix (obstore
    applies the prefix; this adapter works in prefix-relative key space). `put` is a
    single atomic S3 PUT — a key is never partially visible, so no temp-key dance is
    needed (obstore uses multipart transparently for large objects). Object stores
    carry no POSIX metadata, so `stat` reports mode/uid/gid as None and mtime from the
    object's last-modified time; `put`/`set_meta` cannot apply mode, ownership, or an
    arbitrary mtime (the catalog remains authoritative for those, PLAN.md -> State
    store). Directories are emulated: a prefix that contains any object reads as a
    directory, and `mkdir` holds an otherwise-empty one with a reserved zero-byte
    '.terrace.keep' sentinel (a trailing-slash marker aliases with `head` on this
    backend). The managed '.terrace' tree reads as absent and refuses mutation, exactly
    as on POSIX. `delete` and `rmdir` marker removal are idempotent — re-running a
    committed removal never fails.
    """

    def __init__(self, name: str, store: S3Store) -> None:
        """Initialize over a ready obstore store (any obstore store object works)."""
        self.name = name
        self.store = store

    @classmethod
    def from_spec(cls, name: str, spec: S3Spec) -> S3Tier:
        """Build an `S3Store` from a parsed `S3Spec` and wrap it as a tier."""
        store = S3Store(spec.bucket, prefix=spec.prefix or None, **s3_store_options(spec))
        return cls(name, store)

    @staticmethod
    def _split(path: PurePosixPath | str) -> tuple[PurePosixPath, str]:
        """Normalize to overlay-absolute form and the prefix-relative key ('' for the root)."""
        pure = ROOT / path
        if '..' in pure.parts:
            raise ValueError(f'Path escapes tier root: {path}')
        return pure, '' if pure == ROOT else pure.relative_to(ROOT).as_posix()

    @staticmethod
    def _internal(pure: PurePosixPath) -> bool:
        """Whether `pure` is the managed '.terrace' directory at the tier root (or within it)."""
        return pure.parts[1:2] == (MARKER_DIR, )

    def _is_dir(self, key: str) -> bool:
        """A key names a directory when any object lives beneath its '<key>/' prefix."""
        listing = obstore.list_with_delimiter(self.store, key + '/')
        return bool(listing['objects'] or listing['common_prefixes'])

    def stat(self, path: PurePosixPath) -> Optional[TierStat]:
        """Metadata for `path`, or None when missing; files win over same-named prefixes."""
        pure, key = self._split(path)
        if self._internal(pure) or pure.name == KEEP:
            return None
        if pure == ROOT:
            return TierStat(kind='dir', size=0, mtime=0.0)
        with _s3_errors(pure):
            try:
                meta = obstore.head(self.store, key)
            except (FileNotFoundError, NotFoundError):
                meta = None
            if meta is not None:
                return TierStat(kind='file', size=meta['size'], mtime=meta['last_modified'].timestamp())
            return TierStat(kind='dir', size=0, mtime=0.0) if self._is_dir(key) else None

    def list(self, path: PurePosixPath) -> Iterator[TierEntry]:
        """Entries of the directory at `path`, name-sorted, excluding markers and '.terrace'."""
        pure, key = self._split(path)
        prefix = '' if pure == ROOT else key + '/'
        if pure != ROOT:
            existing = self.stat(path)
            if existing is None:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure))
            if existing.kind != 'dir':
                raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', str(pure))
        with _s3_errors(pure):
            listing = obstore.list_with_delimiter(self.store, prefix)
        entries = []
        for common in listing['common_prefixes']:
            name = common[len(prefix):].rstrip('/')
            if not name or (pure == ROOT and name == MARKER_DIR):
                continue
            entries.append(TierEntry(name=name, kind='dir', stat=TierStat(kind='dir', size=0, mtime=0.0)))
        for obj in listing['objects']:
            object_key = obj['path']
            if object_key == prefix or object_key.endswith('/'):
                continue                                    # the directory's own marker, or a child marker
            name = object_key[len(prefix):]
            if '/' in name or name == KEEP or (pure == ROOT and name == MARKER_DIR):
                continue
            entries.append(TierEntry(name=name, kind='file', stat=TierStat(
                kind='file', size=obj['size'], mtime=obj['last_modified'].timestamp())))
        for entry in sorted(entries, key=lambda item: item.name):
            yield entry

    def read(self, path: PurePosixPath, offset: int = 0, size: int = -1) -> bytes:
        """Bytes of the object at `path`; ranged reads use S3 GET Range."""
        pure, key = self._split(path)
        if self._internal(pure):
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure))
        with _s3_errors(pure):
            if size < 0:
                data = bytes(obstore.get(self.store, key).bytes())
                return data[offset:] if offset else data
            return bytes(obstore.get_range(self.store, key, start=offset, length=size))

    def open_read(self, path: PurePosixPath) -> BinaryIO:
        """A binary handle over the object at `path` (buffered whole; streaming is future work)."""
        pure, key = self._split(path)
        if self._internal(pure):
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure))
        with _s3_errors(pure):
            return io.BytesIO(bytes(obstore.get(self.store, key).bytes()))

    def put(self, path: PurePosixPath, source: BinaryIO | Path, *, meta: FileMeta) -> None:
        """Store the full content of `source` at `path` as one atomic PUT; POSIX metadata is not retained."""
        pure, key = self._split(path)
        if self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        with _s3_errors(pure):
            if isinstance(source, Path):
                with open(source, mode='rb') as content:
                    obstore.put(self.store, key, content)
            else:
                obstore.put(self.store, key, source)
        log.trace(f'Put {pure} (tier={self.name}, size={meta.size})')

    def set_meta(self, path: PurePosixPath, meta: FileMeta) -> None:
        """Verify the object exists; S3 cannot apply POSIX mode/ownership/mtime, so this is otherwise a no-op."""
        pure, _ = self._split(path)
        if self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        if self.stat(path) is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure))
        log.trace(f'Set meta {pure} (tier={self.name}) — no-op on object storage')

    def delete(self, path: PurePosixPath) -> None:
        """Remove the object at `path`; idempotent (a committed unlink re-run never fails)."""
        pure, key = self._split(path)
        if self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        with _s3_errors(pure):
            obstore.delete(self.store, key)
        log.trace(f'Deleted {pure} (tier={self.name})')

    def mkdir(self, path: PurePosixPath, mode: int = 0o777, *,
              uid: Optional[int] = None, gid: Optional[int] = None) -> None:
        """
        Create a directory marker at `path`; the parent must exist. `mode`, `uid` and `gid` are not retained on
        S3.
        """
        pure, key = self._split(path)
        if pure == ROOT or self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        parent = self.stat(PurePosixPath(pure.parent))
        if parent is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure.parent))
        if parent.kind != 'dir':
            raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', str(pure.parent))
        if self.stat(path) is not None:
            raise FileExistsError(errno.EEXIST, 'File exists', str(pure))
        with _s3_errors(pure):
            obstore.put(self.store, f'{key}/{KEEP}', b'')
        log.trace(f'Mkdir {pure} (tier={self.name})')

    def rmdir(self, path: PurePosixPath) -> None:
        """Remove the directory at `path`; it must be empty."""
        pure, key = self._split(path)
        if pure == ROOT or self._internal(pure):
            raise PermissionError(f'Managed internal: {pure}')
        existing = self.stat(path)
        if existing is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure))
        if existing.kind != 'dir':
            raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', str(pure))
        if any(True for _ in self.list(path)):
            raise OSError(errno.ENOTEMPTY, 'Directory not empty', str(pure))
        with _s3_errors(pure):
            obstore.delete(self.store, f'{key}/{KEEP}')
        log.trace(f'Rmdir {pure} (tier={self.name})')

    def rename(self, old: PurePosixPath, new: PurePosixPath) -> None:
        """Move `old` to `new`; files rename atomically, directories move their whole prefix (not atomic)."""
        pure_old, key_old = self._split(old)
        pure_new, key_new = self._split(new)
        if pure_old == ROOT or self._internal(pure_old) or self._internal(pure_new):
            raise PermissionError(f'Managed internal: {pure_old} -> {pure_new}')
        existing = self.stat(old)
        if existing is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', str(pure_old))
        with _s3_errors(pure_old):
            if existing.kind == 'dir':
                self._rename_prefix(key_old, key_new)
            else:
                obstore.rename(self.store, key_old, key_new, overwrite=True)
        log.trace(f'Renamed {pure_old} -> {pure_new} (tier={self.name})')

    def _rename_prefix(self, key_old: str, key_new: str) -> None:
        """Relocate every object under '<key_old>/' (marker and descendants) to '<key_new>/'."""
        prefix = key_old + '/'
        moved = False
        for chunk in obstore.list(self.store, prefix):
            for obj in chunk:
                source_key = obj['path']
                obstore.rename(self.store, source_key, key_new + '/' + source_key[len(prefix):], overwrite=True)
                moved = True
        if not moved:                                       # defensive: re-anchor an otherwise-empty directory
            obstore.put(self.store, f'{key_new}/{KEEP}', b'')

    def usage(self) -> TierUsage:
        """Sum object sizes and counts across the prefix, excluding markers and '.terrace' internals."""
        total, count = 0, 0
        for chunk in obstore.list(self.store):
            for obj in chunk:
                key = obj['path']
                if key.rsplit('/', 1)[-1] == KEEP or key.split('/', 1)[0] == MARKER_DIR:
                    continue
                total += obj['size']
                count += 1
        return TierUsage(bytes=total, files=count)

    # -- managed-location marker (privileged small-object I/O; bypasses the '.terrace' guard) --------------------

    def read_marker(self) -> Optional[Marker]:
        """Read the '.terrace/marker' object; None when missing, `MarkerError` when corrupt."""
        try:
            text = bytes(obstore.get(self.store, MARKER_KEY).bytes()).decode()
        except (FileNotFoundError, NotFoundError):
            return None
        return Marker.load(text)

    def write_marker(self, marker: Marker) -> None:
        """Write the '.terrace/marker' object (reusing the shared TOML serialization)."""
        obstore.put(self.store, MARKER_KEY, marker.dump().encode())
        log.trace(f'Marker written (tier={marker.tier}, generation={marker.generation}, bucket={self.store})')
