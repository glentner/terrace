# GOAL — Removing nested directories after moving an entry out drains the queue instead of recursing

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** nested-rmdir-replay-recursion
- **kind:** fix
- **appetite:** big (raised from small at the plan stop, Q6)

## Problem

**A user can leave the write-back queue in a state no replay route can drain.** The sequence is ordinary:
move an entry out of a directory nested inside another, remove both directories, then remove the moved
entry. Every route that replays the queue then raises `RecursionError`, and the queue stays as it was.
The cycle 8 planner found it on 2026-11-09, and the foreman reproduced it at `980f998`:

```python
seed(root / 'project', 'd2/s/a.dat', b'payload')       # adopted from the source
e = OverlayEngine(build_spec(root, tiers=1), clock=Clock()); e.start()
e.readdir('/'); e.readdir('/d2'); e.readdir('/d2/s')
e.rename('/d2/s/a.dat', '/a.dat'); e.rmdir('/d2/s'); e.rmdir('/d2'); e.unlink('/a.dat')
e.flush()                                               # RecursionError; queue: rmdir, rmdir, unlink
```

What the user sees:

- `flush`, `drain` and the scheduler's `commit_row` raise `RecursionError`, which is not an `OSError`
  (`AGENTS.md` § 11). The queue is left whole, with no unresolved intent.
- The write-back pump's thread dies at its first scan. Every later op stays queued, and nothing reports
  the dead pump (`AGENTS.md` § 10). Measured on 3 tiers: the thread was dead within 2 s, and a later `mkdir`
  stayed queued.
- A SIGINT drain raises out of `Mount.shutdown` before `engine.stop`, so `shutdown_state` is never
  settled. The next mount finds `mounted` and stops (`AGENTS.md` § 8). This is argued from
  `src/sosfs/fuse/mount.py:238-262`, not run.
- A user `mkdir`, `create` or `rename` onto the outer directory's name raises `RecursionError` itself.
  So does a `create` at the moved entry's old name.
- The queue is in the state database. Stopping and starting the engine keeps every row, and the next
  replay raises again. A state directory written today may already hold this shape.

**Shape map**, run against `980f998` (shaper, 2026-11-09). The scratch script
`c9shape-map.py`, which is not committed, ran 7,040 cases: the cross-product of these axes:

- depth: 1 to 4 removed directories;
- the entries moved out: a file, an empty directory, or a directory with a file. One or several of
  them, at the deepest level, at the top level, or at every level. Controls: an entry kept after it
  moves out, and an entry removed in place;
- origin: adopted; created and flushed; created and never flushed; the directories adopted with the
  entries created, flushed or not;
- the order of the removals: the moved entries last (A), first (B), or between the deepest `rmdir` and
  the rest (C);
- tiers: 1 and 3;
- consumer: `flush`, `drain`, `commit_row`, a scheduler `poll`, and a later `mkdir`, `rename` or
  `create` onto the outer directory's name or the moved entry's name.

The result:

| Axis | Raises `RecursionError` | Passes |
|---|---|---|
| depth | 2, 3, 4, all alike | 1 |
| the entries moved out | a removed file, directory or directory with contents, below an inner removed directory, alone or with others at other levels | only at the top level, kept, or removed in place |
| order | A (the moved entry is removed after both `rmdir`s) | B, C |
| origin | the moved entry and the inner directory are both held on the source: adopted, or flushed; never-flushed files on 1 tier too, because a `create` lands on the source | either is still unplaced on the source: never-flushed directories; never-flushed files on 3 tiers |
| tiers | 1 and 3, alike | — |
| consumer | every one, including the user's `mkdir`, `create` and `rename` | — |
| stop, then start | the queue persists, and the next `flush` raises again (843 of 843) | — |

843 cases raise `RecursionError`, and 5,658 complete. Every case that completes leaves the source equal
to a plain-`os` oracle tree and passes `assert_converged`. The other 539 cases:

- 110 are the script's own `create` over a live entry it kept, which correctly raises `EEXIST`;
- 429 are a scheduler `poll` on a source-only stack, which raises `KeyError: 'project'` in every case that has a
  namespace row queued, the shape or not. That is a separate defect (Q1).

