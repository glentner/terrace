# Research 03: extending the model (R7) without weakening it (R8); seeds and CI (R9, R10)

Every number below was measured in a lab built by `git archive` from HEAD. HEAD's `src/` and `tests/` match `141aa86`. The lab had its own venv (Hypothesis 6.155.7, Python 3.15.2), ran on macOS on 2026-11-05, and kept its probes outside the repo.

## 1. Rules

**commit_entry.** `@rule(path=FILE_PATHS)` calling `self.engine.commit_upstream(path)`. It has no oracle side. On an absent, directory or clean path it is a quiet no-op (`overlay.py:811-834`), so it needs no guard. Defect 1 reproduces in five steps: create, flush, overwrite, rename, commit. The source ends with both `a.dat` (old bytes) and `b.dat` (new bytes), and both queues are empty.

**Scheduler poll.** The machine holds one unstarted `WritebackScheduler(self.engine)`. An unstarted `StateThread` has `halting == False` (`fsm.py:86`), so `poll()` runs synchronously (`writeback.py:90`). Take the clock as `self.clock = self.engine.clock` and leave `test_model.py:74` unchanged, so `__init__` only grows.

In the 3-tier stack, both boundaries use `delay=60` (`spec.py:89`, `writeback.py:35`). A data row for tmp→scratch is due at `w+60`. Its hop queues scratch→project with `last_write = t1`, the time of that poll. A rename row is due at `r+60`.

Data overtakes the rename only when the rename lands after the first hop (`t1 < r`) and a poll falls in `[t1+60, r+60)`. That window is `r−t1` ticks wide. An older rename always wins, because its deadline is earlier and `due` sorts namespace rows first (`writeback.py:88`).

Scheduler-only reproduction:
1. poll at 175 (tmp→scratch hop)
2. rename at 176
3. poll at 235: commits the data (deadline 235) while the rename is due at 236

Result: the orphan. A uniform jump rarely lands in a 1-tick window, so use two rules:
- `poll_writeback(advance=st.integers(min_value=0, max_value=150))`: `clock.now += advance`, then `poll()`. This covers arbitrary jumps, including "now" and past both delays.
- `poll_next_due()`: `clock.now = max(now, min(deadline(row)) − clock.step)`, then `poll()`. `poll` ticks once, so exactly the earliest-due rows fire, in true scheduler order. Once the op sequence exists, the 1-tick window is certain.

**fsync (Q7, recommended).** `fsync_policy` is read only at `overlay.py:707`, and no existing rule calls `fsync`. Building with `dataclasses.replace(build_spec(root, tiers=3), fsync_policy='commit')` therefore changes no existing behaviour (precedent: `test_datapath.py:166`). The rule opens, writes, fsyncs and releases, under parity with an oracle `pwrite` plus `os.fsync`.

**Eviction (Q7).** The dirty variant is `(path=FILE_PATHS, tier=sampled_from(['scratch','tmp']))`: `commit_upstream` then `evict_copy`, mirroring `evict.py:151-152`. The clean variant is `evict_copy` alone, which returns False while dirty (`overlay.py:1018`).

Call these directly. Never use an `EvictionManager` with quotas. With `QuotaSpec()` defaults, `eligible_tier` always returns `tmp` (`overlay.py:183-197`). The next `open` or no-handle `truncate` promotes back through `_serve` from `source_path` (`:629-635`). Live stat (`:277`) and `truncate` (`:685`) then act on `tmp`, so the deferred siblings stay unreachable. Quotas on both stages would let `eligible_tier` fall through to the source (`:191`).

**Reachability (measured, important).** I added `commit_entry`, `poll_writeback`, `poll_next_due`, the drain rule, the invariant and the remount, at HEAD.

