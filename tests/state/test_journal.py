# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the write-ordering invariant primitives."""

# Type annotations
from __future__ import annotations
from typing import Tuple

# Standard libs
from pathlib import Path

# External libs
from pytest import fixture, mark, raises

# Internal libs
from sosfs.state.db import Database
from sosfs.state.catalog import Catalog
from sosfs.state.journal import Journal, temp_name, is_temp_name


pytestmark = mark.unit


@fixture
def site(tmp_path: Path) -> Tuple[Database, Catalog, Journal]:
    """Database with a three-tier stack, plus catalog and journal over it."""
    db = Database(tmp_path / 'state.db')
    db.initialize()
    with db.transaction() as conn:
        conn.execute("INSERT INTO tier (name, position, type, role) VALUES ('project', 0, 'posix', 'source')")
        conn.execute("INSERT INTO tier (name, position, type) VALUES ('scratch', 1, 'posix')")
        conn.execute("INSERT INTO tier (name, position, type, scope) VALUES ('shm', 2, 'posix', 'node')")
    yield db, Catalog(db), Journal(db)
    db.close()


def resident_file(catalog: Catalog, path: str, tier: str = 'scratch') -> None:
    """Shorthand: a file entry with a clean copy in `tier`."""
    catalog.add(path, 'file', mode=0o644, uid=1000, gid=1000, size=8, mtime=1.0, ctime=1.0, atime=1.0)
    catalog.add_residency(path, tier, atime=1.0, size=8, mtime=1.0)


# -- metadata before acknowledgment -----------------------------------------------------------------------------


def test_mark_dirty_flips_and_queues(site) -> None:
    """The clean->dirty flip and the queue row land together."""
    db, catalog, journal = site
    resident_file(catalog, '/data/x.bin')
    item = journal.mark_dirty('/data/x.bin', 'scratch', to_tier='project', now=5.0)
    copy = catalog.get_residency('/data/x.bin', 'scratch')
    assert copy.state == 'dirty' and copy.atime == 5.0
    assert (item.op, item.from_tier, item.to_tier, item.state) == ('data', 'scratch', 'project', 'queued')
    assert item.first_dirty == item.last_write == 5.0 and item.last_commit is None
    assert journal.queued() == [item]


def test_mark_dirty_refreshes_existing_row(site) -> None:
    """A dirty copy refreshes last_write on the same row; first_dirty is preserved."""
    db, catalog, journal = site
    resident_file(catalog, '/data/x.bin')
    first = journal.mark_dirty('/data/x.bin', 'scratch', to_tier='project', now=5.0)
    again = journal.mark_dirty('/data/x.bin', 'scratch', to_tier='project', now=9.0)
    assert again.id == first.id
    assert again.first_dirty == 5.0 and again.last_write == 9.0
    assert len(journal.queued()) == 1
    assert catalog.get_residency('/data/x.bin', 'scratch').atime == 9.0


def test_mark_dirty_per_boundary_rows(site) -> None:
    """Each (entry, boundary) tracks its own queue row; filters select one boundary."""
    db, catalog, journal = site
    resident_file(catalog, '/data/x.bin', tier='shm')
    catalog.add_residency('/data/x.bin', 'scratch', atime=1.0, size=8, mtime=1.0)
    journal.mark_dirty('/data/x.bin', 'shm', to_tier='scratch', now=5.0)
    journal.mark_dirty('/data/x.bin', 'scratch', to_tier='project', now=6.0)
    assert len(journal.queued()) == 2
    assert [item.to_tier for item in journal.queued(to_tier='project')] == ['project']


def test_mark_dirty_requires_entry_and_copy(site) -> None:
    """Unknown paths and non-resident tiers are caller bugs."""
    db, catalog, journal = site
    with raises(LookupError, match='No such entry'):
        journal.mark_dirty('/missing', 'scratch', to_tier='project', now=5.0)
    resident_file(catalog, '/data/x.bin')
    with raises(LookupError, match='No copy'):
        journal.mark_dirty('/data/x.bin', 'shm', to_tier='scratch', now=5.0)


