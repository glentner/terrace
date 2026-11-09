# PLAN — A handle-less `truncate` records its cut on the file an ancestor's rename has moved

> **Status:** Draft for review · **Last updated:** 2026-11-08
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Q1 is answered **held off**. A user `rename` holds the source lock from its `_vacate` through the
transaction that moves the catalog, so its catalog move can no longer land while the handle-less
`truncate` (the cut) holds that lock, from its lookup under the lock to its recording. An ancestor's rename
either commits its catalog move first, and the cut's lookup under the lock raises `ENOENT` with nothing
marked or cut, or it waits until the cut's recording commits and then moves the file with the cut
recorded. The fix is one `with` in `rename`. R5's guard loses its `previous is not None` half, which the
hold makes unreachable. Nine deterministic two-thread tests, seven mutants and the records R7 lists, plus
four it doesn't (§ 2.3). Three phases, the previous cycle's shape.

## 2. Design

### 2.1 The engine (`src/sosfs/engine/overlay.py`)

`rename`, after `self._remove(target, replaced=True)` and unchanged above it:

```python
            with self._source_lock:
                self._vacate(new, mover=entry.id)
                now = self.clock()
                with self.db.transaction():
                    self.catalog.rename(old, new)
                    ...                                     # journal.queue, the tombstone: unchanged
            for name, tier in self.tiers.items():           # stage moves: unchanged, after the release
```

`_vacate` already takes the lock; the outer `with` re-enters it, so the hold now runs without a gap from
the vacate's collect-and-replay to the commit of the catalog move. Lock order is unchanged: path locks
(`lock(old, new)`), then the source lock, then the SQLite write lock (`BEGIN IMMEDIATE`). Inside the hold
there are only catalog and journal calls: no `locks.hold`, `_ensure`, `getattr`, `readdir` or
`_handles_mutex` (research 01 § 3). The stage moves stay after the release (§ 4, rabbit hole 3).

`truncate`'s recording drops half its guard:

```python
                        if serving != self.source.name:
                            self.registry.adjust_usage(serving, bytes=length - previous.size)
```

Nothing else in `truncate` changes but its docstring. The gate checks that its code equals the base's
with only `previous is not None` removed, so the property Geoffrey signed off on 2026-11-08 (GOAL
Constraints) is untouched: one lookup under the lock with the path, id and kind check, one decision, the
dirty mark before the `O_WRONLY | O_NOFOLLOW` open and cut, and meta, residency and counter in one
transaction.

Why the hold meets R4. `Catalog.rename` is the only statement that changes an `entry.path`, and
`rename` is its only caller (research 01 § 1). The gate checks both: no other function in `src/` has SQL
that sets `path`, `META_FIELDS` lets `update_meta` set neither `path` nor `parent`, and the one call sits in
the hold. `entry.path` is `UNIQUE`, so nothing
can put another row at the file's path while the file is there. A removal of the file needs its path
lock, which the cut holds, and a removal of an ancestor needs that ancestor to be empty. So from the
cut's lookup under the lock to its recording's commit, the source lock and the path lock between them
keep the entry at the path the lookup read. Every read and write the cut makes by that path reaches that
entry: `fastest_copy` in `_serve`, the promotion's `_lookup_again`, `transfer.copy`'s recording and its
`get_residency`, `mark_dirty`, `get_residency` for the counter, `update_meta` and `update_residency`.
That also holds for the identity gap's file (Non-goals): what the lookup returns is what is written.

