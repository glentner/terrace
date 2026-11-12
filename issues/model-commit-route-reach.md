---
status: unshaped
kind: test
appetite: small
lane: public
---

# The model rarely reaches the scheduler and eviction commit routes

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The `deferred-rename-source-orphan` cycle closed a data commit during a queued rename that orphaned
the pre-rename object (`AGENTS.md` § *Invariants* § 5). Three production routes reach that commit:
`fsync` under `fsync_policy = "commit"`, the eviction manager committing a dirty copy, and a
write-back scheduler poll that settles the data row before the rename row. The cycle added rules to
the stateful model (`tests/engine/test_model.py`) so it reaches single commits and scheduler polls:
`commit_entry`, `poll_once`, `poll_next_due`, `scan_writeback`/`commit_scanned`, `evict`,
`write_and_fsync`, `rewrite_rename_commit` and `drain_by_polls`, with a convergence check after a
drain by polls.

The rules fire, but two of the routes almost never reach the state that matters. The cycle's first
review (`spec/deferred-rename-source-orphan/REVIEW.md`, F4, 2026-11-06) instrumented
`OverlayEngine._commit_hop` over the model's fixed CI draw (`CI=true`, 1023 examples) and counted
commits to the source made while the entry's own rename was queued:

| Route into `_commit_hop` | Fixed CI draw | Two random runs |
|---|---|---|
| `fsync` under the commit policy | 7 | — |
| `rewrite_rename_commit` (a direct `commit_upstream`) | 46 | — |
| a scheduler poll (`commit_row`) | 0 | 1, 0 |
| the `evict` rule (the eviction manager's commit-then-drop) | 0 | 0, 0 |

Both `poll_*` and `evict` run about two thousand times per draw. The scheduler route needs a data
row due before its entry's rename row: a write, a rename of that file, then a poll whose clock lands
past the data row's deadline and short of the rename row's (`poll_next_due` exists for that window
and still lands in it rarely). The `evict` route needs the rule's random path to name a file whose
staged copy is dirty and whose rename is queued. So the model's convergence check, the net for
defects nobody has named yet, is not cast over those two routes.

Nothing is unguarded today. `tests/engine/test_replay.py` pins both routes deterministically
(`test_scheduler_poll_settles_data_before_rename`, `test_eviction_commit_before_queued_rename`), and
the fix-removal mutant for a commit at the logical path fails the model through the routes it does
reach.

## Why it was deferred

The review graded it a coverage observation, not a defect (R7's stated check holds: both ops are in
the rule mix and the convergence check exists). It was not changed in that cycle's remediation:
Hypothesis derives the fixed CI draw from a digest of the `OverlayMachine` class source, so any edit
to the model, a docstring included, redraws the sample the cycle's `verify` and CI were graded on.
Leaving `test_model.py` unchanged was the foreman's instruction in the remediation brief for that
reason, not a review-gate ruling; the rulings at the review gate (2026-11-06) were to fix F2 and F3
in that cycle and to sign off after the re-review.

## Outcome / vision

Under the fixed CI draw, the model commits to the source through a scheduler poll and through the
eviction manager while the entry's rename, or its directory's, is queued, often enough that a
defect on either route turns the model red, and the count is measured rather than assumed.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHEN `tests/engine/test_model.py` runs under `CI=true`, the model SHALL reach
  `_commit_hop` with the source as its destination, while the entry's or an ancestor's rename is
  queued, through `commit_row` and through the `evict` rule at least a stated number of times each,
  as counted by an instrumented run the cycle commits as evidence.
- **R2** — The model SHALL keep every rule it has, its strategies no narrower, and its `max_examples`
  and `stateful_step_count` no lower; it SHALL add no skip guard, `assume`, precondition or filter
  (`AGENTS.md`'s high-blast-radius row for the engine test modules).
- **R3** — WHEN the edit redraws the fixed CI sample, the profile's `verify` SHALL pass under
  `CI=true` and `test_model` SHALL pass for `--hypothesis-seed` 1–20, and every fix-removal mutant the
  model is required to fail SHALL still fail it.

## Notes

- Open question — **how to get there.** A composite rule (write, advance past the data row's delay,
  rename, poll) gives the scheduler route one-step reach under parity, the way
  `rename_scan_unlink_commit` does for stale rows; so does a composite for eviction (write, rename,
  `evict` the renamed path). Driving the real `EvictionManager` would need a stage quota, which changes
  the spec `__init__` builds; `tests/engine/conftest.py` is an interface (`AGENTS.md` § Conventions),
  so any quota goes through `dataclasses.replace`, as `fsync_policy` already does.
- Open question — **the draw.** Any edit redraws the CI sample, so the cycle re-runs `verify`, the
  twenty seeds and the mutants on the new draw, as the namespace-replay cycle's P3 had to.
- Related: the `source-path-truncate-and-stat` cycle (landed 2026-11-08,
  `spec/source-path-truncate-and-stat/`) decided whether the model gains a source-served
  configuration, which edits the same class and redraws the same sample; doing both in one cycle
  would have paid for one redraw.
  `spec/deferred-rename-source-orphan/` (GOAL R7, R8; PLAN § 2.7; REVIEW F4).
  - 2026-11-06: `source-path-truncate-and-stat` decided it: the model gains no source-served
    configuration, and `tests/engine/test_model.py` stays unchanged (that cycle's GOAL Q2).
    Model coverage of source-served files, if wanted, stays with this seed, whose cycle redraws
    the sample anyway.
- 2026-11-11 (whole-tree review at `64faf27`): the cycle that adopted this (branch
  `test/model-commit-route-reach`, parked at its plan's stop on 2026-11-09) found two engine defects on
  its dry runs, and each opened a fix cycle of its own.
  [`issues/namespace-sync-redesign.md`](namespace-sync-redesign.md) proposes to take the widened model as
  its acceptance instrument (its R7) instead of landing it against the current replay. Ruled by the
  maintainer on 2026-11-11: it is folded into that rebuild and does not resume. The branch stays as the
  record.
- Found by: `deferred-rename-source-orphan` review cycle 1 (F4, coverage observation), 2026-11-06.
