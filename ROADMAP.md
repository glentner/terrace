---
status: in-progress
current_phase: 2
phases_completed: ["1"]
last_updated: 2026-07-01
plan_ref: "PLAN.md"
---

Roadmap — Simple Overlay Service (`sosfs`)
==========================================

Tactical implementation checklist derived from [PLAN.md](PLAN.md) — the canonical design;
read it first. AGENTS.md holds conventions and commands.

How to work this document: read `current_phase` from the frontmatter, find the first
unchecked item in that phase, implement it, check it off, and advance `current_phase` /
`phases_completed` only when every item in the phase is checked. One item ≈ one `WIP:`
commit on the `wip` branch. The `/continue` skill automates this loop; `/release` ships
`wip` to `main`.


## Phase 1 — Scaffolding

Establish the package, tooling, and CI so every later phase lands in a working harness.
After this phase `uv run sosfs --help` shows the full subcommand surface (all stubs) and
CI is green on Linux 3.15 and 3.15t.

- [x] `pyproject.toml` (hatchling; `requires-python = ">=3.15"`; deps: `cmdkit[toml]`,
      `mfusepy`, `obstore`, `rich`; dev group: `pytest`, `pytest-xdist`, `hypothesis`,
      `moto[server]`; docs group: `sphinx`, `furo`; `[project.scripts] sosfs = "sosfs:main"`),
      `.python-version` (3.15 via uv), `.gitignore`, `LICENSE` (Apache-2.0), minimal
      `README.md`, `CHANGELOG.md` (Keep a Changelog, `0.1.0-dev` unreleased section)
- [x] `src/sosfs/core/`: `logging.py` (Logger with TRACE, `Logger.with_name`, rich console
      formatting), `config.py` (cmdkit layered config: CLI > `SOSFS_*` env > `.sosfs.toml` >
      `~/.config/sosfs/config.toml` > `/etc/sosfs/config.toml` > defaults), `platform.py`
      (path conventions), `exceptions.py`
- [x] `src/sosfs/core/units.py`: size (`"10TB"`, `"512GiB"` — SI and binary suffixes),
      duration (`"60s"`, `"15m"`), and rate (`"4/day"`) parsing with unit tests
- [x] `src/sosfs/__init__.py`: `SOSFSApp(ApplicationGroup)` + `main()`; stub Application
      subclasses for `mount`, `umount`, `status`, `flush`, `commit`, `audit`, `config`;
      version via `importlib.metadata`; FUSE-touching imports kept lazy so the engine
      imports cleanly on hosts without libfuse
- [x] `tests/` skeleton: `conftest.py` with isolated `temp_site` fixture (scrub `SOSFS_*`
      env, tmp state dir), test helpers (`main(argv)` shell-out, `assert_output`) — strict
      `unit`/`integration` markers already wired in `pyproject.toml` (landed with units)
- [x] CI (GitHub Actions): uv-based; Linux matrix {3.15, 3.15t} running unit tests;
      macOS job running engine-only unit tests; lint/build check
- [x] **Commit (logical):** "Phase 1 — project scaffolding: package, core modules, CLI
      skeleton, test harness, CI"


## Phase 2 — State store

The SQLite layer and the invariants that make it authoritative (PLAN.md → State store).
After this phase the database can represent an overlay's full state and survive `kill -9`.

- [x] `src/sosfs/state/schema.sql`: `entry`, `residency` (incl. recorded size/mtime,
      optional checksum, per-tier atime), `writeback`, `intent`, `tier_usage`,
      `archive_index`, `overlay` (UUID, generation, `shutdown_state`, mounting host)
      — plus a `tier` registry table (stack identity, marker generations, spec-drift
      detection) split from the hot `tier_usage` counters
- [x] `src/sosfs/state/db.py`: connection management (per-thread, WAL,
      `synchronous=NORMAL`, foreign keys), schema init, exclusive-lock acquisition for
      offline commands
- [ ] `src/sosfs/state/catalog.py`: entry CRUD, path resolution, residency queries,
      LRU atime updates
- [ ] `shutdown_state` transitions (`mounted` ↔ `clean`/`detached`) and mounting-host
      recording; managed-location markers (`.sosfs/marker`: overlay UUID, tier name,
      generation) — write, verify, mismatch detection
- [ ] Invariant primitives: dirty-transition transaction helper (metadata-before-ack)
      and intent journal helpers (create/resolve/roll-back, temp-name conventions)
- [ ] Unit tests: schema round-trips, WAL survival across simulated process death
      (subprocess `kill -9`), marker verification, intent resolution
- [ ] **Commit (logical):** "Phase 2 — SQLite state store: schema, catalog,
      shutdown-state flag, markers, metadata-before-ack and intent-journal primitives"


## Phase 3 — Tier protocol + POSIX tier

The storage contract and its first implementation (PLAN.md → Tier protocol).

