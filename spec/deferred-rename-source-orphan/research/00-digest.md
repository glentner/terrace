# Research digest — deferred-rename-source-orphan

Four briefs, written in parallel by read-only research workers against the cycle worktree (code
identical to `141aa86` in every region read), each with a throwaway lab outside the repo:

- [`01-data-commit-path.md`](01-data-commit-path.md): where a data commit to the source writes, and
  the physical-path collisions it meets.
- [`02-namespace-replay.md`](02-namespace-replay.md): why `flush()` diverges, and whether
  current-path replay converges.
- [`03-model-test.md`](03-model-test.md): extending the model (R7) without weakening it (R8); seeds
  and CI (R9, R10).
- [`04-concurrency.md`](04-concurrency.md): lock order and races around the candidate fix.

The planner then built a prototype of the synthesized design in a detached worktree and dry-ran the
drafted gates on it (PLAN § 4, § 6).

## Consolidated decisions

1. **Replay resolves an entry's current target, not its queue-time path.** The queue is a lossy
   history: `_remove` cancels a purged entry's rows, so replaying the recorded `detail['path']` /
   `detail['new']` re-creates ancestors nobody owns (C2) and reads the kind and mode of reused paths
   that have since changed (C1). Every patch to historical replay needs ownership tracking for
   materialized ancestors, which is current-path replay by another name (02 § 3). The target of
   entry E, T(E), is its parent's committed source path (or the parent's own target, recursively)
   plus E's name.
2. **A data commit to the source writes at `source_path`, else at T(E)** (GOAL Q2). The queued
   rename stays the only mover of an existing source object. Ancestors the source lacks are placed
   at their own targets, by entry id and with catalog modes, intent-bracketed, each recording its
   `source_path`; nothing on the source side resolves a mode by path again (01 § 1, 02 amendment 1).
   The `source_path` update moves into the transaction that confirms the copy, closing a § 1 split
   that exists at `141aa86` (01 § 1).
3. **The uniqueness rule is enforced when the user op runs, keyed on the physical target.** No two
   entries, live or tombstoned, share a `source_path`, and no new name's target equals another
   entry's `source_path`. `_vacate(X)` runs, in queue order, every queued namespace row up to and
   including the rows that free T(X): the tombstone's own removal and the queued departure of any
   other entry holding T(X). The commit is the wrong place for this (01 § 3: passthrough creates,
   the mkdir replay's silent adoption, and the commit only ever needs an assertion once the rule
   holds). Brief 01's "flush the queue prefix up to F's row" and brief 02's "replay the queue in
   order through the bound" are the same mechanism; the digest adopts it.
4. **`_purge_source` replays the rows that empty a replaced directory first** (02 P4), through the
   same in-order helper, so the helper is the single place a namespace op jumps its policy clock.
5. **Tombstone purge after a rename row is strict.** Purge the tombstone at `detail['old']` only when
   it is deleted, holds no `source_path`, has no queued row, and no entry holds `detail['old']` on the
   source (02 amendment 2, P6a and P6b). Tombstones never carry rename rows (02 amendment 3).
6. **One re-entrant source-namespace mutex,** ordered path locks → source mutex → `_handles_mutex` →
   SQLite write lock. Replay never takes a path lock (04 § 2). Nothing holding the mutex calls
   `locks.hold`, `_ensure`, `getattr` or `readdir`, and nothing acquires it inside a transaction
   (04 § 3). It spans the whole copy when a data commit's destination is the source: brief 04
   recommended staging outside the mutex and publishing inside it, which changes the `Tier` protocol
   in `posix.py` and `s3.py` (two more high-blast-radius regions) and has no cheap S3 form; the
   digest takes the simple form and records the latency cost as a deviation (PLAN § 3).
7. **Stale rows are recognized, not just looked up.** A scheduler snapshot can hold a row that a
   user op has since run (vacate), cancelled (`_remove`) or cascaded away. `writeback.id` is an
   `INTEGER PRIMARY KEY` without `AUTOINCREMENT`, so the id of a deleted highest row is reused: the
   planner's probe on the prototype showed a cancelled `rename` row's id taken by the `unlink` row
   queued in its place, the stale rename executing, and the `unlink` row settled under it, leaving
   the old bytes on the source with an empty queue. A row runs, or settles as moot, only while the
   queued row with its id has the same entry, op and detail. No schema change (04 § 1 rows 1-2,
   extended by the planner).
8. **Same-path renames skip the tier call** (01 § 4: `S3Tier.rename(p, p)` destroys the object on
   an obstore store; POSIX hides it).
9. **The model gains rules, never loses one** (03): commit one entry; one poll after an arbitrary
   advance; one poll at the next deadline; a scan and a later commit of the scanned rows (the pump's
   SCAN and COMMIT states, with user ops between them); write-and-fsync under
   `fsync_policy = "commit"`; commit-then-evict; a drain that polls deadline by deadline and then
   asserts the queue is empty and the source equals the oracle; an invariant that every source
   object but the never-adopted `/seeded.txt` has exactly one holder; a remount in `teardown`.
   `max_examples` rises to 1000: uniform rule choice rarely builds the 4-7 step windows (03 § 1:
   the extended model found the defects on 1 seed in 6 at 100 examples). The adversarial review later
   added two composite rules (commit-rewrite-rename-commit; commit-rename-reuse-commit), without which
   the defect-1 mutant escaped the fixed CI seed (PLAN § 4.1).
10. **Deterministic regressions sit beside the model** in `tests/engine/test_replay.py`, one per
    route and class, each failing on the `141aa86` engine (03 § 1, 02 § 2).
11. **R10 runs from a `git archive` of HEAD, with no `.hypothesis/` and `CI` unset** (any `CI` value
    loads Hypothesis's `ci` profile, whose `derandomize` beats `--hypothesis-seed`; 03 § 4).

## Contradictions resolved

- **Mutex scope** (04 recommends staged publish; 02 needs only some serialization): simple whole-copy
  mutex, decision 6.
- **Vacate keyed on X or on P(X)** (01 § 3 (d) shows the check must be on the physical target; 02
  builds it on T): on T(X), decision 3.
- **Tombstone purge rule** (02's "pure tombstone" rule unmasks in P6a): the strict rule, decision 5.
- **Drain order in the model** (03 advances to the earliest deadline; an early prototype jumped past
  every deadline, which reproduces `flush`'s namespace-first order inside one poll): the earliest
  deadline, decision 9.
- **The orphan invariant's exemption** (03: exempt `/seeded.txt` by name; an early prototype exempted
  any path whose parent was unlisted, which never flags a root-level orphan because no rule lists
  `/`): by name, decision 9.

## Questions the research raised about how an R-ID reads

None blocks the design. Each reading the design takes is a row in PLAN § 3:

- R1's three routes and R2 are met by writing at the existing physical place, so the source holds the
  committed bytes at the *pre-rename* path until the rename row runs. R1 and R2 say "once the queue
  drains", which this satisfies; the regressions assert the intermediate state too.
- R3 is read for the source tree's paths, kinds and file bytes (R3's own words), not modes or
  mtimes.
- R7's "advancing a single write-back scheduler poll" is met by `WritebackScheduler.poll` called once
  per rule, synchronously, on an unstarted scheduler (`fsm.py`: `halting` is false until started).
