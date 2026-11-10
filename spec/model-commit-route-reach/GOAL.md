# GOAL — The model commits through a scheduler poll and through eviction while a rename is queued

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** model-commit-route-reach
- **kind:** test
- **appetite:** small

## Problem

`AGENTS.md` § Invariants § 5 promises that a data commit made while a namespace row is still queued
leaves no orphan on the source. The `deferred-rename-source-orphan` cycle closed the defect that broke
it, and three production routes reach that commit:

- `fsync` under `fsync_policy = "commit"` (`src/sosfs/engine/overlay.py:868`);
- the eviction manager committing a dirty staged copy (`src/sosfs/engine/evict.py:151`);
- a write-back scheduler poll that settles an entry's data row before its rename row
  (`writeback.py:90` `poll` → `overlay.py:939` `commit_row`).

That cycle gave the stateful model (`tests/engine/test_model.py`) rules that commit one entry and run
one scheduler poll, and a convergence check after a drain by polls (its GOAL R7, R8; PLAN § 2.7). The
rules fire about two thousand times each per run, but two routes almost never reach the state that
matters: a commit to the source while the entry's rename, or a directory's above it, is queued. The
cycle's first review (`REVIEW.md` F4, 2026-11-06) instrumented `OverlayEngine._commit_hop` over the
fixed CI draw (`CI=true`, Hypothesis's derandomized sample) and counted such commits:

| Route into `_commit_hop` | Fixed CI draw (1023 examples) |
|---|---|
| `fsync` under the commit policy | 7 |
| `rewrite_rename_commit` (a direct `commit_upstream`) | 46 |
| a scheduler poll (`commit_row`) | 0 |
| the `evict` rule (commit a dirty copy, then drop it) | 0 |

Review cycle 2 recounted, taking only commits made while the entry's *own* rename was queued, and
recorded "2 / 34 / 0, eviction 0" (`REVIEW.md:307`). The record does not name the order; read in the
order of the sentence before it, that is 2 through `commit_row`, 34 direct and 0 through `fsync`. So the
figures depend on how a commit is counted and are not a baseline. They agree only that the scheduler
and eviction routes are almost never reached; the fsync route may be thin as well.

The scheduler route needs a data row due before its entry's rename row: a write, a rename
of that file, then a poll whose clock lands past the data row's deadline and short of the rename row's.
`poll_next_due` exists for that window and still rarely lands in it. The `evict` rule needs its random
path to name a file whose staged copy is dirty and whose rename is queued. So the model's convergence
check, the net for defects nobody has named yet, is not cast over those two routes.

Nothing is unguarded today. `tests/engine/test_replay.py` pins both routes deterministically
(`test_eviction_commit_before_queued_rename` :301, `test_scheduler_poll_settles_data_before_rename`
:317), and the fix-removal mutant for a commit at the logical path fails the model through the routes it
does reach.

**Why it was deferred.** Hypothesis derives the fixed CI draw from a digest of the `OverlayMachine`
class source, docstrings included and comments excluded. Any edit to the class redraws the sample that
CI and every earlier cycle's gate were graded on. No cycle since has edited `test_model.py`; it is
unchanged since `fe26962`. Geoffrey has now allowed this cycle to edit it, on condition that the cycle
re-grades on the new draw (Clarifications).

## Outcome / vision

Under the fixed CI draw, the model commits to the source through a scheduler poll and through the
`evict` rule while the entry's rename, or an ancestor directory's, is queued. Both counts are measured,
not assumed, and the reach comes from the model's structure rather than a lucky draw. The routes the
model already reaches keep reaching. The model loses nothing: every rule, strategy and setting holds.
On the new draw, `verify` is green under `CI=true` and on twenty random seeds, every fix-removal mutant
the model must fail still fails it, and CI's four jobs conclude `success`. `src/` is untouched.

## Acceptance criteria (the contract)

These terms hold throughout:

- *The base* is `980f998` (`origin/main` when this cycle opened).
- *A queued-rename commit* is a call to `OverlayEngine._commit_hop` whose destination is the source
  tier, made while `journal.queued()` holds a `rename` row for the entry being committed or for a
  directory above it.
- *The route* of a queued-rename commit is the path that reached `_commit_hop`:
  - *scheduler*: through `OverlayEngine.commit_row` on a data row;
  - *eviction*: through the model's `evict` rule, or through `EvictionManager` if the plan drives it;
  - *fsync*: through `OverlayEngine.fsync`;
  - *direct*: through the `commit_upstream` calls of `rewrite_rename_commit`.
  The plan states how its instrumentation attributes a commit to a route.
- *An instrumented run* is a run of `tests/engine/test_model.py` that counts queued-rename commits per
  route by wrapping engine code from outside the test module. It leaves the committed
  `OverlayMachine` source, and so the draw, exactly as graded. It is committed as a gate under
  `spec/model-commit-route-reach/verify/`, not as a unit test (Q5).
- *The mutants the model must fail* are the entries of `MUTANTS` in
  `spec/deferred-rename-source-orphan/verify/replay_gate.py` marked for the model: M1 (commit at the
  logical path), M2 (stale rows looked up by id alone), M3 (vacate ignores other holders) and M7 (an
  rmdir row runs before its moved-out children). Where a mutant's planted text no longer matches the
  base, the plan re-anchors the same fix removal and says so.
- Counts are the numbers pytest prints in its summary line, asserted as numbers (`AGENTS.md`
  § Verification).

The criteria:

- **R1 — reach on the fixed draw.** WHEN an instrumented run executes under `CI=true` on the final tree,
  the model SHALL make at least *N* queued-rename commits through the scheduler route and at least *N*
  through the eviction route. *N* is at least 1, stated and justified in `PLAN.md`, and measured. The
  same instrumented run on the base is recorded as evidence of the starting point (0 and 0 per the
  seed, re-measured under this GOAL's definition). Re-measuring the base also settles how thin the
  fsync route is today, which R3 depends on. Each route has one count, own and ancestor renames
  together, and the gate reports the split between them (Q4).
- **R2 — reach is not one draw's luck** (Q2). WHEN an instrumented run executes
  without `CI` set, once for each `--hypothesis-seed` from 1 to 20, every run SHALL make at least one
  queued-rename commit through the scheduler route and at least one through the eviction route.
- **R3 — no coverage traded away.** WHEN the instrumented run of R1 executes on the final tree, the
  fsync route and the direct route SHALL each make at least a floor stated in `PLAN.md`, justified
  against the base's re-measured count for that route, and never zero (Q7).
- **R4 — a scheduler-route defect turns the model red** (Q3). WHEN a mutant that
  breaks the engine's handling of a queued rename only on the scheduler route (the plan names it and
  plants it in a scratch copy of the final tree's `src/`) is planted, `test_model` SHALL fail under
  `CI=true`, and the same mutant SHALL fail `test_scheduler_poll_settles_data_before_rename`.
- **R5 — the model is not weakened.** The model on the final tree SHALL keep every rule, invariant and
  teardown check it has at the base. Its strategies SHALL be no narrower, and its settings no lower
  than the base's `max_examples=1000` and `stateful_step_count=50` (`test_model.py:415`). It SHALL add
  no skip guard, `assume`, precondition, filter or exception handler that retires coverage the model
  has at the base.
- **R6 — the required mutants still fail the model.** WHEN each of M1, M2, M3 and M7 is planted in a
  scratch copy of the final tree's `src/`, `test_model` SHALL fail under `CI=true`, with a falsifying
  example (shrinking may be off for that run).
- **R7 — the records**, in the same commit as the change they describe, saying what ships and no more:
  - `test_model.py`'s class docstring SHALL describe the rules the class has (it counts "three
    composites" at the base).
  - The measured counts in `AGENTS.md` SHALL match the final tree wherever the change alters them:
    § Commands (`:70`), § Repository map (`:111`), the engine-test count (`:136`), § Conventions
    (`:693`) and § Verification (`:734`), including the test_model timing if it moves.
  - Q1's home SHALL be written in this cycle: `issues/model-source-served-reach.md`, `status:
    unshaped`, carrying the history of the seed's 2026-11-06 note, and its `ROADMAP.md` entry.
  - The seed is `adopted:model-commit-route-reach` from this GOAL's commit. Once the roadmap step has
    retired it and its `ROADMAP.md` entry, no file outside `spec/` SHALL name
    `issues/model-commit-route-reach.md`.
- **R8 — the gate.** All of these SHALL hold:
  - WHEN the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`) runs with `CI=true`
    on macOS, `tools/lint.sh` SHALL exit 0, and pytest SHALL report 0 failed and 0 errors, with a
    passed count no lower than 548 (the base) and equal to `AGENTS.md` § Verification's count.
  - WHEN `uv run pytest tests/engine/test_model.py` runs on macOS without `CI` set, from a tree with no
    `.hypothesis/` directory, once for each `--hypothesis-seed` from 1 to 20, every run SHALL pass.
  - WHEN the CI workflow runs on this cycle's final tree, each of `Lint & build`, `Unit (Linux, 3.15)`,
    `Unit (Linux, 3.15t)` and `Unit (macOS, engine-only)` SHALL conclude `success`, as read from the
    run record.

## Non-goals (no-gos)

- **A source-served configuration for the model** (a stage `bypass_size`). Home:
  `issues/model-source-served-reach.md`, written in this cycle (Q1, R7).
- **Driving the real `EvictionManager` through a stage quota**, unless the plan finds R1's eviction
  route needs it. If it does, the quota goes through `dataclasses.replace` on the spec, as
  `fsync_policy` already does; `tests/engine/conftest.py` is an interface (`AGENTS.md` § Conventions)
  and stays unchanged.
- **Any `src/` change.** This is a test cycle. A defect the new reach finds is a stop-and-ask
  (Q6, Constraints).
- **New deterministic tests in `tests/engine/test_replay.py`.** Both routes are pinned there already.
- **A standing reach assertion in the unit suite** (Q5). The counts are a gate's evidence, not a test.
- **The integration lane and real threads.** Coverage stays engine-level under `-m unit`. Homes:
  `issues/ci-integration-lane.md`, `issues/freethreaded-concurrency-stress.md`.

## Constraints

- **`src/` is byte-identical to the base**, and so are `tests/engine/conftest.py` and
  `tests/engine/test_replay.py`. The only `tests/` file this cycle edits is `tests/engine/test_model.py`.
- **The model keeps its guards** (R5; `AGENTS.md`'s high-blast-radius row for the engine test modules,
  `:238`).
- **The redraw is re-graded, never tuned.** Every claim the cycle makes about the model is measured on
  the final tree's draw: R1, R3, R4, R6 and R8. No text in the class is edited to steer the draw;
  reach that holds only on one draw is what R2 rules out.
- **No `src/` or `tests/` text cites an `issues/` seed** (Geoffrey's directive, 2026-11-09), and no
  feature-scoped spec id (`AGENTS.md` § Conventions).
- **A defect the new reach finds is a stop-and-ask** (Q6). If the model goes red on the final tree for
  an engine defect, the builder records the falsifying example and stops. It writes no `src/` change
  and does not weaken the model. The foreman brings it to Geoffrey.
- **Same-commit records** (R7, `AGENTS.md` § Conventions).
- **Human sign-off at review is conditional.** This is a test-only cycle, so Geoffrey's sign-off is
  triggered only by a CONFIRMED review finding in a high-blast-radius path. `tests/engine/test_model.py`
  is on that list (`AGENTS.md:238`), so a CONFIRMED finding in the model triggers it.

## Clarifications

Resolved:

- **Q:** May this cycle edit `tests/engine/test_model.py`? — **A:** Yes. The edit redraws the fixed CI
  sample, so the cycle re-grades on the new draw: the profile's `verify` under `CI=true`,
  `--hypothesis-seed` 1–20, and every fix-removal mutant the model is required to fail. The model keeps
  every rule, keeps its strategies no narrower, and keeps `max_examples` and `stateful_step_count` no
  lower. It gets no skip guard, `assume`, precondition or filter that retires coverage. (Geoffrey,
  2026-11-09, in the foreman's session)
- **Q:** When does Geoffrey sign off at review? — **A:** Only on a CONFIRMED review finding in a
  high-blast-radius path, as for any test-only cycle; `test_model.py` is on the list. Recorded under
  Constraints. (foreman's brief, 2026-11-09)
- **Q:** Kind and appetite? — **A:** `test`, `small`, as the seed gives them. One deliverable (the
  model's reach) with its evidence, eight criteria, three of them the model's guards, records and the
  gate. The size circuit breaker does not trip. (recorded 2026-11-09, shaper)
- **Q:** Does the seed's draft contract carry over? — **A:** As input only. Its R1 becomes R1, with the
  queued-rename commit and the route defined, the floor's form fixed and the base re-measured; R2
  (reach on random seeds) and R3 (no coverage traded away) are new. Its R2 becomes R5, with the
  settings floors stated (1000 and 50) and exception handlers added. Its R3 splits into R6 (the
  mutants, named) and R8 (the gate, with CI's four jobs added). R4 (detection), R7 (records) are new.
  (recorded 2026-11-09, shaper)

Ruled at the GOAL gate. Each takes the shaper's recommended answer:

- **Q1 — Is a source-served configuration for the model in scope?** — **A:** (A), out. This cycle writes
  `issues/model-source-served-reach.md` and its `ROADMAP.md` entry in P1's records (R7). The seed stays
  `unshaped` and carries the history of this seed's 2026-11-06 note. A `bypass_size` stage reaches the
  source-served `open`, `truncate` and stat paths cycles 4–6 changed: a second deliverable with its own
  oracle questions, which `small` does not hold. (ruled 2026-11-09, foreman as gate judge)
- **Q2 — Must the reach hold on random seeds too?** — **A:** (A), yes. Every one of seeds 1–20 reaches
  each of the scheduler and eviction routes at least once (R2). A floor met only on the CI draw would
  hold until the next edit redraws it. (ruled 2026-11-09, foreman as gate judge)
- **Q3 — Must a defect be detected, not just reached?** — **A:** (A), for the scheduler route only (R4).
  `commit_row` is a distinct engine path, so a mutant confined to it exists. The eviction route shares
  `commit_upstream` with `commit_entry` in the model, so it is graded on reach (R1, R2) and on R6's
  mutants. (ruled 2026-11-09, foreman as gate judge)
- **Q4 — Do own and ancestor renames share one floor?** — **A:** (A), one floor per route, counting
  both, with the own-rename and ancestor-rename split reported (R1). (ruled 2026-11-09, foreman as gate
  judge)
- **Q5 — Where does the reach evidence live?** — **A:** (A), a gate under
  `spec/model-commit-route-reach/verify/`, patching from outside so the draw is untouched; no standing
  unit test. A later edit to the model can lose the reach without CI noticing; that trade is accepted.
  (ruled 2026-11-09, foreman as gate judge)
- **Q6 — What if the new reach finds a real engine defect?** — **A:** (A), stop and ask. The builder
  records the falsifying example, makes no `src/` edit and does not weaken the model; the foreman
  brings it to Geoffrey (Constraints). (ruled 2026-11-09, foreman as gate judge)
- **Q7 — How high must the fsync and direct routes stay?** — **A:** (A), a floor the plan states,
  justified against the re-measured base, and never zero (R3). Requiring the base's count itself would
  push the build to tune the draw. (ruled 2026-11-09, foreman as gate judge)

Ruled at the Q6 stop, before the plan gate. The planner stopped uncommitted when the P2 dry run's seed 5 reached
a base-engine defect (`research/03-engine-defect.md`); the foreman reproduced it on `980f998`.

- **Q8 — How does the cycle proceed after the Q6 stop?** — **A:** (A), a separate fix cycle first. A `fix` cycle
  files and fixes the rename-over-tombstoned-child defect and lands on `main`. This cycle is parked unchanged and
  then resumes on the new `main`: rebase the prototype, refill PLAN § 6, re-run P2 and P3, commit the plan. This
  cycle's Non-goals and Constraints stand (`src/` byte-identical to the base it builds on). (Geoffrey, 2026-11-09,
  in the foreman's session)
- **Q-a — Which draw grades R1, R3, R4 and R6?** — **A:** (A), the standalone fixed CI draw
  (`pytest tests/engine/test_model.py` with `CI=true`), as PLAN deviation row 3 states. (Geoffrey, 2026-11-09,
  approved for the plan gate)
- **Q-b — May the composites branch on the oracle to create their file?** — **A:** (A), yes, as PLAN deviation
  row 2 states; either branch runs under parity. (Geoffrey, 2026-11-09, approved for the plan gate)
- **Q-c — The floors.** — **A:** (A), scheduler 50 polled lagging, eviction 50 lagging, fsync 50, direct 17, one
  lagging ancestor commit, and R2's per-seed minimum of 1, as PLAN deviation row 4 states. (Geoffrey, 2026-11-09,
  approved for the plan gate)
- **Q-d — The model's added run time.** — **A:** (A), accepted: about 6 s standalone, and AGENTS.md's suite time
  goes from ~30 s to ~40 s. (Geoffrey, 2026-11-09, approved for the plan gate)
- **Q-e — A second composite for M7.** — **A:** (A), yes, `move_out_rmdir_unlink`, so R6's M7 dies on the CI draw
  and on random seeds, as PLAN deviation row 6 states. (Geoffrey, 2026-11-09, approved for the plan gate)

## Related materials

- Seed: [`issues/model-commit-route-reach.md`](../../issues/model-commit-route-reach.md)
- Origin cycle: [`spec/deferred-rename-source-orphan/`](../deferred-rename-source-orphan/): GOAL R7, R8,
  Q7; PLAN § 2.7 (the rules, and the draw amendment), § 2.8 (the fix-removal mutants); REVIEW F4
  (`:161`) and review cycle 2's recount (`:307`); `verify/replay_gate.py` (`MUTANTS`, `:597`).
- Shape precedents: [`spec/entry-identity-token/GOAL.md`](../entry-identity-token/GOAL.md),
  [`spec/truncate-recording-after-ancestor-rename/GOAL.md`](../truncate-recording-after-ancestor-rename/GOAL.md).
- Related seeds: `issues/ci-integration-lane.md`, `issues/freethreaded-concurrency-stress.md`.
- Contract text: `AGENTS.md` § Invariants § 5 and § 7; the high-blast-radius row for the engine test
  modules (`:238`); § Conventions; § Verification.
- Sources, anchors confirmed at `980f998`:
  - `tests/engine/test_model.py`: `ADVANCE` 39; `OverlayMachine` 56, its docstring 57-82; `__init__` 83
    (the commit-policy spec 91, the unstarted scheduler 94); `write_and_fsync` 207;
    `rewrite_rename_commit` 228; `rename_reuse_commit` 251; `rename_scan_unlink_commit` 275;
    `commit_entry` 296; `poll_once` 301; `poll_next_due` 307; `scan_writeback` 318; `commit_scanned`
    324; `evict` 331; `drain_by_polls` 340; the invariant 355; `teardown` 396; settings 414-416.
  - `tests/engine/test_replay.py`: `test_eviction_commit_before_queued_rename` 301,
    `test_scheduler_poll_settles_data_before_rename` 317 (276 and 292 when F4 was written).
  - `src/sosfs/engine/overlay.py`: `fsync` 868, `commit_row` 939, `_commit_hop` 966, `commit_upstream`
    1013, `evict_copy` 1338.
  - `src/sosfs/engine/writeback.py`: `deadline` 70, `due` 85, `poll` 90.
  - `src/sosfs/engine/evict.py`: `_drop_dirty` 136, its commit 151.