**Mechanism.** The planner proposed this, and the shaper confirmed it with a spy on `_replay_through`.
It is given for orientation only, and no criterion depends on it. The row `rmdir s` replays the
moved-out entry's removal by running the queue prefix in order (`overlay.py:1114-1116`, `:1302-1315`).
The prefix holds `rmdir d2`, whose own pre-replay includes `rmdir s`. Each call skips only its own row,
so the calls alternate: (`{unlink}`, skip `{rmdir s}`), then (`{rmdir s, unlink}`, skip `{rmdir d2}`),
and so on until the stack is exhausted.

**History.** At `141aa86`, the parent of `fe26962`, the same shapes at depth 1, 2 and 3 failed with
`OSError` `ENOTEMPTY` from `flush`, `drain` and `commit_row`. A `mkdir` onto the outer directory's name
raised `ENOTEMPTY` too, so the queue was already stuck, at depth 1 as well. A `create` at the moved
entry's old name succeeded there. `fe26962` (`deferred-rename-source-orphan`) fixed depth 1, with
`test_flush_removes_a_moved_out_child_before_its_old_parent`. It also turned depth 2 and deeper from
`ENOTEMPTY` into `RecursionError`, and made the `create` at the moved entry's old name fail.

## Outcome / vision

An engine that holds this shape drains it the way it drains any other queue: one `flush` or one
`drain`, a scheduler pass, or the replay a new name triggers. The source ends exactly as the same
operations would leave a plain directory. A queue already stuck in a state database drains the same way
once the fixed engine starts on it. Nothing that converges today changes behaviour. The profile's
`verify` stays green, and CI's four jobs conclude `success`.

## Acceptance criteria (the contract)

These terms hold throughout:

- *The shape* is two or more nested directories, each held on the source, and one or more entries that
  the source holds below an inner one of them. Each such entry is renamed out of the outermost
  directory. The directories are then removed, innermost first, and after that the moved entries are
  removed (order A above). A moved entry is a file, an empty directory, or a directory with contents
  (removed contents first). The directories and entries are held on the source either because they were
  adopted or because they were created and flushed.
- *The oracle* is the same user operations applied with plain `os` calls to a copy of the source's
  starting tree.
- *The queue drains* means `journal.queued() == []` and `journal.pending() == []`.
- *Converges* means `tests/engine/test_replay.py::assert_converged` passes.
- Counts are the numbers pytest prints in its summary line, asserted as numbers (`AGENTS.md`
  § Verification).

The criteria:

- **R1 — `flush` and `drain`.** WHEN `flush()` or `drain()` runs **once** on an engine holding the shape,
  it SHALL return without raising. `drain` SHALL report `complete=True`. The queue SHALL drain in that
  one call, the source tree SHALL equal the oracle's, and the engine SHALL converge. Each of these SHALL
  satisfy it, on 1 tier and on 3:
  - depths 2 and 3;
  - a moved file, an empty directory, and a directory with contents;
  - two or more moved entries at different levels;
  - adopted and flushed origins.

  (Today both calls raise `RecursionError` and leave the queue whole. Q4 is why it says one call.)
- **R2 — the scheduler.** WHEN a scheduler `poll` runs, on a stack with a stage, at a time when every
  queued row is due, it SHALL raise nothing. After that one poll, no namespace row of the shape SHALL
  stay queued, and once the queue drains the source SHALL equal the oracle's and the engine SHALL
  converge. A running `WritebackScheduler` thread SHALL stay alive past the shape, and SHALL commit an op
  the user makes afterwards. (Today `commit_row` raises `RecursionError`, and the pump's thread dies on
  its first scan.)
- **R3 — a new name over the shape.** WHEN, on an engine holding the shape, a user `mkdir`, `create` or
  `rename` targets the outermost removed directory's name, or a moved entry's name after its removal,
  the call SHALL succeed. A following `flush` SHALL then drain the queue, the source SHALL equal the
  oracle's, and the engine SHALL converge. (Today the call itself raises `RecursionError`. At `141aa86`
  the `create` at the moved entry's name succeeded.)
- **R4 — a queue already stuck.** WHEN the engine starts on a state directory holding the shape's queued
  rows as the code at `980f998` writes them, the first `flush` or `drain` SHALL meet R1. That includes
  a directory on which a `980f998` replay has already raised. A test SHALL show this with a queue whose
  rows are those `980f998` queues for the shape. (In scope: Q2.)
