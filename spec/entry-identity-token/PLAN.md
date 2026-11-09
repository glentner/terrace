# PLAN — A call that waits for the source lock never acts on a file that took a purged entry's id

> **Status:** Draft for review · **Last updated:** 2026-11-09
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Under Q1 (A), with no schema change, each of the three calls opens an in-process **purge watch** under its path
lock before its first lookup. That lookup, `_ensure`, unchanged in signature, clears the watch *atomically with
the read that returns the entry*: under the watch's mutex for an entry already cataloged, or inside the insert's
transaction for one it adopts. Every purge of an entry row records the purged id in every open watch, inside its
own transaction, holding that mutex, before it commits. So a record that lands after the naming read can only be
the named entry's own purge. The lookup under the source lock refuses with `ENOENT` an entry whose id the watch
holds: a file at the path with that id took it after the named entry was purged. A purge of another entry adds an
id the call never named, so it refuses nothing (Q4, R4).

One new leaf lock, held across one catalog read (deviation row 2), and no new holder of the source lock. 18
tests and 13 mutants. The records R8 lists, plus one count it doesn't and pins on drifted cites in untouched
seeds. Three phases, the shape of cycles 4 and 5.

The adversarial review found that the first draft's naming loop could name, and cut, a file that took the id
while the call was naming (§ 4.1, HIGH). The design was changed to atomic naming, which meets the GOAL's Outcome as
worded, and the review's route is now a test (T14) that is red on the old design. A second adversarial pass, on
the redesign, found no interleaving that breaks § 2.1 and no deadlock; its four findings are folded (§ 4.1).

## 2. Design

### 2.1 The engine (`src/sosfs/engine/overlay.py`, `src/sosfs/state/catalog.py`)

- **`Catalog.purge`** returns the purged row's id (`DELETE … RETURNING id`; `Catalog.add` already uses
  `RETURNING`), or `None`.
- **`PurgeWatch`**, declared *below* `OverlayEngine`, so nothing above `overlay.py:76` moves and the unpinned
  cite `issues/engine-error-mapping.md:17` (`overlay.py:74-76`) still holds (research 03 § 3): a set of purged
  ids, with `add` and `clear` (each called with the watch mutex held) and a locked `__contains__`.
