# Research digest — truncate-recording-after-ancestor-rename

Three briefs, fanned out because the change touches high-blast-radius paths (`truncate`, and `rename`
beside `_vacate`): trigger 2. The deliverable carries its own gate, so the adversarial review ran too
(trigger 3; PLAN § 4). All three read the tree at `d6d2280`, whose `src/` and `tests/` equal `630a406`.

- [`01-locks-and-identity.md`](01-locks-and-identity.md): proves the held-off design by reading the code.
- [`02-test-harness.md`](02-test-harness.md): the test module, the seed's evidence re-run in a lab, and the
  two-thread driver.
- [`03-records-and-gate.md`](03-records-and-gate.md): every record R7 names and three it does not, and the
  lessons of cycles 3 and 4's gates.

## Decisions

1. **Q1: held off, not keyed by identity.** A user `rename` holds the source lock from its `_vacate`
   through its catalog-move transaction. `Catalog.rename` is the only statement that changes `entry.path`,
   and its one caller is that block (01 § 1), so while the cut holds the source lock and its own path lock
   the file's path cannot change, and every path-keyed read and write the cut makes reaches the entry its
   lookup returned (R4). Keyed by identity fails on its own terms: a promotion's stage `put` at the old
   logical path can race the rename's stage move, leaving bytes recorded by id at a path the catalog does
   not hold (01 § 6). It would also touch `journal.py`, `catalog.py` and `transfer.py`, all coupled core.
2. **The lock order holds.** The rename takes path locks, then the source lock (`_vacate` re-enters it),
   then `BEGIN IMMEDIATE`. Inside the hold are only catalog and journal calls; `_remove` stays before it
   and the stage moves after it (01 § 3).
3. **The stage moves stay outside the hold.** Moving them inside would close a window outside R1 and R3
   (a call at the *new* path between the catalog move and the stage move, 01 § 5a), at the price of
   holding the source mutex across stage renames, which on an object-store stage copy a whole prefix. That
   window goes to `issues/freethreaded-concurrency-stress.md` with Q3's line, and a pin test holds the
   stage moves outside the lock so the records stay exact.
4. **R5: remove `previous is not None`.** Under the hold, nothing can drop a stage residency while the cut
   holds its path lock and the source lock (01 § 2), so `previous` is never `None` for a stage. Today the
   guard is reachable only through the race, where it hides the miss (02 § 2: the counter stays right
   when the pause is at `open_direct` only because the guard skips). The `serving != source` half stays.
5. **Tests use a second thread, never the seed's same-thread injection.** The RLock lets a same-thread
   rename re-enter, so that injection models a state production cannot reach and stays red under the fix
   (01 § 4, 02 § 2). Each test runs the rename on its own thread, stopped just past `_vacate`, and the cut
   on another, stopped at a point in its window; a watched lock reports a thread that must wait, so the
   driver never waits on a blocked thread (02 § 3). Each test asserts R1's two outcomes as an either/or,
   so a correct design other than this one passes it.
6. **Records beyond R7's list** (03 § 1): `rename` joins both high-blast-radius tables; the engine-test
   count; `test_replay.py`'s module docstring; and the stress seed's "A replay racing a user rename of the
   entry's directory" bullet, which the hold also closes and which must not keep saying the rename "moves
   the catalog without the source lock" (01 § 5, 03 § 1).
7. **The gate** follows cycle 4's `open_gate.py`, with the review's lessons folded: added-line length,
   `HYPOTHESIS_STORAGE_DIRECTORY` in scratch for every run, "owned by"/"home" in the ledger's vocabulary,
   and bounded waits in every threaded test (03 § 2).

## Contradictions resolved

- 02 proposed six tests and called a cut-first test optional; 03 and R2's "WHEN the call returns" need the
  return branch pinned. Resolved: nine tests, six that the base fails and three pins (PLAN § 2.2).
- 02 found the counter drift only with the pause after `get_residency`; 01 argued it from the code.
  Resolved: a test pauses there (`COUNTED`), and the plan cites 02's measurement.
- 01 listed the same-shape calls widely (`create`, `mkdir`, `_remove`, …); the GOAL's Q3 names
  `evict_copy` and `_set_meta` "and any other". Resolved: Q3's line names all of 01's list, argued from the
  code, not reproduced.
