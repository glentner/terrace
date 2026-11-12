# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the engine data path: promotion, passthrough I/O, dirty transitions, fsync, flush."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
import dataclasses
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark, raises

# Internal libs
from terrace.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit


def read_all(engine: OverlayEngine, path: str) -> bytes:
    """Open-read-release convenience."""
    handle = engine.open(path, os.O_RDONLY)
    try:
        return engine.read(handle, 1 << 20, 0)
    finally:
        engine.release(handle)


def write_all(engine: OverlayEngine, path: str, data: bytes) -> None:
    """Open-write-release convenience (creates when missing)."""
    try:
        engine.getattr(path)
    except FileNotFoundError:
        engine.create(path)
    handle = engine.open(path, os.O_RDWR)
    try:
        engine.truncate(path, 0, handle=handle)
        engine.write(handle, data, 0)
    finally:
        engine.release(handle)


def test_open_promotes_to_fastest(engine: OverlayEngine) -> None:
    """Opening a source-resident file synchronously stages it on the fastest tier."""
    handle = engine.open('/data/a.txt', os.O_RDONLY)
    try:
        assert handle.tier == 'scratch'
        assert engine.read(handle, 100, 0) == b'alpha'
    finally:
        engine.release(handle)
    residency = engine.catalog.get_residency('/data/a.txt', 'scratch')
    assert residency is not None and residency.state == 'clean'
    assert engine.tiers['scratch'].read(PurePosixPath('/data/a.txt')) == b'alpha'
    assert engine.registry.usage('scratch').files == 1
    assert engine.journal.pending() == []


def test_open_serves_resident_without_recopy(engine: OverlayEngine) -> None:
    """A second open serves the already-resident copy (no new intent activity)."""
    read_all(engine, '/data/a.txt')
    before = engine.catalog.get_residency('/data/a.txt', 'scratch')
    read_all(engine, '/data/a.txt')
    after = engine.catalog.get_residency('/data/a.txt', 'scratch')
    assert after.size == before.size
    assert after.atime > before.atime                       # LRU clock advanced


def test_write_marks_dirty_before_ack(engine: OverlayEngine) -> None:
    """The first write flips the copy dirty and queues write-back; later writes are free."""
    handle = engine.open('/data/a.txt', os.O_RDWR)
    try:
        engine.write(handle, b'ALPHA', 0)
        residency = engine.catalog.get_residency('/data/a.txt', 'scratch')
        assert residency.state == 'dirty'
        rows = engine.journal.queued_for('/data/a.txt', to_tier='project')
        assert [row.op for row in rows] == ['data']
        first_write = rows[0].last_write
        engine.write(handle, b'!', 5)
        assert engine.journal.queued_for('/data/a.txt')[0].last_write == first_write
    finally:
        engine.release(handle)
    assert read_all(engine, '/data/a.txt') == b'ALPHA!'
    assert engine.source.read(PurePosixPath('/data/a.txt')) == b'alpha'   # source untouched until commit


def test_getattr_live_size_while_open(engine: OverlayEngine) -> None:
    """Size grows in getattr while a written handle is open; release records it."""
    engine.create('/out.log')
    handle = engine.open('/out.log', os.O_WRONLY)
    try:
        engine.write(handle, b'x' * 4096, 0)
        assert engine.getattr('/out.log').size == 4096
    finally:
        engine.release(handle)
    assert engine.catalog.get('/out.log').size == 4096
    assert engine.catalog.get_residency('/out.log', 'scratch').size == 4096


def test_truncate_paths(engine: OverlayEngine) -> None:
    """Truncate works with and without a handle and records size."""
    write_all(engine, '/t.dat', b'0123456789')
    engine.truncate('/t.dat', 4)
    assert engine.getattr('/t.dat').size == 4
    assert read_all(engine, '/t.dat') == b'0123'
    handle = engine.open('/t.dat', os.O_RDWR | os.O_TRUNC)
    engine.release(handle)
    assert engine.getattr('/t.dat').size == 0


def test_bypass_size_skips_small_tier(tmp_path: Path, clock: Clock) -> None:
    """A file over a tier's bypass_size is never staged into it."""
    from terrace.tier.spec import QuotaSpec
    seed(tmp_path / 'project', 'big.bin', b'B' * 1000)
    seed(tmp_path / 'project', 'small.bin', b's' * 10)
    spec = build_spec(tmp_path, tiers=3, tmp_quota=QuotaSpec(bypass_size=100))
    with OverlayEngine(spec, clock=clock) as engine:
        big = engine.open('/big.bin')
        small = engine.open('/small.bin')
        try:
            assert big.tier == 'scratch'                    # bypassed tmp (fastest)
            assert small.tier == 'tmp'
        finally:
            engine.release(big)
            engine.release(small)


