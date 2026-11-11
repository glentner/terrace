# 00 — Digest

The plan ran research itself, sequentially, and wrote the briefs below. There was no worker fan-out: the root
cause was reproduced on the first read, and the design was probed directly on a prototype.

- [01 — Root cause](01-root-cause.md). The replay keeps no record of its stack, so rows whose replays reach each
  other re-enter until `RecursionError`. This happens for nested chains, sibling chains (from two chains up) and
  crossing renames. A second cause, a one-shot read of a rename's places between, makes R10's crossings raise
  `EngineError`. The v2 design, which passed the stack down the recursion, still recursed at about 330–400
  chains.
- [02 — The worklist and Q4](02-worklist-and-q4.md). The replay becomes a loop over an explicit stack. Rows are
  known by their marks. A nested row that needs one on the stack waits. A row reads its needs again until none
  is queued, and the bottom row never waits. One pass therefore drains every row it read.
- [03 — Evidence](03-evidence.md). The evidence on the prototype:
  - 57,500 differential runs with 0 regressions;
  - the shape map with 0 violations;
  - 47 real base-written stuck directories, each drained in one pass;
  - 78 of 78 hand probes passing;
  - the scale and mutant tables, which are in PLAN § 4.

## Decisions settled here

- **The explicit stack, not a bound on nesting depth.** This follows Q5.
- **Marks, not ids.** Ids are reused, so the replay keys its sets by mark (review 2, F1).
- **`Catalog.held` and `Journal.queued_marks`.** These add two narrow reads instead of an engine cache. Without
  them, 500 chains take 10–26 s.
- **The memo of waiting rows.** It is kept as pure performance.
- **The bottom-stuck branch.** It is kept as a guard, unreachable from queues the engine writes, and its mutants
  are listed as unreached.