Why R5 removes the guard. Under the hold, a stage residency of the file can be dropped only by `_remove`
(needs the file's path lock), `evict_copy` (needs it too), or a replay (drops source rows only), so
`previous` is never `None` for a stage `serving` (research 01 § 2). Today the guard is reachable, and only
through this race: with the rename's move landing before `get_residency`, `previous` reads `None` and the
guard skips the counter, which is why the seed's F6 stack showed no counter drift while F1's, paused after
`get_residency`, did (research 02 § 2). A pin would assert a state that no longer occurs, and a mutant
dropping the half guard is equivalent. Removing it means a future regression that reopens the race fails
loudly (`AttributeError`) instead of silently skipping the counter.

The docstrings, stated as the code delivers and no more (cycle 4's F1):

- `rename`: holds the source lock from `_vacate` through the catalog move, so the move never lands inside
  a handle-less `truncate` of a file below `old`; moves stage copies after releasing it.
- `truncate`: replaces the sentence that says the catalog writes "match no row" with: an ancestor's
  rename either commits first, and the call raises `ENOENT` before anything is marked or cut, or waits
  until the recording commits. It adds the one window it does not close: the rename moves stage copies
  after releasing the lock, so a cut at the path the file was just moved to can find no stage copy to
  open and raise `ENOENT` after its dirty mark (`issues/freethreaded-concurrency-stress.md`).
- The `_source_lock` comment in `__init__` names a user rename from its `_vacate` through its catalog move
  among its holders.

### 2.2 The tests (`tests/engine/test_replay.py`, appended after the last test)

Helpers: `Watched` stands in for the source lock and sets a thread's event when that thread finds the lock
held and must wait (through a `WaitSignal`, which also records it); `stop_at` wraps one engine, catalog, journal or tier method so the cut's thread stops
there once; `race` runs the rename and the cut on two daemon threads of their own; `assert_counted`
checks every stage counter against the sum and count of its residency rows, and that neither is
negative; `cut_returned` and `assert_recorded_or_refused` grade R1's two outcomes; `assert_counted_through`
re-checks the counters after a drain, after each stage copy's eviction, and after a read-only open and
release; `staged` builds the seed's F6 stack. `JOIN = 10.0` bounds every wait and join.

`race`, with the rename first (the "past its `_vacate`" case): the rename runs on its thread until its
`_vacate` returns and stops there, before its catalog move. The cut then runs until it reaches its stop
or has to wait for the source lock. The rename goes on until it finishes or has to wait for the lock;
then the cut goes on; both are joined. No step waits on a thread that is waiting on the other, whatever
the engine under test locks, so the same test runs to an outcome on the fixed engine, the baseline and
every mutant, never to a hang. With the cut first, the cut stops, the rename runs until it waits for the
lock, and the cut goes on. `race` also returns whether the cut reached its stop and which threads had to wait
for the lock, so a test can show it raced rather than ran in sequence (`WAITING` asserts both).

On the fixed engine the six rename-first tests below do not reach their stops: in each, the cut waits at
the source lock while the rename holds it, then raises `ENOENT` at its lookup (deviation row 4). The stops
matter on the baseline and the mutants, where they are where the rename lands; each test's baseline cause
shows its stop fires there.

| Test | Cut's stop | Fixed engine | Baseline (`630a406`) |
|---|---|---|---|
| `STAGED` (F6 stack) | after the stage `open_direct` | `ENOENT`, nothing cut | returns, `getattr` 5 |
| `COUNTED` (F1) | after `get_residency` | `ENOENT`, nothing cut | counter `(2, 1)` beside a 5-byte row |
| `SERVING` | before `fastest_copy` | `ENOENT` | `EngineError` |
| `DIRTY` | before `mark_dirty` | `ENOENT` | `LookupError` |
| `SOURCE` (served off the source, two levels deep) | after the source `open_direct` | `ENOENT` | returns, `getattr` 200 |
| `PROMOTE` (promotion off the source) | after the stage `put` | `ENOENT`, no intent, stage empty | `LookupError`, `promote` intent left, stage bytes with no residency |
| `LOOKUP` (pin) | before the promotion's `_lookup_again` | `ENOENT` | `ENOENT` (cycle 4's lookup) |
| `WAITING` (pin, cut first) | after the stage `open_direct` | stops there; the rename waits; returns; `/moved/s.txt` 2 bytes, `('dirty', 2)`, counter `(2, 1)` | the same |
| `STAGE_FREE` (pin) | — (probes the lock inside the stage `rename`) | the lock is free | the same |

The full names are in `verify/cut_gate.py` (`STAGED` … `STAGE_FREE`). Each of the first eight checks R1's
either/or, no unresolved intent, every stage counter (R2), and the counters through drain, eviction and an
open and release where a stage copy exists, and each ends in `assert_converged` after a flush. `PROMOTE`
and `LOOKUP` read the stage tree before any check opens the file, since an open promotes it. The module
docstring gets one bullet for the class. No existing function or class changes (R6).

### 2.3 The records (R7), all in P1's commit

- `AGENTS.md` § 5, the reading bullet: the sentence about a rename past its `_vacate` leaving the writes
  matching no row becomes the held-off statement of § 2.1 (refuse with `ENOENT`, or wait), pointing to
  § 10; the reused-id sentence keeps its pointer to § Known open violations.
- `AGENTS.md` § 10, the source-mutex bullet: a user `rename` holds it from its `_vacate` through its
  catalog move, and moves its stage copies after releasing it.
- Both high-blast-radius tables: `rename` joins the `_vacate` row and the overlay.py list (the list only
  widens; research 03 § 1).
- § Known open violations: the `5, 7, 10` row this seed owns goes. Nothing it describes stays open (the
  stage-move lag is a different window, outside R1 and R3, homed in the stress seed below).
- The counts: 521 → 530 at § Commands, § Conventions and § Verification; 531 → 540 total and 530 unit in
  § Repository map; 183 → 192 engine tests in § Architecture. Measured on the prototype: 530 passed under
  `CI=true` (§ 6).
- Root `PLAN.md`, the namespace-replay risk: the handle-less `truncate`'s recording joins the closed list,
  the "Still open" clause naming this seed goes, and one sentence says the rename's hold keeps an
  ancestor's catalog move out of the cut.
- `issues/freethreaded-concurrency-stress.md`, three dated lines naming this slug:
  - under "Path locks do not cover descendants", Q3's line: the other calls that hold a path lock and
    write the catalog by that path, where an ancestor's catalog move can still land: `evict_copy`,
    `_set_meta`, `_mark_handle_dirty`, `_record` (a copy served off a stage), `commit_row` and
    `_commit_hop`, `commit_upstream`, `commit_to_shared`, `create`, `mkdir` and `_remove`; argued from
    the code (research 01 § 7);
  - under the same bullet, the stage-move lag: a cut or `open` at the new path before the rename moves
    its stage copies raises `ENOENT` after a dirty mark, or promotes into a new stage directory, after
    which the rename's stage move fails with `ENOTEMPTY`; argued from the code (research 01 § 5a);
  - under "A replay racing a user rename of the entry's directory": closed by the same hold, which the
    replay's read and `begin` sit under; argued from the code, no test pins it.
- `issues/truncate-recording-after-ancestor-rename.md` and its `ROADMAP.md` entry stay until
  `code terrace roadmap` retires them after landing; no other file outside `spec/` names the seed.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | § 2.1: the rename's hold. Tests `STAGED` (a staged file, the F6 stack), `SOURCE` (served off the source), each with the rename on another thread past its `_vacate`; `WAITING`, the rename reaching the lock while the cut holds it. Deviation rows 2 and 3 |
| R2   | `COUNTED` and every test's `assert_counted`; `assert_counted_through` (drain, eviction, open and release) on the staged tests and `WAITING`, the return branch |
| R3   | `SERVING`, `DIRTY`, `PROMOTE` (no `promote` intent, no stage bytes without a residency); `LOOKUP` pins the promotion's lookup again |
| R4   | § 2.1's argument; the gate checks that `Catalog.rename` alone sets a path, that its one call is inside `rename`'s hold, and that nothing in the hold's own body takes a path lock or calls `_ensure`, `getattr` or `readdir` (its callee `_vacate` is unchanged and already ran under the mutex). The gate pins the chosen shape (the hold begins with `_vacate`), so it turns E1, an equivalent design, red; that is intended |
| R5   | § 2.1: the `previous is not None` half is removed as unreachable; the gate checks `truncate`'s code is otherwise the base's |
| R6   | § 2.2: nine tests, each ending in `assert_converged`; six fail on the baseline for their named cause; M1 (the fix removed) and M3 turn those six red, M2 also `LOOKUP`; M4 turns `STAGE_FREE` red, M5 `PROMOTE`, M6 `STAGED` and `SOURCE`, M7 `WAITING`; every kill must match a named cause; deviation row 3 for the pins |
| R7   | § 2.3, in P1's commit; the gate's `records` mode |
| R8   | P2 (`suite`, `seeds`) and P3 (`ci` on the saved run record) |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (GOAL intent) and again against this design.

- **§ 1, metadata before acknowledgment.** The cut's dirty mark still commits before the open and the cut;
  the cut's body is unchanged. The rename's queue row and catalog move still commit in one transaction.
- **§ 1, intent → act → confirm.** No bracket changes. `PROMOTE` pins that a promotion the rename used to
  interrupt now leaves no unresolved intent and no unaccounted stage bytes; the rename's stage-move
  intents are unchanged.
- **§ 5, the committed source path.** No `source_path` read or write changes. A rename stays deferred on the
  source: the hold covers the catalog move, not a source move.
- **§ 7, eviction trusts the counters.** The cut's counter write now always lands beside its residency write
  (R2); eviction is unchanged.
- **§ 10, concurrency.** One new holder of the source mutex: `rename` from `_vacate` through its catalog
  transaction (deviation row 1). Lock order holds (path locks → source mutex → SQLite write lock). Nothing
  under the mutex calls `locks.hold`, `_ensure`, `getattr` or `readdir`; it is never taken inside
  `db.transaction()` (mutant M2 is exactly that inversion, and the tests catch it). The delay it adds is
  the rename's catalog transaction: its wait for SQLite's write lock (up to the 5 s busy timeout) and the
  subtree `UPDATE`, whose cost grows with the directory's descendants. Meanwhile every source-served open,
  cut, promotion, commit to the source and replay waits. The rename already waited for the mutex in `_vacate`.
- **§ 11, the API contract.** The cut raises only `OSError` in R3's window (`ENOENT`); no `EngineError` or
  `LookupError` escapes it there.
- **High-blast-radius paths.** `truncate` (listed) and `rename` (joins the list). Geoffrey signs off on the
  engine diff at review, whatever it finds (Q4).

### Deviation justifications

Each row was read against the wording of every R-ID it touches.

| # | Deviation | Why needed | Simpler alternative rejected because |
|---|-----------|-----------|--------------------------------------|
| 1 | § 10: `rename` becomes a holder of the source mutex, from its `_vacate` through its catalog-move transaction, including that transaction's wait for the SQLite write lock and the subtree `UPDATE` of a large directory; the § 10 text says so in the same diff | Q1's held-off family: it is what keeps the catalog move out of the cut (R1, R3, R4) | **Keyed by identity**: a promotion's stage `put` at the old logical path can race the rename's stage move and leave bytes recorded by id at a path the catalog does not hold, so reads of the file fail (R1) and R3's "no stage bytes without a residency" fails; it touches `journal.py`, `catalog.py` and `transfer.py` (coupled core) and leaves the replay race open. **Hold only the transaction** (`_vacate` outside): equally correct for R1–R4 (equivalent mutant E1), but it takes the lock twice per rename and leaves a gap between the vacate's decision and the move it clears the way for; one hold is the smaller statement. Ancestor path locks and retry loops are excluded (Q1). Read against R4: the property holds as worded (§ 2.1). Against Constraints: the lock order and the "nothing under the mutex" rules hold |
| 2 | R1's "a rename already past its `_vacate`" is modelled by a rename on another thread stopped past its `_vacate`, not by the seed's same-thread injection | The RLock is re-entrant: a rename injected into the cut's own thread re-enters the lock and still breaks the recording under the fix. Production cannot reach that state: nothing inside the cut calls `rename`, and FUSE runs each operation on its own thread (research 01 § 4) | Keeping the same-thread injection would need the cut to refuse re-entrant renames, a check for a state that cannot occur. Read against R1: every sequence R1 lists runs (a staged file, a file served off the source, a rename on another thread, one past its `_vacate`); the seed's injection technique is evidence, not a criterion. R6's "deterministic injected-interleaving" holds: the interleaving is injected by stops on two threads |
| 3 | R6's "each SHALL have a fix-removal mutant that turns it red": three pins pass on the baseline, so no removal of the fix can turn them red. Each has a mutant that does: `LOOKUP` (the promotion's lookup again, `ENOENT` since cycle 4) under M2, the hold's lock order; `STAGE_FREE` under M4, the hold's extent; `WAITING` (a rename that reaches the lock while the cut holds it waits) under M7, the cut's counter write | Each pins a sequence R1 or R3 names, or a statement the records make, that the baseline already gets right | Dropping them leaves R1's return branch and R2's "WHEN the call returns" unpinned, R3's promotion window half pinned, and § 10's "after releasing it" unchecked. Read against R6: this is a reading of its SHALL, not a design choice, so it goes to the sign-off as a GOAL clarification (§ 5): either R6's "fix-removal" covers a mutant of the fix's own decisions, or the three pins are kept as pins outside R6's sentence |
| 4 | R1's and R3's WHEN clauses never fire on the fixed engine: no rename's catalog move lands between the cut's lookup and its return | The held-off family closes the window rather than making the call survive inside it; GOAL Q1 admits it | — (Q1 ruled it). Read against R1, R3, R4: each test drives a rename that, without the fix, lands in the window (the baseline column of § 2.2), and grades R1's two outcomes; R4's "whatever path a rename gives it meanwhile" is met because none can |
| 5 | `truncate`'s code changes (R5's half guard) in a function Geoffrey signed off on | R5 asks for the guard pinned or removed; the hold makes it unreachable, so a pin cannot exist | Keeping it would leave R5 unmet. The gate pins the rest of the code byte-for-byte (as an AST) to the base. Read against the Constraint: every clause of the signed-off property holds |
| 6 | `rename` joins both high-blast-radius tables (an § Invariants edit inside a `[fix]` diff) | Its hold is now what keeps the cut's recording keyed right; a reorder in it is silent, the definition of the list | Leaving it out under-states the review gate. Same-commit rule: § Invariants text moves with the behaviour |

