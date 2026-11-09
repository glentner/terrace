---
slug: truncate-recording-after-ancestor-rename
title: A handle-less truncate records its cut on the file an ancestor's rename has moved
kind: fix
appetite: small
status: done
branch: fix/truncate-recording-after-ancestor-rename
base: main
current_phase: done
last_updated: '2026-11-09'
phases:
- id: P1
  name: A rename holds the source lock through its catalog move, and the cut records or refuses
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/truncate-recording-after-ancestor-rename/verify/cut_gate.py regressions mutants records suite
- id: P2
  name: Verify is green under the fixed CI seed and twenty random seeds
  status: done
  satisfies:
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/truncate-recording-after-ancestor-rename/verify/cut_gate.py suite seeds
- id: P3
  name: CI is read from the run record, and the ledger closes
  status: done
  satisfies:
  - R7
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/truncate-recording-after-ancestor-rename/verify/cut_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs
review:
  last_reviewed_commit: e47a785e3533abb79bbb7b9607ff12a11e5bc128
  verdict: changes-requested
  blocked_reason: 'F1: records misstate the outcome of a cut in the rename''s stage-move gap'
  cycle: 1
---

# TECH.md — A handle-less truncate records its cut on the file an ancestor's rename has moved

The **context engine and finite-state machine** for building this fix. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/truncate-recording-after-ancestor-rename/TECH.md`);
the per-phase checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `code-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- `branch`, `base`: the cycle's branch (prefix per GOAL `kind`, from the profile's `branches`) and the
  profile's `base`.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `code-review`).
  **Artifact-deliverable R-IDs.** Some requirements are met by a *committed document* — a research
  audit, a protocol doc, an assessment — not by CLI-observable behaviour. Attach such an R-ID to the
  phase that produces and commits the artifact, and give that phase a content `verify:`
  (`test -f …`, a `grep -q` for the sections that must exist) rather than forcing a CLI drive.
  **Say so in the phase body**, because it changes who can grade it: `code-review` blinds its reviewer
  to all of `spec/`, so an R-ID whose evidence lives there is graded by the *orchestrator*, not the
  blind reviewer. Naming it in `TECH.md` is what lets the review skill route it correctly instead of
  reporting an unverifiable requirement.
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (docs, tests, isolated leaf
  modules). Any phase touching the profile's `coupled core` is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase (prefer driving the real CLI, not just tests —
  wrap CLI drives in the profile's `throwaway environment` helper, `<helper> sh -c "…"`, so they hit
  a disposable instance, never the developer's real data). A gate carrying `$( )` or quoting the
  calling shell would mangle is set with `set_phase.py --verify-file PATH`, not `--verify`.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`;
  REVIEW.md's "Cycle {n}" mirrors it and the ≤3-cycle bound is graded against it. `--verdict approved`
  also clears `review.blocked_reason` unless one is passed explicitly.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from the repo's `AGENTS.md` — it
  is the constitution. Walk the sections of its § Invariants that this change touches; that is the
  curated footgun checklist.
- One phase per `code-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. The branch commit subject is code-build's (`$KB/skill/code-build.md` →
  Step 6), not restated here; branch commits are squashed away at `code-publish` per the profile's
  `landing`. Trailers follow the profile's `branch commits`.
- The last phase carries the standing last-phase items: the deferral ledger (`code-plan` Step 6.8),
  and every item the previous cycle's last phase carried for a project-level obligation (mpsearch's
  `## Times` quote, taken as `code-build` Step 4 says). A plan that drops one says why in `PLAN.md`.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

**This cycle in particular.**

