# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the per-file lock registry."""

# Type annotations
from __future__ import annotations
from typing import List

# Standard libs
import threading

# External libs
from pytest import mark

# Internal libs
from terrace.engine.locks import LockRegistry


pytestmark = mark.unit


def test_mutual_exclusion() -> None:
    """Two threads never occupy the critical section for one path at once."""
    registry = LockRegistry()
    active = 0
    overlaps: List[int] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        nonlocal active
        barrier.wait(timeout=30)
        for _ in range(200):
            with registry.hold('/a/file'):
                active += 1
                overlaps.append(active)
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert max(overlaps) == 1
    assert len(overlaps) == 800


def test_distinct_paths_do_not_block() -> None:
    """A held lock on one path leaves other paths free."""
    registry = LockRegistry()
    entered = threading.Event()

    def worker() -> None:
        with registry.hold('/b'):
            entered.set()

    with registry.hold('/a'):
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(timeout=5)
        thread.join(timeout=5)
    assert len(registry) == 0


def test_reentrant_per_thread() -> None:
    """Nested holds of the same path by one thread compose."""
    registry = LockRegistry()
    with registry.hold('/a'):
        with registry.hold('/a', '/b'):
            assert len(registry) == 2
    assert len(registry) == 0


def test_multi_path_ordering_avoids_deadlock() -> None:
    """Opposite-order pair holds (rename A->B racing rename B->A) always complete."""
    registry = LockRegistry()
    barrier = threading.Barrier(2)
    done = []

    def worker(first: str, second: str) -> None:
        barrier.wait(timeout=30)
        for _ in range(100):
            with registry.hold(first, second):
                done.append(threading.get_ident())

    threads = [threading.Thread(target=worker, args=('/a', '/b')),
               threading.Thread(target=worker, args=('/b', '/a'))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(done) == 200 and len(registry) == 0


def test_registry_shrinks_to_zero() -> None:
    """Entries are discarded when the last holder releases."""
    registry = LockRegistry()
    with registry.hold('/x/y', '/x/z'):
        assert len(registry) == 2
    assert len(registry) == 0
