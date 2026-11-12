---
slug: rename-to-terrace
title: Rename the project to Terrace
kind: refactor
appetite: big
status: in_review
branch: refactor/rename-to-terrace
base: main
current_phase: done
last_updated: '2026-11-12'
phases:
- id: P1
  name: The package, the distribution and the console script are terrace
  status: done
  satisfies:
  - R2
  - R3
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/rename-to-terrace/verify/rename_gate.py identity suite
- id: P2
  name: Tier roots, the environment, the paths and the attributes carry the new name
  status: done
  satisfies:
  - R4
  - R5
  - R6
  - R7
  - R8
  - R9
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/rename-to-terrace/verify/rename_gate.py contracts suite
- id: P3
  name: Documents, seeds, roadmap and changelog, then the census and the ledger
  status: done
  satisfies:
  - R1
  - R10
  - R11
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/rename-to-terrace/verify/rename_gate.py census docs ledger identity contracts suite
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — Rename the project to Terrace

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/rename-to-terrace/TECH.md`); the
per-phase checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** none. The plan ran lean; `PLAN.md` § 4 holds what a brief would.

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
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase.
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
  and every item the previous cycle's last phase carried for a project-level obligation.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

Particular to this cycle:

- **The builder runs rules and does not hand-edit.** Each phase is one run of
  [`rename.py`](rename.py), whose rules `PLAN.md` § 2 states. If a site fails to match, the tree has
  moved since the plan: stop and read the site, do not edit around it.
- **Stage, then verify.** Every gate mode refuses a tree with unstaged edits to tracked files. Stage
  the phase's paths by name, run the gate, then commit.
- **The gate deletes nothing.** Its scratch stays under `$TMPDIR`. It runs `uv sync --locked`, so
  the project environment is exact when a drive starts.
- **`contracts` and `suite` exit 3 where libfuse cannot be loaded,** and `contracts` where the S3
  test stack is missing. That is "could not run", never a pass and never a red.
- **A fail-once mutation** edits one file, stages it, runs the one mode that should catch it, and
  restores the file. `PLAN.md` § 4 lists the mutants the plan already ran.

---

## Phase P1 — The package, the distribution and the console script are terrace
**Satisfies:** R2, R3 · **Depends on:** —
**Goal:** `src/terrace/` is the package, `terrace` is the distribution and the console script, the
classes are `TerraceApp` and `TerraceError`, and the suite is green at the base commit's count. The
names written to a tier root, the environment prefix, the path components and the attribute
namespace are held back for P2 (`PLAN.md` § 2, the contract forms).

- [x] `python3 -I spec/rename-to-terrace/rename.py p1`. It moves `src/sosfs` to `src/terrace`,
  rewrites `PLAN.md`'s technology row first, applies the nineteen proper-noun sites, then rules 2, 4
  and 5 over the code scope.
- [x] `uv lock`, then `uv sync`. `uv.lock` changes only in the project's own entry.
- [x] Read `git status` and `git diff --stat`: the 34 tracked files of the package now under
  `src/terrace/`, and modifications only in the code scope, `PLAN.md` and `uv.lock`. Read the diff of `pyproject.toml`,
  `src/terrace/__init__.py`, `tools/lint.sh` and `.github/workflows/ci.yml` in full.
- [x] Stage by name: `src tests tools pyproject.toml docker-compose.yml .github uv.lock PLAN.md`.
- **Verify:** `rename_gate.py identity suite`. Dry-run green on the prototype's P1 tree: 731 passed.
  `contracts` and `census` are red on this tree, as they should be.
- **Touches:** every file under `src/` (moved), most of `tests/`, `tools/lint.sh`,
  `tools/temp_site.sh`, `pyproject.toml`, `docker-compose.yml`, `.github/workflows/ci.yml`,
  `uv.lock`, one row of `PLAN.md`. The coupled core changes in import lines, base-class names and
  docstrings only.

## Phase P2 — Tier roots, the environment, the paths and the attributes carry the new name
**Satisfies:** R4, R5, R6, R7, R8, R9 · **Depends on:** P1
**Goal:** a managed tier root holds `.terrace/`, the prefix is `TERRACE_`, the nine paths sit under
`terrace`, the attributes are `user.terrace.*`, and nothing in the code scope spells an old name. An
overlay written before the rename is refused and left as it was. The state store is unchanged.

- [x] `python3 -I spec/rename-to-terrace/rename.py p2`. It rewrites the marker file's first line,
  then applies rules 3 and 4 with nothing held back.
- [x] Read the diff of `state/lifecycle.py`, `state/journal.py`, `tier/s3.py`, `fuse/adapter.py`,
  `core/config.py`, `core/platform.py`, `tests/__init__.py`, `tests/conftest.py` and
  `tools/temp_site.sh` in full: these hold every literal the GOAL names as a hazard.
- [x] Stage by name: `src tests tools docker-compose.yml`.
- [x] R8: the gate runs the adapter's unit tests and drives the adapter without a mount. The check
  on a real mount (`tests/fuse/test_integration.py`, `-m integration`) is **not run** in this cycle.
  Say so in the phase commit's body.
- [x] `core/platform.py` uses an `XDG_*_HOME` value as written, empty or relative. This phase
  changes the directory name and nothing else there. The seed was filed with the plan:
  deferred to `issues/xdg-empty-and-relative-values.md`.
- **Verify:** `rename_gate.py contracts suite`. Dry-run green on the prototype's P2 tree; `census`
  is red there (the documents have not moved).
- **Touches:** `src/terrace/state/{lifecycle.py,journal.py,schema.sql}` (the coupled core: two
  constants, one tuple, comments), `src/terrace/tier/`, `src/terrace/fuse/adapter.py`,
  `src/terrace/core/{config.py,platform.py}`, `src/terrace/engine/overlay.py` (one docstring), the
  tests that spell a contract form, `tools/temp_site.sh`, `docker-compose.yml`.

## Phase P3 — Documents, seeds, roadmap and changelog, then the census and the ledger
**Satisfies:** R1, R10, R11 · **Depends on:** P2
**Goal:** no tracked file inside the census spells an old name, `CHANGELOG.md` announces the break and
lists what moved, `AGENTS.md` describes the tree as it is, and every deferral has a record.

- [x] `python3 -I spec/rename-to-terrace/rename.py p3`. It applies the document sites (`PLAN.md` § 2
  lists them), rules 2 to 5 over the document scope, the one correction after the rules, and the two
  dated notes.
- [x] Read the diff of `AGENTS.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md` and `PLAN.md` in full,
  and of `issues/config-command.md`, `issues/hpc-deployment.md` and `issues/audit-command.md`. Skim
  the other seeds for a sentence the rules made false; none was found on the prototype.
- [x] Stage by name: `AGENTS.md PLAN.md README.md ROADMAP.md CHANGELOG.md issues`.
- [x] R11 and R8's record are graded from committed text: `CHANGELOG.md`, `PLAN.md` and `AGENTS.md`
  by any reviewer, and the **not run** statement in `spec/rename-to-terrace/PLAN.md` by the
  orchestrator, since a blind reviewer does not read `spec/`.
- [x] Deferral ledger. Walk P1 to P3 for anything left unfixed and confirm each has its record:
  - an empty or relative `XDG_*_HOME` is deferred to `issues/xdg-empty-and-relative-values.md`,
    which `ROADMAP.md` lists;
  - `issues/audit-command.md` gains a dated note for a known limitation: a refused attach leaves
    the crash flag set, so a second mount attempt stops at the crash gate;
  - `issues/config-command.md` gains a dated note for a known limitation: the environment prefix
    is matched without its underscore.
- [x] Before the push, `rename_gate.py prepush` on the clean, committed tree.
- **Verify:** `rename_gate.py census docs ledger identity contracts suite`. Dry-run green on the
  prototype's P3 tree, 731 passed.
- **Touches:** `AGENTS.md` (§ Invariants and § Factory included: names and paths only, and the
  notice removed), `PLAN.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`, 34 files under `issues/`.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[{category}]` commit; stop and report.
