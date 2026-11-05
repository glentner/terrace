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
- Open question — **the sampling profiler.** PEP 799 is named as a technology decision and appears
  nowhere in `src/` or `tests/`. As a local race-hunting aid it is cheap and useful; wired into CI it
  produces artifacts nobody reads and a second thing to keep green.
- Open question — **which marker.** Inside `-m unit` the stress module inflates the 3.5 s gate that
  every job and every `verify:` pays. Behind a new marker it needs an entry in
  `pyproject.toml:58-61`, a CI step, and a decision about whether the macOS job runs it too.
- Open question — **how long is long enough.** A workload that finds a race in 10 s on a 16-core
  runner may find nothing in 10 s on a 2-core one. Bounding by operation count makes the run
  deterministic and makes the coverage machine-dependent instead.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the FUSE/CLI/
  layering review named the handle fields and the logging initializer, and the verification-substrate
  review measured that both CI jobs run the same command.
