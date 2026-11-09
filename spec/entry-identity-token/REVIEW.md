# REVIEW — A call that waits for the source lock never acts on a file that took a purged entry's id

> Adversarial QA by `code-review`, run in an isolated context (one fresh reviewer subagent at effort
> `xhigh`). The correctness pass grades the branch diff against [`GOAL.md`](GOAL.md) + the repo's
> `AGENTS.md` § Invariants **only**. It does not see `PLAN.md`/`TECH.md`. Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `68a5f95`  ·  **Base:** `main` (`origin/main` = `c7d2049`)  ·  **Date:** 2026-11-09
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3. This mirrors `review.cycle` in `TECH.md`.
- **Mode:** a fresh blind pass over the full spec-excluded diff (`git diff origin/main...HEAD -- . ':(exclude)spec/'`). The sequential fallback did not run.
- **Contract drift (step 1.4):** `GOAL.md` moved after the shape commit `647067e`, in two commits:
  - `5893e1f`: the Q1–Q6 rulings;
  - `f39baca`: the plan-gate readings and Q-a to Q-e.

  The foreman (Themis) confirmed both as post-shape clarifications under Geoffrey's standing autonomy mandate for terrace. This review grades `GOAL.md` as it stands at HEAD.
- **Evidence:** the worktree's gitignored `.local/review1/`, cited below as `$E/…`.

## Verification run

| Command | Result | Evidence |
|---|---|---|
| `CI=true tools/lint.sh` | exit 0, all five checks OK | `$E/lint_ci.log` |
| `CI=true uv run pytest -n auto -m unit` at HEAD | **548 passed**, 0 failed, 0 errors | `$E/verify_ci_head.log` |
| The same command in a clone at `c7d2049` | **530 passed** | `$E/verify_ci_base.log` |
| `uv run pytest tests/engine/test_model.py --hypothesis-seed=N` for N = 1..20, with `CI` unset and no `.hypothesis/` in the tree | **20/20 passed** | `$E/hyp_seeds_{1_10,11_16,17_20}.txt` |
| Collected counts at HEAD | 558 total, 548 unit, 10 integration, 42 test files, 210 engine tests. At baseline: 540 total, 192 engine. | `$E/counts_head.txt`, `$E/counts_base.txt` |
| `tools/temp_site.sh uv run sosfs --version` | exit 0. The diff has no CLI surface, so this is a smoke run only. | `$E/temp_site_smoke.txt` |

The reviewer added `-q -p no:cacheprovider` to the pytest runs so that no `.pytest_cache` landed in the tree. The test selection is unchanged.

Other drives the reviewer ran:

- **Injected-interleaving probe** (`$E/probe_reuse.py`). The baseline reproduces all three defects:
  - the cut returns success and cuts the new file to 4 B, both when it is source-served and when it is staged;
  - `open` returns a handle on the new file's 500 B;
  - the promotion copies 30 B into scratch and records a residency.

  At HEAD every call raises `ENOENT`, and the bytes, the handles, the intents and the residency all stay unchanged (`$E/probe_reuse_summary.txt`).
- **R5 drive** (`$E/r5_summary.txt`). The `c7d2049` code wrote a state database, and HEAD opened it with entries, residencies, queued rows, intents and `user_version` all equal.
- **Mutant sweep:** eleven mutants, run in the clone `$E/mut`. They are listed under R7 below.

**Gates not observed.** R9's CI-workflow bullet (the four jobs concluding `success`) was not observed in this pass. That check belongs to `code-publish`, which reads the run record.

## Requirement → evidence matrix

No R-ID is met by a committed `spec/` artifact: every R8 record sits outside `spec/`. The blind reviewer therefore graded all nine. The orchestrator re-checked F1 against `c7d2049` itself.

