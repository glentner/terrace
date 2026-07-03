# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Engine suite: multi-tier POSIX stacks (2-4 tiers) — promotion, residency, metadata."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark

# Internal libs
from sosfs.tier.spec import QuotaSpec
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit

DEPTHS = [2, 3, 4]
FASTEST = {2: 'scratch', 3: 'tmp', 4: 'shm'}


def make_engine(tmp_path: Path, tiers: int, **kwargs) -> OverlayEngine:
    """A started engine over a seeded source at the requested stack depth."""
    seed(tmp_path / 'project', 'inputs/model.dat', b'M' * 300, mode=0o640, mtime=42.0)
    seed(tmp_path / 'project', 'inputs/params.txt', b'alpha=1\n')
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers, **kwargs), clock=Clock())
    engine.start()
    return engine


def stack_names(engine: OverlayEngine) -> list:
    """Tier names, source first."""
    return [spec.name for spec in engine.spec.tiers]


@mark.parametrize('tiers', DEPTHS)
def test_promotion_lands_fastest_with_metadata(tmp_path: Path, tiers: int) -> None:
    """Promote-on-open stages the whole file on the fastest tier, metadata intact."""
    engine = make_engine(tmp_path, tiers)
    try:
        handle = engine.open('/inputs/model.dat')
        engine.release(handle)
        assert handle.tier == FASTEST[tiers]
        fast = engine.tiers[handle.tier]
        stat = fast.stat(PurePosixPath('/inputs/model.dat'))
        assert (stat.size, stat.mode, stat.mtime) == (300, 0o640, 42.0)
        residencies = engine.catalog.residencies('/inputs/model.dat')
        assert [r.tier for r in residencies] == ['project', FASTEST[tiers]]
        assert all(r.state == 'clean' for r in residencies)
    finally:
        engine.stop()


@mark.parametrize('tiers', DEPTHS)
def test_write_dirty_only_at_fastest_boundary(tmp_path: Path, tiers: int) -> None:
    """A write dirties exactly the serving copy, queued against its own upstream neighbor."""
    engine = make_engine(tmp_path, tiers)
    try:
        handle = engine.open('/inputs/model.dat', os.O_RDWR)
        engine.write(handle, b'CHANGED', 0)
        engine.release(handle)
        names = stack_names(engine)
        fastest = FASTEST[tiers]
        for residency in engine.catalog.residencies('/inputs/model.dat'):
            assert residency.state == ('dirty' if residency.tier == fastest else 'clean')
        rows = engine.journal.queued_for('/inputs/model.dat')
        assert [(row.op, row.to_tier) for row in rows] == [('data', names[names.index(fastest) - 1])]
    finally:
        engine.stop()


@mark.parametrize('tiers', DEPTHS)
def test_commit_cascade_leaves_stack_warm_and_identical(tmp_path: Path, tiers: int) -> None:
    """After commit_upstream every tier holds identical clean bytes and counters match disk."""
    engine = make_engine(tmp_path, tiers)
    try:
        handle = engine.open('/inputs/model.dat', os.O_RDWR)
        engine.truncate('/inputs/model.dat', 0, handle=handle)
        engine.write(handle, b'fresh-output', 0)
        engine.release(handle)
        engine.commit_upstream('/inputs/model.dat')
        for name in stack_names(engine):
            residency = engine.catalog.get_residency('/inputs/model.dat', name)
            assert residency is not None and residency.state == 'clean', name
            assert engine.tiers[name].read(PurePosixPath('/inputs/model.dat')) == b'fresh-output', name
            assert residency.size == 12
        for name in stack_names(engine)[1:]:
            assert engine.registry.usage(name) == engine.tiers[name].usage(), name
        assert engine.journal.queued() == [] and engine.journal.pending() == []
    finally:
        engine.stop()


