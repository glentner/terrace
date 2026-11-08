# Research 02 — records (R6) and gate (R7)

Read at `58151ec` (branch `fix/source-served-open-race`; merge base with `origin/main` is `5e0d997`).

## 1. R6 record sites

**(a) Files outside `spec/` naming `issues/source-served-open-race.md`** (grep, complete):

- `AGENTS.md:667` (identity-gap row, Seed column) and `AGENTS.md:669` (`open`/`_serve` row).
- `PLAN.md:592` ("…during the wait is cut (both `issues/source-served-open-race.md`)").
- `ROADMAP.md:35` (`**Seed:**` line of the first Queued entry, heading at :31).
- `issues/truncate-recording-after-ancestor-rename.md:98` ("Related: `issues/source-served-open-race.md` (the same cycle's other deferral, on the same function);").
- The seed itself names only its slug (`status: adopted:source-served-open-race`, :2), not its path.

`ROADMAP.md:35` and the seed file go when `code terrace roadmap` retires the seed after landing, so an in-branch "no file names it" check must exclude those two, or the plan must retire them in-cycle. Also stale after retirement: `ROADMAP.md` truncate-recording entry, last sentence: "Placed beside the `open` seed, which works in the same function."

**(b) Test counts** (no count in `README.md`):

- `AGENTS.md:70` `uv run pytest -m unit   # 502 tests, ~30 s — must pass before every commit`
- `AGENTS.md:111` `| tests/ | 42 files, 512 tests (502 `unit`, 10 `integration`). …` (42 = all `tests/**/*.py`, 35 are `test_*.py`)
- `AGENTS.md:136` "…directly, by 164 engine tests with no mount." (engine count; previous cycle updated it)
- `AGENTS.md:643` "(10 skipped, 502 deselected on a host with no `/dev/fuse` and no MinIO)"
- `AGENTS.md:687` "**502 unit tests in ~30 s serial, no faster under `-n auto`**"

The old gate's `unit_counts` regexes (`(\d+) unit tests`, `\((\d+) \`unit\``, `pytest -m unit\s+# (\d+) tests`, `(\d+) deselected`) read exactly :70, :111, :643, :687 and require one value; `:111`'s total must equal unit + integration.

**(c) Sentences the change makes false or incomplete:**