Figures, rederived from the design: `JOIN` is 10 s; SQLite's `busy_timeout` is 5 s (`db.py:35`). Under M2 the
cut's next transaction waits out the 5 s and raises `OperationalError` with 5 s of the cut's 10 s join left,
so M2 fails the tests on the outcome, not on a join timeout (measured on the prototype: M2's run of `test_replay.py` took 39 s, seven tests failing on
`OperationalError('database is locked')`). On the fixed engine every wait ends in milliseconds; 10 s is slack for a loaded runner, not a bound
on correctness.

## 4. Rabbit holes (resolved)

1. **Which family (Q1)?** Held off; keyed by identity fails on the promotion's stage bytes
   ([01 § 6](research/01-locks-and-identity.md)). Deviation row 1.
2. **Is the hold enough for R4?** Yes: one statement moves paths, one caller, inside the hold; the cut's
   lock span has no gap, since `_serve`'s inner `with` re-enters ([01 § 1](research/01-locks-and-identity.md)).
3. **Stage moves inside the hold?** No. Inside, they would close a window at the *new* path (a call there
   before the stage move), which R1 and R3 do not name, at the cost of holding the source mutex across stage
   renames (a whole-prefix copy on an object-store stage). Recorded in the stress seed; `STAGE_FREE` and M4
   keep the records exact ([01 § 5](research/01-locks-and-identity.md),
   [02 § 3](research/02-test-harness.md)).
