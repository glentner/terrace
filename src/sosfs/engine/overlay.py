# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Overlay engine: the synchronous VFS-operations API over a tier stack (the product)."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, List, Optional

# Standard libs
import io
import os
import time
import errno
import socket
import sqlite3
import threading
import dataclasses
from uuid import uuid4
from pathlib import Path, PurePosixPath

# Internal libs
from sosfs.core.exceptions import SOSFSError
from sosfs.core.logging import Logger
from sosfs.state.db import Database
from sosfs.state.catalog import Catalog, Entry, normalize, parent_of
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
            root = Path(tier_spec.path)
            if tier_spec.role == 'source':
                if not root.is_dir():
                    raise EngineError(f'Source root does not exist: {root}')
            else:
                root.mkdir(parents=True, exist_ok=True)
            record, status = self.registry.attach(
                tier_spec.name, position=position, type=tier_spec.type, role=tier_spec.role,
                scope=tier_spec.scope, root=root, uuid=self.overlay.uuid, now=now)
            if status is not MarkerStatus.OK:
                raise EngineError(f'Tier {tier_spec.name!r} marker is {status.value} — audit required')
            self.tiers[tier_spec.name] = PosixTier(tier_spec.name, root)
        if self.catalog.get('/') is None:
            stat = self.source.stat(PurePosixPath('/'))
            self.catalog.add('/', 'dir', mode=stat.mode if stat.mode is not None else 0o755,
                             uid=stat.uid or 0, gid=stat.gid or 0,
                             mtime=stat.mtime, ctime=stat.ctime or stat.mtime, atime=stat.atime or stat.mtime)
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

    def eligible_tier(self, size: int) -> Tier:
        """
        The fastest tier admitting a file of `size`: bypass_size excludes large
        files outright, quota headroom excludes full tiers; the source always
        admits (its quota is informational, PLAN.md -> FUSE adapter).
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
        return self.source

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
                    ctime=stat.ctime or stat.mtime, atime=stat.atime or stat.mtime, target=stat.target)
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
        """Metadata for `path`; size/mtime read live from the serving tier while written handles are open."""
        entry = self._ensure(path)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', normalize(path))
        if entry.kind == 'file':
            with self._handles_mutex:
                open_written = [h for h in self._handles.get(entry.id, []) if h.written]
            if open_written:
                stat = self.tiers[open_written[-1].tier].stat(_pure(entry.path))
                if stat is not None:
                    entry = dataclasses.replace(entry, size=stat.size, mtime=stat.mtime)
        return entry

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
            intent = self.journal.begin('promote', dest.name, now=now, detail={'path': path, 'op': 'create'})
            try:
                materialize_dirs(self.catalog, dest, path)
                dest.put(_pure(path), io.BytesIO(b''), meta=FileMeta(size=0, mtime=now, mode=mode))
            except BaseException:
                self.journal.rollback(intent)
                raise
            with self.db.transaction():
                entry = self.catalog.add(path, 'file', mode=mode,
                                         uid=uid if uid is not None else 0, gid=gid if gid is not None else 0,
                                         mtime=now, ctime=now, atime=now)
                upstream = self.upstream_of(dest.name)
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
            self._vacate(new)
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
        eviction intents; the source copy outlives as a tombstone plus a queued
        op — unless nothing upstream exists (purge) or a rename is about to
        overwrite the source copy anyway (`replaced`, purge).
        """
        path = entry.path
        now = self.clock()
        on_source = False
        if entry.kind == 'dir':
            on_source = self.source.stat(_pure(path)) is not None
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
                    on_source = True
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
        with self.db.transaction():
            self.journal.cancel(path)                       # pending propagation is moot now
            if on_source and not replaced:
                op = 'rmdir' if entry.kind == 'dir' else 'unlink'
                self.catalog.mark_deleted(path)
                self.journal.queue(path, op, to_tier=self.source.name, now=now)
            else:
                self.catalog.purge(path)

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
            fd = self.tiers[serving].open_direct(_pure(path), direct)
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
            self.transfers.copy(entry.path, self.tiers[fastest.tier], target,
                                action='promote', now=self.clock())
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
        """Truncate to `length` — a write: the dirty transition commits first."""
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
                serving = self._serve(entry)
                upstream = self.upstream_of(serving)
                now = self.clock()
                if upstream is not None:
                    self.journal.mark_dirty(path, serving, to_tier=upstream.name, now=now)
                fd = self.tiers[serving].open_direct(_pure(path), os.O_WRONLY)
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
        """
        entry = self.catalog.by_id(handle.entry_id)
        if entry is None or entry.deleted:
            return None                                     # unlinked while open; nothing to record
        now = self.clock()
        stat = self.tiers[handle.tier].stat(_pure(entry.path))
        if stat is None:
            return entry
        with self.locks.hold(entry.path):
            previous = self.catalog.get_residency(entry.path, handle.tier)
            with self.db.transaction():
                if handle.written:
                    self.catalog.update_meta(entry.path, size=stat.size, mtime=stat.mtime)
                self.catalog.update_residency(entry.path, handle.tier,
                                              size=stat.size, mtime=stat.mtime, atime=now)
                if previous is not None and handle.tier != self.source.name:
                    self.registry.adjust_usage(handle.tier, bytes=stat.size - previous.size)
        return entry

    # -- upstream commit ----------------------------------------------------------------------------------------

    def commit_row(self, row: Writeback) -> None:
        """
        Execute one queued write-back row — the scheduler's unit of work: a
        namespace op runs against the source; a data row commits the dirty copy
        one hop across its boundary. Rows made moot since they were scanned
        (entry unlinked, copy already committed) settle quietly.
        """
        if row.op != 'data':
            self._execute_namespace(row)
            return
        entry = self.catalog.by_id(row.entry)
        if entry is None or entry.deleted:
            self._settle_moot(row.id)
            return
        with self.locks.hold(entry.path):
            residency = self.catalog.get_residency(entry.path, row.from_tier)
            if residency is None or residency.state != 'dirty':
                self._settle_moot(row.id)                   # committed by fsync or flush since queued
                return
            self._commit_hop(entry.path, row.from_tier, row.to_tier)

    def _settle_moot(self, row_id: int) -> None:
        """Settle a queue row unless a concurrent commit already did."""
        try:
            self.journal.settle(row_id)
        except LookupError:
            pass

    def _commit_hop(self, path: str, holder: str, upstream: str) -> None:
        """
        Copy the dirty copy of `path` at `holder` one hop to `upstream` and
        record the outcome (the caller holds the file lock). Open handles on the
        holder re-arm their dirty marking, so a write after the copy re-queues;
        a write racing the copy itself shows in the holder's stat, which leaves
        the holder dirty and re-clocks the boundary instead of settling it.
        """
        copied = self.transfers.copy(path, self.tiers[holder], self.tiers[upstream],
                                     action='commit', now=self.clock())
        now = self.clock()
        entry = self.catalog.get(path)
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
                self.journal.settle(row.id)
                continue
            self.commit_upstream(entry.path)
        log.debug('Flushed write-back queue to source')

    def _execute_namespace(self, row) -> None:
        """Apply one queued namespace op to the source, intent-bracketed and tolerant of no-ops."""
        entry = self.catalog.by_id(row.entry)
        if entry is None:
            self.journal.settle(row.id)
            return
        path = entry.path
        now = self.clock()
        detail = dict(row.detail or {})
        detail['op'] = row.op
        intent = self.journal.begin('commit', self.source.name, now=now, path=path, detail=detail)
        try:
            if row.op == 'mkdir':
                materialize_dirs(self.catalog, self.source, path)
                try:
                    self.source.mkdir(_pure(path), entry.mode)
                except FileExistsError:
                    pass
            elif row.op == 'unlink':
                try:
                    self.source.delete(_pure(path))
                except FileNotFoundError:
                    pass
            elif row.op == 'rmdir':
                try:
                    self.source.rmdir(_pure(path))
                except FileNotFoundError:
                    pass
            elif row.op == 'rename':
                materialize_dirs(self.catalog, self.source, path)
                try:
                    self.source.rename(PurePosixPath(row.detail['old']), _pure(path))
                except FileNotFoundError:
                    pass                                    # nothing at the old path; data ops fill in
            else:
                raise EngineError(f'Unhandled namespace op: {row.op}')
        except BaseException:
            self.journal.rollback(intent)
            raise
        with self.db.transaction():
            self.journal.settle(row.id)
            self.journal.confirm(intent)                    # before any purge — cascades eat intent rows
            if row.op in ('unlink', 'rmdir'):
                self.catalog.drop_residency(path, self.source.name)
                self.catalog.purge(path)
            elif row.op == 'rename':
                old = self.catalog.get(row.detail['old'], include_deleted=True)
                if old is not None and old.deleted:
                    self.catalog.purge(row.detail['old'])
                residency = self.catalog.get_residency(path, self.source.name)
                if residency is None:
                    stat = self.source.stat(_pure(path))
                    if stat is not None and entry.kind == 'file':
                        self.catalog.add_residency(path, self.source.name,
                                                   atime=now, size=stat.size, mtime=stat.mtime)

    def _vacate(self, path: str) -> None:
        """
        Clear a tombstone occupying `path` before re-creation: the queued source
        removal executes now (the one place a namespace op jumps its clock), so
        the new entry can take the path.
        """
        tombstone = self.catalog.get(path, include_deleted=True)
        if tombstone is None or not tombstone.deleted:
            return
        now = self.clock()
        for row in self.journal.queued(self.source.name):
            if row.entry != tombstone.id or row.op not in ('unlink', 'rmdir'):
                continue
            intent = self.journal.begin('commit', self.source.name, now=now, path=path,
                                        detail={'op': row.op})
            try:
                if row.op == 'unlink':
                    self.source.delete(_pure(path))
                else:
                    self.source.rmdir(_pure(path))
            except FileNotFoundError:
                pass
            self.journal.confirm(intent)
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