- **`OverlayEngine.__init__`**: `_watches` (a list), `_watches_mutex` (a `threading.Lock`), and `_naming` (a
  `threading.local`, each thread's open watch), declared after `_source_lock`. The `_source_lock` comment's order
  sentence gains the watch mutex.
- **`_watch()`**, a context manager: saves the thread's previous `_naming.watch`, then, inside its `try`,
  registers a fresh watch and sets it as the thread's naming watch; its `finally` restores the previous one and
  removes the watch if it was registered. Restoring rather than asserting none was set: no call nests a watch
  today, and if one ever did, each call would name with its own watch and the outer call's would be left in place,
  where an assertion would turn a harmless nesting into an exception in production. Registering inside the `try`
  means no exception path leaves a watch registered (the second review's F4).
- **`_purge(path)`**: `catalog.purge(path)`, then, under the watch mutex, adds the id to every open watch. It is
  the only caller of `Catalog.purge`, and it runs only inside the caller's `db.transaction()`. It never opens
  one, since `transaction()` does not nest (`db.py:133`). All four purge sites call it: `_remove`,
  `_execute_namespace_locked` (twice), and `_vacate`, whose tombstone purge was autocommit and is now wrapped in
  `with self.db.transaction():`.
- **`_ensure(path)`**, its signature unchanged: its first read is `_named(path)`. **`_named(path)`**: with no
  watch on the thread, a plain `catalog.get`; with one, it first opens the thread's connection
  (`self.db.connection`), so the per-connection pragmas never run under the mutex, then `with self._watches_mutex:
  watch.clear(); return self.catalog.get(path)`. **`_adopt(path, stat)`**, its signature unchanged: inside its insert's transaction, it
  clears the thread's watch under the mutex; its raced-adoption branch reads through `_named`. The signatures stay
  because cycle 4's test helper `AfterLookup` replaces `engine._ensure` with a one-argument callable. A watch
  passed as an argument failed seven existing tests (R7; § 4, rabbit hole 3).
- **`_lookup_again(entry, watch)`**: the existing test (path, id, kind) plus `or entry.id in watch`.
- **`open`**: `with self.locks.hold(path), self._watch() as watch:`, then `self._ensure(path)` as before, with the
  watch passed to `_serve` and `_lookup_again`. **`_serve(entry, watch)`** passes it to its promotion's
  `_lookup_again`.
- **`truncate`**, handle-less: the same `with`, `self._ensure(path)` as before. Its inline check under the source
  lock keeps its form and gains one test, `or entry.id in watch`. Everything from `serving = self._serve(current,
  watch)` through the recording is the base's code, which the gate pins (deviation row 4). The check stays inline,
  not folded into `_lookup_again`: folded, it became the first `_lookup_again` call in a cut, and cycle 5's test
  that stops at the promotion's `_lookup_again` stopped at the cut's own check instead, still green but no longer
  pinning what it says (research 02).

**Why it is sound** (research 01 § 3, revised by the review). A purge transaction holds SQLite's write lock from
`BEGIN IMMEDIATE` to `COMMIT`, and records inside it while holding the watch mutex.
- (a) If a purge deletes the very row the call's naming read returned, it began after that read. During it, the
  committed state holds the row where the purge deletes it, which is not the call's path: a `_remove` of the call's
  path needs the path lock the call holds, and replay purges delete tombstones only.
- (b) If it deletes an earlier row that held the same id, it committed before the naming read, which saw the reuse.

The naming read and the clear are one step under the watch mutex (or, for an adoption, inside the insert's
transaction, which no purge transaction can interleave). So every record made before that step is (b), and the
clear drops it. Every record made after is (a), the named entry's own purge. Recording after `COMMIT` would let
(b) land after the step, a false refusal; recording before `BEGIN` would let (a) land before it and be cleared, a
miss. The entry the call names is exactly what `_ensure` returns, its first lookup.

**Per window** (research 03 § 1, and the review). The records give each outcome in its own window, never a
blanket one.
- The cut takes the source lock for every cut, so a file that took the id is refused in every window after the
  naming read, served off the source or off a stage.
- `open` and a promotion off the source refuse it whenever the call reaches its lookup under the lock.
- Two stage windows stay open, both the GOAL's Non-goal (an ancestor's round trip), homed in the stress seed:
  - a route that lands before `_serve` reads `fastest_copy` and leaves the new file's fastest copy on a stage,
    which is served or promoted from that stage with no lookup under the lock;
  - a route that lands after a promotion off the source and before `open` opens the stage copy it made, which
    `open` opens at the logical path without the lock (found by the review).
- A purge that rolls back after recording (a failed `COMMIT`: in every site the purge is the transaction's last
  statement) leaves the id in the watch, and a call whose named entry that row was refuses it (deviation row 3).

### 2.2 The tests (`tests/engine/test_replay.py`, appended after the last test)

Helpers, new: `reuse_route` (rename `/data` away, remove the file and drain, create a file that takes the id,
keep it on the stage or evict it, rename `/data` back), `other_purged`, `other_created`, `with_other` (an
`/other.bin` holding the largest id) and `first_call` (a `stop_at` pause that runs an action once, on another
thread, for the caller's thread). Existing helpers reused unchanged: `source_served`, `Interleaved`,
`concurrent_removal`, `rewrite`, `stop_at`, `tree`, `seed`, `walk`, `assert_cut`, `assert_converged`, `JOIN`.

| # | Test (suffix after the call's prefix) | GOAL | Fixed | Baseline `c7d2049` |
|---|---|---|---|---|
| T1 | truncate `refuses_a_source_served_file_that_took_the_entry_id` | R1 | `ENOENT`, 500 bytes kept, residency clean | cuts the new file: `DID NOT RAISE` |
| T2 | truncate `refuses_a_staged_file_that_took_the_entry_id` | R1 | `ENOENT`, stage bytes and residency clean | `DID NOT RAISE` |
| T3 | open `refuses_a_source_served_file_that_took_the_entry_id` | R2 | `ENOENT`, no handle for the id | `DID NOT RAISE` |
| T4 | open `that_promotes_refuses_a_file_that_took_the_entry_id` | R3 | `ENOENT`, no intent, stage empty, no residency | `DID NOT RAISE` (reproduces the GOAL's argued case) |
| T5–T10 | each call `…when_another_entry_is_purged_during_the_wait` / `…when_another_file_is_created_during_the_wait` | R4 | acts as with no wait | the same (pins) |
| T11 | truncate `names_a_file_that_took_a_purged_id_before_its_first_lookup` | R4 | cuts the file at the path | the same (pin) |
| T12 | truncate `refuses_once_its_entry_is_purged_after_its_first_lookup` | R1 | `ENOENT` at the lock, after the carry lands there | `DID NOT RAISE` |
| T13 | truncate `refuses_a_file_that_took_the_id_of_an_entry_removed_with_no_source_copy` | R1 | `ENOENT`, stage bytes kept | `DID NOT RAISE` |
| T14 | truncate `refuses_a_file_that_took_its_id_right_after_its_first_lookup` | R1 | `ENOENT`, new file kept | `DID NOT RAISE`; red on the first draft's naming loop too |
| T15 | truncate `cuts_a_file_it_adopts_with_an_id_another_purge_freed` | R4 | cuts its adopted file | the same (pin) |
| T16 | open `refuses_a_source_served_file_that_took_its_id_right_after_its_first_lookup` | R2 | `ENOENT`, no handle | `DID NOT RAISE` |
| T17 | open `that_promotes_refuses_a_file_that_took_its_id_right_after_its_first_lookup` | R3 | `ENOENT`, no intent, no stage residency | `DID NOT RAISE` |
| T18 | truncate `is_not_refused_by_a_purge_that_committed_before_it_named_its_file` | R4 | cuts its adopted file | the same (pin) |

Each test asserts that the id it relies on was reused. Each refusal test asserts `engine._watches == []`, since a
leaked watch grows with every purge; T15 and T18, pins, cannot, since the baseline has no watches. Each ends in
`assert_converged`.

How the routes are built:
- T5–T10's `create` purges `/other.bin`'s tombstone through its `_vacate` and takes the id that purge freed.
- T11's route runs before `_ensure`; T12's purge runs right after it, and its carry onto the path at the source
  lock (`lock.ran` asserted); T14's whole route, the carry included, runs right after it, the review's HIGH.
- T13 purges the entry through `_remove` directly, with no replay. Its route runs the rename row alone
  (`commit_row`), so the rename's tombstone, which holds a larger id, goes without a flush committing the file.
- T15 adopts its file from an unlisted directory, and another entry's purge lands at the source `stat` between
  the naming read and the adoption.
- T16 and T17 are T14's route for a source-served open and an open that promotes (the second review's probes):
  the whole route lands right after `open`'s first lookup returns.
- T18 places a thread right after a removal's purge transaction commits: it adopts a file that takes the freed
  id, and a cut names it. A purge records before it commits, so the cut goes ahead; a build that recorded after
  the commit would refuse it (the second review's E3 probe). It imports `contextmanager` locally, so no line above
  it moves.

The module docstring gets one bullet. No existing function or class changes (R7).

**Mutants** (planner's lab, prototype, full `test_replay.py`, from a green tree; the gate's `mutants` mode re-runs
them with cause regexes):

| Mutant | Kills |
|---|---|
| M1 `_lookup_again` ignores the watch | T3, T4, T16, T17 |
| M2 `_purge` records nothing | T1–T4, T12, T13, T14, T16, T17 |
| M3 the watch matches any purge | T5–T10 (and 13 existing replay-during-the-wait tests) |
| M4 naming never clears the watch | T11 |
| M5 the cut clears its watch after its first lookup returns | T12, T14 |
| M6 the cut's inline check ignores the watch | T1, T2, T12, T13, T14 |
| M7 / M8 one site's `_lookup_again` with a fresh watch (open / promotion) | T3, T16 / T4, T17 |
| M9 `_remove` purges without `_purge` | T13 |
| M10 adoption does not clear inside its transaction | T15 |
| M11 `open` clears its watch after its first lookup returns | T16, T17 |
| M12 a promotion clears the watch before its lookup under the lock | T17 |
| M13 `_remove` records its purge after the transaction commits | T18 |

Not run as mutants:
- **E1**, `_vacate`'s purge left outside a transaction: **guarded statically** (`purges_watched`). Autocommit
  commits the `DELETE` before the record, so a thread can run between the two, take the freed id and name a file
  with it, and the late record then refuses that file: a false refusal (R4), the class M13 runs for `_remove`. The
  tombstone `_vacate` purges is never a named entry, so no test reaches it there.
- **E3**, recording after `COMMIT` rather than before: **guarded statically** (`purges_watched` pins the record
  inside the caller's transaction), and run as M13 for `_remove`. The first draft called it equivalent under any
  deterministic test; the second review placed a thread between the commit and the record and showed it is an R4
  false refusal that the earlier tests missed (T18 now catches it).
- **E2**, the rename-tombstone purge unrecorded: equivalent (those rows are never a live entry, so never a named
  one).
- **E4**, the kind test dropped from both checks under the lock: equivalent (for a named file, the id test or the
  watch refuses every non-file first; the first review ran it, 98 of 98 green).
- **The naming read made outside the mutex, apart from its clear**: argued, not run. A test thread placed between
  the two would deadlock on the mutex the design holds; `watch_leaf` pins the shape statically (one hold, the
  clear then the read, and `watch.clear` nowhere but `_named` and `_adopt`).

### 2.3 The records (R8), all in P1's commit

Drafted on the prototype (research 03 § 1), with the review's folds:

- **`AGENTS.md` § 5**: `_lookup_again` checks "path, id, kind and the call's purge watch". "Short of the reused
  id below" goes. The cut decides "the entry at its path (path, id and its purge watch)". The sentence that said
  the id check cannot tell, and pointed to § Known open violations, becomes the watch's account: each call opens
  a watch under its path lock; its first lookup clears it atomically with the naming read (`_named`, or inside
  `_adopt`'s transaction); `_purge` records every purge inside its transaction, holding the mutex, before commit;
  the lookup under the lock refuses an id the watch holds. Then the outcome in each window: a purge of another
  entry, or a create that takes another purged id, never refuses; a rolled-back purge refuses its own named
  entry; and the two stage windows of § 2.1, the stress seed's ancestor's round trip.
- **`AGENTS.md` § 10**: the order ends "→ SQLite write lock → purge-watch mutex". A new bullet says the mutex is a
  leaf and names its holders: `_purge`, inside its transaction; `_adopt`, inside its insert's; `_watch`;
  `_named`, which makes one catalog read holding it; and the cut's check and `_lookup_again`. Nothing else is
  acquired under it: `_named` opens the thread's connection before it takes the mutex, so no pragma runs there. The
  source mutex gains no holder.
- **Both high-blast-radius tables**: `_purge` joins the replay row. `_ensure`, `_named`, `_adopt` and `_watch` join
  the `open`/`_serve`/`_lookup_again` row, whose Why names the watch. `purge` joins the `catalog.py` rows. The list
  only widens.
- **§ Known open violations**: the `5, 10` row goes. What stays open is a different window or a different
  identity, both homed in the stress seed.
- **The counts** (prototype figures; P1 re-measures): 530 → 548 at `:70`, `:672` and `:714` (base lines); 540/530 → 558/548
  at `:111`; and 192 → 210 engine tests at `:136`, which R8 does not list but which is a measured figure in the
  same file.
- **The docstrings** of `truncate`, `open`, `_serve`, `_lookup_again`, `_ensure`, `_named` and `_adopt`;
  `Catalog.purge`'s; the `_source_lock` comment; and `test_replay.py`'s module docstring. Each drops "cannot
  tell", "only a schema change" and "short of a file that took", and names the watch and its window. Every
  other sentence of the cut's docstring stays, as the signed-off property.
- **Root `PLAN.md`**: the cut's decision names the watch, the closed list gains one sentence, and the "Still
  open" clause naming the seed goes.
- **`issues/freethreaded-concurrency-stress.md`**: Q5's dated line, nested under "Queue-row identity is by
  value". It names:
  - handles keyed by entry id (`_handles`, `Handle.entry_id`, read by `_mark_handle_dirty`, `_record`,
    `getattr`, `_commit_hop`, `evict_copy`);
  - data rows read from a scheduler snapshot (`commit_row`, `flush`, `dirty_copies`);
  - the two stage windows of § 2.1.

  All are argued, not run (research 03 § 2). It cites no `file:line`, so it carries no pin.
- **Drifted cites in seeds this cycle does not otherwise touch** (the review's MEDIUM): each unpinned `file:line`
  cite of a changed `.py` file that no longer matches its base text gets "holds at `c7d2049`", where it was true.
  The gate's `cites_hold` now checks every seed, not only touched ones.
- After this, only `ROADMAP.md:35` (`**Seed:**`) and the seed itself name `issues/entry-identity-token.md`
  outside `spec/`. `code terrace roadmap` retires both after landing.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | § 2.1: the watch, atomic naming, the cut's inline check. T1 (source), T2 (stage), T12 (a purge after the first lookup, carried at the lock), T13 (a purge by `_remove`), T14 (the whole route after the first lookup) |
| R2 | `open` passes its watch to `_lookup_again`; T3 (no handle registered for the id), T16 (the route right after the first lookup) |
| R3 | `_serve` passes it to the promotion's `_lookup_again`, before `transfers.copy` opens an intent; T4, T17 |
| R4 | The watch is keyed by the purged id, cleared with the naming read, and a purge of another id never matches; T5–T10 (each call, a flush's purge and a create's), T11 (a reuse before the first lookup), T15 (an id freed during an adoption), T18 (an id freed just before a purge records); deviation row 3 |
| R5 | No edit to `schema.sql`, `db.py` or `SCHEMA_VERSION`; the gate's `frozen_schema` |
| R6 | The lookup under the lock accepts only when the watch, cleared with the naming read, holds no record of the id. A purge of the named entry always records after that read (§ 2.1 (a)), so no purge-and-reuse can reproduce the fact |
| R7 | § 2.2: 18 tests, M1–M13 each killing what it names; E1 and E3 guarded statically, E2 and E4 argued; existing tests unchanged, `_ensure`'s and `_adopt`'s signatures kept for them |
| R8 | § 2.3, in P1's commit; the gate's `records`, `cites_hold` over every seed |
| R9 | P1 `suite`, P2 `seeds`, P3 `ci` on the saved run record |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research, against the GOAL's intent, and again against this design.

- **§ 1, metadata before acknowledgment.** Untouched: every refusal lands before the cut's dirty mark (the
  lookup under the lock precedes `_serve` and `mark_dirty`), and before `open` registers a handle.
- **§ 1, intent → act → confirm, and confirm before purge.** The promotion refuses before `transfers.copy` opens
  its intent (T4: no pending intent). Each purge stays where it was in its transaction, after its confirm.
  `_vacate`'s purge gains a transaction of its own and stays the only statement in it.
- **§ 2, durability.** A purge's durability is unchanged. `_adopt`'s transaction gains a clear of an in-memory set,
  no statement. No pragma or connection setting changes.
- **§ 3, SQL construction.** `RETURNING id` is a static fragment in a `Template`; nothing is spliced.
- **§ 5, the committed source path.** No `source_path` read or write changes.
- **§ 10, concurrency.** One new lock, a leaf after the SQLite write lock, held by `_named` across one catalog read
  (deviation rows 1 and 2). The source mutex's holders, and what it serializes, are unchanged. Nothing holding it
  calls `locks.hold`, `_ensure`, `getattr` or `readdir`: `_ensure` runs before the cut and `open` take it. It is
  still never acquired inside `db.transaction()`: `_vacate` opens its new transaction *under* it. No module-global
  state: the watches and the thread-local belong to the engine instance.
- **§ 11, the API contract.** The only new failure is `ENOENT`, an `OSError`.
- **High-blast-radius paths.** `truncate`, `open`, `_serve`, `_lookup_again`, `_remove`,
  `_execute_namespace_locked`, `_vacate` (listed), and `tests/engine/test_replay.py`. `_purge`, `_ensure`,
  `_named`, `_adopt`, `_watch` and `Catalog.purge` join the tables (row 5). Geoffrey signs off on the engine diff
  whatever the review finds (Q6).

### Deviation justifications

Each row was read against the wording of every R-ID it touches. The first draft's row 2, which redefined *the
named file* as the last of several lookups, is gone: the review showed it bent R1–R3 and the Outcome (§ 4.1). The
named file is now exactly what `_ensure`, the first lookup, returns.

| # | Deviation | Why needed | Simpler alternative rejected because |
|---|-----------|-----------|--------------------------------------|
| 1 | § 10: a fifth lock, the purge-watch mutex, a leaf after the SQLite write lock, taken by `_purge` and `_adopt` inside their transactions and by a call around its watch; § 10's order sentence and a new bullet say so in the same diff | A purge must record before it commits (§ 2.1), so the record is made while the SQLite write lock is held; the watch list and its sets are shared between threads | **`_handles_mutex`**: `_purge` would take it after the SQLite write lock, against § 10's order. **Lock-free** (copy-on-write list): a racing registration loses a watch, so a purge goes unrecorded (a miss). **Record under the source lock** (Q1's example): `_remove` purges under path locks only, so `unlink` and `rmdir` would wait out every commit to the source, and § 10's "the mutex does not cover user removals" would change. Read against R4 and R6: the leaf takes no lock, so it cannot deadlock a purge or delay a refusal. Against Constraints: the stated order holds, extended at its end; nothing under the source mutex takes a path lock or `_ensure`; the source mutex is never taken inside a transaction |
| 2 | `_named` holds the leaf mutex across one `catalog.get` (a WAL read, which takes no SQLite write lock and does not wait on a writer); and a `threading.local` on the engine carries the call's open watch into `_ensure` | The naming read and the clear must be one step, or a record in between is ambiguous: refusing on it is a false refusal (R4), and accepting is a miss. The first draft's loop accepted, and the review cut a file through it (§ 4.1). `_ensure` cannot take the watch as an argument: cycle 4's `AfterLookup` replaces it with a one-argument callable, and seven existing tests failed (R7) | **Re-read after `_ensure`**: the re-read, not `_ensure`, names the entry, so the review's route still names and cuts the new file. **The naming read inside a `BEGIN IMMEDIATE`**: serializes every `open` and cut on SQLite's write lock, waiting up to the 5 s busy timeout. **An argument to `_ensure`**: R7. Read against R1–R3 and the Outcome: the named file is what the first lookup returns, and a file that took its id afterwards is refused (T12, T14). Against R4: T11 and T15. Against § 10: the read under the leaf takes no lock a purger holds while it waits for the leaf. `_named` opens the thread's connection before the hold, so a thread's first database use never runs `_connect`'s pragmas under the leaf. The first draft said `_ensure`'s parent reads open it first; the second review showed that is false on a thread's first use, and the code now opens it explicitly. Measured by the second review, on the GIL build: a fresh WAL read takes 0.000 s against an open writer's transaction, and `open` throughput is about 4000 per second, the base equal to the prototype |
| 3 | A purge whose transaction rolls back after its record (a failed `COMMIT`) leaves the id in open watches, and a call whose named entry that row was refuses with `ENOENT` though the entry survives | The record must precede the commit (§ 2.1), and nothing outside `db.py` can see a rollback | **Retract on rollback**: needs a hook in `db.transaction()`, and `db.py` is frozen (Q1). Read against R4: it speaks of *other* entries purged, added or given reused ids; here it is the named entry's own failed removal, on an exception path (the review agrees). The records say so, and never "refuses only when purged". Sent to the sign-off (§ 5, Q-b) |
| 4 | `truncate`'s code changes in the function Geoffrey signed off on 2026-11-08: the watch opens with the path lock, and the inline check under the source lock gains `or entry.id in watch` | The Constraint asks for the refusal of a file that took the id to join the property's refusals of a missing entry, a changed id and a non-file | **Fold the check into `_lookup_again`**: it moves the first `_lookup_again` call in a cut, so cycle 5's promotion-lookup test stops at the wrong call and still passes (research 02). Read against the Constraint: `_ensure(path)` is called as before and names the same entry (the clear inside it changes no value it returns); the check's three tests are unchanged and the watch's joins them; the gate pins the check to the base's but for that one test, and everything from `_serve` through the recording to the base's code |
| 5 | `_purge`, `_ensure`, `_named`, `_adopt`, `_watch` and `Catalog.purge` join both high-blast-radius tables (an § Invariants edit inside a `[fix]` diff) | They are now what tells the entry apart; a reorder in `_purge` (record after commit) or `_named` (read outside the hold) is silent | Leaving them out understates the review gate. Cycle 5's row 6, ruled the same way. The same-commit rule moves § Invariants with the behaviour |

Figures, rederived: none of the new tests waits on time. `Interleaved` and `first_call` join their helper thread
synchronously (`first_call` bounds the join at `JOIN` = 10 s), so a mutant that deadlocks fails within 10 s, and the
gate's 300 s process-group kill is the backstop. The prototype's suite takes about 30 s, the same as the base's
(§ 6).

## 4. Rabbit holes (resolved)

1. **Which mechanism under Q1 (A)?** A per-call purge watch, recorded inside the purge's transaction. Rejected:
   a record under the source lock, identity by value (reproducible; a `chmod` changes `ctime`), no reuse within
   the mount (breaks five existing tests that assert reuse, R7), any-purge and any-add refusals (R4), and a
   per-id map for the mount's life (unbounded) ([01 § 2](research/01-identity-mechanism.md)).
2. **The naming race.** A purge can record while the call reads its entry. The first draft looked again in a loop;
   the review showed that it names a file that took the id during the naming, and cuts it (§ 4.1). Atomic naming
   (§ 2.1, deviation row 2) leaves no window between the read and the clear.
3. **`_ensure`'s signature is load-bearing.** Passing the watch as an argument broke cycle 4's `AfterLookup`
   (seven tests, `TypeError`), found by the full suite after the redesign. A thread-local carries it instead.
4. **The `_vacate` purge** was autocommit. Wrapped, it records before its own commit. No caller of `_vacate` is
   inside a transaction, and `transaction()` cannot nest ([03 § 1](research/03-records-and-gate.md)).
5. **Which purge and naming sites a test can reach.**
   - Only `_remove` (`:534`) and the unlink row's replay (`:1121`) ever purge a live entry's row. T13 reaches the
     first (M9); T1–T4, T12 and T14 the second (M2). The rename-tombstone purges are equivalent (E1, E2).
   - The adopt path's clear is reached by T15 (M10).
   - The found path's atomicity cannot be reached without a thread between two statements under a mutex, and is
     guarded statically.
6. **Per-window records.** Two stage windows of `open` and `_serve` stay open (Non-goals); the records scope each
   outcome to its window ([03 § 1](research/03-records-and-gate.md), § 4.1).
7. **Q5's inventory.** Handles keyed by entry id and data-row snapshots stay open, argued
   ([03 § 2](research/03-records-and-gate.md)).
8. **Cites the change shifts.** `PurgeWatch` below `OverlayEngine` keeps `engine-error-mapping.md:17` true. The
   review found twelve more unpinned cites in eight untouched seeds that drift; they get pins, and `cites_hold`
   now covers every seed (§ 4.1).
9. **Folding the cut's check into `_lookup_again`**, drafted first, made cycle 5's promotion-lookup test stop at
   the cut's own check: green, but no longer pinning its subject. Research 02 caught it; the check stays inline
   (deviation row 4) ([02](research/02-test-harness.md)).
10. **Test routes that really reuse the id.** The id is reused only when the purged row held the largest id. A
    rename adds a tombstone with a larger one, so each route drains it first. T13's first draft did not, and its
    own reuse assertion went red on both trees; its route now runs the rename row alone.

### 4.1 Adversarial review

One fresh reviewer had the GOAL, the drafted PLAN, the drafted gate, and a committed prototype tree on which P1's
gate was green. It attacked the gate with variants and the design with interleavings. It found the § 2.1 argument
for the *wait* sound under real threads, every entry purge going through `_purge` inside a transaction, the lock
order free of deadlock paths, and `purges_watched`, `watch_leaf` and `truncate_kept` tight against its attacks.
Its findings and folds:

| Sev. | Finding | Shown | Fold |
|---|---|---|---|
| HIGH | The naming loop names, and cuts, a file that took the id while the call was naming: a whole route landing between `_ensure`'s read and the loop's test makes the loop read again and accept the new file (500 bytes cut to 4). Row 2 redefined the named file to pass, which bends R1–R3 and the Outcome. T12 refused only because its re-read found the path empty, so PLAN's "carry lands at the lock" was false | demonstrated | Atomic naming (§ 2.1, row 2): the loop is gone, and the named file is what `_ensure` returns. The review's route is T14, red on the loop design and on the baseline. T12 now asserts its carry reaches the lock. M4, M5 and M10 attack the new decisions. The first try passed the watch to `_ensure` and failed seven existing tests (rabbit hole 3); the thread-local fixed it, and the full suite is green |
| MEDIUM | The records' overclaim check was case-sensitive and matched "took" only: "It never opens a file that has taken its entry's id" and "Never does one of the three calls act on a file that took its entry's id" passed `records` | demonstrated | The check is case-insensitive over `took`/`taken`/`takes`/`taking`, and flags "always refuses" and "in every window" with no window qualifier |
| MEDIUM | `cites_hold` checked only touched seeds; twelve unpinned cites in eight untouched seeds now point at different code (one cited a Q5 site) | demonstrated | Pins where each cite held at `c7d2049`; `cites_hold` covers every seed |
| LOW | The kind test under the lock is redundant for a named file | demonstrated | E4, argued; the test stays as defence in depth |
| LOW | T12 lacked the watch-list assertion | read | Added |
| LOW | The post-promotion stage window is not named in the per-window records | argued | Named in § 5, the stress seed's dated line and § 2.1 |

Rows 1, 3 and 5 read their R-IDs faithfully, per the review. Row 4 now says that the named entry is unchanged.

**Second pass, on the atomic-naming redesign** (ordered by the foreman). A fresh reviewer had the GOAL, the
revised PLAN, the gate and the folded prototype. It found no interleaving that breaks § 2.1 and no deadlock. Its
findings and folds:

| Sev. | Finding | Shown | Fold |
|---|---|---|---|
| MEDIUM | A wrong `open` that clears its watch right after `_ensure` returns passed P1's `regressions` and `mutants`: T3 and T4 inject at the source lock, after such a clear | demonstrated (a lab branch) | T16 and T17 (T14's route for a source-served open and an open that promotes; red on the baseline with `DID NOT RAISE`); M11 (`open`'s late clear) kills both, and M12 (a promotion's clear before its lookup) kills T17; `watch_leaf` now requires `watch.clear` in `_named` and `_adopt` only, and `_serve`, `_lookup_again` and `_held_path` never to name an entry |
| LOW | Row 2's "`_ensure`'s parent reads open the connection first" was false: on a thread's first database use, the connection and its pragmas run inside `_named`'s hold | demonstrated | `_named` opens the connection before the hold; `watch_leaf` checks the order; row 2, § 5 and `AGENTS.md` § 10 reworded, with the review's measurements (a fresh WAL read 0.000 s against an open writer; about 4000 opens per second, base equal to prototype, GIL build only) |
| LOW | E1 and E3 were called equivalent under any deterministic test; E3 is an R4 false refusal `test_replay.py` missed, which `purges_watched` catches statically | demonstrated for E3 | E1 and E3 relabelled "guarded statically", with the reason, here and in the gate; the E3 probe kept as T18, and M13 runs E3 for `_remove` (T18 is fast, a fraction of a second) |
| LOW | `_watch` overwrote the thread's naming watch with `None` instead of restoring it, and registered the watch before its `try` | argued | `_watch` saves and restores the previous naming watch, and registers inside its `try`; § 2.1 says why restore rather than assert; `watch_leaf` checks both |

## 5. Risks & open questions

- **A rolled-back purge** refuses its own named entry (row 3).
- **The read under the leaf mutex** (row 2) relies on WAL readers not waiting on a writer's transaction, which
  the second review measured (0.000 s against an open writer). A thread's first open no longer runs under it
  (F2). A WAL recovery could still make the read wait, holding the mutex, for up to the 5 s busy timeout, and
  every purge would wait behind it. No deadlock: nothing the reader waits for is held by a purger blocked on the
  mutex. Argued.
- **What stays open** is homed in the stress seed (§ 2.3): the two stage windows, handles keyed by entry id, and
  data-row snapshots.
- **Open for the gate** (each needs a ruling):
  - **Q-a** (row 2): accept a catalog read held under the leaf mutex, and a thread-local carrying the watch into
    `_ensure`. **Recommend accept**: it is the smallest change that meets the Outcome as worded and keeps the
    existing tests unchanged.
  - **Q-b** (row 3): R4's "other entries" does not cover the named entry's own rolled-back purge. **Recommend
    accept**: the alternative needs `db.py`.
  - **Q-c**: R2's "source-served open" and R3's "open that promotes" are decided by the copy `_serve` picks, and
    the two stage windows of § 2.1 are the Non-goals' "ancestor's round trip", not R2 or R3. **Recommend
    confirm**: the Non-goals already say so.
  - **Q-d** (row 5): the six names join the tables in this cycle. **Recommend accept**, as cycle 5's row 6.
  - **Q-e**: pins on drifted cites in eight seeds this cycle does not otherwise touch, in P1's commit, as records
    the change makes untrue (R8). **Recommend accept**.

## 6. Verification strategy

The engine is mount-free, so the evidence is the unit suite, driven through `verify/identity_gate.py` (modes in
its docstring). The profile's `verify` runs in its `suite` mode. No CLI drive applies: the behaviour is an
interleaving inside the engine, which `tools/temp_site.sh` cannot inject.

- **P1** `regressions mutants records suite`:
  - the 18 tests pass; the nine fix tests fail on the baseline with `DID NOT RAISE`, and the nine pins pass
    there;
  - existing tests, `test_model.py`, both `conftest.py`, `schema.sql` and `db.py` are unchanged, and
    `SCHEMA_VERSION` is the base's;
  - `_ensure` and `_adopt` keep their signatures;
  - one `catalog.purge` call, in `_purge`, called from the four base sites, each inside a transaction;
  - the watch mutex is a leaf but for `_named`'s one read, taken after the thread's connection is open;
    `watch.clear` only in `_named` and `_adopt`; the lookup side never names again; `_watch` registers inside its
    `try` and restores the previous naming watch;
  - `truncate`'s inline check is the base's plus the watch test, and from `_serve` through its recording is the
    base's code;
  - M1–M13 kill what they name, each for its cause;
  - the records say what § 2.3 says, and every seed's cites hold;
  - the full suite under `CI=true`.
- **P2** `suite seeds`: 20 Hypothesis seeds from committed state.
- **P3** Run A `ledger prepush`, after which the foreman pushes; Run B `ledger records ci` on the saved run
  record.

Measured on the planning prototype (development Mac, 2026-11-09), after both reviews' folds: the full suite under
`CI=true`, 548 passed in about 29 s; `tools/lint.sh` green; 210 engine tests. The mutant table above is the
planner's lab sweep (full `test_replay.py`, 103 tests, from a green tree); the gate's `mutants` mode re-runs it.

**The gate's dry runs** (2026-11-09), on the design after both reviews' folds, each phase's verify run from the root
of a lab built plan-first (`git clone --shared --no-checkout` of the cycle worktree; `fix/entry-identity-token` at
`5893e1f`; `origin/main` at `c7d2049`; a plan commit of this spec, then a build commit of the prototype's 14
changed files, byte-checked against the prototype), one gate at a time, with the gate from the cycle worktree:
- **P1** `regressions mutants records suite`: GREEN, 470 checks, 60 s wall (`regressions` 5 s, `mutants` 23 s,
  `records` 1 s, `suite` 30 s with 548 passed and 210 engine tests). M1–M13 each killed exactly what it names, for
  its cause; the nine fix tests failed on the baseline with `DID NOT RAISE` and the nine pins passed there;
  `cites_hold` checked 27 seeds (76 pinned cites, 3 unpinned that still match their base text).
- **P2** `suite seeds`: GREEN, 73 checks, 357 s wall (`suite` 31 s, `seeds` 326 s); 20 of 20 seeds from
  `git archive HEAD` trees, two workers, no `.hypothesis/` before or after. Under the 10-minute cap with 4 min to
  spare.
- **P3** Run A `ledger prepush`: GREEN, 212 checks, 34 s wall; the ledger reached the stress seed's deferral,
  the tree was clean, 548 passed, and no home-directory path is in the branch diff or messages. Run B's `ci`
  needs the pushed run record, so the dry run stops there.

The first draft's dry runs (P1 47 s, P2 305 s, P3 Run A 29 s) and the gate subagent's on the first fold sized the
phases; they are not evidence for this design.