4. **R5.** The guard is reachable today only through the race; removed ([01 § 2](research/01-locks-and-identity.md),
   [02 § 2](research/02-test-harness.md)).
5. **A deterministic two-thread driver.** The watched lock reports a thread that must wait before it
   blocks, so the driver releases the rename or the cut by events, never by time; 30 runs of the nine tests
   under four xdist workers on the prototype were green 30 times ([02 § 2](research/02-test-harness.md)).
6. **Mutants.** M1 (fix removed), M2 (mutex inside the transaction), M3 (only a file rename holds) each turn
   the six fix tests red, and M2 `LOOKUP` too; M4 (stage moves under the mutex) turns `STAGE_FREE` red. M5–M7
   came from the review (§ 4.1): M5, a promotion that refuses after its copy and strands the stage bytes
   (`PROMOTE`); M6, a cut recorded by id that then refuses having cut (`STAGED`, `SOURCE`); M7, a counter
   that moves only when a cut grows the copy (`WAITING`). Each kill must match its named cause. Two
   equivalents, argued and run: E1, `_vacate` before the hold (a rename past `_vacate` then waits for the hold, and the cut
   returns recorded; all nine pass), and E2, only a directory rename holds (a file's own rename needs the
   path lock the cut holds; all nine pass).
7. **The records beyond R7's list**: the tables, the engine count, the module docstring, and the stress
   seed's replay bullet, which the hold also closes ([03 § 1](research/03-records-and-gate.md)).
