# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Write-ordering invariant primitives: dirty transitions and the intent journal."""

# Type annotations
from __future__ import annotations
from typing import List, Optional, Iterator, Final

# Standard libs
import json
import sqlite3
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import PurePosixPath

# Internal libs
from terrace.core.logging import Logger
from terrace.state.db import Database
from terrace.state.catalog import normalize

# Public interface
__all__ = ['Journal', 'Intent', 'Writeback', 'ACTIONS', 'NAMESPACE_OPS',
           'PARTIAL_DIR', 'BUNDLE_DIR', 'temp_name', 'is_temp_name']

# Initialize logger
log = Logger.with_name(__name__)


ACTIONS: Final[frozenset] = frozenset({'promote', 'commit', 'evict', 'compact', 'resync'})

NAMESPACE_OPS: Final[frozenset] = frozenset({'create', 'unlink', 'rename', 'mkdir', 'rmdir', 'meta'})

PARTIAL_DIR: Final[str] = '.terrace/partial'

BUNDLE_DIR: Final[str] = 'bundles'            # under '.terrace': a staged tar.gz bundle, one counted file


def temp_name(intent_id: int, path: str) -> str:
    """Tier-relative temp name for an intent's act phase, recognizable to the audit."""
    return f'{PARTIAL_DIR}/{intent_id}-{PurePosixPath(normalize(path)).name}'


def is_temp_name(name: str) -> bool:
    """Whether a tier-relative name is a transfer temp by convention (an orphan if unjournaled)."""
    return PurePosixPath(name).parts[:2] == ('.terrace', 'partial')


@dataclass(frozen=True)
class Intent:
    """A journaled in-flight mutation of a managed location."""

    id: int
    action: str
    entry: Optional[int]
    tier: str
    temp_name: Optional[str]
    detail: Optional[dict]
    created: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Intent:
        """Build from a database row."""
        detail = row['detail']
        return cls(id=row['id'], action=row['action'], entry=row['entry'], tier=row['tier'],
                   temp_name=row['temp_name'], detail=None if detail is None else json.loads(detail),
                   created=row['created'])


@dataclass(frozen=True)
class Writeback:
    """A queued commit or namespace operation for one (entry, boundary)."""

    id: int
    entry: int
    op: str
    from_tier: Optional[str]
    to_tier: str
    detail: Optional[dict]
    state: str
    first_dirty: float
    last_write: float
    last_commit: Optional[float]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Writeback:
        """Build from a database row."""
        detail = row['detail']
        return cls(id=row['id'], entry=row['entry'], op=row['op'], from_tier=row['from_tier'],
                   to_tier=row['to_tier'], detail=None if detail is None else json.loads(detail),
                   state=row['state'], first_dirty=row['first_dirty'], last_write=row['last_write'],
                   last_commit=row['last_commit'])


