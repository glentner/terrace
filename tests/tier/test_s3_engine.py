# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""End-to-end engine tests over an S3 source with a POSIX staging tier (moto-backed, mount-free)."""

# Type annotations
from __future__ import annotations
from typing import Tuple

# Standard libs
import io
import os
from pathlib import PurePosixPath as P

# External libs
from pytest import fixture, mark

# Internal libs
from terrace.tier.spec import OverlaySpec, TierSpec, WritebackSpec, S3Spec
from terrace.tier.s3 import S3Tier
from terrace.tier.protocol import FileMeta
from terrace.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock


pytestmark = mark.unit


@fixture
def s3_stack(tmp_path, moto_endpoint: str, s3_bucket: str) -> Tuple[OverlaySpec, S3Tier]:
    """An overlay spec (S3 source -> POSIX scratch stage) plus an S3Tier for out-of-band seeding."""
    url = f's3://{s3_bucket}/space'
    connection = S3Spec(bucket=s3_bucket, prefix='space', region='us-east-1', endpoint=moto_endpoint,
                        access_key_id='test', secret_access_key='test', allow_http=True)
    source = TierSpec(name='cloud', type='s3', path=url, url=url, s3=connection, role='source')
    scratch = TierSpec(name='scratch', type='posix', path=str(tmp_path / 'scratch'),
                       role='stage', writeback=WritebackSpec())
    spec = OverlaySpec(name='cloudspace', state=tmp_path / 'state', tiers=(source, scratch))
    return spec, S3Tier.from_spec('cloud', connection)


@fixture
def engine(s3_stack: Tuple[OverlaySpec, S3Tier]) -> OverlayEngine:
    """A started engine over an initially empty S3 source."""
    spec, _ = s3_stack
    instance = OverlayEngine(spec, clock=Clock())
    instance.start()
    yield instance
    if instance.started:
        instance.stop()


def write_file(engine: OverlayEngine, path: str, data: bytes) -> None:
    """create + write + release a file through the engine."""
    engine.create(path)
    handle = engine.open(path, os.O_WRONLY)
    try:
        engine.write(handle, data, 0)
    finally:
        engine.release(handle)


def test_marker_written_to_s3_source(engine: OverlayEngine) -> None:
    """Attaching the stack writes the managed-location marker into the bucket."""
    marker = engine.tiers['cloud'].read_marker()
    assert marker is not None and marker.tier == 'cloud'


def test_create_write_flush_lands_in_s3(s3_stack, engine: OverlayEngine) -> None:
    """A created-and-written file commits to the S3 source on flush."""
    _, reader = s3_stack
    write_file(engine, '/f.dat', b'hello object storage')
    engine.flush()
    assert reader.read(P('/f.dat')) == b'hello object storage'


def test_promote_on_open_from_s3_source(s3_stack) -> None:
    """A file pre-existing in the S3 source promotes to the POSIX stage on open and reads back."""
    spec, seed = s3_stack
    seed.put(P('/dataset.bin'), io.BytesIO(b'x' * 200), meta=FileMeta(size=200, mtime=1.0))
    engine = OverlayEngine(spec, clock=Clock())
    engine.start()
    try:
        handle = engine.open('/dataset.bin', os.O_RDONLY)
        try:
            assert engine.read(handle, 1 << 20, 0) == b'x' * 200
        finally:
            engine.release(handle)
        assert engine.tiers['scratch'].stat(P('/dataset.bin')) is not None      # promoted to staging
    finally:
        engine.stop()


def test_nested_file_flush_materializes_s3_directories(s3_stack, engine: OverlayEngine) -> None:
    """Committing a nested file creates its ancestor directories in the S3 source."""
    _, reader = s3_stack
    engine.mkdir('/sub')
    write_file(engine, '/sub/deep.dat', b'nested')
    engine.flush()
    assert reader.stat(P('/sub')).kind == 'dir'
    assert reader.read(P('/sub/deep.dat')) == b'nested'


def test_rename_flush_moves_in_s3(s3_stack, engine: OverlayEngine) -> None:
    """A rename committed to the S3 source relocates the object (flush between mutations)."""
    _, reader = s3_stack
    write_file(engine, '/old.dat', b'payload')
    engine.flush()
    engine.rename('/old.dat', '/new.dat')
    engine.flush()
    assert reader.stat(P('/old.dat')) is None
    assert reader.read(P('/new.dat')) == b'payload'


def test_unlink_flush_removes_from_s3(s3_stack, engine: OverlayEngine) -> None:
    """An unlink committed to the S3 source deletes the object."""
    _, reader = s3_stack
    write_file(engine, '/gone.dat', b'temp')
    engine.flush()
    engine.unlink('/gone.dat')
    engine.flush()
    assert reader.stat(P('/gone.dat')) is None


def test_readdir_reflects_s3_source(s3_stack, engine: OverlayEngine) -> None:
    """readdir over the overlay lists what the S3 source (plus staged writes) holds."""
    _, reader = s3_stack
    seed = reader
    seed.put(P('/seeded.dat'), io.BytesIO(b'seed'), meta=FileMeta(size=4, mtime=1.0))
    write_file(engine, '/created.dat', b'made')
    names = sorted(entry.path.rsplit('/', 1)[-1] for entry in engine.readdir('/'))
    assert 'seeded.dat' in names and 'created.dat' in names


def test_modify_promoted_file_writes_back_to_s3(s3_stack, engine: OverlayEngine) -> None:
    """Opening, modifying, and flushing a source-resident file rewrites it in S3."""
    _, reader = s3_stack
    write_file(engine, '/edit.dat', b'v1')
    engine.flush()
    handle = engine.open('/edit.dat', os.O_RDWR)
    try:
        engine.write(handle, b'V2!', 0)
    finally:
        engine.release(handle)
    engine.flush()
    assert reader.read(P('/edit.dat')) == b'V2!'
