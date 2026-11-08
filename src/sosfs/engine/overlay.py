# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Overlay engine: the synchronous VFS-operations API over a tier stack (the product)."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, List, Optional, Tuple

# Standard libs
import io
import os
import time
import errno
import socket
import sqlite3
import threading
import contextlib
import dataclasses
from uuid import uuid4
from pathlib import Path, PurePosixPath

# Internal libs
from sosfs.core.exceptions import SOSFSError
from sosfs.core.logging import Logger
from sosfs.state.db import Database
from sosfs.state.catalog import Catalog, Entry, Residency, normalize, parent_of
from sosfs.state.journal import Journal, Writeback
from sosfs.state.lifecycle import Lifecycle, MarkerStatus
from sosfs.state.registry import TierRegistry
from sosfs.tier.protocol import Tier, TierStat, FileMeta
from sosfs.tier.posix import PosixTier
from sosfs.tier.spec import OverlaySpec
from sosfs.engine.locks import LockRegistry
from sosfs.engine.transfer import TransferPool, materialize_dirs

# Public interface
__all__ = ['OverlayEngine', 'Handle', 'EngineError', 'DB_NAME']

# Initialize logger
log = Logger.with_name(__name__)


DB_NAME = 'state.db'


class EngineError(SOSFSError):
    """The engine cannot start or continue against this stack."""


class Handle:
    """An open file: a passthrough OS fd on the serving tier plus dirty-tracking state."""

    __slots__ = ('entry_id', 'tier', 'fd', 'flags', 'written', 'marked_dirty')

    def __init__(self, entry_id: int, tier: str, fd: int, flags: int) -> None:
        self.entry_id = entry_id
        self.tier = tier
        self.fd = fd
        self.flags = flags
        self.written = False
        self.marked_dirty = False


def _pure(path: str) -> PurePosixPath:
    """Overlay-absolute PurePosixPath for tier calls."""
    return PurePosixPath(normalize(path))


