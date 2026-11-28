---
slug: plan-md-reconciliation
title: Drop the unused rich dependency, and hold PLAN.md to the redraw's reconciliation
kind: docs
appetite: small
status: in_review
branch: docs/plan-md-reconciliation
base: main
current_phase: done
last_updated: '2026-11-27'
phases:
- id: P1
  name: rich leaves pyproject.toml and uv.lock; AGENTS.md's Code style says so in the same commit
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
  verify: uv run --no-project --python 3.15 python -I spec/plan-md-reconciliation/verify/dependency_gate.py lock text guards ledger build
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — Drop the unused rich dependency, and hold PLAN.md to the redraw's reconciliation

The **context engine and finite-state machine** for building this cycle. The YAML frontmatter above
is the resume ground-truth (read it from the cycle worktree with
`uv run --no-project --with pyyaml python "$KB/bin/factory/next_phase.py" spec/plan-md-reconciliation/TECH.md`);
the per-phase checklist below is the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --no-project --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Gate:** [`verify/dependency_gate.py`](verify/dependency_gate.py), one mode per concern (PLAN § 6).
  No `research/`: the lean path (PLAN § 4).

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

---

## Phase P1 — rich leaves pyproject.toml and uv.lock; AGENTS.md's Code style says so in the same commit
**Satisfies:** R1, R2, R3, R4, R5, R6, R7 · **Depends on:** —
**Goal:** the package declares only what it imports: `rich` is gone from `[project] dependencies`, and
`uv.lock` loses `rich` and the two packages only it required (`markdown-it-py`, `mdurl`) and changes
nothing else. `AGENTS.md` § *Code style* stops calling `rich` a declared dependency in the same commit.
R4 to R7 are guards: `PLAN.md` is not edited, and the gate re-checks each against the phase's tree.

R3's `AGENTS.md` sentence is a **committed document**: the orchestrator and the blind reviewer can both
grade it, since it lives outside `spec/`.

- [x] **Remove the dependency with a lock operation that only removes.** From the cycle worktree, with the
      step's `UV_*` environment exported: `uv remove rich --no-sync`. It deletes the `"rich>=13.7",` line
      from `pyproject.toml` and re-locks keeping every existing pin. Never `--upgrade`, never
      `uv lock --upgrade-package`, and never a hand edit of `uv.lock`. Check that
      `git diff --numstat -- pyproject.toml uv.lock` reads `0 1` and `0 36`; the prototype's run did
      (PLAN § 4).
- [x] **`AGENTS.md` § *Code style*, same commit.** The bullet at `AGENTS.md:956-957` becomes exactly:
      ```
      * Logging through `Logger.with_name(__name__)`; TRACE for per-operation detail; formatting goes
        through `cmdkit.ansi`.
      ```
      No other line of `AGENTS.md` changes. The gate's `text` mode enforces: `git grep -n -w rich -- ':!spec'
      ':!issues' ':!ROADMAP.md'` returns nothing; § *Code style* contains the phrase
      "formatting goes through `cmdkit.ansi`" (whitespace-normalized); `AGENTS.md` outside § *Code style*
      is byte-identical to the base; and every commit touching `pyproject.toml` or `uv.lock` also touches
      `AGENTS.md`. § *Verification*'s 851 stays: `build` checks the figure is still there and still true.
- [x] **Nothing else.** `PLAN.md`, `src/`, `tests/`, `.github/`, `README.md` and `CHANGELOG.md` are not
      touched (GOAL Non-goals); the gate's `text` mode allows only `AGENTS.md`, `pyproject.toml`,
      `uv.lock`, `spec/plan-md-reconciliation/`, `issues/*.md` and `ROADMAP.md` in the diff from the base.
      The references to the seed in `AGENTS.md` § *Known open violations* stay for `code terrace roadmap`.
- [x] **Stage, then verify.** Stage the three files by path (code-build Step 6), then run the phase's
      `verify:`. Export `GATE_SCRATCH` as the cycle's scratch directory
      first (the foreman's working rules name it), so the run log, the JUnit report and `uv build`'s `dist-<stamp>/` land in the cycle's scratch
      directory; unset, they land in the main checkout's git-ignored `.local/plan-md-reconciliation/`.
      Never in the worktree. The `build` mode takes about two minutes (`uv sync --locked`, `uv build`, a
      collection pass, `tools/lint.sh`, and the profile's verify under `-n auto`).
- [x] **Deferral ledger** (last phase). This plan files no new seed. Walk this phase's list items and
      prose (not headings) for deferral phrasing and confirm each names an `issues/` file with a
      `ROADMAP.md` entry; the gate's `ledger` mode does the same mechanically.
- **Verify:** `uv run --no-project --python 3.15 python -I spec/plan-md-reconciliation/verify/dependency_gate.py lock text guards ledger build`.
  Read the `ok` lines, not the exit code. Expected: `lock` removes three packages
  (`markdown-it-py`, `mdurl`, `rich`) and keeps 63 at their base versions; `guards` lists the seven
  subcommands and the four testing texts' paths; `build` reports `851/870 tests collected` and a JUnit
  report of 851 cases with no failure or error (the bundle loop's 9 strict xfails count as skipped).
- **Touches:** `pyproject.toml`, `uv.lock`, `AGENTS.md`.
