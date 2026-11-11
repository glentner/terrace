# PLAN — Removing nested directories after moving an entry out drains the queue instead of recursing

> **Status:** Draft for review · **Last updated:** 2026-11-10
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md) (amended in `442dc54`:
> R9, R10, appetite `big`); the phased executable roadmap is [`TECH.md`](TECH.md); backing detail is in
> [`research/`](research/00-digest.md). Line anchors are at `980f998` (the base) unless a sentence names
> another commit.

## 1. Summary

At the base, a replay keeps no record of which rows it is already inside. So two rows whose replays reach
each other re-enter each other until Python's recursion limit:

- a nested directory's `rmdir` and its parent's;
- two sibling directories' `rmdir` rows;
- two directory renames whose targets lie inside each other's objects.

Separately, a rename reads the rows that must move before it only once, so a crossing whose first move
leaves the target inside still raises `EngineError` (R10).

The fix makes `_replay_through` a loop over an explicit stack of rows, never a recursion:

- A row runs the queue prefix through the rows it needs, then reads its needs again, until none is
  queued.
- A row on the stack never runs again inside its own replay.
- A row reached inside the replay of a row it needs waits, queued, for a later visit.
- The row a replay starts with sits alone at the bottom of the stack and never waits, so one `flush`,
  `drain` or poll still runs every row it reads (Q4).

The functions that change:

- in `overlay.py`, five (`_execute_namespace`, `_execute_namespace_locked`, `_clear_inner_target`,
  `_replay_through`, `_holder_rows`), plus a small `_Frame` record;
- `Catalog.held` is new, one indexed read in place of a lookup per queued row;
- `Journal.queued_marks` is new: each queued row's identity, read without decoding, so a replay finds the rows a
  racing removal queued without re-reading the queue.

The engine gains no state and no lock holder. Five hundred chains side by side drain in about a second.

## 2. Design

### Root cause, with evidence

At `980f998` there are two pre-replays:

- an `rmdir` row's (`src/sosfs/engine/overlay.py:1110-1118`);
- a rename's, through `_clear_inner_target`, which call `_replay_through(rows, skip={row.id})`
  (`:1116`, `:1295`).

`_replay_through` walks the queue in order, skips only `skip` (`:1312`), and runs every other row with
no skip of its own (`:1314`). So a prefix row whose own pre-replay reaches a row already running runs it
again from the top.

For the GOAL's shape at depth 2 the queue is `[2 rmdir /d1/d2, 3 rmdir /d1, 4 unlink /m.dat]`, the file
still held at `/d1/d2/m.dat`. This was measured at `980f998` by the planner's reproduction, and R4's
test asserts these rows. Row 2 replays toward row 4. The prefix reaches row 3, whose replay (skipping
only 3) reaches row 2 again. No row acts and no intent opens before the raise.

The same cause reaches two more shapes, each `RecursionError` at `980f998`:

- **Sibling chains** at depth 1 (from two chains up).
- **Crossing directory renames.** 38 of 8,000 sequences in a directory-heavy random walk.

A second cause sits in the same function. `_clear_inner_target` reads the rows between once (`:1286-1294`),
so when one move leaves the target inside, the newly-between rows never run and it raises (`:1299`).
Reproduced at `980f998`:

- R10's listed renames: the first flush raises `EngineError: Cannot move /c/a into its own subtree`,
  and the second drains.
- A user `rename` (the `rename` case of `CROSSED_NAMES`) raises the same.

[`research/01-root-cause.md`](research/01-root-cause.md) has the traces. The v2 design (stack passed down
the recursion) failed at about 330–400 sibling chains and kept the one-shot read; GOAL Q5 and Q6 rule the
response.

### The design

All of the following are in `src/sosfs/engine/overlay.py` unless noted; every function named is already on
both high-blast-radius tables.

1. **`_execute_namespace(row)`** takes the source lock and calls `_replay_through(set(), row)`. Its
   signature is the base's, so `flush`, `commit_row` and `drain` are unchanged.