- [ ] `src/sosfs/tier/protocol.py`: `Tier` protocol (`stat`, `list`, `read` with
      offset/size, `open_read`, `put`, `delete`, `mkdir`/`rmdir`/`rename`, `usage`) and
      types (`TierStat`, `TierEntry`, `TierUsage`, `FileMeta`)
- [ ] `src/sosfs/tier/posix.py`: `PosixTier` + passthrough file handles for resident-copy
      hot I/O; atomic put via temp-name + fsync + rename
- [ ] Overlay-spec model: parse `[overlay]` and `[[tier]]` TOML into typed configs
      (posix only for now; `role`, `scope`, `quota` incl. `bypass_size`, `writeback`
      policies via `core.units`); stack validation (exactly one source, source first,
      state dir durability/scope checks)
- [ ] Quota accounting: `tier_usage` counters wired to put/delete; marker initialization
      on first tier attach
- [ ] Unit tests: PosixTier semantics against tmpdirs, spec validation errors, counter
      correctness under concurrent puts
- [ ] **Commit (logical):** "Phase 3 — Tier protocol, PosixTier, overlay spec parsing,
      quota accounting"


## Phase 4 — Overlay engine core

The mount-free heart of the system (PLAN.md → Overlay engine). After this phase a
multi-tier POSIX stack works end-to-end through the engine API, with no FUSE anywhere.

- [ ] `src/sosfs/engine/locks.py`: per-file lock registry
- [ ] `src/sosfs/engine/transfer.py`: `TransferPool` (thread executor) with
      intent-journaled copies (intent → temp+rename act → confirm)
- [ ] `src/sosfs/engine/overlay.py` (part 1): namespace — lazy catalog population from
      the source tier, `getattr`/`readdir`, `mkdir`/`rmdir`, `create`/`unlink`/`rename`
      journaled as namespace ops in the write-back queue
- [ ] `src/sosfs/engine/overlay.py` (part 2): data path — open with synchronous
      promote-on-open (whole-file, `bypass_size` rule), read/write through passthrough
      handles, truncate, dirty transitions via metadata-before-ack, `fsync`
      (`fsync_policy = "resident" | "commit"`), release
- [ ] Engine test suite: multi-tier POSIX stacks (2–4 tiers), promotion, residency, and
      metadata assertions
- [ ] Hypothesis stateful model tests: random op sequences vs. plain-directory oracle,
      equivalence after quiesce + flush
- [ ] **Commit (logical):** "Phase 4 — overlay engine: namespace, promotion,
      read/write paths, transfer pool, model-based tests"


## Phase 5 — Policies

Write-back, eviction, drain, and rescue (PLAN.md → Write-back / Eviction / Lifecycle).
All policy logic is driven by an injectable clock so tests are deterministic.

- [ ] `src/sosfs/engine/writeback.py`: `WritebackScheduler` FSM thread — per-boundary
      `delay` / `max_interval` / `passthrough` cascade, injectable clock
- [ ] `src/sosfs/engine/evict.py`: `EvictionManager` FSM thread — high/low watermarks
      (default 95%/85%), LRU on per-tier atime, commit-before-evict, never evict the
      most-upstream dirty holder, `bypass_size` respected
- [ ] Drain mode: collapse policy clocks, waive re-sync rate limits, loss-risk ordering
      (most-volatile dirty first, then upstream hop by hop)
- [ ] Rescue mode: dirty data on `scope = "node"` tiers one hop to the nearest shared
      durable tier, bounded by a time budget; shared ordering logic with drain
- [ ] Virtual-clock policy tests: grace expiry, max-interval under constant writes,
      passthrough, watermark hysteresis, drain/rescue ordering under constrained budgets
- [ ] **Commit (logical):** "Phase 5 — write-back scheduler, LRU eviction, drain and
      rescue with loss-risk ordering"


## Phase 6 — FUSE adapter

The thin layer that makes it a filesystem (PLAN.md → FUSE adapter, Lifecycle → Shutdown).

- [ ] `src/sosfs/fuse/adapter.py`: mfusepy `Operations` mapping FUSE callbacks to engine
      calls; `statfs` from source capacity (or configured quota for unbounded sources);
      read-only xattrs (`user.sosfs.tiers`, `user.sosfs.dirty`)
- [ ] `src/sosfs/fuse/mount.py` + `mount` command: foreground (`-f`) and daemonized
      (default) modes, pidfile in the state directory, `shutdown_state = mounted` on
      start, state-dir validation
- [ ] Signal handling: SIGINT → drain → `clean`; second SIGINT → downgrade to detach;
      SIGTERM → detach with `drain_timeout` rescue budget → `detached`
- [ ] `umount` command: resolve daemon via pidfile; drain (SIGINT) by default,
      `--detach` (SIGTERM), `--force` (plain unmount, skips even the rescue)
- [ ] FUSE integration tests (`@mark.integration`, Linux): POSIX tooling against a real
      mount, xattr residency assertions, mount/umount/detach lifecycle