def test_quota_full_tier_skipped(tmp_path: Path, clock: Clock) -> None:
    """A tier without quota headroom is skipped at promotion."""
    from terrace.tier.spec import QuotaSpec
    seed(tmp_path / 'project', 'a.bin', b'a' * 60)
    seed(tmp_path / 'project', 'b.bin', b'b' * 60)
    spec = build_spec(tmp_path, tiers=2, scratch_quota=QuotaSpec(size=100))
    with OverlayEngine(spec, clock=clock) as engine:
        first = engine.open('/a.bin')
        engine.release(first)
        second = engine.open('/b.bin')                      # 60 + 60 > 100: no headroom
        engine.release(second)
        assert first.tier == 'scratch'
        assert second.tier == 'project'


def test_commit_upstream_cascades(tmp_path: Path, clock: Clock) -> None:
    """commit_upstream walks the file hop by hop to the source and settles the queue."""
    seed(tmp_path / 'project', 'f.dat', b'old')
    spec = build_spec(tmp_path, tiers=3)
    with OverlayEngine(spec, clock=clock) as engine:
        write_all(engine, '/f.dat', b'new-content')
        assert engine.catalog.get_residency('/f.dat', 'tmp').state == 'dirty'
        engine.commit_upstream('/f.dat')
        assert engine.source.read(PurePosixPath('/f.dat')) == b'new-content'
        for tier in ('project', 'scratch', 'tmp'):
            residency = engine.catalog.get_residency('/f.dat', tier)
            assert residency is not None and residency.state == 'clean'
        assert engine.journal.queued() == []
        assert engine.journal.pending() == []


def test_fsync_commit_policy(tmp_path: Path, clock: Clock) -> None:
    """Under fsync_policy='commit', fsync forces the file to the source now."""
    seed(tmp_path / 'project', 'f.dat', b'old')
    spec = dataclasses.replace(build_spec(tmp_path, tiers=2), fsync_policy='commit')
    with OverlayEngine(spec, clock=clock) as engine:
        handle = engine.open('/f.dat', os.O_RDWR)
        try:
            engine.truncate('/f.dat', 0, handle=handle)
            engine.write(handle, b'durable', 0)
            engine.fsync(handle)
            assert engine.source.read(PurePosixPath('/f.dat')) == b'durable'
            engine.write(handle, b'!', 7)                   # re-marks dirty after the commit
            assert engine.catalog.get_residency('/f.dat', 'scratch').state == 'dirty'
        finally:
            engine.release(handle)


def test_fsync_resident_policy_stays_local(engine: OverlayEngine) -> None:
    """Default fsync records metadata but does not force the commit."""
    write_all(engine, '/r.dat', b'resident-only')
    handle = engine.open('/r.dat', os.O_RDWR)
    try:
        engine.write(handle, b'X', 0)
        engine.fsync(handle)
    finally:
        engine.release(handle)
    assert engine.source.stat(PurePosixPath('/r.dat')) is None
    assert engine.catalog.get_residency('/r.dat', 'scratch').state == 'dirty'


def test_flush_converges_source(engine: OverlayEngine) -> None:
    """flush executes namespace ops then data commits; source matches the overlay view."""
    engine.mkdir('/results', mode=0o750)
    write_all(engine, '/results/out.dat', b'payload')
    write_all(engine, '/data/a.txt', b'rewritten')
    engine.unlink('/data/b.txt')
    engine.rename('/top.txt', '/results/top.txt')
    engine.flush()
    source = engine.source
    assert source.stat(PurePosixPath('/results')).mode == 0o750
    assert source.read(PurePosixPath('/results/out.dat')) == b'payload'
    assert source.read(PurePosixPath('/data/a.txt')) == b'rewritten'
    assert source.stat(PurePosixPath('/data/b.txt')) is None
    assert source.read(PurePosixPath('/results/top.txt')) == b'top-level'
    assert source.stat(PurePosixPath('/top.txt')) is None
    assert engine.journal.queued() == []
    assert engine.journal.pending() == []
    assert engine.catalog.get('/top.txt', include_deleted=True) is None   # rename tombstone purged


def test_unlink_while_open_posix_semantics(engine: OverlayEngine) -> None:
    """An open handle survives unlink; release records nothing on the dead entry."""
    write_all(engine, '/doomed.dat', b'still-readable')
    handle = engine.open('/doomed.dat', os.O_RDONLY)
    try:
        engine.unlink('/doomed.dat')
        assert engine.read(handle, 100, 0) == b'still-readable'
    finally:
        engine.release(handle)
    with raises(FileNotFoundError):
        engine.getattr('/doomed.dat')


def test_open_directory_fails(engine: OverlayEngine) -> None:
    """EISDIR for opening a directory as a file."""
    with raises(IsADirectoryError):
        engine.open('/data')
