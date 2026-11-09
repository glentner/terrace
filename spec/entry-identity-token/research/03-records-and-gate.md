# Research 03 — the records (R8) and the gate (R9)

Read-only, at `5893e1f`. Its `src/`, `tests/`, `AGENTS.md`, `PLAN.md` and `ROADMAP.md` are byte-identical
to `c7d2049`, which is also the merge base with `origin/main`. So `BASELINE` and "the base" are one commit.
Line numbers are at that tree. The design assumed is the planner's purge watch. Lab: `$S/c6plan-lab-03`.

## 1. Records the watch makes untrue

| Site | Today | Must say instead |
|---|---|---|
| `AGENTS.md:401-406`, § 5 | "(`_lookup_again`: path, id and kind, raising `ENOENT` when it fails) … short of the reused id below" | It checks path, id, kind, and whether the call's watch saw that id purged. Drop "short of the reused id below". |
| `AGENTS.md:409-418`, § 5 | The cut decides "the entry at its path (path and id)". Then: "The id check cannot tell the entry from a file that took its id after a purge … such a file carried onto the path is cut, and `open` would open or promote it the same way; the reused id is in § *Known open violations*." | Name the in-process purge watch. Give the outcome per call and per window (see the window list below). Drop the pointer to the violations table. |
| `AGENTS.md:554-612`, § 10 | `:601-602`: "Lock order: **path locks → source mutex → `_handles_mutex` → SQLite write lock.**" | Append the watch mutex after the SQLite write lock. Add one bullet: the watch mutex is a leaf, nothing is acquired while it is held, and it *is* taken inside `db.transaction()` (by `_purge`). Name its holders: `_purge`, a watch's open, reset and close, and `_lookup_again`'s check. |
| `AGENTS.md:580-607`, the source-mutex bullet | — | Its holders do not change, because `_vacate`'s purge already runs under the mutex (`overlay.py:1258-1267`). That purge used to be an autocommit `DELETE`. It now runs in a `BEGIN IMMEDIATE` transaction: the same SQLite write lock, opened under the source mutex, which the order allows. "Nothing acquires it inside `db.transaction()`" stays true. No `_vacate` caller is inside a transaction (`:329`, `:351`, `:449`), and `db.transaction()` cannot nest (`db.py:133`). So `_purge` must join the caller's transaction and never open one. `_vacate` wraps its own call. |
| `AGENTS.md:211-238`, table 1 | `:223` (replay row, with `_remove`, `_vacate`, `_execute_namespace_locked`); `:224` (`open`/`_serve`/`_lookup_again`); `:216` (`catalog.py` — `holder`, `advance_source_path`) | Add `_purge` to `:223`. Add the naming helper, and the watch type if it lives in `overlay.py`, to `:224`, with the watch in its Why. Add `purge` to `:216`. Drop no name (`:209`: "the list only widens"). |
| `AGENTS.md:243-250`, table 2 | `:246` (the `overlay.py` list); `:247` (the `catalog.py` names) | The same additions. |
| `AGENTS.md:696`, § Known open violations | The whole `5, 10` row, ending "Closing it needs an identity token, a schema change." | Drop it. What stays open is a different window or a different identity. That is the stage-served `open` window below and the Q5 list, and both are homed in the stress seed. |
| `overlay.py:94-103`, the `_source_lock` comment | "Order: path locks, then this, then `_handles_mutex`, then the SQLite write lock." | Append the watch mutex. Give the new mutex its own comment. Declare it **after** `self._source_lock = threading.RLock()`: the cycle-5 `init_comment` helper reads the comment block directly above that line. |
| `truncate` docstring `:743-765` | `:752-754`: "So does a non-file that has taken the entry's id. The id check cannot tell … (ids are reused, and only a schema change could tell them apart), and such a file carried onto `path` is cut." | The cut also raises ENOENT on a file that took the entry's id after a purge. Drop the "only a schema change" clause. Keep every other sentence: Geoffrey's signed-off property. |
| `open` docstring `:627-645` | `:637-640`: "…raises ENOENT when the path then holds no entry, another entry, or a non-file. The id check cannot tell … is opened." | Add "or a file that took the entry's id after a purge". Keep `:640-645`, scoped (the window list below). |
| `_serve` docstring `:672-686` | `:678-680`: the same pair of sentences: "…is promoted." | The same change. Keep the stage-promotion sentence. |
| `_lookup_again` docstring `:703-710` | "It tells the entry apart by path, id and kind only: a file that took the entry's id after a purge … passes." | It checks path, id, kind and the watch. It still reads only the catalog and the watch, never `_ensure`. |
| `catalog.py:166-168`, `purge` | "Hard-delete an entry (…cascade)." | Add that it returns the purged id, or None. |
| `tests/engine/test_replay.py:45-52`, the module docstring | `:48-49`: "short of a file that took the entry's id after a purge (ids are reused) and was carried onto the path" | The lookup under the lock refuses that file too. Add the class the new tests guard. |
| `PLAN.md:583` | "(the entry at its path, by path, id and kind, …)" | Add "and no purge of its id since". |
| `PLAN.md:594-598`, § Risks | "Still open: … tell their entry from another only by path, id and kind, so a file that took the entry's id … is cut, opened or promoted (`issues/entry-identity-token.md`)." | Move it to the closed list, scoped by window. Remove the seed path. `:606-608` (queue-row identity by value) stays true. |
| `issues/freethreaded-concurrency-stress.md:122-125`, the reused-id note | It is about queue rows and is still true. | Add a nested dated line naming this slug: Q5's home (§ 2). Give it a "holds at `<sha>`" pin if it cites `file:line`. |
| Counts | `AGENTS.md:70` "530 tests"; `:111` "42 files, 540 tests (530 `unit`, 10 `integration`)"; `:672` "530 deselected"; `:714` "530 unit tests" | +N at each site. **Also `:136`, "192 engine tests"**, which R8 does not list but which cycle 5's `suite` asserts. Change "42 files" only if a test file is added. A new `tests/state` test raises the unit count but not the engine count. Measured at base: 530/540 collected, 192 under `tests/engine`. |

