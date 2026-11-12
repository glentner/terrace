# REVIEW — A write racing a commit is never recorded clean

> Adversarial QA by `code-review`. The correctness pass ran in a fresh subagent and graded the diff
> against [`GOAL.md`](GOAL.md) and `AGENTS.md` § Invariants **only**. It did not see `PLAN.md` or
> `TECH.md`, which avoids grading the plan's own homework. Every finding cites an **executed**
> command, not an assertion.

- **Reviewed commit:** `99851a7`. **Base:** `main` (`origin/main` at `3e85305`; merge base
  `0967d94`). **Date:** 2026-11-12.
- **Verdict:** changes-requested.
- **Cycle:** 1 of ≤3, mirroring `review.cycle` in `TECH.md`. It was a fresh blind pass over the full
  spec-excluded diff: 10 files, +987/−59.
- **Contract drift:** the GOAL moved once after the shape commit. `4b4b7ec` added the clarification
  of R7 at the plan sign-off ("as today": the one dirty mark after a hop is the base's cost and
  stays). The foreman confirmed it, and the review graded the GOAL as it stands at head.
- **Mode:** one blind reviewer. The `debate` variant and the completeness sub-pass were not run, by
  the foreman's ruling. The sequential fallback did not run. The reviewer was told to break the fix:
  to find an interleaving that leaves a copy recorded clean while its upstream lacks bytes, a
  deadlock or lock-order inversion, a cost added to a write on a dirty handle, a leak of the per-hop
  record, and any way the stat comparison still clears a copy by itself. It built its own injection
  drives and did not rely on the cycle's tests.

**The fix's behaviour stands.** No drive made a copy read `clean` while its upstream lacked bytes,
deadlocked the engine, leaked a hop record, or added SQL or a path-lock wait to a write on a dirty
handle. The two findings are about the records and about one unpinned rule.

Evidence: every script and log named below is in the worktree's gitignored `.local/review1/`. The
harness refused the reviewer's report file, so that folder holds the drives and logs and no
`REPORT.md`; this file is the record.

## Verification run

Interpreter: CPython 3.15.2 with the GIL, macOS. Drives ran as `tools/temp_site.sh uv run python
<drive>` against tmpdir tiers with no mount. Pytest never ran through the helper.

- The profile's `verify` at head, `tools/lint.sh && uv run pytest -n auto -m unit`
  (`verify-head.log`): lint exit 0, `lint: all checks passed`; **762 passed** in 30.97 s, 0 failed,
  0 errors.
- The same command on the merge base, in a detached worktree with its own environment
  (`verify-base.log`): lint exit 0; **731 passed**.
- The cycle's `tests/engine/test_racing.py` copied onto the base tree (`cycle-tests-on-base.log`):
  `22 failed, 9 passed`. Nineteen fail on `assert 'clean' == 'dirty'`, the four parameters of the two
  reproductions among them; three fail on mechanism the base lacks (the hop record and its mutex).
  At head the file gives `31 passed` (`r5-named-tests.head.log`).
- The reviewer's own reproductions of Route 1 and Route 2, written from the GOAL's Problem
  (`repro_routes.py`): on base 4 of 4 show the loss (`residency={project: clean, scratch: clean}
  queued=0 stage=b'BBBB' source=b'AAAA'`, `evict_copy -> True`, read back `AAAA`); at head 4 of 4
  are truthful and reach the source. Route 2 asserts the tier reports the same size and mtime
  before and after.
- An injection matrix (`matrix.py`): 12 kinds of change × 6 points of the hop's interval ×
  `commit_row` and `commit_upstream` × a two-tier stack and both hops of a three-tier stack, with
  each hop's post-copy stat masked to equal the copy's own. Head: **612 passed, 0 failed**. Base:
  428 passed, 184 failed. The rescue route (`matrix.py --shared`): 102 passed, 0 failed.
- Real threads placed by explicit gates (`threads.py`): **29 passed, 0 failed**. The scenarios
  include a write held across a whole commit, a change landing whole between the hop's last stat
  and its record, a writer that reads its flag after the hop took its record, two writers on two
  handles, a release mid-copy, `open` with `O_TRUNC` and a path truncate from other threads, two
  commits of one path from two threads, a hop whose `put` raises with a write in flight, a
  post-copy stat that raises, and a handle on the middle tier. Each ends with no hop record left,
  no change counted in flight and no path lock held.
- Statement and lock counts (`cost.py`, `cost.diff.log`): at head a write on a dirty handle costs
  `sql=0 path_locks=0` across 64 writes and 2 truncates, two alternating handles, writes inside an
  open hop, a source-served handle, and a write made while another thread's commit holds the path
  lock mid-copy. The first write after a hop costs 6 or 7 statements and one path lock on base and
  head alike. The SQL and path-lock columns and the statement texts are identical between the two
  trees. Head adds two acquisitions of the handle mutex per write.
