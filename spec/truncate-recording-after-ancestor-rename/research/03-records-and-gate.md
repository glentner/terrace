# Research 03 — the records R7 names, and the precedent gates

Read-only, at `d6d2280` (its `src/` and `tests/` are `630a406`'s). Line numbers are at that tree. Design
assumed: Q1 "held off", with `rename` holding the source lock from `_vacate` through its catalog-move
transaction.

## 1. Record sites (R7)

| Site | Today | Under the design |
|---|---|---|
| `AGENTS.md:417-420`, § 5, the reading bullet's last sentences | "A rename of an ancestor that has already passed its `_vacate` when the cut takes the lock can still move the entry before the cut is recorded … Both are in § *Known open violations*." | Replace. An ancestor's `rename` holds the source lock from `_vacate` through its catalog move. So its move either committed before the cut's lookup under the lock, and the cut raises `ENOENT` having cut nothing, or it waits until the recording commits. Keep "raises `ENOENT`" scoped to the lookup under the lock, plus the stage open below. |
| `AGENTS.md:572-592`, § 10, the mutex bullet | The holders are "`_execute_namespace`, `_vacate`'s collect-and-replay, `_purge_source`, and a data commit or a `create` whose destination is the source" (:573-575). The delay list at :582-583 names "`rename` that reaches `_vacate`". | Add "a user `rename`, from its `_vacate` through its catalog move" to the holders. Also add what that hold delays: it is a short wait, a catalog transaction, but it waits behind a whole source-bound copy. The lock order, "nothing calls `locks.hold`/`_ensure`/`getattr`/`readdir`" and "not inside `db.transaction()`" all still hold if the `with` wraps the transaction (rename calls none of these between `_vacate` and the commit, `overlay.py:444-453`). |
| `AGENTS.md:682`, the § Known open violations row (`5, 7, 10`) | The whole recording row (`issues/truncate-recording-after-ancestor-rename.md`) | Drop it, or keep only what stays open, under a seed that outlives this one. |
| `overlay.py:748-751`, the `truncate` docstring | "A rename of an ancestor that has already passed its `_vacate` … the catalog writes, keyed by `path`, then match no row, while the stage-counter write, keyed by tier, lands." | Replace with the § 5 text. If R5 removes the guard, nothing in the docstring names it today, so nothing more changes. |
| `overlay.py:411-415`, the `rename` docstring | Three lines that say nothing about locks. | Add that it holds the source lock from `_vacate` through the catalog move, so a cut or replay holding it sees the subtree either before or after the move. Say whether the stage `tier.rename` loop (`:456-466`) is inside the lock. If it is not, a new cut at the *new* path in that gap opens the stage copy at a place it has not reached yet and raises `ENOENT` (the plan-gate ruling allows this). The docstrings must name that window: cycle 4's F1 below. |
| `overlay.py:94-102`, the `_source_lock` comment | The list of what it serializes. | Add rename's catalog move. Keep "`getattr`'s live stat reads … without it". Wrap the lines at 120 characters (cycle 4's F1). |
| `PLAN.md:554-556`, the risk header | "*`truncate` without a handle … stay open*" | Drop `truncate` from the list of what stays open. |
| `PLAN.md:595-599`, § Risks | "…and that cut keys its catalog writes by its path, so a rename of an ancestor already past its `_vacate` can leave those writes matching no row … (`issues/truncate-recording-after-ancestor-rename.md`)." | Remove the clause and the seed name. :569-579 (the holders and the delay list) gains rename's hold. |
| `issues/freethreaded-concurrency-stress.md:75-81`, "Path locks do not cover descendants" | The bullet, plus the dated 2026-11-08 line that pins its cites at `5e0d997`. | Q3's home: append a dated line naming `evict_copy`, `_set_meta` and every other call the plan finds that holds a path lock and writes the catalog by path. The bullet's `transfer.copy` `LookupError` now holds only for copies made outside the source lock. Say so. |
| `issues/freethreaded-concurrency-stress.md:129-138` (**not named by R7**) | "A user `rename` of the entry's directory that has already passed its `_vacate` moves the catalog without the source lock, so it can land between the two; `begin` then raises `LookupError`" | The design makes this false: the replay holds the lock from reading `entry.path` through `begin`. "Exactly what the code delivers" therefore needs a dated line saying it is closed and how (pinned by a test, or argued). |
| `AGENTS.md:70`, :111, :657, :700 | `521 tests`; `42 files, 531 tests (521 unit, 10 integration)`; `521 deselected`; `521 unit tests` | 521+N, 531+N. |
| `AGENTS.md:136` (**not named by R7**) | "183 engine tests" | 183+N. This is a measured figure (§ Conventions), and `open_gate.py`'s `suite` asserts it. |
| `tests/engine/test_replay.py:34-38`, the module docstring | The `truncate` bullet. | Add the class "an ancestor's rename overlapping the cut". `open_gate` checked that the module docstring names its class. |
| `ROADMAP.md:33-43` | The seed's entry. | Leave the entry and the seed in place. `code terrace roadmap` retires both after landing (cycle 4 PLAN deviation row 6; `open_gate.py:700-708` allowed only the `**Seed:**` line). |
| `issues/truncate-recording-after-ancestor-rename.md:100-101` | The counter guard, "which holds at `6425a21`". | Retires with the seed. Nothing else outside `spec/` mentions `previous is not None`. |

**What names the seed outside `spec/`** (grep): `AGENTS.md:682`, `PLAN.md:599`, `ROADMAP.md:35`, and the
seed itself. No other `issues/` file names it.

**The high-blast-radius tables.** `rename` is in neither (`AGENTS.md:211-240`, `:242-249`). § Invariants
:209 says "The list only widens". Both precedent `records` modes went red unless both tables named every
`overlay.py` function the branch changed (`open_gate.py:663-672`, with `__init__` exempt for the comment).
Cycle 4 added `_lookup_again` to both. So add `rename` to the replay row (`:223`, the one carrying
`_vacate`) and to table 2's `overlay.py` list (`:246`), and drop no name.

## 2. The precedent gates

`spec/source-served-open-race/verify/open_gate.py` is the model. `truncate_gate.py` has the same skeleton and
adds `REVIEWED`/`REVIEWED2` engines and `EQUIVALENT` mutants. They are run as `uv run --no-project --python
3.15 python -I spec/<slug>/verify/<gate>.py MODE…`. The first red stops the run. Exit codes: 0 green, 1
red, 3 cannot-run.

- **regressions** (`:446-463`): the tree must be committed. `ast` checks that each named test is defined
  once, walks the tree and ends in `assert_converged(...)`, and that `assert_converged` still checks the
  queues, sizes and remount. Every function `test_replay.py` held at the base must be unchanged
  (`top_level` unparse). `test_model.py` and both `conftest.py` must be byte-identical. No added
  `xfail`/`skip`. Then `-m unit test_replay.py` on HEAD, all passing. Then the same file on `BASELINE`'s
  `src/` (`git archive BASELINE src | tar -x` into `.local/<gate>/`, first on `PYTHONPATH`, with
  `import sosfs` probed). Each fix test must fail there with its `BASELINE_CAUSE` regex. Each pin must
  pass.
- **mutants** (`:468-485`): for each `(label, file, ((old, new), …), tests)`, extract HEAD's `src/`. Each
  `old` anchor must occur exactly once, or the mutant is red. Text-replace it and run `test_replay.py`
  with the shadow first. The named tests must be `['failed']`, and others must pass (the file stays
  runnable). Every new test is named by some mutant. `truncate_gate` also re-checks that each recorded
  equivalent mutant's anchor still occurs once.
- **records** (`:630-746`): read structurally from committed text: `section`/`bullets`/`tables`, the
  docstrings via `ast.get_docstring`, `word()` boundaries, phrase greps for overclaims, the
  ROADMAP-only-names-the-seed check, the count consistency (`unit_counts` across the four sites; map
  total = unit + integration; one engine count), and `cites_hold` (an unpinned `file:line` in a touched
  seed must still point at the same text as at the base).
- **suite** (`:751-769`): `uv sync --locked`, then `CI=true tools/lint.sh`, then `pytest -n auto -m unit`
  under `CI=true`. Requires 0 failed and 0 errors, passed > floor, passed + skipped == the stated count,
  the engine share == the stated engine count, and `test_model` and every named test passed.
- **seeds** (`:797-847`): see § 3.
- **ledger** (`:868-884`): checks the TECH phase bodies, from `## Phase P1` to `## How code-build`. Any
  block matching `DEFERRAL` (`:214`) must name `issues/x.md`. That file must exist and be in `ROADMAP.md`.
  `LEDGER_SEEDS` must be reached.
- **prepush** (`:887-893`): a clean tree, then suite, then records, then no `HOME_PATH` in the branch diff
  or the commit messages.
- **ci** (`:936-1001`): see § 3.

The common machinery:

- **pytest calls.** Every pytest call (`:334-340`) is `uv run --frozen pytest -p no:cacheprovider -rfEs -o
  junit_family=xunit1 --junitxml=…`. Counts come only from the JUnit XML.
- **Process groups.** `run()` (`:254-273`) starts each child with `start_new_session=True` and a 300 s
  timeout. On expiry it sends `os.killpg(SIGKILL)` → Red. It drops `PYTHONPATH` from the inherited
  environment.
- **Footprint** (cycle 4 TECH:143): `regressions`+`mutants` 27 s (about 1 s per mutant); `records` about
  2 s; `suite` 27 s (about 1.5 GB peak under `-n auto`); P1's whole verify 58 s; `seeds` 4 min 36 s.
  Scratch goes under the gitignored `.local/<gate>/`. Nothing is deleted.

**Lessons for this gate:**

1. **Line length** (cycle 4 F1, HIGH). `tools/lint.sh` runs only ruff `E9,F63,F7,F82` (`lint.sh:40`), so
   a 145-character comment passed. Three lines over 120 already exist at the base (`tier/archive.py:175`,
   `tests/engine/test_drain.py:53`, `tests/engine/test_metadata.py:112`). So the gate must check only the
   `+` lines of `git diff -U0 base -- src tests`, not whole files.
2. **Records claim exactly what the code delivers** (cycle 4 F1 cycle 2; cycle 3 F1 ×3). Each raised error
   must be scoped to the window that raises it: `ENOENT` from the lookup under the lock (and the stage
   open, if that window stays). The docstrings, the `_source_lock` comment and the test module docstring
   must say no more than § 5. Use "not", never "never", where a gap such as the reused id remains. Open
   windows go to a homed seed. Both precedents grep `OVERCLAIMS` across live records.
3. **`.hypothesis/` left behind.** Hypothesis 6.155.7 writes `.hypothesis/constants/` even under `CI=true`
   (its database is off, but `constants_ast.py:213` caches through `storage_directory`).
   `-p no:cacheprovider` does not stop it. Set `HYPOTHESIS_STORAGE_DIRECTORY` to a scratch directory
   (`configuration.py:65`) for every pytest run the gate makes, or move the directory aside after. (A
   `.hypothesis/constants/` dated 22:00 today sits in `$WT` now, from a concurrent worker's run. I left
   it alone.)
4. **Ledger vocabulary.** `DEFERRAL` matches "deferred", "out of scope", "stays open" and so on, but not
   "owned by", "home" or "homed". This GOAL defers with "Home:", so a TECH line that homes work without
   saying "deferred" escapes the ledger. Add `owned by|\bhomed?\b|\bhome:` and exempt "home-directory".
   Cycle 3 P3 went red twice on the ledger flagging TECH's own new lines (`TECH.md:488-490`): word
   records lines with that in mind.
5. **Run record fields.** Cycle 3 P3 attempt 3 was red because the saved `run.json` lacked
   `event`/`headBranch` (`TECH.md:495-502`). The foreman must save exactly `RUN_FIELDS`.
6. **Survivors** (cycle 3 F3). Behaviour-changing mutants survived the full suite. Mutate every decision,
   and record a mutant as "equivalent" only after you have argued it. E1 was "not strictly equivalent".
7. **Two-thread tests and hangs.** A mutant that hangs a two-thread test is not "killed": `run()` raises
   Red on the 300 s group kill. Give each helper a bounded `join`/`Event.wait` that fails the test.
   Probe "rename is held off" with a non-blocking `acquire` (the `lock_probe` precedent,
   `test_replay.py:715-733`), never by joining a thread that waits on a lock the test holds.
8. **Stale verifiers.** The older verifiers' `records` modes assert the recording row and seed. They go
   red after this cycle (`open_gate.py:682-683`, `RECORDING_SEED` at `:80`). Precedent (cycle 4
   PLAN:251-253): leave them as they are, since CI, lint and `testpaths` all exclude `spec/`.

## 3. How cycle 4 met R8-like criteria

- **CI=true on macOS.** The gate's `suite` mode was run in the sandbox with `UV_*` dirs in scratch and
  `allowed_domains` for PyPI (lint's `uvx ruff` and `uv sync`). Result: 521 passed in about 27 s. The
  reviewer re-ran the profile verify independently: 521 passed in 26.73 s / 26.52 s. Its first attempt
  failed when the sandbox refused the ruff fetch.
- **20 seeds** (`open_gate.py:779-847`).
  - Setup: two scratch trees from `git archive HEAD`, each with its own `UV_PROJECT_ENVIRONMENT`. Every
    Hypothesis CI variable is dropped, and `is_in_ci()` must print `False`.
  - Per seed: `.hypothesis/` is moved aside before each seed, then the run is `uv run --frozen pytest -p
    no:cacheprovider -q -o junit_suite_name=<token> --junitxml=… tests/engine/test_model.py
    --hypothesis-seed=N`. Odd and even seeds run in two lanes, with 300 s per seed.
  - Teardown: `killpg(SIGTERM)` in `finally`. The `pgrep` check is skipped when the sandbox blocks it
    (`:845-847`).
  - Timing: 4 min 36 s for the full run, about 23.5 to 26.5 s per seed serially (REVIEW:37-38, 161-163).
    The reviewer's own check was the plain loop with `CI` unset and `.hypothesis/` moved aside.
- **CI read** (`:936-1001`):
  - Lookup: `gh run list --branch B --commit SHA --event push --workflow ci.yml --json
    databaseId,headSha`. Take the newest matching run, then `gh run view ID --json
    databaseId,headSha,headBranch,event,status,conclusion,jobs`.
  - Logs: the gate reads them with `gh run view ID --job JOBID --log`, ANSI stripped, or offline through
    `--run-json`/`--log-dir`, which is the pattern P3 Run B used: the foreman saves
    `.local/ci/run.json` and `.local/ci/logs/<job id>.log`.
  - Checks:
    - `ci.yml` is `main`'s.
    - `headSha`, `event` and `headBranch` match, and `status` is `completed`.
    - All four jobs concluded `success`.
    - On Linux, step `Assert libfuse loads` concluded success, and the single pytest summary line shows
      passed == the stated count with 0 skipped.
    - On macOS, passed + skipped == Linux, every `SKIPPED [n]` line is under `tests/fuse` and names
      libfuse, and those skips total ≥ 44.
    - Commits after the pushed SHA touch only `spec/<slug>/`.
  - Cycle 4's record: run 42507324207, 521 / 521 / 477+44.
  - P3 was split into Run A (`ledger prepush`, commit, the foreman pushes) and Run B (`ledger records ci`
    on the saved record).
