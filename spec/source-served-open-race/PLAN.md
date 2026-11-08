# PLAN — `open` finds a source-served file while a replay moves it

> **Status:** Draft for review · **Last updated:** 2026-11-08
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Every touch of the source inside `open` takes the source lock at the touch and reads the place under it.
`open` of a copy served off the source, and `_serve`'s promotion off the source, look the entry up again at
its path under the lock (a new helper, `_lookup_again`, with the same path, id and kind check the
handle-less `truncate` makes inline) and open the object at the place that entry records before they release
the lock: the direct open itself, or the promotion's whole copy (Q1: the lock is held across the copy). A
copy served off a stage takes no source lock. `truncate` is byte-identical. Nineteen deterministic tests in
`tests/engine/test_replay.py`, a twenty-one-mutant sweep, and the records R6 lists, the identity gap re-homed
to a new seed `issues/entry-identity-token.md` (Q2). Three phases, the previous cycle's shape.

## 2. Design

### 2.1 The engine (`src/sosfs/engine/overlay.py`)

`open`, after `_ensure` and the kind checks, unchanged up to `serving = self._serve(entry)`:

```python
            on_source = serving == self.source.name
            with self._source_lock if on_source else contextlib.nullcontext():
                current = self._lookup_again(entry) if on_source else entry
                fd = self.tiers[serving].open_direct(_pure(self._held_path(current, serving)), direct)
            handle = Handle(entry.id, serving, fd, flags)
```

The old `serve_path` expression and its comment go. A staged copy opens at `_held_path(entry, stage)`, which
is the logical path, as today. `_handles_mutex` and `catalog.touch` stay after the `with`, outside the source
lock.

`_serve`, in its promotion branch:

```python
            on_source = fastest.tier == self.source.name
            with self._source_lock if on_source else contextlib.nullcontext():
                current = self._lookup_again(entry) if on_source else entry
                self.transfers.copy(entry.path, self.tiers[fastest.tier], target, action='promote',
                                    now=self.clock(), src_path=self._held_path(current, fastest.tier))
            return target.name
```

The choice of `fastest` and `target` is unchanged and stays under the caller's path lock. A
stage-to-stage promotion takes no source lock, and `src_path` there is the logical path, as `None` was.

The new helper, placed after `_serve`:

```python
    def _lookup_again(self, entry: Entry) -> Entry:
        current = self.catalog.get(entry.path)
        if current is None or current.id != entry.id or current.kind != 'file':
            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', entry.path)
        return current
```

Its docstring says the caller holds the source lock, why (the `source_path` it returns holds until the lock
is released), when it raises, and that a file which took the id after a purge and was carried onto the path
passes. The docstrings of `open` and `_serve` say the same in their terms, the reused-id caveat included,
and claim "not another entry's object", never "never" (R6; adversarial review F1). The `_source_lock` comment in
`OverlayEngine.__init__` names the open, the promotion and the cut among its takers.

Why this meets R3, clause by clause:

- **Under the lock.** The place each source touch uses is `_held_path(current, source)`, and `current` is
  read by `_lookup_again` inside the `with` that holds the lock.
- **Before release.** `open_direct` and `transfers.copy` (its `stat` and `open_read` of the source object,
  and the whole `put`) run inside that `with`.
- **No value carried in.** `entry` (from `_ensure`) decides the path looked up and the id compared, never
  the place. The tier decision (`serving`, `fastest`) is made before the lock under the path lock; it picks
  *whether* the source is touched, not where. The unlocked branch never touches the source, whatever the
  decision turns out to be: a stage open or a stage-to-stage copy reads the stage at the logical path
  (research 01 § 2). The staged-copy pins and mutants M15 and M21 hold that branch lock-free.
- **`truncate` unchanged.** Its body is byte-identical (the gate checks). It calls `_serve(current)` with
  the lock held; `_serve`'s re-entrant `with` and `_lookup_again` re-read the row `truncate` just checked,
  under the same lock, so its decision, held place, `O_NOFOLLOW` open and recording are as signed off
  (research 01 § 5).

