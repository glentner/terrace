# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the eviction manager: watermarks, LRU order, commit-before-evict, pinning."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
import time
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark, raises

# Internal libs
from terrace.tier.spec import QuotaSpec
from terrace.engine.overlay import OverlayEngine
from terrace.engine.evict import EvictionManager
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


def make_engine(tmp_path: Path, clock: Clock, tiers: int = 2, **kwargs) -> OverlayEngine:
    """A started engine over an empty source, with per-tier quota overrides."""
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers, **kwargs), clock=clock)
    engine.start()
    return engine


def stage_clean(engine: OverlayEngine, path: str, size: int, atime: float, tier: str = 'scratch') -> None:
    """Create, fill, and commit a file so `tier` holds a clean copy with a known LRU atime."""
    engine.create(path)
    handle = engine.open(path, os.O_RDWR)
    engine.write(handle, b'x' * size, 0)
    engine.release(handle)
    engine.commit_upstream(path)                            # clean on the stage tier, bytes on the source
    engine.catalog.touch(path, tier, atime)


def stage_dirty(engine: OverlayEngine, path: str, size: int, atime: float, tier: str = 'scratch') -> None:
    """Create and fill a file, leaving `tier` holding an uncommitted (dirty) copy."""
    engine.create(path)
    handle = engine.open(path, os.O_RDWR)
    engine.write(handle, b'x' * size, 0)
    engine.release(handle)
    engine.catalog.touch(path, tier, atime)


def resident(engine: OverlayEngine, path: str, tier: str = 'scratch') -> bool:
    """Whether `tier` still holds a copy of `path`."""
    return engine.catalog.get_residency(path, tier) is not None


def test_rejects_inverted_or_out_of_range_watermarks(tmp_path: Path, clock: Clock) -> None:
    """The watermark band must satisfy 0 < low < high <= 1."""
    engine = make_engine(tmp_path, clock, scratch_quota=QuotaSpec(size=1000))
    try:
        with raises(ValueError):
            EvictionManager(engine, high=0.5, low=0.9)     # inverted
        with raises(ValueError):
            EvictionManager(engine, high=1.5, low=0.8)     # over unity
        with raises(ValueError):
            EvictionManager(engine, high=0.9, low=0.0)     # non-positive low
    finally:
        engine.stop()


def test_no_reclaim_below_high_watermark(tmp_path: Path, clock: Clock) -> None:
    """Usage sitting in the hysteresis band leaves every copy in place — no thrash."""
    engine = make_engine(tmp_path, clock, scratch_quota=QuotaSpec(size=1000))
    try:
        manager = EvictionManager(engine, high=0.9, low=0.5)   # high=900, low=500
        for i in range(3):
            stage_clean(engine, f'/f{i}', 200, atime=float(i))  # usage 600: below high, above low
        assert manager.poll() == 0
        assert engine.registry.usage('scratch').bytes == 600
        assert all(resident(engine, f'/f{i}') for i in range(3))
    finally:
        engine.stop()


def test_reclaims_lru_clean_to_low_watermark(tmp_path: Path, clock: Clock) -> None:
    """Over the high watermark, the least-recently-used clean copies drop until at the low mark."""
    engine = make_engine(tmp_path, clock, scratch_quota=QuotaSpec(size=1000))
    try:
        manager = EvictionManager(engine, high=0.9, low=0.5)   # high=900, low=500
        names = ['a', 'b', 'c', 'd', 'e']
        for i, name in enumerate(names):
            stage_clean(engine, f'/{name}', 200, atime=float(i))   # a oldest, e newest; usage 1000
        assert manager.poll() == 3                          # 1000 -> 800 -> 600 -> 400 (<= 500)
        assert not any(resident(engine, f'/{n}') for n in ('a', 'b', 'c'))
        assert all(resident(engine, f'/{n}') for n in ('d', 'e'))
        usage = engine.registry.usage('scratch')
        assert (usage.bytes, usage.files) == (400, 2)
        for name in names:                                  # the source keeps every committed copy
            assert engine.source.read(PurePosixPath(f'/{name}')) == b'x' * 200
    finally:
        engine.stop()


def test_reclaims_on_file_count_watermark(tmp_path: Path, clock: Clock) -> None:
    """A file-count quota triggers and bounds reclaim just like a byte quota."""
    engine = make_engine(tmp_path, clock, scratch_quota=QuotaSpec(files=10))
    try:
        manager = EvictionManager(engine, high=0.9, low=0.5)   # high=9 files, low=5 files
        for i in range(10):
            stage_clean(engine, f'/n{i}', 1, atime=float(i))
        assert manager.poll() == 5                          # 10 -> 5 files
        assert engine.registry.usage('scratch').files == 5
        assert all(resident(engine, f'/n{i}') for i in range(5, 10))
    finally:
        engine.stop()


