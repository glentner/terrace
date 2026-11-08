---
slug: source-served-open-race
title: open finds a source-served file while a replay moves it
kind: fix
appetite: small
status: done
branch: fix/source-served-open-race
base: main
current_phase: done
last_updated: '2026-11-08'
phases:
- id: P1
  name: open and a promotion off the source resolve the place under the source lock
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/source-served-open-race/verify/open_gate.py regressions mutants records suite
- id: P2
  name: Verify is green under the fixed CI seed and twenty random seeds
  status: done
  satisfies:
  - R7
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/source-served-open-race/verify/open_gate.py suite seeds
- id: P3
  name: CI is read from the run record, and the ledger closes
  status: done
  satisfies:
  - R7
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/source-served-open-race/verify/open_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs
review:
  last_reviewed_commit: f89311b2d9cc534e526e155b5614cb9a28aea08b
  verdict: changes-requested
  blocked_reason: 'Review cycle 2: F1 (LOW, R6) open/_serve docstrings promise ENOENT beyond the lookup under the source lock'
  cycle: 2
---
# TECH.md — open finds a source-served file while a replay moves it

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/source-served-open-race/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
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
  high-blast-radius functions (`open`, `_serve`, and the new `_lookup_again`), and
  `tests/engine/test_replay.py`. Geoffrey signs off on the final engine diff at review, whatever it finds
  (GOAL Q3). No phase edits any other file under `src/`, `truncate`'s body, `tests/engine/test_model.py`, or
  either `conftest.py`. A phase that finds it must is a reason to stop and re-read PLAN, and a
  `SCHEMA_VERSION` bump is a stop-and-ask.
- **§ Invariants text moves with the behaviour.** PLAN § 2.3's `AGENTS.md`, root `PLAN.md`, `ROADMAP.md` and
  `issues/` edits land in P1, the phase whose code makes them true. They are graded at review as a revision
  of the standard, on their merits.
