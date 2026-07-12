---
name: continue
description: Resume sosfs implementation by executing the next incomplete checklist item(s) from ROADMAP.md. Default behavior is conservative — one item, one WIP commit, then stop and report for review. Free-form arguments can target a phase, run several items or phases, do a dry run that surfaces the plan without executing, or just report current state. Wired to ROADMAP.md's YAML frontmatter (current_phase, phases_completed), one-item-one-commit granularity, the pytest verification gate, and the wip-branch workflow.
---

# /continue — resume sosfs implementation

## When to Use

Invoke `/continue` when picking up sosfs work in a fresh session — or after a review
checkpoint — and you want the next slice of ROADMAP.md executed without re-explaining the
project. It is designed for the *one-item-then-stop* rhythm: scale up with arguments when
you trust the next chunk, scale down to a dry run when you don't.

ROADMAP.md is the resume ground truth; PLAN.md (its `plan_ref`) is the design detail;
AGENTS.md carries the conventions. Read AGENTS.md first in a fresh session.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Argument Parsing

Parse case-insensitively against the patterns below. If the instruction is ambiguous or
self-contradictory, STOP and ask rather than guess.

- `status` / `report` — summarize ROADMAP state (current phase, completed phases, next
  item, remaining count, last commit on `wip`) and stop. No work, no edits, no commits.
- `dry run` / `plan only` / `preview` — identify the target and load context, then report
  the plan that *would* be executed (items, files to be touched, expected commit message,
  frontmatter deltas). No edits, no side-effecting tools. Combinable: `dry run phase 6`.
- `phase X` / `at X` — target Phase X instead of `current_phase`.
- `next N` / `N items` — execute the next N items in order, one commit each.
- `through N` — execute items forward, stopping after Phase N completes.
- `phases X..Y` — execute all items across Phases X–Y.
- `skip review` — do not stop at phase boundaries. Use sparingly; the checkpoints exist
  for a reason.
- `bundle` — collapse the run into a single WIP commit instead of one per item. Only for
  tightly coupled mechanical items.
- `... and push wip` — after the final commit, `git push origin wip` (only when a remote
  exists; never `--force` from this skill).

Anything release-shaped (`merge to main`, `squash`, `bump version`, `tag`) is out of
scope: STOP and point the user at `/release`.

## Safety Principles

- **ROADMAP.md frontmatter and checkboxes must agree.** `current_phase` /
  `phases_completed` are the resume markers; the checklists are the record. If they
  disagree on what's done, STOP and report — do not guess which is right.
- **PLAN.md wins.** If implementation surfaces a contradiction or a better design, STOP —
  reconcile PLAN.md (with the user when substantive) before writing the code, and update
  ROADMAP.md items to match.
- **An item is the unit of work.** ROADMAP items are commit-sized by design: one item ≈
  one `WIP:` commit. Use `phase X` / `next N` / `through N` to scale up deliberately.
- **`wip` branch only.** Never commit to `main`; never squash or force-push from this
  skill — that is `/release`'s job.
- **Expected-red gate window.** Until the Phase 1 test-harness item lands there is no test
  suite — pytest exiting with "no tests collected" is expected and NOT a STOP condition;
  note it in the report and proceed. From that item onward the gate is binding: a red gate
  means do not commit.
- **`rm` is aliased away in this environment — use `del`** for untracked removals; prefer
  `git rm` for tracked files.
- **Honor the code conventions in AGENTS.md**: SPDX headers, structured import blocks,
  `__all__`, `Logger.with_name`, FSM/Thread idioms, 120-character lines.
- Never edit GOAL.md. Never commit secrets, credentials, or live endpoints from tests.

## Procedure

### Step 0 — Status / dry run only (when requested)

Skip unless `status`/`report` or `dry run`/`plan only`/`preview` was passed.

- `status`: read the frontmatter, cross-check the first `[ ]` item against
  `current_phase`, report a compact summary (current phase, completed phases, next item,
  last commit on `wip`), and stop.
- `dry run`: do Steps 1–2 (pre-flight + context), report the intended plan, and stop.
  No edits; no `uv`, `pytest`, or `git commit`.

### Step 1 — Pre-flight (always)

1. Working tree must be clean (`git status --porcelain`). Non-empty → STOP and report.
2. Branch check (`git branch --show-current`):
   - On `wip` → proceed.
   - `wip` missing → create it: `git checkout -b wip main` (bootstrap case).
   - On `main` with a clean tree → `git checkout wip`.
   - On any other branch → STOP and report. Do not auto-switch.
3. If a remote exists, `git fetch` (read-only) and note divergence; do not pull.

### Step 2 — Identify the target and load context

1. Read ROADMAP.md frontmatter. Default target: the first `[ ]` item in `current_phase`
   (if that phase is fully checked, advance the frontmatter first — see Step 5 — then take
   the next phase's first item). Cross-check frontmatter against checkboxes; on mismatch,
   STOP.
2. Read AGENTS.md. Read the PLAN.md sections relevant to the item whenever it involves a
   non-trivial design decision (the ROADMAP phase overviews name them).
3. Read the actual files the item will touch before editing them — do not edit blind.

### Step 3 — Implement the item

Execute the item as written. If it is ambiguous or contradicts PLAN.md, STOP and surface
it (plan-drift rule) rather than improvising. Apply the code conventions to everything new
or moved.

### Step 4 — Verification gate (before committing)

```bash
uv sync && uv run pytest -m unit
```

- Red gate → do not commit; report the failure output.
- "No tests collected" is a pass inside the Phase 1 expected-red window (see Safety).
- Run `-m integration` only when the item touches mounts or S3 and the environment
  supports it; say so in the report either way.

### Step 5 — Update ROADMAP.md (same commit as the work)

1. Flip the completed item `- [ ]` → `- [x]`.
2. Advance `current_phase` and append to `phases_completed` (as a string) **only** when
   every item in the phase is checked. Partial completion earns nothing.
3. Set `last_updated` (UTC, `YYYY-MM-DD`). Set `status: in-progress` on first work,
   `status: complete` when the final phase closes.

### Step 6 — Commit

```bash
git add -A
git commit -m "WIP: <short summary of the item>

<optional body: the why of decisions not obvious from the diff, 1-3 lines>

Co-Authored-By: <your standard agent attribution, e.g. Claude Fable 5 <noreply@anthropic.com>>"
```

Do not push by default (see the `and push wip` argument for the narrow exception).

### Step 7 — Continue or stop

- Default: stop after one item and report.
- `next N` / `through N` / `phases X..Y`: loop back to Step 2 until the stop condition,
  pausing at each phase boundary unless `skip review` was passed.
- Any STOP condition from Steps 1–4 ends the run immediately with a report.

Final report: what was completed (items + one-liners), anything deferred and why, the new
frontmatter values, gate results (or why expected-red/skipped), and open questions.

## Examples

```
/continue                      # next item, one WIP commit, stop and report
/continue status               # where are we? no work
/continue dry run phase 6      # preview Phase 6's first item without touching anything
/continue next 3               # three items, three commits, stop
/continue phases 2..3 bundle skip review   # run two phases as one commit, no pauses
```

## Notes

- Keep the resume markers accurate even when stopping mid-item on a STOP condition — never
  advance `current_phase` past partially completed work.
- Commit bodies explain *why*, not what the diff already shows.