R4 follows from the same read: a replay that moves another entry's object onto the old place also advances
this entry's `source_path` (the move is this entry's queued rename, run by `_vacate` or a flush), so the
place read under the lock is this entry's object.

### 2.2 Tests (`tests/engine/test_replay.py`, appended after the last test)

Helpers: `AfterLookup` (wraps the engine instance's `_ensure` for one path; when `open`'s lookup returns
in the test's thread, it runs an action in a helper thread to completion, re-raises its exception, then
sets `ran`), `assert_reads` (opens, asserts the serving tier and the bytes, releases) and `swap_onto` (an
action: rename onto a place, then flush). Existing helpers are reused unchanged: `source_served`,
`Interleaved`, `concurrent_removal`, `lock_probe`, `rewrite`, `tree`, `walk`, `assert_converged`.

Every test builds through `source_served` or walks, and ends in `assert_converged(engine)`. No sleeps, no
timeouts. Names are fixed (the gate reads them):

| Test | R-ID | Sequence | Baseline (`5e0d997`) |
|---|---|---|---|
| `test_open_of_a_source_served_file_after_a_replay_commits_its_own_rename` | R1 | 1000-byte file, bypass stage; own rename queued; `flush` after the lookup | `FileNotFoundError` at the old place |
| `test_open_of_a_source_served_file_after_a_replay_commits_its_directory_rename` | R1 | same, `/data → /moved` | `FileNotFoundError` |
| `test_open_on_a_one_tier_stack_after_a_replay_commits_its_directory_rename` | R1 | one tier, `/data → /moved` | `FileNotFoundError` |
| `test_open_promotes_after_a_replay_commits_its_own_rename` | R2 | 5-byte file the stage admits; own rename; served from `scratch`; `pending() == []`; both residencies clean | `TierError: No file at` |
| `test_open_promotes_after_a_replay_commits_its_directory_rename` | R2 | same, `/data → /moved` | `TierError: No file at` |
| `test_open_after_a_replay_moves_another_file_onto_the_old_source_place` | R4 | `a → t` queued; after the lookup `b → a` (its `_vacate` runs `a → t`) and `flush` moves `b`'s object onto `/a.bin` | reads `b`'s bytes |
| `test_open_promotes_nothing_from_another_file_moved_onto_the_old_source_place` | R4 | same, 5-byte files, promotion; the stage holds `apple` | copies `berry` into `t`'s stage copy |
| `test_open_of_a_source_served_file_opens_under_the_source_lock` | R3 | `lock_probe` inside the source's `open_direct`: held; free after return | `[True] == [False]` |
| `test_open_promotes_off_the_source_under_the_source_lock` | R3 | `lock_probe` inside the source's `open_read`: held; free after return | `[True] == [False]` |
| `test_open_of_a_source_served_file_resolves_inside_the_source_lock` | R3 | `Interleaved(flush)` at the open's lock | `ran` is false |
| `test_open_promotes_from_where_a_replay_during_the_wait_moved_the_file` | R3 | `Interleaved(flush)` at the promotion's lock | `ran` is false |
| `test_open_after_a_concurrent_rename_moves_the_file_away` | R3 | `Interleaved(rename /data → /moved)`: `ENOENT` | `DID NOT RAISE` |
| `test_open_leaves_a_file_that_reused_the_entry_id_alone` | R3 | `Interleaved(concurrent_removal, recreate)`: `ENOENT`; the new file holds the id | `DID NOT RAISE` |
| `test_open_leaves_another_file_carried_onto_the_path_alone` | R3 | one tier; another file, its bytes on the source, carried onto the path: `ENOENT` | `DID NOT RAISE` |
| `test_open_refuses_a_symlink_that_took_the_entry_id` | R3 | a link that took the id, carried onto the path: `ENOENT` | `DID NOT RAISE` |
| `test_open_of_a_staged_copy_takes_no_source_lock` | § 10 | `lock_probe` inside the stage's `open_direct`: free | passes (pin) |
| `test_open_promotes_from_where_the_source_holds_a_file_behind_its_queued_rename` | § 5 | no replay; the promotion reads the pre-rename place, which stays | passes (pin) |
| `test_open_takes_no_path_lock_and_no_lookup_under_the_source_lock` | § 10 | `_ensure`, `getattr`, `readdir` and `locks.hold` wrapped to record whether the calling thread owns the source lock, through a source-served open and a promotion: never | passes (pin) |
| `test_open_promotes_from_a_staged_copy_without_the_source_lock` | § 10 | three tiers; clean on the middle stage only; `lock_probe` inside its `open_read`: free | passes (pin) |

