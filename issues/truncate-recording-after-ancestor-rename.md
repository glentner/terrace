---
status: adopted:truncate-recording-after-ancestor-rename
kind: fix
appetite: small
lane: public
---

# A handle-less `truncate` records its cut under a path an ancestor's rename has just moved

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`truncate` without a handle keys its dirty mark (`journal.mark_dirty`, before the cut) and its recording's
catalog writes (`catalog.update_meta` and `catalog.update_residency`, after it) by the path it was given;
the recording's third write, the stage counter (`registry.adjust_usage`, for a cut off the source), is
keyed by tier. A path lock covers that path, not its ancestors, and `rename` moves a directory's whole
subtree in the catalog (`catalog.rename`), then on every stage (`tier.rename`), under the two directory
paths' locks alone. When that move lands after the cut has decided its keys and before it records:

- the catalog writes match no row. The bytes are cut, but `entry.size` and the copy's residency `size`
  keep the old figure, and the tree does not converge after a flush;
- the counter write lands, so the stage's counter moves by the cut's size change while the residency
  does not. Eviction subtracts the residency's figure and leaves the counter off by that change,
  negative after a cut that shrank the copy: eviction trusts these counters (`AGENTS.md` § 7);
- a read-only open and release heals the residency `size`, and moves the counter by the same change a
  second time, so the counter still ends that far off. `entry.size` heals only on another write or
  `truncate`.

Evidence:

- Review cycle 2 of `source-path-truncate-and-stat` (F6 in its `spec/` `REVIEW.md`), at `ea38083`. The
  stack was a stage with `bypass_size=100` and a 5-byte `data/s.txt` the stage serves, and another
  thread renamed `/data` to `/moved` as the stage `open_direct` returned. `truncate` returned; `getattr`
  reported 5; the residencies were `{'project': ('clean', 5), 'scratch': ('dirty', 5)}`; the stage held
  `moved/s.txt` as `b'sm'`, and the overlay read `b'sm'`. After a flush `getattr` still reported 5 and
  `assert_converged` failed. The output was the same at `0e6d14c` and `c3f31ff`: pre-existing.
- Since that cycle's review cycle 2 remediation, the handle-less cut holds the source lock from its
  decision through its recording, whatever copy serves. `rename` takes the source lock in its `_vacate`,
  before its catalog move, so a rename that starts while the cut runs waits there until the cut is
  recorded. The review's interleaving no longer fails: a probe (not committed) that starts the rename on
  another thread as the stage `open_direct` returns ends with `getattr` 2, the residencies
  `('clean', 5)` on the source and `('dirty', 2)` on the stage, and a converged tree.
- A rename that has already passed its `_vacate` when the cut takes the source lock is not held off.
  The same probe, running the rename in the cut's own thread as the stage `open_direct` returns (the
  source lock is re-entrant, so the rename runs as one already past `_vacate` would), ends with
  `getattr` 5, the residencies `{'project': ('clean', 5), 'scratch': ('dirty', 5)}`, and
  `assert_converged` failing after a flush. A cut served off the source records by the same keys, so
  the same window leaves its recording missing in the same way (argued from the code, not run).
- The counter, from review cycle 3 of that cycle (F1, at `aafdd44`), with the rename landing after the
  stage residency was read and before the recording's transaction: the stage counter read `(2, 1)`
  (bytes, files) with the residency `('dirty', 5)`; after a flush and the copy's eviction, `(-3, 0)`,
  and `assert_converged` failed. A read-only open and release turned the residency into
  `('dirty', 2)`. The cycle's last remediation build re-ran it with that open and release before the
  flush: the counter read `(-1, 1)` after it, eviction still left `(-3, 0)`, and `getattr` stayed 5.
- Landing earlier in that window, the rename makes `_serve` raise `EngineError` (no resident copy at the
  old path), a promotion off the source raise `LookupError` after its copy (its `promote` intent stays
  unresolved, and the stage holds the bytes under no residency), `mark_dirty` raise `LookupError` (no
  entry at the path, a non-`OSError`; see `issues/engine-error-mapping.md`), or the stage open raise
  `ENOENT`, which the cycle's plan-gate ruling accepts for an ancestor renamed mid-call. Review cycle 3
  found the counter and the first two landings the same at `0e6d14c` and `ea38083`: pre-existing.

Other calls that hold a path lock and write the catalog by that path share the shape, `evict_copy` and
`_set_meta` among them. That is argued from the code, not reproduced.

## Why it was deferred

Geoffrey ruled it out of `source-path-truncate-and-stat` at that cycle's review-cycle-2 escalation
(2026-11-07). It is pre-existing, and a stage-served file is outside that GOAL's R1 clause. The cycle's
restructure narrowed the window to renames already past `_vacate`, as above, and did not close it.

## Outcome / vision

A handle-less `truncate` records its cut on the entry it cut, wherever an ancestor's rename has moved
that entry meanwhile. The sizes the catalog reports match the bytes, and the tree converges once the
queue drains.

## Sketch of the acceptance criteria

- **R1** — WHEN a directory rename moves a file's ancestor between a handle-less `truncate` deciding its
  catalog keys and recording its cut, the recording SHALL reach that file's entry and residency, the
  stage counter SHALL agree with the residency it counts, and once the queue drains the source tree
  SHALL equal the overlay view, sizes included.
- **R2** — WHEN the same rename lands before the dirty mark, or during the serving decision or a
  promotion, the call SHALL either act on the copy where the entry now is or raise an `OSError`, never
  `LookupError` or `EngineError`, and SHALL leave no unresolved intent.
- **R3** — Both SHALL be pinned by deterministic injected-interleaving tests in
  `tests/engine/test_replay.py`, each with a fix-removal mutant that turns it red.

One possible mechanism, not decided: resolve the keys from the entry's id inside the transaction that
writes them (`BEGIN IMMEDIATE` serializes it against the rename's catalog move), for the dirty mark as
for the recording. Shaping should weigh it against taking the ancestors' path locks.

## Notes

- Related: `issues/entry-identity-token.md` (the identity gap in the same lookup the cut makes);
  `issues/engine-error-mapping.md` (the `LookupError`).
- *Added 2026-11-08, retiring `source-path-truncate-and-stat`:* **the counter guard.** The recording
  moves the stage counter only when `previous is not None` (`src/sosfs/engine/overlay.py:742`,
  which holds at `6425a21`). That cycle's mutant sweep
  (`spec/source-path-truncate-and-stat/remediation/cycle2-mutant-sweep.md`, E1) found that dropping
  the guard changes behaviour only inside this seed's window, where it keeps the call from raising `AttributeError` after the bytes are cut, and
  left it unpinned because a pin would assert the stale size this seed fixes. Once the recording
  reaches the entry, re-judge the guard: pin it, or remove it if the fix leaves it dead.
- Found by: `source-path-truncate-and-stat` review cycle 2, F6 (2026-11-07).
