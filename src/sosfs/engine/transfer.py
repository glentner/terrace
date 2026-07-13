# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Transfer pool: intent-journaled whole-file copies between tiers."""

# Type annotations
from __future__ import annotations
from typing import Optional

# Standard libs
from pathlib import PurePosixPath
from concurrent.futures import ThreadPoolExecutor, Future

# Internal libs
from sosfs.core.logging import Logger
from sosfs.state.db import Database
from sosfs.state.catalog import Catalog, Residency, normalize
from sosfs.state.journal import Journal
from sosfs.state.registry import TierRegistry
from sosfs.tier.protocol import Tier, TierError, FileMeta

# Public interface
__all__ = ['TransferPool', 'materialize_dirs']

# Initialize logger
log = Logger.with_name(__name__)


def materialize_dirs(catalog: Catalog, tier: Tier, path: str) -> None:
    """Create the missing ancestor directories of `path` on `tier`, with catalog modes."""
    ancestors = PurePosixPath(normalize(path)).parents
    for ancestor in reversed(list(ancestors)):
        if ancestor == PurePosixPath('/') or tier.stat(ancestor) is not None:
            continue
        entry = catalog.get(str(ancestor))
        try:
            tier.mkdir(ancestor, entry.mode if entry is not None else 0o777)
        except FileExistsError:
            pass


class TransferPool:
    """
    Executor for copies between tiers, each bracketed by the intent journal
    (PLAN.md -> State store): the intent commits before any bytes move, the act
    is the destination tier's atomic `put`, and the confirmation lands in the
    same transaction as the residency row and usage counters recording it. A
    crash at any point leaves a resolvable intent row or a recognizable orphan
    temp — never an unaccounted file at a final path.

    `copy` runs on the calling thread (the synchronous promote-on-open path);
    `submit` runs it on a worker thread and returns the Future (write-back and
    rescue). Timestamps are passed in per the injectable-clock convention.
    """

    def __init__(self, db: Database, *, workers: int = 4) -> None:
        """Initialize over an initialized `Database` with `workers` copy threads."""
        self.db = db
        self.catalog = Catalog(db)
        self.journal = Journal(db)
        self.registry = TierRegistry(db)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='sosfs-transfer')

    def copy(self, path: str, source: Tier, dest: Tier, *,
             action: str, now: float, state: str = 'clean', src_path: Optional[str] = None) -> Residency:
        """
        Copy `path` whole from `source` to `dest` under an intent.

        `action` is the journal verb ('promote' downstream, 'commit' upstream);
        `state` is the resulting residency ('clean' for promotion and commit,
        'dirty' for a rescue hop). `src_path` is where `source` physically holds
        the bytes when that lags the logical `path` (a copy promoted off the source
        during a deferred rename); it defaults to `path`. The caller holds the lock.
        """
        path = normalize(path)
        read_path = normalize(src_path) if src_path is not None else path
        if self.catalog.get(path, include_deleted=True) is None:
            raise LookupError(f'No such entry: {path}')
        intent = self.journal.begin(action, dest.name, now=now, path=path)
        try:
            stat = source.stat(PurePosixPath(read_path))
            if stat is None or stat.kind != 'file':
                raise TierError(f'No file at {read_path} in tier {source.name}')
            # Content (size/mtime) comes from the copied bytes; the catalog is the authority
            # for mode/ownership, so a pending chmod/chown is never clobbered by a stale on-disk copy.
            entry = self.catalog.get(path, include_deleted=True)
            meta = FileMeta(size=stat.size, mtime=stat.mtime,
                            mode=entry.mode if entry is not None else stat.mode,
                            uid=entry.uid if entry is not None else stat.uid,
                            gid=entry.gid if entry is not None else stat.gid)
            materialize_dirs(self.catalog, dest, path)
            with source.open_read(PurePosixPath(read_path)) as stream:
                dest.put(PurePosixPath(path), stream, meta=meta)
        except BaseException:
            self.journal.rollback(intent)
            raise
        with self.db.transaction():
            previous = self.catalog.get_residency(path, dest.name)
            if previous is None:
                residency = self.catalog.add_residency(path, dest.name, atime=now,
                                                       size=meta.size, mtime=meta.mtime, state=state)
                self.registry.adjust_usage(dest.name, bytes=meta.size, files=1)
            else:
                self.catalog.update_residency(path, dest.name, state=state, atime=now,
                                              size=meta.size, mtime=meta.mtime)
                residency = self.catalog.get_residency(path, dest.name)
                self.registry.adjust_usage(dest.name, bytes=meta.size - previous.size)
            self.journal.confirm(intent)
        log.trace(f'Copied {path} ({source.name} -> {dest.name}, {meta.size} bytes, {action})')
        return residency

    def submit(self, path: str, source: Tier, dest: Tier, *,
               action: str, now: float, state: str = 'clean') -> Future:
        """Run `copy` on a worker thread; the Future carries the residency or the exception."""
        return self._executor.submit(self.copy, path, source, dest, action=action, now=now, state=state)

    def stop(self, wait: bool = True) -> None:
        """Shut down the executor; with `wait`, in-flight copies finish first."""
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> TransferPool:
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