- **R5 — nothing else changes.** Every neighbouring shape SHALL still complete, match the oracle and
  converge under each consumer of R1–R3:
  - a single removed directory;
  - a moved entry that stays live;
  - an entry removed in place;
  - the moved entries removed before the `rmdir`s, or between them (orders B and C);
  - an entry never placed on the source.

  An `OSError` that the base raises for a reason of its own, such as `EEXIST` or `ENOTEMPTY` for a
  live child, SHALL be raised unchanged. The existing tests in `tests/engine/test_replay.py` SHALL pass
  unchanged.
- **R6 — the tests.** Every sequence in R1–R4, R9 and R10, and R5's controls, SHALL be pinned by a
  deterministic test under `-m unit`. Each test SHALL end in `assert_converged` once the queue drains, and
  the R1–R4, R9 and R10 tests SHALL compare the source tree with the oracle's. Each new test SHALL have a fix-removal mutant that
  turns it red. A control the base already gets right SHALL have a mutant of the fix's own decisions
  instead (`spec/truncate-recording-after-ancestor-rename/` Q6). The R2 thread case MAY use the real
  `StateThread`, with a bounded wait.
- **R7 — the records**, in the same commit as the code they describe, saying exactly what the code
  delivers and no more. They SHALL cover every record the change makes untrue, including these:
  - `AGENTS.md` § 5's `_replay_through` bullet (`:447`), and § 10, if a lock's holders change;
  - the high-blast-radius tables, if the change adds a function to a guarded region;
  - the docstrings of `_replay_through`, `_execute_namespace`, `_execute_namespace_locked`, `_vacate`,
    `flush` and `drain`;
  - the module docstring of `tests/engine/test_replay.py`, whose class list (`:21-23`) names the depth-1
    case only;
  - the measured counts in § Commands (`:70`), § Repository map (`:111`), the engine-test count (`:136`),
    § Conventions (`:693`) and § Verification (`:734`);
  - a new `issues/` seed, with its `ROADMAP.md` entry, for the source-only scheduler defect (Q1), carrying
    its reproduction. It is a record, not a fix: the code that raises is unchanged by this cycle.
