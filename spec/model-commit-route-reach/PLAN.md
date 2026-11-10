# PLAN — The model commits through a scheduler poll and through eviction while a rename is queued

> **Status:** Draft, **stopped uncommitted** at the P2 dry run: the new reach found a base-engine defect
> ([`research/03-engine-defect.md`](research/03-engine-defect.md); GOAL Q6). · **Last updated:** 2026-11-09
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

`OverlayMachine` gains **two composite rules** and one helper; no base rule, strategy, setting, invariant or
teardown check changes, two strategies are added, and `src/` is untouched.

- `write_rename_commit_through(path, new, data, lift, route)` commits a file (creating it first when absent),
  rewrites it through an open handle, renames the file or (`lift`) its parent directory, and then commits the
  rewrite to the source through the route `route` draws while that rename is queued: a scheduler poll, `fsync`
  on the handle, or the model's `evict` rule at the file's new path. This is the cycle's reach (R1–R3).
- `move_out_rmdir_unlink(path, new)` commits a file in a directory, moves it out, removes the emptied directory
  and unlinks the file, so the file's removal is queued behind the directory's. It exists for R6: the base's
  model failed under M7 only by the luck of its CI draw (0 of 8 random seeds), and the redraw lost it (§ 4 item 3).

Reach is counted by a **census**, `verify/route_census.py`, a pytest plugin that wraps
`OverlayEngine._commit_hop` from outside the test module and attributes each commit to the source to a route by
its call stack. On the base's standalone CI draw it counts scheduler 2, eviction 0, fsync 0, direct 34; on the
prototype's, scheduler 106, eviction 162, fsync 141 and direct 38, and on every one of seeds 1–20 at least one
scheduler and one eviction commit (§ 6). R4's scheduler-only mutant turns the new model red while the base's model
stays green under it; M1, M2, M3 and M7 fail the new model on its CI draw and on each of four random seeds.

Four phases: the model and its records (P1), reach on twenty seeds (P2), the suite and twenty seeds (P3), CI and
the ledger (P4). Research trigger 2 fired (`test_model.py` is high-blast-radius): two briefs, digested in
[`research/00-digest.md`](research/00-digest.md). The adversarial review is in § 4.1.

## 2. Design

### 2.1 The model (`tests/engine/test_model.py`)

Module level, between `snapshot` and the class (the only module-level change; placed there so that no line a
seed cites moves: `issues/rename-to-terrace.md` cites `test_model.py:43`, which `records` checks):

```python
LIFT = st.booleans()
ROUTE = st.sampled_from(['poll', 'fsync', 'evict'])
```

In the class, between `rename_scan_unlink_commit` and `commit_entry`, exactly these three methods. The
prototype's text is the reference; the build may reword a docstring, but every word of the class redraws the
sample, so the gate is run on the build's final text and never used to choose it (§ 4 item 6).

- `poll_data_due(self, path, to_source)`: one `self.scheduler.poll` at the earliest deadline among `path`'s queued
  data rows bound for the source (`to_source`) or bound for a stage; nothing when there is none.
- `@rule(path=FILE_PATHS, new=PATHS, data=DATA, lift=LIFT, route=ROUTE) write_rename_commit_through`:
  - `moved` is `path`'s parent when `lift` and the parent is not the root, else `path`; `landed` is the file's
    path after the rename (`new + path[len(moved):]`); `fresh` is whether the oracle lacks `path`.
  - engine side: `create(path)` when `fresh`; `commit_upstream(path)`, so the source holds the file and the later
    commit lags; `open(path, O_RDWR | O_TRUNC)`, `write(data)`; for `poll`, `poll_data_due(path, to_source=False)`
    (the tmp → scratch hop, so the scratch → project row is born before the rename); `rename(moved, new)`; for
    `fsync`, `fsync(handle)` under the commit policy; `release`; then for `poll`, `poll_data_due(landed,
    to_source=True)` (that row falls due 60 ticks after the hop, the rename row 60 after the rename, so the data
    commits while the rename is queued; research 02 § 5); for `evict`, `self.evict(landed)`, the base's rule.
  - oracle side: `open(path, O_CREAT | O_RDWR | O_TRUNC)`, `pwrite`, `rename(moved, new)`; under `self.parity`.
