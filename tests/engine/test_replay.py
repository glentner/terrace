# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Regression tests for write-back namespace-replay under deferred path mutation
(PLAN.md -> Write-back -> Deferred-op replay).

The write-back queue is entry-keyed and the source lags the catalog while a rename is queued
but uncommitted, so the source physically holds an entry's bytes at a path that differs from
its logical one. Each entry tracks that committed source path; deferred removals and renames
resolve against it, so structurally removing or superseding an entry that still carries an
uncommitted rename removes or moves the *real* source object instead of orphaning it at the
pre-rename path. Each test drives one such divergence class and asserts the source tree equals
the oracle's after quiesce + flush.
"""

# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path

# External libs
from pytest import mark

# Internal libs
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import seed


pytestmark = mark.unit


def test_unlink_of_pending_rename_removes_source_copy(engine: OverlayEngine) -> None:
    """Unlinking a file whose deferred rename has not committed removes its source copy, no orphan."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'payload')
    engine.getattr('/x.dat')                                 # adopt the source file
    engine.rename('/x.dat', '/y.dat')                        # deferred: the source still holds /x.dat
    engine.unlink('/y.dat')                                  # removes the entry that owns the pending rename
    engine.flush()
    assert not (root / 'y.dat').exists()
    assert not (root / 'x.dat').exists()                     # the pre-rename source copy is removed, not orphaned
    assert engine.journal.queued() == [] and engine.journal.pending() == []


def test_rmdir_of_pending_rename_removes_source_dir(engine: OverlayEngine) -> None:
    """rmdir of a directory whose deferred rename has not committed removes the source dir, no orphan."""
    root = Path(engine.spec.tiers[0].path)
    (root / 'd1').mkdir()                                    # an empty source directory
    engine.getattr('/d1')                                    # adopt it
    engine.rename('/d1', '/d2')                              # deferred: the source still holds /d1
    engine.rmdir('/d2')
    engine.flush()
    assert not (root / 'd2').exists()
    assert not (root / 'd1').exists()                        # the pre-rename source dir is removed, not orphaned
    assert engine.journal.queued() == [] and engine.journal.pending() == []


def test_rename_over_pending_target_removes_stale_source_copy(engine: OverlayEngine) -> None:
    """Renaming over a target that itself holds a pending rename removes the target's stale source copy."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'a.dat', b'aaa')
    seed(root, 'b.dat', b'bbb')
    engine.getattr('/a.dat')
    engine.getattr('/b.dat')
    engine.rename('/a.dat', '/c.dat')                        # deferred: the source still holds /a.dat
    engine.rename('/b.dat', '/c.dat')                        # supersedes the pending /c.dat target
    engine.flush()
    assert not (root / 'a.dat').exists()                     # the superseded source copy is removed, not orphaned
    assert not (root / 'b.dat').exists()
    assert (root / 'c.dat').read_bytes() == b'bbb'           # last writer wins
    assert engine.journal.queued() == [] and engine.journal.pending() == []


def test_double_rename_then_unlink_removes_source_copy(engine: OverlayEngine) -> None:
    """Chaining two deferred renames then unlinking removes the original source copy, no orphan."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'a.dat', b'aaa')
    engine.getattr('/a.dat')
    engine.rename('/a.dat', '/b.dat')                        # deferred: the source still holds /a.dat
    engine.rename('/b.dat', '/c.dat')                        # deferred again; both ops key the same entry
    engine.unlink('/c.dat')                                  # cancels both renames
    engine.flush()
    assert not (root / 'a.dat').exists()                     # the original source copy is removed, not orphaned
    assert not (root / 'b.dat').exists()
    assert not (root / 'c.dat').exists()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