def test_commits_dirty_before_evicting(tmp_path: Path, clock: Clock) -> None:
    """When only dirty copies remain, reclaim forces write-back first — the newest bytes survive."""
    engine = make_engine(tmp_path, clock, scratch_quota=QuotaSpec(size=300))
    try:
        manager = EvictionManager(engine, high=0.5, low=0.1)   # high=150, low=30
        stage_dirty(engine, '/big', 200, atime=1.0)         # usage 200 > 150; source has nothing yet
        assert engine.source.stat(PurePosixPath('/big')) is None
        assert manager.poll() == 1
        assert not resident(engine, '/big')                 # dropped after its data reached the source
        assert engine.source.read(PurePosixPath('/big')) == b'x' * 200
        assert engine.registry.usage('scratch').bytes == 0
    finally:
        engine.stop()


def test_open_copy_is_never_evicted(tmp_path: Path, clock: Clock) -> None:
    """A copy with a live handle is pinned; it becomes evictable only once released."""
    engine = make_engine(tmp_path, clock, scratch_quota=QuotaSpec(size=100))
    try:
        manager = EvictionManager(engine, high=0.5, low=0.1)   # high=50, low=10
        stage_clean(engine, '/pin', 200, atime=1.0)         # usage 200 > 50
        handle = engine.open('/pin', os.O_RDONLY)
        assert manager.poll() == 0                          # pinned: nothing to reclaim
        assert resident(engine, '/pin')
        engine.release(handle)
        assert manager.poll() == 1
        assert not resident(engine, '/pin')
    finally:
        engine.stop()


def test_bypass_size_copies_are_never_staged_or_evicted(tmp_path: Path, clock: Clock) -> None:
    """A file over the tier's bypass_size serves from the source and never enters the reclaim set."""
    seed(tmp_path / 'project', 'big.dat', b'B' * 500)
    engine = make_engine(tmp_path, clock, scratch_quota=QuotaSpec(size=200, bypass_size=100))
    try:
        manager = EvictionManager(engine, high=0.5, low=0.25)  # high=100, low=50
        for i in range(3):
            stage_clean(engine, f'/s{i}', 40, atime=float(i))   # usage 120 > 100
        handle = engine.open('/big.dat', os.O_RDONLY)       # served from source, not promoted (over bypass)
        engine.release(handle)
        assert not resident(engine, '/big.dat')
        assert manager.poll() == 2                          # 120 -> 80 -> 40 (<= 50)
        assert not resident(engine, '/big.dat')             # bypassed file untouched by reclaim
        assert not any(resident(engine, f'/s{i}') for i in range(2))
        assert resident(engine, '/s2')
    finally:
        engine.stop()


def test_unbounded_tier_is_never_reclaimed(tmp_path: Path, clock: Clock) -> None:
    """A staging tier without a quota is not a reclaim target, however full it gets."""
    engine = make_engine(tmp_path, clock)                   # default QuotaSpec: no bounds
    try:
        manager = EvictionManager(engine)
        assert manager.targets() == []
        for i in range(4):
            stage_clean(engine, f'/u{i}', 500, atime=float(i))
        assert manager.poll() == 0
        assert all(resident(engine, f'/u{i}') for i in range(4))
    finally:
        engine.stop()


def test_thread_reclaims_and_stops_cleanly(tmp_path: Path, clock: Clock) -> None:
    """Lifecycle smoke test: the machine scans, reclaims to the low watermark, and stops on request."""
    engine = make_engine(tmp_path, clock, scratch_quota=QuotaSpec(size=1000))
    try:
        for i in range(6):
            stage_clean(engine, f'/t{i}', 200, atime=float(i))   # usage 1200 > 900
        manager = EvictionManager.new(engine, tick=0.01, high=0.9, low=0.5)
        try:
            for _ in range(500):                            # bounded real-time wait, no policy clocks
                if engine.registry.usage('scratch').bytes <= 500:
                    break
                time.sleep(0.01)
            assert engine.registry.usage('scratch').bytes <= 500
        finally:
            manager.stop(timeout=5.0)
        assert not manager.is_alive()
        assert manager.state is EvictionManager.states.FINAL
    finally:
        engine.stop()