| R-ID | Implemented by | Verified how | Status |
|---|---|---|---|
| R1, the cut | `overlay.py:848`, the truncate check under the lock (`5aaa1f0`) | probe, baseline against HEAD, for the new file both source-served and staged. Tests `…refuses_a_source_served_file…` and `…refuses_a_staged_file…` go red under M0, M1 and M6. | ✅ |
| R2, a source-served open | `_lookup_again`, `overlay.py:744` | probe: HEAD raises `ENOENT`, leaves 0 handles for the id and the bytes intact. `test_open_refuses_a_source_served_file…` goes red under M2 and M6. | ✅ |
| R3, an open that promotes | `_serve` → `_lookup_again`, before `transfers.copy` | probe: no pending intent, no scratch bytes, no scratch residency. `test_open_that_promotes_refuses…` goes red under M2 and M6. | ✅ |
| R4, no false refusals | the purge watch holds only purged ids | six tests: a flush purge and a `create`, for each of the three calls. The probe shows another entry's id in the watch at the check and the calls proceeding, so the tests are not hollow. M3 (refuse on any purge) turns all six red. | ✅ |
| R5, existing state databases | no change to `db.py` or `schema.sql`; `SCHEMA_VERSION` is 2 in both trees | `git diff --stat` is empty; the R5 drive above | ✅ |
| R6, the property | each lookup accepts only an id that is not in its watch. The watch is cleared atomically with the naming read (`_named` under the mutex, or `_adopt` inside its write transaction). Every purge records into the watches inside its own transaction. | reading, plus instrumentation: 618 of 618 `_purge` calls ran inside a transaction (`$E/instr_purge_in_txn.log`) | ✅ |
| R7, the tests | 18 new tests in `tests/engine/test_replay.py` | every new test ends in `assert_converged` (`$E/new_tests_converge.txt`). All 111 existing top-level definitions are byte-identical (`$E/test_replay_hunks.txt`). Every new test has a mutant that turns it red (sweep below). | ✅ |
| R8, the records | `5aaa1f0` (P1), in the same commit as the code | the § 5 and § 10 text and the docstrings agree with the code; both high-blast-radius tables are widened, by additions only; the Known open violations row is dropped; root `PLAN.md` § Risks agrees; the stress seed carries Q5's dated line; the counts 548/558/10/42/210 match the measured tree; no `issues/` cite or R/P/Q id appears in `src/` or `tests/` (`$E/grep_ids.txt`). **The Q-e pins are false (F1).** | ❌ partial |
| R8, last bullet | — | current state only: `ROADMAP.md:35` still names the seed, as expected until the roadmap step runs (`$E/grep_seed_name.txt`) | n/a yet |
| R9, the gate | — | lint exit 0; 548 passed against 530 at baseline; seeds 1–20 pass. The CI jobs were **not observed**. | ✅ (CI not observed) |

**§ Invariants, the sections the diff touches:**

- **§ 1:** confirm and cancel still precede the purge.
- **§ 5 and § 10:**
  - nothing new holds the source mutex;
  - nothing under that mutex calls `locks.hold`, `_ensure`, `getattr` or `readdir`.
- **The purge-watch mutex is a leaf.** In `$E/probe_watch_mutex.txt`, a writer holding the write lock waits on the mutex while the reader's WAL read completes in 0.001 s, with no deadlock.
- **Q-a:** the code matches the § 10 text.
- **Module-global state:** none. The new state is guarded by the watch mutex or is thread-local.

No violations.

**Unmapped changes:** none of substance. The seed-pin edits are R8 records under the Q-e ruling.

The new `PurgeWatch` class goes unnamed in `AGENTS.md`. This is informational, not a finding: `overlay.py` is already gated, and the text describes the holders of the purge-watch mutex.

**Mutant sweep (R7).** Each mutant has a `.diff` and a `.log` in `$E`.

| Mutant | Change | Tests turned red |
|---|---|---|
| M0 | baseline `src/` with HEAD's tests | 9 refusal tests. The R4 and naming tests stay green, as expected. |
| M1 | cut check without the watch | 5 |
| M2 | `_lookup_again` without the watch | 4 |
| M3 | refuse on any purge | 19: all 6 R4 tests, plus 13 existing tests |
| M4 | `_named` doesn't clear the watch | 1 |
| M5 | `_adopt` doesn't clear the watch | 1 |
| M6 | `_purge` records nothing | 9 |
| M7 | `_remove` records after the commit | 1 |
| M8 | `_watch` not installed on the thread | 2 |
| M9 | `_vacate`'s fallback purge unwrapped from `db.transaction()` | **survives** (G1) |
| M10 | the cut opens its watch after `_ensure` | 2 |

## Findings

### F1 [MEDIUM/CONFIRMED] The "(holds at `c7d2049`)" pins added to `issues/*.md` are false

