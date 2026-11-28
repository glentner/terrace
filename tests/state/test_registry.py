# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the tier registry, usage counters, and marker initialization on attach."""

# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path

# External libs
from pytest import fixture, mark, raises

# Internal libs
from terrace.state.db import Database
from terrace.state.lifecycle import Marker, MarkerStatus
from terrace.state.registry import TierRegistry, SpecDrift
from terrace.tier.protocol import TierUsage


pytestmark = mark.unit

UUID = 'a0000000-0000-0000-0000-000000000001'
OTHER = 'b0000000-0000-0000-0000-000000000002'


@fixture
def db(tmp_path: Path) -> Database:
    """An initialized state database."""
    database = Database(tmp_path / 'state.db')
    database.initialize()
    yield database
    database.close()


@fixture
def registry(db: Database) -> TierRegistry:
    """A registry over the database."""
    return TierRegistry(db)


def test_register_first_attach(registry: TierRegistry) -> None:
    """First registration inserts the row with zeroed counters."""
    record, new = registry.register('scratch', position=1, type='posix', role='stage', scope='shared')
    assert new is True
    assert (record.name, record.position, record.generation) == ('scratch', 1, 0)
    assert registry.usage('scratch') == TierUsage(bytes=0, files=0)


def test_register_reattach_verifies(registry: TierRegistry) -> None:
    """Re-registration with the same identity is idempotent; any change is drift."""
    registry.register('scratch', position=1, type='posix', role='stage', scope='shared')
    record, new = registry.register('scratch', position=1, type='posix', role='stage', scope='shared')
    assert new is False and record.name == 'scratch'
    for drifted in (dict(position=2, type='posix', role='stage', scope='shared'),
                    dict(position=1, type='s3', role='stage', scope='shared'),
                    dict(position=1, type='posix', role='source', scope='shared'),
                    dict(position=1, type='posix', role='stage', scope='node')):
        with raises(SpecDrift):
            registry.register('scratch', **drifted)


def test_tiers_in_stack_order(registry: TierRegistry) -> None:
    """tiers() returns source first regardless of registration order."""
    registry.register('shm', position=2, type='posix', role='stage', scope='node')
    registry.register('project', position=0, type='posix', role='source', scope='shared')
    registry.register('scratch', position=1, type='posix', role='stage', scope='shared')
    assert [record.name for record in registry.tiers()] == ['project', 'scratch', 'shm']
    assert registry.get('shm').scope == 'node'
    assert registry.get('absent') is None


def test_adjust_usage(registry: TierRegistry) -> None:
    """Deltas accumulate; negative deltas apply on delete."""
    registry.register('scratch', position=1, type='posix', role='stage', scope='shared')
    assert registry.adjust_usage('scratch', bytes=1000, files=1) == TierUsage(bytes=1000, files=1)
    assert registry.adjust_usage('scratch', bytes=500, files=1) == TierUsage(bytes=1500, files=2)
    assert registry.adjust_usage('scratch', bytes=-1000, files=-1) == TierUsage(bytes=500, files=1)
    with raises(LookupError):
        registry.adjust_usage('absent', bytes=1)


def test_set_usage(registry: TierRegistry) -> None:
    """set_usage overwrites — the audit's counter rebuild."""
    registry.register('scratch', position=1, type='posix', role='stage', scope='shared')
    registry.adjust_usage('scratch', bytes=999, files=9)
    assert registry.set_usage('scratch', bytes=42, files=1) == TierUsage(bytes=42, files=1)
    assert registry.usage('scratch') == TierUsage(bytes=42, files=1)


def test_bump_generation(registry: TierRegistry) -> None:
    """Generation advances when a managed location is re-initialized."""
    registry.register('scratch', position=1, type='posix', role='stage', scope='shared')
    assert registry.bump_generation('scratch').generation == 1
    with raises(LookupError):
        registry.bump_generation('absent')


def test_attach_initializes_marker(registry: TierRegistry, tmp_path: Path) -> None:
    """First attach writes the marker; the location is now owned by this overlay."""
    root = tmp_path / 'scratch'
    root.mkdir()
    record, status = registry.attach('scratch', position=1, type='posix', role='stage', scope='shared',
                                     root=root, uuid=UUID, now=100.0)
    assert status is MarkerStatus.OK and record.generation == 0
    marker = Marker.read(root)
    assert (marker.uuid, marker.tier, marker.generation) == (UUID, 'scratch', 0)


