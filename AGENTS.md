# AGENTS.md

Guidance for coding agents (Claude Code and others) working in this repository. `CLAUDE.md` is a
symlink to this file — edit `AGENTS.md`, never a separate copy.

This is the operating manual: the architecture, the load-bearing invariants, and the process rules an
agent needs to make correct changes here without rediscovering them. When something below disagrees
with the code, **the code is ground truth — fix this file.**

---

## Project

Terrace is a clean reference implementation of managed data tiering for research computing. A FUSE
mount presents one logical namespace over an ordered stack of storage tiers — e.g. `/project`
(authoritative source) → `/scratch` → `/tmp` → `/dev/shm` — with S3 and TAR/ZIP archives available as
tiers. Files promote toward fast tiers on access, write back toward the source on policy clocks
(quiescence delay, max interval, pass-through), and evict LRU when per-tier quotas hit. Whole-file
granularity in v1; the tier protocol is range-ready.

The differentiating lifecycle: SIGINT/`umount` fully drains to the source; SIGTERM or
`umount --detach` stops immediately after *rescuing* node-local dirty data one hop to the nearest
shared tier; a standalone `terrace commit` later finalizes the source from any host — so expensive
compute allocations never wait on data movement. SQLite state is authoritative after any crash
(metadata-before-ack + intent journaling); `terrace audit` reconciles.

The design goal, stated in `PLAN.md`, is **legibility as a reference implementation**: the correct
abstractions, modern Python, and no framework semantics leaking into the core model. That is the
value this repository trades against convenience, the way another project trades against latency.

**Honest status.** Phases 1–8 of the original flat roadmap are complete; the archive tiers are
adapter-complete but not yet reachable from a spec file, and the whole operational surface —
`status`, `flush`, `commit`, `audit`, `config` — is still stubs. The detach half of the
differentiating lifecycle works; the finalize half does not exist yet. A document, a plan or a
`verify:` gate that assumes otherwise is wrong. `ROADMAP.md` is the index of what is left.

Document map:

* **`PLAN.md`** — the canonical product design: architecture, semantics, technology decisions with
  rationale, lifecycle, testing strategy, risks. When a design question comes up, the answer is there
  or belongs there. `PLAN.md` outranks the code *on intent*: if implementation surfaces a
  contradiction or a better design, STOP and reconcile `PLAN.md` — with the maintainer when
  substantive — before writing the code.
* **`AGENTS.md`** (this file) — the constitution for process, conventions and invariants, and the
  only home of the factory profile (§ *Factory*) and the invariants (§ *Invariants*). On
  questions of *fact* the code wins and this file gets fixed.
* **`ROADMAP.md`** — the ordered index of future cycles; each entry's **Seed** points at an
  `issues/{slug}.md` holding the evidence.
* **`GOAL.md`** — the original statement of intent; historical, never edited.

## Environment & working rules

- **`del`, not `rm`** — `rm` is aliased away in this environment. Use `del <path>` (reversible trash;
  directories need no flags) for untracked removals and `git rm` for tracked files.
- **Commit, version and same-commit rules** live in § *Invariants* → § *Conventions*, with the
  factory profile's branch and landing rules in § *Factory*.

## Commands

```bash
uv sync                                          # install (dev group included by default)
uv run pytest -m unit                            # 780 tests, ~60 s — must pass before every commit
uv run pytest -n auto -m unit                    # pytest-xdist; ~30 s, test_model alone takes ~25 s
uv run pytest -m unit tests/engine               # scoped
docker compose up -d --wait                      # MinIO for the S3 integration tests
uv run pytest -m integration                     # 10 tests: real mounts (Linux) + MinIO
tools/lint.sh                                    # ruff + layering + SPDX + version + CLAUDE.md symlink
tools/temp_site.sh uv run terrace --version      # CLI drive in a throwaway site
uv run python tools/bundle_loop.py               # the bundle loop on a local object store; prints the spec
uv build                                         # sdist + wheel
uv run sphinx-build docs docs/_build             # docs (docs/ does not exist yet)
uv run terrace --help                            # CLI surface
uv run terrace mount spec.toml /mnt/space -f     # foreground mount (debug: -d)
```

**Supported Python is 3.15+** (`requires-python = ">=3.15"`), and the **free-threaded build is a
first-class target** — CI runs `3.15` and `3.15t`. obstore ships no free-threaded wheel, so the
`3.15t` job installs a Rust toolchain and compiles it from source, then asserts the GIL is genuinely
disabled (an extension without free-threaded support silently re-enables it). The Linux jobs assert
that libfuse loads before the tests run, so `tests/fuse` runs there in full; the macOS job has no
libfuse and reports those tests skipped with a reason naming libfuse. `-rfEs` adds one
`SKIPPED [n] file:line: reason` line per skip location to the short summary, `n` counting the tests
folded into it; no two `tests/fuse` tests share a location today, so each gets its own line.
Each of the four jobs (`Lint & build`, `Unit (Linux, 3.15)`, `Unit (Linux, 3.15t)` and
`Unit (macOS, engine-only)`) is expected to conclude `success`, and no test failure is tolerated: a
red job is a defect to fix, never a known failure to read past.

**CI does not run the integration suite.** There is no integration job and nothing starts MinIO, so
ten tests — the entire drain / detach / force-umount / xattr / metadata-propagation surface, plus the
only real-S3 round trip — execute on no automated machine. Never infer from a green PR that they
passed. Worse, `uv run pytest -m integration` **exits 0 when every test skipped**, so a gate written
that way proves nothing; assert a pass count.

## Repository map

| Path | Responsibility |
|------|----------------|
| `src/terrace/__init__.py` | `TerraceApp` (cmdkit `ApplicationGroup`), `main()`, the subcommand registry. All CLI wiring lives here. |
| `src/terrace/core/` | `config.py` (layered TOML singleton), `logging.py` (`Logger.with_name`, TRACE), `platform.py` (XDG paths), `units.py` (size/duration/rate parsing), `fsm.py` (`StateThread`), `exceptions.py`. No domain logic. |
| `src/terrace/state/` | `schema.sql`, `db.py` (WAL, per-thread connections, the transaction helper), `sql.py` (PEP 750 t-string rendering), `catalog.py`, `journal.py` (the two write-ordering primitives), `lifecycle.py` (dirty bit + markers), `registry.py` (tier identity, drift, quota counters). |
| `src/terrace/tier/` | `protocol.py` (the `Tier` contract), `posix.py`, `s3.py` (obstore), `archive.py` (zipfile/tarfile over an inner location), `spec.py` (overlay-spec parsing and stack validation). |
| `src/terrace/engine/` | `overlay.py` (the VFS-ops API — the product), `writeback.py`, `evict.py`, `drain.py`, `rescue.py`, `transfer.py`, `locks.py`. Mount-free. |
| `src/terrace/fuse/` | `adapter.py` (mfusepy `Operations`), `mount.py` (lock, pidfile, pumps, daemonization, signal lifecycle). Thin. |
| `tests/` | 50 files, 790 tests (780 `unit`, 10 `integration`). `tests/` is a package; `tests/core`, `tests/state`, `tests/tier` are not. `tests/loop/` is the bundle loop: `test_scenario.py` drives the engine over the spec `tools/bundle_loop.py` writes as nine ordered stages, the loop's progress record (§ *Verification*); `oracle.py` says what each bundle should hold with a directory tree and `tarfile` alone; `tap.py` counts the requests the source receives. |
| `tools/` | `lint.sh` (the static gate), `temp_site.sh` (the throwaway environment, § *Factory*) and `bundle_loop.py` (the bundle loop on one machine: a moto store with no container, a bucket of generated `tar.gz` bundles, an archive-tier spec over it and POSIX stage tiers in one temporary directory, all removed on every exit path; `-n N` for N bundles, and a trailing command runs with `BUNDLE_LOOP_SPEC` set). It imports nothing of the package. |
| `spec/{slug}/` | Committed, dated per-cycle design records. Retained on merge. |
| `issues/{slug}.md`, `ROADMAP.md` | Deferred code work and its ordered index. |

Two directories `PLAN.md` describes do **not exist yet**: `src/terrace/control/` (the unix-socket
control channel) and `docs/`. Both are queued work.

**Layering rule:** `fuse/` and the CLI call `engine/`; `engine/` calls `tier/` and `state/`; nothing
below `engine/` imports above itself. `tier/` and `state/` are mutual *peers*, not a stack — a naive
rank check fails on day one. `lint.sh` asserts the real rule. The engine must remain fully testable
without a mount; never let FUSE concerns leak downward.

## Architecture & data flow

```
FUSE callback ──► fuse/adapter.py ──► engine/overlay.py ──┬──► tier/{posix,s3,archive}.py
   (mfusepy, threaded)   thin translation                 └──► state/{catalog,journal}.py  (SQLite, WAL)
                                                                      ▲
   writeback.py · evict.py  (StateThread pumps) ──► commit_row / commit_upstream / commit_to_shared
   drain.py · rescue.py     (called only from Mount.shutdown)  ──────────┘
```

The engine is the product: a synchronous VFS-operations API (`getattr`, `readdir`, `open`, `read`,
`write`, `create`, `unlink`, `rename`, `truncate`, `fsync`, `chmod`, `chown`, `utimens`, …) consumed
by the FUSE adapter and, directly, by 393 engine tests with no mount. Everything moves through three
methods — `commit_row`, `commit_upstream`, `commit_to_shared` — which funnel into `_commit_hop`, the
single point at which a copy is allowed to go clean.

