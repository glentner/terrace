-- SPDX-FileCopyrightText: 2026 Geoffrey Lentner
-- SPDX-License-Identifier: Apache-2.0
--
-- Terrace state store schema (PLAN.md -> State store, Lifecycle and recovery).
--
-- Conventions:
--   * Timestamps are REAL epoch seconds, matching the injectable policy clock.
--   * Paths are overlay-absolute POSIX form ('/x/y'); the root directory is '/'.
--   * Writers obey two invariants: metadata commits before the operation it
--     describes is acknowledged, and every managed-location mutation journals
--     intent -> act -> confirm. An intent row's presence means the act is
--     unresolved; confirming deletes the row.

PRAGMA user_version = 2;


-- Single-row overlay identity and lifecycle flag.
-- shutdown_state is the filesystem-style dirty bit: 'mounted' persisting across a
-- process death is the crash signal that forces an audit on the next mount/commit.
CREATE TABLE overlay (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    uuid           TEXT    NOT NULL,
    name           TEXT    NOT NULL,
    generation     INTEGER NOT NULL DEFAULT 0,   -- incremented per mount session
    shutdown_state TEXT    NOT NULL DEFAULT 'clean'
                           CHECK (shutdown_state IN ('clean', 'detached', 'mounted')),
    host           TEXT,                         -- most recent mounting host
    created        REAL    NOT NULL,
    last_mounted   REAL
);


-- Tier registry: static identity of the configured stack, recorded so spec drift
-- between mounts is detectable. Position 0 is the source (upstream terminus).
CREATE TABLE tier (
    name       TEXT    PRIMARY KEY,
    position   INTEGER NOT NULL UNIQUE,
    type       TEXT    NOT NULL,                 -- posix | s3 | zip | tar | tar.gz
    role       TEXT    NOT NULL DEFAULT 'stage' CHECK (role IN ('source', 'stage')),
    scope      TEXT    NOT NULL DEFAULT 'shared' CHECK (scope IN ('shared', 'node')),
    generation INTEGER NOT NULL DEFAULT 0,       -- must match the on-disk .terrace marker
    meta       TEXT                              -- JSON adapter state (e.g., archive file identity)
) WITHOUT ROWID;


-- Hot usage counters, kept apart from the static tier rows to isolate WAL churn.
CREATE TABLE tier_usage (
    tier       TEXT    PRIMARY KEY REFERENCES tier (name),
    size_bytes INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;


-- Namespace and POSIX metadata; the catalog is authoritative while mounted.
-- 'listed' marks directories whose children are fully populated from the source
-- (lazy population). 'deleted' rows are tombstones that mask upstream presence
-- until the unlink commits to the source, then are purged.
CREATE TABLE entry (
    id      INTEGER PRIMARY KEY,
    path    TEXT    NOT NULL UNIQUE,
    parent  TEXT,                                -- parent path; NULL only for root
    kind    TEXT    NOT NULL CHECK (kind IN ('file', 'dir', 'symlink')),
    mode    INTEGER NOT NULL,
    uid     INTEGER NOT NULL,
    gid     INTEGER NOT NULL,
    size    INTEGER NOT NULL DEFAULT 0,
    mtime   REAL    NOT NULL,
    ctime   REAL    NOT NULL,
    atime   REAL    NOT NULL,
    target  TEXT,                                -- symlink target
    -- The path at which the source tier physically holds this entry's bytes, which lags the
    -- logical `path` while a rename is queued but not yet committed upstream. NULL means the
    -- source holds no copy at all. Deferred removals/renames resolve against this, not `path`,
    -- so a removed or superseded entry never orphans its source object (PLAN.md -> Write-back).
    source_path TEXT,
    listed  INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX entry_parent ON entry (parent);
CREATE INDEX entry_source_path ON entry (source_path) WHERE source_path IS NOT NULL;


-- Which tiers hold a copy of an entry. 'dirty' means this copy holds data newer
-- than the upstream neighbor. Recorded size/mtime (and checksum, when enabled)
-- are what the audit verifies clean copies against; dirty copies are trusted
-- as-is because their bytes are the newest version.
CREATE TABLE residency (
    entry    INTEGER NOT NULL REFERENCES entry (id) ON DELETE CASCADE,
    tier     TEXT    NOT NULL REFERENCES tier (name),
    state    TEXT    NOT NULL DEFAULT 'clean' CHECK (state IN ('clean', 'dirty')),
    atime    REAL    NOT NULL,                   -- per-tier LRU clock
    size     INTEGER NOT NULL,
    mtime    REAL    NOT NULL,
    checksum TEXT,                               -- blake2b hex, when checksum recording is enabled
    PRIMARY KEY (entry, tier)
) WITHOUT ROWID;

CREATE INDEX residency_lru ON residency (tier, state, atime);


-- Queued commits and namespace operations, one row per (entry, boundary).
-- Policy math: quiescence fires when now - last_write >= delay; max_interval
-- anchors on last_commit (or first_dirty when the boundary has never committed).
CREATE TABLE writeback (
    id          INTEGER PRIMARY KEY,
    entry       INTEGER NOT NULL REFERENCES entry (id) ON DELETE CASCADE,
    op          TEXT    NOT NULL CHECK (op IN ('data', 'create', 'unlink', 'rename', 'mkdir', 'rmdir', 'meta')),
    from_tier   TEXT    REFERENCES tier (name),  -- data source; NULL for pure namespace ops
    to_tier     TEXT    NOT NULL REFERENCES tier (name),
    detail      TEXT,                            -- JSON (e.g., rename {"old": "...", "new": "..."})
    state       TEXT    NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'active')),
    first_dirty REAL    NOT NULL,
    last_write  REAL    NOT NULL,
    last_commit REAL
);

CREATE INDEX writeback_boundary ON writeback (to_tier, state, last_write);


-- In-flight mutations of managed locations (intent -> act -> confirm). A crash at
-- any point leaves either a row to resolve or an orphan temp the audit discards.
CREATE TABLE intent (
    id        INTEGER PRIMARY KEY,
    action    TEXT    NOT NULL CHECK (action IN ('promote', 'commit', 'evict', 'compact', 'resync')),
    entry     INTEGER REFERENCES entry (id) ON DELETE CASCADE,  -- NULL for tier-level actions
    tier      TEXT    NOT NULL REFERENCES tier (name),          -- the managed location being mutated
    temp_name TEXT,                                             -- temp path/key used by the act
    detail    TEXT,                                             -- JSON extras
    created   REAL    NOT NULL
);


-- Member index for TAR/ZIP tiers, built once per archive and cached here.
CREATE TABLE archive_index (
    tier   TEXT    NOT NULL REFERENCES tier (name),
    member TEXT    NOT NULL,                     -- path within the archive
    kind   TEXT    NOT NULL CHECK (kind IN ('file', 'dir', 'symlink')),
    offset INTEGER NOT NULL,                     -- data byte offset (TAR: offset_data, for in-place reads; ZIP: local header)
    size   INTEGER NOT NULL,
    mtime  REAL    NOT NULL,
    mode   INTEGER,
    extra  TEXT,                                 -- JSON format-specific details
    PRIMARY KEY (tier, member)
) WITHOUT ROWID;
