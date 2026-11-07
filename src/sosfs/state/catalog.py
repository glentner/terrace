# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Catalog: namespace and residency data-access over the state database."""

# Type annotations
from __future__ import annotations
from typing import List, Optional, Tuple, Iterator, Final

# Standard libs
import sqlite3
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import PurePosixPath

# Internal libs
from sosfs.core.logging import Logger
from sosfs.state.db import Database

# Public interface
__all__ = ['Catalog', 'Entry', 'Residency', 'normalize', 'parent_of']

# Initialize logger
log = Logger.with_name(__name__)


def normalize(path: str) -> str:
    """Normalize to overlay-absolute POSIX form ('/x/y'); the root is '/'."""
    return str(PurePosixPath('/') / path)


def parent_of(path: str) -> Optional[str]:
    """Parent path of `path`; None only for the root."""
    path = normalize(path)
    return None if path == '/' else str(PurePosixPath(path).parent)


@dataclass(frozen=True)
class Entry:
    """A namespace entry (file, directory, or symlink)."""

    id: int
    path: str
    parent: Optional[str]
    kind: str
    mode: int
    uid: int
    gid: int
    size: int
    mtime: float
    ctime: float
    atime: float
    target: Optional[str]
    source_path: Optional[str]
    listed: bool
    deleted: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Entry:
        """Build from a database row."""
        return cls(id=row['id'], path=row['path'], parent=row['parent'], kind=row['kind'],
                   mode=row['mode'], uid=row['uid'], gid=row['gid'], size=row['size'],
                   mtime=row['mtime'], ctime=row['ctime'], atime=row['atime'], target=row['target'],
                   source_path=row['source_path'], listed=bool(row['listed']), deleted=bool(row['deleted']))


@dataclass(frozen=True)
class Residency:
    """A tier's copy of an entry."""

    entry: int
    tier: str
    state: str
    atime: float
    size: int
    mtime: float
    checksum: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Residency:
        """Build from a database row."""
        return cls(entry=row['entry'], tier=row['tier'], state=row['state'], atime=row['atime'],
                   size=row['size'], mtime=row['mtime'], checksum=row['checksum'])


META_FIELDS: Final[frozenset] = frozenset(
    {'mode', 'uid', 'gid', 'size', 'mtime', 'ctime', 'atime', 'target', 'source_path'})
RESIDENCY_FIELDS: Final[frozenset] = frozenset({'state', 'atime', 'size', 'mtime', 'checksum'})