## Invariants

The gate for every plan and the footgun list for every review. A plan walks only the sections its change touches. A review grades every violation of a numbered section, lettered subsections included, as CRITICAL; a violation of § Conventions is HIGH.

Two consumers:

- **`code terrace plan` (gate):** before research *and* after PLAN/TECH is drafted, walk the sections a
  change touches and confirm the design honors each. Record any bend in PLAN's
  deviation-justification table.
- **`code terrace review` (footgun list):** a violation of any invariant here is **auto-CRITICAL** (a
  § Conventions violation is HIGH, not auto-CRITICAL) and, when it touches a high-blast-radius
  path, forces a human sign-off gate.

Only invoke the sections relevant to the change. Do not manufacture findings against untouched code.

Every section below was derived from an audit of the implementation, not transcribed from the design
documents. Where the code and `PLAN.md` disagree, that is recorded in § *Known open violations* with
the seed that owns the repair — read that section before filing anything.

### Read before editing

§ *Known open violations* below names the defects the code has today, so a reviewer neither re-files
them nor mistakes them for accepted standards. **Read that table before filing a finding.** The
numbered sections are the enumerated form; summarized here:

**Metadata before acknowledgment.** A clean→dirty transition commits to SQLite before the write that
caused it is acknowledged. Reversing the order leaves the fast-tier copy newer than the source with
nothing saying so; after a `SIGKILL` the audit reads a clean residency, trusts the source, and rolls
the file back. Silent data loss with a clean exit code. The mechanism is one keyword —
`sqlite3.connect(..., autocommit=True)` — plus WAL.

**Intent → act → confirm.** Every engine-initiated mutation of a managed location is bracketed: the
intent commits before bytes move, the act is atomic by the adapter's own construction, and the
confirm composes into the same transaction as the residency row and the usage counter it records.
`confirm` and `rollback` are the *same* `DELETE`; nothing records how an intent ended, and `rollback`
undoes nothing. Confirm precedes any catalog purge, because `intent.entry` cascades.

**Each tier adapter owns its own atomicity.** POSIX writes a temp under `.terrace/partial/`, fsyncs,
and renames, applying metadata to the temp *before* the rename. S3 needs no temp key — a PUT is
atomic per key, and a temp-key dance would make it less atomic, not more. Archives re-pack whole
through an atomic inner write; append-supersede was considered and rejected, because stdlib
`zipfile`/`tarfile` offer no crash-safe in-place append and a failed append destroys the entire
dataset rather than one member.

**The committed source path is where the source physically holds the bytes.** Namespace ops are
deferred, so the catalog moves before the source does. Every physical touch of the source resolves
through `entry.source_path`, not the entry's logical path: a deferred rename leaves it untouched,
committing a rename moves the object and only then advances it — rewriting the prefix for every
descendant — and removals target it. Resolving against the logical path orphans the pre-rename object
on the source, and the next mount re-adopts it as live data.

**Eviction drops only a clean, non-source copy with no open handle**, and commits a dirty copy to the
source before dropping it. "Never drop the most-upstream holder of new data" is enforced *indirectly*
through the clean flag, so the invariant rests entirely on that flag being truthful — which is what
makes the write-back re-arm logic load-bearing rather than an optimization.

**`shutdown_state` is a dirty bit and settling it is a promise.** `clean` says the source is final;
`detached` says metadata is trusted, no audit is needed, and node-local dirty data was rescued to a
shared tier. A path that settles `detached` without having completed the rescue is silent data loss
behind a state that promises safety.

**All policy logic takes an injectable clock** and never `time.time()`. A policy test that sleeps is
testing the one-second scan tick, not the sixty-second delay.

**No module-global mutable state and no unlocked counters below `main()`** — the free-threaded build
is a supported target, not an aspiration.

### High-blast-radius paths (human gate)

A CONFIRMED finding in any of these files needs a human decision before the cycle lands; a plan touching one always runs research. The list only widens: when code moves, add the new home and never remove the old entry. Skills and templates cite this list and never copy it. Paths are relative to `src/terrace/`, except the `tests/` row.

| Region | Why |
|---|---|
| `state/db.py` — `autocommit=True`, the four pragmas, `BEGIN IMMEDIATE`, `SCHEMA_VERSION` | Each one silently converts a durability guarantee into a performance characteristic. There is no schema migration machinery: a version bump makes every existing state database unopenable. |
| `state/journal.py` — `mark_dirty`, `begin`, `confirm`, `_resolve` | Both sacred invariants live in about forty lines here. |
| `state/journal.py` — `current`, `settle_current`, `settle`, `cancel`, `queued_marks` | Queue-row identity. Row ids are reused (no `AUTOINCREMENT`), so a replay or settle keyed by id alone acts on, or deletes, a newer op that took the id: silent loss behind a drained queue. |
| `state/catalog.py` — `holder`, `held`, `advance_source_path`, `purge` | Who holds a physical place on the source, and the prefix rewrite a committed rename records. Replay's tombstone purge and every placement decision read them. `purge` returns the id the engine's purge watches record. |
| `state/schema.sql` — the cascades, the `CHECK` vocabularies, `source_path` | A cascade eats unresolved intent rows; a `CHECK` is the only thing keeping `shutdown_state` a three-valued flag. |
| `state/lifecycle.py` — `begin_mount`, `Marker.write` | The crash gate and the managed-location identity. |
| `state/registry.py` — `_attach` | The only thing standing between an evaporated tier and silent re-adoption of its residency rows. |
| `state/sql.py` — `IDENTIFIER`; `state/catalog.py` — `META_FIELDS` / `RESIDENCY_FIELDS` | The only barrier between a userspace-derived name and SQL text. |
| `engine/overlay.py` — `_commit_hop`, `commit_upstream`, `commit_to_shared`, `evict_copy` | The sole clean-transition point and the only code that destroys bytes on purpose. A wrong edit is silent data loss, not a crash. |
| `engine/overlay.py` — `write`, `_mark_handle_dirty`, `truncate` | Metadata-before-ack. Reordering two statements is undetectable in review and unrecoverable in production. |
| `engine/overlay.py` — `_execute_namespace`, `_execute_namespace_locked`, `_remove`, `_purge`, `_purge_source`, `_vacate`, `rename`, `_replay_through`, `_clear_inner_target`, `_place_parents`, `_target`, `_holder_rows`, `create`, `commit_row`, `_settle_moot`, `flush` | Deferred-op replay, where an entry's bytes are placed on the source, and which queued row a replay may settle; the confirm-before-purge ordering is not expressible in the type system. |
| `engine/overlay.py` — `open`, `_serve`, `_lookup_again`, `_ensure`, `_named`, `_adopt`, `_watch`, `eligible_tier`, `getattr`, `_record`, `_stat_held`, `_held_path` | Placement policy, `source_path` resolution (opening, promoting off the source, stating the bytes a handle serves, and cutting them without a handle) and the lookup under the source lock that decides it, with the purge watch that tells the entry from a file that took its id, `bypass_size`, quota admission. |
| `engine/transfer.py` — `copy` | The single place an intent bracket wraps a real byte move, and where tier counters are adjusted. |
| `engine/writeback.py` — `deadline`, `boundary_policies` | The loss window at every boundary is these fifteen lines. |
| `engine/writeback.py` — `due`, `poll`, `on_scan`, `on_commit` | The scheduler's snapshot and replay route: rows scanned in one state run in the next, with user ops in between, so each must be re-checked as the row it was read as. |
| `engine/evict.py` — `_drop_dirty` | The only path that deletes bytes the source may not yet hold. |
| `engine/drain.py` — `tier_volatility`, `dirty_copies` | Shared by drain and rescue; a regression misallocates every constrained time budget the system will ever have. |
| `engine/drain.py` — `drain` | A replay route: namespace rows first, then data, from one snapshot of the queue. |
| `engine/locks.py` — `LockRegistry` | Sixty-seven lines standing between the mount and a deadlock. |
| `core/fsm.py` | Every long-running component inherits its shutdown latency, exception visibility and liveness from it. |
| `fuse/mount.py` — `shutdown`, `_on_sigint`, `_on_sigterm`, `_serve_after_start`, `_daemonize_and_serve` | The only code deciding drain versus rescue versus neither, and the `shutdown_state` it settles is what tells the next mount whether to trust its metadata. |
| `tier/posix.py` — `put`, `_apply_meta`, `usage` | The only crash-atomic write on a POSIX source; `usage` is the rebuild oracle for the quota counters. |
| `tier/s3.py` — `put`, `rename`, `_rename_prefix` | The atomicity contract, and the deferred-rename replay target. |
| `tier/archive.py` — `_repack`, `index`, `_populate`, `resync`, both `atomic_write`s | Whole-dataset blast radius: one bad re-pack loses every member. |
| `tier/spec.py` — `_validate_stack`, `SUPPORTED_TYPES`, `TIER_KEYS` | The last gate before the engine instantiates tiers; a weakened overlap or state-dir check turns a config typo into deletion of source data. |
| `tests/engine/test_replay.py`, `tests/engine/test_model.py` | The only guards on deferred-op replay. Weakening the model's op mix, or reintroducing a skip guard, silently retires the coverage that justified closing the Phase 8 risk. |

The short list, by subsystem. A confirmed defect in any of these forces a human sign-off gate at
review; where its grain differs from the table above, the table governs.

