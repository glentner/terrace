# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for rescue: node-local worklist, hop to nearest shared tier, time-budget cutoff."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark

# Internal libs
from terrace.tier.spec import OverlaySpec, TierSpec, QuotaSpec, WritebackSpec
from terrace.engine.overlay import OverlayEngine
from terrace.engine.rescue import rescue, node_dirty
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


def make_engine(tmp_path: Path, clock: Clock, tiers: int = 4, seeds=(), **kwargs) -> OverlayEngine:
    """A started engine over a source pre-seeded with `(path, size)` files (paths overlay-absolute)."""
    for path, size in seeds:
        seed(tmp_path / 'project', path.lstrip('/'), b'.' * size)
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers, **kwargs), clock=clock)
    engine.start()
    return engine


def node_stack(tmp_path: Path, clock: Clock, seeds=()) -> OverlayEngine:
    """A started engine whose only stage is node-scoped: project(source) <- local(node)."""
    for path, size in seeds:
        seed(tmp_path / 'project', path.lstrip('/'), b'.' * size)
    (tmp_path / 'project').mkdir(exist_ok=True)
    source = TierSpec(name='project', type='posix', path=str(tmp_path / 'project'), role='source')
    local = TierSpec(name='local', type='posix', path=str(tmp_path / 'local'), role='stage',
                     scope='node', quota=QuotaSpec(), writeback=WritebackSpec())
    engine = OverlayEngine(OverlaySpec(name='testspace', state=tmp_path / 'state', tiers=(source, local)),
                           clock=clock)
    engine.start()
    return engine


def dirty_seeded(engine: OverlayEngine, path: str, size: int) -> str:
    """Open a seeded file, promoting it to its eligible tier, and write it dirty there."""
    handle = engine.open(path, os.O_RDWR)
    engine.write(handle, b'!' * size, 0)
    engine.release(handle)
    return path


# Route files to particular tiers by size: shm admits <=4, tmp admits <=8, scratch takes the rest.
BYPASS = dict(shm_quota=QuotaSpec(bypass_size=4), tmp_quota=QuotaSpec(bypass_size=8))


def test_node_dirty_lists_only_node_local_dirty_in_loss_risk_order(tmp_path: Path, clock: Clock) -> None:
    """The worklist is node-scope dirty holders, most-volatile first; shared-tier dirty is excluded."""
    seeds = [('/shm_file', 2), ('/tmp_file', 6), ('/scratch_file', 10)]
    engine = make_engine(tmp_path, clock, seeds=seeds, **BYPASS)
    try:
        dirty_seeded(engine, '/shm_file', 2)                # -> shm (node, fastest)
        dirty_seeded(engine, '/tmp_file', 6)                # -> tmp (node)
        dirty_seeded(engine, '/scratch_file', 10)           # -> scratch (shared)
        worklist = node_dirty(engine)
        assert [copy.path for copy in worklist] == ['/shm_file', '/tmp_file']
        assert [copy.tier for copy in worklist] == ['shm', 'tmp']
    finally:
        engine.stop()


def test_rescue_moves_node_dirty_to_nearest_shared(tmp_path: Path, clock: Clock) -> None:
    """A file dirty on a node tier lands dirty on the nearest shared tier — not on the source."""
    engine = make_engine(tmp_path, clock, seeds=[('/shm_file', 2)], **BYPASS)
    try:
        dirty_seeded(engine, '/shm_file', 2)                # -> shm (node), dirty
        result = rescue(engine)
        assert (result.rescued, result.stranded, result.complete) == (1, 0, True)
        # Newest bytes rest dirty on scratch (shared), node copies now clean, source untouched.
        assert engine.catalog.get_residency('/shm_file', 'scratch').state == 'dirty'
        assert engine.catalog.get_residency('/shm_file', 'tmp').state == 'clean'
        assert engine.catalog.get_residency('/shm_file', 'shm').state == 'clean'
        assert engine.tiers['scratch'].read(PurePosixPath('/shm_file')) == b'!!'
        assert engine.source.read(PurePosixPath('/shm_file')) == b'..'     # not finalized yet
        # The rescue left the write-back row a later commit needs, and that commit finishes the job.
        assert [row.op for row in engine.journal.queued_for('/shm_file', to_tier='project')] == ['data']
        engine.commit_upstream('/shm_file')
        assert engine.source.read(PurePosixPath('/shm_file')) == b'!!'
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_rescue_one_hop_from_tmp(tmp_path: Path, clock: Clock) -> None:
    """The common case: dirty one node hop below the shared tier moves in a single hop."""
    engine = make_engine(tmp_path, clock, seeds=[('/tmp_file', 6)], **BYPASS)
    try:
        dirty_seeded(engine, '/tmp_file', 6)                # -> tmp (node), dirty
        result = rescue(engine)
        assert (result.rescued, result.complete) == (1, True)
        assert engine.catalog.get_residency('/tmp_file', 'scratch').state == 'dirty'
        assert engine.catalog.get_residency('/tmp_file', 'tmp').state == 'clean'
        assert engine.source.read(PurePosixPath('/tmp_file')) == b'.' * 6
    finally:
        engine.stop()


