---
slug: toolchain-repair
title: Repair the toolchain so the verify gate runs on a fresh machine
kind: fix
appetite: small
status: done
branch: fix/toolchain-repair
base: main
current_phase: done
last_updated: '2026-11-05'
phases:
- id: P1
  name: Dev group installs from wheels on 3.15
  status: done
  satisfies:
  - R1
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/toolchain-repair/verify/toolchain_gate.py deps
- id: P2
  name: Tier tests survive a missing moto server and a foreign group
  status: done
  satisfies:
  - R5
  - R7
  depends_on:
  - P1
  parallel: true
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/toolchain-repair/verify/toolchain_gate.py tier
- id: P3
  name: FUSE tests skip by name where libfuse cannot be loaded
  status: done
  satisfies:
  - R3
  - R4
  depends_on:
  - P1
  parallel: true
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/toolchain-repair/verify/toolchain_gate.py fuse
- id: P4
  name: The profile's verify runs clean on the Mac
  status: done
  satisfies:
  - R2
  - R8
  depends_on:
  - P2
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/toolchain-repair/verify/toolchain_gate.py suite
- id: P5
  name: CI runs on cycle branches and is read from a run
  status: done
  satisfies:
  - R9
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/toolchain-repair/verify/toolchain_gate.py ci ledger claims
review:
  last_reviewed_commit: 5d058f800f9c8ee5e7b4370e5102fbb4d0f660d1
  verdict: approved
  blocked_reason: ''
  cycle: 2
---

# TECH.md — Repair the toolchain so the verify gate runs on a fresh machine

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/toolchain-repair/TECH.md`); the
per-phase checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`,
and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Gates:** [`verify/toolchain_gate.py`](verify/toolchain_gate.py), one mode per phase (PLAN § 6).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `code-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- `branch`, `base`: the cycle's branch (prefix per GOAL `kind`, from the profile's `branches`) and the
  profile's `base`.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `code-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (docs, tests, isolated leaf
  modules). Any phase touching the profile's `coupled core` is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3.
- `verify`: the exact command that proves the phase. A gate carrying `$( )` or quoting the calling
  shell would mangle is set with `set_phase.py --verify-file PATH`, not `--verify`.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from the repo's `AGENTS.md` — it
  is the constitution. Walk the sections of its § Invariants that this change touches; that is the
  curated footgun checklist.