- **Where:**
  - `issues/archive-finalization-integration.md:36`
  - `issues/archive-spec-plumbing.md:28`, `:63`, `:71`, `:126`
  - `issues/archive-tier-test-coverage.md:31`
  - `issues/rename-to-terrace.md:98`
  - `issues/invariant-audit-gaps.md:24`, `:39`
  - `issues/benchmark-harness.md:21`
  - `issues/source-commit-staged-publish.md:20`
  - `issues/audit-command.md:201`

  One unpinned cite is also made untrue by this diff: `issues/freethreaded-concurrency-stress.md:81`.
- **Failure scenario:** a reader follows a pinned cite to `c7d2049` and lands on unrelated code. At `c7d2049`:
  - `flush` is at `overlay.py:1006`, not `:870`;
  - `_commit_hop` is at `:900`, and `:835-840` is `_record`;
  - confirm-before-purge is at `:1118`, not `:1061`;
  - the marker refusal is at `:139-140`, not `:127-128`;
  - the `EngineError` is at `:212`, not `:200-201`;
  - `read` and `write` are at `:716` and `:720`, and `:639-654` is `open`'s docstring;
  - the residency insert is at `catalog.py:207`, not `:196-203`;
  - `start`'s `else` branch is at `:128`, outside the cited `:109-126`.

  The `source-commit-staged-publish.md:20` pin also contradicts the same file's dated record at `:112-113`, which says the cite held at `0e6d14c` and had moved by `d557db2`.

  In the other direction, `freethreaded-concurrency-stress.md:81` cites `Catalog.rename` at `catalog.py:170`. That is true at `c7d2049`, but at HEAD `:170` is blank and `rename` is at `:171`, because `purge` grew by a line. This diff touches that seed, and it did not pin the cite.
- **Evidence:** `git show c7d2049:<file> | sed -n …`, plus a grep for each cited symbol (`$E/pins_at_c7d2049.txt`, every cite collected in `$E/line_cites_head.txt`). The orchestrator re-ran `git show c7d2049:src/sosfs/engine/overlay.py | grep -n 'def flush\|def _commit_hop\|def _record'` and got `1006`, `900` and `827`, plus `:1118` for the confirm. That confirms the finding.
- **Touches:**
  - R8: "saying exactly what the code delivers and no more". The Q-e ruling makes these pins R8 records.
  - No § Invariants section.
  - The rubric could grade this HIGH ("R-ID wrong"). It is graded MEDIUM as a partially met requirement that degrades no gate. Either grade blocks.
- **Route, as the property:** every line cite in a file outside `spec/` that this diff writes, pins or leaves standing in a file it touches says what is at that line at the commit it names. An unpinned cite must hold at the final tree.
  - **The domain:** every `(holds at <sha>)` this diff added, plus every unpinned cite in a touched file that this diff's own line shifts made untrue.
  - **The check:** mechanical, against `git show <sha>:<file>`, not by recall.
  - **What it reopens:** P1's records. The phases after it re-run their gates (`code-build` Step 1.3).

## Coverage observations

### G1 [LOW] A surviving mutant: nothing pins `_vacate`'s fallback purge inside `db.transaction()`

- **Where:** `src/sosfs/engine/overlay.py:1333-1334`
- **What:** mutant M9 unwraps the transaction, so the purge autocommits before it records into the watches. All 103 `test_replay` tests stay green (`$E/mutant_M9.diff`, `$E/mutant_M9.log`). Under M9, a thread on another path could take the freed id and name its entry between the commit and the record. It would then be refused falsely, a failure of R4's kind.
- **Why it is not CONFIRMED:**
  - at HEAD the decision is right: 618 of 618 `_purge` calls ran in a transaction;
  - R4's stated sequences don't go through this fallback;
  - reaching it needs real threads, which the Non-goals home in the stress seed.

  Under the rubric's *Coverage observation*, this is recorded and seeded.
- **Home:** an `issues/` seed plus its `ROADMAP.md` entry (`AGENTS.md` § Factory → Where deferred work goes). It is not a security weakness. The orchestrator did not file the seed in this pass and leaves it to the foreman's routing; see the hand-back.

### Dropped after refutation

- **The window between `_named` clearing the watch and reading.** `$E/probe_watch_mutex.txt` shows the read does see an uncommitted purge row. But a live entry is purged only in `_remove`, under the deleted path's lock, and every other purge targets a tombstone, which `catalog.get` masks. A false accept is not reachable.

## Human-gate triggers