2. **`_replay_through(rows, row=None)` is the worklist.** It keeps:
   - a list of `_Frame`s (a row, the rows its prefix has yet to reach, its cursor, the needs it read
     last);
   - the stack (the frames' rows, each known by its mark: id, entry, op and first-dirty time, never its id
     alone, since ids are reused and a racing removal may queue its row under the id of one it cancelled);
   - `done` (rows run or found gone);
   - the memo of waiting rows;
   - the queue as read.

   With `row` it starts with that row alone on the stack. With `rows` (`_vacate`, `_purge_source`, whose
   calls are unchanged) it starts with a root frame that only scans.

   **A scanning frame** walks the queue in order:
   - it skips data rows and rows on the stack;
   - it discards each row it reaches from its remaining set;
   - it pushes the first that is neither done nor memoized as waiting.

   **A row frame**:
   - **reads:** whether its row is still queued, then its entry, then its needs. An `rmdir` needs
     `_holder_rows` below its directory's object. A rename whose target lies inside its own object needs
     `_clear_inner_target`.
   - **waits** (pops, row stays queued, memo noted) if its needs meet the stack, or if a pass through them
     freed none and it is not at the bottom.
   - **at the bottom with no progress:** an `rmdir` acts and a rename raises `EngineError`. Queues the engine
     writes never get there (every need is held strictly inside the needing row's place); the branch is a
     guard, and the gate lists its mutants as unreached (§ 4).
   - **with needs left:** it restarts its prefix.
   - **otherwise:** it acts through `_execute_namespace_locked(row, entry)` and pops.
3. **`_execute_namespace_locked(row, entry)`** is the act alone: the base's body from `entry is None` on,
   unchanged.
4. **`_clear_inner_target(entry, queued)`** returns the set of rows between instead of replaying them. It
   is empty when the target lies outside, and raises, as at the base, when the target lies inside with no
   row between. It reads candidates through `Catalog.held` and the replay's queue.
5. **`_holder_rows(…, queued=None)`** reads the holders' ids with one `Catalog.held` call and filters the
   queue it is handed, or reads the queue as before when not handed one.
6. **`Catalog.held(source_path, below=False)`** (`src/sosfs/state/catalog.py`) returns the ids of the
   entries, tombstones included, whose committed source path is the place, or lies under it. It is one
   indexed range read (`entry_source_path`), in the prefix-range idiom `Catalog.rename` uses.
7. **`Journal.queued_marks(to_tier=None)`** (`src/sosfs/state/journal.py`, after `pending`, so no cite into the
   class moves) returns the `(id, entry, op, first_dirty)` of every queued row in one read that decodes none.
   The replay reads the queue once. Before each needs read, and when a scan reaches the end, it reads the
   marks and adds the rows whose marks it has not seen, wherever they sort. A user removal racing the replay
   (it takes no source lock) queues its row under a reused id, or under a clock that stepped back, and is
   still found.
8. **The memo.** A row that waited is not read again while no row has acted since and a row it waited on
   is still on the stack. In that state its needs read would return the same result. This is what keeps
   500 two-deep chains at about a second rather than 26 s; it changes no outcome.

**Why one pass still drains (Q4).** [`research/02-worklist-and-q4.md`](research/02-worklist-and-q4.md)
has the full argument.

- A row's needs never include a row of its own entry.
- At the bottom the stack is that row alone, so it never waits on the stack.
- At the bottom every pass through its needs makes progress, while the needs relation is acyclic, which
  it is at any moment because each need is held strictly inside the needing row's place. A need the
  bottom row pushes directly cannot wait on it. One pushed deeper sits later in queue order and is
  reached again by the bottom row's own scan once the row it waited on has popped.
- `flush`, `drain` and a poll run every namespace row they read at the bottom of its own replay, so each
  call returns with that row acted or gone, or raises. A row that waited inside an earlier row's replay
  lies after that row in the snapshot, and the driver reaches it.
- No loop is added. `drain`'s `complete=True` is true of these shapes after one pass.

**Why a stuck queue drains (R4).** Queueing is unchanged. A state directory written by `980f998`, one
whose replay already raised included, holds rows the fixed engine reads like any other (measured, § 4).

**R9 at scale.** The replay never recurses; its depth is bounded by the number of queued rows. Each needs
read is one indexed read plus a pass over the replay's in-memory queue.

**R10.** The fixed point reads a rename's places between again after each pass, so the rows a move made
newly between run too. A rename reached inside the replay of the rename it needs waits, as an `rmdir`
does.

**Unchanged:** `flush`'s, `drain`'s, `commit_row`'s, `poll`'s, `_vacate`'s and `_purge_source`'s code;
queueing; `schema.sql` and `db.py`. Docstrings change as listed below.

**The parked candidate** (`refs/park/rename-over-tombstoned-child-prototype`, the recursion half) is
rejected:

- It makes an `rmdir` row return when its holders meet a `_running` set on the engine, but keeps the
  recursion and the base's one-row skip.
- It passes the shaper's map, but recurses on two sibling chains, recurses on crossings, and keeps
  R10's one-shot read.
- It adds engine state the source lock must guard (§ 10).

Its wait rule survives here, on the explicit stack.

### Records (same commit as the code, R7)

- **`AGENTS.md` § 5's `_replay_through` bullet:** the worklist, the needs, the wait, the bottom, the fixed
  point, rows queued meanwhile, and why one pass leaves none.
- **The high-blast-radius tables:** `held` joins `holder` in both catalog cells, and `queued_marks` joins
  the journal's queue-row identity cells. § 10 is unchanged: no lock gains or loses a holder, and there is no
  new state.
- **Docstrings:** `_replay_through`, `_execute_namespace`, `_execute_namespace_locked`,
  `_clear_inner_target`, `_holder_rows`, `_vacate`, `flush`, `drain`, `Catalog.held`, `Journal.queued_marks`.
  `drain`'s keeps its line count, so `issues/pump-liveness-and-loss-windows.md`'s `drain.py:98-105`
  holds. `issues/*`'s whole-function cite `drain.py:74-109` holds as a span.
- **`tests/engine/test_replay.py`'s module docstring:** one-pass draining of nested, sibling and
  five-hundred-chain removals, a removal racing the replay (under a reused id too), a directory a rename
  replaces; crossings and the user calls that meet them.
- **The counts:**
  - 548 → 708 unit tests (`AGENTS.md:70`, `:111`, `:693`, `:734`);
  - 558 → 718 in the repository map (`:111`);
  - 210 → 370 engine tests (`:136`);
  - the serial time at `:70`/`:734`, measured by the build on a quiet host.
- **P2:** `issues/source-only-writeback-scheduler.md` (Q1) and its `ROADMAP.md` entry.

### Requirement → design map

| R-ID | Design element(s) that satisfy it | Tests (cases) |
|------|-----------------------------------|---------------|
| R1 | The stack skip, the wait, the bottom never waiting | `test_one_pass_drains_nested_directories_removed_before_their_moved_out_entries` (64) |
| R2 | `commit_row` → `_execute_namespace`: a poll's rows start at the bottom | `test_one_scheduler_poll_runs_every_namespace_row_of_nested_removals` (6), `test_the_writeback_pump_outlives_nested_removals` (1) |
| R3 | `_vacate`'s and `_purge_source`'s root frame pushes each needed row at the bottom | `test_a_new_name_over_nested_removals` (12), `test_a_rename_over_a_directory_whose_object_holds_nested_removals` (2) |
| R4 | Queueing unchanged; the design acts at replay time | `test_a_queue_left_by_nested_removals_drains_on_the_first_pass_after_a_restart` (4) |
| R5 | Nothing waits at the bottom; rows only lose re-entry | `test_shapes_beside_nested_removals_still_drain` (36), `test_a_new_name_over_a_kept_moved_entry_still_raises_eexist` (1), `test_a_child_the_catalog_never_saw_still_stops_the_rmdir_row_with_enotempty` (2), `test_a_removal_queued_during_a_replay_runs_before_the_directory_it_holds` (2: plain, and under a reused id); existing tests unchanged |
| R6 | 160 deterministic `unit` cases ending in `assert_converged`; R1–R4, R9 and R10 tests compare with an `os` oracle; fix cases red on the baseline; every case named by a mutant (§ 4) | all |
| R7 | Records above | — |
| R8 | The profile's `verify` (708 passed > 548), `test_model.py` seeds 1–20, the four CI jobs from the run record | gate `suite`, `seeds`, `ci` |
| R9 | The explicit stack (no depth bound), `Catalog.held`, the memo | `test_one_pass_drains_hundreds_of_chains_removed_before_their_moved_out_entries` (8: 500 chains; flush/drain × 1/3 tiers × depth 1/2 × either order, pairwise) |
| R10 | The fixed point over `_clear_inner_target`; a nested rename waits | `test_one_pass_drains_renames_crossing_each_others_subtrees` (20: four crossings, one where a nested rename freed nothing on its first pass and must wait, × flush/drain on 1 and 3 tiers, poll on 3), `test_a_new_name_over_renames_crossing_each_others_subtrees` (2: a user rename and a mkdir) |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (from the GOAL), after the v2 stop, and again against this design.

- **§ 1, intent → act → confirm; confirm before purge.** A waiting row pops before `journal.begin`, so it
  opens no intent and settles nothing. Every act keeps its bracket and its settle-then-confirm order,
  unchanged in `_execute_namespace_locked`. The tests assert `pending() == []` after each consumer and
  after the injected raise (R4).
- **§ 2, transactions.** `Catalog.held` and `Journal.queued_marks` are single reads; they open no
  transaction.
- **§ 3, SQL.** Both new queries are t-strings through `render`, with values bound as parameters. The
  range bound `prefix[:-1] + "0"` is the idiom `Catalog.rename` uses. Static DDL is unchanged; the read
  uses the existing `entry_source_path` index.
- **§ 5, the prefix and the escape hatch.** A row still runs the queue prefix in order through the rows
  that free its place. A parent's `mkdir` or a child's move-out still runs before the row that needs it:
  a row that would run first but needs a row on the stack waits instead of acting early. The skipped
  rows are mid-replay above; re-running them was the defect. Settling stays `settle_current`. The bullet's
  text changes in the same diff.
- **§ 6, queue order.** `due`'s and `queued`'s order is unchanged. Rows found by mark join the replay's
  queue at its end, wherever they sort, and the scans that need them reach them there.
- **§ 8, drain.** `drain` reports `complete` after one pass, and by Q4 that pass ran every namespace row it
  read. `Mount.shutdown` is unchanged.
- **§ 10, the source mutex.** No lock gains or loses a holder, and nothing new is taken under it. Replay
  still takes no path lock. The engine gains no attribute: the stack, the memo and the queue are locals
  of one call, so threads never share them. The gate checks § 10's text and every class and module body
  against the base, and that the replay calls neither itself nor `_execute_namespace`.
- **§ 11, `OSError` only.** No mapped, probed, fuzzed or scaled shape lets `RecursionError` or
  `EngineError` escape on the prototype (§ 4). `EngineError` remains the designed refusal for a target
  still inside its object after a bottom-level pass freed nothing, which acyclicity rules out, and for
  one with no row between at all.
- **High-blast-radius.** The five overlay functions are on both tables. `Catalog.held` joins `holder`
  there, and `Journal.queued_marks` joins the queue-row identity functions, since it is how a replay tells
  a row from a newer one that took its id. Q3's sign-off covers the diff.
- **§ 5, row identity.** A replay keys every row it holds by its mark, never by id alone: `current`'s
  discipline, applied to the replay's own sets.
- **Known open violations.** None is fixed or worsened. The `rmdir`-racing-`mkdir` row (§ 5, 10) is
  threaded and out of scope.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Two state-layer reads are added: `Catalog.held` and `Journal.queued_marks`. | Without them a 500-chain flush reads the catalog once per queued row per row: about 625,000 lookups and 10 s. One indexed read per needs read brings it to about 0.6 s. Reading the queued rows' marks keeps a racing removal visible (as the base's re-read did), under a reused id or a clock that stepped back, without decoding the queue each time. | An engine-side cache of entries between acts duplicates the catalog's prefix rewrite and purge rules, and a wrong cache is a wrong replay. A full queue re-read per needs read restores the quadratic decode (about 3 s at 500). Reading only rows after the last one by `(last_write, id)` (the first draft) misses a racing row that reused an id or sorts earlier: review 2's F1. |
