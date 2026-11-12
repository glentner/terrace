---
status: unshaped
kind: test
appetite: small
lane: public
---

# Replay decisions no test distinguishes

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`_replay_through` (`src/terrace/engine/overlay.py:1282`; that cite and the line numbers below hold at
`60406ae`) makes several decisions that hand-planted mutants change without turning any test red. The review of `nested-rmdir-replay-recursion` (cycle 1,
2026-11-10, two independent reviewers at `60406ae`) ran every mutant below against the whole of
`tests/engine/test_replay.py`, and each one survived:

- **Performance-only decisions.** Removing the skip that keeps a waiting row from being read again
  while nothing has acted (`:1344`), and reading holder rows once per row rather than once per
  needs evaluation. Results stay correct; the module's new tests go from about 25 s to 433–717 s.
  No test bounds the work a replay does.
- **The bottom-of-stack branch** (`:1373-1383`). A row at the bottom whose pass freed none of its
  needs: making the `rmdir` raise instead of acting, letting the bottom row wait, and making the
  rename not raise. No test reaches it, and neither did about 28,000 randomized and shape-map
  sequences in the review, which matches § 5's statement that queues the engine writes never get
  there.
- **Bookkeeping that the sweeps showed no divergence for.** Not restarting a frame's prefix when its
  needs are read again, not counting acts, testing "stuck" by equality rather than inclusion, treating
  every row inside the needing row's place as needed, and not reading new rows at a prefix's end.
  Each survived the tests, and the first two also survived 4,000 crossing-rename sequences each.
- **Equivalent mutants** (recorded so they are not re-planted): `Catalog.held` including the directory
  itself, and widening `held`'s upper bound, which a probe over prefix-sharing siblings, unicode
  names and `/` showed matches the base's `startswith` rule.
- **Found by review cycle 2** (2026-11-10, two independent reviewers at `9269f76`; line numbers hold
  there):
  - Dropping the `needs & stack` half of the wait test (`:1387`), so a row waits only when stuck.
    All 183 tests the cycle added pass. 3,000 randomized sequences come out identical to the
    unmutated engine, case for case, because the stuck path catches the same rows one prefix pass
    later.
  - Dropping the check that a row read is still queued (`journal.current(current) is None`,
    `:1368`). All 286 tests in `tests/engine/test_replay.py` pass, including the test of a removal
    queued during a replay.

None of these breaks an R-ID's stated check or a reachable behaviour, so the review recorded them as a
coverage observation, not a defect.

## Why it was deferred

The rubric routes a surviving mutant in defensive or bookkeeping code to a seed when it breaks no
R-ID's check or reachable behaviour. These are the replay's own decisions, found in the cycle that
wrote them. That cycle's remediation changed when a row reads the queue (`_holder_rows`,
`_clear_inner_target`), not the decisions listed here, and the mutants above have not been planted
again since.

## Outcome / vision

Every decision in `_replay_through` has one of three things. A deterministic test turns its mutant
red. Or a test bounds the replay's work, so a performance-only mutant fails. Or it is recorded as
unreachable or equivalent, with the reason, in the test module's docstring.

## Sketch of the acceptance criteria

- **R1** — WHEN each mutant listed above is applied to `_replay_through`, `uv run pytest
  tests/engine/test_replay.py -m unit` SHALL fail, or the mutant SHALL be recorded as equivalent or
  unreachable with the reason.
- **R2** — A test SHALL bound the number of needs evaluations or queue reads one `flush` makes on a
  shape where rows wait, so removing the waiting skip turns it red.

## Notes

- Related: `issues/replay-cost-at-scale.md` (R2 here and its R1 may share a counter; the remediation's
  count of queue reads in `tests/engine/test_replay.py`, `queue_passes`, is one),
  `issues/model-commit-route-reach.md`, `spec/nested-rmdir-replay-recursion/REVIEW.md` (cycle 1,
  coverage observations).
- 2026-11-11 (whole-tree review at `64faf27`): held.
  [`issues/namespace-sync-redesign.md`](namespace-sync-redesign.md) proposes to delete the function these
  mutants live in. This seed has no object if that is adopted, and is worth a cycle only if it is
  declined.
- Found by: `nested-rmdir-replay-recursion` review cycle 1.
