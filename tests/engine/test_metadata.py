# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the engine setattr path: chmod/chown/utimens via the `meta` write-back op."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.engine.overlay import OverlayEngine
from sosfs.engine.writeback import WritebackScheduler
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit

PATH = '/data/a.txt'


def make_engine(tmp_path: Path, clock: Clock, tiers: int = 2) -> OverlayEngine:
    """A started engine over a seeded source file."""
    seed(tmp_path / 'project', 'data/a.txt', b'alpha', mode=0o644, mtime=50.0)
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers), clock=clock)
    engine.start()
    return engine


def source_stat(engine: OverlayEngine, path: str = PATH):
    """The source tier's on-disk stat for `path`."""
    return engine.source.stat(PurePosixPath(path))


def test_chmod_updates_catalog_and_queues_meta(tmp_path: Path, clock: Clock) -> None:
    """chmod updates the catalog immediately and queues a single `meta` op toward the source."""
    engine = make_engine(tmp_path, clock)
    try:
        engine.chmod(PATH, 0o600)
        assert engine.getattr(PATH).mode == 0o600            # authoritative at once
        rows = engine.journal.queued()
        assert len(rows) == 1 and rows[0].op == 'meta' and rows[0].to_tier == 'project'
    finally:
        engine.stop()


def test_chmod_never_dirties_content(tmp_path: Path, clock: Clock) -> None:
    """A chmod on a clean, promoted file leaves every residency clean and queues no data row."""
    engine = make_engine(tmp_path, clock)
    try:
        engine.release(engine.open(PATH, os.O_RDONLY))       # promote a clean copy to scratch
        engine.chmod(PATH, 0o600)
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'clean'
        assert engine.catalog.get_residency(PATH, 'project').state == 'clean'
        assert all(row.op != 'data' for row in engine.journal.queued())
    finally:
        engine.stop()


def test_chmod_applies_to_holder_and_flushes_to_source(tmp_path: Path, clock: Clock) -> None:
    """The change lands on the fastest resident copy at once and reaches the source on flush."""
    engine = make_engine(tmp_path, clock)
    try:
        engine.release(engine.open(PATH, os.O_RDONLY))       # copy now resident on scratch
        engine.chmod(PATH, 0o600)
        assert engine.tiers['scratch'].stat(PurePosixPath(PATH)).mode == 0o600
        assert source_stat(engine).mode == 0o644             # source untouched until the queue drains
        engine.flush()
        assert source_stat(engine).mode == 0o600
    finally:
        engine.stop()


def test_utimens_sets_times_and_propagates(tmp_path: Path, clock: Clock) -> None:
    """utimens records both times in the catalog and carries mtime to the source."""
    engine = make_engine(tmp_path, clock)
    try:
        engine.utimens(PATH, atime=555.0, mtime=777.0)
        entry = engine.getattr(PATH)
        assert (entry.atime, entry.mtime) == (555.0, 777.0)
        engine.flush()
        assert source_stat(engine).mtime == 777.0
    finally:
        engine.stop()


def test_chown_records_intent_best_effort(tmp_path: Path, clock: Clock) -> None:
    """chown records ownership in the catalog and applies it best-effort to the source."""
    engine = make_engine(tmp_path, clock)
    try:
        entry = engine.chown(PATH, uid=os.getuid(), gid=os.getgid())
        assert (entry.uid, entry.gid) == (os.getuid(), os.getgid())
        assert engine.journal.queued()[0].op == 'meta'
        engine.flush()                                       # applies to the source without error
        stat = source_stat(engine)
        assert (stat.uid, stat.gid) == (os.getuid(), os.getgid())
    finally:
        engine.stop()


