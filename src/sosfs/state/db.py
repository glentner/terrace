# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Database layer for the sosfs state store (stdlib sqlite3, WAL, per-thread connections)."""

# Type annotations
from __future__ import annotations
from typing import Dict, Final, Iterator, Optional

# Standard libs
import os
import fcntl
import socket
import sqlite3
import threading
from pathlib import Path
from contextlib import contextmanager
from importlib import resources
from string.templatelib import Template

# Internal libs
from sosfs.core.exceptions import SOSFSError
from sosfs.core.logging import Logger
from sosfs.state.sql import render

# Public interface
__all__ = ['Database', 'ExclusiveLock', 'load_schema', 'SCHEMA_VERSION',
           'DatabaseLocked', 'DatabaseUninitialized', 'SchemaMismatch']

# Initialize logger
log = Logger.with_name(__name__)


SCHEMA_VERSION: Final[int] = 2
DEFAULT_TIMEOUT: Final[float] = 5.0


class DatabaseLocked(SOSFSError):
    """The state directory is exclusively locked by another process."""


class DatabaseUninitialized(SOSFSError):
    """The state database has not been initialized with the schema."""


class SchemaMismatch(SOSFSError):
    """The state database schema version does not match this build."""


def load_schema() -> str:
    """Read the packaged schema definition."""
    return (resources.files('sosfs.state') / 'schema.sql').read_text()


class Database:
    """
    Connection manager for the sosfs state database.

    Each thread gets its own connection (created on demand) with WAL journaling,
    `synchronous=NORMAL`, and foreign keys enforced. WAL guarantees committed
    transactions survive process death (SIGKILL); NORMAL is sufficient because the
    harsher cases (kernel panic, power loss) also evaporate the volatile tiers,
    which the audit handles anyway (PLAN.md -> State store).
    """

    def __init__(self, path: str | Path, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Initialize with database file `path`; no disk access happens here."""
        self.path = Path(path)
        self.timeout = timeout
        self._local = threading.local()
        self._connections: Dict[int, sqlite3.Connection] = {}
        self._lock = threading.Lock()

    def initialize(self) -> None:
        """Create the schema in a fresh database, or verify the version of an existing one."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect(verify=False)
        try:
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version == SCHEMA_VERSION:
                log.trace(f'Database already initialized ({self.path})')
            elif version == 0:
                conn.executescript(load_schema())
                log.debug(f'Database initialized ({self.path})')
            else:
                raise SchemaMismatch(f'Database schema v{version}, expected v{SCHEMA_VERSION} ({self.path})')
        finally:
            conn.close()

    def _connect(self, verify: bool = True) -> sqlite3.Connection:
        """Open a new connection with pragmas applied (and schema version checked)."""
        conn = sqlite3.connect(self.path, timeout=self.timeout, check_same_thread=False, autocommit=True)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA synchronous = NORMAL')
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute(f'PRAGMA busy_timeout = {int(self.timeout * 1000)}')
        if verify:
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                conn.close()
                raise DatabaseUninitialized(f'Database not initialized ({self.path})')
            if version != SCHEMA_VERSION:
                conn.close()
                raise SchemaMismatch(f'Database schema v{version}, expected v{SCHEMA_VERSION} ({self.path})')
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        """The calling thread's connection, created on first use."""
        conn: Optional[sqlite3.Connection] = getattr(self._local, 'conn', None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
            with self._lock:
                self._connections[threading.get_ident()] = conn
        return conn

    def execute(self, template: Template) -> sqlite3.Cursor:
        """Execute a PEP 750 SQL template on the calling thread's connection."""
        sql, params = render(template)
        return self.connection.execute(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """
        Serialized write transaction on the calling thread's connection.

        BEGIN IMMEDIATE takes the write lock up front so concurrent writers queue on
        `busy_timeout` instead of failing mid-transaction on lock upgrade.
        """
        conn = self.connection
        conn.execute('BEGIN IMMEDIATE')
        try:
            yield conn
        except BaseException:
            conn.execute('ROLLBACK')
            raise
        else:
            conn.execute('COMMIT')

    def close(self) -> None:
        """Close all connections (connections are not usable afterward)."""
        with self._lock:
            for conn in self._connections.values():
                conn.close()
            self._connections.clear()
        self._local = threading.local()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class ExclusiveLock:
    """
    Advisory exclusive lock on the state directory (flock on a lockfile).

    The mount daemon holds it for its lifetime; offline commands (audit, commit)
    require it so they never run against a live mount on the same host. Cross-host
    exclusion is governed by `overlay.shutdown_state` and the recorded mounting
    host, not by this lock (PLAN.md -> Deferred commit).
    """

    def __init__(self, path: str | Path) -> None:
        """Initialize with lockfile `path`; the lock is not acquired here."""
        self.path = Path(path)
        self._stream = None

    @property
    def held(self) -> bool:
        """Whether this instance currently holds the lock."""
        return self._stream is not None

    def acquire(self) -> None:
        """Take the lock or raise `DatabaseLocked` with the holder's identity."""
        if self._stream is not None:
            raise DatabaseLocked(f'Lock already held by this process ({self.path})')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(self.path, mode='a+')
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.seek(0)
            holder = stream.read().strip() or 'unknown'
            stream.close()
            raise DatabaseLocked(f'State locked by {holder} ({self.path})') from None
        stream.seek(0)
        stream.truncate()
        stream.write(f'{socket.gethostname()}:{os.getpid()}')
        stream.flush()
        self._stream = stream
        log.trace(f'Acquired exclusive lock ({self.path})')

    def release(self) -> None:
        """Release the lock if held."""
        if self._stream is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None
            log.trace(f'Released exclusive lock ({self.path})')

    def __enter__(self) -> ExclusiveLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
