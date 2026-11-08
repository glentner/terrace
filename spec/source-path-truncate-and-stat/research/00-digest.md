# Research digest — source-path-truncate-and-stat

Two briefs, fanned out because `truncate` and `_stat_held` are on `AGENTS.md`'s high-blast-radius
list (code-plan trigger 2), scaled to a small appetite. Both briefs read HEAD `0a88f30` and both
prototyped the fix in a lab outside the repo.

## Decisions

1. **Resolve the place, keep the rename queued** (GOAL Q1). The cut opens `_held_path(entry, serving)`,
   the expression `_stat_held` already uses, factored into one helper that backs both. No new
   `_replay_through` caller, no `SCHEMA_VERSION` change ([01](01-truncate-contract.md) § 7).
2. **Take the source lock when serving is the source, and re-read the entry inside it.** Replay takes
   no path lock, so `source_path` can advance between `_ensure` and the open. Brief 01 reproduced the
   ENOENT this leaves without the re-read (§ 3). The lock order holds, and nothing forbidden runs
   under it (§ 2). `_ensure` and `_serve` stay outside, and `mark_dirty` stays before the cut (§ 1).
3. **Look the entry up again by path, check the id, and raise `ENOENT` on a mismatch.** Brief 01's
   hazard list notes that a directory rename can land while the cut waits, since a path lock covers its
   own path only. The first draft looked up by id and recorded on the entry's new path. The adversarial
   review showed that draft crashing on a purged entry and cutting a file that reused the id (PLAN
   § 4.1). The catalog writes keep the baseline's `path` keys. `mark_dirty` before the lock keys by the
   handed path; that is pre-existing and recorded.
4. **Fallback when `source_path` is `NULL`**: the logical path, as `open` and `_stat_held` do (01 § 4).
5. **The swap case needs nothing extra.** `_vacate` has already moved the other object, and no
   wrong-object sequence was found at the baseline (01 § 5).
6. **The `open` and `_serve` window is deferred.** Both read `source_path` outside the lock. A replay
   injected there makes the call fail loudly. It is pre-existing, threaded and unseeded. The plan files
   `issues/source-served-open-race.md` and a ROADMAP entry (01 § 6).
7. **Tests: 16, appended to `test_replay.py`, deterministic.** There are twelve *fix* tests, each
   failing at the baseline for its named cause, and four *pins*, which pass at the baseline: the staged
   control (which also pins § 1's order at the cut), the `_record` lock, and R2 for the file's own
   rename and an ancestor's. The pins' teeth are
   mutants. The concurrency tests use a one-call tier patch with a helper-thread lock probe, and a lock
   stand-in that runs `flush` or a `rename` to completion first ([02](02-regressions-and-gate.md) § 3).
8. **The model's CI draw is untouched.** It is a digest of `OverlayMachine`'s source only (02 § 5).
   `test_model.py` and both `conftest.py` files stay byte-identical.
9. **The gate is a fresh, smaller script modelled on the previous cycle's.** It keeps the baseline
   runs, mutants, seeds, ledger, prepush and CI-record modes, and drops the model half (02 § 4).

## Contradictions resolved

- Brief 02 counted 12 tests and four mutants. The design adds the three interleaved user-op tests,
  for decision 3 as amended by the review, and a `_record` lock pin, which brief 02 § 2 showed is
  testable. With the review's folds there are 16 tests and eleven mutants (PLAN § 6).
- Brief 02 § 6 noted that the old gate's count regexes miss `474 deselected`. The new gate reads that
  form too.