8. **The gate's own lessons** from cycles 3 and 4 ([03 § 2](research/03-records-and-gate.md)): added-line
   length, `HYPOTHESIS_STORAGE_DIRECTORY` per run, "owned by" and "home" in the ledger, the run record's
   fields.
9. **Adversarial review.** Ran on the drafted design and gate; findings and folds in § 4.1.

### 4.1 Adversarial review

One fresh reviewer, given the GOAL, this design, the drafted gate and the prototype, attacked the gate with
lab variants. It found no engine defect: the hold meets R1–R4, the R5 removal is sound, the lock order holds,
and the figures check (M2's seven kills at the 5 s busy timeout, 39 s measured). Its findings and folds:

| Sev. | Finding | Fold |
|---|---|---|
| HIGH | No baseline run or mutant reached the `ENOENT` branch's "nothing cut" checks, `PROMOTE`'s and `LOOKUP`'s stage checks, or `WAITING`'s counter checks: tests weakened there passed `regressions mutants records`, and so did two broken engines (a cut recorded by id that then refuses; a promotion that refuses and strands its stage bytes) | M5, M6, M7 added, each naming the tests whose weak spot it attacks; every mutant kill must now match a cause regex (`REMOVED_CAUSE` for M1 and M3, where HEAD's missing half guard turns `STAGED`'s silent miss into an `AttributeError`) |
| MEDIUM | `WAITING` gave the same outcome with its stop on a method the cut never calls: the gate could not tell a race from a sequence | `race` returns whether the stop fired and who waited; `WAITING` asserts the stop fired and the rename waited, and `regressions` checks that it does |
| MEDIUM | `WAITING` can be named by a mutant (a counter that moves only on growth) | M7; no test is exempt in `mutants`. Row 3 then narrows to a reading of R6's "fix-removal", sent to the sign-off |
| MEDIUM | `records` passed a false `truncate` docstring ("never raises ENOENT"): its checks were keyword presence | Positive regexes for each scoped clause (ENOENT "before anything is marked or cut", "unless another rename carried the file back"; "stage copies after releasing … ENOENT after its dirty mark"; § 5's "having cut nothing" and "waits … recorded"), and "never raises ENOENT" or "never leaves a dirty mark" forbidden in § 5, § 10 and both docstrings |
| MEDIUM | Row 2 reads R1's sub-bullet, but § 5 did not send it to the sign-off | Listed in § 5's open questions |
| LOW | § 2.1 said the gate checks both R4 facts; it checked one | The gate scans `src/` for SQL setting `path` and reads `META_FIELDS` |
| LOW | Row 1 understated the hold: it also covers the wait for SQLite's write lock and a large subtree's `UPDATE` | Row 1, § 3 and § 2.3's § 10 text say so |
| LOW | The `truncate` docstring's ENOENT sentence ignored a second rename carrying the file back; the stage-lag line ignored another entry's stage bytes | Both scoped in the drafted text; the stress-seed line names the second case, argued |
| LOW | The gate pins the chosen shape, so it reds E1, an equivalent | Intended; said in the R4 row and the gate's docstring |
| LOW | `cites_hold` was vacuous on the prototype, whose plan commit came after its records | Rerun by the reviewer from the first branch commit: green, two seeds, no unpinned cite. In the real cycle the plan commit precedes the build |
| LOW | `seeds` inherited the caller's `HYPOTHESIS_STORAGE_DIRECTORY` | Set per seed |
| LOW | The ledger's vocabulary missed "remains open", "left open", "defer(s)", "a later cycle", and "home" matched "home directory" | Widened, and "home" no longer matches "home directory" or "home path" |
| LOW | `prepush` scanned a two-dot diff | Three-dot (`origin/main...HEAD`) |
| LOW | § 2.2's table read as if the cut reaches its stop on the fixed engine | § 2.2 says it waits at the lock in all six |

After the folds, `regressions mutants records` ran green on the prototype in 52 s, and `ledger prepush` green
(§ 6).

## 5. Risks & open questions

**Plan-gate rulings (foreman, 2026-11-08).** Q1's held-off design is accepted. Deviation rows 1, 4 and 5 are accepted as written, including row 1's cost, which § 10 states and Geoffrey sees at review under Q4. Rows 2, 3 and 6 are ruled as recommended, and recorded as GOAL clarifications Q5, Q6 and Q7.

- **The stage-move lag** (outside R1 and R3) stays open and is homed in
  `issues/freethreaded-concurrency-stress.md` (§ 2.3). A call at the new path in that gap can raise
  `ENOENT` after a dirty mark, or collide with the stage move (`ENOTEMPTY`), argued from the code.
- **The identity gap** is unchanged and stays owned by `issues/entry-identity-token.md` (Non-goals).
- **Q3's other calls** keep their path-keyed writes; Q3's line in the stress seed is their home.
- **Same-thread injection.** The seed's evidence technique stays red under the fix (deviation row 2). A
  future test that injects a rename into the cut's own thread will read as a regression; row 2 says why
  it is not.
- **Open for the gate** (GOAL readings the design carries; each needs a ruling, not just a nod):
  - row 2: R1's "a rename already past its `_vacate`" is met by a rename on another thread stopped there,
    and the seed's same-thread injection is retired as evidence;
  - row 3: R6's "fix-removal mutant" covers the three pins' mutants of the fix's own decisions (M2, M4,
    M7), or the pins stand outside R6;
  - row 6: `rename` joins the high-blast-radius tables in this cycle.

## 6. Verification strategy

The engine is mount-free, so the evidence is the unit suite, driven through the gate script
`verify/cut_gate.py` (modes in its docstring), with the profile's `verify` in its `suite` mode. No CLI
drive applies: the behaviour is a lock order inside the engine, which `tools/temp_site.sh` cannot inject.

- **P1** `regressions mutants records suite`: the nine tests pass here; the six fail on the baseline for
  their named causes and the three pins pass there; existing tests, `test_model.py` and both `conftest.py`
  are unchanged; `truncate`'s code is the base's but for the half guard; `catalog.rename` sits in the hold;
  M1–M7 kill what they name, each for its cause; the records say what § 2.3 says; the full suite under `CI=true`.
- **P2** `suite seeds`: 20 Hypothesis seeds from committed state.
- **P3** Run A `ledger prepush`, the foreman pushes; Run B `ledger records ci` on the saved run record.

Measured on the planning prototype (development Mac, 2026-11-08), after the review's folds: P1's whole verify
80 s (`regressions`, `mutants` and `records` 52 s, M2's run about 39 s of it); `records` about 5 s; `suite` 28 s with 530 passed; `seeds` 4 min 38 s on two workers, twenty of twenty green.