The R1, R2 and R4 sequences inject at the GOAL's "after the lookup" point through `AfterLookup`, which works
on the baseline as on the fix, so each fails on the baseline for its named cause. The R3 tests use the lock
stand-in, which fires only where the fix takes the lock; their baseline cause is the lock's absence. The
carried-file test runs on one tier so the carried file's bytes sit on the source: on a stage stack the
`ENOENT` would come from an empty place, and the id comparison would go untested (found by the dry run,
mutant M10). A helper action never needs the path lock the test's thread holds: an action that flushes a
dirty data row for the opened path deadlocks (found by the dry run; the gate's run timeout turns such a hang
red).

The module docstring gains one bullet for the class these tests guard. Every function and class the file
held at the base keeps its source exactly (the gate checks).

### 2.3 Records (same commit as the code, P1)

- **`AGENTS.md` § 5**, the "Reading the source resolves through it too" bullet: `open` and a promotion off
  the source resolve the place through `_held_path`, take the source lock, look the entry up again
  (`_lookup_again`, `ENOENT` when it fails) and open the object before releasing it; a replay after
  `_ensure` leaves the call at the object's new place, not another entry's object, short of the reused id;
  a staged copy opens without the lock. The identity-gap sentence adds that `open` would open or promote
  such a file. The test module's docstring bullet says the same, the reused id included.
- **§ 10**, the source-mutex bullet: `open` (a source-served copy) and a promotion off the source (from its
  lookup through its whole copy) are takers; the delay list adds the `open` of a source-served copy and every
  promotion off the source; a promotion off the source, on `open` or a handle-less `truncate`, holds the
  mutex for that whole-file copy, replacing the sentence that said so for `truncate` alone, and says the bound
  is the stage's admission on a passthrough source but the whole object on an S3 or archive source, which a
  stage takes whatever its `bypass_size` or quota (research 01 § 3).
- **§ 11**, "Promotion runs on the calling thread": a promotion off the source also holds the source mutex
  for the copy (same-commit rule: the bullet says the path lock only).
- **Both high-blast-radius tables** name `open`, `_serve` and `_lookup_again` (the short table lacked `open`
  and `_serve`; research 02 § 1).
- **§ Known open violations**: the `open`/`_serve` row goes. The identity-gap row covers `truncate`, `open`
  and a promotion off the source, says it was reproduced by injection for the cut and for `open` (the
  planning probe, § 4), that every route needs a rename of an ancestor during the call, and names
  `issues/entry-identity-token.md`. The recording row's list of earlier landings adds "`_serve` raise …
  `ENOENT`" (research 01 § 5).
- **Counts**: 502 → 521 at the four unit-count places, 512 → 531 in the repository map, 164 → 183 engine
  tests (measured on the prototype).
- **Root `PLAN.md` § Risks**: the mutex sentence adds `open` and the promotion; the closed list adds them;
  "Still open" names `issues/entry-identity-token.md` for the identity gap, and no longer the `open`/`_serve`
  window.
- **`issues/truncate-recording-after-ancestor-rename.md`**: its Related line names the new seed in place of
  this cycle's; the counter-guard cite gets a pin ("which holds at `6425a21`"); its `ROADMAP.md` entry's
  "Placed beside the `open` seed" names the new seed.
- **`issues/entry-identity-token.md`**: filed by the plan commit with its `ROADMAP.md` entry (Q2). P1 appends
  a dated line: `open` and the promotion share the gap, reproduced by injection.
- **`issues/freethreaded-concurrency-stress.md`**: a dated note (the deferral of § 5's round-trip risk: the
  other file's stage bytes, reproduced by injection by the adversarial review; the promotion over a dirty stage
  copy, argued; an evicted stage copy, loud) and a
  pin on the "Path locks do not cover descendants" bullet's line cites ("These line numbers hold at
  `5e0d997`"), which P1's edits move.