class Catalog:
    """
    Namespace and residency operations.

    Queries are PEP 750 t-strings rendered by `state.sql` — interpolations bind as
    parameters, `{name:id}` splices whitelisted identifiers. Methods issue single
    statements on the calling thread's connection, so they compose under a caller's
    `Database.transaction()`. The catalog never reads the clock; all timestamps are
    passed in explicitly (injectable-clock convention).
    """

    def __init__(self, db: Database) -> None:
        """Initialize over an initialized `Database`."""
        self.db = db

    @contextmanager
    def _atomic(self) -> Iterator[sqlite3.Connection]:
        """Join the caller's open transaction or start one for a multi-statement operation."""
        conn = self.db.connection
        if conn.in_transaction:
            yield conn
        else:
            with self.db.transaction() as conn:
                yield conn

    # -- entries ------------------------------------------------------------------------------------------------

    def add(self, path: str, kind: str, *, mode: int, uid: int, gid: int, mtime: float, ctime: float,
            atime: float, size: int = 0, target: Optional[str] = None, source_path: Optional[str] = None) -> Entry:
        """Insert a new entry; parent is derived from `path`. `source_path` records where the source holds it."""
        path = normalize(path)
        row = self.db.execute(
            t'INSERT INTO entry (path, parent, kind, mode, uid, gid, size, mtime, ctime, atime, target, source_path) '
            t'VALUES ({path}, {parent_of(path)}, {kind}, {mode}, {uid}, {gid}, '
            t'        {size}, {mtime}, {ctime}, {atime}, {target}, {source_path}) '
            t'RETURNING *').fetchone()
        return Entry.from_row(row)

    def get(self, path: str, include_deleted: bool = False) -> Optional[Entry]:
        """Look up an entry by path; tombstones are masked unless `include_deleted`."""
        mask = t'' if include_deleted else t' AND deleted = 0'
        row = self.db.execute(t'SELECT * FROM entry WHERE path = {normalize(path)}{mask}').fetchone()
        return None if row is None else Entry.from_row(row)

    def by_id(self, entry_id: int) -> Optional[Entry]:
        """Look up an entry by id, tombstones included — writeback and intent rows carry ids."""
        row = self.db.execute(t'SELECT * FROM entry WHERE id = {entry_id}').fetchone()
        return None if row is None else Entry.from_row(row)

    def holder(self, source_path: str) -> Optional[Entry]:
        """The entry, live or tombstoned, whose committed source path is `source_path`."""
        row = self.db.execute(t'SELECT * FROM entry WHERE source_path = {normalize(source_path)}').fetchone()
        return None if row is None else Entry.from_row(row)

    def children(self, path: str) -> List[Entry]:
        """Direct children of a directory, tombstones masked, ordered by path."""
        rows = self.db.execute(
            t'SELECT * FROM entry WHERE parent = {normalize(path)} AND deleted = 0 ORDER BY path')
        return [Entry.from_row(row) for row in rows]

    def update_meta(self, path: str, /, **fields) -> None:
        """Update metadata fields (whitelisted by META_FIELDS) on an entry."""
        if unknown := set(fields) - META_FIELDS:
            raise ValueError(f'Unknown entry fields: {", ".join(sorted(unknown))}')
        sets = [t'{name:id} = {value}' for name, value in fields.items()]
        self.db.execute(t'UPDATE entry SET {sets} WHERE path = {normalize(path)}')

    def set_listed(self, path: str, listed: bool = True) -> None:
        """Mark a directory's children as fully populated from the source."""
        self.db.execute(t'UPDATE entry SET listed = {int(listed)} WHERE path = {normalize(path)}')

    def mark_deleted(self, path: str) -> None:
        """Tombstone an entry: masked from lookups until the unlink commits upstream."""
        self.db.execute(t'UPDATE entry SET deleted = 1 WHERE path = {normalize(path)}')

    def purge(self, path: str) -> None:
        """Hard-delete an entry (residency, writeback, and intent rows cascade)."""
        self.db.execute(t'DELETE FROM entry WHERE path = {normalize(path)}')

    def rename(self, old: str, new: str) -> None:
        """Rename an entry and, for directories, rewrite the whole subtree."""
        old, new = normalize(old), normalize(new)
        offset = len(old) + 1
        with self._atomic():
            self.db.execute(
                t'UPDATE entry SET path = {new}, parent = {parent_of(new)} WHERE path = {old}')
            # Subtree selection by range: '0' is '/' + 1 in ASCII, and unlike GLOB the
            # comparison is immune to pattern metacharacters appearing in path names.
            self.db.execute(
                t'UPDATE entry SET path = {new} || substr(path, {offset}), '
                t'                 parent = {new} || substr(parent, {offset}) '
                t'WHERE path >= {old + "/"} AND path < {old + "0"}')

    def advance_source_path(self, old_source: str, new: str) -> None:
        """
        Relocate the recorded committed source path from `old_source` to `new` — a source-tier
        rename committing upstream — rewriting the prefix for every descendant so a directory
        move carries its whole subtree (mirrors `rename`'s range selection).
        """
        old_source, new = normalize(old_source), normalize(new)
        offset = len(old_source) + 1
        with self._atomic():
            self.db.execute(t'UPDATE entry SET source_path = {new} WHERE source_path = {old_source}')
            self.db.execute(
                t'UPDATE entry SET source_path = {new} || substr(source_path, {offset}) '
                t'WHERE source_path >= {old_source + "/"} AND source_path < {old_source + "0"}')

    # -- residency ----------------------------------------------------------------------------------------------

    def add_residency(self, path: str, tier: str, *, atime: float, size: int, mtime: float,
                      state: str = 'clean', checksum: Optional[str] = None) -> Residency:
        """Record that `tier` holds a copy of the entry at `path`."""
        entry = self.get(path, include_deleted=True)
        if entry is None:
            raise LookupError(f'No such entry: {path}')
        row = self.db.execute(
            t'INSERT INTO residency (entry, tier, state, atime, size, mtime, checksum) '
            t'VALUES ({entry.id}, {tier}, {state}, {atime}, {size}, {mtime}, {checksum}) '
            t'RETURNING *').fetchone()
        return Residency.from_row(row)

    def get_residency(self, path: str, tier: str) -> Optional[Residency]:
        """The copy of `path` held by `tier`, if any."""
        row = self.db.execute(
            t'SELECT r.* FROM residency r JOIN entry e ON e.id = r.entry '
            t'WHERE e.path = {normalize(path)} AND r.tier = {tier}').fetchone()
        return None if row is None else Residency.from_row(row)

    def residencies(self, path: str) -> List[Residency]:
        """All copies of `path`, ordered upstream to downstream (source first)."""
        rows = self.db.execute(
            t'SELECT r.* FROM residency r '
            t'JOIN entry e ON e.id = r.entry JOIN tier t ON t.name = r.tier '
            t'WHERE e.path = {normalize(path)} ORDER BY t.position')
        return [Residency.from_row(row) for row in rows]

    def fastest_copy(self, path: str) -> Optional[Residency]:
        """The most-downstream (fastest) copy of `path`, if any."""
        row = self.db.execute(
            t'SELECT r.* FROM residency r '
            t'JOIN entry e ON e.id = r.entry JOIN tier t ON t.name = r.tier '
            t'WHERE e.path = {normalize(path)} ORDER BY t.position DESC LIMIT 1').fetchone()
        return None if row is None else Residency.from_row(row)

    def update_residency(self, path: str, tier: str, /, **fields) -> None:
        """Update residency fields (whitelisted by RESIDENCY_FIELDS) for a copy."""
        if unknown := set(fields) - RESIDENCY_FIELDS:
            raise ValueError(f'Unknown residency fields: {", ".join(sorted(unknown))}')
        sets = [t'{name:id} = {value}' for name, value in fields.items()]
        self.db.execute(
            t'UPDATE residency SET {sets} '
            t'WHERE tier = {tier} AND entry = (SELECT id FROM entry WHERE path = {normalize(path)})')

    def touch(self, path: str, tier: str, atime: float) -> None:
        """Advance the per-tier LRU clock for a copy."""
        self.update_residency(path, tier, atime=atime)

    def drop_residency(self, path: str, tier: str) -> None:
        """Forget that `tier` holds a copy of `path`."""
        self.db.execute(
            t'DELETE FROM residency '
            t'WHERE tier = {tier} AND entry = (SELECT id FROM entry WHERE path = {normalize(path)})')

    def least_recent(self, tier: str, *, state: str = 'clean', limit: int = 100) -> List[Tuple[str, Residency]]:
        """Oldest copies in `tier` by LRU atime — the eviction scan."""
        rows = self.db.execute(
            t'SELECT e.path AS path, r.entry AS entry, r.tier AS tier, r.state AS state, '
            t'       r.atime AS atime, r.size AS size, r.mtime AS mtime, r.checksum AS checksum '
            t'FROM residency r JOIN entry e ON e.id = r.entry '
            t'WHERE r.tier = {tier} AND r.state = {state} ORDER BY r.atime LIMIT {limit}')
        return [(row['path'], Residency.from_row(row)) for row in rows]