- **From findings:** none. F1 is in `issues/*.md`, which is not on § Invariants' high-blast-radius list. G1 is in `overlay.py`, but it is a coverage observation, not a CONFIRMED finding.
- **Unconditional (GOAL Q6):** Geoffrey signs off on the final engine diff whatever the review finds. That gate holds until publish, through any later clean cycle.

## Optional completeness sub-pass

Not run: `completeness` was not requested.

## Review cycle 2 — approved (2026-11-09)

- **Reviewed commit:** `5bb6932`  ·  **Base:** `main` (`origin/main` = `c7d2049`)  ·  **Cycle 1 reviewed:** `68a5f95`
- **Verdict:** approved. No CONFIRMED and no PLAUSIBLE findings.
- **Mode:** scoped to verifying the remediation of cycle 1's F1 and G1, as `code-review` allows on cycle 2+ at the foreman's request. One fresh blind reviewer subagent ran at effort `xhigh`. Its inputs were `GOAL.md` and the rubric, given inline, plus cycle 1's F1 and G1 text (the findings only), the whole-change diff (`git diff origin/main...HEAD -- . ':(exclude)spec/'`) and the remediation diff (`git diff 68a5f95 HEAD -- . ':(exclude)spec/'`). It got nothing from PLAN, TECH, research or the plan's reviews. The sequential fallback did not run.
- **Contract drift (step 1.4):** `GOAL.md` has not changed since cycle 1. Its post-shape commits `5893e1f` and `f39baca` stand as confirmed in cycle 1.
- **Evidence:** the worktree's gitignored `.local/review2/`, cited below as `$E2/…`.
- **Blindness:** the reviewer disclosed one unfiltered `git diff --name-only 68a5f95 HEAD`. It printed three `spec/` file names and no contents, and the reviewer read none of those files.

### Verification run

| Command | Result | Evidence |
|---|---|---|
| `git diff --name-only 68a5f95 HEAD -- . ':(exclude)spec/'` | 9 files, all `issues/*.md` | `$E2/g1-and-scope.out` |
| `git diff --quiet 68a5f95 HEAD -- src tests`, and the same for `AGENTS.md PLAN.md ROADMAP.md tools` | rc=0 both: the remediation changed nothing outside `issues/` | `$E2/g1-and-scope.out`; the orchestrator re-ran both |
| `CI=true tools/lint.sh` | exit 0 | `$E2/gate.out` |
| `CI=true uv run pytest -n auto -m unit -q -p no:cacheprovider` at HEAD | **548 passed**, 0 failed, 0 errors (same as cycle 1) | `$E2/verify-head.out` |
| Hypothesis seeds 1–20 | not re-run: carried from cycle 1, since `src/` and `tests/` are byte-identical to `68a5f95` | identity check above |
| `git grep c7d2049 HEAD -- . ':(exclude)spec/'` | no matches (rc=1) | the orchestrator re-ran it |

The CI workflow bullet of R9 was **not observed** in this pass. `code-publish` reads that run record.

### F1 — remediated

The reviewer checked the property by script over the whole domain. That was every file outside `spec/` that the branch changes. Ten `issues/*.md` files carry line cites; the others have none.

- **Cites:** 266 in all. A raw-grep coverage pass found no cite the script had missed (`$E2/coverage.out`).
- **How each was checked:** against `git show <sha>:<file>` at its pin, or at HEAD when unpinned, and against the symbol the prose names.
- **Scripts:** `$E2/cites.py`, `$E2/mkoverrides.py` and `$E2/verdicts.py`.
- **Outputs:** the table `$E2/cites.tsv`, the summary `$E2/verdicts.out` and the full dump `$E2/dump-all.txt`.

| Verdict | Count |
|---|---|
| pinned, holds | 58 |
| pinned, false | **0** |
| unpinned, holds at HEAD | 180 |
| unpinned, false or doubtful | 28, all pre-existing (below) |

- The twelve cycle-1 pins now name commits where they hold: `141aa86` ×8, `5e0d997` ×3, `0e6d14c` ×1.
  - `source-commit-staged-publish.md:20` now agrees with its own dated record at `:112-113`.
  - The orchestrator spot-checked two pins. `141aa86:overlay.py:870` is `def flush`. `5e0d997:overlay.py:1061` is the confirm before any purge.
- All 13 cite-bearing lines the branch wrote hold.
- The only unpinned cite into a file the branch shifted is the new "line 171" (`Catalog.rename`). It holds: `grep -n 'def rename' src/sosfs/state/catalog.py` gives 171.