| The memo of waiting rows. | It is pure performance: a waiting row is skipped only while its needs read would return the same result. | Without it, 500 two-deep chains take about 26 s per flush (measured). |
| A nested row with no progress waits rather than raising. | Only the bottom row may conclude "no way forward"; a nested row's blockage can be an artifact of the stack above it. | Raising there would turn a drainable queue into an `EngineError`, the R10 failure. |
| R4's "a replay already raised" is emulated in the unit test by a one-shot `RecursionError` from `_holder_rows` before any act. | The fixed code cannot recurse. Planning drained real `980f998`-written stuck directories (§ 4). | A binary state fixture ties the suite to today's schema. |
| R2's thread test sets the stage boundary to passthrough. | R2 grades the pump "when every queued row is due". | Patching `deadline` tests less of the scheduler. |
| R9's 500-chain test runs 8 of the 16 combinations of call × tiers × depth × order, pairwise. | Each run takes about 2 s; 16 would add about 30 s serial to a suite stated at about 30 s. Every pair of factor values appears at least once. | Fewer chains would not show R9's "at least 500". |

## 4. Rabbit holes (resolved)

[`research/03-evidence.md`](research/03-evidence.md) has the commands and the full tables; the digest
[`research/00-digest.md`](research/00-digest.md) summarizes them. Every run below is on the final planning
prototype unless it says otherwise.