- Lock order (`rv_lockcheck.py`, `lockcheck-unit.log`): the unit suite run serially under a plugin
  that wraps the handle mutex, the source lock, the path locks and the SQL entry points gives
  `762 passed`, 2,793 engines, 19,889 mutex acquisitions and **0 violations** (nothing acquired
  under the mutex, the mutex never taken inside a transaction). Its negative control records 3.
- Hand-planted mutants in a scratch clone, never in the worktree (`mutate.py`, `mutate.a.log`,
  `mutate.b.log`): 12 planted, 11 killed by the cycle's tests, 1 survived (F2). Bytecode caching
  was off for the cited run.
- A randomised thread drive (`stress.py`) passes at head and on base alike. It is a deadlock and
  convergence smoke and carries no evidence about the loss.

**Not available, and not claimed.** The free-threaded interpreter is not installed on this host.
Every log reports `gil=True`. No result here speaks for the free-threaded build, which the
repository supports; that gap is open. The integration suite was not run. CI was not observed.

**Tree on hand-back.** `git status --porcelain` is empty at `99851a7`. No tracked file was edited.
The scratch clone's status was empty after each mutant, and the head environment still imports
`terrace` from the worktree's `src/`; the orchestrator re-checked all three.

## Requirement → evidence matrix

| R-ID | Verified by | How | Status |
|------|-------------|-----|--------|
| R1 | reviewer | `write` → `_change` / `_meet` / `_mark_handle_dirty`, and `_commit_hop`'s record opened from `TransferPool.copy`'s `before_read`. Own Route 1 and Route 2 lose the write on base and carry it at head, on both commit routes. Matrix 612 of 612; threads 29 of 29. | ✅ |
| R2 | reviewer | Matrix rows for a shorter and a longer truncate through a handle and for `open` with `O_TRUNC`; the threaded placements between the last stat and the record. The cycle's truncate test fails on base on `'clean' == 'dirty'` and passes at head. | ✅ |
| R3 | reviewer | A path truncate from another thread, on both routes, with and without a handle open: the cut waits on the path lock, the commit makes one copy, the holder then reads `dirty` with its row, and the new length reaches the source. The cycle's test passes on base and at head. | ✅ |
| R4 | reviewer | `changed = met or stat is None or …`: the stat can only veto. Own Route 2 with the mtime restored; every matrix cell masks the post-copy stat. Mutants that drop the record from the outcome are killed. By reading, no test of R1 to R4 depends on timestamp granularity. | ✅ |
| R5 | reviewer | The matrix's quiet cells (no change, and a change that returned before the copy started, with written and unwritten handles open) give one copy per hop, `clean`, and the row settled in the one call. `tests/engine/test_writeback.py` is unchanged from the merge base, and the two named tests pass. | ✅ |
| R6, the tests | reviewer | The reproductions are `test_write_held_past_the_commit_reaches_the_source` and `test_same_size_write_behind_an_unchanged_stat_reaches_the_source`, each on both commit routes, under `-m unit`. They are placed by Event handshakes and a wrapper with a frozen stat, with no sleep. Each asserts the bytes at holder and source, no `data` row, `evict_copy` and the read back. On base all four fail on the defect's own observation; at head they pass. | ✅ |
| R6, the record | orchestrator | `verify/base.txt` names the merge base `0967d94` and lists both reproductions `FAILED` on both routes; `verify/final.txt` lists them `PASSED`. The reviewer's independent run agrees, and adds the reason for the base failures, which the record does not state. | ✅ |
| R7 | reviewer | The cycle's test counts statements and asserts zero over 64 writes and a truncate. The reviewer's independent trace agrees and finds the base's and head's costs identical, the one dirty mark after a hop included. | ✅ |
| R8 | reviewer, then orchestrator for F1 | The defect's row is gone from § *Known open violations*. § 6 and § 10 are rewritten, and each claim the reviewer could execute holds. `_change` and `_meet` are added to both high-blast-radius tables and nothing is removed. The unit count reads 762 in all four places. **Two other measured figures are stale: F1.** | ❌ |
| R9, local | reviewer | Lint exit 0; 762 passed, 0 failed, 0 errors; 762 exceeds the base's 731. | ✅ |
| R9, CI | not observed | Read at publish. | — |

**Operating manual.** Apart from F1, no drift. No spec ids are embedded in `src/` or `tests/`
(`counts-stale.log`: the word-match grep exits 1).

**Unmapped changes:** none that block. No source or test hunk is unmapped.

- The new seed `issues/stage-handle-outlives-its-file.md`, its `ROADMAP.md` entry and its § *Known
  open violations* row are a deferral record. The reviewer graded the seed as prose and reproduced
  its Problem as written, with the same result on the merge base and at head (`seedclaims.py`). It
  is pre-existing, and this diff neither repairs nor widens it.
- The dated notes appended to three seeds each hold at head.
- `issues/racing-write-marked-clean.md` reads `status: adopted:racing-write-marked-clean`.

## Findings

One HIGH and one LOW, both CONFIRMED. No PLAUSIBLE finding. No out-of-Scope reproduction.