def test_attach_verifies_on_reattach(registry: TierRegistry, tmp_path: Path) -> None:
    """Re-attach verifies the existing marker instead of rewriting it."""
    root = tmp_path / 'scratch'
    root.mkdir()
    registry.attach('scratch', position=1, type='posix', role='stage', scope='shared',
                    root=root, uuid=UUID, now=100.0)
    record, status = registry.attach('scratch', position=1, type='posix', role='stage', scope='shared',
                                     root=root, uuid=UUID, now=200.0)
    assert status is MarkerStatus.OK
    assert Marker.read(root).written == 100.0


def test_attach_detects_evaporation(registry: TierRegistry, tmp_path: Path) -> None:
    """A registered tier whose marker vanished reads MISSING — never silently rewritten."""
    root = tmp_path / 'shm'
    root.mkdir()
    registry.attach('shm', position=2, type='posix', role='stage', scope='node',
                    root=root, uuid=UUID, now=100.0)
    (root / '.terrace' / 'marker').unlink()
    record, status = registry.attach('shm', position=2, type='posix', role='stage', scope='node',
                                     root=root, uuid=UUID, now=200.0)
    assert status is MarkerStatus.MISSING
    assert Marker.read(root) is None


def test_attach_detects_foreign_and_corrupt(registry: TierRegistry, tmp_path: Path) -> None:
    """A marker from another overlay, or unparseable text, is reported not adopted."""
    root = tmp_path / 'scratch'
    root.mkdir()
    Marker(uuid=OTHER, tier='scratch', generation=0, written=50.0).write(root)
    record, status = registry.attach('scratch', position=1, type='posix', role='stage', scope='shared',
                                     root=root, uuid=UUID, now=100.0)
    assert status is MarkerStatus.FOREIGN
    (root / '.terrace' / 'marker').write_text('not = [valid')
    record, status = registry.attach('scratch', position=1, type='posix', role='stage', scope='shared',
                                     root=root, uuid=UUID, now=200.0)
    assert status is MarkerStatus.CORRUPT


def test_attach_detects_generation_mismatch(registry: TierRegistry, tmp_path: Path) -> None:
    """A marker generation ahead of the registry means the location was re-initialized elsewhere."""
    root = tmp_path / 'scratch'
    root.mkdir()
    registry.attach('scratch', position=1, type='posix', role='stage', scope='shared',
                    root=root, uuid=UUID, now=100.0)
    Marker(uuid=UUID, tier='scratch', generation=5, written=150.0).write(root)
    record, status = registry.attach('scratch', position=1, type='posix', role='stage', scope='shared',
                                     root=root, uuid=UUID, now=200.0)
    assert status is MarkerStatus.GENERATION_MISMATCH


ARCHIVE = {'location': '/data/space.zip', 'signature': [4096, 1700000000.25]}


def test_enlist_reads_ok_with_no_marker(registry: TierRegistry, tmp_path: Path) -> None:
    """A tier with no marker reads OK on its first and later registrations, records its identity, and writes nothing."""
    record, status = registry.enlist('archive', position=0, type='zip', role='source', scope='shared',
                                     meta=ARCHIVE)
    assert status is MarkerStatus.OK
    assert (record.name, record.type, record.meta) == ('archive', 'zip', ARCHIVE)
    record, status = registry.enlist('archive', position=0, type='zip', role='source', scope='shared',
                                     meta=dict(ARCHIVE))
    assert status is MarkerStatus.OK
    assert registry.get('archive').meta == ARCHIVE
    assert registry.usage('archive') == TierUsage(bytes=0, files=0)
    assert all(path.name.startswith('state.db') for path in tmp_path.iterdir())    # no marker written anywhere
    with raises(SpecDrift):
        registry.enlist('archive', position=0, type='tar', role='source', scope='shared', meta=ARCHIVE)


def test_enlist_refuses_changed_meta(registry: TierRegistry) -> None:
    """A later registration whose recorded location or signature differs is refused, naming the tier and archive."""
    registry.enlist('archive', position=0, type='zip', role='source', scope='shared', meta=ARCHIVE)
    moved = {**ARCHIVE, 'location': '/data/elsewhere.zip'}
    rewritten = {**ARCHIVE, 'signature': [4097, 1700000099.5]}
    for changed in (moved, rewritten):
        with raises(SpecDrift) as caught:
            registry.enlist('archive', position=0, type='zip', role='source', scope='shared', meta=changed)
        message = str(caught.value)
        assert "'archive'" in message and changed['location'] in message
        assert 'changed since this state directory recorded it' in message
    assert registry.get('archive').meta == ARCHIVE                # the refusal recorded nothing