- **Differential random walks against the base,** with one consumer call each. There were 57,500 runs:
  - the planner's default profile: 4 × 8,000 runs under flush, drain, one poll, and restart-then-flush;
  - a directory-heavy profile: 8,000 runs;
  - review 1's `chain` profile: 17,500 runs;
  - its `cross` profile, crossings mixed with removals: 10,000 runs.

  Every base `RecursionError` passes: 631 at the consumer and 265 in a user op. There are 0 regressions,
  and no run leaves a row queued after its one call or a drain incomplete. The only failures left are the
  parked `IntegrityError`: 12, 8, 35 and 45 runs in the four profiles, which the base raises too.
- **The shape map (R5's oracle):** 843 `RecursionError` → pass, 5,658 pass → pass, the 429 `KeyError` and
  110 `EEXIST` unchanged. 0 violations.
- **R4 on real state:** 47 stuck state directories written by `980f998`, every one drained in one
  prototype flush and in one drain, with no intent left, the source equal to its oracle, and convergence:
  - the GOAL's shape, after its flush raised;
  - 46 left by review 1's walks, where the base raised in user ops and in flushes.
- **Scale.** One tier unless noted. Each run: one consumer call, then the source checked against the
  oracle and `assert_converged`. Peak memory was under 70 MB throughout.

  | Shape | Call | Replay | User-op setup |
  |---|---|---|---|
  | 500 sibling chains | flush | 1.0 s | 1.1 s |
  | 1,000 sibling chains | flush | 3.9 s | 3.1 s |
  | 2,000 sibling chains | flush | 14.5 s | 10.4 s |
  | 5,000 sibling chains | flush | 89.5 s | 52.8 s |
  | 1,000 sibling chains, 3 tiers | poll | 3.8 s | 3.3 s |
  | 1,000 two-deep chains | drain | 10.3 s | 3.7 s |
  | 500 three-deep chains, 3 tiers | drain | 5.1 s | 1.8 s |
  | one 400-deep chain, files removed last | flush | 1.3 s | 23.6 s |
  | 200 crossings side by side (`listed`) | flush | 1.3 s | 2.0 s |
  | 200 crossings (`growing`) | flush, and poll on 3 tiers | 4.7 s | 7.2 s |
  | 200 crossings (`nested`), 3 tiers | drain | 2.1 s | 3.8 s |
  | 200 crossings (`inverted`) | flush | 2.8 s | 2.5 s |

  - The replay grows about quadratically with the number of chains, and never recurses.
  - Review 2 measured one deep chain in a harder order (files removed innermost first): 0.45 s at depth
    100, 2.4 s at 200, 7.0 s at 300, 15.6 s at 400. That is cubic; the macOS path limit binds first, at
    about depth 450.
  - Every probe drained in its one call and converged.
- **Hand probes outside the map:** 13 shapes × flush/drain/restart × 1/3 tiers. Base: 78 of 78
  `RecursionError`; parked candidate: 18; prototype: 78 pass.
- **The mutants** (`drain_gate.py mutants` on the P1 lab). Each is planted into a scratch copy of the lab's
  committed `src/`, and every case it names fails for its cause, matched in the JUnit `message`:

  | Mutant | Named cases killed, cause |
  |---|---|
  | baseline engine (fix removed, `regressions`) | 120 fix cases: `RecursionError`, or `EngineError: Cannot move` for R10's refusals. The 40 pin cases pass. |
  | M1 no row waits | 92, `Directory not empty` / `Cannot move` |
  | M2 the prefix skips no row on the stack (with the 20,000-push tripwire) | 134, `RecursionError` |
  | M3 the prefix skips only its own row (the parked candidate) | 13: the 500-chain cases and the `nested` crossings |
  | M4 a nested row is not put on the stack | 29, `RecursionError` |
  | M5 every row with a queued need waits, at the bottom too | 106, rows left queued |
  | M6 the wait inverted | 40: every control, `EEXIST`, the stray `[before]`, the racing removal |
  | M7 one prefix and no more | the 5 `growing` crossings, `Cannot move` |
  | M8 a rename needs nothing first | 22: every crossing and both crossed names |
  | M9 the queue is never read again once a replay began | the 2 racing-removal cases, `Directory not empty` |
  | M10 `Catalog.held`'s range ends one character early | 97, `Directory not empty` |
  | M11 a nested row whose pass freed nothing does not wait | the 5 `inverted` crossings, `Cannot move` |
  | M12 the replay's sets keyed by id alone | the reused-id racing removal, `Directory not empty` |

  Every one of the 160 new cases is named by at least one mutant.

  **Equivalent or unreached, argued in the gate and not run:**
  - The memo of waiting rows: pure performance. Review 2's 691-seed runs of its variants agreed with the
    design.
  - The bottom row left off its own stack: its prefix reaches it once, and the copy runs the same prefix
    and acts.
  - The bottom-stuck branch and its variants (`==` for `<=`; `len(frames) > 1` for the bottom test):
    unreachable from engine-written queues.
  - A rename that needs every row inside its object, and a prefix restarted at the cursor: each matched
    the design on 12,000 differential runs.
- **`test_model.py` seeds 1–20:** all twenty pass on the P2 lab, from committed state, without `CI`, and with
  no `.hypothesis/` before a run.

## 5. Risks & open questions

- **Measured figures move.** The counts (708, 370) assume no other cycle lands first; the gate rederives
  them from the run.
- **Real threads.** The stack, memo and queue are per call. A removal racing a replay is found through
  `Journal.queued_marks`, as the base found it through a full re-read. Removal racing a replayed `mkdir` stays
  the open violation it is (non-goal, `issues/freethreaded-concurrency-stress.md`).
- **Lock hold time at scale.** A flush of 2,000 chains holds the source lock for about 15 s, and one of 5,000 for about 90 s, during
  which `create`, `mkdir`, `rename` and source-served opens wait (§ 10 already states this of any long
  replay). The base never got that far: it raised.
- **Setup cost, not replay cost, now dominates at scale.** Each user `rename` vacates through
  `_holder_rows`, a full queue read: 1,000 chains take about 5 s of user ops. This is unchanged from the
  base and out of scope.
- **One deep chain is cubic.** A flush of one 400-deep chain, files removed innermost first, takes
  about 16 s (review 2). A flush would pass 10 minutes only near depth 1,350, past the macOS path limit
  (about 450). `drain` checks `cancel` between rows.
- **The parked `IntegrityError` shows more often.** Sequences that recursed at the base can now reach a
  later op, and some of those hit `Catalog.rename`'s `UNIQUE entry.path` (`rename-over-tombstoned-child`,
  a non-goal; it raises on the base for the same ops when they get that far). Every failure left in the
  prototype's walks is that one (§ 4).

