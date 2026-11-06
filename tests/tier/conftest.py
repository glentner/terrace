# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for S3 tier tests: a session-lived moto server and a per-test S3Tier."""

# Type annotations
from __future__ import annotations
from typing import Iterator

# Standard libs
from uuid import uuid4

# External libs
from pytest import fixture, skip
from obstore.store import S3Store

# Without these the S3 tests skip by name, and the modules that need no server still run.
try:
    import boto3
    from moto.server import ThreadedMotoServer
    S3_UNAVAILABLE = None
except ImportError as error:
    S3_UNAVAILABLE = f'S3 tier tests need {error.name}: {error}'

# Internal libs
from sosfs.tier.s3 import S3Tier
from sosfs.tier.spec import S3Spec


CREDENTIALS = {'aws_access_key_id': 'test', 'aws_secret_access_key': 'test'}
REGION = 'us-east-1'


@fixture(scope='session')
def moto_endpoint() -> Iterator[str]:
    """A moto S3 server for the whole session (pure-Python, real S3 HTTP for obstore)."""
    if S3_UNAVAILABLE:
        skip(S3_UNAVAILABLE)
    server = ThreadedMotoServer(ip_address='127.0.0.1', port=0)
    server.start()
    _, port = server.get_host_and_port()
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        server.stop()


@fixture
def s3_bucket(moto_endpoint: str) -> str:
    """A fresh, uniquely named bucket per test (moto state is shared across the session)."""
    name = f'sosfs-test-{uuid4().hex[:12]}'
    client = boto3.client('s3', endpoint_url=moto_endpoint, region_name=REGION, **CREDENTIALS)
    client.create_bucket(Bucket=name)
    return name


@fixture
def s3_spec(moto_endpoint: str, s3_bucket: str) -> S3Spec:
    """An S3Spec pointing at the per-test bucket under a 'space' prefix."""
    return S3Spec(bucket=s3_bucket, prefix='space', region=REGION, endpoint=moto_endpoint,
                  access_key_id='test', secret_access_key='test', allow_http=True)


@fixture
def store(moto_endpoint: str, s3_bucket: str) -> S3Store:
    """A ready obstore S3Store over the per-test bucket."""
    return S3Store(s3_bucket, prefix='space', endpoint=moto_endpoint, region=REGION,
                   access_key_id='test', secret_access_key='test', client_options={'allow_http': True})


@fixture
def tier(store: S3Store) -> S3Tier:
    """An S3Tier over a fresh bucket."""
    return S3Tier('cloud', store)
