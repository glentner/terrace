# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for the bundle loop scenario."""

# Type annotations
from __future__ import annotations
from typing import Iterator

# External libs
from pytest import fixture, skip

# Without these the loop's tests skip by name, as the S3 tier tests do.
try:
    import boto3                                    # noqa: F401
    import moto.server                              # noqa: F401
    S3_UNAVAILABLE = None
except ImportError as error:
    S3_UNAVAILABLE = f'the bundle loop needs {error.name}: {error}'

# Internal libs
from tests.loop import bundle_loop


@fixture(scope='session')
def store() -> Iterator[str]:
    """The helper's own object store, one per worker."""
    if S3_UNAVAILABLE:
        skip(S3_UNAVAILABLE)
    with bundle_loop.Store() as running:
        yield running.endpoint
