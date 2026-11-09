---
slug: entry-identity-token
title: A call that waits for the source lock never acts on a file that took a purged entry's id
kind: fix
appetite: small
status: done
branch: fix/entry-identity-token
base: main
current_phase: done
last_updated: '2026-11-09'
phases:
- id: P1
  name: A purge watch tells each call's entry from a file that took its id
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/entry-identity-token/verify/identity_gate.py regressions mutants records suite
- id: P2
  name: Verify is green under the fixed CI seed and twenty random seeds
  status: done
  satisfies:
  - R9
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/entry-identity-token/verify/identity_gate.py suite seeds
- id: P3
  name: CI is read from the run record, and the ledger closes
  status: done
  satisfies:
  - R8
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/entry-identity-token/verify/identity_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs
review:
  verdict: approved
  cycle: 2
  last_reviewed_commit: 5bb6932494567845c0e561bf1e0363f76eff98a0
  blocked_reason: ''
---

# TECH.md — A call that waits for the source lock never acts on a file that took a purged entry's id

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/entry-identity-token/TECH.md`); the per-phase
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

- **Coupled core and human gate.** P1 edits `src/sosfs/engine/overlay.py` (the profile's coupled core) in
  high-blast-radius functions (`truncate`, `open`, `_serve`, `_lookup_again`, `_remove`,
  `_execute_namespace_locked`, `_vacate`), `src/sosfs/state/catalog.py` (`purge`'s return only), and
  `tests/engine/test_replay.py`. Geoffrey signs off on the final engine diff at review, whatever it finds
  (GOAL Q6). No phase edits `src/sosfs/state/schema.sql`, `src/sosfs/state/db.py`, `SCHEMA_VERSION`,
  `tests/engine/test_model.py` or either `conftest.py`. A phase that finds it must is a reason to stop: a
  schema change is out of scope (GOAL Q1) and goes back to Geoffrey.
- **`truncate`'s signed-off property.** Only its lookup prologue changes: the watch opens with the path lock,
  and one test, `or entry.id in watch`, is appended to its inline check, which stays inline (not folded into
  `_lookup_again`). It still calls `self._ensure(path)` and names the same entry. From `_serve` through its
  recording the code is the base's, and the gate pins both (PLAN deviation row 4).
- **Signatures the existing tests depend on.** `_ensure(self, path)` and `_adopt(self, path, stat)` keep their
  signatures, and `open` and `truncate` call `self._ensure(path)` with one argument: cycle 4's `AfterLookup`
  replaces `_ensure` with a one-argument callable. The call's watch reaches `_ensure` through the engine's
  `_naming` thread-local (PLAN § 2.1, rabbit hole 3).
- **§ Invariants text moves with the behaviour.** PLAN § 2.3's `AGENTS.md`, root `PLAN.md` and `issues/` edits
  land in P1, the phase whose code makes them true. They are graded at review as a revision of the standard,
  on their merits.
- **No spec ids or seed paths in code.** Comments in `src/` and `tests/` name constraints, never R-IDs, phase
  ids or an `issues/` path (Geoffrey's 2026-11-09 directive).
- **Where the gates run.** The development Mac, inside the agent sandbox, with these settings in every Bash
  call that runs `uv`, as separate statements:
  - `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and `UV_TOOL_DIR` set to per-session directories under the
    session scratchpad. The Python install dir must already hold a 3.15 interpreter.
  - `pypi.org` and `files.pythonhosted.org` in `allowed_domains`: `tools/lint.sh` runs an unpinned
    `uvx ruff`, `suite` runs `uv sync --locked`, and `seeds` builds two scratch environments.
  - Gate scratch, JUnit files, each run's Hypothesis storage and a copy of each run's output go under the
    worktree's gitignored `.local/identity-gate/`. Nothing is deleted.
  - The shell is zsh. `rm` is blocked (move scratch aside instead). Quote globs and separator strings, and
    never name a variable `path`.
  - Measured on the planning prototype's dry runs: see PLAN § 6 for each phase's figure, and the run that
    measured it. Every pytest run the gate starts is killed
    as a process group past 300 s. Run in the foreground with long timeouts (`seeds` needs the 10-minute
    cap). Never hand back while a gate is still running.
- **Hypothesis state.** Every pytest run the gate starts sets `HYPOTHESIS_STORAGE_DIRECTORY` to its own
  scratch, so no run leaves `.hypothesis/` in the tree, and `suite` checks that. `seeds` runs each seed in a
  scratch tree from `git archive HEAD`. `CI=true` gives Hypothesis's fixed seed. A `.hypothesis/` that some
  other run left in the worktree is moved aside, never deleted.

---

## Phase P1 — A purge watch tells each call's entry from a file that took its id
**Satisfies:** R1, R2, R3, R4, R5, R6, R7, R8 · **Depends on:** —
**Goal:** the handle-less `truncate`, a source-served `open` and a promotion off the source each open a purge
watch before they name their entry, and refuse with `ENOENT` a file that took the entry's id after its purge;
a purge of another entry refuses none of them. Eighteen tests pin it, thirteen mutants give them teeth, and the
records say exactly what is closed, in each window, and home what is not in
`issues/freethreaded-concurrency-stress.md`. R8's evidence is committed text outside `spec/`, so the blind
reviewer can grade it.