class OverlayEngine:
    """
    The synchronous VFS-operations API over an ordered tier stack
    (PLAN.md -> Overlay engine). The FUSE adapter and the test suite drive the
    same calls; nothing here knows about mounts.

    Paths are overlay-absolute strings ('/x/y'); failures raise standard
    `OSError` subclasses so the adapter maps errno directly. All timestamps
    come from the injectable `clock`. The catalog populates lazily from the
    source tier; every namespace mutation is durable in the catalog before the
    call returns and reaches the source through the write-back queue.
    """

    def __init__(self, spec: OverlaySpec, *, clock: Callable[[], float] = time.time, workers: int = 4) -> None:
        """Initialize against `spec`; no disk access happens before `start`."""
        self.spec = spec
        self.clock = clock
        self.workers = workers
        self.db = Database(Path(spec.state) / DB_NAME)
        self.locks = LockRegistry()
        self.tiers: Dict[str, Tier] = {}
        self.started = False
        self._handles: Dict[int, List[Handle]] = {}
        self._handles_mutex = threading.Lock()
        # Serializes every change to the source's namespace (a replayed op, a vacate's replay, a purge, and a data
        # commit or a create whose destination is the source, placement through recording), and the stat behind
        # recording a copy served off the source, so a physical place resolved from the catalog still holds when
        # the act or the stat lands. Order: path locks, then this, then `_handles_mutex`, then the SQLite write
        # lock. While holding it, nothing takes a path lock or calls `_ensure`, `getattr` or `readdir` (replay
        # under a nested path lock is the AB/BA deadlock), and nothing acquires it inside `db.transaction()`. It
        # is re-entrant because replay nests: `_vacate` replays rows through `_execute_namespace`, which takes it.
        self._source_lock = threading.RLock()

    # -- lifecycle ----------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Initialize state, attach the stack (markers verified), and populate the root entry."""
        self.spec.state.mkdir(parents=True, exist_ok=True)
        self.db.initialize()
        self.catalog = Catalog(self.db)
        self.journal = Journal(self.db)
        self.registry = TierRegistry(self.db)
        self.lifecycle = Lifecycle(self.db)
        self.transfers = TransferPool(self.db, workers=self.workers)
        now = self.clock()
        if self.lifecycle.load() is None:
            self.lifecycle.create(self.spec.name, uuid=str(uuid4()), now=now)
        self.overlay = self.lifecycle.begin_mount(socket.gethostname(), now=now)
        for position, tier_spec in enumerate(self.spec.tiers):
            if tier_spec.type == 's3':
                from sosfs.tier.s3 import S3Tier                 # lazy: keeps the engine importable without obstore
                tier = S3Tier.from_spec(tier_spec.name, tier_spec.s3)
                record, status = self.registry.attach_tier(
                    tier, position=position, type=tier_spec.type, role=tier_spec.role,
                    scope=tier_spec.scope, uuid=self.overlay.uuid, now=now)
            else:
                root = Path(tier_spec.path)
                if tier_spec.role == 'source':
                    if not root.is_dir():
                        raise EngineError(f'Source root does not exist: {root}')
                else:
                    root.mkdir(parents=True, exist_ok=True)
                tier = PosixTier(tier_spec.name, root)
                record, status = self.registry.attach(
                    tier_spec.name, position=position, type=tier_spec.type, role=tier_spec.role,
                    scope=tier_spec.scope, root=root, uuid=self.overlay.uuid, now=now)
            if status is not MarkerStatus.OK:
                raise EngineError(f'Tier {tier_spec.name!r} marker is {status.value} — audit required')
            self.tiers[tier_spec.name] = tier
        if self.catalog.get('/') is None:
            stat = self.source.stat(PurePosixPath('/'))
            self.catalog.add('/', 'dir', mode=stat.mode if stat.mode is not None else 0o755,
                             uid=stat.uid or 0, gid=stat.gid or 0, mtime=stat.mtime,
                             ctime=stat.ctime or stat.mtime, atime=stat.atime or stat.mtime, source_path='/')
        self.started = True
        log.info(f'Engine started (overlay={self.spec.name}, tiers={len(self.tiers)})')

    def stop(self, state: str = 'clean') -> None:
        """Stop transfers and settle the lifecycle flag ('clean' or 'detached')."""
        if not self.started:
            return
        self.transfers.stop()
        self.lifecycle.end_mount(state)
        self.db.close()
        self.started = False
        log.info(f'Engine stopped ({state})')

    def __enter__(self) -> OverlayEngine:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        if self.started:
            self.stop()

    # -- stack --------------------------------------------------------------------------------------------------

    @property
    def source(self) -> Tier:
        """The authoritative tier."""
        return self.tiers[self.spec.tiers[0].name]

    def upstream_of(self, name: str) -> Optional[Tier]:
        """The next tier toward the source, or None for the source itself."""
        names = [tier.name for tier in self.spec.tiers]
        position = names.index(name)
        return None if position == 0 else self.tiers[names[position - 1]]

    @staticmethod
    def _passthrough(tier: Tier) -> bool:
        """Whether a tier can hand out OS file handles for hot I/O (POSIX); object stores cannot."""
        return hasattr(tier, 'open_direct')

    def eligible_tier(self, size: int) -> Tier:
        """
        The fastest tier admitting a file of `size`: bypass_size excludes large
        files outright, quota headroom excludes full tiers; the source always
        admits (its quota is informational, PLAN.md -> FUSE adapter).

        A file is served by a passthrough (OS-fd) copy, so a non-passthrough
        source (S3, archive) can never be the serving tier — when no stage admits
        by policy we still stage onto the fastest passthrough tier available.
        """
        for tier_spec in reversed(self.spec.tiers[1:]):
            quota = tier_spec.quota
            if quota.bypass_size is not None and size > quota.bypass_size:
                continue
            usage = self.registry.usage(tier_spec.name)
            if quota.size is not None and usage.bytes + size > quota.size:
                continue
            if quota.files is not None and usage.files + 1 > quota.files:
                continue
            return self.tiers[tier_spec.name]
        if self._passthrough(self.source):
            return self.source
        for tier_spec in reversed(self.spec.tiers[1:]):
            tier = self.tiers[tier_spec.name]
            if self._passthrough(tier):
                return tier
        raise EngineError(f'No passthrough-capable tier to stage a {size}-byte file from a '
                          f'{self.spec.source.type} source; an S3/archive source needs a POSIX staging tier')

    # -- lazy population ----------------------------------------------------------------------------------------

    def _ensure(self, path: str) -> Optional[Entry]:
        """Entry for `path`, populating the catalog from the source on first touch."""
        path = normalize(path)
        entry = self.catalog.get(path)
        if entry is not None:
            return entry
        if self.catalog.get(path, include_deleted=True) is not None:
            return None                                     # tombstone masks upstream presence
        parent = parent_of(path)
        if parent is None:
            return None
        parent_entry = self._ensure(parent)
        if parent_entry is None or parent_entry.kind != 'dir' or parent_entry.listed:
            return None
        stat = self.source.stat(_pure(path))
        if stat is None:
            return None
        return self._adopt(path, stat)

    def _adopt(self, path: str, stat: TierStat) -> Entry:
        """Record a source-tier object in the catalog (with its source residency)."""
        now = self.clock()
        try:
            with self.db.transaction():
                entry = self.catalog.add(
                    path, stat.kind, mode=stat.mode if stat.mode is not None else 0o644,
                    uid=stat.uid or 0, gid=stat.gid or 0, size=stat.size, mtime=stat.mtime,
                    ctime=stat.ctime or stat.mtime, atime=stat.atime or stat.mtime, target=stat.target,
                    source_path=path)                       # adopted: the source holds it at this path
                if stat.kind != 'dir':
                    self.catalog.add_residency(path, self.source.name,
                                               atime=now, size=stat.size, mtime=stat.mtime)
        except sqlite3.IntegrityError:
            entry = self.catalog.get(path)                  # raced another adoption; theirs won
        return entry

    def _populate(self, path: str) -> None:
        """Fill in a directory's children from the source and mark it listed."""
        path = normalize(path)
        with self.locks.hold(path):
            entry = self.catalog.get(path)
            if entry is None or entry.listed:
                return
            try:
                listing = list(self.source.list(_pure(path)))
            except (FileNotFoundError, NotADirectoryError):
                listing = []                                # directory exists only downstream so far
            for item in listing:
                child = str(_pure(path) / item.name)
                if self.catalog.get(child, include_deleted=True) is None:
                    stat = item.stat or self.source.stat(_pure(child))
                    if stat is not None:
                        self._adopt(child, stat)
            self.catalog.set_listed(path)

    def _populate_tree(self, path: str) -> None:
        """Recursively populate a subtree (a rename must know everything it moves)."""
        for child in self.readdir(path):
            if child.kind == 'dir':
                self._populate_tree(child.path)

    # -- namespace ----------------------------------------------------------------------------------------------

    def getattr(self, path: str) -> Entry:
        """
        Metadata for `path`; size/mtime read live from the serving tier while written handles are open. The
        live read takes no lock: if a replay moves the object mid-read it finds nothing, and the catalog's
        figure stands, which `_record` keeps right.
        """
        entry = self._ensure(path)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', normalize(path))
        if entry.kind == 'file':
            with self._handles_mutex:
                open_written = [h for h in self._handles.get(entry.id, []) if h.written]
            if open_written:
                stat = self._stat_held(entry, open_written[-1].tier)
                if stat is not None:
                    entry = dataclasses.replace(entry, size=stat.size, mtime=stat.mtime)
        return entry

    def residencies(self, path: str) -> List[Residency]:
        """
        Every tier holding a copy of `path`, source-first — the read behind the
        FUSE observability xattrs (`user.sosfs.tiers`, `user.sosfs.dirty`).
        """
        entry = self.getattr(path)
        return self.catalog.residencies(entry.path)

    def readdir(self, path: str) -> List[Entry]:
        """Children of the directory at `path` (the adapter adds '.' and '..')."""
        entry = self.getattr(path)
        if entry.kind != 'dir':
            raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', entry.path)
        if not entry.listed:
            self._populate(entry.path)
        return self.catalog.children(entry.path)

    def readlink(self, path: str) -> str:
        """Target of the symlink at `path`."""
        entry = self.getattr(path)
        if entry.kind != 'symlink':
            raise OSError(errno.EINVAL, 'Not a symlink', entry.path)
        return entry.target

    def mkdir(self, path: str, mode: int = 0o755, *, uid: Optional[int] = None, gid: Optional[int] = None) -> Entry:
        """Create a directory: durable in the catalog now, on the source via the queue."""
        path = normalize(path)
        with self.locks.hold(path):
            self._require_parent(path)
            if self._ensure(path) is not None:
                raise FileExistsError(errno.EEXIST, 'File exists', path)
            self._vacate(path)
            now = self.clock()
            with self.db.transaction():
                self.catalog.add(path, 'dir', mode=mode,
                                 uid=uid if uid is not None else 0, gid=gid if gid is not None else 0,
                                 mtime=now, ctime=now, atime=now)
                self.catalog.set_listed(path)               # a new directory has no upstream children
                self.journal.queue(path, 'mkdir', to_tier=self.source.name, now=now, detail={'mode': mode})
        log.trace(f'Mkdir {path}')
        return self.catalog.get(path)

    def create(self, path: str, mode: int = 0o644, *,
               uid: Optional[int] = None, gid: Optional[int] = None) -> Entry:
        """
        Create an empty file on the fastest eligible tier, dirty against its
        upstream boundary (metadata commits before this returns).
        """
        path = normalize(path)
        with self.locks.hold(path):
            self._require_parent(path)
            if self._ensure(path) is not None:
                raise FileExistsError(errno.EEXIST, 'File exists', path)
            self._vacate(path)
            now = self.clock()
            dest = self.eligible_tier(0)
            upstream = self.upstream_of(dest.name)
            # Straight onto the source, the file lands at its target (its parent may still sit at a pre-rename
            # place), placed and recorded under the source lock like any other change to the source's namespace.
            with self._source_lock if upstream is None else contextlib.nullcontext():
                where = path if upstream is not None else self._place_parents(path, now)
                intent = self.journal.begin('promote', dest.name, now=now, detail={'path': path, 'op': 'create'})
                try:
                    materialize_dirs(self.catalog, dest, where)
                    dest.put(_pure(where), io.BytesIO(b''), meta=FileMeta(size=0, mtime=now, mode=mode))
                except BaseException:
                    self.journal.rollback(intent)
                    raise
                with self.db.transaction():
                    # A file created straight onto the source (no upstream) is already there; one staged
                    # upstream of the source has no source copy yet (source_path stays NULL until it commits).
                    entry = self.catalog.add(path, 'file', mode=mode,
                                             uid=uid if uid is not None else 0, gid=gid if gid is not None else 0,
                                             mtime=now, ctime=now, atime=now,
                                             source_path=None if upstream is not None else where)
                    state = 'dirty' if upstream is not None else 'clean'
                    self.catalog.add_residency(path, dest.name, atime=now, size=0, mtime=now, state=state)
                    self.registry.adjust_usage(dest.name, files=1)
                    if upstream is not None:
                        self.journal.mark_dirty(path, dest.name, to_tier=upstream.name, now=now)
                    self.journal.confirm(intent)
        log.trace(f'Created {path} (tier={dest.name})')
        return entry

    def unlink(self, path: str) -> None:
        """Remove a file or symlink: staging copies drop now, the source copy by tombstone + queue."""
        path = normalize(path)
        with self.locks.hold(path):
            entry = self._ensure(path)
            if entry is None:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
            if entry.kind == 'dir':
                raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)
            self._remove(entry)
        log.trace(f'Unlinked {path}')

    def rmdir(self, path: str) -> None:
        """Remove an empty directory (empty means: catalog and source agree it is)."""
        path = normalize(path)
        with self.locks.hold(path):
            entry = self._ensure(path)
            if entry is None:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
            if entry.kind != 'dir':
                raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', path)
            if path == '/':
                raise OSError(errno.EBUSY, 'Cannot remove the overlay root', path)
            if not entry.listed:
                self._populate(path)
            if self.catalog.children(path):
                raise OSError(errno.ENOTEMPTY, 'Directory not empty', path)
            self._remove(self.catalog.get(path))
        log.trace(f'Rmdir {path}')

    def rename(self, old: str, new: str) -> None:
        """
        Rename, replacing a live file (or empty directory) at `new`. Durable in
        the catalog immediately; staging copies move now; the source moves when
        the queued op commits.
        """
        old, new = normalize(old), normalize(new)
        if old == '/' or new == '/':
            raise OSError(errno.EBUSY, 'Cannot rename the overlay root')
        moving_dir = self.getattr(old).kind == 'dir'        # ENOENT here even when old == new
        if new == old:
            return
        if _pure(new).is_relative_to(_pure(old)):
            raise OSError(errno.EINVAL, 'Cannot move a directory into itself', new)
        if moving_dir:
            self._populate_tree(old)                        # everything it moves must be cataloged
        with self.locks.hold(old, new):
            entry = self._ensure(old)
            if entry is None:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', old)
            self._require_parent(new)
            target = self._ensure(new)
            if target is not None:
                if target.kind == 'dir':
                    if entry.kind != 'dir':
                        raise IsADirectoryError(errno.EISDIR, 'Is a directory', new)
                    if not target.listed:
                        self._populate(new)
                    if self.catalog.children(new):
                        raise OSError(errno.ENOTEMPTY, 'Directory not empty', new)
                elif entry.kind == 'dir':
                    raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', new)
                self._remove(target, replaced=True)
            self._vacate(new, mover=entry.id)
            now = self.clock()
            with self.db.transaction():
                self.catalog.rename(old, new)
                self.journal.queue(new, 'rename', to_tier=self.source.name, now=now,
                                   detail={'old': old, 'new': new})
                # The source still holds the old path until the op commits; a tombstone
                # masks it from re-adoption (purged when the rename executes upstream).
                self.catalog.add(old, entry.kind, mode=entry.mode, uid=entry.uid, gid=entry.gid,
                                 mtime=now, ctime=now, atime=now)
                self.catalog.mark_deleted(old)
            for name, tier in self.tiers.items():
                if tier is self.source or tier.stat(_pure(old)) is None:
                    continue
                intent = self.journal.begin('commit', name, now=now,
                                            detail={'op': 'rename', 'old': old, 'new': new})
                try:
                    materialize_dirs(self.catalog, tier, new)
                    tier.rename(_pure(old), _pure(new))
                except FileNotFoundError:
                    pass                                    # copy vanished mid-flight; audit's business
                finally:
                    self.journal.confirm(intent)
        log.trace(f'Renamed {old} -> {new}')

    # -- shared removal machinery -------------------------------------------------------------------------------

    def _require_parent(self, path: str) -> Entry:
        """The live parent directory of `path`, or the natural OSError."""
        parent = parent_of(path)
        if parent is None:
            raise FileExistsError(errno.EEXIST, 'File exists', path)
        entry = self._ensure(parent)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', parent)
        if entry.kind != 'dir':
            raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', parent)
        return entry

    def _remove(self, entry: Entry, replaced: bool = False) -> None:
        """
        Remove `entry`: staging copies (and their bytes) drop immediately under
        eviction intents; the source copy outlives as a tombstone plus a queued op
        that targets its committed source path — unless the source holds no copy
        (purge) or a rename is about to overwrite it (`replaced`). In the replaced
        case the source object is removed now, at its committed source path (which
        may differ from the path the rename reuses during a deferred rename), so it
        is never orphaned, and the entry is purged.
        """
        path = entry.path
        now = self.clock()
        if entry.kind == 'dir':
            for name, tier in self.tiers.items():
                if tier is self.source or tier.stat(_pure(path)) is None:
                    continue
                intent = self.journal.begin('evict', name, now=now, path=path)
                try:
                    tier.rmdir(_pure(path))
                except FileNotFoundError:
                    pass
                self.journal.confirm(intent)
        else:
            for residency in self.catalog.residencies(path):
                if residency.tier == self.source.name:
                    continue
                tier = self.tiers[residency.tier]
                intent = self.journal.begin('evict', residency.tier, now=now, path=path)
                try:
                    tier.delete(_pure(path))
                except FileNotFoundError:
                    pass
                with self.db.transaction():
                    self.catalog.drop_residency(path, residency.tier)
                    self.registry.adjust_usage(residency.tier, bytes=-residency.size, files=-1)
                    self.journal.confirm(intent)
        on_source = entry.source_path is not None
        if on_source and replaced:
            self._purge_source(entry, now)                  # overwritten by a rename; may sit off the reused path
        with self.db.transaction():
            self.journal.cancel(path)                       # pending propagation is moot now
            if on_source and not replaced:
                op = 'rmdir' if entry.kind == 'dir' else 'unlink'
                self.catalog.mark_deleted(path)
                self.journal.queue(path, op, to_tier=self.source.name, now=now)
            else:
                self.catalog.purge(path)

    def _purge_source(self, entry: Entry, now: float) -> None:
        """
        Remove `entry`'s source object now (intent-bracketed) at its committed source
        path — used when a rename overwrites the entry, since that physical path may
        differ from the one the rename reuses. A directory that is empty in the catalog
        may still hold children on the source whose removal or move-out is queued; those
        rows run first, in queue order. Tolerant of a copy already gone.
        """
        with self._source_lock:
            if entry.kind == 'dir':
                rows = self._holder_rows(entry.source_path, below=True, exclude=entry.id)
                if rows:
                    self._replay_through(rows)
                    entry = self.catalog.by_id(entry.id)    # a replayed prefix may have moved it
            intent = self.journal.begin('commit', self.source.name, now=now, path=entry.path,
                                        detail={'op': 'rmdir' if entry.kind == 'dir' else 'unlink',
                                                'source_path': entry.source_path})
            try:
                if entry.kind == 'dir':
                    self.source.rmdir(_pure(entry.source_path))
                else:
                    self.source.delete(_pure(entry.source_path))
            except FileNotFoundError:
                pass
            except BaseException:
                self.journal.rollback(intent)
                raise
            self.journal.confirm(intent)

    # -- metadata -----------------------------------------------------------------------------------------------

    def chmod(self, path: str, mode: int) -> Entry:
        """Change permission bits; durable in the catalog now, on the source via the queue."""
        return self._set_meta(path, fields=('mode',), mode=mode & 0o7777)

    def chown(self, path: str, uid: Optional[int] = None, gid: Optional[int] = None) -> Entry:
        """
        Change ownership. `-1` or None leaves a field alone (POSIX chown semantics);
        application to managed copies is best-effort (an unprivileged mount cannot
        chown), but the catalog records the intent regardless — it is the authority.
        """
        changed = {}
        if uid is not None and uid != -1:
            changed['uid'] = uid
        if gid is not None and gid != -1:
            changed['gid'] = gid
        return self._set_meta(path, fields=tuple(changed), **changed)

    def utimens(self, path: str, atime: float, mtime: float) -> Entry:
        """Set access and modification times; the tier copies carry mtime, the catalog keeps both."""
        return self._set_meta(path, fields=('mtime',), atime=atime, mtime=mtime)

    def _set_meta(self, path: str, *, fields: Tuple[str, ...], mode: Optional[int] = None,
                  uid: Optional[int] = None, gid: Optional[int] = None,
                  atime: Optional[float] = None, mtime: Optional[float] = None) -> Entry:
        """
        Shared setattr core (PLAN.md -> Write-back): record the attribute change in
        the catalog (metadata before acknowledgment) and enqueue a `meta` write-back
        op that reaches the source on the namespace clock, then apply the change to
        the fastest resident copy so a later content commit carries it. A `meta` op
        changes no bytes, so it never touches the residency dirty state.
        """
        path = normalize(path)
        with self.locks.hold(path):
            entry = self._ensure(path)
            if entry is None:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
            now = self.clock()
            residencies = self.catalog.residencies(path) if entry.kind == 'file' else []
            holders = [residency.tier for residency in residencies if residency.tier != self.source.name]
            updates: Dict[str, object] = {'ctime': now}
            for name, value in (('mode', mode), ('uid', uid), ('gid', gid), ('atime', atime), ('mtime', mtime)):
                if value is not None:
                    updates[name] = value
            with self.db.transaction():
                self.catalog.update_meta(path, **updates)
                self.journal.queue(path, 'meta', to_tier=self.source.name, now=now, detail={'fields': list(fields)})
                if mtime is not None:
                    for tier in holders:
                        self.catalog.update_residency(path, tier, mtime=mtime)
            # Apply to every staged copy — the copy that later commits content need not be the
            # fastest one — so its on-disk mtime is truthful; mode/ownership ride the catalog on commit.
            meta = FileMeta(size=entry.size, mtime=mtime, mode=mode, uid=uid, gid=gid)
            for tier in holders:
                self.tiers[tier].set_meta(_pure(path), meta)
        log.trace(f'Set meta {path} (fields={",".join(fields) or "-"})')
        return self.catalog.get(path)

    # -- data path ----------------------------------------------------------------------------------------------

    def open(self, path: str, flags: int = os.O_RDONLY) -> Handle:
        """
        Open a file, promoting it synchronously (whole-file) to the fastest tier
        whose bypass_size and quota admit it, then hand back a passthrough handle
        on the serving tier. O_TRUNC is honored through `truncate` so the dirty
        transition commits before bytes vanish; O_APPEND positioning belongs to
        the caller (FUSE sends explicit offsets).
        """
        path = normalize(path)
        with self.locks.hold(path):
            entry = self._ensure(path)
            if entry is None:
                raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
            if entry.kind == 'dir':
                raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)
            if entry.kind == 'symlink':
                raise OSError(errno.ELOOP, 'Symlink not followed here', path)
            serving = self._serve(entry)
            direct = flags & ~(os.O_CREAT | os.O_EXCL | os.O_TRUNC | os.O_APPEND)
            # Served directly off the source (no faster tier admitted), the bytes may sit at the
            # committed source path rather than the logical one during a deferred rename.
            serve_path = entry.source_path if serving == self.source.name and entry.source_path else path
            fd = self.tiers[serving].open_direct(_pure(serve_path), direct)
            handle = Handle(entry.id, serving, fd, flags)
            with self._handles_mutex:
                self._handles.setdefault(entry.id, []).append(handle)
            self.catalog.touch(path, serving, atime=self.clock())
        if flags & os.O_TRUNC:
            self.truncate(path, 0, handle=handle)
        log.trace(f'Opened {path} (tier={serving})')
        return handle

    def _serve(self, entry: Entry) -> str:
        """The tier to serve `entry` from, promoting first when a faster one admits it."""
        fastest = self.catalog.fastest_copy(entry.path)
        if fastest is None:
            raise EngineError(f'No resident copy of {entry.path}')
        names = [tier.name for tier in self.spec.tiers]
        target = self.eligible_tier(entry.size)
        if names.index(target.name) > names.index(fastest.tier):
            # Promoting off the source reads where it physically holds the bytes, which lags the
            # logical path while a rename is queued but uncommitted.
            src_path = entry.source_path if fastest.tier == self.source.name else None
            self.transfers.copy(entry.path, self.tiers[fastest.tier], target,
                                action='promote', now=self.clock(), src_path=src_path)
            return target.name
        return fastest.tier

    def read(self, handle: Handle, size: int, offset: int) -> bytes:
        """Read through the passthrough handle."""
        return os.pread(handle.fd, size, offset)

    def write(self, handle: Handle, data: bytes, offset: int) -> int:
        """
        Write through the passthrough handle. The clean->dirty transition commits
        before the bytes land (metadata before acknowledgment); writes to an
        already-dirty handle carry no metadata cost. Writes served directly by
        the source are write-through — nothing upstream to owe.
        """
        if not handle.marked_dirty:
            self._mark_handle_dirty(handle)
        count = os.pwrite(handle.fd, data, offset)
        handle.written = True
        return count

    def _mark_handle_dirty(self, handle: Handle) -> None:
        """Flip the serving copy dirty against its upstream boundary, once per handle."""
        upstream = self.upstream_of(handle.tier)
        if upstream is not None:
            entry = self.catalog.by_id(handle.entry_id)
            with self.locks.hold(entry.path):
                self.journal.mark_dirty(entry.path, handle.tier, to_tier=upstream.name, now=self.clock())
        handle.marked_dirty = True

    def truncate(self, path: str, length: int, handle: Optional[Handle] = None) -> None:
        """
        Truncate to `length` — a write: the dirty transition commits first. Without a handle, the call takes the
        path lock, then the source lock, and decides under both, once, everything the cut and its recording use:
        the entry at `path`, its kind, the copy that serves it (promoting first, as `open` does), the place that
        copy holds its bytes (`_held_path`) and the keys the recording writes. The source lock holds off every
        replay and every commit to the source until the recording commits, so the object cannot move between the
        decision and the cut. While the call waits for that lock, a replay may move the object, which the
        decision follows; an ancestor's rename or a removal, which the path lock does not cover, may take the
        entry off `path` or carry another entry onto it, and the call then raises ENOENT as if `path` never held
        it. So does a non-file that has taken the entry's id. The id check cannot tell the entry from a file that
        has taken its id after a purge (ids are reused, and only a schema change could tell them apart), and such
        a file carried onto `path` is cut. The open never follows a symbolic link at the file itself, the last
        path component (`O_NOFOLLOW`). A rename of an ancestor that has already passed its `_vacate` when the
        call takes the source lock can still move the entry between the decision and the recording: the catalog
        writes, keyed by `path`, then match no row, while the stage-counter write, keyed by tier, lands.
        """
        path = normalize(path)
        if handle is not None:
            if not handle.marked_dirty:
                self._mark_handle_dirty(handle)
            os.ftruncate(handle.fd, length)
            handle.written = True
        else:
            with self.locks.hold(path):
                entry = self._ensure(path)
                if entry is None:
                    raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
                if entry.kind != 'file':
                    raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)
                with self._source_lock:
                    current = self.catalog.get(path)
                    if current is None or current.id != entry.id or current.kind != 'file':
                        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
                    serving = self._serve(current)
                    held = self._held_path(current, serving)
                    upstream = self.upstream_of(serving)
                    now = self.clock()
                    if upstream is not None:
                        self.journal.mark_dirty(path, serving, to_tier=upstream.name, now=now)
                    fd = self.tiers[serving].open_direct(_pure(held), os.O_WRONLY | os.O_NOFOLLOW)
                    try:
                        os.ftruncate(fd, length)
                    finally:
                        os.close(fd)
                    previous = self.catalog.get_residency(path, serving)
                    with self.db.transaction():
                        self.catalog.update_meta(path, size=length, mtime=now)
                        self.catalog.update_residency(path, serving, size=length, mtime=now)
                        if previous is not None and serving != self.source.name:
                            self.registry.adjust_usage(serving, bytes=length - previous.size)

    def fsync(self, handle: Handle, datasync: bool = False) -> None:
        """
        Make the handle durable in its resident tier and record its metadata;
        under `fsync_policy = "commit"` also force the upstream commit now.
        """
        if datasync and hasattr(os, 'fdatasync'):
            os.fdatasync(handle.fd)
        else:
            os.fsync(handle.fd)
        entry = self._record(handle)
        if self.spec.fsync_policy == 'commit' and handle.written and entry is not None:
            self.commit_upstream(entry.path)                # the hop re-arms this handle's dirty marking

    def release(self, handle: Handle) -> None:
        """Close the passthrough handle and record final size/mtime and LRU atime."""
        os.close(handle.fd)
        with self._handles_mutex:
            siblings = self._handles.get(handle.entry_id, [])
            if handle in siblings:
                siblings.remove(handle)
            if not siblings:
                self._handles.pop(handle.entry_id, None)
        handle.fd = -1
        self._record(handle)

    def _record(self, handle: Handle) -> Optional[Entry]:
        """
        Record the serving copy's live size/mtime into the entry and residency
        rows. Residency size is the accounted size: whoever updates it adjusts
        the tier counter by the delta (source counters track pushes, not I/O).
        A copy served off the source is read where the source holds it, under
        the source lock, so a replay cannot move it between resolving that
        place and the stat (which would leave the old size recorded for good).
        """
        entry = self.catalog.by_id(handle.entry_id)
        if entry is None or entry.deleted:
            return None                                     # unlinked while open; nothing to record
        now = self.clock()
        on_source = handle.tier == self.source.name
        with self.locks.hold(entry.path):
            with self._source_lock if on_source else contextlib.nullcontext():
                entry = self.catalog.by_id(handle.entry_id)
                if entry is None or entry.deleted:
                    return None
                stat = self._stat_held(entry, handle.tier)
                if stat is None:
                    return entry
                previous = self.catalog.get_residency(entry.path, handle.tier)
                with self.db.transaction():
                    if handle.written:
                        self.catalog.update_meta(entry.path, size=stat.size, mtime=stat.mtime)
                    self.catalog.update_residency(entry.path, handle.tier,
                                                  size=stat.size, mtime=stat.mtime, atime=now)
                    if previous is not None and not on_source:
                        self.registry.adjust_usage(handle.tier, bytes=stat.size - previous.size)
        return entry

    def _held_path(self, entry: Entry, tier: str) -> str:
        """
        Where `tier` holds `entry`'s bytes. The source holds them at the committed source path, which lags the
        logical path while the entry's rename, or an ancestor's, is queued; every other tier holds them at the
        logical path.
        """
        return entry.source_path if tier == self.source.name and entry.source_path is not None else entry.path

    def _stat_held(self, entry: Entry, tier: str) -> Optional[TierStat]:
        """The live stat of `entry`'s bytes on `tier`, read where `tier` holds them (`_held_path`)."""
        return self.tiers[tier].stat(_pure(self._held_path(entry, tier)))

    # -- upstream commit ----------------------------------------------------------------------------------------

    def commit_row(self, row: Writeback) -> None:
        """
        Execute one queued write-back row — the scheduler's unit of work: a
        namespace op runs against the source; a data row commits the dirty copy
        one hop across its boundary. Rows made moot since they were scanned
        (entry unlinked, copy already committed) settle quietly; a row that has
        since run, been cancelled or cascaded away does nothing, even when its id
        now names a newer row.
        """
        if row.op != 'data':
            self._execute_namespace(row)
            return
        entry = self.catalog.by_id(row.entry)
        if entry is None or entry.deleted:
            self._settle_moot(row)
            return
        with self.locks.hold(entry.path):
            residency = self.catalog.get_residency(entry.path, row.from_tier)
            if residency is None or residency.state != 'dirty':
                self._settle_moot(row)                      # committed by fsync or flush since queued
                return
            self._commit_hop(entry.path, row.from_tier, row.to_tier)

    def _settle_moot(self, row: Writeback) -> None:
        """Settle a queue row unless it is no longer queued (a concurrent commit settled it, or its id was reused)."""
        self.journal.settle_current(row)

    def _commit_hop(self, path: str, holder: str, upstream: str) -> None:
        """
        Copy the dirty copy of `path` at `holder` one hop to `upstream` and
        record the outcome (the caller holds the file lock). Open handles on the
        holder re-arm their dirty marking, so a write after the copy re-queues;
        a write racing the copy itself shows in the holder's stat, which leaves
        the holder dirty and re-clocks the boundary instead of settling it.

        A commit to the source writes where the source already holds the entry,
        else at its target, and records that place in the copy's confirming
        transaction: a queued rename stays the only thing that moves an object
        on the source, so a commit during the lag leaves no copy behind at the
        pre-rename place. It runs under the source lock (below the caller's path
        lock) so a replay cannot move the place between resolving and writing.
        """
        if self.tiers[upstream] is self.source:
            with self._source_lock:
                entry = self.catalog.get(path)              # read under the lock: a replay may just have moved it
                where = entry.source_path or self._place_parents(path, self.clock())
                copied = self.transfers.copy(path, self.tiers[holder], self.source, action='commit',
                                             now=self.clock(), dest_path=where, source_path=where)
        else:
            entry = self.catalog.get(path)
            copied = self.transfers.copy(path, self.tiers[holder], self.tiers[upstream],
                                         action='commit', now=self.clock())
        now = self.clock()
        with self._handles_mutex:
            for open_handle in self._handles.get(entry.id, []):
                if open_handle.tier == holder:
                    open_handle.marked_dirty = False        # re-marking queues on the path lock we hold
        stat = self.tiers[holder].stat(_pure(path))
        changed = stat is None or (stat.size, stat.mtime) != (copied.size, copied.mtime)
        with self.db.transaction():
            if changed:
                for row in self.journal.queued_for(path, to_tier=upstream):
                    if row.op == 'data':
                        self.journal.rearm(row.id, now=now)
            else:
                self.catalog.update_residency(path, holder, state='clean')
                for row in self.journal.queued_for(path, to_tier=upstream):
                    if row.op == 'data':
                        self.journal.settle(row.id)
            beyond = self.upstream_of(upstream)
            if beyond is not None:
                self.journal.mark_dirty(path, upstream, to_tier=beyond.name, now=now)
        log.trace(f'Committed {path} ({holder} -> {upstream}{", re-armed" if changed else ""})')

    def commit_upstream(self, path: str) -> None:
        """
        Commit the newest copy of `path` hop by hop until the source holds it,
        ignoring policy clocks (`fsync_policy = "commit"` and flush drive this;
        the scheduler paces per boundary through `commit_row`). No-op when clean.
        """
        path = normalize(path)
        with self.locks.hold(path):
            while True:
                movable = [residency for residency in self.catalog.residencies(path)
                           if residency.state == 'dirty'
                           and self.upstream_of(residency.tier) is not None]
                if not movable:
                    break                                   # a dirty source copy settles below
                holder = movable[-1]                        # most-downstream dirty copy
                self._commit_hop(path, holder.tier, self.upstream_of(holder.tier).name)
            with self.db.transaction():
                for residency in self.catalog.residencies(path):
                    if residency.state == 'dirty':
                        self.catalog.update_residency(path, residency.tier, state='clean')
                for row in self.journal.queued_for(path):
                    if row.op == 'data':
                        self.journal.settle(row.id)
        log.trace(f'Committed {path} to source')

    def commit_to_shared(self, path: str) -> bool:
        """
        Rescue one file: commit its newest dirty copy upstream hop by hop only
        until the copy rests on a shared (non-node) tier — the minimal move that
        survives losing the node (PLAN.md -> Lifecycle, Detach/Rescue). Unlike
        `commit_upstream`, which finalizes the source, this stops at the first
        shared tier and leaves the copy dirty there for a later `sosfs commit`;
        when the only shared tier upstream is the source, it finalizes there.

        Each hop keeps the residency invariant truthful (a copy goes clean only
        once its immediate upstream holds its bytes), so the data traverses any
        intervening node tiers rather than skipping them. Returns True when a hop
        moved data onto shared storage, False when the newest dirty copy is
        already shared (or the file is clean). The engine must be quiescent, as
        for a drain.
        """
        path = normalize(path)
        scope = {tier.name: tier.scope for tier in self.spec.tiers}
        moved = False
        with self.locks.hold(path):
            while True:
                movable = [residency for residency in self.catalog.residencies(path)
                           if residency.state == 'dirty' and self.upstream_of(residency.tier) is not None]
                if not movable:
                    break                                   # nothing dirty below the source
                holder = movable[-1]                        # most-downstream dirty copy
                if scope[holder.tier] != 'node':
                    break                                   # newest dirty data already on shared storage
                self._commit_hop(path, holder.tier, self.upstream_of(holder.tier).name)
                moved = True
        if moved:
            log.trace(f'Rescued {path} to shared storage')
        return moved

    def flush(self) -> None:
        """
        Push everything queued to the source now, ignoring policy clocks:
        namespace ops first in queue order (they shape the tree data lands in),
        then data commits against current catalog paths.
        """
        for row in self.journal.queued(self.source.name):
            if row.op != 'data':
                self._execute_namespace(row)
        for row in self.journal.queued():
            if row.op != 'data':
                continue
            entry = self.catalog.by_id(row.entry)
            if entry is None or entry.deleted:
                self.journal.settle_current(row)            # a removal since may have queued its op under this id
                continue
            self.commit_upstream(entry.path)
        log.debug('Flushed write-back queue to source')

    def _execute_namespace(self, row: Writeback) -> None:
        """
        Apply one queued namespace op to the source, intent-bracketed and tolerant of no-ops. A row
        that is no longer queued (run by a vacate, cancelled, cascaded away) does nothing.

        The op resolves against the entry's current place, not the paths recorded when it was queued:
        the queue is a lossy history (removing an entry cancels its rows), so replaying recorded paths
        re-creates ancestors nobody owns and reads the kind and mode of paths since reused. Removals,
        renames and `meta` act on the committed source path, where the source physically holds the
        bytes; `mkdir` and a rename's destination resolve to the entry's target (`_target`).
        """
        with self._source_lock:
            self._execute_namespace_locked(row)

    def _execute_namespace_locked(self, row: Writeback) -> None:
        """The body of `_execute_namespace`, run with the source lock held."""
        if self.journal.current(row) is None:
            return                                          # already run by a vacate, cancelled, or cascaded away
        entry = self.catalog.by_id(row.entry)
        if entry is not None and row.op == 'rmdir' and entry.source_path is not None:
            # Empty in the catalog is not empty on the source: a child moved out or removed since may still sit
            # in the directory's object, its move or removal queued behind this row (removing an entry cancels
            # its queued rename and queues the removal last). Those rows run first, as in `_purge_source`.
            rows = self._holder_rows(entry.source_path, below=True, exclude=entry.id)
            if rows:
                self._replay_through(rows, skip=frozenset({row.id}))
                if self.journal.current(row) is None:
                    return
                entry = self.catalog.by_id(row.entry)
        if entry is None:
            self.journal.settle_current(row)
            return
        if row.op == 'rename' and entry.source_path is not None:
            entry = self._clear_inner_target(row, entry)
        path = entry.path
        now = self.clock()
        detail = dict(row.detail or {})
        detail['op'] = row.op
        source_path = entry.source_path
        placed: Optional[str] = None                        # mkdir: where the directory now sits
        moved: Optional[Tuple[str, str]] = None             # rename: (from, to) when the object moved
        intent = self.journal.begin('commit', self.source.name, now=now, path=path, detail=detail)
        try:
            if row.op == 'mkdir':
                if source_path is None:                     # else a placement or a commit already put it there
                    placed = self._place_parents(path, now)
                    try:
                        self.source.mkdir(_pure(placed), entry.mode)
                    except FileExistsError:
                        pass
            elif row.op in ('unlink', 'rmdir'):
                if source_path is not None:
                    try:
                        if row.op == 'rmdir':
                            self.source.rmdir(_pure(source_path))
                        else:
                            self.source.delete(_pure(source_path))
                    except FileNotFoundError:
                        pass
            elif row.op == 'rename':
                # One move to the current target collapses a chain of queued renames. A same-path rename
                # never reaches the tier: an object store's copy-then-delete would destroy the object.
                if source_path is not None and self._target(path) != source_path:
                    target = self._place_parents(path, now)
                    try:
                        self.source.rename(_pure(source_path), _pure(target))
                        moved = (source_path, target)
                    except FileNotFoundError:
                        pass                                # copy vanished mid-flight; data ops fill in
            elif row.op == 'meta':
                # Only where the source holds this entry: with no source copy yet, the logical path may
                # be another entry's object, and the data commit carries the catalog's attributes.
                if source_path is not None:
                    fields = set(detail.get('fields', ()))
                    try:
                        self.source.set_meta(_pure(source_path), FileMeta(
                            size=entry.size,
                            mtime=entry.mtime if 'mtime' in fields else None,
                            mode=entry.mode if 'mode' in fields else None,
                            uid=entry.uid if 'uid' in fields else None,
                            gid=entry.gid if 'gid' in fields else None))
                    except FileNotFoundError:
                        pass
            else:
                raise EngineError(f'Unhandled namespace op: {row.op}')
        except BaseException:
            self.journal.rollback(intent)
            raise
        with self.db.transaction():
            # Settle only the row this replay read: a removal during the act (it takes no source lock) cancels
            # it and may queue its own op under the same id, which must stay queued. The act's effect is
            # recorded either way, so that op resolves the place the object now holds.
            self.journal.settle_current(row)
            self.journal.confirm(intent)                    # before any purge — cascades eat intent rows
            if row.op in ('unlink', 'rmdir'):
                self.catalog.drop_residency(path, self.source.name)
                self.catalog.purge(path)
            elif row.op == 'mkdir':
                if placed is not None:
                    self.catalog.update_meta(path, source_path=placed)
            elif row.op == 'rename':
                if moved is not None:
                    self.catalog.advance_source_path(*moved)
                held = self.catalog.by_id(entry.id).source_path
                if entry.kind == 'file' and held is not None \
                        and self.catalog.get_residency(path, self.source.name) is None:
                    stat = self.source.stat(_pure(held))
                    if stat is not None:
                        self.catalog.add_residency(path, self.source.name,
                                                   atime=now, size=stat.size, mtime=stat.mtime)
                # The rename's own tombstone masked the old path until now. Purge only that one: a
                # tombstone still holding a source object, owning a queued row, or sitting where another
                # entry's object still is has a removal of its own to wait for.
                old = normalize(row.detail['old'])
                tomb = self.catalog.get(old, include_deleted=True)
                if tomb is not None and tomb.deleted and tomb.source_path is None \
                        and not self.journal.queued_for(old) and self.catalog.holder(old) is None:
                    self.catalog.purge(old)
            elif row.op == 'meta':
                if 'mtime' in set(detail.get('fields', ())) \
                        and self.catalog.get_residency(path, self.source.name) is not None:
                    self.catalog.update_residency(path, self.source.name, mtime=entry.mtime)

    # -- source placement ---------------------------------------------------------------------------------------

    def _target(self, path: str) -> str:
        """
        Where the source should hold logical `path`: its parent's committed source path (or, when the
        source lacks the parent, the parent's own target) joined with its name. Reads only the catalog.
        """
        path = normalize(path)
        parent = parent_of(path)
        if parent is None:
            return '/'
        entry = self.catalog.get(parent)
        base = entry.source_path if entry is not None and entry.source_path is not None else self._target(parent)
        return str(_pure(base) / _pure(path).name)

    def _place_parents(self, path: str, now: float) -> str:
        """
        Create on the source, outermost first, every ancestor of `path` the source lacks, each at its own
        target with its own catalog mode (looked up by entry, never by a path that may since have been
        reused), intent-bracketed and recorded as its committed source path. Returns `path`'s target.
        The source lock is held.
        """
        parent = parent_of(normalize(path))
        if parent is not None and parent != '/':
            entry = self.catalog.get(parent)
            if entry is not None and entry.source_path is None:
                self._place_parents(parent, now)
                target = self._target(parent)
                intent = self.journal.begin('commit', self.source.name, now=now, path=parent,
                                            detail={'op': 'mkdir', 'source_path': target})
                try:
                    try:
                        self.source.mkdir(_pure(target), entry.mode)
                    except FileExistsError:
                        pass
                except BaseException:
                    self.journal.rollback(intent)
                    raise
                with self.db.transaction():
                    self.catalog.update_meta(parent, source_path=target)
                    self.journal.confirm(intent)
        return self._target(path)

    def _holder_rows(self, physical: str, *, below: bool = False, exclude: Optional[int] = None) -> set[int]:
        """
        Ids of the queued namespace rows of every entry (tombstones included, `exclude` not) whose committed
        source path is `physical`, or with `below`, lies under it.
        """
        prefix = physical.rstrip('/') + '/'
        rows = set()
        for row in self.journal.queued(self.source.name):
            if row.op == 'data' or row.entry == exclude:
                continue
            entry = self.catalog.by_id(row.entry)
            held = entry.source_path if entry is not None else None
            if held is None:
                continue
            if (held.startswith(prefix) if below else held == physical):
                rows.add(row.id)
        return rows

    def _clear_inner_target(self, row: Writeback, entry: Entry) -> Entry:
        """
        A directory whose target lies inside its own source object cannot move there. The places between
        are held by entries that have since moved out of it logically (an ancestor-to-be that was placed
        inside it); their queued rows run first, then the target is recomputed. One still inside raises
        rather than settle the row with the source unmoved. The source lock is held.
        """
        held = _pure(entry.source_path)
        target = _pure(self._target(entry.path))
        if target == held or not target.is_relative_to(held):
            return entry
        rows = set()
        for other in self.journal.queued(self.source.name):
            if other.op == 'data' or other.entry == entry.id:
                continue
            holder = self.catalog.by_id(other.entry)
            place = holder.source_path if holder is not None else None
            if place is not None and _pure(place) != held and _pure(place).is_relative_to(held) \
                    and target.is_relative_to(_pure(place)):
                rows.add(other.id)
        self._replay_through(rows, skip=frozenset({row.id}))
        entry = self.catalog.by_id(entry.id)
        target = _pure(self._target(entry.path))
        if target != _pure(entry.source_path) and target.is_relative_to(_pure(entry.source_path)):
            raise EngineError(f'Cannot move {entry.source_path} into its own subtree ({target})')
        return entry

    def _replay_through(self, rows: set[int], skip: frozenset[int] = frozenset()) -> None:
        """
        Run queued namespace rows in queue order until every row in `rows` has run: the one place a
        namespace op jumps its policy clock. Running a prefix, never a row alone, keeps a parent's `mkdir`
        or a child's move-out ahead of the row that needs it. The source lock is held.
        """
        remaining = set(rows)
        for row in self.journal.queued(self.source.name):
            if not remaining:
                break
            if row.op == 'data' or row.id in skip:
                continue
            self._execute_namespace(row)
            remaining.discard(row.id)

    def _vacate(self, path: str, mover: Optional[int] = None) -> None:
        """
        Clear `path` before a new or moved entry takes it. On the source that means the place `path`
        resolves to: the queued rows that free it run now, in queue order (a tombstone's removal at
        `path`, the queued move or removal of any other entry still holding that place). `mover`, the
        entry being renamed onto `path`, is never one of them. The tombstone at `path` is then dropped.
        """
        with self._source_lock:
            rows = self._holder_rows(self._target(path), exclude=mover)
            tombstone = self.catalog.get(path, include_deleted=True)
            if tombstone is not None and tombstone.deleted:
                rows |= {row.id for row in self.journal.queued_for(path) if row.op != 'data'}
            if rows:
                self._replay_through(rows)
            tombstone = self.catalog.get(path, include_deleted=True)
            if tombstone is not None and tombstone.deleted:
                self.catalog.purge(path)

    # -- eviction -----------------------------------------------------------------------------------------------

    def evict_copy(self, path: str, tier: str) -> bool:
        """
        Drop a clean staged copy of `path` from `tier`, freeing its bytes
        (PLAN.md -> Eviction) — the eviction manager's unit of reclaim. Safe by
        construction: a clean copy means its upstream already holds the data, so
        losing it loses nothing. A no-op returning False when the copy is gone,
        lives on the source, is still dirty (the manager commits it upstream
        first), or has an open handle on this tier (never pull bytes from under a
        live fd); otherwise the tier file drops under an eviction intent and the
        residency and counters fall in one transaction with its confirmation.
        """
        path = normalize(path)
        if tier == self.source.name:
            return False
        with self.locks.hold(path):
            entry = self.catalog.get(path, include_deleted=True)
            if entry is None:
                return False
            residency = self.catalog.get_residency(path, tier)
            if residency is None or residency.state != 'clean':
                return False
            with self._handles_mutex:
                if any(handle.tier == tier for handle in self._handles.get(entry.id, [])):
                    return False
            now = self.clock()
            intent = self.journal.begin('evict', tier, now=now, path=path)
            try:
                self.tiers[tier].delete(_pure(path))
            except FileNotFoundError:
                pass                                        # already gone on disk; audit's business
            with self.db.transaction():
                self.catalog.drop_residency(path, tier)
                self.registry.adjust_usage(tier, bytes=-residency.size, files=-1)
                self.journal.confirm(intent)
        log.trace(f'Evicted {path} from {tier} ({residency.size} bytes)')
        return True