- **No spec ids in code.** Comments in `src/` and `tests/` name constraints, never R-IDs or phase ids.
- **Where the gates run.** The development Mac, inside the agent sandbox, with these settings in every Bash
  call that runs `uv`, as separate statements:
  - `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and `UV_TOOL_DIR` set to per-session directories under the
    session scratchpad. The Python install dir must already hold a 3.15 interpreter.
  - `pypi.org` and `files.pythonhosted.org` in `allowed_domains`: `tools/lint.sh` runs an unpinned
    `uvx ruff`, `suite` runs `uv sync --locked`, and `seeds` builds two scratch environments.
  - Gate scratch, JUnit files and a copy of each run's output go under the worktree's gitignored
    `.local/open-gate/`. Nothing is deleted.
  - The shell is zsh. `rm` is blocked (move scratch aside instead). Quote globs and separator strings, and
    never name a variable `path`.
  - Measured on the planning prototype (development Mac, 2026-11-08): `regressions` and `mutants` together
    27 s before the review's folds (one serial `test_replay.py` run per mutant, about 1 s each); `records` about 2 s; `suite` 27 s; P1's
    whole verify 58 s; `seeds` 4 min 36 s on two workers (PLAN § 4.1). Every pytest run the gate starts is killed as a process group past 300 s. Run in
    the foreground with long timeouts (`seeds` needs the 10-minute cap). Never hand back while a gate is
    still running.
- **Hypothesis state.** `.hypothesis/` in the worktree is gitignored and replays found failures. `seeds` never
  reads it: each seed runs in a scratch tree from `git archive HEAD`, with any `.hypothesis/` moved aside
  first, never deleted. `CI=true` gives Hypothesis's fixed seed.

---

## Phase P1 — open and a promotion off the source resolve the place under the source lock
**Satisfies:** R1, R2, R3, R4, R5, R6 · **Depends on:** —
**Goal:** `open` of a source-served file, and a promotion off the source, read the place on the source from
the catalog under the source lock and open the object before releasing it, so a replay landing after the
lookup leaves the call on the file's own bytes. Nineteen tests pin it, twenty-one mutants give them teeth,
and the records say what is closed and who owns the identity gap. R6's evidence is committed text outside
`spec/`, so the blind reviewer can grade it.

- [x] `src/sosfs/engine/overlay.py`: PLAN § 2.1, as written there. `open`'s tail from `on_source` through
  `open_direct`; `_serve`'s promotion branch; `_lookup_again` after `_serve`; the docstrings of all three;
  the `_source_lock` comment in `__init__`. The docstrings of `open` and `_serve` carry the reused-id caveat
  and say "not", never "never", another entry's object. `truncate` stays byte-identical. Nothing under the source lock
  takes a path lock or calls `_ensure`, `getattr` or `readdir`.
- [x] `tests/engine/test_replay.py`: the nineteen tests of PLAN § 2.2, with exactly those names, and the
  helpers `AfterLookup`, `assert_reads` and `swap_onto`, **appended after the last existing test**, so
  every `test_replay.py:N` cite in `issues/` keeps holding. `AfterLookup` sets `ran` only after its action
  has succeeded, and re-raises that action's exception. Add the module-docstring bullet. No existing
  function or class changes. No sleeps, no timeouts. No helper action may need the path lock the test's
  thread holds.
- [x] `AGENTS.md`, per PLAN § 2.3: § 5, § 10 (takers, the delay list, the promotion's whole-copy hold and its
  S3/archive bound), § 11, both high-blast-radius tables, § Known open violations (drop the `open`/`_serve`
  row; the identity-gap row for `truncate`, `open` and the promotion, owned by
  `issues/entry-identity-token.md`; the recording row's `ENOENT`), and the counts (521 unit at four places,
  531 total, 183 engine tests).
- [x] Root `PLAN.md` § Risks, the namespace-replay entry, per PLAN § 2.3.
- [x] `issues/truncate-recording-after-ancestor-rename.md`: the Related line names
  `issues/entry-identity-token.md`; the counter-guard cite reads "which holds at `6425a21`". Its
  `ROADMAP.md` entry names `entry-identity-token` in place of "the `open` seed".
- [x] `issues/entry-identity-token.md`: append the dated line of PLAN § 2.3 (`open` and the promotion share
  the gap; reproduced by injection).
- [x] `issues/freethreaded-concurrency-stress.md`: append the dated note of PLAN § 5 (an ancestor's round
  trip between the tier decision and the act: another file's stage bytes, reproduced by injection; the
  promotion over a dirty stage copy, argued; the evicted stage copy, loud), recorded there as deferred work, and pin the "Path locks do not cover descendants"
  bullet's cites ("These line numbers hold at `5e0d997`").
- [x] Leave `issues/source-served-open-race.md` and its `ROADMAP.md` entry in place: `code terrace roadmap`
  retires both after landing (PLAN deviation row 6).
- [x] If a guarded line's text differs from a `MUTANTS` anchor in `verify/open_gate.py`, re-anchor it there in
  this commit, keeping the decision it attacks and the tests it names.
- **Verify:** `open_gate.py regressions mutants records suite`.
- **Touches:** `src/sosfs/engine/overlay.py`, `tests/engine/test_replay.py`, `AGENTS.md`, `PLAN.md`,
  `ROADMAP.md`, `issues/entry-identity-token.md`, `issues/truncate-recording-after-ancestor-rename.md`,
  `issues/freethreaded-concurrency-stress.md`, `spec/source-served-open-race/TECH.md`, and
  `spec/source-served-open-race/verify/open_gate.py` only to re-anchor.

## Phase P2 — Verify is green under the fixed CI seed and twenty random seeds
**Satisfies:** R7 · **Depends on:** P1
**Goal:** the profile's `verify` is green under `CI=true` with more than 502 passed, equal to `AGENTS.md`'s
count, and `test_model` passes for seeds 1 to 20 from committed state.

- [x] Run the verify. No code changes are expected. A red seed is a defect in P1's code, never a reason to
  touch `test_model.py`. It reopens P1 (`set_phase.py`), and the falsifying example becomes a deterministic
  regression in `test_replay.py`.
- [x] Record in the commit body: the passed count, the suite time, and the seeds' pass list.
- **Verify:** `open_gate.py suite seeds`.
- **Touches:** `spec/source-served-open-race/TECH.md` only.

## Phase P3 — CI is read from the run record, and the ledger closes
**Satisfies:** R7 · **Depends on:** P2
**Goal:** the four CI jobs conclude `success` on the pushed tree, read from the run record. The evidence is a
saved run record the foreman hands over, so the orchestrator grades R7's CI bullet, not the blind reviewer.

This phase waits on CI, so it is **two runs** (code-foreman → "External waits split the run").

**Run A — check, push, stop.**
- [x] Ledger: walk P1 and P2 for deferral wording and confirm each names an `issues/` seed with a
  `ROADMAP.md` entry (`ledger` does the mechanical half; prose and list items, never headings). This plan's
  records name `issues/entry-identity-token.md` (the identity gap, Q2) and
  `issues/freethreaded-concurrency-stress.md` (the ancestor round trip).
- [x] Commit (the phase stays `in_progress`; the body says "Run A"). Run `open_gate.py ledger prepush` on the
  committed tree. On green, push `fix/source-served-open-race` to `origin` under the standing cycle-branch
  approval. Stop and hand back the pushed SHA. Run A committed `8eacba4` and handed back; the foreman, not
  Run A, pushed `8eacba443023f28fa90392d240715a5a10e612bb` to `origin/fix/source-served-open-race`.

**Run B — read, grade, close.**
- [x] The foreman saves `gh run view <id> --json databaseId,headSha,headBranch,event,status,conclusion,jobs`
  for the push run to `.local/ci/run.json`, and the four job logs to `.local/ci/logs/<job id>.log`. Run the
  verify. `ci` takes the commit from `origin/fix/source-served-open-race` unless `--sha=` names another;
  commits after it may touch only `spec/source-served-open-race/`. Every job must conclude `success`, and
  Linux passed must equal `AGENTS.md`'s count with no skip. On macOS, passed plus skipped must equal Linux,
  with every skip under `tests/fuse` naming libfuse.
- [x] Record the run id, the pushed SHA and each job's counts in this phase, then close it.
- **CI record (Run B, 2026-11-08).** Run `42507324207`, event `push`, branch `fix/source-served-open-race`,
  head `8eacba443023f28fa90392d240715a5a10e612bb`, concluded `success`. `.github/workflows/ci.yml` at that
  commit is `main`'s.

  | Job | Id | Conclusion | Counts |
  |---|---|---|---|
  | Unit (Linux, 3.15) | 128812663347 | success | 521 passed, no skip (= `AGENTS.md`'s 521); libfuse loads |
  | Unit (Linux, 3.15t) | 128812663266 | success | 521 passed, no skip (= `AGENTS.md`'s 521); libfuse loads |
  | Unit (macOS, engine-only) | 128812663102 | success | 477 passed + 44 skipped = 521; all 44 skips under `tests/fuse`, naming libfuse |
  | Lint & build | 128812663324 | success | ruff (errors only): all checks passed; sdist and wheel built |

  The verify below ran green on `8eacba4` with this record (`.local/open-gate/p3b-verify-1.log`).
- **Verify:** `open_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs`.
- **Touches:** `spec/source-served-open-race/TECH.md` only.

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
