# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Overlay lifecycle state (the shutdown dirty bit) and managed-location markers."""

# Type annotations
from __future__ import annotations
from typing import Optional, Final

# Standard libs
import os
import sqlite3
import tomllib
from enum import Enum
from pathlib import Path
from dataclasses import dataclass

# Internal libs
from sosfs.core.exceptions import SOSFSError
from sosfs.core.logging import Logger
from sosfs.state.db import Database

# Public interface
__all__ = ['Lifecycle', 'Overlay', 'Marker', 'MarkerStatus', 'verify_marker',
           'LifecycleError', 'CrashDetected', 'MarkerError']

# Initialize logger
log = Logger.with_name(__name__)


class LifecycleError(SOSFSError):
    """An overlay lifecycle transition was not valid from the current state."""


class CrashDetected(SOSFSError):
    """The overlay was left 'mounted' by a process that never shut down — audit required."""


class MarkerError(SOSFSError):
    """A managed-location marker exists but cannot be parsed."""


@dataclass(frozen=True)
class Overlay:
    """The single overlay row: identity plus the lifecycle flag."""

    uuid: str
    name: str
    generation: int
    shutdown_state: str
    host: Optional[str]
    created: float
    last_mounted: Optional[float]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Overlay:
        """Build from a database row."""
        return cls(uuid=row['uuid'], name=row['name'], generation=row['generation'],
                   shutdown_state=row['shutdown_state'], host=row['host'],
                   created=row['created'], last_mounted=row['last_mounted'])


class Lifecycle:
    """
    Transitions of the overlay lifecycle flag (PLAN.md -> Lifecycle and recovery).

    Valid transitions: clean/detached -> mounted (`begin_mount`, bumping the
    generation and recording the host) and mounted -> clean/detached (`end_mount`,
    which the audit also uses to settle a crashed overlay to 'detached' after
    reconciliation). Finding 'mounted' at begin_mount means the previous process
    died without any shutdown path: `CrashDetected`.
    """

    def __init__(self, db: Database) -> None:
        """Initialize over an initialized `Database`."""
        self.db = db

    def create(self, name: str, *, uuid: str, now: float) -> Overlay:
        """Insert the overlay row for a fresh state database."""
        try:
            row = self.db.execute(
                t'INSERT INTO overlay (id, uuid, name, created) '
                t'VALUES (1, {uuid}, {name}, {now}) RETURNING *').fetchone()
        except sqlite3.IntegrityError:
            raise LifecycleError('Overlay already initialized') from None
        log.debug(f'Overlay initialized (name={name}, uuid={uuid})')
        return Overlay.from_row(row)

    def load(self) -> Optional[Overlay]:
        """The overlay row, or None for a schema with no overlay yet."""
        row = self.db.execute(t'SELECT * FROM overlay WHERE id = 1').fetchone()
        return None if row is None else Overlay.from_row(row)

    def begin_mount(self, host: str, *, now: float) -> Overlay:
        """Transition to 'mounted': bump the generation and record the mounting host."""
        with self.db.transaction():
            current = self.load()
            if current is None:
                raise LifecycleError('Overlay not initialized')
            if current.shutdown_state == 'mounted':
                raise CrashDetected(f'Unclean shutdown detected '
                                    f'(last mounted on {current.host or "unknown"}) — audit required')
            row = self.db.execute(
                t"UPDATE overlay SET shutdown_state = 'mounted', host = {host}, "
                t'generation = generation + 1, last_mounted = {now} WHERE id = 1 RETURNING *').fetchone()
        overlay = Overlay.from_row(row)
        log.debug(f'Mounted (host={host}, generation={overlay.generation})')
        return overlay

    def end_mount(self, state: str) -> Overlay:
        """Transition from 'mounted' to 'clean' (drained) or 'detached' (finalize later)."""
        if state not in ('clean', 'detached'):
            raise ValueError(f"Expected 'clean' or 'detached', given {state!r}")
        with self.db.transaction():
            current = self.load()
            if current is None or current.shutdown_state != 'mounted':
                found = current.shutdown_state if current else 'uninitialized'
                raise LifecycleError(f'Not mounted (state: {found})')
            row = self.db.execute(
                t'UPDATE overlay SET shutdown_state = {state} WHERE id = 1 RETURNING *').fetchone()
        log.debug(f'Shutdown state set ({state})')
        return Overlay.from_row(row)


MARKER_DIR: Final[str] = '.sosfs'
MARKER_NAME: Final[str] = 'marker'


class MarkerStatus(Enum):
    """Outcome of verifying a managed-location marker against expectations."""

    OK = 'ok'
    MISSING = 'missing'                            # tier evaporated or never initialized
    CORRUPT = 'corrupt'                            # present but unparseable
    FOREIGN = 'foreign'                            # belongs to a different overlay
    TIER_MISMATCH = 'tier-mismatch'                # right overlay, wrong tier name
    GENERATION_MISMATCH = 'generation-mismatch'    # re-initialized outside our knowledge


@dataclass(frozen=True)
class Marker:
    """
    A managed-location marker (`.sosfs/marker` at a tier root).

    A missing or mismatched marker tells the audit a tier evaporated wholesale or
    was swapped, rather than files going missing one by one. Serialization is TOML;
    POSIX read/write lives here, and non-POSIX tier adapters reuse dump/load over
    their own small-object I/O.
    """

    uuid: str
    tier: str
    generation: int
    written: float

    def dump(self) -> str:
        """Serialize to marker-file text."""
        return ('# sosfs managed-location marker — do not edit\n'
                f'uuid = "{self.uuid}"\n'
                f'tier = "{self.tier}"\n'
                f'generation = {self.generation}\n'
                f'written = {self.written}\n')

    @classmethod
    def load(cls, text: str) -> Marker:
        """Parse marker-file text."""
        try:
            data = tomllib.loads(text)
            return cls(uuid=str(data['uuid']), tier=str(data['tier']),
                       generation=int(data['generation']), written=float(data.get('written', 0.0)))
        except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as error:
            raise MarkerError(f'Corrupt marker: {error}') from None

    def write(self, root: str | Path) -> None:
        """Write atomically under `root` (temp + fsync + rename)."""
        directory = Path(root) / MARKER_DIR
        directory.mkdir(parents=True, exist_ok=True)
        temp = directory / f'{MARKER_NAME}.partial'
        with open(temp, mode='w') as stream:
            stream.write(self.dump())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, directory / MARKER_NAME)
        log.trace(f'Marker written (tier={self.tier}, generation={self.generation}, root={root})')

    @classmethod
    def read(cls, root: str | Path) -> Optional[Marker]:
        """Read the marker under `root`; None when missing, `MarkerError` when corrupt."""
        try:
            text = (Path(root) / MARKER_DIR / MARKER_NAME).read_text()
        except FileNotFoundError:
            return None
        return cls.load(text)

    def status(self, *, uuid: str, tier: str, generation: int) -> MarkerStatus:
        """Compare against expected identity."""
        if self.uuid != uuid:
            return MarkerStatus.FOREIGN
        if self.tier != tier:
            return MarkerStatus.TIER_MISMATCH
        if self.generation != generation:
            return MarkerStatus.GENERATION_MISMATCH
        return MarkerStatus.OK


def verify_marker(root: str | Path, *, uuid: str, tier: str, generation: int) -> MarkerStatus:
    """Verify the marker at a POSIX tier root against expected identity."""
    try:
        marker = Marker.read(root)
    except MarkerError:
        return MarkerStatus.CORRUPT
    if marker is None:
        return MarkerStatus.MISSING
    return marker.status(uuid=uuid, tier=tier, generation=generation)
