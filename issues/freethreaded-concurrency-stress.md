---
status: unshaped
kind: test
appetite: small
lane: public
---

# Concurrency stress on the free-threaded build

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`PLAN.md` § *Testing strategy* says "Free-threaded jobs run the same suite plus targeted concurrency
stress tests". They run the same suite and nothing else. `.github/workflows/ci.yml:44` is
`uv run pytest -n auto -m unit` for both matrix entries, and `ci.yml:58` is the identical string on
macOS. The only free-threading-specific content in the `3.15t` job is `ci.yml:39-42`, which asserts
`sys._is_gil_enabled()` is false after importing `obstore` — a real and necessary check, but it
proves the GIL is off, not that the code is correct without it.
- 2026-11-06: the `ci.yml` line numbers above hold at `6a046b4`. Since the toolchain repair
  (`50a11bd`) added a libfuse step, the free-threading assertion is `ci.yml:44-47` and the unit
  command is `:51` (Linux) and `:65` (macOS), both now `uv run pytest -n auto -m unit -rfEs`.

The nearest thing to a stress test is `tests/tier/test_concurrency.py`: 110 lines, `THREADS = 8`
(`:27`), three tests (`:61`, `:74`, `:95`) covering registry counter arithmetic and `PosixTier.put`
atomicity. It is marked `unit` (`:25`) and therefore runs on every job equally — the GIL build gets
exactly the same eight threads as the free-threaded one. Nothing anywhere drives parallel FUSE
callbacks against transfers against eviction, which is the workload the product actually runs.

The audit named three pieces of unguarded shared state, none of which any test would find:

- **`Handle.written` and `Handle.marked_dirty`** are plain slots (`src/sosfs/engine/overlay.py:53`)
  written from callback threads with no lock: `overlay.py:650-653` in `write`, `:663` in
  `_mark_handle_dirty`, `:669-672` in `truncate`. They are read at `:707` (`fsync_policy == 'commit'
  and handle.written`) and `:738`. The write-back pump resets `marked_dirty = False` under
  `_handles_mutex` (`overlay.py:788-791`) — so the field is guarded on the pump's side and not on the
  callback's. This is benign today because `Journal.mark_dirty` is idempotent and a lost write costs
  one redundant journal round trip. It stops being benign the moment a handle field *gates* an action
  instead of caching one, and nothing in the code says which kind a new field is.
  - 2026-11-06: the `overlay.py` line numbers in this item hold at `141aa86`;
    `deferred-rename-source-orphan` moved the code. On its tree the slots are at `overlay.py:54`,
    the writes at `:678-681` (`write`), `:691` (`_mark_handle_dirty`) and `:697-700` (`truncate`),
    the reads at `:735` (`fsync`) and `:774` (`_record`), and the pump's reset at `:846-849`.
- **`initialize_logging`'s guard is an unsynchronized load-then-store.**
  `src/sosfs/core/logging.py:216-226`: `_INIT: bool = False`, then `if _INIT: return` and, in the
  `else`, `_INIT = True` before any of the work. Setting the flag first narrows the window to the gap
  between the load and the store instead of closing it: nothing serializes the two, so both threads
  can read `False`, both write `True`, and both go on to add the stream handler. Every subsequent log
  line is then emitted twice — including the TRACE per-operation lines that are the only diagnostic
  for a mount that is misbehaving.
- **`ArchiveTier._members` and `_children`** (`src/sosfs/tier/archive.py:254-255`) are plain dicts
  rebuilt wholesale by `reindex` (`:311`, `:315`) and read from roughly a dozen call sites (`:468`,
  `:476`, `:481`, `:491`, `:511`, `:522`, `:602`, `:616`, `:631`, `:648`, `:665`, `:670`).
  `engine/locks.py` is path-keyed, so two writes to different members of the same archive are not
  serialized against each other or against a concurrent `index()`.

None of this was reproduced; it was found by reading, and the citations are the evidence. The
doubled-log-line race is the one that would be cheapest to demonstrate.

*Added 2026-11-06 by `deferred-rename-source-orphan`:* two more, from that cycle's plan
(`spec/deferred-rename-source-orphan/PLAN.md` § 2.5 and § 5, research 04 § 1). Line numbers are at
the tree that cycle landed.

- **The source-namespace mutex is argued, not tested under threads.** `OverlayEngine._source_lock`
  (`AGENTS.md` § *Invariants* § 10) serializes namespace replay, `_vacate`'s collect-and-replay,
  `_purge_source`, a `create` onto the source and a data commit to the source. The race it closes is
  silent loss: a replay reads `source_path = P1`, an eviction or `fsync` commit writes the new bytes
  at `P2` and records `source_path = P2`, and the replay's `rename(P1, P2)` then replaces them with
  the old bytes while the residency already reads clean. The suite is single-threaded; the mutex is
  exercised only by deterministic stale-snapshot regressions in `tests/engine/test_replay.py` and by
  the model's split of a scheduler poll into the pump's scan and commit. Nothing runs a replay and a
  data commit for the same entry on two threads.
