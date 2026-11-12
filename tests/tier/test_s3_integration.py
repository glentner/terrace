# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Integration tests: an S3 source with a POSIX staging stack against a real MinIO server.

Point at a MinIO (or any S3) endpoint with the TERRACE_TEST_MINIO_* environment variables;
the suite skips when none is reachable. Defaults target a local MinIO on 127.0.0.1:9000
with the stock minioadmin credentials — `docker compose up -d --wait` (see docker-compose.yml
at the repo root) brings up exactly that.
"""

# Type annotations
from __future__ import annotations
from typing import Iterator, Tuple

# Standard libs
import io
import os
from uuid import uuid4
from pathlib import PurePosixPath as P

# External libs
from pytest import fixture, mark

# Internal libs
from terrace.tier.spec import OverlaySpec, TierSpec, WritebackSpec, S3Spec
from terrace.tier.s3 import S3Tier
from terrace.tier.protocol import FileMeta
from terrace.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock


ENDPOINT = os.environ.get('TERRACE_TEST_MINIO_ENDPOINT', 'http://127.0.0.1:9000')
ACCESS_KEY = os.environ.get('TERRACE_TEST_MINIO_ACCESS_KEY', 'minioadmin')
SECRET_KEY = os.environ.get('TERRACE_TEST_MINIO_SECRET_KEY', 'minioadmin')
REGION = os.environ.get('TERRACE_TEST_MINIO_REGION', 'us-east-1')


def _minio_client():
    """A boto3 S3 client for the configured MinIO endpoint (fast-failing)."""
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url=ENDPOINT, region_name=REGION,
                        aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY,
                        config=Config(connect_timeout=1, read_timeout=3, retries={'max_attempts': 0}))


def _minio_available() -> bool:
    """Whether a MinIO/S3 endpoint is reachable for integration testing."""
    try:
        _minio_client().list_buckets()
        return True
    except Exception:
        return False


pytestmark = [mark.integration,
              mark.skipif(not _minio_available(), reason=f'requires a reachable S3/MinIO endpoint at {ENDPOINT}')]


@fixture
def minio_stack(tmp_path) -> Iterator[Tuple[OverlaySpec, S3Tier]]:
    """A fresh bucket, an overlay spec (MinIO source -> POSIX stage), and a seeding tier; bucket removed after."""
    bucket = f'terrace-it-{uuid4().hex[:12]}'
    client = _minio_client()
    client.create_bucket(Bucket=bucket)
    url = f's3://{bucket}/space'
    connection = S3Spec(bucket=bucket, prefix='space', region=REGION, endpoint=ENDPOINT,
                        access_key_id=ACCESS_KEY, secret_access_key=SECRET_KEY,
                        allow_http=ENDPOINT.startswith('http://'))
    source = TierSpec(name='cloud', type='s3', path=url, url=url, s3=connection, role='source')
    scratch = TierSpec(name='scratch', type='posix', path=str(tmp_path / 'scratch'),
                       role='stage', writeback=WritebackSpec())
    spec = OverlaySpec(name='cloudspace', state=tmp_path / 'state', tiers=(source, scratch))
    try:
        yield spec, S3Tier.from_spec('cloud', connection)
    finally:
        try:
            for obj in client.list_objects_v2(Bucket=bucket).get('Contents', []):
                client.delete_object(Bucket=bucket, Key=obj['Key'])
            client.delete_bucket(Bucket=bucket)
        except Exception:
            pass


def _write(engine: OverlayEngine, path: str, data: bytes) -> None:
    engine.create(path)
    handle = engine.open(path, os.O_WRONLY)
    try:
        engine.write(handle, data, 0)
    finally:
        engine.release(handle)


def test_end_to_end_round_trip(minio_stack) -> None:
    """create -> write -> flush lands in MinIO; a nested file materializes its directory."""
    spec, reader = minio_stack
    engine = OverlayEngine(spec, clock=Clock())
    engine.start()
    try:
        _write(engine, '/top.dat', b'top-level bytes')
        engine.mkdir('/sub')
        _write(engine, '/sub/deep.dat', b'nested bytes')
        engine.flush()
    finally:
        engine.stop()
    assert reader.read(P('/top.dat')) == b'top-level bytes'
    assert reader.stat(P('/sub')).kind == 'dir'
    assert reader.read(P('/sub/deep.dat')) == b'nested bytes'


def test_promote_on_open_from_minio(minio_stack) -> None:
    """A file pre-existing in MinIO promotes to the POSIX stage on open and reads back correctly."""
    spec, seed = minio_stack
    payload = os.urandom(4096)
    seed.put(P('/dataset.bin'), io.BytesIO(payload), meta=FileMeta(size=len(payload), mtime=1.0))
    engine = OverlayEngine(spec, clock=Clock())
    engine.start()
    try:
        handle = engine.open('/dataset.bin', os.O_RDONLY)
        try:
            assert engine.read(handle, 1 << 20, 0) == payload
        finally:
            engine.release(handle)
        assert engine.tiers['scratch'].stat(P('/dataset.bin')) is not None
    finally:
        engine.stop()


def test_rename_and_unlink_commit_to_minio(minio_stack) -> None:
    """Rename and unlink committed to the MinIO source relocate/remove the object (flush between mutations)."""
    spec, reader = minio_stack
    engine = OverlayEngine(spec, clock=Clock())
    engine.start()
    try:
        _write(engine, '/old.dat', b'payload')
        engine.flush()
        engine.rename('/old.dat', '/new.dat')
        engine.flush()
        assert reader.stat(P('/old.dat')) is None
        assert reader.read(P('/new.dat')) == b'payload'
        engine.unlink('/new.dat')
        engine.flush()
        assert reader.stat(P('/new.dat')) is None
    finally:
        engine.stop()
