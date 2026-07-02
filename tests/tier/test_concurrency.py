# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for counter correctness and put atomicity under concurrent load."""

# Type annotations
from __future__ import annotations
from typing import Callable, List

# Standard libs
import io
import threading
from pathlib import Path, PurePosixPath

# External libs
from pytest import fixture, mark

# Internal libs
from sosfs.state.db import Database
from sosfs.state.registry import TierRegistry
from sosfs.tier.protocol import FileMeta, TierUsage
from sosfs.tier.posix import PosixTier


pytestmark = mark.unit

THREADS = 8
ROUNDS = 25


@fixture
def db(tmp_path: Path) -> Database:
    """An initialized state database."""
    database = Database(tmp_path / 'state.db')
    database.initialize()
    yield database
    database.close()


def run_threads(target: Callable[[int], None]) -> None:
    """Run `target(thread_id)` in THREADS threads behind a barrier; re-raise any failure."""
    barrier = threading.Barrier(THREADS)
    errors: List[BaseException] = []

    def wrapped(thread_id: int) -> None:
        try:
            barrier.wait(timeout=30)
            target(thread_id)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=wrapped, args=(i, )) for i in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    if errors:
        raise errors[0]


def test_concurrent_counter_adjustments(db: Database) -> None:
    """No increment is lost across THREADS writers on their own connections."""
    registry = TierRegistry(db)
    registry.register('scratch', position=1, type='posix', role='stage', scope='shared')

    def hammer(thread_id: int) -> None:
        for _ in range(ROUNDS):
            registry.adjust_usage('scratch', bytes=10, files=1)

    run_threads(hammer)
    assert registry.usage('scratch') == TierUsage(bytes=10 * THREADS * ROUNDS, files=THREADS * ROUNDS)


def test_concurrent_puts_wire_counters_exactly(db: Database, tmp_path: Path) -> None:
    """The engine flow — put then adjust — accounts every file exactly once under contention."""
    root = tmp_path / 'tier'
    root.mkdir()
    tier = PosixTier('scratch', root)
    registry = TierRegistry(db)
    registry.register('scratch', position=1, type='posix', role='stage', scope='shared')

    def stage(thread_id: int) -> None:
        for index in range(ROUNDS):
            data = bytes([thread_id]) * (index + 1)
            tier.put(PurePosixPath(f'/t{thread_id}-{index}'), io.BytesIO(data),
                     meta=FileMeta(size=len(data), mtime=100.0))
            registry.adjust_usage('scratch', bytes=len(data), files=1)

    run_threads(stage)
    expected = TierUsage(bytes=THREADS * sum(range(1, ROUNDS + 1)), files=THREADS * ROUNDS)
    assert registry.usage('scratch') == expected
    assert tier.usage() == expected


def test_concurrent_puts_same_path_atomic(tmp_path: Path) -> None:
    """Racing puts to one path always leave exactly one writer's complete payload."""
    root = tmp_path / 'tier'
    root.mkdir()
    tier = PosixTier('scratch', root)
    payloads = {bytes([thread_id]) * 4096 for thread_id in range(THREADS)}

    def overwrite(thread_id: int) -> None:
        data = bytes([thread_id]) * 4096
        for _ in range(ROUNDS):
            tier.put(PurePosixPath('/contested'), io.BytesIO(data),
                     meta=FileMeta(size=len(data), mtime=100.0))

    run_threads(overwrite)
    assert tier.read(PurePosixPath('/contested')) in payloads
    assert tier.usage() == TierUsage(bytes=4096, files=1)
