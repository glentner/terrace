# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Virtual-clock policy tests: quiescence reset, max-interval, watermark hysteresis, loss-risk order.

These drive the write-back and eviction policies deterministically — the write-back deadline against
explicit virtual times (a bare `mark_dirty` stands in for the write path's clock refresh, which is all
the policy clock sees), the eviction watermarks against controlled usage, and the shared drain/rescue
ordering against a known dirty stack. Time never flows on its own here; the per-component suites
(`test_writeback`, `test_evict`, `test_drain`, `test_rescue`) cover the machine loop and the rest.
"""

# Type annotations
from __future__ import annotations

# Standard libs
import os
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark

# Internal libs
from sosfs.tier.spec import QuotaSpec, WritebackSpec
from sosfs.engine.overlay import OverlayEngine
from sosfs.engine.writeback import WritebackScheduler
from sosfs.engine.evict import EvictionManager
from sosfs.engine.drain import dirty_copies
from sosfs.engine.rescue import node_dirty
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit

PATH = '/data/file.dat'


# -- write-back clock helpers -------------------------------------------------------------------------------------

def wb_engine(tmp_path: Path, clock: Clock, **kwargs) -> OverlayEngine:
    """A started two-tier engine holding one seeded, then dirtied, file at PATH."""
    seed(tmp_path / 'project', 'data/file.dat', b'v0')
    engine = OverlayEngine(build_spec(tmp_path, tiers=2, **kwargs), clock=clock)
    engine.start()
    return engine


def dirty(engine: OverlayEngine, data: bytes = b'fresh') -> None:
    """Write `data` into PATH through the engine, leaving it dirty on the fastest tier."""
    handle = engine.open(PATH, os.O_RDWR)
    engine.write(handle, data, 0)
    engine.release(handle)


def write_at(engine: OverlayEngine, now: float) -> None:
    """Refresh the boundary clock as a write at virtual time `now` would, without touching bytes."""
    engine.journal.mark_dirty(PATH, 'scratch', to_tier='project', now=now)


def the_row(engine: OverlayEngine):
    """The single queued write-back row."""
    rows = engine.journal.queued()
    assert len(rows) == 1
    return rows[0]


# -- eviction helpers ---------------------------------------------------------------------------------------------

def ev_engine(tmp_path: Path, clock: Clock, **kwargs) -> OverlayEngine:
    """A started two-tier engine over an empty source, with per-tier quota overrides."""
    engine = OverlayEngine(build_spec(tmp_path, tiers=2, **kwargs), clock=clock)
    engine.start()
    return engine


def stage_clean(engine: OverlayEngine, path: str, size: int, atime: float, tier: str = 'scratch') -> None:
    """Create, fill, and commit a file so `tier` holds a clean copy with a known LRU atime."""
    engine.create(path)
    handle = engine.open(path, os.O_RDWR)
    engine.write(handle, b'x' * size, 0)
    engine.release(handle)
    engine.commit_upstream(path)
    engine.catalog.touch(path, tier, atime)


def resident(engine: OverlayEngine, path: str, tier: str = 'scratch') -> bool:
    """Whether `tier` still holds a copy of `path`."""
    return engine.catalog.get_residency(path, tier) is not None


# -- loss-risk ordering helpers -----------------------------------------------------------------------------------

def ordering_engine(tmp_path: Path, clock: Clock, seeds=(), **kwargs) -> OverlayEngine:
    """A started four-tier engine (project/scratch/tmp/shm) over `(path, size)` seeds."""
    for path, size in seeds:
        seed(tmp_path / 'project', path.lstrip('/'), b'.' * size)
    engine = OverlayEngine(build_spec(tmp_path, tiers=4, **kwargs), clock=clock)
    engine.start()
    return engine


def dirty_seeded(engine: OverlayEngine, path: str, size: int) -> None:
    """Open a seeded file, promoting it to its eligible tier, and write it dirty there."""
    handle = engine.open(path, os.O_RDWR)
    engine.write(handle, b'!' * size, 0)
    engine.release(handle)


# -- write-back: quiescence delay ---------------------------------------------------------------------------------

def test_grace_period_resets_on_each_write(tmp_path: Path, clock: Clock) -> None:
    """Each write restarts the quiescence timer; the commit fires only after `delay` of true silence."""
    engine = wb_engine(tmp_path, clock, scratch_writeback=WritebackSpec(delay=60.0))
    try:
        scheduler = WritebackScheduler(engine)
        dirty(engine, b'payload')
        t0 = the_row(engine).last_write                     # first-dirty deadline would be t0 + 60
        write_at(engine, t0 + 30.0)                         # a fresh write pushes it out to t0 + 90
        assert scheduler.poll(now=t0 + 60.0) == 0           # the original deadline no longer applies
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'dirty'
        assert scheduler.poll(now=t0 + 89.0) == 0           # still inside the reset grace window
        assert scheduler.poll(now=t0 + 90.0) == 1           # 60s of silence since the last write
        assert engine.source.read(PurePosixPath(PATH)) == b'payload'
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_max_interval_caps_commit_under_constant_writes(tmp_path: Path, clock: Clock) -> None:
    """Writes that never let quiescence expire still commit at the max-interval bound on first-dirty."""
    engine = wb_engine(tmp_path, clock,
                       scratch_writeback=WritebackSpec(delay=60.0, max_interval=100.0))
    try:
        scheduler = WritebackScheduler(engine)
        dirty(engine, b'stream')
        t0 = the_row(engine).first_dirty
        for offset in (40.0, 80.0):                         # 40s apart: quiescence (60s) never elapses
            write_at(engine, t0 + offset)
            assert scheduler.poll(now=t0 + offset + 1.0) == 0
        # deadline = min(last_write 80 + 60 = 140, first_dirty + 100 = 100) = t0 + 100.
        assert scheduler.poll(now=t0 + 99.0) == 0
        assert scheduler.poll(now=t0 + 100.0) == 1          # max_interval forces it, not quiescence
        assert engine.source.read(PurePosixPath(PATH)) == b'stream'
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_delay_only_boundary_defers_under_constant_writes(tmp_path: Path, clock: Clock) -> None:
    """Without a max-interval, a file written faster than `delay` defers indefinitely — the contrapositive."""
    engine = wb_engine(tmp_path, clock, scratch_writeback=WritebackSpec(delay=60.0))
    try:
        scheduler = WritebackScheduler(engine)
        dirty(engine, b'busy')
        t0 = the_row(engine).last_write
        for offset in (50.0, 100.0, 150.0):                 # 50s apart, each before the prior deadline
            write_at(engine, t0 + offset)
            assert scheduler.poll(now=t0 + offset + 59.0) == 0
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'dirty'
        assert scheduler.poll(now=t0 + 150.0 + 60.0) == 1   # only once writing stops does grace elapse
        assert engine.source.read(PurePosixPath(PATH)) == b'busy'
    finally:
        engine.stop()


# -- eviction: watermark hysteresis -------------------------------------------------------------------------------

def test_watermark_hysteresis_full_cycle(tmp_path: Path, clock: Clock) -> None:
    """High triggers reclaim to low; usage staying inside the band never re-triggers; crossing high does."""
    engine = ev_engine(tmp_path, clock, scratch_quota=QuotaSpec(size=1000))
    try:
        manager = EvictionManager(engine, high=0.9, low=0.5)   # high 900, low 500
        for i in range(5):
            stage_clean(engine, f'/a{i}', 200, atime=float(i))  # usage 1000 > 900
        assert manager.poll() == 3                          # 1000 -> 400 (<= 500), a0..a2 dropped LRU-first
        assert engine.registry.usage('scratch').bytes == 400
        assert resident(engine, '/a3') and resident(engine, '/a4')

        stage_clean(engine, '/b0', 400, atime=10.0)         # usage 800: inside the hysteresis band
        assert manager.poll() == 0                          # no thrash while below the high watermark
        assert engine.registry.usage('scratch').bytes == 800
        assert resident(engine, '/a3') and resident(engine, '/a4') and resident(engine, '/b0')

        stage_clean(engine, '/b1', 200, atime=11.0)         # usage 1000 > 900 again
        assert manager.poll() == 3                          # 1000 -> 200: a3, a4, b0 drop; only b1 survives
        assert engine.registry.usage('scratch').bytes == 200
        assert resident(engine, '/b1') and not resident(engine, '/b0')
    finally:
        engine.stop()


# -- drain / rescue: shared loss-risk ordering --------------------------------------------------------------------

def test_drain_and_rescue_share_loss_risk_order(tmp_path: Path, clock: Clock) -> None:
    """Rescue's worklist is exactly the node-scope subsequence of drain's, in the same loss-risk order."""
    seeds = [('/s', 2), ('/t', 6), ('/c', 10)]
    engine = ordering_engine(tmp_path, clock, seeds=seeds,
                             shm_quota=QuotaSpec(bypass_size=4), tmp_quota=QuotaSpec(bypass_size=8))
    try:
        dirty_seeded(engine, '/s', 2)                       # -> shm (node, fastest, most volatile)
        dirty_seeded(engine, '/t', 6)                       # -> tmp (node)
        dirty_seeded(engine, '/c', 10)                      # -> scratch (shared)
        drain_order = [copy.path for copy in dirty_copies(engine)]
        rescue_order = [copy.path for copy in node_dirty(engine)]
        assert drain_order == ['/s', '/t', '/c']            # every dirty file, most volatile first
        assert rescue_order == ['/s', '/t']                 # node-scope only, same order
        assert rescue_order == [path for path in drain_order if path in rescue_order]
    finally:
        engine.stop()