def test_chmod_on_uncommitted_new_file_reaches_source(tmp_path: Path, clock: Clock) -> None:
    """A chmod on a freshly created file (no source copy yet) still lands correctly on flush."""
    engine = make_engine(tmp_path, clock)
    try:
        engine.create('/new.txt', 0o644)                     # staged and dirty; not yet on the source
        engine.chmod('/new.txt', 0o600)
        engine.flush()                                       # meta op skips the absent source; the data commit carries mode
        assert source_stat(engine, '/new.txt').mode == 0o600
    finally:
        engine.stop()


def test_meta_rides_the_writeback_clock(tmp_path: Path, clock: Clock) -> None:
    """A `meta` op ages on the source-boundary policy clock, like any namespace op."""
    engine = make_engine(tmp_path, clock)
    try:
        engine.chmod(PATH, 0o600)
        row = engine.journal.queued()[0]
        scheduler = WritebackScheduler(engine)
        assert scheduler.poll(now=row.last_write + 59.0) == 0
        assert source_stat(engine).mode == 0o644             # not due yet
        assert scheduler.poll(now=row.last_write + 60.0) == 1
        assert source_stat(engine).mode == 0o600
        assert engine.journal.queued() == []
    finally:
        engine.stop()


def test_chmod_survives_partial_cascade(tmp_path: Path, clock: Clock) -> None:
    """Regression: a chmod while the dirty holder is upstream of the fastest (clean) copy must
    not be reverted when the content commit lands (content commit carries authoritative mode)."""
    engine = make_engine(tmp_path, clock, tiers=3)          # project <- scratch <- tmp
    try:
        handle = engine.open(PATH, os.O_RDWR)
        engine.write(handle, b'BRAVO', 0)
        engine.release(handle)                              # dirty on tmp (fastest)
        row = engine.journal.queued()[0]
        WritebackScheduler(engine).poll(now=row.last_write + 60.0)   # hop tmp -> scratch
        assert engine.catalog.get_residency(PATH, 'tmp').state == 'clean'     # fastest, clean
        assert engine.catalog.get_residency(PATH, 'scratch').state == 'dirty'  # holder, upstream
        engine.chmod(PATH, 0o600)
        engine.flush()
        assert engine.source.stat(PurePosixPath(PATH)).mode == 0o600
        assert engine.source.read(PurePosixPath(PATH)) == b'BRAVO'
    finally:
        engine.stop()


def test_utimens_survives_partial_cascade(tmp_path: Path, clock: Clock) -> None:
    """Regression: a utimens mid-cascade reaches the source, carried by the dirty holder's copy."""
    engine = make_engine(tmp_path, clock, tiers=3)
    try:
        handle = engine.open(PATH, os.O_RDWR)
        engine.write(handle, b'BRAVO', 0)
        engine.release(handle)
        row = engine.journal.queued()[0]
        WritebackScheduler(engine).poll(now=row.last_write + 60.0)   # hop tmp -> scratch
        engine.utimens(PATH, atime=555.0, mtime=777.0)
        engine.flush()
        assert engine.source.stat(PurePosixPath(PATH)).mtime == 777.0
        assert engine.source.read(PurePosixPath(PATH)) == b'BRAVO'
    finally:
        engine.stop()


def test_chmod_does_not_revert_mtime(tmp_path: Path, clock: Clock) -> None:
    """Regression: a mode-only chmod must leave the modification time alone."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = engine.open(PATH, os.O_RDWR)
        engine.write(handle, b'XXXX', 0)                    # bumps the holder's on-disk mtime to now
        engine.chmod(PATH, 0o600)                           # mode only — must not touch mtime
        engine.release(handle)
        assert engine.getattr(PATH).mtime > 50.0            # the seed mtime, reverted by the bug
    finally:
        engine.stop()


def test_setattr_missing_path_raises(tmp_path: Path, clock: Clock) -> None:
    """setattr on a missing path raises ENOENT."""
    engine = make_engine(tmp_path, clock)
    try:
        with raises(FileNotFoundError):
            engine.chmod('/nope', 0o600)
    finally:
        engine.stop()