- **This cycle's seed and its `ROADMAP.md` entry stay**: `code terrace roadmap` retires both after landing.
  On the final tree the only file outside `spec/` that names `issues/source-served-open-race.md` is
  `ROADMAP.md`, on that entry's `**Seed:**` line (deviation row 6).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | § 2.1 `open`: the place read by `_lookup_again` under the lock, `open_direct` inside it; the three R1 tests |
| R2   | § 2.1 `_serve`: the same for the promotion, the lock held across `transfers.copy`; the two R2 tests assert the stage serves, `pending() == []` and the stage residency |
| R3   | § 2.1, clause by clause; the lock-probe, inside-the-lock and lookup tests; mutants M1–M21; gate `regressions` checks `truncate` byte-identical |
| R4   | § 2.1: the place is read under the lock, so a replayed swap leaves the call on this entry's object; the two R4 tests (both fail on the baseline by returning or copying the other file's bytes) |
| R5   | § 2.2: nineteen tests, each ending in `assert_converged`; gate `regressions` (baseline causes, unchanged old tests, frozen files) and `mutants` (each new test named by a mutant that turns it red) |
| R6   | § 2.3, in P1's commit; gate `records` |
| R7   | gate `suite` (P1, P2), `seeds` (P2), `ci` on the saved run record (P3) |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (from the GOAL) and again against this design, after the dry runs.

- **§ 1, metadata before acknowledgment.** Not touched: `open` marks nothing dirty, and `truncate`'s order is
  byte-identical.
- **§ 1, intent → act → confirm.** The promotion's bracket is `transfer.copy`'s, unchanged; it now runs
  inside the source lock. A failed copy still rolls its intent back, and R2's tests assert `pending() == []`.
  Nothing acquires the lock inside `db.transaction()`: the lock is taken before `copy`, which opens its own
  transactions inside it, the documented order (research 01 § 1).
- **§ 5, committed source path.** Honoured and widened: the open and the promotion resolve through
  `_held_path`, read under the lock. No deferred rename is forced, and `_replay_through` gains no caller.
- **§ 10, concurrency.** Two new takers, both under a path lock already held (path locks → source mutex →
  `_handles_mutex` → SQLite write lock). Under the mutex nothing calls `locks.hold`, `_ensure`, `getattr` or
  `readdir`: `_lookup_again` reads the catalog, `transfer.copy` and `materialize_dirs` read the catalog and
  the tiers. `_handles_mutex` is taken after the `with`. Re-entrancy covers `truncate` → `_serve`. Cost: a
  promotion off the source holds the mutex for its whole copy, and a source-served `open` waits behind any
  source commit; § 10's text says both in the same commit (deviation row 2).
- **§ 11.** Failures stay `OSError`: a lookup that fails raises `FileNotFoundError`.
- **High-blast-radius paths.** `open`, `_serve` (and the new `_lookup_again`) and `tests/engine/test_replay.py`
  are on the list; the review's human gate applies (Q3).
- **§ Conventions.** Same-commit records (§ 2.3). No spec ids in `src/` or `tests/`. Commit subjects follow
  the profile.

### Deviation justifications

