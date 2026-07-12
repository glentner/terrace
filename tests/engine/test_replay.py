# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Characterization tests for write-back namespace-replay divergence (ROADMAP Phase 8).

The write-back queue is entry-keyed and namespace ops replay at the *historical* path
each was queued against (the Phase 7 fix; see `test_namespace.test_flush_replays_renames_
at_historical_paths`). A residual gap remains, documented in PLAN.md -> Risks: structurally
mutating an entry that still carries an *uncommitted* deferred rename cancels that rename
(`Journal.cancel` is entry-keyed) while the source copy still sits at its pre-rename path —
so the flush orphans it. The correct source tree after quiesce + flush is the oracle's; each
test below asserts that and is `xfail(strict)` until the hardening lands (items 2-3), at which
point the strict marker forces its removal and the test becomes a live regression guard.
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


# Every scenario below diverges through the same root cause: a deferred rename's physical
# source location survives only in the (now-cancelled) rename op, so its source copy is
# orphaned at the pre-rename path once the entry is removed or the rename superseded.
writeback_hardening = mark.xfail(
    reason='ROADMAP Phase 8 — deferred rename/remove replay orphans the source copy at its '
           'pre-rename path (PLAN.md -> Risks)',
    strict=True)


@writeback_hardening
def test_unlink_of_pending_rename_orphans_source_copy(engine: OverlayEngine) -> None:
    """Unlinking a file whose deferred rename has not committed must not orphan its source copy."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'x.dat', b'payload')
    engine.getattr('/x.dat')                                 # adopt the source file
    engine.rename('/x.dat', '/y.dat')                        # deferred: the source still holds /x.dat
    engine.unlink('/y.dat')                                  # removes the entry that owns the pending rename
    engine.flush()
    assert not (root / 'y.dat').exists()
    assert not (root / 'x.dat').exists()                     # orphaned at the pre-rename path today
    assert engine.journal.queued() == [] and engine.journal.pending() == []


@writeback_hardening
def test_rmdir_of_pending_rename_orphans_source_dir(engine: OverlayEngine) -> None:
    """rmdir of a directory whose deferred rename has not committed must not orphan the source dir."""
    root = Path(engine.spec.tiers[0].path)
    (root / 'd1').mkdir()                                    # an empty source directory
    engine.getattr('/d1')                                    # adopt it
    engine.rename('/d1', '/d2')                              # deferred: the source still holds /d1
    engine.rmdir('/d2')
    engine.flush()
    assert not (root / 'd2').exists()
    assert not (root / 'd1').exists()                        # orphaned at the pre-rename path today
    assert engine.journal.queued() == [] and engine.journal.pending() == []


@writeback_hardening
def test_rename_over_pending_target_orphans_source_copy(engine: OverlayEngine) -> None:
    """Renaming over a target that itself holds a pending rename must not orphan the target's source copy."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'a.dat', b'aaa')
    seed(root, 'b.dat', b'bbb')
    engine.getattr('/a.dat')
    engine.getattr('/b.dat')
    engine.rename('/a.dat', '/c.dat')                        # deferred: the source still holds /a.dat
    engine.rename('/b.dat', '/c.dat')                        # supersedes the pending /c.dat target
    engine.flush()
    assert not (root / 'a.dat').exists()                     # orphaned at the pre-rename path today
    assert not (root / 'b.dat').exists()
    assert (root / 'c.dat').read_bytes() == b'bbb'           # last writer wins
    assert engine.journal.queued() == [] and engine.journal.pending() == []


@writeback_hardening
def test_double_rename_then_unlink_orphans_source_copy(engine: OverlayEngine) -> None:
    """Chaining two deferred renames then unlinking must not orphan the original source copy."""
    root = Path(engine.spec.tiers[0].path)
    seed(root, 'a.dat', b'aaa')
    engine.getattr('/a.dat')
    engine.rename('/a.dat', '/b.dat')                        # deferred: the source still holds /a.dat
    engine.rename('/b.dat', '/c.dat')                        # deferred again; both ops key the same entry
    engine.unlink('/c.dat')                                  # cancels both renames
    engine.flush()
    assert not (root / 'a.dat').exists()                     # orphaned at the pre-rename path today
    assert not (root / 'b.dat').exists()
    assert not (root / 'c.dat').exists()
    assert engine.journal.queued() == [] and engine.journal.pending() == []
