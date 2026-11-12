---
slug: racing-write-marked-clean
title: A write racing a commit is never recorded clean
kind: fix
appetite: small
status: done
branch: fix/racing-write-marked-clean
base: main
current_phase: done
last_updated: '2026-11-12'
phases:
- id: P1
  name: A hop records the changes that meet it, and goes clean only on that record
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
  - R9
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/remediation/F2-frozen-gate.py base suite records ledger && uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/remediation/F2-liveness-pin.py && uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/remediation/F1-agents-figures.py
review:
  last_reviewed_commit: d2bf13f96151b2f6cbc95cd7181cdca38e4e9383
  verdict: approved
  blocked_reason: ''
  cycle: 2
---

# TECH.md — A write racing a commit is never recorded clean

The **context engine and finite-state machine** for building this fix. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/racing-write-marked-clean/TECH.md`); the
per-phase checklist below is the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** none. The plan is lean by the foreman's ruling; PLAN § 4 records the prototype
  and the adversarial review.

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

---

## Phase P1 — A hop records the changes that meet it, and goes clean only on that record
**Satisfies:** R1, R2, R3, R4, R5, R6, R7, R8, R9 · **Depends on:** —
**Goal:** the engine change, its tests shown red on the base and green on the tree, and every record
the change makes untrue, in one commit. The phase is single because R8 and the same-commit rule put
the `AGENTS.md` sentences, the docstrings and the counts in the commit that changes the behaviour.
It touches the coupled core and the high-blast-radius list: not parallel, never hammered.

**Who grades what.** R6's record (`verify/base.txt`, `verify/final.txt`) and the gate live under
`spec/`, which the blind reviewer does not see: the orchestrator grades that half of R6. R9's CI half
(every job `success` on the pull request) is read at publish, by the foreman. Everything else is
behaviour the blind reviewer can drive.

**The names.** This branch is rebased onto the rename before the build. Paths below use the new
names. The gate finds the package directory itself (`src/*/engine/overlay.py`).

- [x] **Preflight.** `git merge-base HEAD origin/main` is the rename's commit or later and
      `src/terrace/engine/overlay.py` exists. If not, stop: the rebase has not happened.
- [x] **Tests first.** Copy `spec/racing-write-marked-clean/verify/test_racing.py.draft` to
      `tests/engine/test_racing.py` (it already imports `terrace`). Keep the test names: the gate's
      lists name them, and a test added or renamed changes `BASE_FAILS`, `BASE_PASSES` or `MECHANISM`
      in `verify/racing_gate.py` in the same commit. Four tests reach into `engine._handles_mutex`,
      `engine._hops`, `handle.changing` and `engine.locks._locks`; keep those names, or change the
      tests with them.
- [x] **Show them red before the fix.** With the engine still untouched, run the gate's `base` mode
      alone. It adds a detached worktree of the base commit at
      `<main checkout>/.worktrees/racing-write-marked-clean-base`, runs the new module there by
      absolute path against the base's package, requires every `BASE_FAILS` test to fail with
      `assert 'clean' == 'dirty'` and every `BASE_PASSES` test to pass (the `MECHANISM` tests are not
      run there), runs the base's own unit suite for its count, and closes the worktree with
      `git worktree remove`. It writes `verify/base.txt`.
      If the worktree is left behind, close it with `git worktree remove`, never by force or by
      deleting it.
- [x] **`src/terrace/engine/overlay.py`**, as PLAN § 2 gives it:
  - `Handle` gains the slot `changing` (0 at open).
  - `OverlayEngine.__init__` gains `_hops`, with a comment saying the mutex guards it, the handle
    table and each handle's `marked_dirty` and `changing`.
  - `_change(handle)` and `_meet(entry_id, tier)`. The flag is read and the change admitted in one
    hold of the mutex; retirement is in a `finally`.
  - `write` and `truncate` through a handle run their `os.pwrite` / `os.ftruncate` inside `_change`.
    No lock is held across either call.
  - `_mark_handle_dirty` sets the flag and admits under the mutex, inside the path lock, after
    `mark_dirty` has returned. With no upstream it sets and admits with no SQL.
  - `truncate` with no handle calls `_meet(current.id, serving)` under the mutex, after its
    `mark_dirty` and before its cut.
  - `_commit_hop`: the record's key is `(entry.id, holder)`, with `entry` as each branch reads it
    (the source branch reads it under the source lock); pass `before_read` in **both** branches; the
    opening keeps a record that is already there (`_hops.get(key, False) or …`);
    stat; then the one hold that pops the record (`pop(key, True)`) and resets the flags; `changed`
    is `met or` the existing stat comparison. On an exception, remove the record and re-raise. The
    recording transaction is not edited.
  - `commit_row`, `commit_upstream`, `commit_to_shared`, `evict_copy`, `release` and `_record` are
    not edited.
- [x] **`src/terrace/engine/transfer.py`**: `copy` takes `before_read` and calls it inside its `try`,
      immediately before `source.stat(...)`. `submit` is not edited.
- [x] **Docstrings and comments** the change makes untrue: `Handle`, `write`, `truncate` (its first
      sentence and the handle branch), `_mark_handle_dirty`, `_commit_hop` (it says a racing write
      "shows in the holder's stat"), `TransferPool.copy` (the new keyword). No spec ids in `src/` or
      `tests/`.
- [x] **`AGENTS.md`**, in the same commit (drafts of each edit, as the prototype carried them, are in
      `verify/records.draft.md`):
  - § *Known open violations*: remove the row that names `issues/racing-write-marked-clean.md`. The
    seed file and its `ROADMAP.md` entry stay; `code terrace roadmap` retires them after landing.
    Add a row for `issues/stage-handle-outlives-its-file.md`, citing the seed, and correct the count
    of rows the section's first sentence gives (amendment 1).
  - § 1: `write` reaches `_mark_handle_dirty` through `_change`; say so (amendment 3).
  - § 6: replace the bullet "After a copy, `changed` detection re-arms rather than settles" with the
    rule the code keeps: the record opens at `before_read`, every change writes it under
    `_handles_mutex`, the stat only vetoes, the reset comes after the stat in the hold that reads the
    record, the reset is unconditional and why, and what `commit_upstream` does while a change is in
    flight.
  - § 10: one bullet for `_handles_mutex`: what it guards (the table, `marked_dirty`, `changing`,
    `_hops`), its new holders, and that it is never held across I/O, SQL or a wait. The order is
    unchanged.
  - § *High-blast-radius paths*: add `_change` and `_meet` to the `write` / `_mark_handle_dirty` /
    `truncate` row in both tables.
  - The counts: the unit count in § *Commands*, § *Repository map* (with the file count and the
    total), § *Conventions* and § *Verification*, each equal to what the gate's `suite` mode
    measures.
- [x] **Deferred, with its home.** Measuring the contention of the engine-wide mutex on the write
      path under the free-threaded build is left for `issues/freethreaded-concurrency-stress.md`
      (`ROADMAP.md` lists it). Append one dated line to that seed's notes naming
      `racing-write-marked-clean` and `_handles_mutex`: `marked_dirty` is now guarded, `changing` and
      the hop records are new, `written` is still unguarded, and every change through a handle takes
      the mutex twice. The same cost goes in a dated note to `issues/read-path-metadata-writes.md`
      (`ROADMAP.md` lists it), so it is measured when that cycle runs (amendment 1).
- [x] **Deferred, with its home.** Bounding `commit_upstream` while an admitted change is in flight
      (PLAN, deviation row 7) is left for `issues/pump-liveness-and-loss-windows.md` (`ROADMAP.md`
      lists it). Append one dated line to that seed's notes naming `racing-write-marked-clean` and
      `commit_upstream`: the loop copies again for as long as a change is in flight on the holder,
      with the path lock held, and the measured case (26 copies for a write held across 25).
- [x] **Already filed by the plan.** `issues/stage-handle-outlives-its-file.md` and its `ROADMAP.md`
      entry record the defect the design review reproduced (PLAN § 5). The build does not fix it. It
      adds a row for it to `AGENTS.md` § *Known open violations*, citing the seed (amendment 1); the
      gate's `ledger` mode checks the seed is still there.
- [x] **Deferral ledger.** Walk this file and the build's own notes for "do not fix here", "known
      limitation", "left for" or "follow-up". Each one names an `issues/{slug}.md`, pre-shaped from
      the ISSUE template when it is new, and `ROADMAP.md` lists it. An unrecorded deferral fails the
      phase. The gate's `ledger` mode checks the item above.
- [x] **Run the verify.** It runs `base` again (the record must come from the final test file), then
      `suite`, `records` and `ledger`. `suite` writes `verify/final.txt`. Stage `verify/base.txt` and
      `verify/final.txt` with the phase.
- [x] **Fail it once.** Build's fail-once mutation targets this phase's own decision: with the
      `met or` dropped from `changed` in `_commit_hop`, the `suite` mode must go red on both
      reproductions. `records` and `ledger` run alone in seconds for a mutation of the records.
- **Verify:** `uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/verify/racing_gate.py base suite records ledger`
  (63 s for the four modes in one run on the planning prototype, 10 cores, 2026-11-12, with 31 new
  cases and 762 passed; exit 0 with `--- ledger: green` as its last line). Post-conditions it asserts: each reproduction fails on the base and passes on the tree; 0
  failed, 0 errors, passed above the base's count; `AGENTS.md` states the measured count.
- **Touches:** `src/terrace/engine/overlay.py`, `src/terrace/engine/transfer.py`,
  `tests/engine/test_racing.py`, `AGENTS.md`, `issues/freethreaded-concurrency-stress.md`,
  `issues/pump-liveness-and-loss-windows.md`, `issues/read-path-metadata-writes.md`,
  `spec/racing-write-marked-clean/verify/{base.txt,final.txt}`,
  `spec/racing-write-marked-clean/TECH.md`. The gate, `verify/racing_gate.py`, is frozen at the plan
  commit and is not edited.

**Amendments (build, 2026-11-12).**

1. *The foreman's rulings at the plan sign-off.* PLAN's seven deviation rows stand as written. The
   defect in `issues/stage-handle-outlives-its-file.md` is reproduced and pre-existing, so
   `AGENTS.md` § *Known open violations* gains a row for it in this phase's commit, beside the removal
   of this cycle's own row; PLAN § 5 had left that to the foreman, and the checklist above first said
   no row. Row 4's cost (two holds of the engine-wide mutex per change through a handle) is noted in
   `issues/read-path-metadata-writes.md` as well as `issues/freethreaded-concurrency-stress.md`.
2. *The record's key.* The prototype read the entry once before the branch for the key and again
   under the source lock. The build takes the key in `before_read`, from the entry its branch read,
   as the checklist says; a hop whose copy never calls `before_read` has no key and is read as met.
3. *Records the checklist did not list.* § 1's sentence on `write` now names `_change`. § 6 carries
   the rule as three bullets, and names the two costs PLAN's rows 5 and 7 accept and the premise
   PLAN § 2 states (a handle's fd names its tier's current file). § 10's bullet says `Handle.written`
   is still unguarded and that the mutex's contention is not measured.
4. *The fail-once mutation* ran `suite` alone, as the checklist says; `records` and `ledger` were
   not mutated.

**Remediation, review cycle 1 (build, 2026-11-12).** `REVIEW.md` requested changes on `99851a7`: F1
(HIGH) and F2 (LOW, in `_commit_hop`). P1 is reopened, and its `verify:` is retuned to run the frozen
gate under a wrapper, then the two class checkers, the F1 checker last. No engine change.

- [x] **F1, the class: every measured figure `AGENTS.md` carries equals what the tree measures.**
      Re-measured every count this cycle's diff can move, after F2's test landed: 763 `unit`, 10
      `integration`, 773 in all, 425 in `tests/engine`, 43 `.py` files under `tests/`, 76 under `src/`
      and `tests/`. Corrected § *Commands* (762), § *Repository map* (772 and 762), § *Architecture*
      (393, the review's instance), § *Conventions*' integration gate (762 deselected), § *Verification*
      (762) and § *Code style* (75, the review's instance); the integration figures and the file count
      under `tests/` held. Checker: `remediation/F1-agents-figures.py` reads the 12 figures in their 8
      sentences, the number word "ten" included, requires each sentence once, and compares each with
      pytest's collection and the file walk `tools/lint.sh` uses. It went red on two planted stale
      figures. The timings are approximate and not checked; the gate run below measured the
      parallel unit run at 30.54 s against the stated ~30 s.
- [x] **F2, the pin: the flag reset after a hop is pinned on both sides.** Liveness:
      `tests/engine/test_racing.py::test_writer_that_never_pauses_lets_commit_upstream_return` offers
      a write from a fresh thread at each hop's last look at the holder, before the hop takes its
      record, up to six; it asserts the first is admitted, the second asks for the path lock, the
      commit returns after exactly two copies, the source holds the first write, and the second stays
      queued and reaches the source. The schedule is injected (the holder's `stat`, the copy, and
      `locks.hold` observed); there is no sleep and no ratio. Completeness, the other side: a write
      that finds its flag reset marks under the path lock
      (`test_write_held_before_admission_across_a_whole_commit`,
      `test_first_write_is_admitted_under_the_path_lock`), and a write admitted inside a hop is on its
      record and leaves the copy dirty (`test_same_size_write_behind_an_unchanged_stat_reaches_the_source`,
      `test_second_handles_first_write_inside_the_hop`,
      `test_write_admitted_inside_the_hop_and_still_in_flight_at_its_end`); the new test also asserts
      the copy is truthful. Checker: `remediation/F2-liveness-pin.py` runs the six tests on the tree,
      then plants the review's surviving mutant (the reset written `bool(met)`) in a copy of `src/`
      under `.local/` and requires the new test to fail on its outcome assertion while the other five
      pass.

5. *The frozen gate under a wrapper.* `verify/racing_gate.py` requires `test_racing.py` to hold
   exactly the tests it names, on the base and on the tree, so F2's test makes it red: run unchanged,
   its `suite` mode stopped on "the new module holds the tests this gate names, and no other" (seen
   red before the wrapper was written). `remediation/F2-frozen-gate.py` loads the gate from its path,
   adds the new test to `MECHANISM` (a test of the record's reset, which the base lacks: not run there,
   required to pass here) and calls its `main`. The gate's file is not edited.
6. *The mutant sweep.* The module-wide sweep the review routed for F2 is not run in this cycle, by the
   foreman's ruling: the plan planted 15 mutants and the review ran a 612-case injection matrix and 12
   mutants, and the one survivor is now killed. The sweep is filed as a dated note on
   `issues/freethreaded-concurrency-stress.md`, which `ROADMAP.md` lists.
7. *Teeth.* The F2 mutant was also planted in place, with a `cp` backup restored and checked by
   `cmp -s`: the new test failed (six writes admitted, against one), and the module's other 31 tests
   passed. The new test passed 200 runs in 20 concurrent processes beside a full `-n auto` unit run.

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