@mark.parametrize('tiers', DEPTHS)
def test_workflow_flush_and_remount(tmp_path: Path, tiers: int) -> None:
    """A job session (read, compute, write, flush, stop) leaves a source a fresh mount can serve."""
    engine = make_engine(tmp_path, tiers)
    try:
        handle = engine.open('/inputs/params.txt')
        params = engine.read(handle, 100, 0)
        engine.release(handle)
        engine.mkdir('/outputs', mode=0o755)
        engine.create('/outputs/result.dat', mode=0o600)
        handle = engine.open('/outputs/result.dat', os.O_WRONLY)
        engine.write(handle, b'result-of-' + params.strip(), 0)
        engine.release(handle)
        engine.flush()
        assert engine.source.read(PurePosixPath('/outputs/result.dat')) == b'result-of-alpha=1'
        assert engine.source.stat(PurePosixPath('/outputs/result.dat')).mode == 0o600
    finally:
        engine.stop()

    second = OverlayEngine(build_spec(tmp_path, tiers=tiers), clock=Clock(start=1000.0))
    second.start()
    try:
        assert second.lifecycle.load().shutdown_state == 'mounted'
        entry = second.getattr('/outputs/result.dat')
        assert entry.size == 17
        handle = second.open('/outputs/result.dat')
        try:
            assert second.read(handle, 100, 0) == b'result-of-alpha=1'
        finally:
            second.release(handle)
    finally:
        second.stop()


def test_four_tier_placement_rules(tmp_path: Path) -> None:
    """bypass_size and quota exhaustion push placement upstream tier by tier."""
    seed(tmp_path / 'project', 'huge.bin', b'H' * 5000)
    seed(tmp_path / 'project', 'mid.bin', b'm' * 500)
    seed(tmp_path / 'project', 'mid2.bin', b'n' * 300)
    seed(tmp_path / 'project', 'tiny.bin', b't' * 10)
    engine = OverlayEngine(build_spec(
        tmp_path, tiers=4,
        shm_quota=QuotaSpec(bypass_size=100),               # only tiny files at the fast end
        tmp_quota=QuotaSpec(size=600),                      # room for mid.bin once
        scratch_quota=QuotaSpec(bypass_size=2000)), clock=Clock())
    engine.start()
    try:
        tiny = engine.open('/tiny.bin')
        engine.release(tiny)
        mid = engine.open('/mid.bin')
        engine.release(mid)
        huge = engine.open('/huge.bin')
        engine.release(huge)
        assert tiny.tier == 'shm'
        assert mid.tier == 'tmp'                            # over shm's bypass, fits tmp quota
        assert huge.tier == 'project'                       # over every stage's threshold
        second_mid = engine.open('/mid2.bin')               # tmp holds 500/600: no headroom for 300
        engine.release(second_mid)
        assert second_mid.tier == 'scratch'
        assert engine.registry.usage('tmp').bytes <= 600
    finally:
        engine.stop()


def test_rename_directory_with_promoted_children(tmp_path: Path) -> None:
    """Renaming a directory moves promoted copies physically and flush converges the source."""
    engine = make_engine(tmp_path, tiers=3)
    try:
        handle = engine.open('/inputs/model.dat', os.O_RDWR)
        engine.write(handle, b'X', 0)
        engine.release(handle)
        engine.rename('/inputs', '/run1')
        fast = engine.tiers['tmp']
        assert fast.stat(PurePosixPath('/inputs/model.dat')) is None
        assert fast.stat(PurePosixPath('/run1/model.dat')) is not None
        assert engine.catalog.get_residency('/run1/model.dat', 'tmp').state == 'dirty'
        engine.flush()
        assert engine.source.stat(PurePosixPath('/inputs')) is None
        assert engine.source.read(PurePosixPath('/run1/model.dat'))[:1] == b'X'
        assert engine.source.read(PurePosixPath('/run1/params.txt')) == b'alpha=1\n'
        assert engine.journal.queued() == [] and engine.journal.pending() == []
    finally:
        engine.stop()


def test_counters_track_physical_usage(tmp_path: Path) -> None:
    """Registry counters agree with a physical walk after promote, write, commit, unlink."""
    engine = make_engine(tmp_path, tiers=2)
    try:
        for name in ('/inputs/model.dat', '/inputs/params.txt'):
            handle = engine.open(name, os.O_RDWR)
            engine.write(handle, b'delta', 0)
            engine.release(handle)
        engine.commit_upstream('/inputs/model.dat')
        engine.unlink('/inputs/params.txt')
        assert engine.registry.usage('scratch') == engine.tiers['scratch'].usage()
    finally:
        engine.stop()