**The window list.** Each record above must give the outcome for each window, never one blanket outcome
(cycle 5's F1; cycle 4's review-2 F1). The watch opens before the first lookup and covers only the calls
that hold one.

- **The cut.** Every cut takes the source lock. The watch therefore refuses the new file in every window
  after `_ensure`, whether the new file is served off the source or off a stage. The cut is closed in full.
- **A source-served open, and an open that promotes off the source.**
  - The route lands after `_serve` decides the source: ENOENT.
  - The route lands between `_ensure` and `_serve`'s `fastest_copy` read (`:688`), and the new file's
    fastest copy is on the source: `_serve` decides the source, and the lookup under the lock still
    refuses it. The watch is open by then.
  - The route lands in that same window and the new file's fastest copy is on a stage: `open` serves that
    stage copy, or promotes from it, with no lookup under the lock. It acts on the new file. This stays
    open. It is the Non-goals' "ancestor's round trip" class, homed in the stress seed (`:131-153`).
    Neither `open`'s nor `_serve`'s docstring may say "never acts on a file that took the id" unscoped.
- **A rolled-back purge.** `_purge` adds the id before the transaction commits. A transaction that then
  rolls back leaves an id in the watch for an entry that still exists, and the call refuses it. That is
  an exception path only, but no record should say a refusal happens "only" when the named file's entry
  was purged.

**Other hits** (`git grep`, excluding `spec/` and `.security/`). Some hits stay true:

- They are about queue rows, not entry ids: `AGENTS.md:215`, `AGENTS.md:247`, `PLAN.md:217`,
  `journal.py:213`, `journal.py:231`, `test_model.py:76`, `test_replay.py:26`, `:508` and `:526`.
- They are frozen test text that stays true (the id is reused and refused): `test_replay.py:1186`, `:1202`,
  `:1219`, `:1227`, `:1613`, `:1629`, `:1061` and `:1603`.
- `issues/archive-journaling-and-locking.md:177` is an unrelated "schema change".

Two hits are retired by `code terrace roadmap` after landing, not in this cycle: `ROADMAP.md:33-43`, whose
`:42-43` ("needs an identity token, a schema change") becomes false, and `issues/entry-identity-token.md`
itself. Outside `spec/`, the seed's path appears at `AGENTS.md:696`, `PLAN.md:598` and `ROADMAP.md:35`. The
first two must go in this cycle. No other `issues/` file names the seed.