- [ ] **Commit (logical):** "Phase 6 — mfusepy adapter, mount/umount commands, signal
      lifecycle (drain, detach, rescue budget)"


## Phase 7 — S3 tier

Object storage as a tier (PLAN.md → Tier protocol, Technology decisions).

- [ ] Risk gate: verify obstore free-threaded (cp315t) wheels; if absent, wire
      source-build in CI and record the outcome in PLAN.md (Risks)
- [ ] `src/sosfs/tier/s3.py`: `S3Tier` via obstore (`get`/`get_range`/`put`/`list`/`head`);
      note: S3 PUT is atomic per key, so the intent journal's act step is a single PUT
      (multipart for large objects) — no temp-key dance
- [ ] Credentials and endpoint config (env, profile, explicit endpoint for
      moto/MinIO/on-prem)
- [ ] moto-server pytest fixture + unit tests for S3Tier semantics
- [ ] MinIO integration tests (`@mark.integration`); end-to-end: S3 source with POSIX
      staging stack through the engine
- [ ] **Commit (logical):** "Phase 7 — S3 tier via obstore with moto-server unit tests
      and MinIO integration tests"


## Phase 8 — Archive tiers

TAR and ZIP as tiers, in both operating modes (PLAN.md → ArchiveTier).

- [ ] `src/sosfs/tier/archive.py`: `ArchiveTier` skeleton over an inner location
      (POSIX path first, S3 object next); member indexing (ZIP central directory, TAR
      single sequential scan) cached in `archive_index`
- [ ] Direct-update mode: ZIP append-with-supersede (last-entry-wins) + compaction
      operation; uncompressed TAR append; deletion tombstones in the catalog until
      compaction
- [ ] Staged re-sync mode: extracted staging area + full re-pack to a staging archive +
      atomic push to the inner location; `max_frequency` rate limiting
- [ ] Drain/commit integration: one batched re-pack and one re-sync/upload at
      finalization, never per-file operations
- [ ] Tests: both modes over POSIX; tar.gz over S3 (moto); index persistence across
      remounts; compaction correctness
- [ ] **Commit (logical):** "Phase 8 — archive tiers: ZIP/TAR direct-update with
      compaction, staged re-sync with rate limiting"


## Phase 9 — Control + operations

The operational surface (PLAN.md → Control channel, Audit, Deferred commit).

- [ ] `src/sosfs/control/`: Unix-domain-socket server (line-oriented JSON) wired into the
      mount daemon; client library
- [ ] `status` command: live via socket (per-tier usage vs. quota, dirty counts, transfer
      activity; rich rendering); offline via direct DB read
- [ ] `flush` command via socket (whole overlay or subtree)
- [ ] `config` command: `get`/`set`/`which`/`edit` over the layered application config
      (hypershell pattern, on the `core.config` primitives)
- [ ] `audit` command — reconcile walk (fastest tier upstream): marker check with
      tier-evaporation attribution; residency reconcile (clean copies verified, dirty
      trusted, missing rolled back with loss report, orphans quarantined); intent
      resolution; counter rebuild; report + exit codes (0/1/2); `--fast`/`--full`/
      `--dry-run`/`--forfeit` (hostname-aware for unreachable node-scope tiers)
- [ ] Auto-audit wiring in `mount` when `shutdown_state != clean|detached`
      (`--no-audit`, `--audit=full`)
- [ ] `commit` command — offline finalize: exclusive lock, direct after detach,
      auto-audit after crash, loss-risk ordering, checksum-match skip, batched archive
      finalization, `--direct` hop collapse with stale-copy invalidation
- [ ] **Commit (logical):** "Phase 9 — control socket, status/flush, audit and offline
      commit commands"


## Phase 10 — Hardening & release

Prove the invariants, measure honestly, document, ship v0.1.0.

- [ ] Fault-injection crash suite: kill points between intent/act/confirm of every
      mutation type, mid-drain, mid-rescue, mid-re-sync; audit + remount convergence and
      loss-report accuracy
- [ ] Tier-evaporation and detach/commit tests: wiped staging tier attribution;
      commit from a simulated second host (node-scope paths absent); `--direct` vs.
      cascade byte-identical sources
- [ ] Concurrency stress on free-threaded 3.15: parallel FUSE callbacks + transfers +
      eviction; race hunting with the sampling profiler
- [ ] Benchmark harness: throughput/latency vs. direct tier access, published numbers in
      docs (honest overhead accounting)
- [ ] Sphinx docs (furo): quickstart, overlay-spec reference, architecture (from
      PLAN.md), operations guide (job-script detach/commit pattern), CLI reference
- [ ] Release readiness: CHANGELOG for 0.1.0, README polish, `uv build` artifacts
      verified, version to `0.1.0`
- [ ] **Commit (logical):** "Phase 10 — hardening, benchmarks, documentation; release
      v0.1.0" (then ship via `/release minor bump with tag`)