- **R8 — the gate.** All of these SHALL hold:
  - WHEN the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`) runs with
    `CI=true` on macOS, `tools/lint.sh` SHALL exit 0.
  - In that run, pytest SHALL report 0 failed and 0 errors. Its passed count SHALL exceed the count the
    same command reports at `980f998`.
  - WHEN `uv run pytest tests/engine/test_model.py` runs on macOS without `CI` set, from a tree with no
    `.hypothesis/` directory, once for each `--hypothesis-seed` from 1 to 20, every run SHALL pass.
  - WHEN the CI workflow runs on this cycle's final tree, each of `Lint & build`, `Unit (Linux, 3.15)`,
    `Unit (Linux, 3.15t)` and `Unit (macOS, engine-only)` SHALL conclude `success`, as read from the
    run record.
- **R9 — many sibling chains (added at the plan stop, Q5).** WHEN N sibling directories each have an
  entry moved out of them, then every directory is removed, then every moved entry is removed, in either
  order, a single `flush()` or `drain()` SHALL drain the queue, the source SHALL equal the oracle and the
  engine SHALL converge, for N up to at least 500, on 1 and 3 tiers. The same SHALL hold for the nested
  chains of R1 repeated side by side. No `RecursionError` SHALL escape at any N the tests reach.
- **R10 — renames crossing each other's subtrees (added at the plan stop, Q6).** WHEN queued directory
  renames move targets into or out of each other's subtrees, so that replaying one rename needs others
  to run first (for example, at `980f998`: directories `/c/a/a` and `/c/a/d/b`, then rename `/c/a` →
  `/c/d`, `/c/d/d/b` → `/c/d/a/c`, `/c/d/a` → `/a`, `/c/d` → `/a/c/c`), the first `flush()` or `drain()`
  SHALL drain the queue, the source SHALL equal the oracle, and the engine SHALL converge. No
  `EngineError` or other non-`OSError` SHALL escape that flush, drain, the scheduler, or a user `rename`
  or `mkdir` issued while such renames are queued. (At `980f998` the first flush raises `EngineError:
  Cannot move /c/a into its own subtree`; a second flush drains.)

## Non-goals (no-gos)

- **The rename-over-tombstoned-child defect.** It belongs to the parked `rename-over-tombstoned-child`
  cycle (`refs/park/rename-over-tombstoned-child-prototype`). That ref's recursion candidate (a set of
  in-flight rows, and a deferred `rmdir` row) and its probes are input to the plan step, not a decision.
  This cycle neither lands nor rejects the rename half.
- **The scheduler on a source-only stack.** On a source-only stack, `WritebackScheduler.deadline`
  raises `KeyError` on any queued namespace row. Home: a seed of its own, written in this cycle's
  records (R7, Q1). Until that is fixed, R2 is graded on stacks with a stage.
- **Pump liveness monitoring.** Nothing notices a dead pump. Home:
  `issues/pump-liveness-and-loss-windows.md`.
- **A drain that raises for any other reason** leaves `shutdown_state` at `mounted`, and recovering from
  that needs the audit. Home: `issues/audit-command.md`.
- **Other non-`OSError` escapes from the VFS API.** Home: `issues/engine-error-mapping.md`.
- **The replay under real concurrent threads**, and removal racing a replay. Home:
  `issues/freethreaded-concurrency-stress.md`.
- **A mount-level reproduction and the integration lane.** Home: `issues/ci-integration-lane.md`.

## Constraints

- **`AGENTS.md` § Invariants § 1, § 5 and § 10 hold as written.**
  - That includes settling by `Journal.settle_current`, never by id.
  - It includes the lock order and the one re-entrant source mutex: nothing holding it calls
    `locks.hold`, `_ensure`, `getattr` or `readdir`.
  - It includes `_replay_through` running a queue *prefix* in order, so a parent's `mkdir` or a child's
    move-out still runs before the row that needs it.
  - A design that bends any sentence of these sections goes in the plan's deviation table, before
    Geoffrey, and into the same diff's § Invariants text.
- **Earlier cycles' properties hold.** `deferred-rename-source-orphan`'s converged classes, including
  the depth-1 case, keep their tests green unchanged (R5).
- **No state-database schema change.** No `SCHEMA_VERSION` bump, and no edit to
  `src/sosfs/state/schema.sql` or `src/sosfs/state/db.py`. A bump makes every existing state database
  unopenable. If the plan finds it cannot avoid one, it stops and asks.
- **No edit to `tests/engine/test_model.py`.** Hypothesis's fixed CI draw is a digest of that machine
  class's source, so any edit redraws the CI sample.
- **No `src/` or `tests/` text cites an `issues/` seed or a spec ID** (Geoffrey's directive, 2026-11-09).
- **Same-commit records** (R7, `AGENTS.md` § Conventions).

## Clarifications

Resolved:

- **Q:** Kind, appetite and seed? — **A:** `fix`, appetite `small`, and an inline description with no
  `issues/` seed: this GOAL is the record. There are eight criteria, three of which are tests, records
  and the gate, so the size circuit breaker does not trip. (Themis's hand-off, recorded 2026-11-09,
  shaper)
- **Q:** Where did the defect come from (`escaped`)? — **A:** `deferred-rename-source-orphan`. Correct
  it at the gate if this reading is wrong. (recorded 2026-11-09, shaper)
  - At `980f998` the `rmdir` row's pre-replay (`overlay.py:1110-1118`) and `_replay_through`
    (`:1302-1315`) blame to `fe26962` (2026-11-06), which carries `spec/deferred-rename-source-orphan/`.
  - On its parent `141aa86` the same shapes raise `OSError` `ENOTEMPTY` instead, at depths 1 to 3. A
    `create` at the moved entry's name succeeds there.
  - So the queue was already stuck before the factory touched it. What `fe26962` shipped was the
    recursion, the non-`OSError` escape, the pump's death and the broken `create`. Read as "the stuck
    queue", the answer would be `pre-factory`.
  - Recorded with `metrics.py escaped spec/nested-rmdir-replay-recursion --from
    deferred-rename-source-orphan`.

Ruled at the GOAL gate:

- **Q1 — Where does the source-only scheduler defect go?** On a source-only stack, `boundary_policies`
  (`writeback.py:35-36`) has no entry for the source, so `deadline` (`:76`) raises `KeyError:
  'project'`. The first scan after any `mkdir`, `rename`, `unlink` or `meta` op raises it, and the pump
  dies. A spec with one `[[tier]]` is valid (`tier/spec.py:296-297`, `:311-314`), and the mount starts
  the scheduler unconditionally (`fuse/mount.py:220-223`). The defect blames to `8d2265f` (2026-07-03,
  pre-factory), and no seed records it. The map shows it in all 429 source-only `poll` cases that had a
  namespace row queued, the shape or not.
  - (A) A seed of its own, with a `ROADMAP.md` entry written by the foreman, out of this cycle.
    **Recommended**: it is a different mechanism in `writeback.py`'s policy table, another high-blast
    region, and not in the replay.
  - (B) Fold it into this cycle as R9, which makes R2 hold on a source-only stack too.
  - (C) Record it in this GOAL only.
  - **A:** (A), with the seed written in this cycle's records phase: an `issues/` seed of its own with a
    `ROADMAP.md` entry, carrying this reproduction. Its fix is queued after `rename-over-tombstoned-child`
    and `model-commit-route-reach` land, and is then triaged like any seed. R2 stays graded on stacks
    with a stage. (Geoffrey, 2026-11-09, in the foreman's session)
- **Q2 — Must a queue already stuck in a state database drain after the fix (R4)?**
  - (A) Yes, as R4 says. **Recommended**: the queue persists across stop and start (843 of 843 mapped
    cases), so any user who hit this holds the shape today. A fix made only where the removal is
    queued would leave those queues stuck for good.
  - (B) No: R4 is dropped, and a stuck database is left to the audit (`issues/audit-command.md`).
  - **A:** (A), R4 stands. (ruled 2026-11-09, foreman as gate judge)
- **Q3 — Does Geoffrey sign off at review unconditionally?**
  - (A) Yes, on the final engine diff, whatever the review finds, as for cycles 2–7. **Recommended**: the
    fix sits in `_execute_namespace_locked`, `_replay_through` and `_vacate`, which are high-blast-radius
    paths.
  - (B) Only on a confirmed finding in a high-blast-radius path, the default.
  - **A:** (A). After the blind review approves, Geoffrey signs off on the final engine diff before
    publish, whatever the review found. (Geoffrey, 2026-11-09, in the foreman's session)
- **Q4 — Must one `flush` or `drain` call drain the shape (R1), or may it take several?**
  - (A) One call. **Recommended**: `drain`'s contract is that a single pass converges
    (`drain.py:89-91`), and `Mount.shutdown` calls it once and settles `clean` on `complete=True`
    (`mount.py:252-253`). A drain that left a deferred row queued while reporting `complete` would settle
    `clean` over an unfinished source. That is the parked candidate's open question, and (A) makes it a
    requirement the plan must meet.
  - (B) Repeated calls converge, and `drain` reports `complete=False` while a row is left.
  - **A:** (A), one call. (ruled 2026-11-09, foreman as gate judge)
- **Q5 — The recursion fix fails at scale (plan stop, 2026-11-10).** The planner's v2 design passes the
  replay stack down the recursion. Its adversarial review drove N sibling chains until it recursed again
  (about N=330; `980f998` fails from N=2, reproduced by the foreman). Redesign how?
  - (A) An iterative worklist replay with no recursion-depth bound; Q4's single-pass argument redone; a
    500-sibling test (R9); a second adversarial pass before the plan commit.
  - (B) Bound the nesting depth, so a deeper row waits in the queue.
  - (C) Cap R1 at the listed shapes and seed the scale case.
  - **A:** (A). R9 records it. (Geoffrey, 2026-11-10, in the foreman's session)
- **Q6 — A distinct defect in `_clear_inner_target` (plan stop, 2026-11-10).** Renames crossing each
  other's subtrees make the first flush raise `EngineError` at `980f998` (reproduced by the foreman) and
  in the prototype; the reviewer's fixed-point loop over the rows between cures it, in a function this
  cycle's fix already changes. Fold it in, or seed it?
  - (A) Fold it in: amend this GOAL with R10, raise the appetite to `big`.
  - (B) Keep this cycle to the `rmdir` site, drop the rename wait, seed the inner-target class.
  - **A:** (A). R10 records it; the appetite is now `big`. (Geoffrey, 2026-11-10, in the foreman's
    session)

Ruled at the plan gate (`e441311`, 2026-11-10), each on the planner's recommendation:

- **P-Q1 — The 20-minute `mutants` mode.** — **A:** it runs through the background runner, as TECH says,
  with network allowed on that same call; it is not split. (ruled 2026-11-10, foreman as gate judge)
- **P-Q2 — `Journal.queued_marks` in coupled core.** — **A:** accepted. It is a narrow read the
  racing-removal fix (review 2, F1) needs, and Geoffrey's sign-off (Q3) covers it with the rest of the
  engine diff. (ruled 2026-11-10, foreman as gate judge)
- **P-Q3 — The source lock held through a very large flush** (about 90 s at 5,000 sibling chains, where
  `980f998` raised at once). — **A:** accepted as PLAN § 5's risk; no batching work in this cycle. The
  base never drained these queues at all. (ruled 2026-11-10, foreman as gate judge)
- **P-Q4 — The serial suite time in `AGENTS.md`.** — **A:** the build measures it on a quiet run and
  records it under R7. (ruled 2026-11-10, foreman as gate judge)

Ruled during the build:

- **B-Q1 — § 10's re-entrancy reason is untrue after P1, but PLAN froze § 10** (P1 hand-back, `76f7e8a`).
  `_replay_through` now runs rows through `_execute_namespace_locked`, so replay no longer takes
  `_source_lock` a second time. AGENTS.md § 10's "It is re-entrant because replay nests: …" and the comment
  at `overlay.py:104` still say it does; the lock stays re-entrant for `rename` around `_vacate` and the cut
  around `_serve`. — **A:** R7 governs. P2 rewrites that sentence and the comment to the reasons that still
  hold, as a TECH amendment, and relaxes `drain_gate.py records`' § 10 check to the base text with only that
  sentence changed. Q3's sign-off covers it. (ruled 2026-11-10, foreman as gate judge)

## Related materials

- No seed: the inline description is the record. The prior art is the parked
  `refs/park/rename-over-tombstoned-child-prototype`, read-only: its `park/c8plan-probe/nested.py`,
  `nested_min.py` and `proto-both.diff`.
- Origin cycle: `spec/deferred-rename-source-orphan/` (`fe26962`). The shape precedent is
  `spec/entry-identity-token/`.
- Related seeds: `issues/pump-liveness-and-loss-windows.md`, `issues/engine-error-mapping.md`,
  `issues/audit-command.md`, `issues/freethreaded-concurrency-stress.md`, `issues/ci-integration-lane.md`.
- Contract text in `AGENTS.md`:
  - § 5's `_replay_through` bullet (`:447`);
  - § 8;
  - § 10's state-exception bullet (`:574`);
  - § 11 (`:639`);
  - the high-blast-radius rows for `writeback.py` (`:227`) and `drain.py` (`:230`);
  - § Known open violations (`:721`, `:722`).
- Sources, with anchors confirmed at `980f998`:
  - `src/sosfs/engine/overlay.py`:
    - `rmdir` 428, and `commit_row` 939;
    - `flush` 1072, and `_execute_namespace` 1091;
    - `_execute_namespace_locked` 1105, with the `rmdir` row's pre-replay at 1110-1118;
    - `_holder_rows` 1257, and `_clear_inner_target` 1275;
    - `_replay_through` 1302, with its skip at 1312;
    - `_vacate` 1317;
  - `src/sosfs/engine/writeback.py`: `boundary_policies` 27, `deadline` 70, `due` 85, `poll` 90,
    `on_commit` 111;
  - `src/sosfs/engine/drain.py`: `drain` 74;
  - `src/sosfs/fuse/mount.py`: the pumps start at 220-223, and `shutdown` at 238;
  - `tests/engine/test_replay.py`:
    - the module docstring 4-64, and `assert_converged` 198;
    - `test_flush_after_child_moved_out_of_removed_parent` 413;
    - `test_flush_removes_a_moved_out_child_before_its_old_parent` 423;
    - `test_mkdir_over_removed_directory_with_queued_child_removal` 436.