- **Path locks do not cover descendants** (pre-existing; that cycle neither widened nor fixed it). A
  directory `rename` holds `lock(old, new)` only (`src/sosfs/engine/overlay.py:425`), and
  `Catalog.rename` rewrites every descendant's path (`src/sosfs/state/catalog.py:170`) while another
  thread may hold `lock(child_old_path)`. The child's `transfer.copy` then fails with `LookupError`
  (`src/sosfs/engine/transfer.py:85`) or reads a moved staging file.
  - 2026-11-08: these line numbers hold at `5e0d997`; `source-served-open-race` moves the
    `overlay.py` one.

*Added 2026-11-06 by `deferred-rename-source-orphan`, review cycle 1:* user removals take no source
mutex. `unlink` and `rmdir` cancel an entry's queued rows and queue the removal under path locks alone
(`_remove`, `src/sosfs/engine/overlay.py`), so one can land inside a replay's act. The review found a
replay then settling the removal's own row, which had taken the cancelled row's id; replay now settles
with `Journal.settle_current`, pinned by injected interleavings in `tests/engine/test_replay.py`. One
more race of the same shape stays open:

- **A removal racing a replayed `mkdir`.** The replay places the directory on the source and records
  its `source_path` only in its confirming transaction. An `rmdir` in between reads `source_path` as
  `NULL`, so it purges the entry instead of queuing a removal; the purge cascades the replay's intent
  row, the replay's `confirm` raises `LookupError` out of `commit_row` (on the pump's thread, killing
  the pump), and the source keeps a directory the catalog no longer has. Reproduced single-threaded by
  wrapping the source tier's `mkdir` to call `rmdir` after the real one. Pre-existing: `141aa86` ends
  the same way, raising at its settle. A test needs real threads or that injection; a fix either
  brings removals under the mutex (they would then wait for a whole source-bound copy, like `create`)
  or makes the replay tolerate an entry purged under it and remove what it placed.
- Queue-row identity is by value (id, entry, op, boundary, detail, first-dirty time), because ids are
  reused and `AUTOINCREMENT` is a schema change with no migration path. A reused entry id carrying an
  identical row queued at an identical clock reading would pass as the same row. Not reproduced; it
  needs a clock that repeats a reading.

*Added 2026-11-08 by `source-served-open-race`:* one more, from that cycle's plan (its PLAN § 5 and
adversarial review), deferred here because it needs a rename of an ancestor during the call, which
path locks do not cover (above).

- **An ancestor's round trip between `open`'s tier decision and its act.** `open` decides which copy
  serves under the file's path lock alone, before any source lock, and a rename of an ancestor away
  and back during the call can change what the path names in between. A staged file renamed into the
  path's place makes `open` return a handle on that other file's stage bytes, and a write through it
  lands in that file's copy, which still reads clean. Silent. Reproduced by injection by that plan's
  adversarial review, and identical at `5e0d997`. A promotion off the source could likewise copy over a
  stage copy written meanwhile and record it clean (silent loss; argued, not reproduced), and a chosen
  stage copy evicted meanwhile fails the call loudly. Taking the source lock for every `open` would
  not close it, since the staged branch never touches the source: a fix needs the ancestors' path
  locks, or the decision made again under a lock that covers them.
  - 2026-11-08, review cycle 2 of `source-served-open-race`: the promotion case is now reproduced, not
    only argued. `open` decides to promote a file held on the source; an ancestor is then renamed away,
    the file is opened and written there, and the ancestor is renamed back. The promotion copies the
    source's bytes over the dirty stage copy and records both residencies clean, so the write is gone
    once the queue drains. Reproduced by injection, identically at `bab1e83` and `5e0d997`.

*Added 2026-11-08 by `source-served-open-race`, review cycle 1:* an instance of R7's "no exception",
out of that cycle's scope (it touched none of this code).

- **A replay racing a user rename of the entry's directory.** `_execute_namespace_locked` reads
  `entry.path` under the source lock and passes it to `journal.begin(path=…)`. A user `rename` of the
  entry's directory that has already passed its `_vacate` moves the catalog without the source lock,
  so it can land between the two; `begin` then raises `LookupError: No such entry` out of `flush`,
  and out of `commit_row` on the pump's thread, killing the pump. The row stays queued, and a later
  flush converges. Reproduced deterministically by injection at `e034985` and identically at
  `5e0d997`, and seen under real threads.

## Why it was deferred

Found by the invariant audit run while porting the software factory, which was a harness change.
Beyond that, a stress suite is only worth writing against a surface that has stopped moving: every
cycle queued above this one in [`ROADMAP.md`](../ROADMAP.md) changes the engine, the tier
construction, or both, and a race test written against a signature that changes next month is
deleted rather than fixed. The `ArchiveTier` map is a correctness fix owned by
[`issues/archive-journaling-and-locking.md`](archive-journaling-and-locking.md); this cycle owns the
workload that demonstrates it.

## Outcome / vision