## 2. Q5: lookups by entry id after a wait

The watch lives from before `_ensure` until the call returns. Only the handle-less `truncate` and `open`
(with `_serve` beneath them) hold one. Nothing that runs after `open` returns is covered. The table says
whether the watch closes each lookup.

| Site | What | Closed? |
|---|---|---|
| `overlay.py:775`, `:781-783`, `:784` | the cut's lookup, its check, its `_serve` | **Closed** |
| `overlay.py:649`, `:660` | `open`'s lookup and its lookup under the lock | **Closed** for the source-served branch; the stage branch stays open (window list above) |
| `overlay.py:696` | `_serve`'s promotion off the source | **Closed** when it is given the caller's watch |
| `overlay.py:92`, `:664`, `:819-823` | `_handles` keyed by entry id; `release` removes by object identity | Not applicable: registration happens after the checked lookup |
| `overlay.py:737`, `_mark_handle_dirty` | `by_id(handle.entry_id)` on a handle's first write | **Open.** A handle on an entry purged while open (`_remove` purges a file with no source copy at `:534`) marks the new file's path dirty once its id is reused. |
| `overlay.py:836`, `:843`, `_record` | `release`/`fsync` read `by_id` | **Open.** It records the new entry's stat and residency through the old handle's tier. |
| `overlay.py:291`, `getattr` | `_handles.get(entry.id)` | **Open.** The new file can report a live size through the old handle's tier. |
| `overlay.py:927`, `_commit_hop` | re-arms handles by id | **Open**, benign: re-marks dirty |
| `overlay.py:1293`, `evict_copy` | refuses while a handle is open by id | **Open**, conservative: it only refuses an eviction |
| `overlay.py:885`, `commit_row`; `:1018`, `flush`; `drain.py:64`, `dirty_copies` | a queued data row's `row.entry`, read by id after the snapshot | **Open.** The old row cascades on purge, but a stale snapshot maps it to the new file, which it commits. Argued, not run. |
| `overlay.py:1043`, `:1053`, `:1201`, `:1224` | namespace rows under the source lock | Not applicable: `journal.current(row)` is identity by value; a cascaded row does nothing |
| `overlay.py:549`, `:1128`, `:1230` | `by_id(entry.id)` inside one source-lock hold or transaction | Not applicable: no wait for a lock in between |
| Intent rows | `journal.begin` resolves the entry by path at `begin`; `confirm` deletes by intent id | Not applicable (intent-id reuse; confirm-before-purge, `AGENTS.md:175`) |
| `fuse/adapter.py:56` | keyed by `fh` | Not applicable |

For the stress seed's dated line, name the following as open: handles keyed by entry id (`_handles`,
`Handle.entry_id`), with `_mark_handle_dirty`, `_record`, `getattr`, `_commit_hop` and `evict_copy`; and
data-row snapshots, with `commit_row`, `flush` and `dirty_copies`.

## 3. The gate

### `cut_gate.py`, summarized

- **Behaviour.** Modes run in order, and the first red stops the run. Exit 0 is green, 1 red, 3
  cannot-run. `run()` gives each child its own session and calls `killpg(SIGKILL)` at `RUN_TIMEOUT` = 300 s.
  The docstring carries a footprint header: one subprocess at a time, two for `seeds`, and a peak of
  1.5 GB under `-n auto`.
- **How pytest runs.** Counts come from JUnit XML (`-o junit_family=xunit1`), never from the exit code.
  Every run gets `HYPOTHESIS_STORAGE_DIRECTORY` in its own scratch directory and `-p no:cacheprovider`.
  Scratch goes to `.local/cut-gate/`.
- **The baseline engine.** `git archive BASELINE src` is extracted and put on `PYTHONPATH`, and an
  `import sosfs` probe checks that it is the one loaded.
- **`regressions`.** Each new test is defined once and ends in `assert_converged`, directly or through a
  helper. `WAITING` asserts that its stop fired. Every top-level `test_replay.py` function and class is
  unchanged since the base (`unparse`). `FROZEN` (the model and both conftests) is byte-identical. No
  added skip or xfail (`TOLERANCE`). `truncate_kept` requires `truncate` to be the base's code but for one
  guard. `catalog_moves_held` checks an AST pin of the rename's hold. Then the file runs on HEAD, all
  passing. Each `FIX_TESTS` test fails on the baseline matching `BASELINE_CAUSE[name]`, and each pin
  passes there.