- [x] `src/sosfs/state/catalog.py`: `purge` returns the purged id (`DELETE … RETURNING id`), with its docstring.
- [x] `src/sosfs/engine/overlay.py`, per PLAN § 2.1: `PurgeWatch` declared **below** `OverlayEngine`;
  `_watches`, `_watches_mutex` and `_naming` after `_source_lock`, with the order sentence in its comment
  extended; `_watch`, `_named`, `_purge`; `_ensure`'s first read through `_named`, and `_adopt`'s clear inside its
  insert's transaction, both with their signatures unchanged; the four purge sites through `_purge`, `_vacate`'s
  wrapped in `db.transaction()`; `_lookup_again(entry, watch)`; `open`, `_serve` and `truncate` as PLAN § 2.1
  says, the cut's inline check kept inline with the watch test appended. The docstrings scope each `ENOENT` to its
  window (PLAN § 2.1, "Per window"). Nothing under the source lock takes a path lock or calls `_ensure`,
  `getattr` or `readdir`; nothing but `_named`'s one catalog read runs under the watch mutex, and `_named` opens
  the thread's connection before taking it. `_watch` saves and restores the thread's previous naming watch, and
  registers inside its `try`.
- [x] `tests/engine/test_replay.py`: the 18 tests of PLAN § 2.2, with exactly the names in
  `verify/identity_gate.py`, and the helpers `reuse_route`, `other_purged`, `other_created`, `with_other` and
  `first_call`, **appended after the last existing test**, so every `test_replay.py:N` cite in `issues/` keeps
  holding. Each test asserts that the id it relies on was reused. Each refusal test asserts
  `engine._watches == []`; T12 also asserts its carry reached the source lock. T18 imports `contextmanager`
  inside the test, so no import line is added above the cited lines. Every join is bounded by `JOIN`, and helper threads are daemons. No sleeps. Add the
  module-docstring bullet and drop "short of a file that took". No existing function or class changes.
- [x] `AGENTS.md`, per PLAN § 2.3: § 5, § 10 (the order and the leaf bullet), both high-blast-radius tables,
  § Known open violations (the `5, 10` row goes), and the counts at `:70`, `:111`, `:136`, `:672` and `:714`
  (base lines), each measured on the built tree.
- [x] Root `PLAN.md` § Risks, per PLAN § 2.3.
- [x] `issues/freethreaded-concurrency-stress.md`: Q5's dated line under "Queue-row identity is by value",
  per PLAN § 2.3. It names the open identities (deferred work, owned by that seed) and cites no `file:line`.
- [x] Every seed with an unpinned `file:line` cite of a changed `.py` file that no longer matches its base text:
  add "holds at `c7d2049`" where the cite held there (PLAN § 2.3; `records` lists each one).
- [x] Leave `issues/entry-identity-token.md` and its `ROADMAP.md` entry in place: `code terrace roadmap`
  retires both after landing.
- [x] If a guarded line's text differs from a `MUTANTS` anchor in `verify/identity_gate.py`, re-anchor it
  there in this commit, keeping the decision it attacks and the tests it names.

*Amended 2026-11-09, review cycle 1 remediation (F1, R8; P1 reopened).* The pin item above was wrong: the
twelve cites had drifted before `c7d2049`, so "holds at `c7d2049`" was false for each.
- [x] F1: re-pin each of the twelve cites to the newest commit on `main` where every cite in its block holds
  (`141aa86`, `5e0d997` or `0e6d14c`), checked with `git show <sha>:<file>`; the cite text and the seed prose are
  unchanged (landed text stays append-only). The record is the worktree's `.local/remediation1/cites.txt`.
- [x] F1: `issues/freethreaded-concurrency-stress.md`'s `Catalog.rename` cite, true at its pin `5e0d997` and moved
  by this cycle's `Catalog.purge`, gets a dated line saying where it starts now, in prose, so it needs no pin
  naming an unlanded commit.
- [x] Gate amendment: `records` gains `pins_true`. Every pin this branch writes into a seed must hold its cites at
  the commit it names: the file exists there, the range lies inside it, and the cited text (`PINNED_TEXT`, a
  missing entry is red) matches the lines. An inline pin holds its own cite at that very commit; every other cite
  in its block holds at one of the block's pins. One cite is exempt by key: `issues/audit-command.md`'s
  `overlay.py:1038`, placed on a branch tree with no commit. Shown red on `dcd62ee`'s twelve pins first
  (`.local/remediation1/records-before-fix.log`), then green on the fix.
- [x] G1 (coverage observation): one dated sentence homes the unpinned `_vacate` transaction decision under
  the reused-id note in `issues/freethreaded-concurrency-stress.md`, which `ROADMAP.md` lists.
