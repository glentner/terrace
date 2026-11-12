# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for overlay lifecycle transitions and managed-location markers."""

# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path

# External libs
from pytest import fixture, mark, raises

# Internal libs
from terrace.state.db import Database
from terrace.state.lifecycle import (Lifecycle, Marker, MarkerStatus, verify_marker,
                                   LifecycleError, CrashDetected, MarkerError)


pytestmark = mark.unit

UUID = 'a0000000-0000-0000-0000-000000000001'


@fixture
def lifecycle(tmp_path: Path) -> Lifecycle:
    """Lifecycle over an initialized database."""
    db = Database(tmp_path / 'state.db')
    db.initialize()
    yield Lifecycle(db)
    db.close()


def test_create_and_load(lifecycle: Lifecycle) -> None:
    """The overlay row round-trips; a fresh database has none."""
    assert lifecycle.load() is None
    overlay = lifecycle.create('myspace', uuid=UUID, now=100.0)
    assert (overlay.name, overlay.uuid, overlay.shutdown_state) == ('myspace', UUID, 'clean')
    assert overlay.generation == 0 and overlay.host is None
    assert lifecycle.load() == overlay


def test_create_twice_rejected(lifecycle: Lifecycle) -> None:
    """The overlay row is created exactly once."""
    lifecycle.create('myspace', uuid=UUID, now=100.0)
    with raises(LifecycleError):
        lifecycle.create('other', uuid=UUID, now=200.0)


def test_begin_mount(lifecycle: Lifecycle) -> None:
    """Mounting bumps the generation and records host and time."""
    lifecycle.create('myspace', uuid=UUID, now=100.0)
    overlay = lifecycle.begin_mount('node042', now=200.0)
    assert overlay.shutdown_state == 'mounted'
    assert overlay.host == 'node042'
    assert overlay.generation == 1
    assert overlay.last_mounted == 200.0


def test_begin_mount_requires_overlay(lifecycle: Lifecycle) -> None:
    """Mounting an uninitialized overlay fails."""
    with raises(LifecycleError):
        lifecycle.begin_mount('node042', now=200.0)


def test_crash_detection(lifecycle: Lifecycle) -> None:
    """A lingering 'mounted' state is a crash; the prior host is reported."""
    lifecycle.create('myspace', uuid=UUID, now=100.0)
    lifecycle.begin_mount('node042', now=200.0)
    with raises(CrashDetected, match='node042'):
        lifecycle.begin_mount('login01', now=300.0)


def test_end_mount_clean_and_detached(lifecycle: Lifecycle) -> None:
    """The full cycle: clean -> mounted -> detached -> mounted -> clean."""
    lifecycle.create('myspace', uuid=UUID, now=100.0)
    lifecycle.begin_mount('node042', now=200.0)
    assert lifecycle.end_mount('detached').shutdown_state == 'detached'
    overlay = lifecycle.begin_mount('node043', now=300.0)
    assert overlay.generation == 2
    assert lifecycle.end_mount('clean').shutdown_state == 'clean'


def test_end_mount_validates(lifecycle: Lifecycle) -> None:
    """end_mount rejects bad states and non-mounted transitions."""
    lifecycle.create('myspace', uuid=UUID, now=100.0)
    with raises(ValueError):
        lifecycle.end_mount('crashed')
    with raises(LifecycleError, match='clean'):
        lifecycle.end_mount('detached')


def test_audit_settles_crash(lifecycle: Lifecycle) -> None:
    """After a crash, end_mount('detached') is the audit's settling transition."""
    lifecycle.create('myspace', uuid=UUID, now=100.0)
    lifecycle.begin_mount('node042', now=200.0)
    # (process dies here; next actor finds 'mounted' and audits)
    assert lifecycle.load().shutdown_state == 'mounted'
    lifecycle.end_mount('detached')
    assert lifecycle.begin_mount('login01', now=400.0).shutdown_state == 'mounted'


def test_marker_roundtrip(tmp_path: Path) -> None:
    """Markers write atomically and read back equal."""
    marker = Marker(uuid=UUID, tier='scratch', generation=3, written=123.5)
    marker.write(tmp_path)
    assert (tmp_path / '.terrace' / 'marker').exists()
    assert not (tmp_path / '.terrace' / 'marker.partial').exists()
    assert Marker.read(tmp_path) == marker


def test_marker_missing(tmp_path: Path) -> None:
    """A bare root has no marker."""
    assert Marker.read(tmp_path) is None
    assert verify_marker(tmp_path, uuid=UUID, tier='scratch', generation=1) is MarkerStatus.MISSING


def test_marker_corrupt(tmp_path: Path) -> None:
    """Unparseable markers raise on read and report CORRUPT on verify."""
    (tmp_path / '.terrace').mkdir()
    (tmp_path / '.terrace' / 'marker').write_text('not = valid = toml\n')
    with raises(MarkerError):
        Marker.read(tmp_path)
    assert verify_marker(tmp_path, uuid=UUID, tier='scratch', generation=1) is MarkerStatus.CORRUPT


def test_marker_verification_statuses(tmp_path: Path) -> None:
    """Each mismatch is distinguished."""
    Marker(uuid=UUID, tier='scratch', generation=3, written=1.0).write(tmp_path)
    check = lambda **kw: verify_marker(tmp_path, **kw)  # noqa: E731
    assert check(uuid=UUID, tier='scratch', generation=3) is MarkerStatus.OK
    assert check(uuid='b000', tier='scratch', generation=3) is MarkerStatus.FOREIGN
    assert check(uuid=UUID, tier='tmp', generation=3) is MarkerStatus.TIER_MISMATCH
    assert check(uuid=UUID, tier='scratch', generation=4) is MarkerStatus.GENERATION_MISMATCH


def test_marker_rewrite_supersedes(tmp_path: Path) -> None:
    """Re-initializing a tier overwrites the marker in place."""
    Marker(uuid=UUID, tier='scratch', generation=1, written=1.0).write(tmp_path)
    Marker(uuid=UUID, tier='scratch', generation=2, written=2.0).write(tmp_path)
    assert Marker.read(tmp_path).generation == 2
