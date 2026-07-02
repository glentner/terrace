# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for WAL durability across process death (subprocess kill -9)."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
import sys
import signal
import subprocess
from pathlib import Path

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.state.db import Database
from sosfs.state.catalog import Catalog
from sosfs.state.journal import Journal, is_temp_name
from sosfs.state.lifecycle import Lifecycle, CrashDetected


pytestmark = mark.unit

UUID = 'a0000000-0000-0000-0000-000000000001'

# The victim: builds up committed state (or leaves a transaction open), reports
# readiness on stdout, then waits to be killed without any shutdown path.
CHILD = f'''
import signal, sys
from sosfs.state.db import Database
from sosfs.state.catalog import Catalog
from sosfs.state.journal import Journal
from sosfs.state.lifecycle import Lifecycle

path, mode = sys.argv[1], sys.argv[2]
db = Database(path)
db.initialize()
if mode == 'committed':
    lifecycle = Lifecycle(db)
    lifecycle.create('space', uuid='{UUID}', now=1.0)
    lifecycle.begin_mount('child-host', now=2.0)
    with db.transaction() as conn:
        conn.execute("INSERT INTO tier (name, position, type, role) VALUES ('project', 0, 'posix', 'source')")
        conn.execute("INSERT INTO tier (name, position, type) VALUES ('scratch', 1, 'posix')")
        conn.execute("INSERT INTO tier_usage (tier, size_bytes, file_count) VALUES ('scratch', 8, 1)")
        conn.execute("INSERT INTO archive_index (tier, member, kind, offset, size, mtime) "
                     "VALUES ('project', 'a.txt', 'file', 512, 100, 0.0)")
    catalog = Catalog(db)
    catalog.add('/data/x.bin', 'file', mode=0o644, uid=1000, gid=1000, size=8, mtime=1.0, ctime=1.0, atime=1.0)
    catalog.add_residency('/data/x.bin', 'scratch', atime=1.0, size=8, mtime=1.0)
    journal = Journal(db)
    journal.mark_dirty('/data/x.bin', 'scratch', to_tier='project', now=3.0)
    journal.begin('commit', 'project', path='/data/x.bin', temp_for='/data/x.bin', now=4.0)
elif mode == 'uncommitted':
    with db.transaction() as conn:
        conn.execute("INSERT INTO tier (name, position, type, role) VALUES ('project', 0, 'posix', 'source')")
    conn = db.connection
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("INSERT INTO entry (path, parent, kind, mode, uid, gid, mtime, ctime, atime) "
                 "VALUES ('/ghost', '/', 'file', 420, 0, 0, 0.0, 0.0, 0.0)")
print('ready', flush=True)
signal.pause()
'''


def crash_after(db_path: Path, mode: str) -> None:
    """Run the child scenario to its ready point, then SIGKILL it."""
    proc = subprocess.Popen([sys.executable, '-c', CHILD, str(db_path), mode],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.stdout.readline().strip() != 'ready':
        proc.kill()
        _, errors = proc.communicate()
        raise RuntimeError(f'Child died before ready:\n{errors}')
    os.kill(proc.pid, signal.SIGKILL)
    proc.communicate()


def test_committed_state_survives_sigkill(tmp_path: Path) -> None:
    """Every row committed before death — all eight tables — is present after recovery."""
    db_path = tmp_path / 'state.db'
    crash_after(db_path, 'committed')
    assert Path(f'{db_path}-wal').exists()  # the child never checkpointed; recovery reads the WAL
    with Database(db_path) as db:
        assert db.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        copy = Catalog(db).get_residency('/data/x.bin', 'scratch')
        assert copy.state == 'dirty' and copy.atime == 3.0
        journal = Journal(db)
        item, = journal.queued()
        assert (item.from_tier, item.to_tier, item.first_dirty) == ('scratch', 'project', 3.0)
        intent, = journal.pending()
        assert intent.action == 'commit' and is_temp_name(intent.temp_name)
        counts = db.connection.execute('SELECT size_bytes, file_count FROM tier_usage').fetchone()
        assert tuple(counts) == (8, 1)
        assert db.connection.execute('SELECT COUNT(*) FROM archive_index').fetchone()[0] == 1


def test_crash_detected_and_settled_after_sigkill(tmp_path: Path) -> None:
    """The lingering 'mounted' flag forces an audit; settling and resolution then proceed."""
    db_path = tmp_path / 'state.db'
    crash_after(db_path, 'committed')
    with Database(db_path) as db:
        lifecycle = Lifecycle(db)
        overlay = lifecycle.load()
        assert overlay.shutdown_state == 'mounted' and overlay.host == 'child-host'
        with raises(CrashDetected, match='child-host'):
            lifecycle.begin_mount('login01', now=10.0)
        # what the audit does: resolve intents, then settle the flag
        journal = Journal(db)
        intent, = journal.pending()
        journal.rollback(intent)
        lifecycle.end_mount('detached')
        assert journal.pending() == []
        assert lifecycle.begin_mount('login01', now=11.0).generation == 2


def test_uncommitted_transaction_vanishes(tmp_path: Path) -> None:
    """Work inside an open transaction at death is gone; the database is not wedged."""
    db_path = tmp_path / 'state.db'
    crash_after(db_path, 'uncommitted')
    with Database(db_path) as db:
        assert db.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.connection.execute('SELECT COUNT(*) FROM entry').fetchone()[0] == 0
        assert db.connection.execute('SELECT COUNT(*) FROM tier').fetchone()[0] == 1
        with db.transaction() as conn:  # the dead writer's lock died with it
            conn.execute("INSERT INTO tier (name, position, type) VALUES ('scratch', 1, 'posix')")
        assert db.connection.execute('SELECT COUNT(*) FROM tier').fetchone()[0] == 2
