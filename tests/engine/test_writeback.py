# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the write-back scheduler: boundary policies, deadlines, hop commits."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
import time
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark

# Internal libs
from terrace.state.journal import Writeback
from terrace.tier.spec import WritebackSpec
from terrace.engine.overlay import OverlayEngine, Handle
from terrace.engine.writeback import WritebackScheduler, boundary_policies
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit

PATH = '/data/file.dat'


def make_engine(tmp_path: Path, clock: Clock, tiers: int = 2, **kwargs) -> OverlayEngine:
    """A started engine over a seeded source, with per-tier writeback overrides (the seed
    content is shorter than every test write, so offset-0 writes replace it fully)."""
    seed(tmp_path / 'project', 'data/file.dat', b'v0')
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers, **kwargs), clock=clock)
    engine.start()
    return engine


def dirty(engine: OverlayEngine, data: bytes = b'fresh', keep_open: bool = False) -> Handle:
    """Write `data` into PATH through the engine, leaving it dirty on the fastest tier."""
    handle = engine.open(PATH, os.O_RDWR)
    engine.write(handle, data, 0)
    if not keep_open:
        engine.release(handle)
    return handle


def queue_row(engine: OverlayEngine) -> Writeback:
    """The single queued write-back row."""
    rows = engine.journal.queued()
    assert len(rows) == 1
    return rows[0]


def make_row(**kwargs) -> Writeback:
    """A Writeback row literal for deadline math."""
    base = dict(id=1, entry=1, op='data', from_tier='scratch', to_tier='project', detail=None,
                state='queued', first_dirty=100.0, last_write=100.0, last_commit=None)
    base.update(kwargs)
    return Writeback(**base)


def test_boundary_policies_keyed_by_receiver(tmp_path: Path) -> None:
    """Each boundary is governed by the downstream neighbor of the tier receiving the commit."""
    scratch = WritebackSpec(delay=30.0)
    tmp = WritebackSpec(mode='passthrough', delay=0.0)
    spec = build_spec(tmp_path, tiers=3, scratch_writeback=scratch, tmp_writeback=tmp)
    assert boundary_policies(spec) == {'project': scratch, 'scratch': tmp}


def test_deadline_math(tmp_path: Path, clock: Clock) -> None:
    """Quiescence expiry, bounded by max_interval on its anchor; passthrough is due at once."""
    engine = OverlayEngine(build_spec(tmp_path, tiers=2,
                                      scratch_writeback=WritebackSpec(delay=60.0, max_interval=300.0)),
                           clock=clock)
    scheduler = WritebackScheduler(engine)
    assert scheduler.deadline(make_row(last_write=200.0)) == 260.0
    assert scheduler.deadline(make_row(first_dirty=100.0, last_write=395.0)) == 400.0     # interval bound
    assert scheduler.deadline(make_row(first_dirty=100.0, last_write=395.0, last_commit=350.0)) == 455.0
    scheduler.policies['project'] = WritebackSpec(mode='passthrough', delay=0.0)
    assert scheduler.deadline(make_row(last_write=987.0)) == 987.0