Nothing here overturns a sentence of § 1, § 5 or § 10. Rows 1–4 add complexity; rows 5 and 6 are readings
of an R-ID that the gate is asked to confirm. Each is read against the R-IDs it touches.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| 1. A new helper, `_lookup_again`, joins both high-blast-radius tables; `truncate` keeps its inline copy of the same three lines | One check for the two new lookups, so the mutants that attack it (M9–M12) attack both | Inlining it twice more leaves three copies free to drift. Making `truncate` call it changes the body Geoffrey signed off (REVIEW.md, cycle 3 ruling 5) and R3's "`truncate` unchanged" would then rest on an argument instead of a byte comparison. Touches R3; the gate checks `truncate` byte-identical |
| 2. Two new § 10 takers; a promotion off the source holds the mutex across its whole copy | R2 and R3: the place must be read under the lock and the source object read before release (Q1) | Pinning an opened stream needs `transfer.copy` (high blast radius) to take an opened source, and saves nothing on S3 or archive sources, whose `open_read` buffers the whole object (research 01 § 3). Re-resolve-and-retry is excluded by Q1. The cost, stated in § 10: a promotion off the source delays every `create`, `mkdir` and `rename` that reaches `_vacate`, the `open`, `release` or `fsync` of a source-served copy, and every handle-less `truncate`; unbounded by the stage on an S3 or archive source. Narrowing it is `issues/source-commit-staged-publish.md`'s question |
| 3. The source lock is taken only when the source is touched; a staged copy opens, and a stage-to-stage copy runs, without it. The tier decision is made before the lock | A staged open is the hot path; taking the mutex there would make every `open` of a staged file wait behind every source commit, a whole-file copy | Always taking it (the shape ruled for `truncate`, after its review's F4) is simpler to grade. F4's harm was a source touch made without the lock after the decision flipped; here the unlocked branch has no source touch to make, whatever the decision becomes, so a flip never yields an unlocked source act. It can yield a stage `FileNotFoundError` or `TierError`, or, through a rename of an ancestor and a staged file renamed into its place during the call (the GOAL's non-goal), a handle on that file's stage bytes: reproduced by the adversarial review, identical at the baseline, and recorded in `issues/freethreaded-concurrency-stress.md`. Always locking would not close that route, which never touches the source. R3 constrains the source *place*, which is read under the lock. Mutants M15 and M21 (always lock) are killed by the staged pins; M13 and M14 (lock on the wrong tier) by the lock probes. Open question 1 asks the gate to confirm |
| 4. Tests replace the engine instance's `_ensure` and `_source_lock` for one call | The only deterministic way to land a replay or a user op at the GOAL's "after the lookup" point, and inside the lock wait, without sleeps | A threaded stress test is nondeterministic and belongs to `issues/freethreaded-concurrency-stress.md`. `Interleaved` and tier-method patches are already the file's idiom |
| 5. **Reading of R4**: graded over interleavings with no user rename of an ancestor during the call | The identity gap: if, during the call, the entry is purged, a new file takes its id, a directory rename carries it onto the path and a flush moves its object onto the place the entry was held, R4's IF-clause holds and `open` returns the new file's bytes (planning probe, § 4). Only an identity token, a schema change the GOAL forbids, closes it | No design without a schema change meets R4's THEN on that route. Every route to it needs a user rename of an ancestor mid-`open`, which the GOAL's non-goals exclude ("A user `rename` of an ancestor moving the file's logical path mid-`open`"). Recorded in the identity-gap row and `issues/entry-identity-token.md`. Open question 2 |
| 6. **Reading of R6's last clause**: "Once this cycle lands, no file outside `spec/` SHALL name `issues/source-served-open-race.md`" holds after `code terrace roadmap` retires the seed; on the branch, `ROADMAP.md`'s own `**Seed:**` line for it is the one remaining name | The profile retires a landed cycle's seed and entry after landing (`AGENTS.md` § Factory), and the seed must stay while the cycle is in flight (the ISSUE template's `adopted:` row) | Deleting the seed and its entry in P1 breaks the profile's retirement order. The gate checks that nothing else names it. Open question 3 |

R-ID read-through: R1 and R2's sequences are met as worded and converge. R3's four clauses are met as § 2.1
argues. R4 is met for every interleaving of replays, user ops on other paths and swaps that keeps the file's
ancestors in place (row 5). R5 is met as worded. R6 is met as worded, with row 6's timing. R7 is untouched by
any row.

## 4. Rabbit holes (resolved)

Research ran the full fan-out (trigger 2: `open` and `_serve` are high blast radius), scaled to two briefs.
Digest: [`research/00-digest.md`](research/00-digest.md).

- *Lock order and deadlocks with the new takers* → none; every holder takes path locks before the source
  lock, and nothing forbidden runs under it ([`research/01-open-contract.md`](research/01-open-contract.md)
  § 1).
- *Can the serving decision flip under `open`'s path lock?* → only through an ancestor rename (non-goal);
  the unlocked branch never touches the source (brief 01 § 2).
- *Q1: hold or pin?* → hold (brief 01 § 3; deviation row 2).
- *Is the identity gap reachable without the non-goal?* → no (brief 01 § 4). Reached with it, on the
  prototype: the cut's probe sequence run inside `open`'s wait, plus a final flush, makes `open` return the
  new file's 500 bytes (`probe_identity_open.py`, planning lab, 2026-11-08).
- *Does `truncate`'s signed-off property survive a `_serve` change?* → yes; its body is byte-identical and
  `_serve`'s lookup re-reads the row it checked (brief 01 § 5).
- *Was the baseline only loud?* → no: the R4 swap returns, and promotes, another file's bytes (brief 01 § 6;
  the R4 tests' baseline causes). The `AGENTS.md:669` row and the GOAL's Problem say otherwise; the row goes
  in P1, and the gate is asked to note the GOAL's sentence (§ 5).
- *Records* → the sites, the formats, the second table's missing names and the stale engine count
  ([`research/02-records-and-gate.md`](research/02-records-and-gate.md) § 1, § 2).
- *The retired verifier* → `spec/source-path-truncate-and-stat/verify/truncate_gate.py`'s `records` (lines
  ~719–764) and `ledger` (~895–902) assert this cycle's seed and go red once it retires. Nothing runs that
  script: CI, `tools/lint.sh` and pytest's `testpaths` all exclude `spec/`. It is a point-in-time record of a
  landed cycle; this cycle leaves it unedited, and its own gate is `verify/open_gate.py` (brief 02 § 3).

