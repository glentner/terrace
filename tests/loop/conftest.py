# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for the bundle loop scenario, and the record of what stopped each stage."""

# Type annotations
from __future__ import annotations
from typing import Any, Callable, Iterator, List

# Standard libs
from pathlib import Path

# External libs
import pytest
from pytest import fixture, hookimpl, skip

# Without these the loop's tests skip by name, as the S3 tier tests do.
try:
    import boto3                                    # noqa: F401
    import moto.server                              # noqa: F401
    S3_UNAVAILABLE = None
except ImportError as error:
    S3_UNAVAILABLE = f'the bundle loop needs {error.name}: {error}'

# The stages and the oracle assert in plain `assert`; rewriting must be registered before they import.
pytest.register_assert_rewrite('tests.loop.stages', 'tests.loop.oracle')

# Internal libs
from tests.engine.conftest import Clock
from tests.loop import bundle_loop
from tests.loop.tap import Tap
from tests.loop.stages import Scene, STAGES


@hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[None]:
    """
    Put on each stage what stopped it and the marker it was collected with, for the gates to read. A
    reason string alone can be had by a stage that excuses itself; these cannot. The JUnit report
    takes a test's properties from the item when the test is torn down.
    """
    yield
    if item.name not in STAGES:
        return
    if call.when == 'setup':
        marks = [f'xfail {sorted(found.kwargs)} raises={getattr(found.kwargs.get("raises"), "__name__", None)} '
                 f'strict={found.kwargs.get("strict")} args={len(found.args)}' for found in item.iter_markers('xfail')]
        item.user_properties.append(('marker', '; '.join(marks)))
    if call.excinfo is not None and call.when in ('setup', 'call'):
        error = call.excinfo.value
        item.user_properties.append(('stopped', f'{type(error).__name__}: {error}'))
        item.user_properties.append(('cause', type(error.__cause__).__name__))


@fixture(scope='session')
def store() -> Iterator[str]:
    """The helper's own object store, one per worker."""
    if S3_UNAVAILABLE:
        skip(S3_UNAVAILABLE)
    with bundle_loop.Store() as running:
        yield running.endpoint


@fixture
def clock() -> Clock:
    return Clock()


def scenes(endpoint: str, tmp_path: Path, **credentials: Any) -> Iterator[Callable[..., Scene]]:
    """
    A factory of scenes over `endpoint`: a tap in front of it, a loop built through the tap, and a
    check of every bundle's object against the oracle before a stage sees it. Everything it built is
    torn down afterwards.
    """
    import boto3
    made: List[Callable[[], None]] = []
    names = {'access_key': 'aws_access_key_id', 'secret_key': 'aws_secret_access_key', 'region': 'region_name'}
    settings = {'access_key': 'test', 'secret_key': 'test', 'region': 'us-east-1'} | credentials
    # The oracle reads objects from the store itself, so it neither adds to a window nor depends on the tap.
    direct = boto3.client('s3', endpoint_url=endpoint, **{names[key]: value for key, value in settings.items()})

    def build(**options: Any) -> Scene:
        tap = Tap(endpoint).start()
        made.append(tap.stop)
        loop = bundle_loop.Loop(tap.endpoint, parent=tmp_path, **credentials, **options)
        made.append(loop.close)
        scene = Scene(loop=loop, tap=tap,
                      fetch=lambda key: direct.get_object(Bucket=loop.bucket, Key=key)['Body'].read())
        for stem in loop.stems:
            scene.assert_object(stem)
        return scene

    try:
        yield build
    finally:
        for undo in reversed(made):
            undo()


@fixture
def scene(store: str, tmp_path: Path) -> Iterator[Callable[..., Scene]]:
    """Scenes over the helper's own store."""
    yield from scenes(store, tmp_path)