| Region | Why |
|--------|-----|
| `state/db.py`, `journal.py`, `schema.sql`, `lifecycle.py`, `registry.py` | The durability substrate. One keyword, four pragmas and forty lines of journal carry both sacred invariants; `SCHEMA_VERSION` has **no migration path**, so a bump makes every existing state database unopenable. |
| `engine/overlay.py` — `_commit_hop`, `commit_*`, `evict_copy`, `write`/`_mark_handle_dirty`/`truncate`, `_execute_namespace`/`_execute_namespace_locked`/`_remove`/`_purge`/`_purge_source`/`_vacate`/`rename`/`_replay_through`/`_clear_inner_target`/`_place_parents`/`_target`/`_holder_rows`/`create`/`_settle_moot`/`flush`, `open`/`_serve`/`_lookup_again`/`_ensure`/`_named`/`_adopt`/`_watch`/`getattr`/`_record`/`_stat_held`/`_held_path` | Where a copy goes clean, where bytes are destroyed on purpose, where deferred ops resolve and settle, and where the source places, serves and reports an entry's bytes. Failures here are silent, not loud. |
| `state/journal.py` — `current`/`settle_current`/`settle`/`cancel`/`queued_marks`; `state/catalog.py` — `holder`/`held`/`advance_source_path`/`purge` | Queue-row identity (ids are reused) and the committed source path's bookkeeping. A replay that settles by id alone deletes a newer op. |
| `engine/transfer.py`, `writeback.py`, `evict.py`, `drain.py`, `locks.py`, `core/fsm.py` | The intent bracket around a real byte move; the loss window at every boundary; the ordering every constrained time budget depends on; sixty-seven lines between the mount and a deadlock. |
| `fuse/mount.py` — `shutdown`, the signal handlers, `_daemonize_and_serve` | The only code deciding drain vs rescue vs neither. The daemonized branch has **zero test coverage**. |
| `tier/posix.py`, `s3.py`, `archive.py`, `spec.py` | Adapter atomicity, and the last gate before the engine instantiates tiers. |

### 1. Write ordering — the two sacred invariants

- **Metadata before acknowledgment.** A clean→dirty transition commits to SQLite *before* the write
  that caused it is acknowledged. `Journal.mark_dirty` flips the residency row and inserts or
  refreshes the write-back queue row in one transaction; `OverlayEngine.write` calls
  `_mark_handle_dirty` and only then `os.pwrite`. Both truncate paths mark first.
- Reversing the order leaves the fast-tier copy newer than the source with no record saying so.
  After a `SIGKILL` the audit sees a clean residency, treats the source as authoritative, and rolls
  the file back to the stale upstream version. **Silent data loss with a clean exit code** — no
  error, no report line, nothing for a user to notice until the data is wrong.
- **Intent → act → confirm.** Every engine-initiated mutation of a managed location is bracketed:
  `Journal.begin` commits before any bytes move, the act is made atomic by the tier adapter (§4),
  and `confirm` composes into the *same transaction* as the metadata recording the act's effect —
  the residency row and the usage counter, and for a data commit to the source the entry's committed
  source path (§5). Copies, creates, evictions, staging renames, source
  removals and every queued namespace op all follow it.
- Splitting the confirm out of that transaction leaves either a residency row for bytes the audit
  will discard, or an intent the audit re-runs against state that already moved.
- **`confirm` and `rollback` are the same database operation** — a `DELETE` of the intent row; only
  the log verb differs. An intent row's presence means *unresolved*, its absence means *resolved*,
  and nothing records how it ended. `rollback` is not an undo: it deletes no temp, restores no
  residency, decrements no counter. Any code that assumes otherwise ships a partial write with no
  compensating action.
- **Confirm before purge.** `intent.entry` is `ON DELETE CASCADE`, so purging a catalog entry
  deletes its unresolved intent rows out from under a pending confirm. `_execute_namespace` confirms
  first, deliberately. `Journal.cancel` likewise precedes `Catalog.purge` in `_remove`, so a purge
  never destroys the record that the source still holds an object at the entry's `source_path`.

### 2. State-store durability configuration

- **`sqlite3.connect(..., autocommit=True)`** is the mechanism behind §1. Every statement outside an
  explicit transaction is durable the instant it returns. Flip it to the legacy default and each DML
  statement opens an implicit deferred transaction nobody commits — worse, `conn.in_transaction`
  then reads `True`, so the `_atomic()` helpers decide they are joining a caller's transaction and
  yield without committing. The whole state store becomes write-back with no flush point.
- **WAL, `synchronous = NORMAL`, `foreign_keys = ON`, `busy_timeout`** are applied per connection, on
  every connection. WAL is what makes committed transactions survive `SIGKILL`; `NORMAL` is the
  deliberate trade, justified because the events it concedes (kernel panic, power loss) also
  evaporate the volatile tiers, so the audit is running regardless. Dropping to `OFF` breaks the
  guarantee the entire recovery story rests on.
- **Write transactions take the write lock up front** (`BEGIN IMMEDIATE`). A deferred begin that
  upgrades mid-transaction returns `SQLITE_BUSY` immediately instead of waiting out `busy_timeout`,
  so under multithreaded FUSE callbacks writers fail at random rather than queue. There is no retry
  loop for the expiry: after the timeout the `OperationalError` propagates into the callback.
- **`_atomic()` is not re-entrant with `Database.transaction()`.** The three copies in `catalog.py`,
  `journal.py` and `registry.py` join an open transaction; `Lifecycle.begin_mount`/`end_mount` call
  `transaction()` directly, so invoking either from inside one raises. A new lifecycle method copies
  `_atomic`, never `transaction`.
- **`SCHEMA_VERSION` has no migration machinery.** A bump makes every existing state database
  unopenable — `db.py` refuses a mismatch outright. Changing it is a product decision about existing
  overlays, not a schema detail.
- Single-statement catalog calls are individually durable, so an unwrapped pair (`add` followed by
  `add_residency`) has a crash window producing an entry with no copy anywhere. Wrap them.

### 3. SQL construction

- **`terrace.state.sql.render` is the only way SQL reaches the driver, and it refuses plain strings.**
  `Database.execute` takes a PEP 750 `Template` and nothing else. Interpolations bind as `?`
  parameters; `{name:id}` splices an identifier only after a full match against
  `[A-Za-z_][A-Za-z0-9_]*`; a nested `Template` splices as a fragment carrying its own params; a
  list of templates joins with `', '`; a list of scalars expands to a placeholder list; an empty
  sequence is rejected because it would render `IN ()`.
- The correct idiom for a dynamic column set:

  ```python
  sets = [t'{name:id} = {value}' for name, value in fields.items()]
  self.db.execute(t'UPDATE entry SET {sets} WHERE path = {normalize(path)}')
  ```

- Hand-assembled SQL is forbidden because the identifier whitelist and the parameter binding are the
  same mechanism: an f-string that looks identical interpolates the *value* into the SQL text. Every
  entry path in this system comes from userspace.
- The `IDENTIFIER` pattern and the `META_FIELDS` / `RESIDENCY_FIELDS` whitelists are the only barrier
  between a caller-supplied field name and the SQL text. Widening either is a security change.
- Static DDL lives in `schema.sql`, never in Python.

### 4. Tier atomicity — each adapter owns its own

- **`put` and `rename` are all-or-nothing; a partial object is never visible at the final path.**
  POSIX: write a temp, `fsync`, `os.replace`. S3: a single PUT, which is atomic per key. Archive: a
  whole-archive re-pack through `InnerLocation.atomic_write`.
- Writing into the final path directly — an `open(final, 'wb')` "optimization" for small files, or
  moving the `fsync` after the `replace` — makes a crash leave a truncated file where the catalog
  records a complete clean residency. The audit verifies clean copies by size, so a same-size
  truncation-and-refill survives verification; a later promotion copies the corrupt bytes downstream
  and a commit writes them to the source.
- **The POSIX temp lands under `.terrace/partial/`** so the audit can recognize the orphan; the journal
  classifies by the first two path components, not by filename. A temp moved elsewhere
  (`final.with_suffix('.tmp')`, `NamedTemporaryFile`) is never swept, and because it sits inside the
  tier root it is counted by `list` and `usage` — crash debris accumulates as visible overlay files
  and the quota drifts upward.
- **Metadata is applied to the temp before the rename**, so mode and ownership are atomic with
  content. Reordering publishes new bytes under the previous file's mode for the length of the
  window.
- **Ownership is best-effort; mode and mtime are not.** `os.chown` swallows `PermissionError`;
  `chmod` and `utime` are unguarded. Widening that `except` to cover the whole metadata application
  silently drops mode changes, and the catalog keeps reporting the requested mode while the tier
  holds the old bits.
- **S3 needs no temp key.** Adding a temp-key/copy/delete dance makes the operation *less* atomic —
  three keys, two failure windows — and doubles the egress. Likewise, S3 carries no POSIX metadata
  and the adapter must not pretend otherwise: the catalog is authoritative for mode/uid/gid on a
  non-POSIX tier, and encoding them into object metadata creates a second authority that disagrees
  after any out-of-band write.
- **Direct-update archives re-pack whole; append-supersede was rejected and stays rejected.** stdlib
  `zipfile`/`tarfile` offer no crash-safe in-place append: superseding a member means rewriting the
  central directory or trailer in place, which has a window where the archive parses as neither the
  old nor the new state, and it accumulates tombstones needing a later compaction pass. Re-pack makes
  compaction implicit and keeps the temp+rename invariant. Reversing it means a crash mid-append
  destroys the entire archive, which for an archive source is the whole dataset.
