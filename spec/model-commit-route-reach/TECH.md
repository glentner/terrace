---
slug: model-commit-route-reach
title: The model commits through a scheduler poll and through eviction while a rename is queued
kind: test
appetite: small
status: in_progress
branch: test/model-commit-route-reach
base: main
current_phase: P1
last_updated: '2026-11-09'
phases:
- id: P1
  name: One composite commits through each route while a rename is queued
  status: pending
  satisfies:
  - R1
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/model-commit-route-reach/verify/model_attacks.py && uv run --no-project --python 3.15 python -I spec/model-commit-route-reach/verify/route_gate.py model reach detect mutants records timing suite
- id: P2
  name: The reach holds on twenty random seeds
  status: pending
  satisfies:
  - R2
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/model-commit-route-reach/verify/route_gate.py reach_seeds
- id: P3
  name: Verify is green under the CI draw and twenty random seeds
  status: pending
  satisfies:
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/model-commit-route-reach/verify/route_gate.py suite seeds
- id: P4
  name: CI is read from the run record, and the ledger closes
  status: pending
  satisfies:
  - R7
  - R8
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/model-commit-route-reach/verify/route_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — The model commits through a scheduler poll and through eviction while a rename is queued

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/model-commit-route-reach/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs (if `appetite: big`).

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

- **The one file under `tests/`.** P1 edits `tests/engine/test_model.py` (high-blast-radius) and nothing else under
  `tests/`; `src/`, `tests/engine/conftest.py` and `tests/engine/test_replay.py` stay byte-identical to `980f998`
  (`records`). Additions only: every base method, strategy, setting and module statement stays as it is
  (`model`). No skip guard, `assume`, precondition, filter, exception handler or settings change.
- **The redraw is re-graded, never tuned.** Every word of `OverlayMachine` (docstrings included) redraws the CI
  sample. Write the class's final text first, then run the gate; never edit text to move a count. If the final
  draw misses a floor, that is a design failure to hand back, not a draw to retry.
