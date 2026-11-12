# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for drain: clock collapse, namespace-first ordering, loss-risk order, detach downgrade."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark

# Internal libs
from terrace.tier.spec import QuotaSpec, WritebackSpec
from terrace.engine.overlay import OverlayEngine
from terrace.engine.drain import drain, dirty_copies, tier_volatility
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


def make_engine(tmp_path: Path, clock: Clock, tiers: int = 2, seeds=(), **kwargs) -> OverlayEngine:
    """A started engine over a source pre-seeded with `(path, size)` files (paths overlay-absolute)."""
    for path, size in seeds:
        seed(tmp_path / 'project', path.lstrip('/'), b'.' * size)
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers, **kwargs), clock=clock)
    engine.start()
    return engine


def dirty_seeded(engine: OverlayEngine, path: str, size: int) -> str:
    """Open a seeded file, promoting it to its eligible tier, and write it dirty there."""
    handle = engine.open(path, os.O_RDWR)
    engine.write(handle, b'!' * size, 0)
    engine.release(handle)
    return path


def dirty_new(engine: OverlayEngine, path: str, data: bytes) -> None:
    """Create and fill a new file, leaving the fastest tier holding a dirty copy."""
    engine.create(path)
    handle = engine.open(path, os.O_RDWR)
    engine.write(handle, data, 0)
    engine.release(handle)


def test_tier_volatility_ranks_node_and_downstream_first(tmp_path: Path) -> None:
    """Node-scope tiers outrank shared ones; within a scope the faster tier outranks the slower."""
    spec = build_spec(tmp_path, tiers=4)                     # project(0,shared) scratch(1,shared) tmp(2,node) shm(3,node)
    rank = tier_volatility(spec)
    assert rank == {'project': (1, 0), 'scratch': (1, -1), 'tmp': (0, -2), 'shm': (0, -3)}
    assert sorted(rank, key=rank.get) == ['shm', 'tmp', 'scratch', 'project']


def test_drain_commits_all_dirty_to_source(tmp_path: Path, clock: Clock) -> None:
    """A drain finalizes the source: every dirty copy goes clean and the queue empties."""
    engine = make_engine(tmp_path, clock)
    try:
        for name in ('a', 'b', 'c'):
            dirty_new(engine, f'/{name}', f'content-{name}'.encode())
        result = drain(engine)
        assert (result.data, result.complete) == (3, True)
        for name in ('a', 'b', 'c'):
            assert engine.catalog.get_residency(f'/{name}', 'scratch').state == 'clean'
            assert engine.source.read(PurePosixPath(f'/{name}')) == f'content-{name}'.encode()
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_drain_collapses_policy_clocks(tmp_path: Path, clock: Clock) -> None:
    """Drain commits regardless of the boundary clock — no grace period is waited out."""
    engine = make_engine(tmp_path, clock,
                         scratch_writeback=WritebackSpec(delay=99_999.0, max_interval=99_999.0))
    try:
        dirty_new(engine, '/slow', b'fresh')
        assert engine.catalog.get_residency('/slow', 'scratch').state == 'dirty'
        result = drain(engine)                              # clock never advances past the huge delay
        assert (result.data, result.complete) == (1, True)
        assert engine.catalog.get_residency('/slow', 'scratch').state == 'clean'
        assert engine.source.read(PurePosixPath('/slow')) == b'fresh'
    finally:
        engine.stop()


def test_drain_applies_namespace_ops_before_data(tmp_path: Path, clock: Clock) -> None:
    """Namespace ops go first so the source tree exists before data lands in it."""
    engine = make_engine(tmp_path, clock)
    try:
        engine.mkdir('/results', mode=0o750)
        dirty_new(engine, '/results/out.dat', b'payload')
        result = drain(engine)
        assert result.namespace == 1 and result.data == 1 and result.complete
        assert engine.source.stat(PurePosixPath('/results')).kind == 'dir'
        assert engine.source.read(PurePosixPath('/results/out.dat')) == b'payload'
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_drain_orders_most_volatile_first(tmp_path: Path, clock: Clock, monkeypatch) -> None:
    """Dirty copies commit most-volatile holder first (node before shared, fast before slow)."""
    seeds = [('/shm_file', 2), ('/tmp_file', 6), ('/scratch_file', 10)]
    engine = make_engine(tmp_path, clock, tiers=4, seeds=seeds,
                         shm_quota=QuotaSpec(bypass_size=4), tmp_quota=QuotaSpec(bypass_size=8))
    try:
        dirty_seeded(engine, '/shm_file', 2)                # -> shm (node, fastest)
        dirty_seeded(engine, '/tmp_file', 6)                # -> tmp (node)
        dirty_seeded(engine, '/scratch_file', 10)           # -> scratch (shared)
        assert engine.catalog.get_residency('/shm_file', 'shm').state == 'dirty'
        assert engine.catalog.get_residency('/tmp_file', 'tmp').state == 'dirty'
        assert engine.catalog.get_residency('/scratch_file', 'scratch').state == 'dirty'
        assert [copy.path for copy in dirty_copies(engine)] == \
            ['/shm_file', '/tmp_file', '/scratch_file']

        order = []
        real_commit = engine.commit_upstream
        monkeypatch.setattr(engine, 'commit_upstream',
                            lambda path: (order.append(path), real_commit(path))[1])
        result = drain(engine)
        assert order == ['/shm_file', '/tmp_file', '/scratch_file']
        assert (result.data, result.complete) == (3, True)
        for name, size in seeds:
            assert engine.source.read(PurePosixPath(name)) == b'!' * size
            assert all(residency.state == 'clean' for residency in engine.catalog.residencies(name))
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_second_signal_downgrades_to_detach(tmp_path: Path, clock: Clock) -> None:
    """A cancel firing mid-drain secures the most-volatile data and leaves the rest queued."""
    engine = make_engine(tmp_path, clock, tiers=3, seeds=[('/vol', 2), ('/dur', 8)],
                         tmp_quota=QuotaSpec(bypass_size=4))
    try:
        dirty_seeded(engine, '/vol', 2)                     # -> tmp (node, most volatile)
        dirty_seeded(engine, '/dur', 8)                     # -> scratch (shared, bypasses tmp)
        checks = [0]

        def cancel() -> bool:
            checks[0] += 1
            return checks[0] >= 2                           # allow the first commit, stop before the second

        result = drain(engine, cancel=cancel)
        assert (result.data, result.complete) == (1, False)
        assert engine.source.read(PurePosixPath('/vol')) == b'!!'   # volatile data rescued first
        assert engine.catalog.get_residency('/dur', 'scratch').state == 'dirty'
        assert engine.journal.queued() != []                # remainder left for `terrace commit`
    finally:
        engine.stop()
