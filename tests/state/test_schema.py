# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the state store schema."""

# Type annotations
from __future__ import annotations

# Standard libs
import sqlite3
from importlib.resources import files

# External libs
from pytest import fixture, mark, raises

# Internal libs
import terrace.state


pytestmark = mark.unit

SCHEMA = (files(terrace.state) / 'schema.sql').read_text()

TABLES = {'overlay', 'tier', 'tier_usage', 'entry', 'residency', 'writeback', 'intent', 'archive_index'}


@fixture
def db() -> sqlite3.Connection:
    """In-memory database with the schema applied and foreign keys enforced."""
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO tier (name, position, type, role) VALUES ('project', 0, 'posix', 'source')")
    conn.execute("INSERT INTO tier (name, position, type, scope) VALUES ('shm', 1, 'posix', 'node')")
    conn.execute("INSERT INTO entry (path, parent, kind, mode, uid, gid, mtime, ctime, atime) "
                 "VALUES ('/data.bin', '/', 'file', 420, 1000, 1000, 0.0, 0.0, 0.0)")
    return conn


def test_tables(db: sqlite3.Connection) -> None:
    """All tables from the design exist."""
    found = {name for name, in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert TABLES <= found


def test_user_version(db: sqlite3.Connection) -> None:
    """Schema version is stamped."""
    assert db.execute('PRAGMA user_version').fetchone()[0] == 2


def test_overlay_single_row(db: sqlite3.Connection) -> None:
    """The overlay table admits exactly one row (id = 1)."""
    db.execute("INSERT INTO overlay (id, uuid, name, created) VALUES (1, 'u', 'space', 0.0)")
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO overlay (id, uuid, name, created) VALUES (2, 'u', 'other', 0.0)")


def test_overlay_shutdown_state(db: sqlite3.Connection) -> None:
    """Only the three lifecycle states are admissible."""
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO overlay (id, uuid, name, created, shutdown_state) "
                   "VALUES (1, 'u', 'space', 0.0, 'crashed')")


def test_tier_role_and_scope_checks(db: sqlite3.Connection) -> None:
    """Role and scope values are constrained."""
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO tier (name, position, type, role) VALUES ('x', 2, 'posix', 'cache')")
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO tier (name, position, type, scope) VALUES ('x', 2, 'posix', 'global')")


def test_tier_position_unique(db: sqlite3.Connection) -> None:
    """Stack positions cannot collide."""
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO tier (name, position, type) VALUES ('other', 0, 'posix')")


def test_residency_references_and_states(db: sqlite3.Connection) -> None:
    """Residency requires a real entry and tier; state is constrained; duplicates rejected."""
    db.execute("INSERT INTO residency (entry, tier, atime, size, mtime) VALUES (1, 'shm', 0.0, 10, 0.0)")
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO residency (entry, tier, atime, size, mtime) VALUES (1, 'shm', 0.0, 10, 0.0)")
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO residency (entry, tier, atime, size, mtime) VALUES (1, 'nope', 0.0, 10, 0.0)")
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO residency (entry, tier, state, atime, size, mtime) "
                   "VALUES (1, 'project', 'stale', 0.0, 10, 0.0)")


def test_entry_delete_cascades(db: sqlite3.Connection) -> None:
    """Deleting an entry removes its residency, writeback, and intent rows."""
    db.execute("INSERT INTO residency (entry, tier, atime, size, mtime) VALUES (1, 'shm', 0.0, 10, 0.0)")
    db.execute("INSERT INTO writeback (entry, op, from_tier, to_tier, first_dirty, last_write) "
               "VALUES (1, 'data', 'shm', 'project', 0.0, 0.0)")
    db.execute("INSERT INTO intent (action, entry, tier, created) VALUES ('commit', 1, 'project', 0.0)")
    db.execute("DELETE FROM entry WHERE id = 1")
    assert db.execute('SELECT COUNT(*) FROM residency').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM writeback').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM intent').fetchone()[0] == 0


def test_writeback_op_check(db: sqlite3.Connection) -> None:
    """Only known write-back operations are admissible."""
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO writeback (entry, op, to_tier, first_dirty, last_write) "
                   "VALUES (1, 'transmogrify', 'project', 0.0, 0.0)")


def test_intent_action_check(db: sqlite3.Connection) -> None:
    """Only known intent actions are admissible; tier-level intents need no entry."""
    db.execute("INSERT INTO intent (action, tier, created) VALUES ('resync', 'project', 0.0)")
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO intent (action, tier, created) VALUES ('explode', 'project', 0.0)")


def test_archive_index_member_unique(db: sqlite3.Connection) -> None:
    """One index row per (tier, member)."""
    db.execute("INSERT INTO archive_index (tier, member, kind, offset, size, mtime) "
               "VALUES ('project', 'a/b.txt', 'file', 512, 100, 0.0)")
    with raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO archive_index (tier, member, kind, offset, size, mtime) "
                   "VALUES ('project', 'a/b.txt', 'file', 1024, 200, 0.0)")