def test_poll_commits_after_quiescence(tmp_path: Path, clock: Clock) -> None:
    """A dirty file commits to the source exactly when its grace period expires, not before."""
    engine = make_engine(tmp_path, clock)
    try:
        scheduler = WritebackScheduler(engine)
        dirty(engine, b'fresh-content')
        row = queue_row(engine)
        assert scheduler.poll(now=row.last_write + 59.0) == 0
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'dirty'
        assert scheduler.poll(now=row.last_write + 60.0) == 1
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'clean'
        assert engine.source.read(PurePosixPath(PATH)) == b'fresh-content'
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_passthrough_commits_immediately(tmp_path: Path, clock: Clock) -> None:
    """A passthrough boundary never delays: the first poll pushes the write upstream."""
    engine = make_engine(tmp_path, clock,
                         scratch_writeback=WritebackSpec(mode='passthrough', delay=0.0))
    try:
        scheduler = WritebackScheduler(engine)
        dirty(engine, b'through')
        assert scheduler.poll() == 1
        assert engine.source.read(PurePosixPath(PATH)) == b'through'
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_cascade_ages_per_boundary(tmp_path: Path, clock: Clock) -> None:
    """A hop into a mid-stack tier queues against the next boundary, which ages on its own clock."""
    engine = make_engine(tmp_path, clock, tiers=3,
                         tmp_writeback=WritebackSpec(mode='passthrough', delay=0.0),
                         scratch_writeback=WritebackSpec(delay=60.0))
    try:
        scheduler = WritebackScheduler(engine)
        dirty(engine, b'cascade-bytes')
        assert queue_row(engine).to_tier == 'scratch'
        assert scheduler.poll() == 1                        # passthrough hop: tmp -> scratch
        assert engine.catalog.get_residency(PATH, 'tmp').state == 'clean'
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'dirty'
        row = queue_row(engine)
        assert row.to_tier == 'project'
        assert scheduler.poll(now=row.last_write + 59.0) == 0
        assert scheduler.poll(now=row.last_write + 60.0) == 1
        assert engine.source.read(PurePosixPath(PATH)) == b'cascade-bytes'
        assert all(residency.state == 'clean' for residency in engine.catalog.residencies(PATH))
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_namespace_ops_age_on_the_source_boundary(tmp_path: Path, clock: Clock) -> None:
    """Queued namespace ops reach the source on the same policy clock as data."""
    engine = make_engine(tmp_path, clock)
    try:
        scheduler = WritebackScheduler(engine)
        engine.mkdir('/results', mode=0o750)
        row = queue_row(engine)
        assert scheduler.poll(now=row.last_write + 59.0) == 0
        assert engine.source.stat(PurePosixPath('/results')) is None
        assert scheduler.poll(now=row.last_write + 60.0) == 1
        assert engine.source.stat(PurePosixPath('/results')).kind == 'dir'
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_mid_copy_write_rearms_instead_of_settling(tmp_path: Path, clock: Clock, monkeypatch) -> None:
    """A write landing during the copy leaves the holder dirty and re-clocks the boundary."""
    engine = make_engine(tmp_path, clock)
    try:
        scheduler = WritebackScheduler(engine)
        dirty(engine, b'first-version')
        row = queue_row(engine)
        real_copy = engine.transfers.copy
        raced = []

        def racy_copy(path, source, dest, **kwargs):
            result = real_copy(path, source, dest, **kwargs)
            if not raced:                                   # one racing write, mid-first-commit only
                raced.append(True)
                (tmp_path / 'scratch/data/file.dat').write_bytes(b'landed-mid-copy!')
            return result

        monkeypatch.setattr(engine.transfers, 'copy', racy_copy)
        assert scheduler.poll(now=row.last_write + 60.0) == 1
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'dirty'
        rearmed = queue_row(engine)
        assert rearmed.id == row.id and rearmed.last_commit is not None
        assert scheduler.poll(now=rearmed.last_write + 60.0) == 1
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'clean'
        assert engine.source.read(PurePosixPath(PATH)) == b'landed-mid-copy!'
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_open_handle_requeues_after_background_commit(tmp_path: Path, clock: Clock) -> None:
    """A commit re-arms open handles, so continued writes re-mark dirty instead of leaking clean."""
    engine = make_engine(tmp_path, clock)
    try:
        scheduler = WritebackScheduler(engine)
        handle = dirty(engine, b'checkpoint-1', keep_open=True)
        assert scheduler.poll(now=queue_row(engine).last_write + 60.0) == 1
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'clean'
        assert handle.marked_dirty is False
        engine.write(handle, b'checkpoint-2', 0)
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'dirty'
        engine.release(handle)
        assert scheduler.poll(now=queue_row(engine).last_write + 60.0) == 1
        assert engine.source.read(PurePosixPath(PATH)) == b'checkpoint-2'
    finally:
        engine.stop()


def test_thread_commits_and_stops_cleanly(tmp_path: Path, clock: Clock) -> None:
    """Lifecycle smoke test: the machine scans, commits, and stops on request."""
    engine = make_engine(tmp_path, clock,
                         scratch_writeback=WritebackSpec(mode='passthrough', delay=0.0))
    try:
        dirty(engine, b'threaded')
        scheduler = WritebackScheduler.new(engine, tick=0.01)
        try:
            for _ in range(500):                            # bounded real-time wait, no policy clocks
                if not engine.journal.queued():
                    break
                time.sleep(0.01)
            assert engine.source.read(PurePosixPath(PATH)) == b'threaded'
        finally:
            scheduler.stop(timeout=5.0)
        assert not scheduler.is_alive()
        assert scheduler.state is WritebackScheduler.states.FINAL
    finally:
        engine.stop()