- Seeds 1, 2, 4, 5 and 6 still **passed in about 3 s** with every defect present. Seed 2 fails on the unextended model; added rules reshuffle what each seed draws.
- Seed 3 failed with **9 distinct failures** in 305 s. Each failure below shows its shortest shrunk sequence; three of the nine were not fully shrunk.
  - Defect 1 via the scheduler: create `/a`, rename to `/b`, drain, truncate `/b`, `poll_next_due`, rename `/b→/d1`, `poll_next_due`. Result: `unclaimed /b.dat`, and the drain snapshot shows `b.dat` where the oracle has `d1`.
  - The R2 directory form: mkdir `/d1`, drain, create `/d1/a.dat`, `poll_next_due`, rename `/d1→/d2`, drain. The drain raised `ENOTEMPTY` (`project/d1 → project/d2`), and the invariant reported `unclaimed /d2`.
  - The R6 class.
  - A `NotADirectoryError` rename replay.
  - Two shared-`source_path` collisions (§ 2).
  - The invariant false positive (§ 2): `unclaimed /d1, /d1/s1` after `commit_entry('/d1/s1/a.dat')`.

So the extended model finds all of this, but only about 1 seed in 6 does. Hypothesis has no rule weights, and the chains are 5-7 steps over 17 rules. Three ways to raise detection without narrowing anything:
- **Raise `max_examples`.** R8 forbids only lowering it, and a passing run costs about 3 s. For example, 300 examples is about 10 s.
- **Add compound rules**, for example `rewrite_rename_commit(old, new, data, route)`, all under parity.
- **Keep deterministic tests** in `test_replay.py` for each R1, R2, R5 and R6 route.

## 2. Drain convergence, remount, orphan invariant

**drain_and_compare.** Up to 200 times: stop if `queued()` is empty; otherwise jump to the next deadline (as `poll_next_due` does) and `poll()`. Then assert, in order:
1. `queued() == []`
2. `pending() == []`
3. `snapshot(source) == snapshot(oracle)`

Advance to the *earliest* deadline, not past all of them. That replays rows in deadline order, the order `flush` hides (`overlay.py:876-878`). Jumping past everything reproduces flush's namespace-first order inside one poll. The bound never binds on a correct engine, and if it does the assert fails loudly.

**Remount (R4).** This goes in `teardown`, after the existing flush and assert, so the baseline statements stay in place:
1. `stop()` the engine.
2. Start `OverlayEngine(spec, clock=self.clock)` on the same state. A fresh state dir trips the tier marker check (`overlay.py:121-127`).
3. Walk `readdir` from `/`, reading each file through `open`/`read`.
4. Compare the walk with `snapshot(oracle)`.

No rule lists `/` today, so the walk adopts root orphans, which is the seed's symptom. It added no measurable time.

**Invariant.** Two checks:
- Every source path except `seeded.txt` is the `source_path` of some catalog row. Read the rows with `engine.db.execute(t'SELECT source_path FROM entry WHERE source_path IS NOT NULL')`.
- No two rows share a `source_path`.

Exempt `/seeded.txt` by name. It is never adopted, because it is not in `PATHS` and nothing lists `/`. Do not use a clause like "unadopted means no catalog row": after the rename row purges the tombstone, a real orphan has no row either.

**This is unsound at step boundaries under the current code.** Probe: `mkdir /d1`, create `/d1/a.dat`, `commit_entry`. Here `materialize_dirs` (`transfer.py:29-39`) creates source `/d1` without a claim until the mkdir row runs (`overlay.py:961`). That is a legal transient, so the invariant would fail falsely. The model hit exactly this at seed 3.

It becomes sound if every source-side mkdir records `source_path` (the mkdir row, and `materialize_dirs` into the source) and commits write at the claimed path.

The shared-`source_path` half catches two failures the GOAL does not list. The model reaches both once `commit_entry` lands, so the plan must cover them or R9 and R10 go red:
- **Path reuse.** `rename /a→/b` (queued), create `/a`, `commit_upstream('/a')`, `flush`. The source ends `{'b.dat': NEW}`, the old bytes are destroyed, and both entries record `source_path='/b.dat'`, because `advance_source_path` rewrites every equal row (`catalog.py:188`). A Q2 "write at `source_path`" design still has to place a never-committed entry whose logical path is a queued rename's physical source.
- **Never-committed directory.** `mkdir /d1`, create `/d1/a.dat`, `rename /d1→/d2`, `commit_upstream('/d2/a.dat')`, `flush`. This raises `OSError(66, ENOTEMPTY)`: the mkdir row creates `/d1` at `op_path`, and the rename then hits a populated `/d2`.

## 3. R8 static gate

I prototyped and tested a gate of about 120 lines. It is AST-only and runs as `python -I gate.py <(git show 141aa86:tests/engine/test_model.py) tests/engine/test_model.py`. It checks:

