# REVIEW — Redraw the root PLAN.md around the user story

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent; where the runtime
> has none, `code-review`'s sequential fallback). The correctness pass grades the branch diff against
> [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `16da8b7`  ·  **Base:** `main`  ·  **Date:** 2026-11-16
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** fresh, a blind pass over the full spec-excluded diff (`git diff origin/main...HEAD -- .
  ':(exclude)spec/'`; `main` merged in at `3c82093`). One fresh reviewer for R1–R7, given `GOAL.md`
  and the rubric by exact path, and the root `PLAN.md`, `AGENTS.md`, `ROADMAP.md`, `issues/` and the
  git object `a0c88f1:issues/local-bundle-loop.md` to read. **R8 and R9 are met by committed records
  under `spec/` and were graded by the foreman (orchestrator)**, not the reviewer. The subagent route
  ran. No `debate`, no completeness sub-pass.
- **Contract:** `GOAL.md` carries only its shape commit (`7e36368`).

Evidence: the reviewer's in the worktree's gitignored `.local/review1/` (`R1/` below); the foreman's
in `.local/orch1/`.

## Verification run

- `tools/lint.sh`: all checks passed. `uv run pytest -n auto -m unit`: **824 passed, 9 xfailed**
  (833), 35.47 s (`R1/verify-pytest.log`). Collection 852 / 833 unit / 19 integration; 425 engine
  tests (`R1/counts.txt`).
- R1 quote, line-exact against the git object (`diff` exit 0, `R1/r1-quote-compare.txt`).
- R4's single-host check run once as the document gives it, in a scratch directory: the five
  expected lines, `B1 HOLDS` and `B2 HOLDS, nothing left behind` (`R1/r4-single-host-output.txt`).
- Every code citation of § *What the tree does not yet do* and all 16 *Overturned by …* markers
  opened at `16da8b7` and true (`R1/r6-*.txt`). 46 seeds paired with 46 `ROADMAP.md` entries
  (`R1/r7-seed-pairing.txt`). `AGENTS.md` numstat 51/0 against `main`. All 43 paths and every §
  reference in the root `PLAN.md` resolve (`R1/plan-links.txt`). No internal name in the added lines
  or the commit messages (`R1/internal-names-grep.txt`).
- **P1's gate, run by the foreman** on `16da8b7` as stored in `TECH.md`: exit 1 in 152 s
  (`orch1/p1-gate.log`), on its last clause alone (F3). With that clause replaced by the
  additions-only form P4 gave P2's and P3's gates: exit 0 in 157 s (`orch1/p1-gate-retuned.sh`,
  `orch1/p1-gate-retuned.log`) — both prototypes ran on the landed helper and printed their `RESULT …
  yes` lines, and the record's three lines hold.
- CI at `16da8b7` (run 43908794052), read by the foreman: all four jobs green.

## Requirement → evidence matrix

| R | Verdict | Verified how | Who |
|---|---|---|---|
| R1 | ✅ | Statement line-exact against `a0c88f1:issues/local-bundle-loop.md`; C1–C6 word for word (C6's links rebased to the root); attributed, dated; every later section opens with a *Follows from:* line | reviewer |
| R2 | ✅ | Rulings A–J in one table with date, reason, alternative turned down; G names its owner; H and I in the non-goals; quotes match their seeds; none worded as open | reviewer |
| R3 | ✅ | Decisions 1–7 each with reason, alternative and evidence; the two sign-off rulings of 2026-11-15 recorded | reviewer |
| R4 | ✅ (F1) | Assumption, local run, what was not run, what changes on failure, the commands, the holding output, the failing-line table and the single-host variant are stated; the variant ran and held | reviewer |
| R5 | ✅ | (a)–(h) each a subsection; (f) agrees with `ROADMAP.md`; (g) cites the R9 record's results | reviewer |
| R6 | ✅ (F2) | Every citation and overturned marker true of the tree; `AGENTS.md` notes and rows present | reviewer |
| R7 | ✅ | Seeds and entries paired; `local-bundle-loop` retired; additions-only `AGENTS.md`; figures equal the tree; nothing under `src/`, `tests/`, `tools/` changed | reviewer |
| R8 | ✅ (F3) | `prototype/RECORD.md` states what ran, on helper `a0c88f1`, trees equal in 18 of 18 runs, requests by kind per window; Decisions 2–4 cite those runs; the prototype re-ran green on the landed helper under P1's gate with its last clause retuned | foreman |
| R9 | ✅ (F3) | The record states what ran, on the same helper, 504 of 504 processes read the oracle's bytes, and the fetch count per scenario; the root `PLAN.md` (g) cites it; re-ran green as above | foreman |

## Findings

### [LOW/CONFIRMED] F1 · The check's `wal` run is described as the tree's mode, and is not
- **Where:** root `PLAN.md:880-881` (and the same claim at `PLAN.md:250`); `issues/hpc-deployment.md:148`.
- **Failure scenario:** the documents say that `wal` as a last argument "runs the tree's mode instead";
  the check's own `db()` (`PLAN.md:892`) always sets `PRAGMA synchronous=FULL`, so the `wal` run is
  WAL with `FULL`, while the tree runs WAL with `synchronous = NORMAL` (`src/terrace/state/db.py:94-95`).
  A maintainer comparing modes on his filesystem would read WAL+FULL as the tree's behaviour. The
  default run, which R4 requires, is correct.
- **Evidence:** `R1/probe-wal-sync.txt`: the check's `db()` and mode parsing, run in a scratch
  directory, read back `journal_mode=delete synchronous=2` with no mode argument and
  `journal_mode=wal synchronous=2` with `wal`.
- **Touches:** R4.
- **Property to route:** every statement of what a mode of the check runs is what that mode runs; if
  a mode is offered as "the tree's", it runs the tree's journal mode and synchronous level both.

### [LOW/PLAUSIBLE → fix] F2 · Decision 1's consequence for the loop helper and its tests is not recorded
- **Where:** root `PLAN.md:290` (the Decision 1 row of § *What the tree does not yet do*);
  `issues/archive-spec-plumbing.md` (its 2026-11-16 note).
- **Concern:** Decision 1 retires `staging` and puts the staged bundle inside the durable tier's root;
  the row cites `tier/archive.py:236-241` and `tier/spec.py:25,:28` only. The landed helper writes
  `staging = "<base>/scratch/<stem>.tar"` outside every tier root (`tools/bundle_loop.py:201-204`,
  whose comment points at the example this diff removed), a passing unit test asserts the staged path
  lies in no tier root (`tests/loop/test_helper.py:92`), and stage 8 asserts the bundle at that path
  (`tests/loop/stages.py:402`, owned by `offline-commit-command`). The cycle that builds Decision 1
  must change all three, and no seed, entry or row says so.
- **Evidence:** `R1/helper-spec-staging.txt` (the helper's printed spec carries `staging = …`); a grep
  of `PLAN.md`, `ROADMAP.md` and `issues/` finds no note tying them to Decision 1.
- **Triage (foreman, 2026-11-16): fix.** It sits on R6's own wording ("what the tree does not yet
  do"): the helper and its test are part of the tree, so the row is incomplete. Not deferrable.
- **Property to route:** every place in the tree that encodes the rule a decision overturns — engine,
  tools and tests alike — is named in that decision's row and in the owning seed's note.

### [LOW/CONFIRMED] F3 · P1's stored gate can no longer pass, found by the foreman
- **Where:** `spec/plan-the-bundle-loop/TECH.md`, P1's `verify:`.
- **Failure scenario:** P1's gate ends with `git diff --cached --quiet --merge-base main -- … AGENTS.md`,
  which P4's additions to `AGENTS.md` break by design; P4 retuned P2's and P3's gates for the same
  clause (code-build Step 1.3) but not P1's. Run as stored on `16da8b7`: exit 1.
- **Evidence:** `orch1/p1-gate.log` (exit 1), `orch1/p1-gate-retuned.log` (exit 0 with the clause
  retuned).
- **Property to route:** every phase's stored `verify:` passes on the cycle's final tree.

## Human-gate triggers

None: nothing under `src/` changed, and no finding sits in a high-blast-radius path.

## Out of scope, noted and not graded

- `AGENTS.md:1108` (*Out of scope for v1*) still lists "multi-client coherence" and points at the
  root `PLAN.md`'s non-goals, which no longer use the phrase and now put a read-only shared tier in
  v1 scope. `AGENTS.md` changes in this cycle are additions only by design.
- The `AGENTS.md` summary says §§ 2, 5, 7, 8 and 10 overturn "a rule summarized above"; § 10's rule
  is not among the summaries.
- The root `PLAN.md`'s CI-matrix line gives the wrong reason the macOS job is engine-only
  (`tests/fuse/conftest.py` skips without libfuse; on a macOS host where libfuse loads, 44
  `tests/fuse` unit tests pass).
- Decision 7 says `issues/shared-staged-tier.md` "reopens this rule if it wants one", while the list's
  introduction says revisiting a decision is the maintainer's call.