- **`mutants`.** `MUTANTS` holds `(label, ((file, exact anchor, replacement), …), tests, why)`, where
  `why` is a regex or a per-test dict (`REMOVED_CAUSE` differs from `BASELINE_CAUSE` where HEAD fails
  louder). Each anchor must occur exactly once. Every new test must be named by some mutant. Each kill
  must match its regex. Equivalent mutants are argued in a comment.
- **`records`.**
  - Section and bullet regexes for § 5 and § 10.
  - The `STALE` phrases are absent from § 5, § 10 and the docstrings.
  - Both tables keep every old name and name every changed `overlay.py` function (`__init__` exempt).
  - The violation row is dropped, and the `TOKEN_SEED` row is required to remain. **This one must be
    inverted.**
  - Positive docstring regexes, and an overclaim regex (`never raises ENOENT`).
  - `git grep 'issues/' -- src tests` is empty, and no `issues/` path appears in the `truncate` docstring.
  - The `__init__` comment, the module docstring, and the PLAN.md and ROADMAP.md `**Seed:**`-only rule.
  - The stress seed's dated lines (`SAME_SHAPE` names).
  - Added lines in `src/` and `tests/` are ≤ 120 characters and carry no `SPEC_ID`.
  - Counts: `unit_counts` gives one value, the map total equals unit plus integration, and there is one
    engine count.
  - `cites_hold`: in seeds touched since `plan_commit~1`, every unpinned `file:line` cite of a changed
    `.py` file must still match its base text. `PIN` is "holds at `<sha>`", per `cite_blocks` block.
- **`suite`.** Under `CI=true`: lint, `-n auto -m unit`, passed > `UNIT_FLOOR`, passed plus skipped equals
  the stated count, and the engine share equals `:136`.
- **`seeds`.** Two `git archive HEAD` trees, each with its own venv and storage, and no CI variables. It
  calls `pgrep`, which the sandbox blocks; the check is skipped unless the return code is 0 or 1.
- **`ledger`.** `DEFERRAL` wording in TECH phase bodies must name a seed that `ROADMAP.md` lists, and every
  `LEDGER_SEEDS` entry must be reached.
- **`prepush`.** A clean tree, then `suite` and `records`, then `HOME_PATH` over the three-dot diff and the
  commit messages.
- **`ci`.** From `--run-json`/`--log-dir` or `gh`: all four `CI_JOBS` succeed, Linux passed equals the
  stated count with 0 skips, the macOS skips are all `tests/fuse`/libfuse and number at least
  `FUSE_UNIT_FLOOR` = 44, and `ci.yml` equals main's.

### `identity_gate.py`: keep, drop, change