- **A red model is a stop** (GOAL Q6). If test_model fails on any run for an engine defect, record the falsifying
  example (the run's output under `.local/route-gate/`), make no `src/` edit, do not weaken the model, and hand
  back. A red that is the model's own mistake (a parity the composite gets wrong) is fixed in the composite.
- **No spec ids or seed paths in `tests/`.** Comments and docstrings name constraints, never R-IDs, phase ids, an
  `issues/` path or this slug (`records`).
- **Where the gates run.** The development Mac, inside the agent sandbox, with these settings in every Bash call
  that runs `uv`, as separate statements:
  - `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and `UV_TOOL_DIR` set to per-session directories under the session
    scratchpad. The Python install dir must already hold a 3.15 interpreter.
  - `pypi.org` and `files.pythonhosted.org` in `allowed_domains`: `tools/lint.sh` runs an unpinned `uvx ruff`,
    `suite` runs `uv sync --locked`, and every scratch tree syncs its own environment.
  - Gate scratch, JUnit files, census JSON, each run's Hypothesis storage and a copy of each run's output go under
    the worktree's gitignored `.local/route-gate/`. Nothing is deleted.
  - The shell is zsh. `rm` is blocked (move scratch aside instead). Quote globs and separator strings, and never
    name a variable `path`.
  - Each phase's time is in PLAN § 6, with the run that measured it. Run in the foreground with the 10-minute
    timeout; never hand back while a gate is still running.
- **Hypothesis state.** Every pytest run the gate starts sets `HYPOTHESIS_STORAGE_DIRECTORY` to its own scratch.
  The census, mutant and seed runs run in scratch trees from `git archive` (or with a planted copy of HEAD's
  `src/`), so commit before running a gate that reads HEAD. A `.hypothesis/` some other run left in the worktree is
  moved aside, never deleted.

---

## Phase P1 — One composite commits through each route while a rename is queued
**Satisfies:** R1, R3, R4, R5, R6, R7 · **Depends on:** —
**Goal:** `OverlayMachine` gains `write_rename_commit_through`, `move_out_rmdir_unlink` and `poll_data_due`
(PLAN § 2.1). On the standalone fixed CI draw the census counts at least the floors: scheduler 50 polled lagging
commits, eviction 50 lagging ones, fsync 50 and direct 17, with at least one lagging commit under a renamed
directory. The base's census reproduces 2 / 0 / 0 / 34. The scheduler-only mutant turns the model red and leaves
the base's model green; M1, M2, M3 and M7 still fail it; the serial suite time is measured. R7's records land in the same commit; they are committed text
outside `spec/`, so the blind reviewer can grade them.

- [ ] `tests/engine/test_model.py`, per PLAN § 2.1: `LIFT` and `ROUTE` just above the class (so no cited line
  moves); `poll_data_due`, `write_rename_commit_through` and `move_out_rmdir_unlink` between
  `rename_scan_unlink_commit` and `commit_entry`; the class docstring counts "five composites" in words and
  carries PLAN § 2.1's five phrases verbatim (`model` checks them). Lines fit 120 characters. Nothing else in the file changes. Write the final text before the
  first gate run (Conventions: the redraw is re-graded, never tuned).
- [ ] `AGENTS.md`: the serial suite time at `:70` and `:734` from `~30 s` to the measured figure, rounded to ten
  seconds (`~40 s` on the prototype; measure `CI=true uv run pytest -m unit` serially on the built tree, and if it
  rounds elsewhere, set `SERIAL_SECONDS` in `verify/route_gate.py` to match in this commit). The counts stay, since no
  test function is added: 548 at `:70` and `:734`, 558 at `:111`, 210 engine tests at `:136`, and 10 skipped with
  548 deselected at `:693`.
- [ ] `issues/model-source-served-reach.md`, `status: unshaped`, per PLAN § 2.4: GOAL Q1's home, the model's
  missing source-served configuration, carrying the 2026-11-06 history and this cycle's Q1 ruling, and naming this
  cycle only as `spec/model-commit-route-reach/`. Its `ROADMAP.md` entry, with a `**Seed:**` line, right after
  this cycle's entry. (The prototype's text may be used as is.)
- [ ] Leave `issues/model-commit-route-reach.md` and its `ROADMAP.md` entry in place: `code terrace roadmap`
  retires both after landing.
- [ ] If an anchor in `verify/route_gate.py` (`SCHEDULER_MUTANT`, `MODEL_MUTANTS`) no longer matches, re-anchor it
  in this commit, keeping the decision it attacks. None is expected: `src/` does not change.
- [ ] Record in the commit body the census's four counts with their own/ancestor split, and the serial suite time.
- **Verify:** `model_attacks.py && route_gate.py model reach detect mutants records timing suite` (the first is
  the `model` mode's self-test: HEAD green, each of eleven weakening edits red).
- **Touches:** `tests/engine/test_model.py`, `AGENTS.md`, `issues/model-source-served-reach.md`, `ROADMAP.md`,
  `spec/model-commit-route-reach/TECH.md`, and `spec/model-commit-route-reach/verify/route_gate.py` only to
  re-anchor or to set `SERIAL_SECONDS`.

## Phase P2 — The reach holds on twenty random seeds
**Satisfies:** R2 · **Depends on:** P1
**Goal:** for each of `--hypothesis-seed` 1 to 20, without `CI`, the census run passes and counts at least one
queued-rename commit through the scheduler route (inside a scheduler poll) and one through the eviction route,
each with a commit that lands off the logical path.

- [ ] Run the verify. No code changes are expected. A seed that misses a route is a design failure: hand it back
  with the seed's census; never edit the class to move the draw. A red seed follows GOAL Q6 (Conventions above).
- [ ] Record in the commit body the per-seed minimum of each route's count.
- **Verify:** `route_gate.py reach_seeds`.
- **Touches:** `spec/model-commit-route-reach/TECH.md` only.

## Phase P3 — Verify is green under the CI draw and twenty random seeds
**Satisfies:** R8 · **Depends on:** P2
**Goal:** the profile's `verify` under `CI=true` is green with 548 passed (at least the base's 548, and equal to
`AGENTS.md`'s count), and `uv run pytest tests/engine/test_model.py` passes for seeds 1 to 20 without `CI`, each
from a tree with no `.hypothesis/`.

- [ ] Run the verify. No code changes are expected. A red seed follows GOAL Q6.
- [ ] Record in the commit body the passed count, the suite time and the seeds' pass list.
- **Verify:** `route_gate.py suite seeds`.
- **Touches:** `spec/model-commit-route-reach/TECH.md` only.

## Phase P4 — CI is read from the run record, and the ledger closes
**Satisfies:** R7, R8 · **Depends on:** P3
**Goal:** the four CI jobs conclude `success` on the pushed tree, read from the run record, and the records still
hold on the final tree. The evidence is a saved run record the foreman hands over, so the orchestrator grades
R8's CI bullet, not the blind reviewer.

This phase waits on CI, so it is **two runs** (code-foreman → "External waits split the run").

**Run A — check, push, stop.**
- [ ] Ledger: walk P1–P3 for deferral wording and confirm each names an `issues/` seed with a `ROADMAP.md` entry
  (`ledger` does the mechanical half; prose and list items, never headings). This cycle's one deferral is GOAL
  Q1's source-served configuration, homed in `issues/model-source-served-reach.md` by P1.
- [ ] Commit (the phase stays `in_progress`; the body says "Run A"). Run `route_gate.py ledger prepush` on the
  committed tree. On green, stop and hand back the commit SHA for the foreman to push.

**Run B — read, grade, close.**
- [ ] The foreman saves `gh run view <id> --json databaseId,headSha,headBranch,event,status,conclusion,jobs` for
  the push run to `.local/ci/run.json`, and the four job logs to `.local/ci/logs/<job id>.log`. Run the verify.
  `ci` takes the commit from `origin/test/model-commit-route-reach` unless `--sha=` names another; commits after
  it may touch only `spec/model-commit-route-reach/`. Every job must conclude `success`, and Linux passed must
  equal `AGENTS.md`'s count with no skip. On macOS, passed plus skipped must equal Linux, with every skip under
  `tests/fuse` naming libfuse.
- [ ] Record the run id, the pushed SHA and each job's counts in this phase, then close it.
- **Verify:** `route_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs`.
- **Touches:** `spec/model-commit-route-reach/TECH.md` only.

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