The `3.15t` job runs something the `3.15` job does not, and that something is a workload with the
same shape as production: many callback threads, both pumps ticking, transfers in flight, eviction
under pressure. A race is a red CI job with a reproducible seed, not a support ticket about a mount
that hung once. The handle-field rule is written down where the next person adding a field to
`Handle` will read it.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHEN 32 threads drive mixed create/write/read/rename/unlink against one `OverlayEngine`
  for a bounded operation count on a free-threaded interpreter, the engine SHALL raise no exception
  and the source after `flush()` SHALL equal a plain-directory oracle.
- **R2** — WHILE the write-back and eviction pumps run at a zero-second tick under that workload, no
  reader SHALL observe a short read or `EBADF` on a file it holds open, and no residency row SHALL be
  dropped for a tier that holds an open handle on that entry.
- **R3** — WHEN two threads call `initialize_logging` concurrently, exactly one stream handler SHALL
  be installed and a single emitted record SHALL produce exactly one line.
- **R4** — WHEN concurrent readers call `ArchiveTier.index()` while another thread re-packs, every
  reader SHALL observe either the pre-pack or the post-pack member set, never a partial one.
- **R5** — THE CI `3.15t` job SHALL run the stress module as a step distinct from
  `uv run pytest -n auto -m unit`, and that step SHALL fail if `sys._is_gil_enabled()` is true.
- **R6** — THE stress module SHALL bound itself by operation count rather than wall time, and SHALL
  print the seed it used on failure so the case is reproducible.
- **R7** *(added 2026-11-06, `deferred-rename-source-orphan`)* — WHILE the write-back scheduler
  replays queued `rename` rows on its own thread, and other threads commit the same entries' data to
  the source (eviction, `fsync` under `fsync_policy = "commit"`) and run `create`, `mkdir` and
  `rename` callbacks over those paths, the engine SHALL raise no exception, and once the queue drains
  the source SHALL hold exactly one object per entry, with the last committed bytes, equal to the
  overlay view.
- **R8** *(added 2026-11-06, `deferred-rename-source-orphan`)* — WHEN a directory is renamed while
  another thread commits or stages a child of it, the child's operation SHALL complete against the
  child's current path, or fail with an `OSError` the VFS API maps, never `LookupError` or a read of
  a moved staging file.
- **R9** *(added 2026-11-06, `deferred-rename-source-orphan` review cycle 1)* — WHEN a user `unlink`
  or `rmdir` runs on one thread while the write-back scheduler, `flush` or a drain replays a queued
  op for the same entry on another, the replay SHALL raise no exception, SHALL settle only the row it
  read, and once the queue drains the source SHALL equal the overlay view (no directory left behind a
  replayed `mkdir` whose entry was removed).

## Notes

- Related: [`issues/archive-journaling-and-locking.md`](archive-journaling-and-locking.md) owns the
  tier-wide lock R4 asserts against;
  [`issues/crash-fault-injection-suite.md`](crash-fault-injection-suite.md) builds a comparable
  out-of-process harness and the two should share it rather than each grow one;
  [`issues/benchmark-harness.md`](benchmark-harness.md) needs the same mixed-workload driver;
  [`issues/pump-liveness-and-loss-windows.md`](pump-liveness-and-loss-windows.md) matters here because
  a pump that dies mid-stress today leaves the workload passing and nothing committing. Root
  `PLAN.md` → § *Testing strategy* (the CI-matrix bullet), § *Risks and mitigations* ("SQLite
  contention under heavy metadata load … measured in the Phase 10 stress suite"),
  § *Technology decisions* (the sampling profiler). `AGENTS.md` § *Invariants* § 10 states the
  handle-field and module-global rules this cycle would test.
- Related *(added 2026-11-06)*: `spec/deferred-rename-source-orphan/` (PLAN § 2.5, § 5; research
  04), whose mutex R7 tests; [`issues/source-commit-staged-publish.md`](source-commit-staged-publish.md)
  narrows that mutex's hold and needs the same threaded driver to show its latency claim.
- Open question — **the sampling profiler.** PEP 799 is named as a technology decision and appears
  nowhere in `src/` or `tests/`. As a local race-hunting aid it is cheap and useful; wired into CI it
  produces artifacts nobody reads and a second thing to keep green.
- Open question — **which marker.** Inside `-m unit` the stress module inflates the 3.5 s gate that
  every job and every `verify:` pays. Behind a new marker it needs an entry in
  `pyproject.toml:58-61`, a CI step, and a decision about whether the macOS job runs it too.
  - 2026-11-06: `pyproject.toml:58-61` holds at `6a046b4`. Since the toolchain repair (`50a11bd`)
    the `markers` list is `pyproject.toml:61-64`.
- Open question — **how long is long enough.** A workload that finds a race in 10 s on a 16-core
  runner may find nothing in 10 s on a 2-core one. Bounding by operation count makes the run
  deterministic and makes the coverage machine-dependent instead.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the FUSE/CLI/
  layering review named the handle fields and the logging initializer, and the verification-substrate
  review measured that both CI jobs run the same command.