- `AGENTS.md:395` (§ 5): "Reading the source resolves through it too: `open` serves a source-held copy from `source_path`, …" — says nothing about lock; the paragraph's lock text covers only `_record` ("`_record` reads the entry again under the source lock (§ 10), so a replay cannot move the object between the read and the stat.") and the handle-less `truncate`. Needs a sentence for `open` and promotion off the source.
- `AGENTS.md:564-575` (§ 10 mutex bullet): "serializes every change to the source's namespace: `_execute_namespace`, `_vacate`'s collect-and-replay, `_purge_source`, and a data commit or a `create` whose destination is the source (placement, copy and recording). `_record` takes it to stat a copy served off the source, and `truncate` without a handle takes it for every cut, …" — must add `open`/promotion as takers. Delay sentences to extend if the promotion holds it for the copy: "It spans the whole copy, so a large commit to the source delays every `create`, `mkdir` and `rename` that reaches `_vacate`, the `release` or `fsync` of a copy served off the source, and every handle-less `truncate`, until it finishes. A handle-less `truncate` that promotes holds it in turn for that whole-file copy (as large as the stage admits), and delays the same calls." Also "Nothing holding it calls `locks.hold`, `_ensure`, `getattr` or `readdir`" constrains the design (lock must be taken after `_ensure`).
- `AGENTS.md:594-596` (§ 11): "**Promotion runs on the calling thread, under the file lock.** A promote-on-open of a large file holds the path lock for the whole copy. That is intended — synchronous whole-file promote — but it means `open` latency is a copy." — incomplete if a promotion off the source also holds the source mutex (that delays every other path's `create`/`mkdir`/`rename`, scheduler replay, `release`/`fsync` of source-served copies).
- `AGENTS.md:669` row — drop (or reduce to what stays open).
- `AGENTS.md:224` high-blast-radius table 1 already names `open`, `_serve`. Table 2 (`AGENTS.md:246`) does **not** name `open` or `_serve`; the old gate's records check required every changed `overlay.py` function in **both** tables, so a reused check forces adding them to :246.
- `src/sosfs/engine/overlay.py:620-626` `open` docstring (no lock statement; "promoting it synchronously (whole-file) … then hand back a passthrough handle on the serving tier") and the inline comment `:637-638` "Served directly off the source (no faster tier admitted), the bytes may sit at the committed source path rather than the logical one during a deferred rename."
- `overlay.py:652` `_serve` docstring: "The tier to serve `entry` from, promoting first when a faster one admits it." plus comment `:659-660` "Promoting off the source reads where it physically holds the bytes, which lags the logical path while a rename is queued but uncommitted." Note `truncate` calls `_serve(current)` under the lock at `:727`.
- `overlay.py:94-100` `_source_lock` comment: "Serializes every change to the source's namespace (a replayed op, a vacate's replay, a purge, and a data commit or a create whose destination is the source, placement through recording), and the stat behind recording a copy served off the source, …" — omits truncate already; add open/promotion.
- `tests/engine/test_replay.py:4-46` module docstring lists guarded classes; nothing on `open`/promotion under a replay. Add a bullet (the truncate bullet at :33-38 is the model).
- `PLAN.md:569-576` § Risks: "The mutex is held for a whole copy to the source, `_record` takes it to stat a copy served off the source, and a handle-less `truncate` takes it for every cut, so a large source commit delays … and a handle-less `truncate` that promotes holds it for that copy in turn" — add open/promotion. `PLAN.md:587-590`: "Still open: … and `open` and a promotion off the source read the committed source path outside the mutex, so a replay landing in between fails the call with `ENOENT` on a file that exists, and the handle-less `truncate` tells its entry … (both `issues/source-served-open-race.md`)" — split: drop the `open` clause, repoint the identity clause. Heading at `PLAN.md:556-557` "*(open in part: … `truncate` without a handle, a removal racing a replayed `mkdir`, and the thread-level test stay open)*" — judge whether still accurate. `PLAN.md:165-167` (Read path, "promotes on open (synchronously, whole-file)") and `:218-220` ("Reading the source resolves the same way: the size reported …") are not false, optionally extendable.
- `ROADMAP.md:31-41` entry — removed at retirement.

## 2. Identity-gap text and seed/roadmap format

Every sentence to carry into `issues/entry-identity-token.md`:

- `AGENTS.md:667`: "The handle-less `truncate` tells its entry from another only by path, id and kind, and ids are reused. If, while the cut waits for the source lock, the entry is removed and purged, a new file elsewhere takes its id, and a directory rename carries that file onto the path, the call cuts the new file and returns success. Silent; reproduced by injection. A symbolic link or a directory that takes the id is refused with `ENOENT`. Closing it needs an identity token, a schema change." (Seed column → repoint.)
- `AGENTS.md:405-407` (§ 5): "The id check cannot tell the entry from a file that took its id after a purge, since ids are reused; such a file carried onto the path is cut." … "Both are in § *Known open violations*." (no seed named; stays true).
- `overlay.py:705-707` truncate docstring: "The id check cannot tell the entry from a file that has taken its id after a purge (ids are reused, and only a schema change could tell them apart), and such a file carried onto `path` is cut."
- `PLAN.md:590-592`: "and the handle-less `truncate` tells its entry from another only by path, id and kind, so a file that took the entry's id after a purge and was carried onto the path during the wait is cut (both `issues/source-served-open-race.md`)".
- Seed `issues/source-served-open-race.md`, paragraph "A residual gap in that cycle's identity check, reproduced by injection. …" (full repro: stage `bypass_size=100`, 1000-byte `data/big.bin`; rename `/data`→`/moved`, unlink, flush (purge frees id), create 500-byte `/moved/big.bin` (takes id), flush, rename back; call returns success, cuts it to 4 bytes; history at `c3f31ff` and `ea38083`; symlink/directory now refused, `O_NOFOLLOW`). Plus its R4 and "Why it was deferred".
- `GOAL.md:53-59` (this cycle's Problem).

**Seed format** (both `truncate-recording-after-ancestor-rename.md` and `source-commit-staged-publish.md`): frontmatter exactly `status:`, `kind:`, `appetite:`, `lane: public` (no other keys); `# Title`; the repo's short candidate blockquote ("> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and / > that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means / > none of that has happened yet."); then `## Problem`, `## Why it was deferred`, `## Outcome / vision`, `## Sketch of the acceptance criteria` (often opened "Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes."), `## Notes` (Related; "Found by: …"). KB template `$KB/skill/_templates/ISSUE.md` agrees (status enum `unshaped|shaped|adopted:{slug}|declined|accepted-behaviour`; `lane:` only with the security lane on, which it is). New seed: `status: unshaped`, `kind: fix`, appetite per planner (schema change; `SCHEMA_VERSION` has no migration path, `AGENTS.md:245`, suggests `big`).

**ROADMAP entry format:** `### <title>` / blank / `**Seed:** [\`issues/<slug>.md\`](issues/<slug>.md) · \`<kind>\` · appetite <small|big>` / blank / one paragraph (origin, date, mechanism, evidence, why placed here). Entries are unnumbered and cross-reference by slug. Placement: the Queued list now runs source-served-open-race (retiring), truncate-recording-after-ancestor-rename, model-commit-route-reach, rename-to-terrace, … . The natural slot is directly after `truncate-recording-after-ancestor-rename` (same function, same truncate decision) and before `model-commit-route-reach`; reword that entry's "Placed beside the `open` seed" sentence accordingly.

## 3. The retired verifier and `issues/source-served-open-race.md`

`truncate_gate.py` asserts about it (`WINDOW_SEED`, :80):
- `records`: :719-720 one § 5 row names the identity gap **and `WINDOW_SEED`**; :721-724 reads `WINDOW_SEED`'s text (raises `FileNotFoundError`, an uncaught crash, if reached after deletion); :727-728 one § 5 row names the `open`/`_serve` window and `WINDOW_SEED`; :750 `PLAN.md` names `WINDOW_SEED`; :763-764 it exists and `ROADMAP.md` lists it.
- `ledger`: :895-899 each seed a `TECH.md` deferral block names exists and `ROADMAP.md` lists it (the old `TECH.md` names it at :182, :187, :231, :439, …); :901-902 the ledger must reach `source-served-open-race`.

So `records` and `ledger` go red once the seed retires and the rows repoint. Acceptable: it is a retired cycle's point-in-time record, keyed to that branch (BASELINE `0a88f30`, REVIEWED `aec3676`/`ea38083`, and `records` diffs against `merge-base origin/main HEAD`, so `changed_functions` is already empty — red — on any tree whose base is past `6425a21`, including this branch today). Nothing runs it: `.github/workflows/ci.yml` runs only `uv run pytest -n auto -m unit -rfEs` (Linux, macOS), `uvx ruff check --select E9,F63,F7,F82 src tests` and `uv build`; `tools/lint.sh` scans `src tests` only (ruff, layering, SPDX, version, symlink); `pyproject.toml:60` `testpaths = ["tests"]`, and the file is not `test_*.py`. Do not edit it.

## 4. Gate reuse

**Generic, reusable verbatim** (change only `LOCAL`/token names): `Red`, `CannotRun`, `say`, `check`, `scratch`, `run`, `git`, `base`, `plan_commit`, `Case`/`Report`/`junit`, `pytest`, `no_red`, `extract_src`, `with_engine`, `names_in`, `section`, `bullets`, `tables`, `changed_functions`, `word`, `unit_counts`, `stated_unit_count`, `DATED`/`cite_blocks`/`cites_hold`, `CI_VARS`/`SEED_TIMEOUT`/`seed_tree`/`mode_seeds`, `blocks`, `mode_prepush` (+`HOME_PATH`), `summary`/`pushed_sha`/`gh_json`/`mode_ci` and its constants, `main`/`MODES`/`OPTIONS`. `mode_suite` is generic except its `NEW_TESTS + (EARLIER_SIZE_TEST,)` loop. `regression_shape`, `frozen_files`, `mode_regressions`, `mode_mutants` are generic in shape but driven by slug tables. `mode_ledger` is generic except its phase delimiters (`## Phase P1` … `## How \`code-build\``) and the required-reach tuple at :901.

**Slug-specific:** `SLUG`, `BASELINE`, `UNIT_FLOOR`, `FROZEN`, `OWNER`, `WINDOW_SEED`, `OWN_SEED`, `RECORDING_SEED`, `OVERCLAIMS`, all test-name tables (`FIX_TESTS`, `PIN_TESTS`, `CONCURRENT`, `CYCLE2_FIX`, …), `REVIEWED*`/`*_CAUSE`, `BASELINE_CAUSE`, the anchor strings (`LOCKED`, `DECIDED`, `GUARD`, `SERVED`, …, `HELD`), `MUTANTS`, `EQUIVALENT`, `LOCAL = .local/truncate-gate`, the seeds token prefix, and all of `mode_records`' body.

**BASELINE:** `5e0d997` (the GOAL's tree, and the merge base). `git diff --stat 6425a21 HEAD -- src tests` is empty, and `5e0d997..58151ec` touches only `issues/source-served-open-race.md` and this spec, so `src/` and `tests/` are identical at `6425a21`, `5e0d997` and HEAD. `UNIT_FLOOR = 502`. `FROZEN` keeps `tests/engine/test_model.py` (GOAL constraint); keeping both conftests frozen is a plan choice (old Q2-derived). New tests' baseline causes: `FileNotFoundError` (R1) and `TierError: No file at` (R2), per GOAL evidence. `assert_converged` is at `tests/engine/test_replay.py:181`.

**CI jobs** (`ci.yml`): `Unit (Linux, 3.15)`, `Unit (Linux, 3.15t)` (matrix `unit`, step `Assert libfuse loads`), `Unit (macOS, engine-only)`, `Lint & build` — matches `CI_JOBS`. `FUSE_UNIT_FLOOR = 44` holds: `tests/fuse/test_adapter.py` 27 + `test_mount.py` 17 unit tests, no parametrize; `tests/fuse/conftest.py:34-38` skips them all with the libfuse reason; previous runs logged "44 skipped" (`spec/source-path-truncate-and-stat/REVIEW.md:48`, :240). Holds unless this cycle adds `tests/fuse` tests.

## 5. Timing (previous gate)

Docstring :53-56 and `TECH.md:147-150` (dev Mac, 2026-11-06): `regressions` + `mutants` (eleven serial `test_replay` runs) + `records` together 10 s; P1's whole verify 41 s (suite pytest 29 s); `seeds` 4 min 46 s for 20 seeds on two workers (per-seed timeout 300 s; a passing seed ~30 s). Later: review-cycle-1 `regressions mutants records seeds` 318 s (`TECH.md:406`); `seeds` "about 5 minutes" (:412); `-m unit` under `CI=true` 26.5-26.6 s, serial 28.7-29.7 s (:384, :413, :419). `suite`/`prepush` use `-n auto` (~1.5 GB peak); other runs serial, < 500 MB. `seeds` needs the 10-minute tool cap; run in the foreground.
