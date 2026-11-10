# Research digest — model-commit-route-reach

Trigger 2 fired: `tests/engine/test_model.py` is on § Invariants' high-blast-radius list, so the plan ran a
fan-out despite `appetite: small`. Two read-only briefs, plus the planner's own measurements (PLAN § 4):

- [`01-hypothesis-draw.md`](01-hypothesis-draw.md): how Hypothesis 6.155.7 derives and draws the fixed CI sample.
- [`02-commit-routes.md`](02-commit-routes.md): every route into `OverlayEngine._commit_hop`, and whether the
  census attributes and counts them as the GOAL defines.

## Decisions

1. **Which draw is "the fixed CI draw".** Not one: Hypothesis feeds int (|v| ≥ 100) and bytes literals of the
   loaded local modules into 5% of its draws (01 § 1), and the full suite loads more of `src/` than a standalone
   run of the model. R1, R3, R4 and R6 are graded on the **standalone** draw, `pytest tests/engine/test_model.py`
   under `CI=true` on macOS, for the base and the final tree alike; R8's suite runs its own draw and is graded on
   pass and counts only. The census adds no constant to the pool (no qualifying literal, and it imports only
   `sosfs.engine.overlay`, which the model loads anyway); the gate checks both.
2. **Composites, one rule.** Rule choice is swarm-uniform over enabled rules (01 § 2): k rules that must
   co-occur are all enabled in about 1/(k+1) examples, and each added rule dilutes the rest by R/(R+1). So the
   reach is built inside one rule (the whole sequence in one step), and one rule rather than three. Measured
   (PLAN § 4): three rules cut the direct route's seed mean from 39 to 23; one rule raised it to 56.
3. **The scheduler route needs two polls.** In the three-tier stack a write's first poll commits only tmp →
   scratch; the scratch → project row is born then, so the rename must come after that poll, and the second poll
   at that row's deadline commits it while the rename row (queued later, same 60 s delay) is not yet due
   (02 § 5). `poll_next_due` alone cannot produce that order.
4. **Attribution is sound, with three refinements** (02 §§ 2–3): `commit_row` reaches a hop only for data rows, and no
   namespace replay reaches one, so "commit_row means scheduler" holds. The census also records each count's
   outermost model rule and whether a `WritebackScheduler.poll` is on the stack (*polled*), because the
   literal scheduler route also counts the model's direct `commit_row` calls (`commit_scanned`,
   `rename_scan_unlink_commit`); the scheduler floor is met by polled commits alone. *Lagging* is
   `(source_path or _target(path)) != path`, so a commit under a renamed parent with no source copy of its own
   counts, and a queued rename with no lag (a directory not yet on the source, a round trip) does not.
5. **Reads are inert.** The census's reads are plain SELECTs and `_target` reads only the catalog; none ticks
   the model's clock, which the census asserts on every call (02 § 4).
6. **Rule names steer nothing new.** The slot Hypothesis gives the rule at sort index 0 stays with
   `commit_scanned`; the new rule, `write_rename_commit_through`, sorts after it (01 § 2).
7. **Seeds.** `--hypothesis-seed=N` without CI runs with no database; every run still writes a
   `.hypothesis/constants/` cache, so each seed run sets `HYPOTHESIS_STORAGE_DIRECTORY` to its own scratch and the
   tree is checked for `.hypothesis/` before each run (01 § 3).
8. **No-shrink runs.** A profile parented on `ci` loaded from a `-p` plugin keeps `derandomize` and the module's
   four explicit settings (01 § 4), so a mutant run stops at its first falsifying example on the same draw.