- `@rule(path=FILE_PATHS, new=FILE_PATHS) move_out_rmdir_unlink`: `fresh` as above; engine side `create` when
  fresh, `commit_upstream(path)` (which places the directory on the source too), `rename(path, new)`,
  `rmdir(parent)`, `unlink(new)`; oracle side the same four steps (`O_CREAT | O_EXCL` for the create); under
  `self.parity`. When it runs whole, the unlink cancels the queued move and queues the removal behind the
  `rmdir` row, whose directory object on the source still holds the file: the state M7 breaks.

The class docstring's "three composites" becomes "five composites", naming both new ones (R7). `model` checks the
count in words and these five phrases verbatim, so a reword keeps them: "scheduler poll", "eviction", "`fsync`",
"its directory" and "moved out of a directory".

**Why one route rule, not three.** Rule choice is swarm-uniform (research 01 § 2), so each added rule dilutes the
others, and rules that need an existing file thin the file population for the rest. Measured (§ 4 item 2): three
rules, one per route, cut the direct route's seed mean from 39 to 23 (16 on their CI draw); one rule that creates
its file when absent raised it to 56.

### 2.2 The census (`spec/model-commit-route-reach/verify/route_census.py`)

Loaded as `-p route_census` with its directory on `PYTHONPATH` and `ROUTE_CENSUS_OUT` naming its JSON. It wraps
`OverlayEngine._commit_hop` and `OverlayEngine.start` on the class. It never reads, imports or patches the test
module, so the `OverlayMachine` source, and the draw, are the committed ones.