**F1's property holds at `5bb6932`.**

**Out of Scope: 28 stale cites that predate the branch.**
- **Where:** they sit in files this branch touches:
  - `invariant-audit-gaps.md`, `rename-to-terrace.md` and `archive-spec-plumbing.md`, mostly cites into root `PLAN.md`;
  - `archive-finalization-integration.md:112`.
- **Why out of Scope:** each one's target text is byte-identical at `origin/main` and HEAD, and none sits on a line this branch wrote or shifted (`$E2/verdicts.out`). So R8 ("every record the change makes untrue") does not reach them, and cycle 1's F1 domain does not either. That domain was the pins this diff added, plus the cites its own line shifts made untrue.
- **Home:** they are recorded here and not filed. Their home in the four-homes table is the foreman's call. A seed committed after this verdict would need `--reviewed-commit` re-pinned before publish.

**Informational, not a finding.** Some dated records hold at the commit they name but give no current location (`$E2/strict.py`, `$E2/strict.out`):
- `audit-command.md:72` and `:78` hold on the `deferred-rename-source-orphan` tree. At HEAD, `Catalog.purge`'s extra line moves `least_recent` to `catalog.py:255` and `add_residency` to `:201`.
- `freethreaded-concurrency-stress.md:86` holds at `d557db2`.
- `source-commit-staged-publish.md:113` holds at its pins.

All of these meet the property as stated, since a pinned cite need only hold at its pin.

### G1 — routed and accurate

- **The line:** `issues/freethreaded-concurrency-stress.md:138-141`, a dated sub-bullet under the reused-id note, next to Q5's line at `:128-137`. It describes `overlay.py:1331-1334` at HEAD (`_vacate`'s `with self.db.transaction(): self._purge(path)`).
- **Accurate:** the mutant still survives. In the clone `$E2/g1-clone` at `5bb6932`, unwrapping the transaction left the full unit suite at **548 passed** (`$E2/g1-mutant.diff`, `$E2/g1-mutant-suite.out`).
  - **Reachability control:** a `raise` in place of the purge gives 5 failures (`$E2/g1-reach-control.out`). The suite does run the branch, so "no test pins it" is true.
  - "Only real threads can reach" holds too: a same-thread injected call unregisters its watch before control returns.
- **No spec ids:** `grep -nE '\b[RPQFGMT][0-9]+\b|spec/'` found no matches in `:87-88`, in `:138-141`, in every added line of the remediation, or in every added `issues/` line of the branch (`$E2/g1-and-scope.out`).

### Requirement → evidence (this pass)

| R-ID | This pass | Verified by |
|---|---|---|
| R1–R7 | carried from cycle 1 ✅ | `src/` and `tests/` are byte-identical to `68a5f95` |
| R8, the `issues/` records | re-verified ✅ | reviewer: F1 and G1 above, and Q5's line in place |
| R8, the counts | re-verified ✅ | reviewer: `AGENTS.md` says 548 / 10 / 558 / 42 files and 210 engine tests, matching the measured tree |
| R8, the rest (`AGENTS.md`, root `PLAN.md`, docstrings) | carried from cycle 1 ✅ | byte-identical |
| R8, last bullet | n/a yet | waits for the roadmap step |
| R9, lint and the unit gate | re-verified ✅ | reviewer: lint exit 0; 548 passed, more than 530 |
| R9, seeds 1–20 | carried ✅ | `src/` and `tests/` identical |
| R9, CI jobs | **not observed** | `code-publish` |

No R-ID is met by a committed `spec/` artifact, so the orchestrator graded none separately. No change in the remediation maps to no R-ID: all of it is R8, by way of F1 and G1.

### Findings

None. No CONFIRMED, no PLAUSIBLE, no new coverage observation.

### Human-gate triggers

- **From findings:** none.
- **Unconditional (GOAL Q6):** Geoffrey signs off on the final engine diff whatever the review finds. That gate still holds and has not been recorded as given. It must be recorded here before publish.
- **Q6 sign-off, given.** Geoffrey signed off on the engine diff (`git diff origin/main...49670b4 -- src`),
  reviewed at `5bb6932`, and approved publishing squash-local to `main`. (Geoffrey, 2026-11-09, in the foreman's
  session)

### Tree state on hand-back

- `git status --porcelain` was empty, and the orchestrator re-ran it.
- The reviewer mutated no ignored build state in the worktree: its mutant and control ran only in its gitignored clone.