### [HIGH/CONFIRMED] F1 — two measured figures in `AGENTS.md` are stale

- **Where:** `AGENTS.md:128` ("393 engine tests") and `AGENTS.md:834` ("all 75 `.py` files carry
  them").
- **Failure scenario:** the diff adds `tests/engine/test_racing.py`, one file and 31 tests. It moved
  the unit count, the total and the `tests/` file count, and left two figures behind. `tests/engine`
  collects 424 tests, and `src/` with `tests/` holds 76 `.py` files. Both figures were right on the
  merge base, so this diff made them false.
- **Evidence:** `counts.log` (base: `393 tests collected`, 75 files; head: `424 tests collected`,
  76 files). `verify-head.log:5`: `lint: OK    SPDX header pair on all 76 .py files under src/ and
  tests/`. The orchestrator re-measured at `99851a7`: `uv run pytest -m unit --collect-only -q
  tests/engine` ends `424 tests collected`, and `git ls-files 'src/*.py' 'tests/*.py' | wc -l`
  prints 76.
- **Touches invariant / requirement:** R8's last bullet (the measured test counts `AGENTS.md`
  carries), and § Conventions' same-commit rule: the file's measured figures have no other owner.
  HIGH by the rubric's table. Neither line is inside § Invariants.
- **Route:** the property is that every measured figure `AGENTS.md` carries equals what the tree
  measures at the commit that changes it. Its domain is every such figure: the test counts at each
  scope (unit, integration, total, per directory), the file counts, and the timings. Lines 128 and
  834 are the instances found. Re-measure the whole set and do not stop at these two.

### [LOW/CONFIRMED] F2 — no test pins the reset of a handle's flag after a hop a change met

- **Where:** `src/terrace/engine/overlay.py:1085-1087` (`_commit_hop`). § 6 states the rule at
  `AGENTS.md:513-516`.
- **Failure scenario:** head's code is right. Changing the reset to `open_handle.marked_dirty =
  bool(met)` passes the whole unit suite. With that change `commit_upstream` admits a write inside
  every hop and copies again for as long as another thread keeps writing, with the path lock held.
- **Evidence:** `mutate.b.log`: `SURVIVED M13 the flags are reset only when the hop met no change:
  40 passed`. `liveness.m13.log`: the mutant's diff, then `762 passed` for the unit suite, then the
  drive's `writes admitted inside a hop: 12; writes that waited on the path lock: 0; copies: 13`.
  `liveness.head.log` at head: `writes admitted inside a hop: 1; writes that waited on the path
  lock: 1; copies: 2; commit_upstream returned: True`. The drive places one whole write from a
  fresh thread between each hop's stat and its record, by gates on the handle mutex.
- **Touches invariant / requirement:** § 6, which calls the unconditional reset the thing that lets
  `commit_upstream`'s loop end under a writer that never pauses. No R-ID states the property.
- **Why CONFIRMED and not a coverage observation** (rubric → *Verdict & loop*). The mutated line is
  not defence in depth: it runs on every hop with a handle open, and the mutant changes a reachable
  behaviour, shown by an executed drive. The rule and the `met` distinction it turns on are new in
  this diff, so the gap is this cycle's. The other reading is that the reset is a class already
  pinned (mutant M8, no reset at all, is killed) and M13 a deeper layer of it. The orchestrator did
  not take that reading, because M8's test pins the safety side and nothing pins the liveness side.
  The reviewer left the call open, and the foreman may re-class it.
- **Route:** a surviving-mutant finding. The property, both ways: `commit_upstream` returns under a
  writer that never pauses, because after a hop a change met that handle's next change waits on
  the path lock the commit holds; and no change is admitted without being on a hop's record or
  behind a dirty mark, so the pin for the first half is never bought by losing a write. Its domain
  is every caller of `commit_upstream` (`flush`, `fsync` under the commit policy, eviction, drain),
  a write and a truncate through a handle, and each hop of a stack. Then a mutant sweep over the
  decisions of the hop record in `overlay.py` (`_commit_hop` and its `before_read`, `_change`,
  `_meet`, `_mark_handle_dirty`, `write`, `truncate`) and the `before_read` call in
  `transfer.py`'s `copy`, each mutant under `commit_row` and `commit_upstream`, with equivalent
  mutants recorded and why. The reviewer's twelve mutants and `liveness.py` are a starting ledger.
  The decision is computed in one place, so no structural fix comes first.

## Human-gate triggers

**Triggered by F2.** It is CONFIRMED and sits in `_commit_hop`, a path on the high-blast-radius
list in `AGENTS.md` § Invariants. The finding is an unpinned rule and not a defect in the code at
head. For this run the foreman is the judge of the gate; the sign-off is the foreman's to record
here before publish, and it holds across later cycles.

F1 does not trigger the gate: `AGENTS.md` lines outside § Invariants are not on the list, and no
`[human gate]` section is violated.

## Optional completeness sub-pass

Not run, by the foreman's ruling.
