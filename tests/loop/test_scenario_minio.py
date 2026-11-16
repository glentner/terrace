# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The bundle loop, stage by stage, against the S3 endpoint the TERRACE_TEST_MINIO_* variables name.

The same nine stages as `test_scenario.py`, under the same markers, with the defaults and the skip of
`tests/tier/test_s3_integration.py`: a local MinIO on 127.0.0.1:9000 with the stock credentials,
which `docker compose up -d --wait` brings up. Written and collected; not yet run against MinIO on
any machine (`issues/ci-integration-lane.md`).
"""

# Type annotations
from __future__ import annotations
from typing import Callable, Iterator

# Standard libs
import os
from pathlib import Path

# External libs
from pytest import fixture, mark

# Internal libs
from tests.loop.conftest import scenes
from tests.loop.stages import Scene
from tests.loop.stages import (test_stage_1_reach, test_stage_2_read, test_stage_3_pressure,     # noqa: F401
                               test_stage_4_change, test_stage_5_reaggregate, test_stage_6_read_back,
                               test_stage_7_release, test_stage_8_offline_commit, test_stage_9_several_bundles)

ENDPOINT = os.environ.get('TERRACE_TEST_MINIO_ENDPOINT', 'http://127.0.0.1:9000')
ACCESS_KEY = os.environ.get('TERRACE_TEST_MINIO_ACCESS_KEY', 'minioadmin')
SECRET_KEY = os.environ.get('TERRACE_TEST_MINIO_SECRET_KEY', 'minioadmin')
REGION = os.environ.get('TERRACE_TEST_MINIO_REGION', 'us-east-1')


def _minio_available() -> bool:
    """Whether a MinIO/S3 endpoint is reachable for integration testing."""
    try:
        import boto3
        from botocore.config import Config
        boto3.client('s3', endpoint_url=ENDPOINT, region_name=REGION, aws_access_key_id=ACCESS_KEY,
                     aws_secret_access_key=SECRET_KEY,
                     config=Config(connect_timeout=1, read_timeout=3, retries={'max_attempts': 0})).list_buckets()
        return True
    except Exception:
        return False


pytestmark = [mark.integration,
              mark.skipif(not _minio_available(), reason=f'requires a reachable S3/MinIO endpoint at {ENDPOINT}')]


@fixture
def scene(tmp_path: Path) -> Iterator[Callable[..., Scene]]:
    """Scenes over the MinIO endpoint."""
    yield from scenes(ENDPOINT, tmp_path, access_key=ACCESS_KEY, secret_key=SECRET_KEY, region=REGION)