def test_rescue_leaves_shared_dirty_untouched(tmp_path: Path, clock: Clock) -> None:
    """Dirty data already on a shared tier is safe from node loss — rescue is a no-op for it."""
    engine = make_engine(tmp_path, clock, seeds=[('/big', 10)], **BYPASS)
    try:
        dirty_seeded(engine, '/big', 10)                    # -> scratch (shared), dirty
        assert node_dirty(engine) == []
        result = rescue(engine)
        assert (result.rescued, result.stranded, result.complete) == (0, 0, True)
        assert engine.catalog.get_residency('/big', 'scratch').state == 'dirty'
        assert engine.source.read(PurePosixPath('/big')) == b'.' * 10
    finally:
        engine.stop()


def test_rescue_to_source_when_no_shared_stage(tmp_path: Path, clock: Clock) -> None:
    """With no shared stage, the nearest shared durable tier is the source, so rescue finalizes there."""
    engine = node_stack(tmp_path, clock, seeds=[('/only', 5)])
    try:
        dirty_seeded(engine, '/only', 5)                    # -> local (node), dirty against source
        assert [copy.tier for copy in node_dirty(engine)] == ['local']
        result = rescue(engine)
        assert (result.rescued, result.complete) == (1, True)
        assert engine.catalog.get_residency('/only', 'local').state == 'clean'
        assert engine.source.read(PurePosixPath('/only')) == b'!' * 5   # source is the only shared tier
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_rescue_budget_strands_least_volatile(tmp_path: Path, clock: Clock) -> None:
    """A budget that runs out mid-pass secures the most-volatile file and strands the rest."""
    seeds = [('/shm_file', 2), ('/tmp_file', 6)]
    engine = make_engine(tmp_path, clock, seeds=seeds, **BYPASS)
    try:
        dirty_seeded(engine, '/shm_file', 2)                # -> shm (node, most volatile)
        dirty_seeded(engine, '/tmp_file', 6)                # -> tmp (node, less volatile)
        # A dedicated budget clock (independent of the engine's) ticks 10 per poll: start=10,
        # first file polls 20 (elapsed 10 < 15, proceed), second polls 30 (elapsed 20 >= 15, stop).
        budget_clock = Clock(start=0.0, step=10.0)
        result = rescue(engine, budget=15.0, clock=budget_clock)
        assert (result.rescued, result.stranded, result.complete) == (1, 1, False)
        assert engine.catalog.get_residency('/shm_file', 'scratch').state == 'dirty'    # rescued
        assert engine.catalog.get_residency('/tmp_file', 'scratch') is None             # stranded
        assert engine.catalog.get_residency('/tmp_file', 'tmp').state == 'dirty'
    finally:
        engine.stop()


def test_rescue_zero_budget_rescues_nothing(tmp_path: Path, clock: Clock) -> None:
    """A zero budget (SIGTERM with no grace) does no work but reports the exposure."""
    engine = make_engine(tmp_path, clock, seeds=[('/shm_file', 2)], **BYPASS)
    try:
        dirty_seeded(engine, '/shm_file', 2)
        result = rescue(engine, budget=0.0, clock=Clock(start=0.0, step=10.0))
        assert (result.rescued, result.stranded, result.complete) == (0, 1, False)
        assert engine.catalog.get_residency('/shm_file', 'shm').state == 'dirty'        # untouched
    finally:
        engine.stop()


def test_rescue_none_budget_rescues_all(tmp_path: Path, clock: Clock) -> None:
    """An unbounded budget (the --detach case) rescues every node-local dirty file."""
    seeds = [('/shm_file', 2), ('/tmp_file', 6)]
    engine = make_engine(tmp_path, clock, seeds=seeds, **BYPASS)
    try:
        dirty_seeded(engine, '/shm_file', 2)
        dirty_seeded(engine, '/tmp_file', 6)
        result = rescue(engine, budget=None)
        assert (result.rescued, result.stranded, result.complete) == (2, 0, True)
        for name in ('/shm_file', '/tmp_file'):
            assert engine.catalog.get_residency(name, 'scratch').state == 'dirty'
        assert node_dirty(engine) == []                     # nothing left on node-local storage
    finally:
        engine.stop()