## 6. Verification strategy

One gate script, `spec/nested-rmdir-replay-recursion/verify/drain_gate.py`, with these modes:

- **`regressions`:**
  - the thirteen new tests' names, case counts and shape: each ends in `assert_converged`, compares with
    the oracle, and makes one consumer call (no loop);
  - every case passes here;
  - fix cases fail on the baseline engine (`980f998`'s `src/`) for their cause (`RecursionError`, or
    `EngineError: Cannot move` for R10's refusals), and pin cases pass there;
  - existing tests, `test_model.py`, the conftests, `schema.sql` and `db.py` are unchanged;
  - only the named functions change, module and class bodies are the base's but for `_Frame`, no file
    outside `overlay.py`, `drain.py`, `catalog.py`, `journal.py` and `test_replay.py` changes under `src/` or
    `tests/`, and the replay calls neither itself nor `_execute_namespace`.
- **`mutants`:** the twelve mutants of § 4, each against its named cases and cause (matched in the JUnit
  failure's `message`, never the test's source); every new case is named. A mutant that lets a row run
  again inside its own replay carries a tripwire that raises `RecursionError` past 20,000 pushes in one
  replay (the tests' most is about 2,000), so it fails rather than hangs.
- **`records`:** § 5's bullet, ten docstrings, the test module docstring, the tables (`held` and
  `queued_marks` the only change), § 10 unchanged, the counts, no seed cites or spec ids in `src/`/`tests/`, and cites into
  changed files hold.
- **`seedfile`:** the Q1 seed, its reproduction and cites, its ROADMAP entry, and `writeback.py`
  unchanged.
- **`suite`, `seeds`, `ledger`** (on a clean tree), **`ci`, `prepush`:** as before.

The new tests (names fixed by the gate):

- `test_one_pass_drains_nested_directories_removed_before_their_moved_out_entries`
- `test_one_scheduler_poll_runs_every_namespace_row_of_nested_removals`
- `test_the_writeback_pump_outlives_nested_removals`
- `test_a_new_name_over_nested_removals`
- `test_a_queue_left_by_nested_removals_drains_on_the_first_pass_after_a_restart`
- `test_shapes_beside_nested_removals_still_drain`
- `test_a_new_name_over_a_kept_moved_entry_still_raises_eexist`
- `test_a_child_the_catalog_never_saw_still_stops_the_rmdir_row_with_enotempty`
- `test_one_pass_drains_hundreds_of_chains_removed_before_their_moved_out_entries`
- `test_one_pass_drains_renames_crossing_each_others_subtrees`
- `test_a_new_name_over_renames_crossing_each_others_subtrees`
- `test_a_removal_queued_during_a_replay_runs_before_the_directory_it_holds`
- `test_a_rename_over_a_directory_whose_object_holds_nested_removals`

A build that renames one, or reshapes a line a mutant anchors on, updates the gate in the same commit.

## 7. Adversarial review

Two passes, each by a fresh reviewer at effort xhigh. Each was given the GOAL, the PLAN as drafted, TECH, the
gate and the prototype diff, never the planner's conclusions.

**Review 1** (on v2, the stack passed down the recursion) stopped the plan before its commit. Its engine
findings became GOAL Q5 and Q6 (amended in `442dc54`).

1. **HIGH: crossings raise `EngineError` on the first pass.** Folded as R10: the fixed point over
   `_clear_inner_target`.
2. **HIGH: about 330–400 sibling chains recurse again.** Folded as R9: the explicit stack.
3. **HIGH: gate bugs.** Fixed in the dry runs.
4. **MEDIUM: a pre-existing `EngineError` on both engines.** This is R10's listed example, folded with
   finding 1.
5. **LOW: `confined()` and `ledger` gaps.** Folded: `confined()` compares module and class bodies, and
   `ledger` checks for a clean tree.

**Review 2** (on v3, the worklist):

- **F1, HIGH: rows keyed by id lose a racing removal that reuses an id.** The `after=` read also misses a
  row under a clock that stepped back. Both regress from the base.
  - Folded: every set the replay holds is keyed by the row's mark, and `Journal.queued_marks` finds new
    rows by mark wherever they sort.
  - The plain and reused-id cases of the racing-removal test pin it, and M12 (sets keyed by id) is killed
    by the reused-id case.
- **F2, HIGH: the nested no-progress wait was reachable but unpinned.** Folded: the `inverted` crossing
  (+5 cases) and M11, which it kills.
- **F3, HIGH: the gate was red on its own prototype.** Causes: a stale count check, two stale anchors, and
  a cite into `journal.py` that the added method shifted. Folded:
  - the check is structural;
  - the anchors are re-anchored;
  - `queued_marks` sits after `pending`, so `issues/invariant-audit-gaps.md`'s `journal.py:229-247` holds;
  - every phase was dry-run again (TECH, "Measured").
- **F4, MEDIUM: decisions that survive every test.** Folded as the gate's and § 4's list of equivalent or
  unreached mutants, each with its argument:
  - the memo variants (pure performance; review 2's 691-seed differential runs agreed);
  - the bottom-stuck branch (unreachable from engine-written queues; never reached in about 33,000 runs);
  - `==` against `<=`;
  - `len(frames) > 1` against the bottom test (the same unreachable branch).
  - Also: a rename that needs every row inside its object, and a restart at the cursor rather than 0. Each
    matched the design on 12,000 differential runs, and both appear in the list.
- **F5, MEDIUM: edits elsewhere under `src/` or `tests/` went unseen.** Folded: `confined()` asserts the
  branch's changed files under `src/` and `tests/` are only the five the plan names.
- **F6, LOW: `STUCK`'s cause regex matched the test's own source.** Folded: causes match only the JUnit
  failure's `message` attribute, and the recursion cause is anchored (`^RecursionError`).
- **F7, LOW: one deep chain is cubic.** Folded as a § 5 risk, with the figures.
- **F8, LOW: the `_purge_source` route was unpinned.** Folded:
  `test_a_rename_over_a_directory_whose_object_holds_nested_removals` (2 cases, red on the base).

Review 2 also confirmed:
- the fix/pin partition;
- the records regexes;
- the `Catalog.held` range for `/` and odd names;
- § 10 (no new shared state, no path lock under the source lock);
- 0 regressions in about 1,900 differential seed pairs;
- Q4 across about 12,000 runs: no row was left queued, and no needed row went unrun.

The second pass forced no redesign; its folds are local.

