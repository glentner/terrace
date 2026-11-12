# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Per-file lock registry for the overlay engine."""

# Type annotations
from __future__ import annotations
from typing import Dict, Tuple, Iterator

# Standard libs
import threading
from contextlib import contextmanager

# Public interface
__all__ = ['LockRegistry']


class LockRegistry:
    """
    Path-keyed reentrant locks, created on demand and discarded when the last
    holder releases — the registry never outgrows the set of in-flight paths.

    Locks are reentrant per thread, so engine operations compose (an op holding
    a path lock may call helpers that take it again). Multi-path holds (rename)
    acquire in sorted order, so two concurrent operations touching the same pair
    of paths cannot deadlock.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._locks: Dict[str, Tuple[threading.RLock, int]] = {}

    def __len__(self) -> int:
        """Number of paths currently locked or awaited."""
        with self._mutex:
            return len(self._locks)

    def _acquire(self, path: str) -> None:
        with self._mutex:
            lock, count = self._locks.get(path, (None, 0))
            if lock is None:
                lock = threading.RLock()
            self._locks[path] = (lock, count + 1)
        lock.acquire()

    def _release(self, path: str) -> None:
        with self._mutex:
            lock, count = self._locks[path]
            if count == 1:
                del self._locks[path]
            else:
                self._locks[path] = (lock, count - 1)
        lock.release()

    @contextmanager
    def hold(self, *paths: str) -> Iterator[None]:
        """Hold the lock for each of `paths` (duplicates collapse, acquisition is sorted)."""
        ordered = sorted(set(paths))
        acquired = []
        try:
            for path in ordered:
                self._acquire(path)
                acquired.append(path)
            yield
        finally:
            for path in reversed(acquired):
                self._release(path)
