# Research digest — `open` finds a source-served file while a replay moves it

Two briefs, fanned out because `open` and `_serve` are on `AGENTS.md`'s high-blast-radius list
(code-plan trigger 2): [`01-open-contract.md`](01-open-contract.md) (the design against § 5 and § 10,
the Q1 choice, the identity gap, baseline probes) and [`02-records-and-gate.md`](02-records-and-gate.md)
(R6's record sites, the seed format, the retired verifier, gate reuse, timings). The planner then built
the design in a detached prototype worktree and dry-ran the drafted gate on it (PLAN § 4).

## Decisions

1. **Shape.** Every touch of the source in `open` takes the source lock at the touch and reads the place
   under it: `open` of a copy served off the source, and `_serve`'s promotion off the source, each look
   the entry up again at its path (a new helper, `_lookup_again`: same path, id and kind as the cut's
   inline check) and open the object before releasing the lock. A copy served off a stage takes no source
   lock. `truncate` stays byte-identical; it already holds the lock when it calls `_serve`, and the lock
   is re-entrant (brief 01 § 1, § 5).
2. **Q1: hold the lock across the promotion copy**, not pin an opened stream. A POSIX stream survives a
   replay's `os.replace`, but the S3 and archive `open_read` buffer the whole object, so pinning would
   still hold the lock for the download; and pinning reshapes `transfer.copy`'s intent bracket for all
   its callers. Holding it is the shape Geoffrey signed off for `truncate` (brief 01 § 3).
3. **Lock order holds.** Nothing reached under the lock takes a path lock, calls `_ensure`, `getattr` or
   `readdir`, or takes the lock inside `db.transaction()` (brief 01 § 1).
4. **The serving decision stays under the path lock alone.** Under `open`'s path lock only a rename of an
   ancestor (a GOAL non-goal) can change the residency set; the unlocked stage branch never touches the
   source (brief 01 § 2). The round-trip consequences are recorded in
   `issues/freethreaded-concurrency-stress.md`.
5. **Keep path + id + kind, not a lookup by id.** By id would follow the entry through an ancestor rename
   but widen the identity gap to "a reused id anywhere" (brief 01 § 4).
6. **The baseline is worse than the records say.** The R4 swap, injected after the lookup, makes the
   baseline `open` hand back the other file's bytes (1000-byte files) and promote them into the file's
   stage copy, marked clean (small files). `AGENTS.md:669`'s "Loud, never a wrong object" and the GOAL's
   "found no case" are both wrong at the baseline (brief 01 § 6; reproduced by the prototype's tests).
7. **Records.** Count sites are four unit-count places, the repository map's total and the
   "164 engine tests" line; the second high-blast-radius table lacks `open` and `_serve` and gets them
   (brief 02 § 1). The new seed follows the repo's seed shape (brief 02 § 2).
8. **The retired verifier.** `spec/source-path-truncate-and-stat/verify/truncate_gate.py`'s `records`
   and `ledger` modes assert this seed and go red when it retires. Nothing runs that script (CI, lint and
   pytest's `testpaths` exclude `spec/`); it stays as a point-in-time record (brief 02 § 3).
9. **Gate.** A new `verify/open_gate.py` reuses the previous gate's helpers, suite, seeds, ledger,
   prepush and ci modes, with `BASELINE = 5e0d997` and `UNIT_FLOOR = 502` (`src/` and `tests/` are
   unchanged from there to the shaping commits) (brief 02 § 4).

## Contradictions resolved

- Brief 02 suggested `appetite: big` for the identity-token seed; adopted (a schema change with no
  migration path).
- Brief 01 suggested `Interleaved` would not fire on a mutant without the lock; the R1, R2 and R4 tests
  inject after `_ensure` instead, and the lock-shaped tests assert `ran`.