- Every baseline rule still exists, with the same argument names. Each strategy resolves through module names and must be no narrower: `sampled_from` sets are supersets, and `binary`/`integers` bounds are equal or wider (None counts as wider).
- Rule bodies, `parity` and `real` are AST-identical.
- In `__init__` and `teardown`, the baseline's simple statements appear in order, each keyed by its chain of enclosing compound statements. Wrapping them in `if False:` or `try/except AssertionError: pass` therefore fails.
- There is exactly one `TestOverlayModel.settings`, with `max_examples ≥ 100` and `stateful_step_count ≥ 50`, and no new settings keys (`phases=`, `database=`, `derandomize=`).
- The count of `assume`, `precondition`, `reject`, `filter`, `skip*`, `xfail`, `register_profile` and `load_profile` does not grow.
- No method name is duplicated (a later def shadows the earlier one).
- After the class, the only module-level statements are the two `TestOverlayModel` lines (no monkeypatching).
- `snapshot` and `pytestmark` are unchanged. Dropping `mark.unit` silently removes the test from `verify`.

Results: HEAD passes, and so does the extended lab model. Six mutants were caught: narrowed `DATA`, early return in `quiesce_and_flush`, `parity` catching `Exception`, `phases=[]`, `assume`, and a guarded teardown. A legitimate new rule passes.

Gaps the gate does not cover yet:
- Edits to `Clock`, `seed` or `build_spec` in `tests/engine/conftest.py`. Fix: apply the same identity check to them.
- A new rule that reassigns `self.oracle`, `self.engine` or `self.root`, which resets depth. Fix: forbid such assignments outside `__init__`, `teardown` and the remount.
- New handlers that swallow errors with `except ...: pass`.
- Profiles loaded elsewhere. None exist today; grep the whole repo.
- Dilution, which R8 allows: each baseline rule's draw share drops from 1/12 toward 1/17. Prefer a few compound rules.

## 4. Seeds, CI, timings

| Run (`uv run --frozen pytest tests/engine/test_model.py -p no:cacheprovider`) | Result | Wall time |
|---|---|---|
| `--hypothesis-seed=1`, CI unset | pass | 3 s (9-35 ms per example) |
| seed 2 | fail | 58 s (shrinking) |
| seed 3 | pass | 2 s |
| `CI=true --hypothesis-seed=1` | fail: stray-dir class | 47 s |
| extended lab model, seeds 1, 2, 4, 5, 6 | pass | 3-4 s each |
| extended lab model, seed 3 | fail: 9 distinct failures | 306 s |

Hypothesis 6.155.7 behaves as follows:
- **Any `CI` value loads the `ci` profile, even `CI=false`** (`_settings.py:392-413, 1211-1223`). That profile sets `derandomize=True, database=None`. R10 needs `CI` unset.
- `derandomize` beats `--hypothesis-seed` (`core.py:715-719`; the docstring at `:362` refers to `@seed`). Under `CI=true` the seed is ignored, which the seed-1 pass and fail above confirm.
- The TestCase's explicit `settings` does not set `derandomize`, so the seed is honoured when `CI` is unset.
- With a seed, `database_key` is None (`core.py:1397-1403`), so the example DB is neither read nor written. Only `.hypothesis/constants/` and `.gitignore` appear, relative to the current directory (`configuration.py:20`, or `HYPOTHESIS_STORAGE_DIRECTORY`). No `examples/` appeared even after the failure.

**R10 protocol:**
1. `lab=$(mktemp -d)` and `git -C WT archive HEAD | tar -x -C $lab`: a committed tree with no `.hypothesis/`.
2. `UV_PROJECT_ENVIRONMENT=$lab.venv uv sync --frozen --offline`.
3. For each seed: `unset CI`, move any `.hypothesis` to a numbered scratch dir (never delete it), run, and record the pass count from the summary line.
4. Run four batches of five seeds, one at a time.

A passing seed takes about 3 s today; budget about 2× once the drain rule, remount and invariant land. A failing seed takes 1 to more than 5 minutes (extended-model shrinking, see seed 3), so wrap each pytest in `timeout 540`.

R9's `CI=true … -n auto -m unit` is one deterministic draw of the model. That is necessary but weak evidence.
