# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for intent-journaled transfers between POSIX tiers."""

# Type annotations
from __future__ import annotations
from typing import Tuple

# Standard libs
import io
from pathlib import Path, PurePosixPath

# External libs
from pytest import fixture, mark, raises

# Internal libs
from terrace.state.db import Database
from terrace.state.catalog import Catalog
from terrace.state.journal import Journal
from terrace.state.registry import TierRegistry
from terrace.tier.protocol import FileMeta, TierError, TierUsage
from terrace.tier.posix import PosixTier
from terrace.engine.transfer import TransferPool, materialize_dirs


pytestmark = mark.unit


@fixture
def site(tmp_path: Path) -> Tuple[Database, PosixTier, PosixTier]:
    """An initialized database and two registered tiers, with '/deep/data.h5' on the source."""
    db = Database(tmp_path / 'state.db')
    db.initialize()
    registry = TierRegistry(db)
    registry.register('project', position=0, type='posix', role='source', scope='shared')
    registry.register('scratch', position=1, type='posix', role='stage', scope='shared')
    (tmp_path / 'project').mkdir()
    (tmp_path / 'scratch').mkdir()
    source = PosixTier('project', tmp_path / 'project')
    dest = PosixTier('scratch', tmp_path / 'scratch')

    catalog = Catalog(db)
    catalog.add('/deep', 'dir', mode=0o750, uid=0, gid=0, mtime=1.0, ctime=1.0, atime=1.0)
    catalog.add('/deep/data.h5', 'file', mode=0o644, uid=0, gid=0, mtime=2.0, ctime=2.0, atime=2.0, size=7)
    source.mkdir(PurePosixPath('/deep'), 0o750)
    source.put(PurePosixPath('/deep/data.h5'), io.BytesIO(b'payload'),
               meta=FileMeta(size=7, mtime=2.0, mode=0o644))
    catalog.add_residency('/deep/data.h5', 'project', atime=2.0, size=7, mtime=2.0)
    registry.adjust_usage('project', bytes=7, files=1)

    yield db, source, dest
    db.close()


def test_copy_promotes_with_metadata(site) -> None:
    """A copy lands whole on the destination with meta, residency, and counters recorded."""
    db, source, dest = site
    with TransferPool(db) as pool:
        residency = pool.copy('/deep/data.h5', source, dest, action='promote', now=10.0)
    assert dest.read(PurePosixPath('/deep/data.h5')) == b'payload'
    stat = dest.stat(PurePosixPath('/deep/data.h5'))
    assert (stat.mode, stat.mtime) == (0o644, 2.0)
    assert (residency.tier, residency.state, residency.atime) == ('scratch', 'clean', 10.0)
    assert TierRegistry(db).usage('scratch') == TierUsage(bytes=7, files=1)
    assert Journal(db).pending() == []


def test_copy_materializes_ancestors_with_catalog_modes(site) -> None:
    """Missing destination directories are created top-down with the catalog's modes."""
    db, source, dest = site
    with TransferPool(db) as pool:
        pool.copy('/deep/data.h5', source, dest, action='promote', now=10.0)
    assert dest.stat(PurePosixPath('/deep')).mode == 0o750


def test_copy_replacement_adjusts_delta(site) -> None:
    """Re-copying a changed file adjusts counters by the size delta, not double-counting."""
    db, source, dest = site
    with TransferPool(db) as pool:
        pool.copy('/deep/data.h5', source, dest, action='promote', now=10.0)
        source.put(PurePosixPath('/deep/data.h5'), io.BytesIO(b'bigger payload'),
                   meta=FileMeta(size=14, mtime=3.0, mode=0o644))
        pool.copy('/deep/data.h5', source, dest, action='promote', now=20.0)
    assert TierRegistry(db).usage('scratch') == TierUsage(bytes=14, files=1)
    assert Catalog(db).get_residency('/deep/data.h5', 'scratch').size == 14


def test_copy_failure_rolls_back_intent(site) -> None:
    """A failed act abandons the intent and records nothing."""
    db, source, dest = site
    source.delete(PurePosixPath('/deep/data.h5'))
    with TransferPool(db) as pool:
        with raises(TierError):
            pool.copy('/deep/data.h5', source, dest, action='promote', now=10.0)
    assert Journal(db).pending() == []
    assert Catalog(db).get_residency('/deep/data.h5', 'scratch') is None
    assert TierRegistry(db).usage('scratch') == TierUsage(bytes=0, files=0)


def test_copy_unknown_entry_refused(site) -> None:
    """Only cataloged files transfer — managed locations hold no mystery bytes."""
    db, source, dest = site
    with TransferPool(db) as pool:
        with raises(LookupError):
            pool.copy('/not/cataloged', source, dest, action='promote', now=10.0)


def test_submit_runs_on_worker(site) -> None:
    """submit executes the same copy on a pool thread."""
    db, source, dest = site
    with TransferPool(db, workers=2) as pool:
        future = pool.submit('/deep/data.h5', source, dest, action='promote', now=10.0)
        residency = future.result(timeout=30)
    assert residency.tier == 'scratch'
    assert dest.read(PurePosixPath('/deep/data.h5')) == b'payload'


def test_rescue_state_dirty(site) -> None:
    """A rescue hop records the destination copy dirty (newest data, not yet upstream)."""
    db, source, dest = site
    with TransferPool(db) as pool:
        residency = pool.copy('/deep/data.h5', source, dest, action='commit', now=10.0, state='dirty')
    assert residency.state == 'dirty'


def test_materialize_dirs_idempotent(site) -> None:
    """materialize_dirs tolerates racing creation and existing paths."""
    db, source, dest = site
    catalog = Catalog(db)
    materialize_dirs(catalog, dest, '/deep/data.h5')
    materialize_dirs(catalog, dest, '/deep/data.h5')
    assert dest.stat(PurePosixPath('/deep')).kind == 'dir'
