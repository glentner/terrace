# PLAN — A write racing a commit is never recorded clean

> **Status:** Draft for review · **Last updated:** 2026-11-12
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). This is a lean plan (`kind: fix`,
> `appetite: small`, `skip research` by the foreman's ruling): there is no `research/`. Every design
> element traces to a GOAL R-ID.

**Names.** Another cycle renames the project before this one is built, and this branch is rebased onto
it first. Every path, import and command here uses the new names (`src/terrace/`, `import terrace`).
The code was read, and the prototype run, at `bf438b4`, where the package is `src/sosfs/`; line
numbers are from that commit.

## 1. Summary

A hop keeps its own record of whether a change met it. The record opens when the hop's copy starts
reading the holder, every route that changes a holder's bytes writes to it under the mutex that
already guards the handle table, and the hop reads it once, after its last look at the holder, to
decide between clean and re-armed. The size-and-timestamp comparison stays, as a veto for bytes
changed behind the engine's back, and is never what clears a copy. No table, no configuration key
and no schema change; a write on a dirty handle still runs no SQL.

## 2. Design

### What is wrong, confirmed

Read at `bf438b4` and run on a prototype (§ 4). `_commit_hop` decides from two signals, and both are
on the wrong side of a writer that takes no lock:

- `write` reads `handle.marked_dirty` with no lock and then calls `os.pwrite` (`overlay.py:793-796`).
  The hop resets the flag under `_handles_mutex`, then stats (`:992-996`). A writer that read the flag
  set and lands after the stat is seen by neither. `truncate` through a handle has the same two lines
  (`:835-837`), and the prototype shows the base loses it the same way (R2's open question: **the
  base fails R2**).
- A change that lands after the copy read its range and leaves size and mtime equal passes the stat
  comparison (`:997`), and the flag was still set when it ran, so it marked nothing.
- A handle that writes inside the hop and is released before the hop decides leaves no trace at all:
  the reset loop only visits handles still open.

### The record

Three pieces of state, all guarded by the existing `OverlayEngine._handles_mutex`
(`src/terrace/engine/overlay.py`):

| State | Meaning |
|---|---|
| `OverlayEngine._hops: Dict[Tuple[int, str], bool]` | One key, `(entry id, holder tier)`, per hop in its interval. The value is "a change met this hop". Present only between a hop's opening and its decision. |
| `Handle.changing: int` | Changes through this handle that the engine has admitted and that have not returned. More than one thread may write one handle. |
| `Handle.marked_dirty: bool` | Unchanged in meaning: the handle may skip the dirty mark. It is now read and written only under the mutex. |

### The three routes that change a holder's bytes

One context manager, `OverlayEngine._change(handle)`, wraps the act of both handle routes, and one
helper, `OverlayEngine._meet(entry_id, tier)`, is the only code that writes a hop's record.

```
_change(handle):                                  # write, and truncate through a handle
    with _handles_mutex:
        admitted = handle.marked_dirty
        if admitted: handle.changing += 1; _meet(handle.entry_id, handle.tier)
    if not admitted: _mark_handle_dirty(handle)   # path lock; mark_dirty; then, still under the
                                                  # path lock, with the mutex: set the flag,
                                                  # changing += 1, _meet(...)
    try:    yield                                 # os.pwrite / os.ftruncate
    finally:
        with _handles_mutex: handle.changing -= 1

_meet(entry_id, tier):                            # caller holds the mutex
    if (entry_id, tier) in _hops: _hops[entry_id, tier] = True
```

- **`write`** and **`truncate` through a handle** (`open` with `O_TRUNC` reaches the second) run
  their `os.pwrite` or `os.ftruncate` inside `_change`. `handle.written` is set after, as today.
- **`truncate` with no handle** already holds the path lock from its decision through its recording.
  It calls `_meet(current.id, serving)` under the mutex after its `mark_dirty` and before its cut, so
  a cut that runs inside a hop (the path lock is re-entrant on the committing thread) is on the
  record too.
- `_mark_handle_dirty` keeps its order: the dirty mark commits before the bytes land (§ 1). It now
  sets the flag and admits the change under the mutex while it still holds the path lock, so a commit
  that takes the path lock next finds the change in flight. A handle served by the source has no
  upstream and no hop; it sets the flag and admits the same way, with no SQL, as today.

The flag is read and the change admitted in one hold of the mutex. That is what closes Route 1: a
writer can no longer have read the flag and be invisible.

### The hop

`_commit_hop` (its callers hold the path lock, as today):

```
arm():                                            # called by TransferPool.copy, before its stat
    with _handles_mutex:
        _hops[key] = _hops.get(key, False) or any(
            h.tier == holder and h.changing for h in _handles.get(entry.id, []))

copied = transfers.copy(..., before_read=arm)     # both branches; the source branch under _source_lock
stat   = tiers[holder].stat(path)                 # the veto's input: the hop's last look at the holder
with _handles_mutex:                              # the one decision
    met = _hops.pop(key, True)                    # no record is read as met
    for h on holder: h.marked_dirty = False
changed = met or stat is None or (stat.size, stat.mtime) != (copied.size, copied.mtime)
# then the recording transaction, unchanged: re-arm the row when `changed`, else clean and settle
```

- **Opening.** `TransferPool.copy` (`src/terrace/engine/transfer.py`) takes one new keyword,
  `before_read: Optional[Callable[[], None]] = None`, and calls it inside its `try`, immediately
  before `source.stat(...)`, the first time it observes the holder. The intent row has committed by
  then and no transaction is open. Promotions pass nothing. The record opens **already met** when any
  handle on the holder has a change in flight: that change may land after its range is read. An
  opening never clears a record that is already there (two hops on one key cannot happen under one
  path lock; if they ever do, neither goes clean on the other's record).
- **Closing.** After the copy and after the stat, one hold of the mutex pops the record and resets
  `marked_dirty` on the holder's open handles. A change admitted before that hold wrote the record; a
  change that comes after finds its flag reset, goes to `_mark_handle_dirty`, and waits on the path
  lock the commit holds until the outcome is recorded. There is no third case.
- **Failure.** If the copy or the stat raises, the record is removed (`pop(key, None)`) and nothing
  else changes: the holder stays dirty and queued, and the flags stay set. A missing record at the
  decision reads as met, so a hop that never opened one cannot record clean.
- **The reset stays unconditional**, as today (`:992-995`). After a hop that a change met, the
  holder is still dirty and queued, so the reset is not needed for truth. It is what makes a writer's
  next write wait on the path lock, and that wait is what lets `commit_upstream`'s loop end under a
  writer that never pauses (deviation row 5).
- `commit_upstream`, `commit_row`, `commit_to_shared` and `evict_copy` are not edited. Eviction keeps
  trusting the `state` column.

### Why clean is now truthful

For a hop that records clean, take any change through a holder handle. Its admission and the hop's
opening and closing all happen under one mutex, so they are ordered:

1. Admitted and returned before the opening: its bytes were on disk before the copy's first look, and
   the copy carries them.
2. Admitted before the opening, not returned at the opening: the record opens met. Not clean.
3. Admitted between the opening and the closing: `_meet` writes the record. Not clean. This holds
   whether or not the handle is still open at the closing, because the record belongs to the hop.
4. Not admitted by the closing: the handle's flag is reset in that same hold (it is in the table,
   since `open` needs the path lock and a handle with a change in flight is not released), so the
   change takes the path lock and marks the holder dirty after the hop's recording has committed.

A `truncate` with no handle from another thread waits on the path lock (R3, as today); on the
committing thread it is case 3. Equal size and timestamps appear nowhere in the argument (R4).

The argument has one premise it does not prove: a holder handle's fd names the holder's current file.
That fails today, and still fails after this change, when a commit from a faster tier renames a new
file over a stage a handle is open on. It is a different defect with no commit in flight, reproduced
by the review, and it has its own seed (§ 5).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `_change` around `os.pwrite`; the record opens met for a change in flight, `_meet` writes it for one admitted later; `_commit_hop` serves both `commit_row` and `commit_upstream`. Offset, length and the stat play no part. |
| R2 | `truncate` through a handle runs `os.ftruncate` inside the same `_change`. The base fails it (prototype, § 4). |
| R3 | The path lock serializes a handle-less `truncate` with a commit, unchanged; `_meet` before its cut covers the committing thread. |
| R4 | `changed = met or <stat veto>`: clean needs an unmet record. The same-size test freezes the tier's reported stat, so it does not depend on the filesystem. |
| R5 | No change: the record opens unmet (`changing` is 0 for every handle, written or idle) and stays so; one copy, clean, settled, in the one call. A write that returned before the copy's first look is case 1, which is why the record opens inside `copy` and not in `_commit_hop`. The stat veto is kept, so `test_mid_copy_write_rearms_instead_of_settling` passes unchanged; `marked_dirty` is kept and still reset, so `test_open_handle_requeues_after_background_commit` passes unchanged too. |
| R6 | `tests/engine/test_racing.py`: Route 1 by two threads and three events, Route 2 by injection after the copy with a frozen stat; each asserts the write reaches the source, and neither sleeps. `verify/racing_gate.py base` shows them red on the base engine and `suite` green on the tree; both write their result under `spec/racing-write-marked-clean/verify/`. |
| R7 | A marked handle's change takes the mutex twice and runs no SQL. A test counts statements with `sqlite3`'s trace callback. Read as "as today" (deviation row 5). |
| R8 | One phase, one commit: the `AGENTS.md` row, § 6, § 10, the high-blast-radius rows, the docstrings and the counts change with the code. |
| R9 | `verify/racing_gate.py suite`: `tools/lint.sh` exits 0, 0 failed, 0 errors, passed above the base's count. The CI half is read at publish. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against `AGENTS.md` § Invariants before the reads and again after this design was drafted and
reviewed. The change touches `engine/overlay.py` (`_commit_hop`, `write`, `_mark_handle_dirty`,
`truncate`) and `engine/transfer.py` (`copy`): all on the high-blast-radius list and in the coupled
core. Research was skipped by the foreman's ruling; the adversarial review ran (§ 4).

- **§ 1, metadata before acknowledgment** — kept. `_mark_handle_dirty` still commits the dirty mark
  before `os.pwrite` or `os.ftruncate` runs, and both truncate paths still mark first. The rejected
  alternative that would bend it (admit after the bytes land, mark late) is in row 4.
- **§ 1, intent → act → confirm** — untouched. `before_read` runs after `Journal.begin` has committed
  and before the act; the confirming transaction is the same.
- **§ 6, "after a copy, `changed` detection re-arms rather than settles"** — **overturned**, row 1.
  The rest of § 6 (boundary policy, queue order, clocks) is untouched: `rearm` and `settle` are called
  as before.
- **§ 7, eviction** — untouched code. Its premise, a truthful `state` column, is what this delivers.
- **§ 10, lock order** — kept: path locks → source mutex → `_handles_mutex` → SQLite write lock →
  purge-watch mutex. The mutex is never held across I/O, SQL or a wait, never taken inside a
  transaction, and nothing is acquired while it is held. It gains holders (row 2).
- **§ 10, "no unlocked counters"** — `Handle.changing` and `_hops` are written only under the mutex.
  `Handle.written` stays as it is (the stress seed owns it).
- **§ 10, "`_commit_hop` is called under a lock the caller already holds"** — kept; it takes no path
  lock.
- **§ 11, synchronous API, `OSError` failures** — unchanged. A failed `os.pwrite` still raises its
  `OSError`, after `_change` has taken the change out of flight.
- **State store (§ 2, § 3)** — no SQL added or changed, `schema.sql` and `db.py` untouched,
  `PRAGMA user_version` stays 2.
- **High-blast-radius list** — it only widens: `_change` and `_meet` join the `write` /
  `_mark_handle_dirty` / `truncate` row in both tables.

### Deviation justifications

| # | Deviation | Why needed | Simpler alternative rejected because |
|---|-----------|-----------|--------------------------------------|
| 1 | **§ 6's bullet is overturned.** It says the holder goes clean "only when the stat matches what was copied" and that the reset must come *before* the stat. After this change the condition is the hop's own record, with the stat as a veto, and the reset comes *after* the stat, in the hold that reads the record. | R4 forbids the stat as evidence, and a reset before the stat leaves a writer that read the flag earlier unseen (Route 1). GOAL Q5 puts the overturn here. | Keeping the bullet and adding a sentence: the bullet would still name the stat as the condition and the old order as load-bearing, and a reviewer grading against it would fail correct code. |
| 2 | **`_handles_mutex` gains holders**: `write` and `truncate` through a handle (twice each, alone), `_mark_handle_dirty` (under the path lock, after its `mark_dirty` has committed), the hop's opening (from inside `TransferPool.copy`) and closing, and the handle-less `truncate` (under the path and source locks). § 10 says so in the same commit. | The flag's read, the admission and the hop's decision must be ordered against each other, on the free-threaded build too. | A lock per handle or per copy: a new lock in § 10's order, and the committer would hold several at once. Unlocked counters: § 10 forbids them, and `+= 1` is not atomic on either build. |
| 3 | **`TransferPool.copy` takes a callback**, `before_read`. A high-blast-radius signature gains a parameter. | The GOAL's interval starts when the copy starts reading, and R5 requires clean for "a write that returned before the copy started". | Opening the record in `_commit_hop` before calling `copy`: the interval would start before the intent's insert, and a write returning in that gap would keep the holder dirty although the copy carried it. A test that wraps `copy` and writes first sees the difference. |
| 4 | **Every change through a handle takes the engine-wide mutex twice**, where today a marked handle takes no lock. | Admission before the bytes land and retirement after are the two facts the hop needs. | One hold after the write, with a late dirty mark when the flag was reset meanwhile: the bytes would land before the mark, against § 1. No lock: Route 1. The holds are a few statements each; no timing budget is set (GOAL non-goal), and contention on the free-threaded build is the stress seed's to measure (TECH, ledger). |
| 5 | **R7 is read as "as today".** R7's condition is "no commit of it has started since that handle last wrote". The design, like the base, resets the flag when a hop records its outcome, so a handle whose last write began before that moment (inside the hop, or held across it) pays one dirty mark on its next write, although no commit has started since. Measured by the review: 6 statements on the prototype, 7 on the base, in the same position. | The unconditional reset is what bounds `commit_upstream` under a writer that never pauses: the writer's next change waits on the path lock, so the next hop meets nothing. | (a) Not resetting the handles that wrote inside the hop: `commit_upstream`, and through it `fsync` under `commit`, `flush` and the eviction manager, would copy the file again for as long as writes keep landing. (b) Keeping the reset and the wait, and letting the re-mark skip its SQL when the hop left the holder dirty and queued: it needs a second in-memory copy of what the `state` column says, kept per handle or per copy, which is one more thing that can be stale on a path where stale means silent loss; and it still runs SQL when another handle or a handle-less `truncate` re-dirtied the copy after a clean hop. **This is a reading of an R-ID and goes to the PLAN/TECH sign-off as a GOAL clarification**; see § 5. |
| 6 | **A change the engine has admitted is treated as landing in the interval**, whether or not its bytes had already landed when the copy started reading, and whether or not its call then fails. The review measured the second: a write that raises `ENOSPC` inside the hop leaves the holder dirty, where the base records clean. | The engine cannot tell "landed, not yet returned" from "not yet landed", and a failed `os.pwrite` may have written part of its range. | None that is truthful. Dirty with the row queued is truthful by the GOAL's definition, and the next commit settles it. R5 promises clean for a write that *returned* before the copy started, which holds. |
| 7 | **`commit_upstream` copies the file again for as long as one admitted change is in flight.** A hop that opens met re-arms, and the loop takes the same hop again at once. The review measured it: a write held inside `os.pwrite` across 25 copies gave 26 copies, with the path lock held throughout; the base copies once and records the lost write clean. | A hop that cannot know where an in-flight change will land must not go clean, and `commit_upstream` promises its callers the source holds the bytes when it returns. | Waiting for the change to return before copying again (a condition variable on the mutex, notified at retirement, with a finite timeout): a second synchronization object in the coupled core, for a loop that already ends when one system call does and never restarts, since the first hop's reset stops every later change at the path lock. Returning with the holder still dirty: it changes what `fsync` under `commit`, `flush` and the eviction manager are promised. The cost is recorded against the pump seed (TECH, ledger). |

Each row was read against the R-IDs it touches. Rows 1 to 4, 6 and 7 bend none: row 6 keeps R5's
promise for a write that returned, and row 7 concerns a commit that does meet a change, which R5 does
not govern. Row 5 is the one place the design does not meet an R-ID to the letter, and it is raised,
not settled, here.

## 4. Rabbit holes (resolved)

- **Does the base fail R2?** → Yes. A `truncate` through a handle held before its `os.ftruncate`
  while a commit runs ends clean with the upstream at the old length, on both commit routes, shorter
  and longer (prototype, below).
- **Can Route 1 be a deterministic test of the engine's own `write`?** → Yes, with two threads and
  three events: the writer is held inside `os.pwrite`, the commit runs, the holder tier's first `stat`
  after the copy lets the writer go and waits for it to return. No sleep; every wait is bounded and
  asserted. A single-thread version (the commit nested inside the held `os.pwrite`) cannot be used
  with `commit_upstream`: the writer never returns while the commit runs, so the loop would not end.
- **Does anything in the suite depend on the flag's old timing?** → No. The prototype passes the
  whole unit suite.
- **Prototype.** First a probe in a scratch copy of `bf438b4`, then a detached worktree of
  `origin/main` at `bf438b4` (`.worktrees/racing-write-marked-clean-prototype`, today's names), which
  carries the design above (80 lines added and 31 removed across `overlay.py` and `transfer.py`), the 31-case
  test module, the record edits and this gate. All four gate modes are green on it. On the base engine
  the 18 cases of the nine fix tests fail, each with `assert 'clean' == 'dirty'`, and the 9 cases of
  the six pins pass; the 4 tests of the record itself are not run there. On the prototype all 31 pass
  and the unit suite reports 762 passed (731 + 31), Python 3.15.2 with the GIL, macOS. The
  free-threaded build was not run here; CI runs it.
- **Adversarial design review** (one pass, a fresh subagent given the GOAL, this design and the
  prototype; it ran probes against the prototype and the base and wrote no file here). It found no
  interleaving inside the contract that records clean with bytes missing upstream, no deadlock, and no
  SQL or path-lock wait added to a write on a dirty handle. What it found, and what changed:
  1. *The Route 1 test passed on an engine with Route 1's window reopened* (the flag read before the
     mutex is taken), because it parks the writer inside `os.pwrite`, after admission. → New test
     `test_write_held_before_admission_across_a_whole_commit` parks the writer at its first take of
     the mutex.
  2. *A record opened after the copy's read survived every test*, because every injection fired after
     `copy` returned. → `test_same_size_write_while_the_copy_is_in_flight` injects after the
     upstream's `put` and before `copy` returns.
  3. *Four more mutants survived*: `_meet` at retirement, `pop(key, False)`, the first write admitted
     after the path lock is released, `_meet` missing from `_mark_handle_dirty`. → One test each:
     `test_write_admitted_inside_the_hop_and_still_in_flight_at_its_end`,
     `test_hop_whose_copy_opens_no_record_stays_dirty`,
     `test_first_write_is_admitted_under_the_path_lock`,
     `test_second_handles_first_write_inside_the_hop`; and `test_failed_copy_leaves_no_record` for the
     record a failed copy would leak.
  4. *Three mutants were caught only by a hang* (`commit_upstream` looping; the project has no test
     timeout). → `make_engine` caps the copies a test may make, the failed-write pin commits one row,
     and `test_mid_stack_hop_meeting_no_change_settles` covers the stage-to-stage branch in one hop.
  5. *`commit_upstream` re-copies while a change is in flight.* → Deviation row 7, § 5, and a line for
     the pump seed.
  6. *The R7 clarification first proposed here did not describe the design* (a write held across the
     hop returns after it, and its next write still marks), and an alternative that meets R7's letter
     without touching the loop exists. → Row 5 now names that alternative and why it is not taken, and
     § 5 words the clarification as "began".
  7. *A handle on a stage whose file a faster tier's commit replaces* loses its writes, on the base and
     on the prototype alike. → Stated as the premise in § 2, filed as
     `issues/stage-handle-outlives-its-file.md` with a `ROADMAP.md` entry.
  8. *A failed write inside the hop keeps the holder dirty.* → Row 6.
  9. *An opening overwrote an existing record.* → The opening now keeps it (§ 2).
  10. *The cut-from-another-thread test did not show the cut waiting.* → It now waits until the
      lock registry counts a second taker of the path before the commit goes on.

  After the fold, fifteen mutants of the design were planted in the prototype one at a time (the
  twelve the review named, and the reset, the fast-path `_meet` and the `met or` dropped): each is
  killed by a named test of the new module or by an R5 pin, and none by a hang.

## 5. Risks & open questions

- **Question for the sign-off (row 5): R7's condition.** As worded, R7 promises no SQL to a handle
  whose copy is still dirty and whose last write met a commit that is still the latest one. The base
  does not behave that way either (7 statements there, 6 here), and R7 says "as today". The plan
  holds the "as today" reading and proposes the clarification "…and no hop of it has recorded its
  outcome since that handle's last write began". If the maintainer wants the letter instead, row 5's
  alternative (b) is the way: it adds per-handle state and a test, not a new design, and it leaves one
  corner (a third party re-dirties the copy after a clean hop) where the letter still is not met.
- **The base commit.** The GOAL calls it the commit the branch was rebased onto before the plan
  step. The rebase onto the rename happens before the build instead (the foreman's ruling), so the
  gate takes the base as `git merge-base HEAD origin/main` at build time.
- **Free-threaded build.** Every shared read and write in the design is under one mutex, so the
  argument in § 2 does not depend on the GIL. It is still an argument: this cycle's tests are
  deterministic and single-interleaving, and the stress suite is a non-goal.
- **A change that is slow to return** keeps `commit_upstream` copying (row 7). For a healthy tier the
  bound is one system call; a tier that hangs a write will usually hang the copy's read of the same
  file too. A change that never returns keeps every hop of its copy re-armed; today the same hang goes
  unseen and the copy can be recorded clean under it. `_change` retires in a `finally`, so an
  `os.pwrite` that raises does not leak.
- **A handle on a stage whose file is replaced** (the premise in § 2). Pre-existing, reproduced, not
  widened, and outside this GOAL: no commit is in flight when the write is lost. Its seed is
  `issues/stage-handle-outlives-its-file.md`. Whether `AGENTS.md` § *Known open violations* gains a
  row for it is the foreman's call; this cycle's phase does not add one.
- **`release` with a change in flight on the same handle** closes the fd under the writer. That is a
  caller's error today and stays outside the contract; the kernel does not release a file with a
  request in flight.
- **Another path.** A handle opened, or a slow-path mark made, under another path after an ancestor's
  rename does not wait on the commit's path lock. That is the GOAL's non-goal, and the design neither
  repairs nor widens it: a hop with no record reads as met.

## 6. Verification strategy

Engine-first, against tmpdir tiers with no mount. One gate script,
`spec/racing-write-marked-clean/verify/racing_gate.py`, run from the cycle worktree's root:

```
uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/verify/racing_gate.py MODE [MODE ...]
```

| Mode | Shows |
|---|---|
| `base` | Adds a detached worktree of the base commit, runs this tree's `tests/engine/test_racing.py` against the base's package, and requires each fix test to **fail** there for the named cause and each pin to pass (the four tests of the record itself are not run there: the base has no record); runs the base's own unit suite for its passed count; closes the worktree with a plain `git worktree remove`. Writes `verify/base.txt`. |
| `suite` | `tools/lint.sh` exits 0; `uv run pytest -n auto -m unit` has 0 failed, 0 errors, passed above the base's count; every test in the new module and the two R5 tests in `test_writeback.py` pass; `test_mid_copy_write_rearms_instead_of_settling` is the base's, byte for byte. Writes `verify/final.txt`. |
| `records` | The `AGENTS.md` row is gone; § 6 and the `_commit_hop` docstring no longer give the stat as the condition; § 10 names the mutex's new holders; the high-blast-radius rows name `_change`; the counts `AGENTS.md` states equal the count `suite` measured; `schema.sql` and `db.py` are the base's. |
| `ledger` | Every deferral this plan names has its line in the seed that owns it (`freethreaded-concurrency-stress`, `pump-liveness-and-loss-windows`), the seed the review's finding filed exists, and `ROADMAP.md` lists each. |

The test file is read where it is, by absolute path, with the base worktree as the working directory;
it is not copied in. A copy would leave an untracked file that only a delete or a forced removal
clears, and both are refused here. The gate checks which package each run imported.

Tests the build writes into `tests/engine/test_racing.py` (names fixed here; `[route]` is
`commit_row` and `commit_upstream`):

| Test | R-ID | On the base |
|---|---|---|
| `test_write_held_past_the_commit_reaches_the_source[route]` (Route 1) | R1, R6 | fails |
| `test_same_size_write_behind_an_unchanged_stat_reaches_the_source[route]` (Route 2) | R1, R4, R6 | fails |
| `test_same_size_write_while_the_copy_is_in_flight[route]` | R1, R4 | fails |
| `test_truncate_held_past_the_commit_reaches_the_source[length-route]`, shorter and longer | R2 | fails |
| `test_truncate_without_a_handle_inside_the_hop[route]` | R3 | fails |
| `test_write_then_release_inside_the_hop[route]` | R1 | fails |
| `test_second_handles_first_write_inside_the_hop[route]` | R1 | fails |
| `test_write_admitted_inside_the_hop_and_still_in_flight_at_its_end` | R1 | fails |
| `test_write_inside_a_mid_stack_hop` (three tiers) | R1 | fails |
| `test_truncate_without_a_handle_from_another_thread[route]` | R3 | passes |
| `test_commit_meeting_no_change_copies_once_and_settles[route]` | R5 | passes |
| `test_write_returned_before_the_copy_reads_is_carried[route]` | R5 | passes |
| `test_mid_stack_hop_meeting_no_change_settles` (three tiers) | R5 | passes |
| `test_write_on_a_dirty_handle_executes_no_sql` | R7 | passes |
| `test_failed_write_leaves_no_change_in_flight` | R5 | passes |
| `test_write_held_before_admission_across_a_whole_commit` | R1 | not run |
| `test_hop_whose_copy_opens_no_record_stays_dirty` | R4 | not run |
| `test_first_write_is_admitted_under_the_path_lock` | R1 | not run |
| `test_failed_copy_leaves_no_record` | R5 | not run |

Thirty-one cases. The last four drive the record itself (the mutex, `before_read`, `_hops`) and reach
into the engine's private state to do it; each is the only test that kills one mutant of the design.

Every test that names R1 to R4 ends by asserting the residency is truthful and the change reaches the
source as the GOAL defines it: handles released, one `flush()`, the source's bytes, no `data` row,
and a read through the engine after `evict_copy` has dropped the staged copy. `make_engine` caps the
copies a test may make, so a commit that does not end fails instead of hanging.
A draft of the module, as the prototype ran it with the import renamed, is committed beside the gate
as `verify/test_racing.py.draft`.
