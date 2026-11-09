# Research digest — entry-identity-token

Three briefs, fanned out because the change touches high-blast-radius paths (`truncate`, `open`, `_serve`,
`_lookup_again`, and the purge sites in `_remove`, `_execute_namespace_locked` and `_vacate`): trigger 2. The
deliverable carries its own gate, so the adversarial review runs too (trigger 3; PLAN § 4.1). All three read the
tree at `5893e1f`, whose `src/` and `tests/` equal `c7d2049`, the merge base.

- [`01-identity-mechanism.md`](01-identity-mechanism.md) (planner): the purge sites, the families rejected,
  the purge watch and why its order is sound, and what a deterministic test can see.
- [`02-test-harness.md`](02-test-harness.md): the baseline re-run for every sequence R1–R4 names, the draft
  tests and their mutants, from a lab with its own build of the watch.
- [`03-records-and-gate.md`](03-records-and-gate.md): every record R8 names plus one count it doesn't, Q5's
  inventory, and what `identity_gate.py` keeps from cycle 5's gate.

## Decisions

1. **Q1 (A): an in-process purge watch per call.** It is opened before the first lookup, and every purge
   records into it inside its own transaction, before commit. No schema change, no new holder of the source
   lock, and ids are still reused, so the tests that assert reuse pass unchanged (01 § 2–3).
2. **Atomic naming** (revised after the adversarial review; PLAN § 4.1). The first draft named again on an
   ambiguous record, in a loop, and the review cut a file through it. The naming read and the clearing of the
   watch are now one step: under the watch mutex for a cataloged entry, inside the insert's transaction for an
   adopted one. A thread-local carries the watch into `_ensure`, whose one-argument signature cycle 4's
   `AfterLookup` relies on (passing it as an argument failed seven existing tests). M4, M5 and M10 attack the
   new decisions.
3. **The cut's check stays inline.** Folding it into `_lookup_again` moved the first `_lookup_again` call in a
   cut, so cycle 5's promotion-lookup test stopped at the wrong call and still passed (02). The prototype was
   unfolded; M1 and M6 attack the two sites separately (PLAN deviation row 4).
4. **`PurgeWatch` is declared below `OverlayEngine`.** That keeps the unpinned cite
   `issues/engine-error-mapping.md:17` (`overlay.py:74-76`) true without editing that seed (03 § 3). On the
   prototype, lines 1–100 of `overlay.py` differ from the base only in one same-length import line.
5. **A purge site per route.** Only `_remove` (`:534`) and the unlink row's replay (`:1121`) ever purge a live
   entry. A test reaches each: T13 through `_remove` (M9), T1–T4 and T12 through the replay (M2). The rename-tombstone
   purges are never a named entry and are guarded statically (E1, E2).
6. **Records per window** (03 § 1): the cut is closed in every window. `open` and a promotion are closed
   whenever they reach the lookup under the lock. The stage window before `_serve` decides stays open, as the
   Non-goals' "ancestor's round trip". A rolled-back purge refuses its own named entry. Q5's line names handles
   keyed by entry id and data-row snapshots (03 § 2).
7. **The gate** adapts cycle 5's `cut_gate.py`. It adds `frozen_schema` (R5), `purges_watched`, `watch_leaf`
   and a `truncate_kept` pin of the inline check and everything from `_serve` on. It keeps the `issues/` grep,
   the 120-character and spec-id checks, and process-group kills. No prose check requires a seed path in
   `src/` or `tests/` (03 § 3).

## Contradictions resolved

- 03 sketched "opening the watch after `_ensure`" as equivalent under single-thread injection. 02 and the
  planner's lab both kill it (M5) with a test whose purge lands right after the first lookup and whose carry
  lands at the source lock. Resolved: a real mutant. Only "record after `COMMIT`" stays unobservable, graded by
  reading.
- 03 asked for a test that purges through `_remove` and one through the replay. The planner's first draft
  reached only the replay; T13 and M9 were added. T13's first route did not actually reuse the id (a rename's
  tombstone held a larger one) and its own reuse assertion caught it; its route now runs the rename row alone.
- 02 counted 542 or 543 tests. After the review's folds: 545 (530 + 15), T14 (the review's route) and T15 (the
  adopt path) added, measured on the prototype.
- 01 § 3 argued the naming loop sound; the review demonstrated a miss through it. Resolved: atomic naming
  (decision 2); 01 carries a dated note.