- **Keep:** the skeleton, exit codes, JUnit counting, `run()` with its process-group kill, the footprint
  header, per-run Hypothesis storage, `extract_src` with its import probe, `unchanged_code`,
  `regression_shape`, the `MUTANTS` shape with per-kill cause regexes and argued equivalents,
  `catalog_moves_held` (cycle 5's property, a constraint here), the `git grep 'issues/'` check, the
  120-character and `SPEC_ID` checks, `suite`, `seeds`, `ledger`, `prepush`, and `ci` unchanged.
  Set `BASELINE` and the base to `c7d2049`, `UNIT_FLOOR = 530`.
- **Drop:** `truncate_kept`'s exact-equality pin; the "identity-gap row still stands" check; `SAME_SHAPE`;
  the rename-specific § 5/§ 10 regexes.
- **New `frozen_schema` (R5):** add `src/sosfs/state/schema.sql` and `src/sosfs/state/db.py` to `FROZEN`.
  `ast` reads `SCHEMA_VERSION` and it must equal the base's.
- **New `purges_watched`:**
  - `src/` holds exactly one `catalog.purge` call, inside `OverlayEngine._purge`, which uses its return.
  - `DELETE FROM entry` appears once in `src/`, with `RETURNING id`. Nothing in `src/` does `REPLACE INTO`
    or `INSERT OR REPLACE` into `entry`.
  - `_purge` is called from exactly the base's purge functions: `_remove`, `_execute_namespace_locked`
    twice, and `_vacate`. Each call sits lexically inside a `with self.db.transaction():`.
  - `_purge` itself opens no transaction. It asserts `in_transaction`, or the gate checks for the absence
    of a `transaction` call.
  - The `:1142` (rename tombstone) and `:1267` sites purge ids no call can have named. A mutant there is
    equivalent: argue it, and let this static check guard those sites.
- **New `watch_leaf`:**
  - The watch mutex is a `threading.Lock()`, not an `RLock`.
  - Each `with <watch mutex>:` body contains no `with` and makes no call on `self.db`, `self.catalog`,
    `self.journal`, `self.locks`, `_source_lock`, `_handles_mutex` or `_ensure`. Allow only set and list
    operations.
  - No `with self._source_lock` appears lexically inside a `with self.db.transaction()` in `overlay.py`.
  - In `truncate` and `open`, the watch opens before the first `_ensure` call. It is released in a
    `finally` or a context manager. Make the new tests assert the engine's watch list is empty afterwards:
    a leaked watch grows with every purge.
  - Every `_lookup_again` and `_serve` call passes the watch.
- **New `truncate_kept`:** take the base `truncate`'s statements from `serving = self._serve(` through the
  recording, with the `_serve` watch argument normalized away. They must equal HEAD's, which pins
  "nothing else in it changes". Allow only the lookup prologue to differ.
- **Mutants (sketch):**
  - The watch check is removed from `_lookup_again`: kills R1 (both stacks), R2 and R3. Its cause is the
    baseline's: `DID NOT RAISE`, or a bytes assertion.
  - `open`/`_serve` get no watch while the cut keeps its own: kills R2 and R3.
  - A refusal on any non-empty watch: kills R4's pins (a tombstone purge by `flush`, a `create`).
  - `catalog.purge` returns `None`: kills everything R1–R3 names.
  - Purge-site bypasses at whichever of `:534` and `:1121` the tests' routes use. Make one test purge
    through the replay (a source-held file, flushed) and one through `_remove` (a stage-only file).
  - Opening the watch after `_ensure`, and adding to it after commit: equivalent under single-thread
    injection; argue them.
- **`records`, per window:**
  - Required in § 5, in the `truncate`, `open`, `_serve` and `_lookup_again` docstrings and in the module
    docstring: words for the watch, such as `purge` near `id`.
  - Absent everywhere: `STALE` = (`cannot tell the entry from a file that took`, `only a schema change`,
    `short of the reused id`, `short of a file that took`, `Closing it needs an identity token`). Check § 5,
    § 10, the four docstrings, the module docstring, root `PLAN.md` and the violations table.
  - In § 5 and in the `open` and `_serve` docstrings, the stage-branch scoping must remain ("off a stage
    … without the source lock"). Forbid an unscoped `never (?:opens|promotes|acts on)[^.]{0,60}took`.
  - In § 10: the order string ends with the watch mutex, a bullet calls it a leaf, and the `__init__`
    comment carries the same order.
  - Both tables name `_purge`, the naming helper and `purge`. Restrict `changed_functions` to
    `OverlayEngine` methods (plus `__init__`), so a new watch class's methods don't force table entries;
    or name the class.
  - No violation row names the seed.
  - The stress seed gets a dated line under the bullet that begins "Queue-row identity is by value". It
    names this slug and every Q5 name in § 2 (a `Q5_NAMES` tuple).
  - Never require a seed path in `src/` or `tests/` text (Geoffrey's 2026-11-09 directive).
- **`cites_hold` lessons:**
  - The stress seed's `overlay.py` and `catalog.py` cites are all pinned. That was checked with
    `cite_blocks` and `PIN`: `:53`, `:425`, `catalog.py:170` and others.
  - `issues/engine-error-mapping.md:17` cites `overlay.py:74-76` **unpinned**. A module-level watch class or
    an import added above line 74 shifts it. A review seed note on that seed would then turn the check red,
    as in cycle 5.
  - So either declare the watch type below `OverlayEngine`'s docstring (`threading` is already imported at
    `:17`), or pin that cite in the plan commit.
  - Have the check print base and HEAD text for each failing cite, and require every dated line this
    cycle adds that cites `file:line` to carry its own "holds at `<sha>`".