- **Coupled core and human gate.** P1 edits `src/sosfs/engine/overlay.py` (the profile's coupled core) in
  high-blast-radius functions (`truncate`, and `rename`, which joins the list), and
  `tests/engine/test_replay.py`. Geoffrey signs off on the final engine diff at review, whatever it finds
  (GOAL Q4). No phase edits any other file under `src/`, `tests/engine/test_model.py`, or either
  `conftest.py`, and `truncate`'s code changes only by the half guard of PLAN § 2.1. A phase that finds it
  must is a reason to stop and re-read PLAN, and a `SCHEMA_VERSION` bump is a stop-and-ask.
- **§ Invariants text moves with the behaviour.** PLAN § 2.3's `AGENTS.md`, root `PLAN.md` and `issues/`
  edits land in P1, the phase whose code makes them true. They are graded at review as a revision of the
  standard, on their merits.
- **No spec ids in code.** Comments in `src/` and `tests/` name constraints, never R-IDs or phase ids.
- **Where the gates run.** The development Mac, inside the agent sandbox, with these settings in every Bash
  call that runs `uv`, as separate statements:
  - `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and `UV_TOOL_DIR` set to per-session directories under the
    session scratchpad. The Python install dir must already hold a 3.15 interpreter.
  - `pypi.org` and `files.pythonhosted.org` in `allowed_domains`: `tools/lint.sh` runs an unpinned
    `uvx ruff`, `suite` runs `uv sync --locked`, and `seeds` builds two scratch environments.
  - Gate scratch, JUnit files, each run's Hypothesis storage and a copy of each run's output go under the
    worktree's gitignored `.local/cut-gate/`. Nothing is deleted.
  - The shell is zsh. `rm` is blocked (move scratch aside instead). Quote globs and separator strings, and
    never name a variable `path`.
  - Measured on the planning prototype (development Mac, 2026-11-08), after the review's folds: P1's whole
    verify 80 s; `regressions`, `mutants` and `records` 52 s (M2's run alone about 39 s, its tests failing on
    SQLite's 5 s busy timeout); `records` about 5 s;
    `suite` 28 s with 530 passed; `seeds` 4 min 38 s on two workers. Every pytest run the gate starts is killed as a
    process group past 300 s. Run in the foreground with long timeouts (`seeds` needs the 10-minute cap).
    Never hand back while a gate is still running.
- **Hypothesis state.** Every pytest run the gate starts sets `HYPOTHESIS_STORAGE_DIRECTORY` to its own
  scratch, so no run leaves `.hypothesis/` in the tree, and `suite` checks that. `seeds` runs each seed in a
  scratch tree from `git archive HEAD`. `CI=true` gives Hypothesis's fixed seed. A `.hypothesis/` that some
  other run left in the worktree is moved aside, never deleted.

---

## Phase P1 — A rename holds the source lock through its catalog move, and the cut records or refuses
**Satisfies:** R1, R2, R3, R4, R5, R6, R7 · **Depends on:** —
**Goal:** an ancestor's rename can no longer commit its catalog move inside the handle-less `truncate`, so the
cut either refuses with `ENOENT` having cut nothing or is recorded where the rename moves the file, with every
stage counter agreeing. Nine tests pin it, seven mutants give them teeth, and the records say what is closed
and name the seeds that own what is not: `issues/freethreaded-concurrency-stress.md` (the stage-move lag, Q3's
calls) and `issues/entry-identity-token.md` (the identity gap). R7's evidence is committed text outside
`spec/`, so the blind reviewer can grade it.

- [x] `src/sosfs/engine/overlay.py`, per PLAN § 2.1: the `with self._source_lock:` in `rename` around
  `_vacate` and the catalog-move transaction, stage moves after it; `truncate`'s guard loses
  `previous is not None`; the `rename` and `truncate` docstrings and the `_source_lock` comment in
  `__init__`. The `truncate` docstring scopes each `ENOENT` to its window: the lookup under the lock (an
  ancestor's catalog move committed first, nothing marked or cut) and the stage open at a path the file was
  just moved to (after the dirty mark, owned by `issues/freethreaded-concurrency-stress.md`). Nothing under
  the source lock takes a path lock or calls `_ensure`, `getattr` or `readdir`.
- [x] `tests/engine/test_replay.py`: the nine tests of PLAN § 2.2, with exactly the names in
  `verify/cut_gate.py`, and the helpers `Watched`, `WaitSignal`, `stop_at`, `race`, `assert_counted`, `cut_returned`,
  `assert_recorded_or_refused`, `assert_counted_through` and `staged`, **appended after the last existing
  test**, so every `test_replay.py:N` cite in `issues/` keeps holding. Every wait and join is bounded by
  `JOIN`, and helper threads are daemons. No sleeps. `WAITING` asserts the race happened (`outcome['stopped']`,
  `outcome['waited']`). Add the module-docstring bullet. No existing function or
  class changes.
- [x] `AGENTS.md`, per PLAN § 2.3: § 5, § 10, both high-blast-radius tables (`rename` joins), § Known open
  violations (the row this seed owns goes), and the counts (530 unit at four places, 540 total, 192 engine
  tests), each measured on the built tree.
- [x] Root `PLAN.md` § Risks, the namespace-replay entry, per PLAN § 2.3.
- [x] `issues/freethreaded-concurrency-stress.md`: the three dated lines of PLAN § 2.3. Q3's line and the
  stage-move lag go under "Path locks do not cover descendants", which stays their home (deferred work,
  owned by that seed); the replay bullet's line says it is closed and how, argued, with no test.
- [x] Leave `issues/truncate-recording-after-ancestor-rename.md` and its `ROADMAP.md` entry in place:
  `code terrace roadmap` retires both after landing.
- [x] If a guarded line's text differs from a `MUTANTS` anchor in `verify/cut_gate.py`, re-anchor it there in
  this commit, keeping the decision it attacks and the tests it names.
- **Verify:** `cut_gate.py regressions mutants records suite`.
- **Touches:** `src/sosfs/engine/overlay.py`, `tests/engine/test_replay.py`, `AGENTS.md`, `PLAN.md`,
  `issues/freethreaded-concurrency-stress.md`, `spec/truncate-recording-after-ancestor-rename/TECH.md`, and
  `spec/truncate-recording-after-ancestor-rename/verify/cut_gate.py` only to re-anchor.

## Phase P2 — Verify is green under the fixed CI seed and twenty random seeds
**Satisfies:** R8 · **Depends on:** P1
**Goal:** the profile's `verify` is green under `CI=true` with more than 521 passed, equal to `AGENTS.md`'s
count, and `test_model` passes for seeds 1 to 20 from committed state.

- [x] Run the verify. No code changes are expected. A red seed is a defect in P1's code, never a reason to
  touch `test_model.py`. It reopens P1 (`set_phase.py`), and the falsifying example becomes a deterministic
  regression in `test_replay.py`.
- [x] Record in the commit body: the passed count, the suite time, and the seeds' pass list.
- **Verify:** `cut_gate.py suite seeds`. On the planning prototype, `seeds` took 4 min 38 s on two workers, all twenty green.
- **Touches:** `spec/truncate-recording-after-ancestor-rename/TECH.md` only.

## Phase P3 — CI is read from the run record, and the ledger closes
**Satisfies:** R7, R8 · **Depends on:** P2
**Goal:** the four CI jobs conclude `success` on the pushed tree, read from the run record, and the records
still hold on the final tree. The evidence is a saved run record the foreman hands over, so the orchestrator
grades R8's CI bullet, not the blind reviewer.

This phase waits on CI, so it is **two runs** (code-foreman → "External waits split the run").

**Run A — check, push, stop.**
- [x] Ledger: walk P1 and P2 for deferral wording and confirm each names an `issues/` seed with a
  `ROADMAP.md` entry (`ledger` does the mechanical half; prose and list items, never headings). This plan's
  records home the stage-move lag and Q3's calls in `issues/freethreaded-concurrency-stress.md`, and the
  identity gap stays owned by `issues/entry-identity-token.md`.
- [x] Commit (the phase stays `in_progress`; the body says "Run A"). Run `cut_gate.py ledger prepush` on the
  committed tree. On green, stop and hand back the commit SHA for the foreman to push. Run A committed
  `d83a748`; the foreman pushed `d83a74880fba88e41f0adf31b0861f72a658fd5a`.

**Run B — read, grade, close.**
- [x] The foreman saves `gh run view <id> --json databaseId,headSha,headBranch,event,status,conclusion,jobs`
  for the push run to `.local/ci/run.json`, and the four job logs to `.local/ci/logs/<job id>.log`. Run the
  verify. `ci` takes the commit from `origin/fix/truncate-recording-after-ancestor-rename` unless `--sha=`
  names another; commits after it may touch only `spec/truncate-recording-after-ancestor-rename/`. Every job
  must conclude `success`, and Linux passed must equal `AGENTS.md`'s count with no skip. On macOS, passed
  plus skipped must equal Linux, with every skip under `tests/fuse` naming libfuse.
- [x] Record the run id, the pushed SHA and each job's counts in this phase, then close it.

**CI evidence (Run B, 2026-11-08).** Push run `42559854782` (event `push`, branch
`fix/truncate-recording-after-ancestor-rename`) on `d83a74880fba88e41f0adf31b0861f72a658fd5a`, `ci.yml` unchanged
from `origin/main`, every job `success`, read from `.local/ci/run.json` and `.local/ci/logs/`:

| Job | Id | Result |
|---|---|---|
| Unit (Linux, 3.15) | 128973661650 | 530 passed, 0 skipped, in 52.36 s; `Assert libfuse loads` success |
| Unit (Linux, 3.15t) | 128973661638 | 530 passed, 0 skipped, in 67.64 s; `Assert libfuse loads` success |
| Unit (macOS, engine-only) | 128973661489 | 486 passed, 44 skipped (all under `tests/fuse`, naming libfuse), in 65.26 s |
| Lint & build | 128973661746 | ruff errors-only "All checks passed!"; sdist and wheel built |

Linux's 530 equals `AGENTS.md`'s stated count; macOS's 486 + 44 equals it. `cut_gate.py ledger records ci` green.
- **Verify:** `cut_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs`.
- **Touches:** `spec/truncate-recording-after-ancestor-rename/TECH.md` only.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[{category}]` commit; stop and report.