class Journal:
    """
    The two write-ordering invariants that make the database authoritative after
    any process death (PLAN.md -> State store):

    * **Metadata before acknowledgment** — `mark_dirty` commits the clean->dirty
      residency flip and the queued write-back as one transaction; the caller
      acknowledges the triggering write only after it returns.
    * **Intent journaling** — every managed-location mutation brackets its act
      with `begin` (committed before any bytes move) and `confirm` (composed into
      the same transaction as the metadata recording the act's effect), with the
      act made atomic via a recognizable temp name + fsync + rename. `rollback`
      abandons an intent whose act never took effect.

    Like the catalog, single-statement methods run on the calling thread's
    connection and multi-statement operations join a caller's open
    `Database.transaction()`, so journal and catalog updates can commit together.
    Timestamps are always passed in (injectable-clock convention).
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

    def _entry_id(self, path: str) -> int:
        """Resolve a path to its entry id or raise `LookupError`."""
        row = self.db.execute(t'SELECT id FROM entry WHERE path = {normalize(path)}').fetchone()
        if row is None:
            raise LookupError(f'No such entry: {normalize(path)}')
        return row['id']

    # -- metadata before acknowledgment -------------------------------------------------------------------------

    def mark_dirty(self, path: str, tier: str, *, to_tier: str, now: float) -> Writeback:
        """
        Record that the copy of `path` in `tier` holds new data and queue (or
        refresh) its write-back toward `to_tier`.

        The residency flip and the queue row commit as one transaction, and the
        triggering write must not be acknowledged until this returns. Idempotent:
        an already-dirty copy just refreshes `last_write` (and the LRU clock).
        """
        path = normalize(path)
        with self._atomic():
            entry = self._entry_id(path)
            flipped = self.db.execute(
                t"UPDATE residency SET state = 'dirty', atime = {now} "
                t'WHERE entry = {entry} AND tier = {tier}')
            if flipped.rowcount == 0:
                raise LookupError(f'No copy of {path} in tier {tier}')
            row = self.db.execute(
                t'UPDATE writeback SET last_write = {now} '
                t"WHERE entry = {entry} AND to_tier = {to_tier} AND op = 'data' RETURNING *").fetchone()
            if row is None:
                row = self.db.execute(
                    t"INSERT INTO writeback (entry, op, from_tier, to_tier, first_dirty, last_write) "
                    t"VALUES ({entry}, 'data', {tier}, {to_tier}, {now}, {now}) RETURNING *").fetchone()
        log.trace(f'Marked dirty ({path}: {tier} -> {to_tier})')
        return Writeback.from_row(row)

    def queue(self, path: str, op: str, *, to_tier: str, now: float, detail: Optional[dict] = None) -> Writeback:
        """
        Queue a namespace operation for commit toward `to_tier` — no bytes to
        move; data commits queue through `mark_dirty`. Call inside the same
        transaction as the catalog mutation it propagates, so the op and the
        namespace state it describes commit together.

        The queue-time `path` is recorded in `detail` for the audit. Replay does
        not read it: the row is entry-keyed and resolves the entry's current place
        when it runs, because removing an entry cancels its rows and leaves the
        recorded history with gaps that replaying it verbatim cannot converge over.
        """
        if op not in NAMESPACE_OPS:
            raise ValueError(f'Unknown namespace op: {op!r}')
        path = normalize(path)
        detail = {**(detail or {}), 'path': path}
        encoded = json.dumps(detail, sort_keys=True)
        with self._atomic():
            entry = self._entry_id(path)
            row = self.db.execute(
                t'INSERT INTO writeback (entry, op, to_tier, detail, first_dirty, last_write) '
                t'VALUES ({entry}, {op}, {to_tier}, {encoded}, {now}, {now}) RETURNING *').fetchone()
        log.trace(f'Queued {op} ({path} -> {to_tier})')
        return Writeback.from_row(row)

    def queued(self, to_tier: Optional[str] = None) -> List[Writeback]:
        """Queued write-back rows in queue order (oldest first; `id` breaks clock ties), optionally for one boundary."""
        boundary = t'' if to_tier is None else t' AND to_tier = {to_tier}'
        rows = self.db.execute(
            t"SELECT * FROM writeback WHERE state = 'queued'{boundary} ORDER BY last_write, id")
        return [Writeback.from_row(row) for row in rows]

    def cancel(self, path: str) -> int:
        """Drop all queued rows for `path` (a canceled or superseded propagation); returns the count."""
        cursor = self.db.execute(
            t'DELETE FROM writeback WHERE entry = (SELECT id FROM entry WHERE path = {normalize(path)})')
        return cursor.rowcount

    def queued_for(self, path: str, to_tier: Optional[str] = None) -> List[Writeback]:
        """Queued rows for one entry, oldest first, optionally at one boundary."""
        boundary = t'' if to_tier is None else t' AND to_tier = {to_tier}'
        rows = self.db.execute(
            t'SELECT * FROM writeback '
            t'WHERE entry = (SELECT id FROM entry WHERE path = {normalize(path)}){boundary} '
            t'ORDER BY last_write')
        return [Writeback.from_row(row) for row in rows]

    def current(self, row: Writeback) -> Optional[Writeback]:
        """
        The queued row `row` was read from, or None once it ran, was cancelled or cascaded away.
        Row ids are reused (the id of a deleted highest row goes to the next insert), so a match
        is the same id, entry, op, boundary, detail and first-dirty time: a refreshed row keeps its
        first-dirty time, and a new one does not.
        """
        found = self.db.execute(t'SELECT * FROM writeback WHERE id = {row.id}').fetchone()
        if found is None:
            return None
        found = Writeback.from_row(found)

        def key(r: Writeback) -> tuple:
            return r.entry, r.op, r.from_tier, r.to_tier, r.detail, r.first_dirty

        same = key(found) == key(row)
        return found if same else None

    def settle_current(self, row: Writeback) -> bool:
        """
        Settle the queued row `row` was read from, and nothing else; returns whether it did. Between the read
        and the settle a user op may have cancelled the row and queued another that took its id, so a settle
        by id alone would delete that newer op. The check and the delete run in one write transaction.
        """
        with self._atomic():
            if self.current(row) is None:
                return False
            self.settle(row.id)
        return True

    def settle(self, writeback_id: int) -> None:
        """Remove a queued row whose operation committed (compose with the recording transaction)."""
        cursor = self.db.execute(t'DELETE FROM writeback WHERE id = {writeback_id}')
        if cursor.rowcount == 0:
            raise LookupError(f'No such writeback row: {writeback_id}')

    def rearm(self, writeback_id: int, *, now: float) -> None:
        """
        Re-clock a row whose boundary committed without settling (writes landed
        during the copy, so the holder stays dirty): quiescence restarts at `now`
        and `max_interval` re-anchors on this commit.
        """
        cursor = self.db.execute(
            t'UPDATE writeback SET last_write = {now}, last_commit = {now} WHERE id = {writeback_id}')
        if cursor.rowcount == 0:
            raise LookupError(f'No such writeback row: {writeback_id}')

    # -- intent journal ------------------------------------------------------------------------------------------

    def begin(self, action: str, tier: str, *, now: float, path: Optional[str] = None,
              temp_for: Optional[str] = None, detail: Optional[dict] = None) -> Intent:
        """
        Journal the intent to mutate the managed location `tier`; the act may only
        start after this returns. When `temp_for` names the destination path, a
        recognizable temp name is assigned for the act (write-to-temp + fsync +
        rename; atomic-per-key stores like S3 need no temp and omit it).
        """
        if action not in ACTIONS:
            raise ValueError(f'Unknown intent action: {action!r}')
        encoded = None if detail is None else json.dumps(detail, sort_keys=True)
        with self._atomic():
            entry = None if path is None else self._entry_id(path)
            row = self.db.execute(
                t'INSERT INTO intent (action, entry, tier, detail, created) '
                t'VALUES ({action}, {entry}, {tier}, {encoded}, {now}) RETURNING *').fetchone()
            if temp_for is not None:
                temp = temp_name(row['id'], temp_for)
                row = self.db.execute(
                    t'UPDATE intent SET temp_name = {temp} WHERE id = {row["id"]} RETURNING *').fetchone()
        intent = Intent.from_row(row)
        log.trace(f'Intent begun (id={intent.id}, action={action}, tier={tier})')
        return intent

    def confirm(self, intent: Intent) -> None:
        """
        Resolve `intent`: the act completed. Call inside the same transaction as
        the metadata recording the act's effect (e.g., the new residency row) so
        the journal and the catalog commit together.
        """
        self._resolve(intent, 'confirmed')

    def rollback(self, intent: Intent) -> None:
        """
        Abandon `intent`: the act never took effect. The caller discards the temp
        when possible; either order is crash-safe, since an orphaned temp remains
        recognizable by name and is discarded by the audit.
        """
        self._resolve(intent, 'rolled back')

    def _resolve(self, intent: Intent, verb: str) -> None:
        """Delete the intent row; resolving twice is a caller bug."""
        cursor = self.db.execute(t'DELETE FROM intent WHERE id = {intent.id}')
        if cursor.rowcount == 0:
            raise LookupError(f'No such intent: {intent.id}')
        log.trace(f'Intent {verb} (id={intent.id}, action={intent.action}, tier={intent.tier})')

    def pending(self, tier: Optional[str] = None) -> List[Intent]:
        """Unresolved intents in creation order, optionally for one tier — the audit's worklist."""
        scope = t'' if tier is None else t' WHERE tier = {tier}'
        rows = self.db.execute(t'SELECT * FROM intent{scope} ORDER BY id')
        return [Intent.from_row(row) for row in rows]

    def queued_marks(self, to_tier: Optional[str] = None) -> set[tuple[int, int, str, float]]:
        """
        `(id, entry, op, first_dirty)` of each queued row, optionally for one boundary: enough to tell a row read
        earlier from a newer one that took its id (`current` compares more), in one read that decodes no row.
        """
        boundary = t'' if to_tier is None else t' AND to_tier = {to_tier}'
        rows = self.db.execute(t"SELECT id, entry, op, first_dirty FROM writeback WHERE state = 'queued'{boundary}")
        return {(row['id'], row['entry'], row['op'], row['first_dirty']) for row in rows}