- **Queued-rename commit** (the GOAL's definition): a hop whose `upstream` is the source tier, made while
  `journal.queued()` holds a `rename` row for the entry at `path` (*own*) or, failing that, for a directory whose
  current catalog path is a proper ancestor of the entry's (*ancestor*), read when the hop begins. Sound for the
  single-threaded model (research 02 § 3): rename rows are keyed to the moved entry, tombstones carry no rows, a
  purge cascades its rows away, and the census matches on `row.entry`, never on a reused row id.
- **Route**, the innermost of these frames walking out from the hop: `OverlayEngine.commit_row` → *scheduler*
  (it reaches a hop only for data rows; research 02 § 2); `OverlayEngine.fsync` → *fsync*; a frame in
  `sosfs/engine/evict.py`, or the model's `evict` rule → *eviction*; the model's `rewrite_rename_commit` →
  *direct*; anything else → *other*, keyed by the outermost model rule. The route rule's evictions are the
  eviction route because they run through the `evict` rule, as the GOAL defines it.
- **Reported per route:** own, ancestor, *lagging* (the commit lands off the logical path:
  `(source_path or _target(path)) != path`), *polled* (a `WritebackScheduler.poll` is on the stack), the
  intersections *polled_lagging* and *ancestor_lagging*, and a count per outermost model rule; also every hop to the
  source per route, the hop total, and the engines started. R1's floors read the commits that exercise the lag:
  scheduler on *polled_lagging*, eviction on *lagging* (§ 4.1, finding 3).
- **Inert.** Its reads are SELECTs and `_target` reads only the catalog; it asserts on every call that the model's
  clock did not move. It imports only `sosfs.engine.overlay`, which the model loads anyway, and Hypothesis's own
  extractor (`constants_from_module`) pools no int, float or bytes constant from it, so the draw it measures is the
  plain run's (research 01 § 1). The gate checks both.

### 2.3 The gate (`spec/model-commit-route-reach/verify/route_gate.py`)

Its modes and what each grades are in its docstring. Every pytest run reads committed state: a `git archive` tree
with its own environment, or HEAD's `src/` with a mutant planted and checked to be the sosfs imported. Each run has
its own `HYPOTHESIS_STORAGE_DIRECTORY`. Footprint: one subprocess at a time, but the seed modes run two, each with
its own tree, process group and argv token, killed as a group and checked gone by token. Every group the gate
starts is tracked: a SIGTERM, SIGHUP or SIGINT to the gate kills them all, and a 570 s deadline per invocation
ends it with exit 3 before the 10-minute command cap can orphan them. R5 is graded by allowlist: node kinds, calls
(what the base's rules call, plus a named list) and attribute stores (`self.clock.now`, forward only).

- **R4's mutant**, scheduler-only by construction: `_commit_hop` gains `logical: bool = False` and writes at
  `path` when it is set, and `commit_row` alone passes `logical=True`. The gate checks that only `_commit_hop` and
  `commit_row` change and that one call site opts in. Under it, `test_scheduler_poll_settles_data_before_rename`
  fails, while `test_eviction_commit_before_queued_rename` and `test_fsync_commit_policy_before_queued_rename`
  pass. The new model fails under `CI=true` with a falsifying example (shrinking off), after the unmutated model
  passes the same no-shrink run (`control`), and the base's model passes on its own CI draw. That last check is
  deterministic, since the base's model and draw are fixed and `src/` is the base's. It shows the base's draw misses
  the mutant; it does not by itself prove which rule catches it in the new model (§ 4.1, finding 6). The planning
  evidence does: the base's model also passes under the mutant on each of seeds 1–4, and the new model fails under
  it on each of seeds 1–4 (§ 4 item 3).
- **R6's mutants** are `replay_gate.py`'s M1, M2, M3 and M7, verbatim. Each anchor occurs once at `980f998`, so
  none is re-anchored. A kill is a `failed` test_model with a falsifying example whose last raised error (pytest's
  `E   XxxError` line) is not an API drift, after `control` has shown the unmutated model passes the same run.

### 2.4 The records (R7), in P1's commit

- `test_model.py`'s class docstring (§ 2.1).
- `AGENTS.md`: the serial suite time at `:70` and `:734` goes from `~30 s` to the built tree's measured figure,
  rounded to ten seconds (`~40 s` on the prototype; § 6). The counts at `:70`, `:111`, `:136`, `:693` and `:734`
  do not change, because no test function is added: 548 unit, 558 total, 210 engine tests and 10 deselected
  stand. `:71`'s "test_model dominates" stays true.
- `issues/model-source-served-reach.md`, `status: unshaped`, `kind: test`, records that the model never serves a
  file from the source. It carries the 2026-11-06 history (the truncate cycle's GOAL Q2) and this cycle's Q1
  ruling, and names this cycle as `spec/model-commit-route-reach/`, never by its seed path. Its `ROADMAP.md` entry
  goes right after this cycle's, with a `**Seed:**` line. The prototype carries the text.
- Left for `code terrace roadmap`: `issues/model-commit-route-reach.md` (`adopted:`) and its entry. Nothing else
  outside `spec/` names that seed, so retiring it leaves no dangling name; the gate checks this.

### Requirement → design map

| R-ID | Design element(s) that satisfy it | Graded by |
|------|-----------------------------------|-----------|
| R1 | § 2.1's route rule; § 2.2's census; floors 50 polled lagging (scheduler) and 50 lagging (eviction), and one lagging commit under a renamed directory; the base's 2 / 0 recorded and re-measured exactly | `reach` (P1) |
| R2 | the route rule reaches its drawn route whenever it runs whole (§ 4 item 2); per seed, one polled lagging scheduler commit and one lagging eviction commit | `reach_seeds` (P2) |
| R3 | floors fsync 50 and direct 17 (deviation row 4), against the base's 0 and 34 | `reach` (P1) |
| R4 | § 2.3's scheduler-only mutant | `detect` (P1) |
| R5 | additions only (§ 2.1): every base method and module statement unchanged; no except, assume, precondition, filter, skip, bundle or settings change | `model` (P1) |
| R6 | M1, M2, M3, M7 planted from `replay_gate.py`; `move_out_rmdir_unlink` makes M7's state structural | `mutants` (P1) |
| R7 | § 2.4 | `records` (P1, P4), `timing` (P1); the docstring in `model` |
| R8 | the profile's `verify` under `CI=true`; seeds 1–20 without the census; the CI run record | `suite` (P1, P3), `seeds` (P3), `ci` (P4) |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research, against the GOAL's intent, and again against this design.

- **§ 5, the committed source path.** Not changed, but exercised. The route rule's commits land at the
  committed source path while a rename is queued (every scheduler and eviction commit on the prototype's CI draw
  lags), and `move_out_rmdir_unlink` queues a child's removal behind its old directory's `rmdir`, the ordering
  § 5's `_replay_through` bullet guards. `drain_by_polls`, the invariant and the teardown remount check the result.
- **§ 6, write-back policy.** Not changed. The route rule moves the model's clock forward to a row's deadline, as
  `poll_next_due` does; it never moves the clock back.
- **§ 7, eviction.** Not changed. The route rule runs the base's `evict` rule, which commits a dirty copy to the
  source before dropping it.
- **§ 10, concurrency.** The model is single-threaded; nothing changes.
- **High-blast-radius paths.** `tests/engine/test_model.py`: the op mix only widens (two rules added, none
  changed), and no skip guard, `assume`, precondition or filter is added (`model`). A CONFIRMED finding there takes
  Geoffrey's sign-off (GOAL Constraints).
- **Conventions.** No spec ids or seed paths in `tests/` (`records`); same-commit records (§ 2.4); `pytestmark`
  unchanged; 120-character lines.

### Deviation justifications

Each row was read against the wording of every R-ID it touches. Every figure is rederived from the rule the row
states.

| # | Deviation | Why needed | Simpler alternative rejected because |
|---|-----------|-----------|--------------------------------------|
| 1 | Two new module-level strategies, `LIFT` and `ROUTE` | One rule chooses its route, and whether a directory is renamed | **Three rules, one per route:** they dilute the rest (row 4's figures). **Inline strategies:** the same draw, but the gate could not pin them by name. Read against R5: the base's eight strategies are byte-identical, so nothing is narrowed |
| 2 | Both composites branch on the oracle (`fresh`): they create the file first when the oracle lacks it | A composite needs a file, and the population is thin: the three-rule design, which used only existing files, reached the eviction route 15 times on its CI draw against the one rule's 162 | **Rewrite only existing files:** the reach thins to that. **`try: create … except FileExistsError`:** an exception handler, which R5 forbids. Read against R5: the branch skips nothing. Both sides run under `parity` on either branch, and a divergence (the oracle has the file, the engine doesn't) fails `parity` as any rule does |
| 3 | "The fixed CI draw" for R1, R3, R4 and R6 is the **standalone** draw: `pytest tests/engine/test_model.py` under `CI=true` on macOS | Hypothesis feeds the literals of loaded local modules into its draws, so the full suite (R8) and Linux CI each draw differently from a standalone run (research 01 § 1) | **Grade on the suite's draw:** the census and mutant runs would have to run the whole suite, and the draw would move with every test module's imports. Read against R1: the GOAL measures "the fixed CI draw" with "an instrumented run of `tests/engine/test_model.py`", which is the standalone run, and base and final are measured alike. R8 grades its own draws on pass and counts only. To the sign-off as Q-a |
| 4 | Floors on the standalone CI draw: scheduler 50 and eviction 50 (R1), fsync 50 and direct 17 (R3) | R1 needs N ≥ 1, stated and justified; R3 needs a floor justified against the base, never zero | **N = 1:** a design that loses its structure (one branch broken) still passes on a stray hit, and the base itself reaches the scheduler twice. **The base's count for direct (34):** Q7 rules it out as tuning. The rule: each composite route's floor is under half its count on the prototype's CI draw (106, 162, 141), and above anything the base reaches on 21 measured draws (0 to 7). Direct's 17 is half the base's 34; the three-rule design (16 on its draw) fails it, and the prototype (38) clears it. The fsync base is 0, so any floor is a gain; it takes the composite routes' 50 |
| 5 | `AGENTS.md`'s serial suite time moves from `~30 s` to `~40 s`, and the standalone model from 27 s to about 33 s | The composites make five to eight engine calls per step | **Fewer examples or steps:** R5 forbids lowering them. Read against R7: the GOAL asks for the timing if it moves |
| 6 | A second composite, `move_out_rmdir_unlink`, which no R-ID names | R6: M7 survived the redraw, and the base caught it on 1 of 9 draws measured (its CI draw, and none of seeds 1–8) | **Leave M7 to the draw:** the final draw misses it, and Constraints forbid steering the text toward one that doesn't. **Hand R6 back:** the design can carry it, as the origin cycle's three composites did for M1, M2 and M3 (its PLAN § 2.7). To the sign-off as Q-e |
| 7 | The gate grades more than the GOAL asks: R1's floors on commits that lag (and, for the scheduler, are made inside a poll), one lagging ancestor commit, the base's model green under R4's mutant, a control run before each mutant sweep, the census's constant pool, and an allowlist for R5 | Each closes a way the floors or the guards could be met hollowly (§ 4.1) | None: each is stricter than the R-ID it serves, and each holds on the prototype. Read against R1: the floor is still the GOAL's count (own and ancestor together), now also met by the lagging subset |

Arithmetic beside row 2: each `poll` branch jumps the clock once, to a deadline about 60 ticks out, where `ADVANCE`
already jumps up to 120. No bound is shrunk.

## 4. Rabbit holes (resolved)

1. **Which draw** (row 3; research 01 § 1): the standalone one, for the base and the final tree alike.
2. **Mechanism.** Measured in planning on scratch trees with the census, under `CI=true` and on seeds 1–20
   (2026-11-09). Each cell is a count, or min / mean / max over the 20 seeds:

   | Design | Draw | scheduler | eviction | fsync | direct |
   |---|---|---|---|---|---|
   | base (`980f998`) | CI | 2 | 0 | 0 | 34 |
   | base | seeds 1–20 | 0 / 1.5 / 7 | 0 / 0 / 0 | 0 / 1.4 / 7 | 12 / 38.8 / 76 |
   | three rules, one per route, existing files only | CI | 20 | 15 | 12 | 16 |
   | three rules | seeds 1–20 | 5 / 27.7 / 52 | 2 / 22.6 / 43 | 1 / 25.6 / 65 | 6 / 22.9 / 42 |
   | the route rule alone, creating when absent (pilot text) | seeds 1–20 | 82 / 178 / 249 | 97 / 154 / 225 | 82 / 165 / 296 | 17 / 56 / 114 |
   | the route rule alone (prototype text) | CI | 226 | 300 | 220 | 31 |
   | **both composites (the prototype, § 2.1)** | CI | 106 (3 ancestor) | 162 (0) | 141 (11) | 38 |

   Ancestor renames reached 0 on every base draw but seed 7's. The final prototype's seeds are in § 6. Every run
   above passed.
3. **M7 was luck at the base.** The first dry run of `mutants` on the route-rule prototype went red: M7 passed the
   model on its CI draw. Without CI and with shrinking off, M7 passed the base's model on each of seeds 1–8, and
   the route-rule prototype's too, while the base's CI draw catches it. `move_out_rmdir_unlink` makes the state
   structural: with it, M7 fails the model on its CI draw and on each of seeds 1–8, in under 11 s each. M1, M2, M3
   and R4's mutant fail it on each of seeds 1–4 as well. R4's mutant passes the base's model on its CI draw and on
   each of seeds 1–4: the base never catches it, the prototype always does.
4. **The scheduler route needs two polls** (research 02 § 5): write → poll (tmp → scratch) → rename → poll at the
   scratch → project row's deadline.
5. **Attribution** (research 02 §§ 2–3) is sound, refined with *polled*, *lagging* and the per-rule counts.
6. **Rule order.** The rule at sort index 0 gets extra simple examples (research 01 § 2); that is still
   `commit_scanned`. **The redraw is re-graded, never tuned.** Every figure names its draw, the build re-grades on
   its own final text, and the floors leave room for a redraw that keeps the structure (row 4).

### 4.1 Adversarial review

One reviewer pass (trigger 3), a fresh child given the GOAL, this PLAN, TECH, both gate scripts and the prototype,
tasked with gates that pass a broken build or fail a good one. It found two HIGH or near-HIGH problems, both already
caught by the planner's own dry run of P1 and fixed before its report landed, and fifteen more. It confirmed that the
route rule reaches each route for both `lift` values with every commit lagging, that `move_out_rmdir_unlink` builds
M7's state, that the `fresh` branch is not a filter, that the census attributes soundly, and that R4's mutant is
confined. What each finding changed:

| # | Sev. | Finding | Fold |
|---|---|---|---|
| 1 | HIGH | `records` required the own seed to name itself, so it was red on every build | Already fixed: only `ROADMAP.md`'s Seed line may name it |
| 2 | MED-HIGH | Strategies after `ADVANCE` move `test_model.py:43`, which `issues/rename-to-terrace.md` cites | Already fixed: strategies just above the class; `model` pins the place |
| 3 | MED | Floors met by commits that never lag | R1's floors on *polled_lagging* (scheduler) and *lagging* (eviction), on the CI draw and per seed |
| 4 | MED | R5 as a denylist passed a `with` that swallows assertions and a rebinding of `self.oracle` | Allowlist: node kinds, calls, attribute stores (`self.clock.now`, forward only); bases and keywords compared |
| 5 | MED | Children orphaned if the 10-minute cap kills the gate | Tracked groups killed on SIGTERM, SIGHUP or SIGINT; a 570 s deadline that exits 3 |
| 6 | MED-LOW | "Base passes under R4's mutant" does not show the new rule is what catches it | Wording narrowed (§ 2.3); seeds 1–4 measured both ways (§ 4 item 3) |
| 7 | LOW-MED | Mutant kills without a control; API-drift words matched anywhere in the text | `control` run first; the last `E   XxxError` line decides |
| 8 | LOW-MED | The suite time in `AGENTS.md` was never measured | `timing` mode in P1, on pytest's own duration; exact on the side load cannot move (below the figure), a warning above it |
| 9 | LOW-MED | `records` read uncommitted records | Requires them committed |
| 10 | LOW | R1–R6 graded on a draw no CI job runs | Kept: deviation row 3, Q-a |
| 11 | LOW | Ancestor reach unguarded | One lagging ancestor commit on the CI draw (3 + 0 + 11 on the prototype's) |
| 12 | LOW | Docstring phrase check vs "may reword" | The five phrases are listed (§ 2.1) |
| 13 | LOW | Census inertness approximated Hypothesis's extractor | Hypothesis's own `constants_from_module` |
| 14 | LOW | No fence from the base to the plan commit | `records` fences the whole branch too |
| 15 | LOW | Counts from JUnit, not pytest's summary line | `suite` checks the summary line agrees |
| 16 | LOW | A denylist of names could fail a good build | Gone with the allowlist |
| 17 | LOW | Scratch environments pile up | Stated in the gate's footprint (about 100 MB each); nothing is deleted |

**Second pass, on the folds.** The folds changed the gate's grading of R5, R6 and its process handling, so a second
fresh child attacked only the folded code. It found one HIGH, two MEDIUM and three LOW problems; the rest held
(the lagging floors and their JSON ints, `control`, the deadline, the summary-line regex, the record fences).

| # | Sev. | Finding | Fold |
|---|---|---|---|
| 1 | HIGH | The name-only allowlist still passed a comprehension that rebinds `self.oracle`, an alias of a builtin to `self.__init__`, a decorator on a nested def, an oracle side that copies the engine's bytes, and `self.engine.flush()` | Calls allowed per scope (the body, `engine_side`, `oracle_side`), by an exact set per method; the oracle side touches nothing of the engine; stores in any context only to locals that shadow no builtin and to `self.clock.now`; nested defs undecorated; `self.parity(engine_side, oracle_side)` exactly |
| 2 | MED | On macOS `killpg` of an exited, unreaped leader raises `PermissionError`, masking the real error in a `finally` | Caught; the seed cleanup kills only live runs and reaps each with a bounded wait |
| 3 | MED | The `E   XxxError` regex missed qualified and non-`Error` exceptions, and judged only the last line, after teardown | The final type from JUnit's reprcrash; any API-drift type anywhere in the chain rejects the kill |
| 4 | LOW | Order checks read `ast.walk`'s breadth-first order | Calls sorted by source position |
| 5 | LOW | `self.real(path).exists` and a tuple unpacking were red on a reword | Calls normalized (arguments dropped, local receivers as `_`); `For` allowed |
| 6 | LOW | `records`' greps read the working tree; the timing upper bound is a warning; a cite at line 56 or later could never pass | Greps read `HEAD`; the timing trade-off is deliberate (load only slows a run); the one cite today is line 43, above every insertion |

Then the planner attacked `model` with eleven edits from both passes (the eight above, a base rule emptied, the
settings lowered, a subscript store) plus the prototype as control: each edit went red for its own cause and the
control green. That attack is committed as `verify/model_attacks.py`, `model`'s self-test, and P1's verify runs it
first.

## 5. Risks & open questions

- **The census is the evidence.** A census that miscounts would pass a hollow model. Mitigations: it reproduces
  the base's figures exactly (`reach`); R4's mutant shows the reached route has teeth; and the lagging and polled
  counts show the commits are the kind a defect would break.
- **Test time.** The standalone model takes about 33 s and the serial suite about 42 s. The seed modes take about
  6 min each on two workers, under the 10-minute cap, which is why they are separate phases.
- **A defect the new reach finds** (GOAL Q6): none in planning. If the build's draw finds one, the build stops and
  hands back the falsifying example.
- **A static allowlist is not a proof.** `model` grades the added methods' shape; a weakening it cannot see (an
  allowed call used to wrong effect) is the blind review's to find. The reach, control and mutant runs are the
  behavioural check: a model that stopped asserting would pass every mutant, and `mutants` would go red.
- **Open for the gate** (each needs a ruling):
  - **Q-a** (row 3): the standalone draw is "the fixed CI draw" for R1, R3, R4 and R6. **Recommend accept.**
  - **Q-b** (row 2): the composites branch on the oracle to create their file. **Recommend accept**: it retires
    nothing, and it is what keeps the direct route's reach.
  - **Q-c** (row 4): floors 50 / 50 / 50 / 17. **Recommend accept.**
  - **Q-d** (row 5): the model's time grows by about 6 s standalone. **Recommend accept.**
  - **Q-e** (row 6): a second composite for M7, which R6 needs on the new draw. **Recommend accept**: it is the
    origin cycle's remedy for the same failure, and the alternative is a draw tuned to catch M7.

## 6. Verification strategy

The engine is mount-free and the deliverable is test reach, so the evidence is pytest runs graded by
`verify/route_gate.py`; no CLI drive applies.

- **P1** `model reach detect mutants records suite`.
- **P2** `reach_seeds`: the census on seeds 1–20, two workers.
- **P3** `suite seeds`: the profile's `verify`, then plain seeds 1–20.
- **P4** Run A `ledger prepush`, after which the foreman pushes; Run B `ledger records ci` on the saved run record.

*(The dry-run figures are filled in below once measured.)*
