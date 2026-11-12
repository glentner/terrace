# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the state database layer."""

# Type annotations
from __future__ import annotations

# Standard libs
import sqlite3
import threading
from pathlib import Path

# External libs
from pytest import fixture, mark, raises

# Internal libs
from terrace.state.db import Database, ExclusiveLock, SCHEMA_VERSION, DatabaseLocked, DatabaseUninitialized, \
    SchemaMismatch


pytestmark = mark.unit


@fixture
def db(tmp_path: Path) -> Database:
    """An initialized database in a tmp directory."""
    database = Database(tmp_path / 'state.db')
    database.initialize()
    yield database
    database.close()


def test_initialize_creates_schema(db: Database) -> None:
    """Initialization stamps the version and applies WAL + pragmas."""
    conn = db.connection
    assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
    assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert conn.execute('PRAGMA synchronous').fetchone()[0] == 1  # NORMAL
    assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_initialize_idempotent(db: Database) -> None:
    """Re-initialization of an existing database is a no-op."""
    db.initialize()
    assert db.connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_uninitialized_rejected(tmp_path: Path) -> None:
    """Connecting to an uninitialized database raises."""
    database = Database(tmp_path / 'state.db')
    with raises(DatabaseUninitialized):
        _ = database.connection


def test_version_mismatch_rejected(tmp_path: Path) -> None:
    """A database from a different schema version is refused."""
    path = tmp_path / 'state.db'
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA user_version = 99')
    conn.close()
    database = Database(path)
    with raises(SchemaMismatch):
        database.initialize()
    with raises(SchemaMismatch):
        _ = database.connection


def test_per_thread_connections(db: Database) -> None:
    """Each thread gets its own connection object."""
    main_conn = db.connection
    seen = {}

    def worker() -> None:
        seen['conn'] = db.connection

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen['conn'] is not main_conn
    assert db.connection is main_conn


def test_transaction_commit_and_rollback(db: Database) -> None:
    """Transactions commit on success and roll back on error."""
    with db.transaction() as conn:
        conn.execute("INSERT INTO tier (name, position, type, role) VALUES ('project', 0, 'posix', 'source')")
    with raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO tier (name, position, type) VALUES ('scratch', 1, 'posix')")
            raise RuntimeError('abort')
    names = [row['name'] for row in db.connection.execute('SELECT name FROM tier')]
    assert names == ['project']


def test_concurrent_writers(db: Database) -> None:
    """Threaded writers serialize on BEGIN IMMEDIATE without erroring."""
    with db.transaction() as conn:
        conn.execute("INSERT INTO tier (name, position, type) VALUES ('shm', 1, 'posix')")

    def worker(offset: int) -> None:
        for i in range(25):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO entry (path, parent, kind, mode, uid, gid, mtime, ctime, atime) "
                    "VALUES (?, '/', 'file', 420, 0, 0, 0.0, 0.0, 0.0)", (f'/file-{offset}-{i}',))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert db.connection.execute('SELECT COUNT(*) FROM entry').fetchone()[0] == 100


def test_exclusive_lock(tmp_path: Path) -> None:
    """Second acquisition fails with holder identity; release allows reacquire."""
    path = tmp_path / 'lock'
    first = ExclusiveLock(path)
    first.acquire()
    assert first.held
    second = ExclusiveLock(path)
    with raises(DatabaseLocked, match=r':\d+'):  # host:pid recorded
        second.acquire()
    first.release()
    assert not first.held
    with second:
        assert second.held
    assert not second.held


def test_exclusive_lock_reentry_rejected(tmp_path: Path) -> None:
    """The same instance cannot acquire twice."""
    lock = ExclusiveLock(tmp_path / 'lock')
    lock.acquire()
    with raises(DatabaseLocked):
        lock.acquire()
    lock.release()
