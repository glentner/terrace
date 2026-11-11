---
status: unshaped
kind: fix
appetite: big
lane: public
---

# A single replay's cost grows faster than its queue, under the source lock

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`flush`, `drain` and a scheduler poll run each namespace row they read through `_replay_through`, which
holds the source lock (`OverlayEngine._source_lock`, `AGENTS.md` § 10) for the whole replay. Some
queue shapes now drain correctly but take time that grows much faster than the queue. While one
runs, every `create`, `mkdir` and `rename` that reaches `_vacate`, every promotion off the source and
every handle-less `truncate` waits for it. A drain bounded by `drain_timeout` may not finish in time.

Measured at `60406ae` by the review of `nested-rmdir-replay-recursion` (cycle 1, 2026-11-10), on 1 and 3
tiers, every case draining, matching a plain-`os` oracle and converging:

- **Chains removed level by level.** N sibling chains of nested directories, each with an entry moved
  out, and the removals issued one level at a time across all chains. At depth 2 one flush took 2.6 s
  at N = 100, 16 s at N = 200, 49 s at N = 300, and 89–136 s at N = 500 (flush and drain).
- **Every level, removed in reverse.** At depth 3, with an entry moved out of every level, one
  `drain` at N = 500 took 1,097 s.
- **One deep chain turned inside out.** K levels removed outermost first: 2.45 s at K = 100 (199 rows)
  on a quiet machine, and 40.8 s at K = 200 (399 rows) under load.

At `980f998` none of these queues drained at all: each raised `RecursionError`. The same chains
removed chain by chain scale as `nested-rmdir-replay-recursion` planned: 78 s at 5,000 chains.

Mechanism, for orientation only; these cites hold at `60406ae`. Before an `rmdir` or `rename` row's
needs, the replay reads the queue's marks again (`later()`, `src/sosfs/engine/overlay.py:1315-1321`,
through `Journal.queued_marks`, `src/sosfs/state/journal.py:312`) and rebuilds the list of rows not
yet run (`src/sosfs/engine/overlay.py:1363-1364`). Each of these is a full pass over the queue. When
the removals interleave across chains, the replay evaluates on the order of N² needs, and each costs a
pass. A profile of the level-order case puts most of the time in `queued_marks` and
`Writeback.from_row`.

The cycle's remediation of review 1 made only a row that can need another read the queue: an `rmdir`
whose directory's object still holds another entry, or a rename whose target lies inside its own
object. Every removal in the shapes above is such a row, so their cost is as measured: the level-order
case at depth 2 took 0.90 s before that change and 0.93 s after at N = 100, and 6.05 s and 6.14 s at
N = 200, back to back on one host.

## Why it was deferred

Out of Scope for `nested-rmdir-replay-recursion`. No R-ID states a latency: R9 asks that one `flush` or
`drain` drain N ≥ 500 chains, and it does. The plan gate (P-Q3) accepted the source lock held through a
very large flush, at about 90 s for 5,000 chains in chain order; the orders above go beyond what that
ruling measured. The cost is new with the fix, but only on queues the base could never drain.

## Outcome / vision

A replay's cost is close to linear in the queue it runs, whatever the order of the removals, so a
large flush or drain holds the source lock for time proportional to its work.

## Sketch of the acceptance criteria

- **R1** — WHEN `flush()` or `drain()` runs on N sibling chains removed level by level, or on one chain
  removed outermost first, the time per queued row SHALL stay within a constant factor as N doubles,
  from N = 250 to N = 1,000, measured by a deterministic count of queue reads rather than wall time.
- **R2** — Every queue `tests/engine/test_replay.py` drains today SHALL still drain, match the oracle and
  converge.

## Notes

- Related: `issues/source-commit-staged-publish.md` (the other long hold of the source lock),
  `issues/pump-liveness-and-loss-windows.md`, `spec/nested-rmdir-replay-recursion/REVIEW.md` (cycle 1:
  out of Scope O2 and the inverted chain). The slowdown on plain rename queues that the base already
  drained was finding P1 there; Geoffrey ruled it a fix, and the cycle's remediation fixed it: a pass
  over rows nothing stands in reads the queue a fixed number of times, pinned by a count of queue reads
  in `tests/engine/test_replay.py`.
- Found by: `nested-rmdir-replay-recursion` review cycle 1 (both debate reviewers).
