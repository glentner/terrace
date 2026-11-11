# 03 — Evidence on the final planning prototype

Planner, 2026-11-10. "Base" means the engine at `980f998`. "Prototype" means the planning worktree
`nested-rmdir-replay-recursion-prototype` with the final design: the worklist, row marks, `Catalog.held` and
`Journal.queued_marks`. Every comparison runs the same sequences on both, with one consumer call per run. The
scripts live in the session scratchpad, not in the tree.

## Differential random walks

Each sequence runs against a plain-`os` oracle. A run passes when:
- the consumer call returns;
- the queue and the intents are empty;
- the source equals the oracle;
- `assert_converged` holds.

| Walk | Runs | Base failures that now pass | Regressions | Left on both |
|---|---|---|---|---|
| Planner, default profile (6–22 ops, 1 or 3 tiers), `flush` | 8,000 | 40 at the consumer, 11 in an op | 0 | 3 `IntegrityError` |
| same, `drain` (`complete` asserted) | 8,000 | 40 + 11 | 0 | 3 |
| same, one `poll` with every row due, then `flush` (no namespace row left after the poll) | 8,000 | 39 + 11 | 0 | 3 |
| same, restart then `flush` | 8,000 | 40 + 11 | 0 | 3 |
| Planner, directory-heavy profile, `flush` | 8,000 | 25 + 13 | 0 | 8 `IntegrityError` |
| Review 1's `chain` profile (flush, drain, poll, restart × 1 and 3 tiers) | 17,500 | 395 + 188 | 0 | 35 `IntegrityError` |
| Review 1's `cross` profile (crossings mixed with removals; flush, drain, poll) | 10,000 | 52 + 20 | 0 | 45 `IntegrityError` |

Every failure left on the prototype is `sqlite3.IntegrityError` (`UNIQUE entry.path`) in `Catalog.rename`.
That is the parked `rename-over-tombstoned-child` defect, a non-goal, and the base raises it for the same
ops.

The two review profiles were rerun on the base with the current harness (`c9plan-rf-base`). An earlier base
run of the same harness disagreed on 7 `cross` seeds. Rerun, those seeds raise the same `IntegrityError` on
the base as on the prototype.

## The shape map (R5's oracle)

The shaper's 7,040 cases, rerun on the prototype:

| Outcome on base | Outcome on prototype | Cases |
|---|---|---|
| `RecursionError` | pass | 843 |
| pass | pass | 5,658 |
| `KeyError` (source-only scheduler, Q1) | `KeyError` | 429 |
| `EEXIST` (the script's own) | `EEXIST` | 110 |

Violations: 0.

## R4 on real state

- **One stuck state directory written by base.** `980f998` built the GOAL's shape and its `flush` raised
  `RecursionError`. The queue was `[(rmdir, /d1/d2), (rmdir, /d1), (unlink, /m.dat at /d1/d2/m.dat)]`, with 0
  intents. One prototype flush drained it, and so did one drain (`namespace=3, complete=True`). The source
  kept only the untouched file.
- **46 more.** These are the state directories review 1 left from base `RecursionError`s: 26 from its chain
  profile and 20 from its cross profile, raised in user ops or in a flush. Each one, copied, drained in one
  prototype flush (46 of 46) and in one drain (46 of 46). Each copy ended with no intent left, the source
  equal to its oracle, and `assert_converged` holding.

## Hand probes outside the map

There are 13 shapes, each run under flush, drain and restart-then-flush on 1 and 3 tiers, for 78 runs:
- a moved entry renamed again;
- a moved-out directory renamed again;
- a moved-out directory whose child moved again;
- the inner directory renamed before the move-out, and after it;
- the outer directory renamed;
- an entry moved into a sibling directory, then removed;
- mixed orders across two levels;
- a file moved into a moved-out directory;
- the outer name re-created and removed again;
- two sibling chains, three sibling chains, and two nested sibling chains.

Results:
- base: 78 `RecursionError`;
- the parked candidate: 18 `RecursionError`, all on the sibling probes;
- prototype: 78 pass.

## Scale

See `../PLAN.md` § 4 for the table, measured with `c9plan-scale.py`.

## The mutants

See `../PLAN.md` § 4 for the table, from the P1 lab's `drain_gate.py mutants`.
