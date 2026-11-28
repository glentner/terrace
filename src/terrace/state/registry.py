# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Tier registry and quota-usage counters over the state database."""

# Type annotations
from __future__ import annotations
from typing import List, Optional, Tuple, Iterator

# Standard libs
import json
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from contextlib import contextmanager

# Internal libs
from terrace.core.exceptions import TerraceError
from terrace.core.logging import Logger
from terrace.state.db import Database
from terrace.state.lifecycle import Marker, MarkerStatus, MarkerError
from terrace.tier.protocol import TierUsage

# Public interface
__all__ = ['TierRegistry', 'TierRecord', 'SpecDrift']

# Initialize logger
log = Logger.with_name(__name__)


class SpecDrift(TerraceError):
    """The configured tier stack does not match what this state database registered."""


@dataclass(frozen=True)
class TierRecord:
    """A registered tier: static identity of one stack position."""

    name: str
    position: int
    type: str
    role: str
    scope: str
    generation: int
    meta: Optional[dict]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> TierRecord:
        """Build from a database row."""
        meta = row['meta']
        return cls(name=row['name'], position=row['position'], type=row['type'], role=row['role'],
                   scope=row['scope'], generation=row['generation'],
                   meta=None if meta is None else json.loads(meta))


class TierRegistry:
    """
    The configured stack's identity and its hot usage counters.

    `register` records a tier on first attach and detects spec drift on every
    later one; `attach` composes registration with managed-location marker
    initialization/verification (PLAN.md -> State store), and `enlist` registers
    a tier with no marker by its recorded identity instead. Counter updates are
    single UPDATE statements, so concurrent writers serialize in SQLite and no
    increment is ever lost. Counters are wired to put/delete by the engine:
    every acknowledged put or delete adjusts them in the same transaction as
    the residency change it records.
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

    def register(self, name: str, *, position: int, type: str, role: str, scope: str) -> Tuple[TierRecord, bool]:
        """
        Record the tier on first attach (with a zeroed usage row) and return
        (record, True); on re-attach verify identity against the registered row
        and return (record, False) — any difference is `SpecDrift`.
        """
        with self._atomic():
            row = self.db.execute(t'SELECT * FROM tier WHERE name = {name}').fetchone()
            if row is None:
                row = self.db.execute(
                    t'INSERT INTO tier (name, position, type, role, scope) '
                    t'VALUES ({name}, {position}, {type}, {role}, {scope}) RETURNING *').fetchone()
                self.db.execute(t'INSERT INTO tier_usage (tier) VALUES ({name})')
                log.debug(f'Tier registered (name={name}, position={position}, type={type})')
                return TierRecord.from_row(row), True
        record = TierRecord.from_row(row)
        found = (record.position, record.type, record.role, record.scope)
        if found != (position, type, role, scope):
            raise SpecDrift(f'Tier {name!r} was registered as (position={record.position}, '
                            f'type={record.type}, role={record.role}, scope={record.scope}); '
                            f'the spec now says (position={position}, type={type}, role={role}, scope={scope})')
        return record, False

    def attach(self, name: str, *, position: int, type: str, role: str, scope: str,
               root: str | Path, uuid: str, now: float) -> Tuple[TierRecord, MarkerStatus]:
        """Register a POSIX tier and initialize or verify its filesystem marker under `root`."""
        return self._attach(name, position=position, type=type, role=role, scope=scope, uuid=uuid, now=now,
                            read=lambda: Marker.read(root), write=lambda marker: marker.write(root))

    def attach_tier(self, tier, *, position: int, type: str, role: str, scope: str,
                    uuid: str, now: float) -> Tuple[TierRecord, MarkerStatus]:
        """Register a tier that owns its marker I/O (e.g. S3) and initialize or verify it via the tier."""
        return self._attach(tier.name, position=position, type=type, role=role, scope=scope, uuid=uuid, now=now,
                            read=tier.read_marker, write=tier.write_marker)

    def _attach(self, name: str, *, position: int, type: str, role: str, scope: str, uuid: str, now: float,
                read, write) -> Tuple[TierRecord, MarkerStatus]:
        """
        Register the tier and initialize or verify its managed-location marker.

        A brand-new registration with no marker writes one (this overlay now owns
        the location). Every other combination verifies and reports: any status
        but OK is the audit's business, not grounds to proceed with a mount.
        Marker I/O is supplied by the caller so POSIX (filesystem) and object-store
        tiers share the identity/generation logic.
        """
        record, new = self.register(name, position=position, type=type, role=role, scope=scope)
        try:
            marker = read()
        except MarkerError:
            return record, MarkerStatus.CORRUPT
        if marker is None:
            if not new:
                return record, MarkerStatus.MISSING
            write(Marker(uuid=uuid, tier=name, generation=record.generation, written=now))
            return record, MarkerStatus.OK
        return record, marker.status(uuid=uuid, tier=name, generation=record.generation)

    def enlist(self, name: str, *, position: int, type: str, role: str, scope: str,
               meta: dict) -> Tuple[TierRecord, MarkerStatus]:
        """
        Register a tier that carries no marker (an archive source) and read it OK by type.

        No marker I/O happens. `meta` records the archive's identity in `tier.meta` on the first
        registration; every later one compares it with the stored record and refuses a change, the
        archive's stand-in for a marker's swap detection.
        """
        given = json.loads(json.dumps(meta))
        with self._atomic():
            record, new = self.register(name, position=position, type=type, role=role, scope=scope)
            if new:
                row = self.db.execute(
                    t'UPDATE tier SET meta = {json.dumps(given)} WHERE name = {name} RETURNING *').fetchone()
                return TierRecord.from_row(row), MarkerStatus.OK
        if record.meta != given:
            raise SpecDrift(f'Tier {name!r}: the archive {given.get("location")} changed since this state '
                            f'directory recorded it (recorded {record.meta}, found {given})')
        return record, MarkerStatus.OK

    def get(self, name: str) -> Optional[TierRecord]:
        """The registered tier, or None."""
        row = self.db.execute(t'SELECT * FROM tier WHERE name = {name}').fetchone()
        return None if row is None else TierRecord.from_row(row)

    def tiers(self) -> List[TierRecord]:
        """All registered tiers in stack order (source first)."""
        rows = self.db.execute(t'SELECT * FROM tier ORDER BY position')
        return [TierRecord.from_row(row) for row in rows]

    def bump_generation(self, name: str) -> TierRecord:
        """Advance the marker generation (a re-initialized managed location)."""
        row = self.db.execute(
            t'UPDATE tier SET generation = generation + 1 WHERE name = {name} RETURNING *').fetchone()
        if row is None:
            raise LookupError(f'No such tier: {name}')
        return TierRecord.from_row(row)

    # -- usage counters -----------------------------------------------------------------------------------------

    def usage(self, name: str) -> TierUsage:
        """Current counters for quota accounting."""
        row = self.db.execute(t'SELECT * FROM tier_usage WHERE tier = {name}').fetchone()
        if row is None:
            raise LookupError(f'No such tier: {name}')
        return TierUsage(bytes=row['size_bytes'], files=row['file_count'])

    def adjust_usage(self, name: str, *, bytes: int = 0, files: int = 0) -> TierUsage:
        """Apply deltas atomically (one statement — concurrency-safe by construction)."""
        row = self.db.execute(
            t'UPDATE tier_usage SET size_bytes = size_bytes + {bytes}, '
            t'file_count = file_count + {files} WHERE tier = {name} RETURNING *').fetchone()
        if row is None:
            raise LookupError(f'No such tier: {name}')
        return TierUsage(bytes=row['size_bytes'], files=row['file_count'])

    def set_usage(self, name: str, *, bytes: int, files: int) -> TierUsage:
        """Overwrite the counters (the audit's rebuild step)."""
        row = self.db.execute(
            t'UPDATE tier_usage SET size_bytes = {bytes}, file_count = {files} '
            t'WHERE tier = {name} RETURNING *').fetchone()
        if row is None:
            raise LookupError(f'No such tier: {name}')
        return TierUsage(bytes=row['size_bytes'], files=row['file_count'])
