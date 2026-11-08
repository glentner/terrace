# PLAN — `truncate` without a handle finds a source-served file during a queued rename

> **Status:** Draft for review · **Last updated:** 2026-11-06
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

The handle-less branch of `OverlayEngine.truncate` resolves the place where a tier holds the entry's
bytes the way `_stat_held` already does, through one small helper, `_held_path`. When the serving
tier is the source, the branch takes the source lock and looks the entry up again at its path inside
it. If the entry is no longer there, it raises `ENOENT`. Otherwise it cuts and records at the place
the source holds now, the shape `_record` already has. The queued rename stays queued (GOAL Q1, the
shaper's lean), so no new caller reaches `_replay_through`.

Sixteen deterministic tests in `tests/engine/test_replay.py` pin the fix, the lock, the lookup, § 1's
ordering, and last cycle's size behaviour through a `bypass_size` stage. The records move in the same
commit: `AGENTS.md` § 5 and § 10, both high-blast-radius tables, the open-violation rows, and the
counts; the repo's `PLAN.md` risk register; and a dated note in `issues/audit-command.md`. One code
region and three phases fit the small appetite.

## 2. Design

### 2.1 The cut (`src/sosfs/engine/overlay.py`)

`truncate(path, length, handle=None)`, no-handle branch. Every statement up to and including the
dirty mark stays byte-identical: the path lock, `_ensure`, the kind checks, `_serve`,
`upstream_of`, `now`, and `journal.mark_dirty` when the serving tier has an upstream (§ 1, metadata
before acknowledgment). For the source, `upstream_of` is `None` and nothing is marked. Then:

```python
on_source = serving == self.source.name
with self._source_lock if on_source else contextlib.nullcontext():
    current = self.catalog.get(path)
    if current is None or current.id != entry.id:
        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)
    entry = current
    fd = self.tiers[serving].open_direct(_pure(self._held_path(entry, serving)), os.O_WRONLY)
    try:
        os.ftruncate(fd, length)
    finally:
        os.close(fd)
    previous = self.catalog.get_residency(path, serving)        # the catalog writes keep the baseline's text
    with self.db.transaction():
        self.catalog.update_meta(path, size=length, mtime=now)
        self.catalog.update_residency(path, serving, size=length, mtime=now)
        if previous is not None and not on_source:
            self.registry.adjust_usage(serving, bytes=length - previous.size)
```

- **Where.** `_held_path(entry, tier)` returns `entry.source_path` for the source when it is set, and
  `entry.path` otherwise: exactly the expression `_stat_held` holds today. `_stat_held` becomes
  `self.tiers[tier].stat(_pure(self._held_path(entry, tier)))`, so one rule backs both calls. `open`
  keeps its own inline expression. It tests `source_path` for truth, not for `None`, which differs only
  on `''`, and `normalize` never yields that. Moving `open` onto the helper would widen the diff into a
  path this cycle need not change.
- **Under which lock.** The source lock is taken only when serving is the source, after `_serve` and
  inside the path lock. The order is path locks, then the source mutex, then `_handles_mutex`, then the
  SQLite write lock. Nothing under it calls `locks.hold`, `_ensure`, `getattr` or `readdir`, and it is
  never taken inside `db.transaction()` (§ 10). `_ensure` and `_serve` stay outside it.
- **From which lookup.** Replay takes no path lock. A scheduler poll, `flush`, `drain` or another
  path's `_vacate` can commit the queued rename and advance `source_path` between `_ensure` and the
  open. Looking the entry up again inside the lock closes that window; brief 01 § 3 reproduced the
  ENOENT without it. The second lookup is **by path, checked against the id**, never by id alone. A
  path lock covers its own path, not the ancestors. So while the cut waits for the lock, an
  ancestor's rename can take the entry off `path`. A removal and a flush can purge it, and a new file
  can then take its id (adversarial review, § 4.1). Each of those raises `ENOENT`. That is an
  outcome POSIX allows for a call racing a rename or an unlink, and it is what the baseline did.
  Same-path replacement needs the path lock, which the cut holds: `unlink`, `create` and a `rename`
  onto `path` all take it.
- **Which rows.** The catalog writes key by `path`, unchanged from the baseline. The identity check
  makes `path` the entry's current path.
- **Unchanged.** The handle branch (`ftruncate` on the handle's fd, already at the right place since
  `open` resolved it), `open`, `_serve`, `_record`, and the queue. No schema change and no
  `SCHEMA_VERSION` bump.

The queued swap (`rename /a.bin → /t.bin`, `rename /b.bin → /a.bin`) needs nothing extra. The second
rename's `_vacate` has already moved `t`'s object to `/t.bin`, and `/a.bin`'s `source_path` is
`/b.bin`. The cut lands on `b`'s own object. Brief 01 § 5 found no sequence in which the logical path
held another entry's object, so the defect at the baseline is ENOENT, not a cut of the wrong object.

### 2.2 Tests (`tests/engine/test_replay.py`, appended at the end)

The tests are appended after the last existing test, so every existing `test_replay.py:N` cite in
`issues/` stays valid. They get a section header in the module's house style and a module-docstring
bullet for the new class. Three helpers come with them:

- `source_served(tmp_path, clock, files, tiers=2)` seeds the files, adds a stage with
  `bypass_size=100` when `tiers > 1`, starts the engine, and walks it.
- `assert_cut(engine, path, original, length)` checks `getattr`'s size, and that a fresh handle reads
  `original` cut to `length` or zero-padded to it.
- `Interleaved` stands in for `engine._source_lock`. The first time the test's thread acquires it, a
  helper thread runs an action to completion first. It re-raises any exception that action raised,
  and it sets `ran` only on success.

Every test walks first and ends in `assert_converged`, which keeps its size comparison. No test sleeps
or sets a timeout.

| Test | Stack | Asserts | Kind |
|---|---|---|---|
| `test_truncate_without_a_handle_behind_its_own_rename` | 2 tiers, 1000 B | cut to 4 at `big.bin` before flush; source counter unchanged; R1 reads | fix |
| `test_truncate_without_a_handle_grows_behind_its_own_rename` | 2 tiers | grow to 2000, zero padding | fix |
| `test_truncate_without_a_handle_inside_a_renamed_directory` | 2 tiers | cut at `data/big.bin` | fix |
| `test_truncate_without_a_handle_of_an_adopted_file_inside_a_renamed_directory` | 1 tier | cut to 0 | fix |
| `test_truncate_without_a_handle_of_a_file_created_inside_a_renamed_directory` | 1 tier | create, write, cut to 0 | fix |
| `test_truncate_without_a_handle_of_a_committed_file_behind_its_own_rename` | 1 tier | the Problem table's sixth row | fix |
| `test_truncate_without_a_handle_after_a_queued_swap` | 2 tiers | `b`'s object cut, `t.bin` untouched | fix |
| `test_truncate_without_a_handle_cuts_under_the_source_lock` | 2 tiers | a helper thread cannot take the lock at `open_direct`, and can after the call | fix |
| `test_truncate_without_a_handle_resolves_inside_the_source_lock` | 2 tiers | `flush` lands before the lock; cut at the new place | fix |
| `test_truncate_without_a_handle_after_a_concurrent_rename_moves_the_file_away` | 2 tiers | `rename /data → /moved` lands before the lock: `ENOENT`, nothing cut | fix |
| `test_truncate_without_a_handle_after_a_concurrent_removal` | 2 tiers | rename, unlink, flush land: `ENOENT`, not a crash (§ 11) | fix |
| `test_truncate_without_a_handle_leaves_a_file_that_reused_the_entry_id_alone` | 2 tiers | a new file takes the id (asserted): `ENOENT`, its 500 bytes kept | fix |
| `test_truncate_without_a_handle_of_a_staged_copy_inside_a_renamed_directory` | 2 tiers, 5 B | stage-served control: at the cut the residency is already dirty and still 5 B (§ 1); stage counter nets 2 | pin |
| `test_release_of_a_source_served_copy_stats_under_the_source_lock` | 2 tiers | `_record`'s stat holds the lock | pin |
| `test_size_through_a_bypass_stage_behind_its_own_rename` | 2 tiers | R2: open, fsync, release, flush | pin |
| `test_size_through_a_bypass_stage_inside_a_renamed_directory` | 2 tiers | R2, ancestor | pin |

*Fix* tests fail at the baseline, and the gate names each one's cause. The cause is
`FileNotFoundError`; for the three interleaved user ops it is `DID NOT RAISE`, because the baseline
takes no lock and the interleaving never fires. *Pins* pass at the baseline by design (R2 says the
behaviour already holds), so their teeth are the mutants (§ 6). A fresh-handle read after a shrink
below `bypass_size` promotes the file to the stage. `assert_converged` compares sizes as a set over
every residency, so the promotion is covered, not dodged (brief 02 § 1).

The suite grows from 474 to 490 unit tests. `tests/engine/test_model.py` and both `conftest.py` files
are untouched (GOAL Q2). The fixed CI draw is a digest of `OverlayMachine`'s source alone, and brief
02 § 5 measured it unchanged with `test_replay.py` and `overlay.py` edited.

### 2.3 Records (same commit as the fix)

- **`AGENTS.md` § 5**, the "Reading the source resolves through it too" bullet: `truncate` without a
  handle cuts a source-held copy there. `_record` and `truncate` resolve the place (`_held_path`) from
  the entry read again under the source lock. `truncate` reads it again at its path, so an entry taken
  off the path raises `ENOENT` and a file that took its id is never cut.
- **§ 10**, the source-mutex bullet: a handle-less `truncate` is a taker, beside `_record`, and one of
  the calls a long source commit delays.
- **Both high-blast-radius tables**: add `_held_path` to the row that holds `_stat_held`, and rewrite
  the long table's "why" to cover cutting as well as opening and stating. `truncate` and `_stat_held`
  are already in both. Nothing is removed, so the list only widens.
- **§ Known open violations**: the § 5 row no longer names `truncate` or this cycle's seed. It becomes
  the old-sizes row owned by `issues/audit-command.md` (R4). A new `| 5, 10 |` row names the
  `open`/`_serve` window and `issues/source-served-open-race.md`, the seed this plan files.
- **Counts**: 474 → 490 at all four places (`# 474 tests`, the repository map's `484 tests (474
  `unit`…)` → `500 (490 …)`, `474 deselected`, and § Verification's figure). P1's suite run re-measures
  the ~28 s figure.
- **Repo `PLAN.md` § Risks**, the namespace-replay entry:
  - the handle-less `truncate` becomes a mutex taker and is listed as closed;
  - "Still open" names `issues/audit-command.md` and `issues/source-served-open-race.md`, not this
    cycle's seed;
  - the injection sentence admits a helper thread.
- **`issues/audit-command.md`**: a dated note with no `file:line` cites, carrying the GOAL's probe
  facts:
  - which fields can be wrong, and on which copies;
  - which fields heal and which do not;
  - that the staged-tier quota counters are unaffected, correcting the earlier note's overstatement.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | § 2.1: cut at `_held_path`, under the source lock, from the entry looked up again at its path; the seven R1/swap tests, plus the five lock and lookup tests (§ 2.2) |
| R2   | `_stat_held` through `_held_path`, behaviour unchanged; the two `test_size_through_a_bypass_stage_*` pins and the `_record` lock pin; mutants M7, M8 |
| R3   | every new test ends in `assert_converged` (paths, kinds, bytes, sizes from `getattr` and from every residency, remount) |
| R4   | § 2.3: audit seed note, § Known open violations rows; gate `records` |
| R5   | gate `suite` (P1, P2, P3 prepush) |
| R6   | gate `seeds` (P2) |
| R7   | P3 split run; gate `ci` grades the saved run record |

## 3. Invariant gate (AGENTS.md constitution check)

Checked before research (from the GOAL) and again against this design, after the adversarial review's
folds.

- **§ 1, metadata before acknowledgment.** `truncate` is on that row of the table. The dirty mark still
  commits before `ftruncate` on a staged copy. On the source there is no upstream and nothing to mark,
  as before. The catalog transaction still follows the cut. The staged control pins the order at the
  cut: the residency is already dirty there and still 5 bytes. Mutant M11, which moves the mark after
  the cut, turns it red.
- **§ 1, intent → act → confirm.** Not engaged. A user truncate is not an engine-initiated mutation of
  a managed location, as at the baseline.
- **§ 5, committed source path.** Honoured and widened: one more physical touch of the source resolves
  through `source_path`. No deferred rename is forced, and `_replay_through` gains no caller, so its
  § 5 enumeration stays true.
- **§ 10, concurrency.** The new taker respects the lock order and every "nothing holding it" rule
  (brief 01 § 2). The mutex stays re-entrant, and nothing nests a path lock inside it. The cost: a long
  source commit now also delays a handle-less `truncate` of a source-served file. The § 10 text says so
  in the same commit. Mutant M9, which takes the lock and never releases it, is caught by the
  lock-probe test.
- **§ 11.** Failures stay `OSError`. An entry removed, purged or moved off `path` while the cut waits
  raises `FileNotFoundError`, never `AttributeError` on a missing row. The concurrent-removal test pins
  this.
- **High-blast-radius paths.** `truncate`, `_stat_held` and the new `_held_path` sit in both tables,
  and `tests/engine/test_replay.py` sits in the long one. The review's human gate applies (GOAL Q4).
- **§ Conventions.** Same-commit records (§ 2.3). No spec ids in `src/` or `tests/`. Every test module
  keeps its `pytestmark`. Commit subjects follow the profile.

### Deviation justifications

Nothing here overturns a sentence of § 1, § 5 or § 10. Each row adds a little complexity, and each is
read against the R-IDs it touches.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| A new helper, `_held_path`, joins both high-blast-radius tables | One rule for "where a tier holds the bytes" backs `_stat_held` and the cut | Inlining the expression a third time leaves `open`, `_stat_held` and `truncate` free to drift. Touches R1 and R2. R2's behaviour is the old expression byte for byte, and mutant M7 shows its pin still bites. `open`'s truth test differs only on `''`, which is never stored |
| The handle-less cut on the source takes the source lock (a new § 10 taker) | Without it, a replay can move the object between the lookup and the open: ENOENT, or a stale size recorded | Leaving it unlocked meets R1's single-threaded sequences but keeps the race `_record` was given the lock to close (§ 10). Touches only R1, which it strengthens. The cost is that a long source commit delays the call (§ 10 text) |
| The entry is looked up again at `path` under the lock, checked against its id, and the call raises `ENOENT` when it no longer matches | A lookup by id alone, the first draft, crashed on a purged entry and cut an unrelated file that reused the id (review finding 1, reproduced) | Skipping the second lookup keeps a stale `source_path` (brief 01 § 3). Looking up by id alone breaks § 11 and destroys another file's bytes. Touches R1's no-raise clause only when another thread removes or moves the file mid-call, which R1's single-threaded sequences never do |
| Tests swap `engine._source_lock` for a stand-in, and patch tier methods for one call | The only deterministic way to land a replay, a rename or a removal inside the window without sleeps | A threaded stress test is nondeterministic and belongs to `issues/freethreaded-concurrency-stress.md`. `unlink_during_next` already patches tier methods in this file |

R-ID read-through:

- R1's four sequences, its shrink and grow, and its `getattr` and fresh-read clauses are met as
  worded.
- R2's behaviour is untouched.
- R3's "nothing left at a pre-rename path" is met by `assert_converged`'s source-equals-catalog check
  after `flush()`.
- R4 is met as worded, with the owner the GOAL names.

No row bends an R-ID, and none needs a GOAL clarification. `_record` still looks its entry up again by
id alone, and that stays benign: it stats and records whichever entry holds the id, on that entry's own
object and row, and it cuts nothing.

## 4. Rabbit holes (resolved)

Research ran the full fan-out, because `truncate` and `_stat_held` are high-blast-radius (trigger 2).
It was scaled to two briefs. Digest: [`research/00-digest.md`](research/00-digest.md).

- *Is a second lookup needed, or does the path lock suffice?* It is needed. Replay takes no path lock,
  and `advance_source_path` rewrites descendants. Reproduced by injection
  ([`research/01-truncate-contract.md`](research/01-truncate-contract.md) § 3).
- *Is a source lock inside the path lock safe for lock order?* Yes. It is the documented order, and
  nothing forbidden runs under it (brief 01 § 2).
- *Is the baseline a wrong-object hazard?* No sequence found; the baseline gives ENOENT only (brief
  01 § 5). The first draft's lookup by id introduced one, which § 4.1 removed.
- *Other source touches at a logical path.* All resolved or documented safe, except `open` and
  `_serve`, which read `source_path` outside the lock. That window is loud, threaded and pre-existing.
  It is deferred to `issues/source-served-open-race.md`, filed by this plan with a ROADMAP entry
  (brief 01 § 6).
- *Can the lock and the lookup be tested deterministically?* Yes, in two ways. A one-call tier patch
  with a helper-thread lock probe, or a lock stand-in that runs `flush`, a rename or a removal first.
  Neither can deadlock, because replay takes no path lock and the stand-in's action runs before the
  test's thread takes the source lock
  ([`research/02-regressions-and-gate.md`](research/02-regressions-and-gate.md) § 3). The `_record`
  lock is testable the same way (brief 02 § 2), so it gets a pin.
- *Does any of this redraw the model's fixed CI sample?* No. It is a digest of `OverlayMachine` alone
  (brief 02 § 5).

### 4.1 Adversarial review

One fresh reviewer took the GOAL, this design, TECH, the drafted gate and the built prototype. It ran
its experiments in an export of the prototype. Each finding, and how it was folded:

1. **HIGH, reproduced: the first draft's lookup by id could crash or cut another file.** A rename of
   `/data` plus `unlink` plus `flush` inside the wait purged the entry, and `catalog.by_id` returned
   `None`: an `AttributeError`, breaking § 11. Adding a `create` reused the id, and the call cut an
   unrelated 500-byte file to 4 bytes. *Folded:* the second lookup is by path with an id check (§ 2.1).
   Three tests now cover it: rename-away, removal, and id reuse with the reuse asserted. Mutant M6
   restores the lookup by id. The § 3 claim and the deviation row are rewritten.
2. **MEDIUM: a lock that is never released passed every test.** *Folded:* the lock-probe test also
   asserts that a helper thread can take the lock after the call. Mutant M9.
3. **MEDIUM: adjusting the source's quota counter passed every test.** *Folded:* the own-rename test
   asserts that the source counter is unchanged. The staged control asserts that the stage counter
   nets 2 bytes. Mutant M10.
4. **MEDIUM: § 1 ordering is pinnable single-threaded, though the draft said it was not.** *Folded:*
   the staged control probes the stage tier's `open_direct`. At the cut, the residency is already dirty
   and still 5 bytes. Mutant M11 moves the mark after the cut. § 3 and § 5 are amended.
5. **MEDIUM-LOW: `records` read the working copy.** *Folded:* `records` first asserts that `AGENTS.md`,
   `PLAN.md`, `ROADMAP.md`, `issues/`, `src/` and `tests/` are committed. It goes red on a dirty
   `PLAN.md`, and that self-test was run.
6. **LOW: any backticked SHA counted as a pin, and the plan's own seed escaped the cite check.**
   *Folded:* a pin must read "holds at `<sha>`" (or is/are/pinned at). The touched set starts at the
   plan commit's parent. Self-tests were run: an unpinned drifted cite went red, a pinned one green.
7. **LOW: `Interleaved` set `ran` before its action ran, so a helper exception went unseen.** *Folded:*
   `ran` is set after a clean join, and the action's exception is re-raised in the test's thread.
8. **LOW: the open-violation check did not prove the old claim gone.** *Folded:* no § 5 row may contain
   "`truncate` without a handle". Self-test run: red.
9. **LOW: "one rule, two users" overstated `open`'s parity.** *Folded:* the deviation row now states
   the `''` difference.

What the reviewer checked and found holding: the residency and meta writes are each killed by six or
more tests. The mutant anchors are unique. `assert_cut` cannot mask the source's bytes, because each R1
test asserts the source tree first. The baseline-cause rule holds, a missed count site goes red, and
R1–R4 are met as worded.

Dry runs on the prototype, a detached worktree cut back per phase:

- **P1** (`regressions mutants records suite`): green in 42 s on the folded tree. The baseline fails
  12 of the 16 new tests for their named causes and passes the 4 pins. All 11 mutants are killed.
- **P2** (`seeds`): green in 4 min 46 s on the pre-fold P1 tree. Re-run on the folded tree together
  with P1's modes, it went green again: 5 min 28 s for all five modes, 490 passed.
- **P3** (`ledger prepush`): green. `ci` stops at CannotRun with no saved record, as designed. On a
  synthetic record, labelled as such and kept in the prototype's `.local/`, it parsed all four jobs and
  went green.

## 5. Risks & open questions

- **§ 1 ordering inside the cut is pinned for staged copies only** (M11). On the source there is no
  mark to order. The review's human gate (Q4) still reads the diff.
- **A long source commit now delays a handle-less `truncate`** of a source-served file. `_record`
  already pays this cost, and `issues/source-commit-staged-publish.md` proposes narrowing the hold.
- **A residual identity gap.** Between `_ensure` and the lock, four things would have to happen: the
  entry purged, a new file created elsewhere taking its id, then a directory renamed onto the cut's
  ancestor, carrying that file onto `path`. The id check would then accept the new file. The chain
  needs a removal, a flush, a create and a rename inside one call's wait, and no identity token
  exists without a schema change, which the GOAL forbids. This is recorded in
  `issues/source-served-open-race.md`.
- **The `open`/`_serve` window stays open** (`issues/source-served-open-race.md`). So does
  `mark_dirty`'s write before the lock, keyed by the path it was handed. Both are pre-existing and
  recorded.
- **Old sizes stay wrong** in development databases until the audit exists (`issues/audit-command.md`,
  R4).
- **The swap test pins today's vacate order.** If a later cycle makes `_vacate` lazier, the swap test's
  tree assertion before the flush changes with it. That is intended: the test flags the change.
- 2026-11-07 (review cycle 2 remediation, Geoffrey's escalation ruling): the design above changed. The
  handle-less cut now takes the source lock for every cut, whatever copy serves, and decides under it
  and the path lock, once, the entry (path, id and kind), the serving copy (one `_serve` call, which may
  promote), the held place and its write keys; it opens with `O_NOFOLLOW`. Three risks follow. A cut
  that promotes holds the source lock for that whole-file copy, and every handle-less cut waits behind a
  source commit. The identity gap above stays open for a file and is closed for a non-file (refused with
  `ENOENT`). A rename of an ancestor already past its `_vacate` can still move the entry between the
  decision and the recording, which then matches nothing: review cycle 2's F6, ruled out of this cycle
  to `issues/truncate-recording-after-ancestor-rename.md`. `mark_dirty`'s write is no longer before the
  lock.

### Plan-gate rulings (2026-11-06, foreman)

No deviation overturns a sentence of `AGENTS.md` § 1, § 5 or § 10, so the plan gate is the
foreman's. Accepted as planned, with the planner's three questions ruled:

- **ENOENT on a removal or ancestor rename mid-call** is accepted: POSIX allows it, the baseline did
  it, and no R-ID's single-threaded sequence reaches it. It is shown to Geoffrey at the review
  sign-off (GOAL Q4).
- **The residual identity gap** stays recorded in `issues/source-served-open-race.md`, not closed:
  closing it needs an identity token, a schema change the GOAL forbids.
- **`issues/model-commit-route-reach.md:90`** says this seed "decides" the model question. P1 appends a
  dated line there stating the decision (GOAL Q2: `test_model.py` unchanged, so source-served model
  coverage stays with that seed), so the record is true on P1's tree.

## 6. Verification strategy

Engine-level, under `-m unit` (the GOAL's non-goals rule out the mount lane). One gate script,
`spec/source-path-truncate-and-stat/verify/truncate_gate.py`, with these modes (its docstring is the
reference):

- **`regressions`**:
  - each new test's shape: defined once, walks first, ends in `assert_converged`, which still compares
    sizes;
  - all of them pass on this tree;
  - every *fix* test fails on the baseline engine (`src/` at `0a88f30`) for its named cause;
  - every *pin* passes there;
  - `test_model.py` and both `conftest.py` files are byte-identical to the base;
  - no skip or xfail is added.
- **`mutants`**: eleven fix-removal mutants, each planted into a scratch copy of HEAD's `src/`. Each
  makes the tests it names fail, and the rest of `test_replay.py` still runs. The decisions they
  attack:

  | Mutant | Decision | Must fail |
  |---|---|---|
  | M1 | where the cut lands (logical path) | the seven R1/swap tests |
  | M2 | the lock (none) | cuts-under-lock, resolves-inside-lock, the three interleaved user ops |
  | M3 | the lookup (the entry from before the lock) | resolves-inside-lock |
  | M4 | the lock's tier (`!=`) | cuts-under-lock |
  | M5 | `_held_path`'s tier (`!=`) | staged control, own rename |
  | M6 | the lookup by id alone | the three interleaved user ops |
  | M7 | `_stat_held` at the logical path (R2) | both size pins, the earlier size test |
  | M8 | `_record` without the lock (R2's guard) | the `_record` lock pin |
  | M9 | the lock never released | cuts-under-lock |
  | M10 | the source counter adjusted | own rename |
  | M11 | the dirty mark after the cut (§ 1) | staged control |

  Anchors are the built engine's text. A build that reshapes a guarded line re-anchors it in the same
  commit. An anchor that is not found exactly once is red.
- **`records`**: the committed tree's § 2.3 records, read structurally.
  - Both tables keep every name they had at the base and name every `overlay.py` function the branch
    changes.
  - No § 5 row still lists the handle-less `truncate`.
  - The counts have one source of truth.
  - Every `file:line` cite of a changed file, in any seed the plan or the build touched, is pinned or
    still holds its base line.
- **`suite`** (R5), **`seeds`** (R6), **`ledger`**, **`prepush`** and **`ci`** (R7) follow the previous
  cycle's gate.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