- **`delete` idempotency differs per adapter** — POSIX raises, S3 is idempotent, archive returns
  early — and every engine call site catches `FileNotFoundError`. A new call path that omits the
  guard turns a replayed journaled unlink, the normal crash-recovery case, into a hard failure on
  POSIX only.
- **The `.terrace` marker tree reads as absent and refuses mutation on every tier.** A tier that lets
  `.terrace/marker` be written through the overlay lets a user forge the tier identity, after which a
  wiped tier reads OK and the evaporation path never fires.
- **`usage()` is the rebuild oracle** for the quota counters and must exclude managed internals. If
  it starts counting temps or sentinels, every audit "corrects" the counters to a wrong value and
  eviction pressure is permanently biased.

### 5. Committed source path — deferred-op replay

- Namespace ops are entry-keyed and deferred: the catalog moves the moment the call returns, the
  source follows when the queued op commits. So the physical location of an entry's source copy can
  differ from its current catalog path while a rename is queued.
- **`entry.source_path` is where the source physically holds the bytes**, and every physical touch of
  the *source* tier resolves through it, not through the entry's logical path. It is `NULL` until the
  source holds a copy at all. Adoption sets it to the entry path. `create` sets it only when the file
  lands straight on the source. A data commit to the source writes where the source already holds the
  entry, else at its target, and sets it there in the transaction that confirms the copy, so a queued
  rename stays the only thing that moves a source object; the `mkdir` row and ancestor placement set
  it for directories. **A deferred rename leaves it untouched** — the source has not moved.
  Committing a rename upstream moves the object from `source_path` to the entry's *current target*
  when the row runs — its parent's committed source path (or the parent's own target) plus its name,
  not the path the row recorded — and only then advances it, rewriting the prefix for every
  descendant, because one source rename relocates a whole subtree. A chain of queued renames
  collapses into one move, and a rename whose target already equals `source_path` never calls the
  tier (an object store's copy-then-delete onto the same key destroys the object). Removals target it.
- **Replay resolves the entry's current place, never the paths a row recorded.** The queue is a lossy
  history: removing an entry cancels its rows, so replaying recorded paths re-creates ancestors nobody
  owns and reads the kind and mode of paths since reused. A queued `mkdir` creates the directory at its
  target, placing any ancestor the source lacks at that ancestor's own target with its catalog mode,
  looked up by entry. `Journal.queue` still records the queue-time path, for the audit only.
- Reading the source resolves through it too: `open` serves a source-held copy from `source_path`,
  `getattr`'s live stat and `_record` stat it there (`_stat_held`), and `truncate` without a handle cuts
  it there, so a file whose bytes sit off its logical path (inside a renamed directory, or behind its own
  queued rename) reports the size its object holds and is cut where that object is. `open`, a promotion,
  `_stat_held` and the cut resolve the place through one rule, `_held_path`. `_record` reads the entry
  again under the source lock (§ 10), so a replay cannot move the object between the read and the stat.
  `open` of a copy served off the source, and a promotion off the source (`_serve`, reached from `open` or a
  handle-less `truncate`), take the source lock, look the entry up again at its path (`_lookup_again`: path,
  id, kind and the call's purge watch, raising `ENOENT` when any fails), and open the object at the place that
  entry records before they release the lock: the direct open itself, or the promotion's whole copy. A replay
  that lands after `_ensure` and moves the object leaves the call at the object's new place, not at the old
  one or another entry's object. A copy served off a stage, and a promotion from one stage to another, are
  opened at the logical path without the source lock: no replay moves a stage copy. The handle-less `truncate`
  takes the source lock for every cut, whatever copy serves, and decides under it and its path lock, once,
  everything the cut and its recording use: the entry at its path (path, id and its purge watch), its kind,
  the serving copy (promoting first, as `open` does), the held place and the path its catalog writes are keyed
  by. An entry that an ancestor's rename or a removal took off the path during the wait raises `ENOENT`, and
  so does another entry, or a non-file, found there; a faster copy taken meanwhile through a renamed ancestor
  is the one marked dirty and cut. The cut's open never follows a symbolic link at the file itself, the last
  component of the held place (`O_NOFOLLOW`). Ids are reused: once the entry holding the largest id is
  purged, the next entry the catalog adds takes that id. So each of the three calls (the cut, the `open` of a
  copy served off the source, and a promotion off the source) opens a purge watch (`_watch`) under its path lock,
  and its first lookup (`_ensure`) clears the watch atomically with the read that returns its entry: under the
  watch mutex for an entry already cataloged (`_named`), or inside the insert's transaction for one it adopts
  (`_adopt`). Every purge records the purged id in every open watch inside its own transaction, before it
  commits and holding that mutex (`_purge`, the one caller of `Catalog.purge`), so a record that lands after
  the read can only be the named entry's own purge. The lookup under the source lock refuses with `ENOENT` an
  entry whose id its watch holds: a file that took the id after the named entry's purge, carried onto the
  path. A purge of another entry, or a `create` that takes another purged id, never refuses. A purge that
  rolls back after its record leaves the id in the watch, and refuses a call whose named entry it was. A route
  that lands before `_serve` decides the serving copy, and leaves the new file's fastest copy on a stage, is
  served or promoted from that stage with no lookup under the source lock, as any stage copy is; so is a route
  that lands after a promotion off the source and before `open` opens the stage copy it made, which `open`
  opens at the logical path without the lock (`issues/freethreaded-concurrency-stress.md`, an ancestor's round
  trip). A user `rename` holds the source lock from its `_vacate` through its catalog move (§ 10), so an
  ancestor's rename never moves the entry between the cut's lookup under the lock and its recording. One whose
  catalog move commits first leaves the path empty (unless another rename has carried the file back), and the
  cut raises `ENOENT` having cut nothing; one that reaches the lock while the cut holds it waits, then moves
  the file with its cut recorded. The rename moves its stage copies after releasing the lock, and a cut at the
  file's new path before they move turns on where the file's fastest copy is. On a stage copy not yet moved,
  the cut that serves it finds no copy to open and raises `ENOENT` after its dirty mark, having cut nothing;
  where a faster tier admits the file, `_serve` first promotes from that copy and raises `TierError`, not an
  `OSError`, before any dirty mark (`issues/engine-error-mapping.md`). On the source, or on a stage copy the
  rename has already moved, the cut goes on and is recorded (`issues/freethreaded-concurrency-stress.md`).
- Resolving against `entry.path` instead orphans the pre-rename source object: it stays on the source
  at a path the catalog no longer knows, and the next mount re-adopts it as a live file, resurrecting
  deleted or renamed-away data. `tests/engine/test_replay.py` guards the divergence classes its module
  docstring lists; see § *Known open violations* for the paths that still get this wrong.
- **`_populate_tree` before a directory rename is load-bearing and looks like a pessimization.** The
  eager recursive listing is what guarantees every descendant carries `listed=True`, which is why
  `_populate` may safely list the *logical* path — a renamed directory is never re-listed. Making a
  directory rename O(1) by deferring the walk makes directory listings silently lose children.
- **`_replay_through` is the one place a namespace op jumps its policy clock.** `_vacate` reaches it
  before `mkdir`, `create` or `rename` takes a name, and `_purge_source` before a rename replaces a
  directory; a rename row whose target lies inside its own source object uses it too, and so does an
  `rmdir` row whose directory's source object still holds a child moved out or removed since
  (removing an entry cancels its queued rename and queues the removal last, behind the parent's
  `rmdir`). It runs the queue *prefix* in order through the rows that free a physical place — a
  tombstone's removal, and the queued move or removal of any other entry still holding the place the
  name resolves to, which can take other entries' renames, mkdirs and meta rows with it — so a
  parent's `mkdir` or a child's move-out always runs before the row that needs it. A row acts only once
  the rows it needs have run: an `rmdir` row the rows of the entries its directory's object still holds
  (`_holder_rows`, one indexed read through `Catalog.held`), a rename row whose target lies inside its
  own object the rows holding the places between (`_clear_inner_target`); after its prefix ran it reads
  its needs again, until none is queued, since a move can leave a target still inside. The rows being
  replayed form an explicit stack walked in a loop, never a recursion, so neither a long queue nor
  hundreds of directories whose replays reach one another can exhaust the interpreter's stack. A row on
  the stack never runs again inside its own replay. A row reached inside the replay of a row it needs
  (one on the stack) cannot act yet: it returns, stays queued, and waits for a later visit to reach it.
  While no row has acted and the rows it waited on are still on the stack, it is not read again. The row
  a replay starts with, and each row `_vacate`'s or `_purge_source`'s replay reaches directly, sits at
  the bottom of the stack, where its needs never include a row on it, so it never waits. `flush` and
  `drain` start a replay at every queued row they read, so a row that waited inside an earlier row's
  replay runs when the same call reaches it, and one pass leaves none of the rows they read queued; a
  scheduler poll does the same for the rows it reads, the due ones. `_vacate` and `_purge_source` run
  every row up to the last they need and stop there: a row that waited inside their replay and lies past
  that row stays queued after the user call returns, like any other, for the next `flush`, drain or poll.
  A row whose pass through its needs freed none of them waits too, unless it is at the bottom; there a
  rename raises and an `rmdir` acts, which queues the engine writes never reach, since every need is held
  strictly inside the needing row's place. A row is known by its mark (id, entry, op, first-dirty time),
  never by its id alone (ids are reused). The queue is read only for a row that can need one: an `rmdir`
  whose directory's object still holds another entry, or a rename whose target lies inside its own
  object. A pass over other rows reads the queue a fixed number of times, not once per row. Before such
  a row's needs, the rows a removal queues while a replay runs (it takes no source lock) are found by
  their marks (`Journal.queued_marks`). Anything that makes namespace ops lazier must keep that escape
  hatch, or `create` over a tombstone races the source and a new name lands on an object another entry's queued op has yet to
  take away.
- **A `meta` op never dirties content.** It writes the attribute to the catalog and queues a `meta`
  row in one transaction, then applies to every staged holder; it never calls `mark_dirty` and never
  touches residency `state`. If it did, every `touch(1)` would schedule a whole-file re-copy of a
  multi-gigabyte file across the boundary.
- **The catalog is authoritative for mode/uid/gid; the copied bytes are authoritative for
  size/mtime.** `transfer.copy` builds its `FileMeta` accordingly. Reversing it lets a `chmod` queued
  behind a data commit be clobbered by the stale on-disk mode of the copy that commits.

### 6. Write-back policy and the loss windows

- **A boundary's policy belongs to the tier the data commits *from*, keyed by the tier it commits
  *to*.** Inverting the mapping applies the wrong tier's `delay` to every hop — `/dev/shm`'s
  `passthrough` would govern the source-facing boundary, turning a deliberately lazy boundary into
  write-through and multiplying source I/O by the write rate.
- **The queue is entry-keyed, refreshed rather than duplicated, and replayed namespace-ops-first.**
  `due()` sorts namespace before data and Python's stable sort preserves the `id` tiebreak, which
  matters for two namespace ops queued at the same clock value. `drain` repeats the discipline
  explicitly. Data landing on the source before the tree that holds it does not invent that tree: a
  commit first places each ancestor the source lacks at that ancestor's own target with its catalog
  mode, looked up by entry (§5), so `materialize_dirs` finds every ancestor present. Inventing parents
  by path instead leaves the source tree correct in shape and wrong in permissions (or, at a path since
  reused, carrying another entry's mode), and the divergence is invisible to the catalog. Stage tiers
  keep `materialize_dirs`, whose paths are logical because stage copies move with the catalog.
- **After a copy, `changed` detection re-arms rather than settles.** A write racing the commit must
  never mark the holder clean: the residency goes clean only when the stat matches what was copied,
  and open handles have `marked_dirty` reset *before* the stat so a subsequent write re-queues.
  Moving the reset after the stat loses a write that lands between them — no journal row, no
  residency evidence, nothing for the audit to reconcile against.
- **All policy logic takes an injectable clock.** `poll` takes an explicit `now`; every deadline is
  arithmetic on stored timestamps. A test that sleeps is testing the one-second scan tick, not the
  sixty-second delay: it either takes a minute or asserts nothing.
- **Know which windows are unbounded.** `max_interval = None` under writes closer together than
  `delay` defers the commit forever. A boundary with no `writeback` table silently gets the 60 s
  default — not "immediate" and not "never". A tier with neither `quota.size` nor `quota.files` is
  never a reclaim target and fills the device instead. Each of these is a loss window an operator
  cannot see; a change that adds one must say so.

### 7. Eviction

- **Eviction drops only a clean, non-source copy with no open handle on that tier.** Dropping a dirty
  copy is unrecoverable data loss; dropping bytes under a live fd gives the reader short reads on a
  file it holds open.
- **Dirty copies are committed upstream before being dropped**, and to the *source*, not one hop. An
  "optimization" to a single hop leaves the copy clean against a stage tier that is itself over
  quota, and both copies can then be reclaimed in the same pass.
- PLAN's "the most-upstream holder of new data is never dropped" is enforced **indirectly**, through
  the clean check. The invariant therefore rests entirely on the residency `state` column being
  truthful, which is §6's job. If a clean flag is ever set before the bytes land upstream, eviction
  turns that lie into permanent data loss on the next quota crossing.
- **Hysteresis is strict:** reclaim triggers above `high` and continues while above `low`, and the
  constructor refuses `low >= high`. Collapsing the band makes a tier at steady-state quota evict and
  re-promote the same file indefinitely.
- Eviction trusts the incrementally-maintained usage counters completely; they are not a derived
  `SUM`. A counter drifting high evicts a healthy tier to nothing, one drifting low never reclaims.
  Reconciliation is the audit's job.

### 8. Lifecycle — mount, drain, detach, rescue

- **`shutdown_state` is a three-valued dirty bit on a single-row table**, and finding `mounted` at
  mount time is an unconditional stop. `begin_mount` bumps the generation and records the host in the
  same transaction so a crash report can name the node. A mount that proceeded instead would run
  against residency rows describing a tier state nobody verified.
- **A managed-location marker is written atomically and is never rewritten for an already-registered
  tier.** Relaxing that to "write the marker if missing" means a `/dev/shm` tier wiped by a reboot is
  silently re-adopted with all its residency rows intact: the catalog claims copies that do not
  exist, and the dirty ones among them are unrecoverable.
- **Drain and rescue share one loss-risk ordering, computed once, in `drain.py`.** Scope dominates
  position, so a shared tier downstream of a node tier still sorts as lower risk. Duplicating the
  ordering in `rescue.py` is the predictable regression: the two drift, and a partial drain then
  secures different files than a rescue with the same budget.
- **Rescue stops at the first shared tier and leaves the copy dirty there**, hopping through
  intervening node tiers rather than skipping them, so no copy goes clean before its immediate
  upstream holds the bytes.
- **The rescue budget is polled between files**, so a file is indivisible and the real bound is the
  budget plus one whole-file transfer. Transfers are serial.
- **`drain` does not stop the pumps — the caller must.** Draining a live engine races the scheduler
  on the same rows and destroys the loss-risk ordering even though the double-settle is swallowed.
- **`shutdown()` always releases the lock and drops the pidfile in a `finally`**, and `start()`
  orders lock → engine → pidfile → pumps so a failed mount never leaves a pidfile pointing at a dead
  process.
- **The signal mode is latched once and never overwritten**, and handlers stay installed across
  `shutdown()` — that is what makes the second-SIGINT downgrade reachable at all. Restoring handlers
  before the drain converts the second Ctrl-C into a `KeyboardInterrupt` inside a transfer.
- The `shutdown_state` this code settles is what tells a later `terrace commit` whether to audit.
  **Settling `detached` asserts that metadata is trusted and node-local dirty data was rescued.** A
  path that settles it without having done the rescue is silent data loss with a clean exit code.

### 9. Layering and import discipline

- `fuse/` and the CLI call `engine/`; `engine/` calls `tier/` and `state/`; **nothing below `engine/`
  imports above itself.** `lint.sh` asserts it mechanically.
- `tier/` and `state/` are **mutual peers, not a stack** — `state/registry.py` imports
  `tier/protocol`, `tier/posix.py` imports `state/journal`. That cycle is legal and a naive
  `core < state < tier < engine` rank check fails on day one.
- An engine that reaches for FUSE, or an adapter that reaches into engine internals, makes the engine
  unimportable on any host without libfuse and kills the engine-first test discipline: every
  `tests/engine/` test would need `/dev/fuse`, and the macOS CI job would go red for unrelated
  reasons.
- **libfuse and obstore load only when actually used.** The imports are function-local in
  `fuse/mount.py`, `engine/overlay.py` and `tier/archive.py`. Hoisting either to module scope makes
  the offline subcommands unrunnable on a login node and turns a missing Rust toolchain on 3.15t into
  a total import failure rather than a degraded S3 tier.
- **The adapter holds no policy** — its only mutable state is the file-handle table. Policy in the
  adapter is policy no cheap test can reach, since the adapter tests run mount-free and the
  integration suite does not run in CI at all.

### 10. Concurrency — the FSM/Thread contract and free-threaded safety

- A `StateThread` subclass declares a nested `states` enum containing `START` and `FINAL`, plus one
  `on_<state>` method per non-`FINAL` member. The wiring is checked at class-definition time, so a
  mistake is an import-time `TypeError` rather than a runtime surprise. There is **no `HALT` state** —
  halting is an `Event`.
- **Every blocking operation inside a state uses a finite timeout and re-enters its state.** That
  timeout is the shutdown-latency bound. A state that blocks without one hangs the unmount forever:
  the pump stop never returns, the drain never starts, and the overlay is left at `mounted` — the
  crash flag — forcing a full audit on the next mount.
- **An exception inside a state kills the machine silently and surfaces only on `join`.** Nothing
  monitors pump liveness today, so a dead write-back scheduler leaves the filesystem fully functional
  and accepting writes while nothing ever commits upstream, converting a bounded loss window into an
  unbounded one.
- **No module-global mutable state and no unlocked counters below `main()`.** Module-level bindings
  are `Final` constants or cached loaders. The file-handle counter is mutex-guarded because an
  unlocked increment hands two concurrent `open(2)` calls the same handle and the second `release`
  closes a live file.
- CI asserts the GIL is genuinely disabled on 3.15t by importing `obstore` in the same check — an
  extension without free-threaded support silently re-enables it.
- **Multi-path locks are taken in one sorted `hold()` call.** A nested `hold()` on a different path
  inside an already-locked region defeats the sort and reintroduces AB/BA deadlock, which under FUSE
  hangs the mount and needs a forced unmount.
- `_commit_hop` is called under a lock the caller already holds. Taking the lock inside it deadlocks
  all three callers. When its destination is the source it takes the source mutex below, never a path
  lock.
- **One re-entrant source-namespace mutex** (`OverlayEngine._source_lock`) serializes every change to
  the source's namespace: `_execute_namespace`, `_vacate`'s collect-and-replay, `_purge_source`, and a
  data commit or a `create` whose destination is the source (placement, copy and recording). A user
  `rename` holds it from its `_vacate` through its catalog move, and moves its stage copies after
  releasing it, so no ancestor's catalog move lands inside a handle-less `truncate`'s hold. `_record`
  takes it to stat a copy served off the source, `open` to look up and open a copy served off the
  source, a promotion off the source from its lookup through its whole copy, and `truncate` without a
  handle for every cut, whatever copy serves, from its decision through its recording, so the size
  recorded is read, the place opened or copied from is decided and read, and the cut decided and made,
  while no replay or commit can move the object. Without it a replay reads a
  `source_path`, another thread moves or writes the object, and the replay's act lands on a place that
  no longer holds what it read; a commit's write lands on a place a replay has just moved away from. It
  spans the whole copy, so a large commit to the source delays every `create`, `mkdir` and `rename`
  that reaches `_vacate`, the `open`, `release` or `fsync` of a copy served off the source, every
  promotion off the source, and every handle-less `truncate`, until it finishes. A promotion off the
  source, on `open` or a handle-less `truncate`, holds it in turn for that whole-file copy, and delays
  the same calls. On a passthrough source a promotion's copy is as large as the stage admits; on an S3
  or archive source it is the whole object, which a stage takes whatever its `bypass_size` or quota.
  A user `rename` holds it through its catalog transaction too: that transaction's wait for SQLite's
  write lock (up to the 5 s busy timeout) and the `UPDATE` that moves every descendant's path, so
  renaming a large directory delays the same calls, and every replay and commit to the source, for that
  long. Lock order: **path locks → source
  mutex → `_handles_mutex` → SQLite write lock → purge-watch mutex.** Nothing holding it calls `locks.hold`, `_ensure`,
  `getattr` or `readdir` (replay never takes a path lock; a nested `hold()` inside replay is the AB/BA
  deadlock above). Nothing acquires it inside `db.transaction()`. It is re-entrant because
  `rename` takes it around its `_vacate`, and the cut around `_serve`, which takes it again to promote
  off the source; a replay does not nest it, since it runs every row it reaches under the hold its caller
  took, through `_execute_namespace_locked`.
- **The purge-watch mutex** (`OverlayEngine._watches_mutex`) guards the open purge watches and the ids
  recorded in them (§ 5). It is a leaf, last in the order. Its holders: `_purge`, inside its transaction,
  after the SQLite write lock, to record a purged id; `_adopt`, inside its insert's transaction, to clear
  the calling thread's watch; `_watch`, to open and close a watch, under the call's path lock; `_named`,
  which makes one catalog read while holding it, so the read that names a call's entry and the clearing of
  its watch are one step; and the cut's check and `_lookup_again`, to test it under the path lock and the
  source lock. Nothing else is acquired while it is held: `_named` opens the thread's connection before it
  takes the mutex, so no pragma runs under it, and its read takes no SQLite write lock; a WAL reader does
  not wait on a writer's open transaction (measured: 0.000 s against one). `_purge` runs only inside a
  `db.transaction()` and never opens one; `_vacate` wraps its tombstone purge in one, under the source
  mutex, which the order allows. The source mutex gains no holder.
- **The mutex does not cover user removals.** `unlink` and `rmdir` cancel an entry's queued rows and
  queue the removal under path locks alone, so one can land inside a replay's act. A replay therefore
  settles with `Journal.settle_current`, never by id: the removal may have queued its op under the
  cancelled row's id, and that op must stay queued. Removal racing a replayed `mkdir` that has placed
  the directory but not yet recorded it is still open; see § *Known open violations*.

### 11. The engine API contract

- The engine exposes a **synchronous, mount-free VFS-operations API**. Making an op asynchronous
  invalidates the whole test strategy.
- **Failures are `OSError` subclasses**, so the adapter maps errno directly. A non-`OSError` escaping
  the API reaches libfuse as an unhandled exception; see § *Known open violations*.
- **Only cataloged entries transfer.** A copy of an unknown path would put bytes in a managed location
  with no residency row, which the audit reports as unaccounted and may delete.
- **Promotion runs on the calling thread, under the file lock.** A promote-on-open of a large file
  holds the path lock for the whole copy, and a promotion off the source holds the source mutex for it
  too (§ 10). That is intended — synchronous whole-file promote — but it means `open` latency is a copy,
  and a promotion off the source delays every call that waits on the source mutex.
- Paths are overlay-absolute strings; all time comes from the injectable clock.

### Conventions (same-commit rules)

Violations are HIGH, not auto-CRITICAL.

- Version lives only at the release recipe's `version.source`; the release recipe's `gate` mirrors CI.
- No spec R#/P# ids in source code.
- Point to a rule; don't restate it. A rule restated in N places is a same-commit obligation in all N.
- Prose and commit voice follow § *Prose and comments*; voice is hygiene, not a review finding — except
  comments in `src/` and `tests/`, which the **Code style** bullet below grades.
- **Commit only when explicitly asked.** When you do, branch off **`main`**; there is no `develop`.
  Lifecycle work goes on a `{kind}/{slug}` branch (the profile's `branches`) and lands on `main` by
  squash.
- **Squash merges.** Commit subjects are `[category] Imperative summary`. Categories in use:
  `feature`, `fix`, `refactor`, `docs`, `test`, `ci`, `build`, `release`, and `factory` (changes to
  § *Factory* and § *Invariants*; it retires the old `harness` category). The set is not closed —
  coin a lowercase category when one genuinely fits.
- **Commit messages are short.** Subject at most 72 characters. A body is optional and earns its
  place the way a comment does: it records a decision, a rejected alternative, or a consequence the
  diff does not show. It never narrates the diff or lists the files touched. Two or three lines is a
  normal body; past about fifteen, the commit should have been two commits. The *Prose and comments*
  rules below apply here too.
- **Keep the `Co-Authored-By: Claude <Model>` trailer** on commits that land on `main` (a cycle's
  landing squash commit; § Factory's `branch commits` row leaves cycle-branch commits without one).
  This repo records it. PR bodies end with the Claude Code generation line.
- **Version is single-sourced** at `version = ` in `pyproject.toml`; `terrace.__version__` reads it
  through `importlib.metadata`. No literal version string exists anywhere in `src/` or `tests/`; `tools/lint.sh`
  asserts both halves.
- **Same-commit rule.** A change to behavior updates, in the *same commit*, whichever of these it
  invalidates: **this file**, `PLAN.md` when a design decision moves, the CLI help surface **and**
  `tests/test_cli.py` when a subcommand changes, `.github/workflows/ci.yml` when the gate changes,
  and `docs/` once it exists. This file carries measured figures — the test counts, the file counts,
  the timings — and they have no other owner. A change that **overturns an invariant** asserted in
  § *Invariants* adds that section too. `code terrace review` grades against it, so a section left
  asserting the decision a change reversed turns correct code into an auto-CRITICAL
  finding. Overturning one is a design decision, not bookkeeping — it belongs in the plan's deviation
  table and in front of a human, never in the diff alone. An edit to § *Invariants* sits inside the
  graded diff, so it revises the standard and is judged on its merits — never read as license.
- **Adding a subcommand touches four places and missing one is silent**: the `Application` subclass,
  `APP_HELP`, the `commands` registry, and `tests/test_cli.py`. A command in `commands` but absent
  from `APP_HELP` fails the suite; one added to `APP_HELP` and not to `commands` passes and fails only
  at invocation.
- **Every test module declares `pytestmark`** with `unit` or `integration`. Markers are strict. A
  module without one is collected but deselected by `-m unit`, so it never runs in CI or in any gate —
  a test that is written, reviewed, merged, and permanently green because it never executes.
- **An `-m integration` gate exits 0 when nothing ran** (10 skipped, 780 deselected on a host with no
  `/dev/fuse` and no MinIO). Any integration gate must assert a pass count, not an exit code.
- `tests/` is a package; `tests/core`, `tests/state`, `tests/tier` are not. A second `test_db.py`
  under a non-package directory collides at the top-level module name and aborts collection for the
  whole suite. `Clock`, `seed` and `build_spec` in `tests/engine/conftest.py` are imported across
  packages — treat them as an interface.
- **Code style** follows § *Code style* and § *Prose and comments*: SPDX headers, 120-character
  lines, the four import groups, explicit `__all__`, full annotations with PEP 604 unions, `Logger.with_name(__name__)`, and
  comments only for constraints the code cannot express — declarative, the *why* not the what, no
  filler, no marketing adjectives, no emoji, and **no feature-scoped spec ids** (`R1`, `P3`) in `src/`,
  `tests/` or `README.md`.
- **Verify by driving the suite and, for CLI behavior, `tools/temp_site.sh`** — never against
  the developer's real state directory. Exit 0 alone is not a pass; assert the post-condition.

### Known open violations

Defects the harness port's audit found and reproduced, and four a review of the whole tree reproduced
on 2026-11-11, each recorded so a reviewer neither re-files it nor mistakes it for an accepted
standard. **These are violations, not exceptions.** Each names the seed that owns the repair; a cycle
that touches the region should either fix it or say why not.

| § | Violation | Seed |
|---|---|---|
| 1, 4 | Archive re-pack and re-sync mutate a managed location with **no intent bracket**. `'compact'` and `'resync'` exist in the journal's action vocabulary and have no producer. | `issues/archive-journaling-and-locking.md` |
| 5 | Sizes a state database written before `fe26962` may hold wrong for a copy held on the source: `entry.size` and the source residency's `size`. The residency heals on the next `release` of any handle on that copy; `entry.size` heals only on a write or a `truncate`, so `getattr` and placement read the stale figure meanwhile. Staged-tier quota counters are unaffected. | `issues/audit-command.md` |
| 5, 10 | A user `rmdir` racing a replayed `mkdir` that has placed the directory but not yet recorded it reads a `NULL` `source_path`, purges the entry and its intent row (the cascade), and the replay's confirm then raises; the source keeps a directory the catalog no longer has. Threaded; reproduced by a single-thread injection, and pre-existing (`141aa86` raises at its settle instead). | `issues/freethreaded-concurrency-stress.md` |
| 8 | A **second SIGINT settles `detached` without running the rescue**, so node-local dirty data is stranded behind a state that promises it is safe. `umount --detach` is bounded by `drain_timeout` where PLAN says it completes, and an incomplete rescue is never recorded. | `issues/shutdown-rescue-gaps.md` |
| 4, 10 | `ArchiveTier` has **no tier-wide lock** and re-packs per call; two concurrent puts to different paths lose a member with both calls returning success. A stale staging copy silently wins over a newer source. | `issues/archive-journaling-and-locking.md` |
| 2, 8 | The **audit does not exist**: `journal.pending()` has no consumer, `begin_mount` raises rather than recovering, and `terrace audit` is a stub. The recovery half of §1 is unimplemented. | `issues/audit-command.md` |
| 10 | **Nothing monitors pump liveness**; a dead scheduler is unbounded, unreported loss. | `issues/pump-liveness-and-loss-windows.md` |
| 11 | `EngineError` and `LookupError` escape the VFS API; writing through a handle to an unlinked file fails where POSIX requires success. | `issues/engine-error-mapping.md` |
| 4, Conventions | `ArchiveTier` is **unreachable from a spec file** — the tier types are rejected and the archive keys are unknown — so the archive capability PLAN.md documents cannot be configured. | `issues/archive-spec-plumbing.md` |
| Conventions | The **integration suite runs on no automated machine**; CI has no integration job. | `issues/ci-integration-lane.md` |
| — | Smaller measured gaps: `residency.checksum` is never written though the spec parses the flag; `tier.generation` never advances in production; `intent.temp_name` is dead and the archive's sibling `.tmp` is outside the orphan convention; `statfs` reports 100% full for an unquota'd non-POSIX source; no directory `fsync` anywhere; `S3Tier.rename`'s docstring claims an atomicity S3 does not have. | `issues/invariant-audit-gaps.md` |
| — | The daemonized (default) mount **fails on a first-ever mount** — the log file is opened before the state directory exists. No test covers the daemonized branch. | `issues/daemonized-mount-first-run.md` |
| 1, 6, 7 | A **write racing a commit is recorded clean**. `_commit_hop` clears a copy from a flag it resets and a stat it compares; a write that read the flag before the reset and lands after the stat, or one of the same size inside one timestamp tick, leaves the holder clean with bytes its upstream lacks, and the next eviction deletes them. Both routes reproduced by injection. | `issues/racing-write-marked-clean.md` |
| 5, 11 | A **cancelled rename leaks its old-path tombstone**. Rename a file, remove it before the move commits, and the masking row at the old path outlives every drain and remount; a later directory rename that brings the same name under that path raises `sqlite3.IntegrityError`, not an `OSError`. | `issues/namespace-sync-redesign.md` |
| 8 | A **detach leaves an overlay the next node cannot mount**. After a complete rescue the database still records copies on the node-scope tiers, so a mount where those tiers are empty is refused with "audit required". | `issues/shutdown-rescue-gaps.md` |
| 5, 9 | **Entries created through the mount are recorded `0:0`.** `create` and `mkdir` default the owner to root and the FUSE adapter passes no caller identity, so the catalog disagrees with the source it commits to. The engine half is measured; the adapter half is read from the code. | `issues/posix-surface-gaps.md` |

Several of these are also `PLAN.md` inaccuracies rather than pure code defects. Under the repository's
plan-drift rule the reconciliation belongs in `PLAN.md` first, with the maintainer; that work is
tracked in `issues/plan-md-reconciliation.md`.

## Verification

The suite is the gate, and it is fast enough to be one: **780 unit tests in ~60 s serial, ~30 s under
`-n auto`** (the stateful model alone takes ~25 s). Four layers; a `verify:` should use at least one and
name the post-condition it asserts:

- **Static** — `tools/lint.sh`: the ruff selection CI runs, the layering assertion, SPDX headers,
  the version single-source, and the `CLAUDE.md` symlink.
- **Unit** — `uv run pytest -m unit`, scoped for a focused phase. Engine-first: most behavior is
  reachable against tmpdir tiers with no mount, which is the payoff of the layering rule.
- **Behavioral, sandboxed** — `tools/temp_site.sh` for CLI drives and real mounts, with
  `TERRACE_*` scrubbed and the XDG paths redirected. It isolates an *invocation*, never an in-process
  `import terrace`: `core/platform.py` reads the XDG variables into module constants at import and
  creates directories as a side effect, which is why the pytest helpers shell out too. **Do not run
  pytest through it** — pytest loses its rootdir and exits 5, and the scrub strips
  `TERRACE_TEST_MINIO_*`.
- **Integration** — `uv run pytest -m integration` after `docker compose up -d --wait`. Not reachable
  on every machine and not run by CI; when a criterion needs it, say so.

**The bundle loop is a progress record.** `tests/loop/test_scenario.py` drives the loop end to end
as nine stages: reach, read, pressure, change, re-aggregate, read back, release, offline commit and
several bundles. Today they are 9 expected failures, each a strict `xfail` whose reason names the
seed that owns the stage, and each stopped at the same step: the spec parser's refusal of the archive
tier (`issues/archive-spec-plumbing.md`). The marker accepts one exception, `Blocked`, raised only by
`blocked(...)` around the one step the tree is known to stop at, with the exact error and text; a
stage that fails anywhere else fails the run, and so does a stage that starts to pass. A cycle that
moves the block runs every stage behind it to its next stop and must, in the same change, remove the
marker of each stage that now passes and pin the new stop of each that does not with `blocked(...)`.
`tests/loop/conftest.py` records on each stage's report what stopped it (`stopped`, `cause`) and the
marker it was collected with (`marker`), since a reason string can be had by a stage that excuses itself.

**Exit 0 is necessary but not sufficient.** Assert the post-condition: the residency row, the xattr,
the file present at the source after a flush, the reported pass count, the specific stderr line. A
run that "completed" but left the wrong catalog state is a failure — and an integration gate that
exits 0 having skipped everything is the canonical example.

## Prose and comments

The project's documentation and comments have a voice. Keep it. Overly verbose prose — the padding,
hedging and marketing adjectives that generated text tends toward — costs a reader's confidence in
code that is otherwise correct, and a reference implementation that is not read is worthless.

**Write:** declarative statements; the *why*, not the what; concrete failure modes with their costs;
whole sentences with terminal punctuation. Comments only for constraints the code cannot express.

**Do not write:** filler and hedging ("simply", "just", "note that", "essentially"); marketing
adjectives ("comprehensive", "robust", "seamless", "elegant", "leverage", "utilize") — if a property
matters, state the measurement; "This ensures that…", "In order to…"; restatements of the adjacent
line; emoji or decorative Unicode; bulleted lists where two sentences would do; symmetry for its own
sake.

**Never embed feature-scoped spec ids** (`R1`, `P3`) in `src/`, `tests/` or `README.md`. They restart
per feature, live in `spec/{slug}/`, and collide across branches. Requirement provenance lives in the
commit, the PR, and the retained `spec/{slug}/` record. Referencing stable things is fine: real
symbols, documented invariants, `PLAN.md` sections.

## Code style

Follows hypershell conventions (the author's house style):

* `src/` layout; 120-character lines; SPDX headers on every module (all 84 `.py` files under `src/`,
  `tests/` and `tools/` carry them; `lint.sh` keeps it that way).
* Module template:

```python
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Concise module docstring."""

# Type annotations
from __future__ import annotations
from typing import Protocol

# Standard libs
import os

# External libs
from cmdkit.app import Application

# Internal libs
from terrace.core.logging import Logger

# Public interface
__all__ = ['Thing']

# Initialize logger
log = Logger.with_name(__name__)
```

* Import groups in that order, blank line between groups; explicit `__all__` per module.
* Full type annotations; PEP 604 unions (`str | None`); concise docstrings.
* Classes for state machines, threads, tiers, and cmdkit Applications; functions for utilities.
  Long-running concurrent components are `StateThread` subclasses — `core/fsm.py` merges the state
  machine and the thread into one class, deliberately, unlike hypershell's split. A subclass declares
  a nested `states` enum with `START` and `FINAL` plus one `on_<state>` method each, wired at class
  definition; halting is an `Event`, not a state.
* Logging through `Logger.with_name(__name__)`; TRACE for per-operation detail. `rich` is a
  declared dependency but is **not used** — formatting goes through `cmdkit.ansi`.
* SQL is PEP 750 t-strings rendered by `terrace.state.sql.render` (interpolations bind as parameters;
  `{name:id}` splices whitelisted identifiers) — never hand-assemble SQL strings or placeholders.
  Static DDL lives in `schema.sql`.
* Deferred imports are ordinary function-local imports, not PEP 810 `lazy import` syntax, which
  appears nowhere in `src/`. libfuse and obstore load only when actually used.
* Adding a subcommand touches four places and missing one is silent: the `Application` subclass,
  `APP_HELP`, the `commands` registry, and `tests/test_cli.py`.

## Working on this codebase as an agent

- **Use the factory for non-trivial work.** A feature, fix or refactor flows through the factory
  lifecycle (§ *Factory*): **`code terrace feature`** (shape `GOAL.md`, or promote an
  `issues/{slug}.md`) → **`code terrace plan`** (research + `PLAN.md`/`TECH.md`) →
  **`code terrace build`** (execute phases) → **`code terrace review`** (blind, externally-verified
  QA) → **`code terrace publish`** (squash onto `main` per the profile's `landing`), each on a branch
  with artifacts committed under `spec/{slug}/`. The factory's method in the operator's KB is the
  *why*; § *Invariants* is the curated footgun checklist. **Ceremony scales to appetite** — a
  one-sentence change skips the lifecycle entirely.
- Three operational siblings sit outside the lifecycle: **`code terrace harness`** keeps the KB's
  factory canon and this file's § *Factory* and § *Invariants* in step, **`code terrace roadmap`**
  retires the seeds whose cycles have landed and keeps `ROADMAP.md` true, and
  **`code terrace release`** cuts a tagged version per the release recipe.
- **Verify by running the suite and driving the CLI**, not by reading. The concurrency, the crash
  semantics and the policy clocks are where the defects are, and the first two are exactly where the
  suite is thinnest — there is no fault injection and the integration lane runs nowhere.
- **Put logic where it belongs.** Namespace and residency state → `state/catalog.py` and
  `state/journal.py`; placement and policy decisions → `engine/`; atomicity → the tier adapter. Never
  hand-roll a state predicate in adapter code, and never put policy in the FUSE adapter, where no
  cheap test can reach it.
- **Prefer deleting to adding.** This is a reference implementation whose value is that a reader can
  follow it. A new configuration knob, a new fallback, a new special case each cost more than their
  implementation, and `PLAN.md` § *Future work* is the record of what was already deferred on
  purpose.
- **This file is the map, and it drifts.** For a deep change, re-verify the specific invariant against
  the source before relying on it, and update this file when the code moves.

## Factory

This repo is built by the spec-driven factory: GOAL → PLAN/TECH → build → blind review → publish. The method and the skills live in the operator's KB (its agentic-engineering method and the `code-*` skills, invoked as `code terrace <verb>`), and none of it is committed here; a local copy is gitignored. The cycle's records live here: `spec/{slug}/` for work in flight, `issues/` and `ROADMAP.md` for deferred work. Inside this repo, its own rules win.

### Profile

| Key | Value |
|---|---|
| base | `main`, the branch every cycle starts from and lands on |
| branches | `{kind}/{slug}`: `feature/`, `fix/`, `refactor/`, `docs/`, `test/`, `ci/`, the prefix per GOAL `kind`; the set is open |
| commit categories | `[feature] [fix] [refactor] [docs] [test] [ci] [build] [release] [factory]`, where `[factory]` is a change to § Factory or § Invariants (the old `[harness]` category is retired). Branch commits take the GOAL's `kind` verbatim (an open set); landing subjects use these categories, and the set is open |
| branch commits | `[{kind}] Build {slug} P<n>`, with no trailer; the landing (squash) commit carries the `Co-Authored-By` trailer this repo records (§ Conventions) |
| landing | `squash-pr`: push the cycle branch, open a pull request, and squash-merge it as one `[category] what changed`. The foreman merges once the pull request's checks are green, then fast-forwards the main checkout |
| ci | the landing gate. `.github/workflows/ci.yml` runs on every pull request and on every push to `main` and to a cycle branch; the foreman reads `gh pr checks` before a merge |
| setup | `uv sync`: dependency refresh, run before any verify |
| verify | `tools/lint.sh && uv run pytest -n auto -m unit`: the full gate. Assert the reported pass count, not the exit code: exit 0 is necessary but not sufficient (§ Verification), and `uv run pytest -m integration` exits 0 when every test skipped. Phases narrow it; publish and release run all of it |
| throwaway environment | `tools/temp_site.sh <cmd…>` (contract below) |
| coupled core | `src/terrace/state/{db.py,journal.py,schema.sql,lifecycle.py,registry.py}`, `src/terrace/engine/overlay.py`, `src/terrace/engine/transfer.py` |
| same-commit rules | see § Conventions |
| high-blast-radius paths | see § Invariants |
| security lane | on: undisclosed security findings go to gitignored `.security/`, never `issues/` |
| release | see Release recipe (`release.trigger`) |

### Release recipe

```yaml
release:
  trigger: on-demand
  version:
    source: pyproject.toml project.version
    scheme: pep440
    tag_format: "{v}"
    prerelease_syntax: '(a|b|rc)[0-9]+$|\.dev[0-9]+$'
    propose_next: none
  bump:
    lock: uv lock
    lockstep: []
    files: [pyproject.toml, uv.lock]
    diff_rule: only project.version in pyproject.toml and the project's own version entry in uv.lock change
    subject: "[release] Version {v}"
  gate: ["tools/lint.sh", "uv run pytest -n auto -m unit", "uv build", "uvx twine check dist/*"]
  tag:
    message: "Release {v}"
    signed: true
  notes: none
  modes:
    release:
      base: main
      prerelease: forbidden
      final: true
      inputs: []
      work_branch: in-place
      branch_ops: []
      merge_into: none
      tag_on: bump
      back_merge_into: none
      push: ["HEAD:main", "{v}"]
      publish: none
  post_verify: []
  caveats: {}
  clean_paths: [dist/, .venv/]
```

Not on PyPI yet, so no mode publishes; the tag is bare (`0.1.0`, not `v0.1.0`) and signed.

### Throwaway environment

`tools/temp_site.sh <cmd…>` runs `<cmd…>` against a disposable instance of the app. It:

1. runs from the repo or worktree root;
2. creates a fresh state directory and removes it on every exit path;
3. redirects all app state (database, config, index, logs, keys) into that directory, so real data is never touched;
4. runs the command inside it, so relative writes can't reach the worktree;
5. exercises the current tree: rebuild or run from source, never a stale binary;
6. pins tool discovery back to the repo;
7. writes its own output to stderr and passes the command's exit status through.

Here that means: every `TERRACE_*` variable is scrubbed, `XDG_CONFIG_HOME` and `XDG_STATE_HOME` point into the sandbox, the command's working directory is the sandbox (which also isolates `./.terrace.toml`), and `UV_PROJECT` points back at the repo so `uv run` exercises the current tree. `--keep` leaves the sandbox in place and prints its path, for inspecting a state database after a failed drive. It isolates an invocation, never an in-process `import terrace`, and pytest must not run through it (§ Verification).

### Where deferred work goes

A pass that decides *not* to fix something still records it, and the destination is not a matter of
taste:

| Home | Holds | Written by |
|---|---|---|
| `issues/{slug}.md` | **Deferred code work**, pre-shaped from the KB's ISSUE template, `status: unshaped` | whoever defers it |
| `ROADMAP.md` | the **ordered index** — one entry per issue, `**Seed:**` pointing at the file | whoever defers it |
| `.security/issues/{slug}.md` + `.security/ROADMAP.md` | the same two, for **unremediated security findings** — gitignored, never published | whoever defers it |
| `spec/{slug}/` | work **actually in flight** | the lifecycle skills |
| the KB inbox | **Harness/skill feedback only** — "was this the *factory's* fault", as `friction` lines. Never code follow-ups. | the lifecycle skills |

An `issues/{slug}.md` is a *candidate, not a contract*. `code terrace feature` promotes it into a
`GOAL.md`, and that promotion is where appetite, non-goals and the R-IDs get negotiated with a human.
Never copy one into a `GOAL.md` verbatim. The `status:` field is the guard: `unshaped` (raw deferral),
`shaped` (negotiated with a human but not yet accepted into a cycle), `adopted:{slug}` (promoted),
plus the terminal `declined` and `accepted-behaviour`.

**A deferral is retired, not kept forever.** When the cycle that adopted a seed lands on `main`,
`code terrace roadmap` deletes the seed and removes its `ROADMAP.md` entry: `spec/{slug}/` is the
retained account and git history holds the file. The security lane inverts this — nothing under
`.security/` is ever deleted, because it is gitignored and a deletion there leaves no history to
recover from.

**The security lane is not optional.** A deferral describing an unremediated weakness — a live
exploitable mechanism or enough evidence to reconstruct one — goes in `.security/`, which is
gitignored. A public roadmap of live vulnerabilities is an attacker's work plan. The *fixes* land as
ordinary public commits when they ship; only the standing inventory stays private. When in doubt
which lane, use `.security/` and ask.

## Out of scope for v1

Do not implement these without an explicit plan change (`PLAN.md` → non-goals / future work):
multi-client coherence, chunk-level staging, special files and cross-tier hard links, byte-range lock
pass-through, indexed random access into compressed archives, pinning, Prometheus metrics, a pyfuse3
adapter, Windows.