- **Verify:** `identity_gate.py regressions mutants records suite`.
- **Touches:** `src/sosfs/engine/overlay.py`, `src/sosfs/state/catalog.py`, `tests/engine/test_replay.py`,
  `AGENTS.md`, `PLAN.md`, `issues/freethreaded-concurrency-stress.md`, the seeds whose drifted cites get pins,
  `spec/entry-identity-token/TECH.md`, and
  `spec/entry-identity-token/verify/identity_gate.py` to re-anchor and, in remediation, for `pins_true`.

## Phase P2 — Verify is green under the fixed CI seed and twenty random seeds
**Satisfies:** R9 · **Depends on:** P1
**Goal:** the profile's `verify` is green under `CI=true` with more than 530 passed, equal to `AGENTS.md`'s
count, and `test_model` passes for seeds 1 to 20 from committed state.

*Reopened 2026-11-09 by the review cycle 1 remediation: the verify re-runs on the remediated tree.*

- [x] Run the verify. No code changes are expected. A red seed is a defect in P1's code, never a reason to
  touch `test_model.py`. It reopens P1 (`set_phase.py`), and the falsifying example becomes a deterministic
  regression in `test_replay.py`.
- [x] Record in the commit body: the passed count, the suite time, and the seeds' pass list.
- **Verify:** `identity_gate.py suite seeds`.
- **Touches:** `spec/entry-identity-token/TECH.md` only.

## Phase P3 — CI is read from the run record, and the ledger closes
**Satisfies:** R8, R9 · **Depends on:** P2
**Goal:** the four CI jobs conclude `success` on the pushed tree, read from the run record, and the records
still hold on the final tree. The evidence is a saved run record the foreman hands over, so the orchestrator
grades R9's CI bullet, not the blind reviewer.

This phase waits on CI, so it is **two runs** (code-foreman → "External waits split the run").

*Reopened 2026-11-09 by the review cycle 1 remediation: Run A and Run B re-run on the remediated tree. The
record under Run B is the first push's, kept as the record of that run.*

**Run A — check, push, stop.**
- [x] Ledger: walk P1 and P2 for deferral wording and confirm each names an `issues/` seed with a
  `ROADMAP.md` entry (`ledger` does the mechanical half; prose and list items, never headings). This plan's
  records home the stage window, handles keyed by entry id and data-row snapshots in
  `issues/freethreaded-concurrency-stress.md`.
- [x] Commit (the phase stays `in_progress`; the body says "Run A"). Run `identity_gate.py ledger prepush` on
  the committed tree. On green, stop and hand back the commit SHA for the foreman to push.

**Run B — read, grade, close.**
- [x] The foreman saves `gh run view <id> --json databaseId,headSha,headBranch,event,status,conclusion,jobs`
  for the push run to `.local/ci/run.json`, and the four job logs to `.local/ci/logs/<job id>.log`. Run the
  verify. `ci` takes the commit from `origin/fix/entry-identity-token` unless `--sha=` names another; commits
  after it may touch only `spec/entry-identity-token/`. Every job must conclude `success`, and Linux passed
  must equal `AGENTS.md`'s count with no skip. On macOS, passed plus skipped must equal Linux, with every skip
  under `tests/fuse` naming libfuse.
- [x] Record the run id, the pushed SHA and each job's counts in this phase, then close it.

  **Record (2026-11-09).** Push run `42657576921` (event `push`, branch `fix/entry-identity-token`) for
  `9213a0c8a6e755966c8e4227ca28510b00a7dee7`, concluded `success`:
  - `Lint & build` (job 129300109282): `success`; lint (errors only) and the build of the sdist and wheel
    succeeded.
  - `Unit (Linux, 3.15)` (job 129300108658): `success`, 548 passed, no failure, error or skip.
  - `Unit (Linux, 3.15t)` (job 129300109404): `success`, 548 passed, no failure, error or skip.
  - `Unit (macOS, engine-only)` (job 129300109220): `success`, 504 passed and 44 skipped (= 548), every skip
    under `tests/fuse` naming libfuse.

  `identity_gate.py ledger records ci` on the saved record and logs: GREEN, 209 checks, 1 s.

  **Record after the review cycle 1 remediation (2026-11-09).** Push run `42667243086` (event `push`, branch
  `fix/entry-identity-token`) for `0370912c5e5e0028512d0929bef7fd8e665ba45e`, concluded `success`:
  - `Lint & build` (job 129334077687): `success`; lint (errors only) and the build of the sdist and wheel
    succeeded.
  - `Unit (Linux, 3.15)` (job 129334077756): `success`, 548 passed, no failure, error or skip.
  - `Unit (Linux, 3.15t)` (job 129334077526): `success`, 548 passed, no failure, error or skip.
  - `Unit (macOS, engine-only)` (job 129334077815): `success`, 504 passed and 44 skipped (= 548), every skip
    under `tests/fuse` naming libfuse.

  `identity_gate.py ledger records ci` on the saved record and logs: GREEN, 214 checks, about 2 s; `pins_true`
  green on the pushed tree.
- **Verify:** `identity_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs`.
- **Touches:** `spec/entry-identity-token/TECH.md` only.

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
