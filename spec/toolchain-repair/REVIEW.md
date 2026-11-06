# REVIEW — Repair the toolchain so the verify gate runs on a fresh machine

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent; where the runtime
> has none, `code-review`'s sequential fallback). The correctness pass grades the branch diff against
> [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** e7a995fdf68e437f8568f1b70786abadeb396eb1  ·  **Base:** main (`origin/main` 6a046b4)  ·  **Date:** 2026-11-05
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** a fresh blind subagent (effort xhigh) reviewed the full spec-excluded diff. The sequential fallback did not run. The reviewer had `GOAL.md` and the rubric as byte-exact copies in the scratchpad, read `AGENTS.md` from the worktree, and saw the diff through `git diff origin/main...HEAD -- . ':(exclude)spec/'`.

### Contract drift (step 1.4)

`git log --oneline origin/main..HEAD -- spec/toolchain-repair/GOAL.md` shows two commits after the
shape commit `feffd6a`. Both were confirmed before grading.

- `9429db5` [fix] Clarify toolchain-repair R9 grading: the foreman's rulings at the GOAL gate.
- `6a4e867` [fix] Amend toolchain-repair R9 at the plan gate: Geoffrey narrowed R9 to tolerate
  `tests/engine/test_model.py::TestOverlayModel::runTest` as a unit job's only failure. It is recorded in
  the GOAL's Clarifications.

## Verification run

All commands ran on macOS arm64 with CPython 3.15.2 and uv 0.13.6, inside the sandbox; none needed to
run outside it. "Fresh" means a `git clone` of the worktree at `e7a995f`, a new `UV_CACHE_DIR` and no
`.venv`. "No libfuse" means `FUSE_LIBRARY_PATH=/nonexistent/libfuse.dylib`, under which
`import mfusepy` raises `OSError`.

**Setup and verify in the worktree**

- `uv sync` → exit 0. `git diff --exit-code uv.lock` → 0.
- `tools/lint.sh && uv run pytest -n auto -m unit`, with libfuse (macFUSE) → lint clean; **449
  passed**, 0 failed, 0 skipped, 0 errors.
- The same with no libfuse → lint clean; **405 passed, 44 skipped**, 0 failed, 0 errors. All 44
  skips are in `tests/fuse` (27 in `test_adapter.py`, 17 in `test_mount.py`), each with the reason
  "libfuse cannot be loaded: …".
- `pytest -m unit tests/fuse -rs`, with libfuse → 44 passed, 0 skipped.
- `pytest -m unit tests/tier -rs` → 127 passed, 0 skipped.

**Fresh machine**

- `uv sync` → exit 0. 46 packages, no pydantic. `uv.lock` unchanged and `git status --porcelain`
  empty.
- Verify with no libfuse → exit 0; **405 passed, 44 skipped**, 0 failed, 0 errors.
- `uv run pytest -n auto -m 'not integration' -rfEs` with no libfuse → 405 passed, 44 skipped (all in
  `tests/fuse`), 0 failed.
- `pytest tests/fuse` with no `-m` and no libfuse → 51 skipped, all with the libfuse reason, 0 errors.
- **Verify with `CI=true`** (Hypothesis's CI profile, which uses a fixed seed) → **exit 1; 1 failed,
  404 passed, 44 skipped.** The failure is `tests/engine/test_model.py::TestOverlayModel::runTest`,
  with two distinct failures: `PermissionError [Errno 13]` on `project/d2 -> project/d1/s1`, and a
  stray `d1` left on the source.
- `test_model.py` alone, 12 runs with random seeds and no example database → 2 failed, 10 passed.

**R5: a scratch `-p` plugin that makes `boto3`, `moto.server`, `moto` or `flask` unimportable**

- `tests/tier -m unit` → 91 passed, 36 skipped (`test_s3` 21, `test_s3_engine` 8, `test_archive` 7).
  Each skip reason names the blocked package.
- `test_posix.py` → 23 of 23 passed under every block.

**R7: temp dir with group `wheel` (0); the process's group is `staff` (20)**

- The HEAD test passes.
- Control: `origin/main`'s version of the test fails there, `assert (501, 0) == (501, 20)`.
- Negative control: a mutant that forces the file's group to the process gid makes the HEAD test fail,
  so the new assertion is not hollow.

**Collected test IDs, base `6a046b4` versus HEAD**

- 459 in total and 449 `unit`, identical at both commits; 44 `tests/fuse` unit tests at each.
- No test was removed or deselected.

**Product code and source ids**

- `git diff --stat origin/main...HEAD -- src/` → empty.
- `uv tree --no-dev` → runtime dependencies unchanged; the `cffi` and `markupsafe` bumps are dev-only.
- Grep of the added lines for `R#`/`P#` spec ids → no match (exit 1).

**CI: run 42059324110**

The run was a push of `0ab60a6` (`fix/toolchain-repair`). It was read from the saved `gh run view
--json` record and the per-job logs, not from GitHub directly.
`git diff --stat 0ab60a6 HEAD -- . ':(exclude)spec/'` → empty, so the pushed tree is the reviewed tree
outside `spec/`. The orchestrator re-read each job's summary line:

| Job | Conclusion | Summary line | `tests/fuse` |
|---|---|---|---|
| `Lint & build` | success | (no tests) | n/a |
| `Unit (Linux, 3.15)` (127356300742) | failure | `1 failed, 448 passed`; only failure `test_model…runTest` | "Assert libfuse loads" passed; 0 skips |
| `Unit (Linux, 3.15t)` (127356300549) | failure | `1 failed, 448 passed`; only failure `test_model…runTest` | "Assert libfuse loads" passed; 0 skips |
| `Unit (macOS, engine-only)` (127356300109) | failure | **`1 failed, 404 passed, 44 skipped`**; only failure `test_model…runTest` | 44 skips, "Unable to find libfuse" |

**Main run 42023846602 at `6a046b4`**

The saved record was read only to check the prose claims that cite it. Its macOS job (127233016755)
reads `FAILED tests/engine/test_model.py::TestOverlayModel::runTest - AssertionError: assert {'d1':
'DIR',…} == {'d2': 'DIR',…}` and `1 failed, 404 passed, 3 errors`, so `test_model` failed on macOS
there too.

**Gates not observed**

None was left unobserved. This step has no live CI access, so R9 rests on the saved run record above.

## Requirement → evidence matrix

All nine R-IDs were graded by the blind reviewer. None is a `spec/` artifact deliverable; the
cross-check against `TECH.md` agrees (every phase's `satisfies` names code R-IDs, and no artifact flag
is set). The orchestrator re-read the CI evidence for R9 and for F1 and F2.

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `pyproject.toml` dev group (`moto[server]` → `moto[s3]` + boto3 + flask + flask-cors); `uv.lock` (pydantic chain gone) | Fresh-cache, no-`.venv` `uv sync`: exit 0, lock unchanged. CI Linux `uv sync --locked`, no cache, OK | ✅ |
| R2 | `tests/fuse/conftest.py`, `tests/fuse/test_adapter.py`, `tests/tier/conftest.py`, `tests/tier/test_posix.py` | Fresh verify, no libfuse: exit 0, 405 passed, 0 failed, 0 errors. With `CI=true`: exit 1, 1 failed. macOS CI: 1 failed | ❌ partial (F1) |
| R3 | `tests/fuse/conftest.py` probe + `pytest_collection_modifyitems`; guarded imports in `test_adapter.py` | No libfuse: 44/44 unit (51/51 all markers) skipped with the libfuse reason, 0 errors. macOS CI the same | ✅ |
| R4 | same files, plus CI's "Assert libfuse loads" step | macFUSE: 44 passed, 0 skipped, IDs identical to base. Linux CI: 0 skips | ✅ |
| R5 | `tests/tier/conftest.py` guarded import + fixture skip | Import-blocking plugin: 36 S3 tests skipped naming the package; `test_posix` 23/23 passed | ✅ |
| R6 | `pyproject.toml`, `uv.lock` | Worktree `tests/tier`: 127 passed, 0 skipped. Fresh env: no skips outside `tests/fuse`. Linux CI: 0 skips | ✅ |
| R7 | `tests/tier/test_posix.py:53-62` | Passes in a `wheel`-group directory; base fails there; kind/size/mode/mtime asserts unchanged; gid mutant caught | ✅ |
| R8 | as R2 | Fresh env, no libfuse: 405 passed, 0 failed outside `tests/fuse`. `CI=true` and macOS CI: 404 passed, 1 failed (`test_model`) | ❌ partial (F1) |
| R9 | `.github/workflows/ci.yml` (push trigger `'*/**'`, `uv sync --locked`, libfuse assertion, `-rfEs`) | Run 42059324110, table above; pushed tree = reviewed tree | ✅ (as amended) |

Unmapped changes (possible scope creep):

- `AGENTS.md:740`, the § Factory profile's `branch commits` row, now reads: branch commits carry no
  trailer, and the landing commit carries `Co-Authored-By`. No R-ID asks for this.
  - It is a § Factory change, which the profile's own categories file as `[factory]`, riding in a
    `[fix]` cycle.
  - It matches this branch's commits.
  - Reported, not blocking.
- The `ci` row edit (CI runs on cycle branches) maps to the R9 Clarification. Everything else maps to
  an R-ID or a Clarification.

## Findings

Severity: **CRITICAL** (a violation of any numbered section of § Invariants is auto-CRITICAL,
**including lettered subsections**; a § Conventions violation is **HIGH**) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `code-build`.

No § Invariants section is violated: `src/` is untouched, and so are `tests/engine/test_model.py` and
`tests/engine/test_replay.py`.

### [HIGH/CONFIRMED] F1: R2 and R8 are not reliably met on a fresh Mac; `test_model` fails there too

- **Where:** `tests/engine/test_model.py` (`TestOverlayModel::runTest`), exercising
  `engine/overlay.py` `_execute_namespace` → `tier/posix.py` `rename`. The diff touches neither file.
- **Failure scenario:** on a fresh macOS machine without libfuse, the profile's `verify` reports
  `1 failed, 404 passed, 44 skipped` and exits 1:
  - every time under Hypothesis's CI profile (`CI=true`);
  - on the macOS CI runner, in this cycle's run and in `main`'s;
  - in about 1 of 6 local runs with random seeds.
  R2 requires 0 failed, and R8 requires 0 failed outside `tests/fuse`, so a passing local run is
  luck. R9's amendment rests on the premise that the defect shows only on Linux. Read against that
  premise, the R9 Clarification left R2 and R8 untouched; the evidence says the premise is false.
- **Evidence:**
  - Fresh clone, `CI=true tools/lint.sh && uv run pytest -n auto -m unit`, no libfuse → exit 1,
    `1 failed, 404 passed, 44 skipped`. Both distinct failures appear: `PermissionError [Errno 13]`
    on `project/d2 -> project/d1/s1`, and a stray `d1`.
  - 12 random-seed runs of `test_model.py` → 2 failed.
  - CI 42059324110, job 127356300109 → `1 failed, 404 passed, 44 skipped`.
  - CI 42023846602, job 127233016755 → `1 failed, 404 passed, 3 errors`, `test_model` failing.
- **Touches invariant / requirement:** R2, R8, and the premise of the R9 amendment.
- **Routing:** **a build loop cannot close this.** The cause is the pre-existing namespace-replay
  defect. The GOAL forbids product changes, and `test_model.py` may not be touched. Closing it needs
  Geoffrey's ruling on the contract. Two examples:
  - extend R9's `test_model` exception to R2 and R8;
  - pull the `deferred-rename-source-orphan` fix into scope.
  Either way, the property is: the profile's verify on a fresh Mac reports 0 failures other than
  those the contract explicitly tolerates, under the CI Hypothesis profile as well as random seeds.

### [MEDIUM/CONFIRMED] F2: the seed and the ROADMAP say the `test_model` defect does not show on macOS; both CI runs and the Mac show it does

- **Where:**
  - `issues/deferred-rename-source-orphan.md:91-92`: says run 42023846602 failed only the two Linux
    jobs on `test_model`.
  - `issues/deferred-rename-source-orphan.md:109`: "Neither shows on the macOS runner or the
    development Mac."
  - `ROADMAP.md:66-69`: "failed both Linux unit jobs on `test_model.py` alone … It did not show on
    macOS."
- **Failure scenario:** the next cycle takes the fix, under a human gate, and is told the bug cannot
  be reproduced on a Mac. It may chase a Linux-specific cause, or decide it cannot reproduce locally.
- **Evidence:**
  - CI 42023846602, macOS job 127233016755 → `FAILED …test_model…runTest - AssertionError: assert
    {'d1': 'DIR',…} == {'d2': 'DIR',…}`.
  - CI 42059324110, macOS job 127356300109 → `FAILED …test_model…runTest - ExceptionGroup: Hypothesis
    found 2 distinct failures`.
  - Local `CI=true` → both failures reproduce.
- **Touches invariant / requirement:** seed prose in the graded diff (rubric, "The spec/ exclusion
  bounds the artifacts…").
- **Routing (property):** every claim in `issues/` and `ROADMAP.md` about where and how the
  `test_model` defect reproduces must match the run records: both CI runs, every job, and the Mac
  under `CI=true` and under random seeds.

### [MEDIUM/CONFIRMED] F3: `-rfEs` output is described as "one line per skip reason"; pytest prints one line per skipped test location

- **Where:** `AGENTS.md:88` ("one `-rfEs` line per skip reason"; also "skipped by name") and
  `.github/workflows/ci.yml:48` ("adds one line per skip reason").
- **Failure scenario:** a reader of `AGENTS.md` or the workflow expects one summary line per cause.
  The macOS job prints 44 `SKIPPED [1] tests/fuse/<file>:<line>: …` lines for a single reason, and
  the lines give `file:line`, not test names. The operating manual misdescribes a gate's output.
- **Evidence:** in macOS job 127356300109, `grep -c 'SKIPPED \['` → 44. Grouped by reason after
  stripping line numbers: 27 lines in `test_adapter.py` and 17 in `test_mount.py`, all reading
  "libfuse cannot be loaded: Unable to find libfuse". The local run matches.
- **Touches invariant / requirement:** operating-manual drift (rubric Scope 5). It falls under
  `AGENTS.md` § Verification prose, not § Invariants or a file-path-keyed gate list, so it is MEDIUM.
- **Routing (property):** every description of what the CI unit step prints, in `AGENTS.md` and in
  `ci.yml` comments, matches pytest's actual `-rfEs` output.

## Human-gate triggers

**Triggered: F1 (CONFIRMED, HIGH).**

- The defect behind F1 lives in `tests/engine/test_model.py` (the `tests/` row) and in
  `engine/overlay.py` `_execute_namespace` (deferred-op replay). Both are on `AGENTS.md` § Invariants
  → *High-blast-radius paths (human gate)*.
- No change the GOAL permits can close F1, so it needs Geoffrey's ruling on R2 and R8 before any
  further step.
- The gate holds until publish.

F2 and F3 are prose findings in `issues/`, `ROADMAP.md`, `AGENTS.md` and `ci.yml`. They are not on
the list.

## Reviewer conduct

- `git status --porcelain` was empty on hand-back; the orchestrator re-checked it.
- The reviewer created two git-ignored artifacts in the worktree (`.pytest_cache/` and one
  `.hypothesis/constants/` file). It moved both to the scratchpad, verified that nothing newer than
  the session start remains under `.hypothesis`, `.pyc` or `.ruff_cache`, and confirmed no
  `.hypothesis/examples` directory exists.
- `.venv` was not modified.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Review cycle 2 — approved (2026-11-05)

- **Reviewed commit:** 5d058f800f9c8ee5e7b4370e5102fbb4d0f660d1  ·  **Base:** main (`origin/main` 6a046b4)
- **Verdict:** approved. No CONFIRMED and no PLAUSIBLE findings.
- **Cycle:** 2 of ≤3.
- **Mode: scoped to verifying the remediation of F1, F2 and F3** (Geoffrey's choice at the review
  gate), not a fresh full pass. A fresh blind subagent (effort xhigh) was asked to:
  - confirm each fix by executed evidence, against the finding's routing property;
  - check that the remediation diff `git diff e7a995f HEAD -- . ':(exclude)spec/'`, read with the
    full spec-excluded diff for context, adds no defect and weakens nothing;
  - re-grade R2, R8 and R9 against the amended GOAL.

  Its inputs were byte-exact scratchpad copies of `GOAL.md` (at HEAD) and the rubric, and
  `AGENTS.md` read from the worktree. It was not shown PLAN, TECH, `verify/` or this file. R1 and
  R3–R7 stand as graded in cycle 1; the reviewer saw no regression in them. The sequential fallback
  did not run.

### Contract drift (step 1.4)

GOAL changed twice since cycle 1. Both were pre-confirmed by Geoffrey's ruling on F1.

- `2975183` [fix] Amend toolchain-repair R2/R8 at the review gate. R2 and R8 take R9's exception:
  `tests/engine/test_model.py::TestOverlayModel::runTest` may be the only failure, it counts toward
  R8's floor of 405 whether it passes or fails, and everything else stands. A new Clarification
  records the ruling ("resolved 2026-11-05, Geoffrey at the review gate").
- `2b941e7` [fix] Align toolchain-repair GOAL vision with the amended R-IDs. The Outcome/vision text
  now names the same one-test exception.

F1's remediation is in `spec/toolchain-repair/verify/toolchain_gate.py`, the cycle's own gate. The
blind reviewer cannot see it, so it graded F1 against the contract: the profile's `verify` and R8's
command, run by hand.

### Verification run

All on macOS arm64 inside the sandbox; nothing needed to run outside it. "Fresh" means a `git clone`
of HEAD with a new `UV_CACHE_DIR` and no `.venv`. "No libfuse" means
`FUSE_LIBRARY_PATH=/nonexistent/libfuse.dylib`. The reviewer's logs are in the session scratchpad
under `review-toolchain-repair-c2/`.

**R2 and R8, fresh clone, no libfuse**

- `uv sync` → exit 0; `uv.lock` unchanged.
- `CI=true tools/lint.sh && uv run pytest -n auto -m unit` → lint exit 0 ("lint: all checks
  passed"); **`1 failed, 404 passed, 44 skipped`**. The only FAILED line is `test_model…runTest`
  ("Hypothesis found 2 distinct failures").
- The same verify with random seeds, 4 runs → **`405 passed, 44 skipped`** each time.
- R8, `uv run pytest -n auto -m 'not integration'`:
  - under `CI=true` → `1 failed, 404 passed, 44 skipped`, so 404 plus the tolerated test is 405
    outside `tests/fuse`;
  - with random seeds → 405 passed.
  - All 44 skips are in `tests/fuse`, each with the libfuse reason.
- Negative control, in the clone only: an injected failure in `tests/tier/test_posix.py::test_put_replaces`
  under `CI=true` → `2 failed, 403 passed, 44 skipped`. The tolerated test does not hide a second
  failure.
- Random-seed runs of `test_model.py` alone sometimes pass and sometimes fail, which the amended
  R2/R8 permit.

**F3: what `-rfEs` prints**

- Local no-libfuse runs, serial and `-n auto` → one `SKIPPED [1] tests/fuse/<file>:<line>: libfuse
  cannot be loaded: …` line per skipped test.
- Folding control: a probe file in the clone with three tests at one location, all skipped →
  `SKIPPED [3] tests/fuse/test_zz_fold_probe.py:6: …`. pytest folds by (file, line, reason), which
  matches the new wording.
- CI macOS log 127661445678 → `grep -c 'SKIPPED \[1\]'` = 44, and no line folds more than one test.
  That matches "no two `tests/fuse` tests share a location today".

**F2: seed and ROADMAP claims**

- **Reviewer, on the development Mac:**
  - failure under `CI=true` on every run;
  - which failures appear in the full verify (both) and in `test_model.py` alone;
  - failure on some random seeds.
  - Every `file:line` the seed cites checks out at HEAD.
  - Not reproduced: the seed's historical "one full-suite run found five distinct failures". This
    is unverified, not refuted; it is a past observation and no gate depends on it.
- **Orchestrator, against the saved run records** (the reviewer was fenced to run 42147199752):
  - 42023846602 at `6a046b4`:
    - Both Linux jobs show `1 failed, 448 passed`, with `test_model` alone ("2 distinct
      failures").
    - macOS job 127233016755 shows `1 failed, 404 passed, 3 errors`. The failure is `test_model`,
      `AssertionError: assert {'d1': 'DIR',…} == {'d2': 'DIR',…}`; that is failure 2, and the log
      has no `PermissionError`. The 3 errors are `ERROR tests/fuse - OSError: Unable to find
      libfuse`.
  - 42059324110 at `0ab60a6`: all three unit jobs fail on `test_model` alone with "2 distinct
    failures" (Linux `1 failed, 448 passed`; macOS `1 failed, 404 passed, 44 skipped`).
  - `@reproduce_failure` blobs appear in every unit-job log of both runs.
  - The new seed and ROADMAP text matches all of it.

**R9: CI run 42147199752** (push of `1864e12`, `origin/fix/toolchain-repair`)

The run was read from the saved `gh run view --json` record and the job logs. Both the reviewer and
the orchestrator read them.

`git diff --stat 1864e12 HEAD -- . ':(exclude)spec/'` → empty, so the pushed tree is the reviewed
tree outside `spec/`.

| Job | Conclusion | Summary line | Only FAILED line |
|---|---|---|---|
| `Lint & build` (127661445979) | success | (no tests) | n/a |
| `Unit (Linux, 3.15)` (127661446203) | failure | `1 failed, 448 passed` | `test_model…runTest`; "Assert libfuse loads" succeeded |
| `Unit (Linux, 3.15t)` (127661445967) | failure | `1 failed, 448 passed` | `test_model…runTest`; "Assert libfuse loads" succeeded |
| `Unit (macOS, engine-only)` (127661445678) | failure | `1 failed, 404 passed, 44 skipped` | `test_model…runTest` |

**Gates not observed:** none. This step has no live CI access, so R9 rests on the saved run record.

### Cycle 1 findings

| Finding | Status | Evidence |
|---|---|---|
| F1 (HIGH) R2/R8 not met on a fresh Mac | **Resolved**: contract amended (`2975183`); verify meets the amended R2/R8 | `CI=true`: `1 failed (test_model only), 404 passed, 44 skipped`, lint 0. Random seeds: 405 passed. Negative control: a second failure is reported |
| F2 (MEDIUM) seed/ROADMAP said macOS is spared | **Fixed** | `issues/deferred-rename-source-orphan.md` and `ROADMAP.md` now match all three run records and the Mac (see F2 above) |
| F3 (MEDIUM) `-rfEs` described per reason | **Fixed** | `AGENTS.md:88-90` and `.github/workflows/ci.yml:48-49` match the local output, the folding control and the CI macOS log |

### Requirement → evidence matrix (cycle 2)

| R-ID | Implemented by | Verified how (by whom) | Status |
|------|----------------|------------------------|--------|
| R1, R3–R7 | as cycle 1 | cycle 1; no regression seen in cycle 2 (reviewer) | ✅ (cycle 1) |
| R2 (amended) | `tests/fuse/conftest.py`, `tests/fuse/test_adapter.py`, `tests/tier/*` | Fresh verify, no libfuse: lint 0, 0 errors, nonzero passed. `CI=true`: only `test_model` fails. Random: 0 failed (reviewer) | ✅ |
| R8 (amended) | as R2 | `CI=true`: 404 + the tolerated test = 405, no other failure outside `tests/fuse`. Random: 405 passed (reviewer) | ✅ |
| R9 | `.github/workflows/ci.yml` | Run 42147199752, table above; pushed tree = reviewed tree (reviewer and orchestrator) | ✅ |

None of the R-IDs is a `spec/` artifact deliverable.

### Findings

None, CONFIRMED or PLAUSIBLE. The remediation diff weakens nothing: it changes prose only, in
`ci.yml`, `AGENTS.md`, `ROADMAP.md` and the seed. No test, marker, gate or § Invariants text is
narrowed.

**Observations, not findings**, routed to the `deferred-rename-source-orphan` cycle that owns this
test:

- One random seed hit a third failure class in `test_model…runTest`: `OSError(66, 'Directory not
  empty')` via `_vacate` (`src/sosfs/engine/overlay.py:994`), on `mkdir /d1, mkdir /d1/s1, flush,
  rmdir /d1/s1, rmdir /d1, mkdir /d1`. It belongs to the same deferred-replay family. That cycle's
  shaping should fold it into the seed's reproduction list. No new seed: the existing one owns the
  test.
- Under load, a local `CI=true` run stopped shrinking at Hypothesis's 5-minute limit and reported a
  different falsifying example for failure 1, of the same type and origin.

### Scope creep (triaged)

- `AGENTS.md:549-551` (§ Conventions): the `Co-Authored-By` trailer rule now covers only commits
  that land on `main`. This removes a contradiction with the § Factory `branch commits` row. It
  maps to no R-ID.
- **Foreman triage: accepted, not a finding.** It is the trailer ruling made at P1, folded in at
  remediation, and Geoffrey saw it reported in cycle 1's unmapped-changes list.

### Human-gate triggers

- **This cycle:** none. There is no CONFIRMED finding.
- **Cycle 1 trigger (F1, high-blast-radius `tests/engine/test_model.py` / `engine/overlay.py`):
  signed off.** Geoffrey ruled at the review gate to extend the exception to R2/R8, fix F2 and F3,
  re-review only those fixes, and land. The ruling is recorded in the GOAL Clarification dated
  2026-11-05, "Geoffrey at the review gate". The gate is satisfied for publish.

### Reviewer conduct

- `git status --porcelain` was empty on hand-back; the orchestrator re-checked it.
- All runs used throwaway clones in the scratchpad, and probes and negative controls were built only
  there. Nothing in the worktree was modified, including ignored state: no `.hypothesis/examples`
  exists, and `.hypothesis/constants` predates the pass.
- Everything ran sandboxed. No hang was observed.