### 4.1 Dry runs on the prototype

A detached worktree, `.worktrees/source-served-open-race-prototype` beside the cycle worktree, holds the
design built as in § 2. The drafted gate ran from the cycle worktree's copy by absolute path.

- `regressions mutants` (before the review's folds): GREEN in 27 s. All fifteen fix tests fail on the
  baseline for their named causes; both pins then drafted pass there; all nineteen mutants then drafted are
  killed.
- `records`: GREEN in about 2 s.
- `suite` (before the folds): GREEN in 27 s (lint OK, 519 passed under `CI=true`, 0 skipped, equal to the stated 519; 181 engine
  tests, equal to the stated 181).
- `seeds`: GREEN in 4 min 36 s, `test_model` passing for seeds 1–20, two workers.
- Per phase, on a genuine tree: the prototype was rebuilt as a plan commit (this cycle's `spec/`, the new
  seed and its `ROADMAP.md` entry, on `58151ec`) followed by a P1 commit. **P1** (`regressions mutants records
  suite`): GREEN in 56 s. **P2** (`suite seeds`): the `seeds` run above, on the same `src/` and `tests/`, and
  `suite` inside P1's run. **P3** Run A (`ledger prepush`): GREEN in 28 s, the ledger reaching both seeds;
  red once first, on a home-directory path in a research brief, since fixed. Run B (`ledger records ci`):
  `ci` stops at CANNOT RUN (exit 3) with no saved record, as designed; its parsing is the previous cycle's,
  unchanged, which graded three real run records.
- The full suite ran once on the prototype: 519 passed in 28 s under `CI=true`.
- After the review's folds (§ 4.2), on the rebuilt tree: P1's whole verify GREEN in 58 s (521 passed, 183
  engine tests, all four pins passing on the baseline, all twenty-one mutants killed). Two self-tests of the
  new `records` checks went red as they should: the `open` docstring without its reused-id clause, and a
  sibling link to this cycle's seed inside another seed.
- What the dry runs changed: the carried-file test moved to one tier (M10 survived on a stage stack); M5's
  and M7's target lists were narrowed to the tests they kill; M19's target is the earlier cycle's
  bypass-stage size tests (the handle-less cut's tests open through a promotion, not off the source);
  "never released" mutants hang every later locked call and were replaced by "released before the act"
  mutants (M16, M17), and the gate's runs got a process-group timeout so a hang goes red; the `__init__`
  lock comment is exempt from the "tables name every changed function" check; two seed cites the P1 edits
  move got pins.

### 4.2 Adversarial review

One fresh reviewer took the GOAL, this design, TECH, the drafted gate and the built prototype (as a commit,
experimenting in its own lab). It ran because the cycle's gate and R5's mutant sweep are gate-shaped
deliverables (trigger 3). Each finding, and how it was folded:

1. **MEDIUM: the `open` and `_serve` docstrings, the test module bullet and § 5 said "never" another entry's
   object, while the identity gap opens one** (reproduced by the reviewer). *Folded:* each now says "not",
   qualified by the reused id (§ 2.1, § 2.3); `records` requires "reused" in both docstrings, the module bullet
   and § 5's bullet, and rejects "never … another entry" there. Self-tested red.
2. **MEDIUM (non-goal, pre-existing): the staged branch can hand back another entry's stage bytes** through an
   ancestor rename and a staged file renamed into place after `_serve`; identical at `5e0d997`. *Folded:*
   deviation row 3 and § 5's risk say so; P1's note in `issues/freethreaded-concurrency-stress.md` records it as
   reproduced, and `records` checks the note.
3. **MEDIUM: nothing checked § 10's "nothing holding it calls `_ensure`, `locks.hold`, `getattr` or `readdir`"**;
   a `_lookup_again` through `_ensure` passed every test. *Folded:* the nothing-nested pin and mutant M20.
4. **LOW: nothing tested that a stage-to-stage promotion takes no source lock.** *Folded:* the three-tier pin
   and mutant M21.
5. **LOW: several `records` checks read whole files.** *Folded:* the seed-name check also finds the bare file
   name (sibling links in `issues/`); the root `PLAN.md` check reads the namespace-replay risk entry only; the
   recording seed's check reads its Related line; § 10's check reads the takers sentence. Self-tested red.

What it checked and found holding: inside the GOAL's scope the unlocked branches never reach the source and
nothing re-decides the tier after the lock choice; M1–M19 cover R1, R2, R4 and R3's four clauses; it tried
re-resolve-and-retry, a lock taken only on lag and lookups by id, each killed; lock order holds and nothing
takes the lock inside `db.transaction()`; `truncate` keeps its signed-off property; the counts agree.

## 5. Risks & open questions

**Plan-gate rulings (foreman, 2026-11-08).** All four as recommended: (1) a, the conditional lock; (2) a and
(3) a, recorded as GOAL clarifications Q4 and Q5; (4) a dated correction under the GOAL's Problem. Deviation
rows 1–4 accepted as written, row 2's unbounded hold on an S3 or archive source included (stated in § 10, and
before Geoffrey at review under Q3).

**Open questions for the gate** (each has a recommendation; none blocks P1 as drafted):

1. **Conditional lock (row 3).** (a) Lock only at a source touch, as drafted; (b) always take the source lock
   in `open`, as `truncate` does. *Recommend (a)*: (b) makes every staged open wait behind every source commit,
   and buys nothing R3 asks for, since the unlocked branch has no source touch.
2. **R4's reading (row 5).** (a) Accept: R4 is graded without user ancestor renames mid-call, the gap recorded
   in the new seed; (b) widen this cycle to a schema change, which the GOAL forbids. *Recommend (a)*.
3. **R6's last clause (row 6).** (a) The roadmap step retires the seed and its entry after landing, as the
   profile says; (b) P3 deletes them before landing. *Recommend (a)*.
4. **The GOAL's Problem text** says the earlier probe "found no case where `open` acted on another entry's
   object". The R4 tests show the baseline does, silently. The GOAL is locked and this plan does not edit it;
   the gate may want a dated correction there or in REVIEW.md, and may weigh the severity accordingly.

**Risks:**

- **The source mutex's hold grows.** A source-served `open` now waits behind any commit to the source, and a
  promotion off the source holds the mutex for its copy (row 2). On an S3 or archive source that copy is the
  whole object. Narrowing a commit's hold is `issues/source-commit-staged-publish.md`.
- **An ancestor's round trip during the call** can change what the path names between the tier decision and
  the act. A staged file renamed into the path's place makes `open` return a handle on that file's stage bytes,
  and a write through it lands in that file's copy, which still reads clean (silent; reproduced by the
  adversarial review, identical at `5e0d997`). A promotion off the source could copy over a stage copy written
  meanwhile and record it clean (silent loss, argued, not reproduced); a chosen stage copy evicted meanwhile
  fails the call loudly. All pre-existing and inside the GOAL's non-goal; P1 records them in
  `issues/freethreaded-concurrency-stress.md`.