def test_mark_dirty_atomic_under_caller_transaction(site) -> None:
    """Joined into a failing caller transaction, neither the flip nor the row persist."""
    db, catalog, journal = site
    resident_file(catalog, '/data/x.bin')
    with raises(RuntimeError):
        with db.transaction():
            journal.mark_dirty('/data/x.bin', 'scratch', to_tier='project', now=5.0)
            raise RuntimeError('write not acknowledged')
    assert catalog.get_residency('/data/x.bin', 'scratch').state == 'clean'
    assert journal.queued() == []


# -- intent journal ----------------------------------------------------------------------------------------------


def test_begin_assigns_temp_name(site) -> None:
    """An intent with a destination path gets a recognizable temp name."""
    db, catalog, journal = site
    resident_file(catalog, '/data/x.bin')
    intent = journal.begin('promote', 'shm', path='/data/x.bin', temp_for='/data/x.bin', now=5.0)
    assert intent.action == 'promote' and intent.tier == 'shm' and intent.created == 5.0
    assert intent.temp_name == temp_name(intent.id, '/data/x.bin')
    assert is_temp_name(intent.temp_name)
    assert journal.pending() == [intent]


def test_begin_tier_level_with_detail(site) -> None:
    """Tier-level actions carry no entry; detail round-trips as JSON."""
    db, catalog, journal = site
    intent = journal.begin('resync', 'scratch', detail={'members': 3}, now=5.0)
    assert intent.entry is None and intent.temp_name is None
    assert journal.pending()[0].detail == {'members': 3}


def test_begin_validates(site) -> None:
    """Unknown actions and unknown paths are rejected before any row lands."""
    db, catalog, journal = site
    with raises(ValueError, match='Unknown intent action'):
        journal.begin('shred', 'scratch', now=5.0)
    with raises(LookupError, match='No such entry'):
        journal.begin('promote', 'scratch', path='/missing', now=5.0)
    assert journal.pending() == []


def test_confirm_and_rollback_resolve(site) -> None:
    """Confirming or rolling back deletes the row; resolving twice is a caller bug."""
    db, catalog, journal = site
    done = journal.begin('resync', 'scratch', now=5.0)
    undone = journal.begin('compact', 'scratch', now=6.0)
    journal.confirm(done)
    journal.rollback(undone)
    assert journal.pending() == []
    with raises(LookupError, match='No such intent'):
        journal.confirm(done)


def test_confirm_composes_with_metadata(site) -> None:
    """Intent resolution and the act's recorded effect commit together or not at all."""
    db, catalog, journal = site
    resident_file(catalog, '/data/x.bin')
    intent = journal.begin('promote', 'shm', path='/data/x.bin', temp_for='/data/x.bin', now=5.0)
    with raises(RuntimeError):
        with db.transaction():
            catalog.add_residency('/data/x.bin', 'shm', atime=6.0, size=8, mtime=1.0)
            journal.confirm(intent)
            raise RuntimeError('crash before commit')
    assert catalog.get_residency('/data/x.bin', 'shm') is None
    assert journal.pending() == [intent]
    with db.transaction():
        catalog.add_residency('/data/x.bin', 'shm', atime=6.0, size=8, mtime=1.0)
        journal.confirm(intent)
    assert catalog.get_residency('/data/x.bin', 'shm') is not None
    assert journal.pending() == []


def test_pending_scope_and_order(site) -> None:
    """Pending intents come back in creation order, optionally scoped to one tier."""
    db, catalog, journal = site
    journal.begin('resync', 'scratch', now=5.0)
    journal.begin('compact', 'shm', now=6.0)
    journal.begin('evict', 'scratch', now=7.0)
    assert [intent.action for intent in journal.pending()] == ['resync', 'compact', 'evict']
    assert [intent.action for intent in journal.pending(tier='scratch')] == ['resync', 'evict']


def test_temp_name_convention() -> None:
    """Temp names are tier-relative, unique per intent, and recognizable."""
    assert temp_name(3, '/data/x.bin') == '.sosfs/partial/3-x.bin'
    assert is_temp_name('.sosfs/partial/3-x.bin')
    assert not is_temp_name('data/x.bin')
    assert not is_temp_name('.sosfs/marker')