- One phase per `code-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. The branch commit subject is code-build's (`$KB/skill/code-build.md` →
  Step 6), not restated here; branch commits are squashed away at `code-publish` per the profile's
  `landing`. Trailers follow the profile's `branch commits`.
- The last phase carries the standing last-phase items: the deferral ledger (`code-plan` Step 6.8),
  and every item the previous cycle's last phase carried for a project-level obligation. This is the
  repo's first factory cycle, so there is no previous last phase to inherit from.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

**Where the gates run.** The agent sandbox on the development Mac is not known to block any mode. The
planning-time hang of cross-parent directory renames and of a fresh-cache `uv sync` was a transient
machine-wide kernel stall that hung the same renames outside the sandbox too, not the sandbox
(PLAN § 5); P4 ran `suite` sandboxed in about 9 s. Running `deps`, `suite` or `prepush` outside the
sandbox stays approved if one is needed. Every mode refuses to run under `UV_FROZEN`, `UV_LOCKED`,
`UV_NO_SYNC` or `UV_OFFLINE`: unset them for the gate run.

---

## Phase P1 — Dev group installs from wheels on 3.15
**Satisfies:** R1, R6 · **Depends on:** —
**Goal:** a fresh-cache `uv sync` on Python 3.15 final exits 0 without building pydantic-core, leaves
`uv.lock` untouched, and the S3 tier tests still run against the in-process moto server.

- [x] In `pyproject.toml`'s `dev` group, replace `"moto[server]>=5"` with `"boto3"`, `"flask"`,
  `"flask-cors"`, `"moto[s3]>=5"` (PLAN § 2.1). A trailing comment per new entry may say what needs
  it; keep the voice of § Prose and comments.
- [x] `uv lock`, then `uv lock --upgrade-package cffi --upgrade-package markupsafe`. Expected: 35
  packages removed (pydantic, pydantic-core, cfn-lint, openapi-spec-validator and their trees), none
  added; `cffi` 2.0.0 → 2.1.1 and `markupsafe` 3.0.3 → 3.0.4; moto stays 5.2.2. A different outcome
  is a reason to stop and re-read PLAN § 2.1, not to accept it.
- [x] Confirm `uv.lock` names no `pydantic-core` and that its only sdist-only packages are the project
  and `pyyaml`.
- **Verify:** `toolchain_gate.py deps` (outside the sandbox; see Conventions). It needs a Python 3.15
  **final** interpreter and exits 3 without one.
- **Touches:** `pyproject.toml`, `uv.lock`. No `AGENTS.md` line names the dev group's contents.

## Phase P2 — Tier tests survive a missing moto server and a foreign group
**Satisfies:** R5, R7 · **Depends on:** P1
**Goal:** `tests/tier` collects without `boto3` or `moto.server`, reporting the S3 tests skipped by
name; `test_put_and_stat_applies_meta` passes in a directory whose group is not the process's.

- [x] `tests/tier/conftest.py`: move `import boto3` and `from moto.server import ThreadedMotoServer`
  into `try`/`except ImportError as error` at module scope; record one reason string that names
  `error.name`; `moto_endpoint` calls `skip(reason)` first when it is set (PLAN § 2.2). Catch
  `ImportError` only.
- [x] `tests/tier/test_posix.py::test_put_and_stat_applies_meta`: keep the kind/size/mode/mtime
  assertion as it stands; assert `(result.uid, result.gid) == (os.getuid(), reference.stat().st_gid)`
  where `reference` is a file the test creates with `touch()` in `tier.root` (PLAN § 2.3). One comment
  says why the group is not `os.getgid()`; update the docstring so it no longer claims less than the
  test checks.
- [x] The reason string is `f'S3 tier tests need {error.name}: {error}'`. The gate checks the
  `need <module>` slot, so it must name no package of its own.
- [x] R7's teeth are in the gate. `tier` loads two probe plugins, one with a wrong gid and one with a
  wrong mode and mtime, and requires the test to fail under each. Code-build Step 4's fail-once
  mutation can target those probes rather than an edit under `src/`.
- **Verify:** `toolchain_gate.py tier`. Dry-run green on the planning prototype. The unchanged tree is
  red on R7, and so are a tautological oracle and a test with its metadata assertions deleted.
- **Touches:** `tests/tier/conftest.py`, `tests/tier/test_posix.py`. Nothing under `src/`.

## Phase P3 — FUSE tests skip by name where libfuse cannot be loaded
**Satisfies:** R3, R4 · **Depends on:** P1
**Goal:** on a host where `import mfusepy` raises `OSError`, every selected test under `tests/fuse` is
reported skipped with a reason naming libfuse and nothing errors at collection; where it loads, the
package runs exactly as today.

- [x] `tests/fuse/conftest.py`: probe `import mfusepy` in `try`/`except OSError` at module scope; the
  reason starts `libfuse cannot be loaded:`. Add `pytest_collection_modifyitems` that, when the probe
  failed, prepends `mark.skipif(True, reason=…)` (`add_marker(..., append=False)`) to each item under
  this conftest's directory and to no other (PLAN § 2.4). The gate runs `test_posix.py` next to
  `tests/fuse` to catch a hook that reaches further. Move the `OverlayOperations` import into the
  `operations` fixture, with a `TYPE_CHECKING` import for the annotation.
- [x] `tests/fuse/test_adapter.py`: put the `mfusepy.ENOATTR` and `sosfs.fuse.adapter` imports inside
  `try`/`except OSError: pass`, with a one-line comment naming the conftest as what skips the module's
  tests.
- [x] R4 is graded on CI's Linux jobs (P5). Locally the gate was planned to take its R3 branch.
  - 2026-11-05 amendment: macFUSE is now installed on the development Mac and libfuse loads, so the
    `fuse` gate as written takes its R4 branch there (44 passed, 0 skipped). The R3 branch was run
    as well, with `FUSE_LIBRARY_PATH` naming a missing file, so mfusepy's `dlopen` raises `OSError`
    (44 / 44 / 51 skipped, every reason naming libfuse). CI's Linux jobs still grade R4 and its
    macOS job R3 (P5). Fail-once: red with the marker appended, with the hook unfiltered, and with
    the skip applied where libfuse loads.
- **Verify:** `toolchain_gate.py fuse`. Dry-run green on the planning prototype (44 / 44 / 51
  skipped, all naming libfuse); the unchanged tree is red.
- **Touches:** `tests/fuse/conftest.py`, `tests/fuse/test_adapter.py`. Nothing under `src/`.

## Phase P4 — The profile's verify runs clean on the Mac
**Satisfies:** R2, R8 · **Depends on:** P2, P3
**Goal:** with P2 and P3 together, the profile's `verify` exits 0 with a nonzero pass count and no
failure or error, and `-m 'not integration'` passes at least 405 outside `tests/fuse`.
- 2026-11-05 (review cycle 1, F1): R2 and R8 as amended at the review gate let
  `tests/engine/test_model.py::TestOverlayModel::runTest` pass or fail. "No failure" now reads "no
  failure but that test", and the exit code is 1 when it fails. Reopened for the gate below.

- [x] Run the gate; if a test outside the GOAL's four findings fails, stop and report it rather than
  fix it here (it would be a new finding, outside this GOAL).
- [x] Record the measured counts (unit passed/skipped; `not integration` passed outside
  `tests/fuse`) in the phase commit body. `AGENTS.md`'s figures (449 unit, 459 total) stay true as
  collected counts; change them only if the run shows otherwise.
  - 2026-11-05 amendment: libfuse loads on the development Mac (macFUSE), so the gate as written
    runs the 44 `tests/fuse` unit tests for real and does not show R2's stated condition, a Mac
    without libfuse. The gate was run a second time with `FUSE_LIBRARY_PATH` exported naming a
    missing file, so `import mfusepy` raises `OSError` as it does without libfuse (P3's method).
    Both runs green: with libfuse, `-m unit` 449 passed, 0 skipped; without, 405 passed, 44
    skipped, every skip under `tests/fuse` and naming libfuse. `-m 'not integration'` passed 405
    outside `tests/fuse` in both. `AGENTS.md`'s 449 holds, so it is unchanged. Both runs went
    inside the agent sandbox, where the planning-time hang did not recur. Fail-once: red at 404
    with `test_put_and_stat_applies_meta` deselected through `PYTEST_ADDOPTS`.
- [x] Review cycle 1, F1: `suite` grades R2 and R8 as amended. `tools/lint.sh` exits 0. Each pytest
  run reports 0 errors and no failed test but `CI_TOLERATED`, the constant `ci` already uses; that
  test must run once, may pass or fail, and the run's exit code must be 1 exactly when it failed.
  `-m unit` passes > 0. `-m 'not integration'` passes, plus that test if it failed, ≥ 405 outside
  `tests/fuse`. The test is matched by JUnit classname and name, never by `file`, which points into
  Hypothesis's `stateful.py` (P4's finding). Neither PLAN nor the profile freezes `verify/`, so the
  gate is amended in place. Nothing under `src/` or `tests/` changes.
  - 2026-11-05 remediation run, inside the agent sandbox with PyPI reachable (`tools/lint.sh` fetches
    ruff through `uvx`), all green. `CI=true`: `-m unit` 448 passed, 1 failed (runTest, "2 distinct
    failures"), exit 1; `not integration` 404 + 1 outside `tests/fuse`. `CI=true` with
    `FUSE_LIBRARY_PATH` naming a missing file: `-m unit` 404 passed, 1 failed, 44 skipped; `not
    integration` 404 + 1. Random seed with no example database: 449 passed, exit 0; 405 + 0. A first
    random-seed run failed runTest ("5 distinct failures") and was green as tolerated. Fail-once,
    each through a scratch plugin or `PYTEST_ADDOPTS`, with no tree edit: red when
    `test_put_and_stat_applies_meta` fails beside runTest under `CI=true`, and when it fails on a
    random seed; red on 0 errors when it errors in setup; red at 403 + 1 < 405 with it deselected
    under `CI=true`; red when runTest itself is deselected. The `ci` mode's 25 offline fixtures
    still match (4 green, 19 red, 2 could not run), and the saved run 42059324110 stays green.
- **Verify:** `toolchain_gate.py suite` (outside the sandbox; see Conventions).
- **Touches:** nothing beyond `TECH.md`, unless the counts in `AGENTS.md` turn out wrong.

## Phase P5 — CI runs on cycle branches and is read from a run
**Satisfies:** R9 (and the CI evidence for R1, R3, R4, R6) · **Depends on:** P4
**Goal:** the workflow runs on this branch, the four named jobs conclude `success` on the pushed tree,
and the run record shows it.

This phase waits on CI, so it is **two runs** (code-foreman → "External waits split the run").

**Run A — change, check, push, stop.**
- [x] `.github/workflows/ci.yml` (PLAN § 2.5): `on.push.branches: [main, '*/**']` with a one-line
  comment; both unit jobs `uv sync --locked`; the Linux matrix job gains `Assert libfuse loads`
  (`uv run python -c "import mfusepy"`) after `Install project`; both unit pytest steps add `-rs`. No
  job is renamed.
  - 2026-11-05 amendment: the steps add `-rfEs`, not `-rs`. pytest's `-r` replaces its default `fE`,
    so `-rs` alone drops the `FAILED` and `ERROR` lines from the short test summary (checked on
    pytest 9.1.1), and those lines are how the amended `ci` mode names a job's only failure.
    `-rfEs` keeps pytest's default lines and adds the skip reasons.
- [x] `AGENTS.md`, same commit: § Factory profile `ci` row says the workflow runs on every push to
  `main` and to every cycle branch; § Commands' CI paragraph gains one sentence on the Linux libfuse
  assertion and on the macOS job skipping `tests/fuse` by name.
  - 2026-11-05 (review cycle 1, F3): `-rfEs` prints one `SKIPPED [n] file:line: reason` line per
    skip location, `n` folding the tests skipped there, not one line per reason, and it names no
    test. See the F3 item below.
- [x] Amend the `ci` mode to R9 as amended on 2026-11-05 (GOAL Clarifications). `Lint & build`
  must be `success`. A unit job passes the gate when it is `success`, or when it is `failure` and
  its only failed or erroring test is `tests/engine/test_model.py::TestOverlayModel::runTest`. That
  test counts toward the Linux floor (passed + that one failure ≥ 449) and toward the macOS parity
  check, and every other condition stays as written. Prove the new branch with saved fixtures:
  green on main's run 42023846602 shape (that test alone failing), red on any other failed test.
  The gate must not change the test.
  - 2026-11-05 Run A: a unit job that concluded `failure` passes only when `Unit tests` is its one
    failed step, the summary's failed and error counts equal the short summary's `FAILED`/`ERROR`
    lines, and every one of those lines names the test's node id (never its file). `--log-dir` also
    takes logs saved as `<job id>.log`. Proved offline on 25 scratch fixtures built from run
    42023846602's record and logs (macOS log with P3's 44 libfuse skips in place of its collection
    errors): green with the test failing alone in all three unit jobs, as an error, mixed with
    successes, and on an all-success run; red when another test fails or errors, alone or beside it,
    including another test in the same file, and on every red the planning dry run listed; could not
    run on a jobs-only file or an unfinished run. The pre-amendment gate is red on the first green
    fixture. Fixtures stay outside the repo.
- [x] Commit (phase stays `in_progress`; the body says "Run A"), then run
  `toolchain_gate.py prepush` (see Conventions for where) on the committed tree. Green, then push
  `fix/toolchain-repair` to `origin` under the standing cycle-branch approval. Stop and hand back the
  pushed SHA. The foreman reads the run.
- [x] If Run B finds `Assert libfuse loads` red because the runner lacks the library, add an
  `apt-get install` step for the FUSE 3 runtime ahead of it, and repeat Run A. The test package's skip
  stays as it is.

**Run B — read, grade, close.**
- [x] Once the push run for the pushed SHA has completed, run the gate. `ci` takes the commit from
  `origin/fix/toolchain-repair`, the last push, unless `--sha=` names another; commits after it may
  touch only `spec/toolchain-repair/`, so review can re-run the same gate after Run B's state commit. `ci` reads the run with
  `gh`, which needs network access the sandbox may refuse. If the builder can't, the foreman runs it,
  or saves `gh run view <id> --json databaseId,headSha,headBranch,event,status,conclusion,jobs` and the
  four job logs and passes `--run-json`/`--log-dir`. The same SHA, branch, event and status checks
  apply to saved files.
  - 2026-11-05 Run B: push run 42059324110 for `0ab60a6`, read from saved files, is green under
    `ci ledger`. `Lint & build` succeeded and `Assert libfuse loads` succeeded on both Linux jobs, so
    the `apt-get` contingency was not needed. Both Linux jobs show `1 failed, 448 passed`, and macOS
    shows `1 failed, 404 passed, 44 skipped` (all 44 skips libfuse, under `tests/fuse`). The only
    failed test in all three unit jobs is the tolerated `test_model.py::TestOverlayModel::runTest`:
    the defect fails on macOS as well as Linux, which main's run 42023846602 could not show because
    its macOS job stopped at collection. The ledger walk found no deferral wording in the phase
    bodies; the tolerated defect's seed, `issues/deferred-rename-source-orphan.md`, is on
    `ROADMAP.md`.
  - 2026-11-05 (review cycle 1, F2), correction: main's run 42023846602 did show it on macOS. Job
    127233016755 reports `1 failed, 404 passed, 3 errors`, the failure `test_model`'s runTest; its
    three `tests/fuse` collection errors kept it from being the job's only red.
- [x] Deferral ledger: walk every earlier phase body for deferral wording and confirm each names an
  `issues/` seed with a `ROADMAP.md` entry (`ledger` mode does the mechanical half). The plan
  currently names none.

**Review cycle 1 remediation (2026-11-05).** Reopened with P4 and committed with it in one commit:
P5's `prepush` runs P4's `suite` on the committed tree, so one run stands in for P4's gate there.
- [x] F2: the seed's CI section and its `ROADMAP.md` entry said the `test_model` defect did not show
  on macOS. Both CI runs' macOS jobs failed it (42023846602 job 127233016755 beside its three
  `tests/fuse` collection errors; 42059324110 job 127356300109 alone), and the Mac fails it under
  `CI=true`. Both passages are this cycle's own text, not yet on `main`, so they are corrected in place.
- [x] F3: `AGENTS.md` § Commands and the `ci.yml` comment say `-rfEs` adds one
  `SKIPPED [n] file:line: reason` line per skip location; `AGENTS.md` says the macOS skips carry a
  reason naming libfuse, not that they are "skipped by name". Class sweep: the test-module comments
  that say "skip by name" describe the skip mechanism, not the report, and are tests (not touched);
  PLAN § 2.5 gets a dated line.
- [x] `AGENTS.md` § Conventions' trailer bullet says the trailer goes on commits that land on `main`
  (the landing squash commit), matching § Factory's `branch commits` row.
- [x] Class gate: a new `claims` mode. Over `AGENTS.md`, `ROADMAP.md` and every `issues/*.md`, no text
  says the defect spares macOS, and the seed names both runs, macOS and `CI=true`. Over `AGENTS.md`
  and `ci.yml`, no "line per skip reason" claim, and each names the `SKIPPED [n] file:line: reason`
  line. P5's `verify:` is now `ci ledger claims`. Fail-once: red on `2975183`'s text (three F2 hits),
  and red with F2 fixed and F3 not.
- [x] Run A again: commit, `prepush` on the committed tree, push. `ci.yml` changed, so Run B reads R9
  from the new push run, not from 42059324110.
- [x] Run B: `ci ledger claims` on the new run; close P5.
  - 2026-11-05 (foreman): push run 42147199752 for `1864e12` graded GREEN by `ci ledger claims`
    from the saved run record and job logs. Each unit job's only failure is the tolerated
    `test_model` node id (Linux 448 + 1, macOS 404 + 44 skipped + 1), the libfuse step succeeded in
    both Linux jobs, and `Lint & build` succeeded. Commits after the push touch only `spec/`.
- **Verify:** `toolchain_gate.py ci ledger`. `ci` was dry-run against saved fixtures:
  - green on a well-formed run;
  - red on a failed job, a Linux skip, a Linux run below 449, a macOS skip outside `tests/fuse`, a
    macOS skip count short of the summary, a wrong SHA, and a code commit after the push;
  - could not run on a jobs-only file.
- **Touches:** `.github/workflows/ci.yml`, `AGENTS.md`.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