- **The identity gap now reaches `open`** (row 5), recorded in `issues/entry-identity-token.md`.
- **A user ancestor rename after `_serve`** turns a success into `ENOENT` (research 01 § 4): a legal outcome,
  inside the non-goal.
- **The retired cycle's verifier goes red** when this seed retires (§ 4). Accepted as a historical record.
- **The swap tests pin today's vacate order**, as the previous cycle's swap test does.

## 6. Verification strategy

Engine-level, under `-m unit` (the GOAL's non-goals rule out the mount lane). One gate script,
`spec/source-served-open-race/verify/open_gate.py`, adapted from the previous cycle's; its docstring is the
reference. Modes:

- **`regressions`** (R1–R5): each new test's shape; all pass here; each fix test fails on the baseline engine
  (`src/` at `5e0d997`) for its named cause; each pin passes there; every function and class
  `test_replay.py` held at the base is unchanged; `truncate` is byte-identical; `test_model.py` and both
  `conftest.py` files are byte-identical; no skip or xfail is added.
- **`mutants`** (R3, R5): twenty-one mutants, each planted into a scratch copy of HEAD's `src/`; each makes the
  tests it names fail, and the rest of `test_replay.py` still runs; every new test is named by one. Anchors
  are the built engine's text; a build that reshapes a guarded line re-anchors it in the same commit.

  | Mutant | Decision attacked | Must fail |
  |---|---|---|
  | M1 | the open uses the place read before the lock | R1 ×3, R4 open, open-inside |
  | M2 | the open takes no source lock | open-holds, open-inside, the four lookup tests |
  | M3 | the promotion copies from the place read before the lock | R2 ×2, R4 promote, promote-inside |
  | M4 | the promotion takes no source lock | promote-holds, promote-inside |
  | M5 | the open does not look the entry up again | R1 ×3, R4 open, open-inside, moved-away |
  | M6 | the promotion does not look the entry up again | R2 ×2, R4 promote, promote-inside |
  | M7 | the open looks up again before the lock | open-inside, moved-away |
  | M8 | the promotion looks up again before the lock | promote-inside |
  | M9 | the lookup by id alone | moved-away, reused id, carried |
  | M10 | no id comparison | carried |
  | M11 | a missing entry not refused | moved-away, reused id |
  | M12 | the kind not decided again | symlink id |
  | M13 | the open's lock on the wrong tier | R1 ×3, open-holds, staged pin |
  | M14 | the promotion's lock on the wrong tier | R2 ×2, promote-holds |
  | M15 | every open takes the source lock | staged pin |
  | M16 | the open releases the lock before it opens | open-holds |
  | M17 | the promotion releases the lock before it copies | promote-holds |
  | M18 | the promotion copies from the logical path | queued pin |
  | M19 | the open uses the logical path | the two bypass-stage size tests, open-holds |
  | M20 | the lookup under the lock goes through `_ensure` (§ 10's "nothing holding it calls `_ensure`") | nothing-nested pin |
  | M21 | every promotion takes the source lock | stage-to-stage pin |

- **`records`** (R6): § 2.3, read structurally from committed text.
- **`suite`** (R7 bullets 1–2): `uv sync --locked`; lint under `CI=true`; `-n auto -m unit` with 0 failed,
  0 errors, passed > 502 and equal to `AGENTS.md`'s stated count; the engine share equal to its stated count.
- **`seeds`** (R7 bullet 3): `test_model.py` for seeds 1–20 without `CI`, from scratch trees of HEAD with no
  `.hypothesis/`, two workers.
- **`ledger`**, **`prepush`** and **`ci`** (R7 bullet 4): as the previous cycle's.

Every pytest run the gate starts runs in its own process group under a 300 s timeout, killed whole on
expiry; `seeds` keeps its own per-seed timeout and group kill.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
