# Research 01 — the `open`/`_serve` contract under the source lock

Tree: `58151ec` (baseline, no fix). Anchors are `src/sosfs/engine/overlay.py` unless named. Lab: a throwaway
`mktemp -d` outside the repo; no repo file touched but this brief.

**Headline.** The baseline is worse than the records say. A queued swap through two renames makes `open`
hand back **another entry's bytes**, and a promotion **copies them into the file's clean stage residency**,
which survives the drain (§ 6, e1/e2). `AGENTS.md:669` ("Loud, never a wrong object") and GOAL § Problem
("found no case where `open` acted on another entry's object") are wrong at baseline. R4 is a live,
reachable defect, not a hypothetical. The planner's draft fixes all eight probes (§ 6, prototype).

## 1. Lock order and the § 10 "nothing holding it" rules

Under the source lock the draft reaches `catalog.get` (one SELECT), `_held_path` (pure, 802-808),
`open_direct` (an `os.open`), and in `_serve` `transfers.copy`. That copy runs on the calling thread
(`transfer.py:79-118`; `submit` has no caller in `src/`). It reaches `catalog.get`, `journal.begin`
(its own transaction), `source.stat`, `materialize_dirs` (`catalog.get`, `dest.stat/mkdir`),
`open_read`/`dest.put`, and one `db.transaction()` (`transfer.py:105`). None of these takes a path lock
or calls `_ensure`/`getattr`/`readdir`. None calls back into the engine, so nothing acquires the
source lock inside a transaction. `db.Database._lock` guards only the connection map (`state/db.py:115`).
The draft takes the source lock after `_ensure`, so `_adopt`'s transaction (237) stays outside it.

Every other holder takes path lock, then source lock, or the source lock alone:
- path lock, then source lock: `rename`→`_vacate` (425, 443, 1201), `create` (344, 354), `_commit_hop`
  (859, under a caller's path lock), `_record` (784-785), `truncate` (717, 723);
- source lock alone, never a path lock: `flush`/`commit_row`/`drain`→`_execute_namespace` (979).

`evict_copy` takes no source lock (1228). The scheduler (`writeback.py:97,116`) and `drain.py:100` reach
the engine only through `commit_row` and `commit_upstream`. `open` would add one more path-lock-then-source-lock
edge, so no cycle forms. `truncate` already holds the lock when it calls `_serve`, and the `RLock`
re-enters (101). Empirically, `tests/engine -m unit` (164 tests, `test_replay.py` and the `truncate`
tests among them) passes with the draft monkeypatched in.

**Verdict:** no lock-order break and no deadlock. The draft keeps every sentence of § 10.

## 2. Can the `_serve` decision change under `open`'s path lock?

Residency adds and drops happen at `_adopt` 244, `create` 371, `_remove` 514, replayed removal 1063,
replayed rename 1076, `evict_copy` 1245, and `transfer.copy` (`transfer.py:108`).
- Every one except 1063 and 1076 runs under the path lock of the file it changes, so `open`'s lock
  serializes it. Those callers are `_commit_hop` (`commit_row` 832, `commit_upstream` 897,
  `commit_to_shared` 934), eviction 1228 and `unlink` 382.
- 1063 drops the source residency of a tombstone. `entry.path` is `UNIQUE` (`schema.sql:60`), so it
  is never the live file's.
- 1076 adds a source residency only to a file that has a `source_path` but no source row. Every site
  that sets `source_path` also adds the row, so this is defensive. Even when it fires, it can only turn
  "no copy" (`EngineError`) into "source". It never moves the fastest copy between source and stage.
- A user rename moves residencies with the catalog (`catalog.rename`). For this file that is either
  its own rename (blocked by the path lock) or an ancestor's (the non-goal).

Replay acts only on `self.source` (`_execute_namespace_locked`, `_place_parents`, `_purge_source`). It
never touches a stage or changes `entry.path`. So the unlocked stage branch, and a stage-to-stage
promotion, never touch the source.

**Verdict:** only the non-goal ancestor rename can change "source serves" against "a stage serves".
Deciding `serving` before the lock is sound, and the stage branch needs no source lock.

## 3. Q1: hold the lock across the copy, or pin an opened object

How each source adapter's opened read survives a replay:
- `posix.py:120-125` returns a real `open()` file object. A replay's `os.replace` (`posix.py:205`)
  moves the name and not the inode, so a pinned stream keeps reading the right bytes.
- `s3.py:173-179` and `archive.py:503-505` buffer the whole object into `BytesIO`, so their stream
  survives anything trivially.

On S3 and archive, though, the whole download is inside `open_read`. Pinning would still hold the lock
across it and save only the local `dest.put`.

Pinning needs an API change to `transfer.copy`, which is on the high-blast-radius placement row. Today
`copy` does `journal.begin`, then `stat` and `open_read` inside its bracket (`transfer.py:86-101`). Any
of these would reshape that bracket for all three callers (662, 862, 866):
- a caller-supplied `stream=`/`stat=`;
- a prepare and finish split;
- an `opened` callback that releases a lock taken in another frame.

Holding the lock is what the signed-off `truncate` already does through this same `_serve` (727), so
`_serve` keeps one behaviour for both callers.

The cost of holding it: every replay, `create`/`mkdir`/`rename` that reaches `_vacate`, `release`/`fsync`
of a source-served copy, and handle-less `truncate` waits out the promotion. The § 10 text must state
that delay. **Records caveat:** "as large as the stage admits" (`AGENTS.md` § 10, about `truncate`) is
not a bound when the source is not passthrough. `eligible_tier` (183-212) then falls back to the fastest
POSIX stage, whatever its `bypass_size` or quota, so a promotion off an S3 source can be any size. The
new sentence, and arguably the old one, should say so.

**Verdict:** hold the lock across the copy. It needs no `transfer.copy` change, matches the signed-off
`truncate`, and pinning buys nothing on S3 or archive. Record the delay, including the unbounded size
on a source that is not passthrough.

## 4. The identity gap and R4

`open` holds the path lock on `path`, so `unlink(path)` and `rename(x, path)` block. For the path+id+kind
re-lookup to accept another file, the file must leave `path` and a file with its reused id must arrive
there. Both need a user rename of an ancestor mid-open (the non-goal). Replays move source objects and
never `entry.path` or ids. **Every identity-gap interleaving needs the non-goal; none needs only replays.**

R4's IF-clause is independent of the gap and holds with replays alone. In e1/e2, `b`'s `_vacate` replays
`a`'s queued rename (`/a.bin`→`/t.bin`), and the flush moves `b`'s object onto `/a.bin`. `a` keeps its
id and path, and only its `source_path` changes. The draft's re-read under the lock follows that change.

A re-lookup by id (`by_id`, not deleted, kind file) would survive an ancestor rename. It would widen the
gap to "reused id anywhere", though, and break symmetry with `truncate`'s signed-off check.

Behaviour shift inside the non-goal window: a user ancestor rename landing after `_serve` and before
`open`'s re-lookup turns today's success (`open_direct` at the unmoved object) into `ENOENT`. That is a
legal linearization. Landing before `_serve`, it raises `EngineError` today and after the fix (probe f).

**Verdict:** the gap needs the non-goal. R4's IF-clause holds without it and is closed by the re-read.
Keep path+id+kind.

## 5. `truncate`'s signed-off property and the `AGENTS.md:668` row

The `truncate` body stays byte-identical. Under the lock it already holds, the new `_serve` re-reads
the entry `truncate` just checked. Replays are excluded, so the result is identical, as are the
decision, held place, `O_WRONLY|O_NOFOLLOW` and recording (REVIEW.md:626-630). The draft only uses
`_held_path(current, source)` where `_serve` used `current.source_path`. For a source copy, `source_path`
is non-NULL, so the two are the same value.

In the row's own window, an ancestor rename that has passed `_vacate` can commit `catalog.rename`
under `truncate`'s source lock. One new outcome appears: landing between `fastest_copy` and the
re-lookup, `_serve` now raises `ENOENT` before `journal.begin`. Today, landing at the same point raises
`LookupError` at `transfer.py:85`, also before the intent. The row's other landings (`EngineError`, a
mid-copy `LookupError` leaving the intent unresolved, `mark_dirty`, the recording) stay possible and
unchanged.

**Verdict:** the property holds, and the row stays accurate. Optionally add "or `_serve`'s re-lookup
raise `ENOENT`" to its list of earlier landings.

## 6. Baseline probes

Each probe wraps `engine._ensure` and fires once in a helper thread that is joined before `open`
continues. The stack is `source_served` (stage `bypass_size=100`), with `walk` and `Clock`.

| # | Sequence | Baseline | Draft (prototype) |
|---|---|---|---|
| a | 1000 B `big.bin`, own rename queued, flush | `FileNotFoundError` `[Errno 2] … project/big.bin` | handle, right bytes |
| b | `data/big.bin`, `/data→/moved`, flush | `FileNotFoundError` `… project/data/big.bin` | handle, right bytes |
| c | one tier, `data/a.txt`, `/data→/moved`, flush | `FileNotFoundError` `… project/data/a.txt` | handle, `alpha` |
| d1 | 50 B, own rename, flush | `TierError: No file at /small.bin in tier project` | stage handle, right bytes |
| d2 | 50 B, ancestor rename, flush | `TierError: No file at /data/small.bin in tier project` | stage handle, right bytes |
| e1 | swap 1000 B: `a→t` queued; action `rename(b→a)`; `flush` | **handle on `/t.bin` reads `b`'s bytes** | `a`'s bytes |
| e2 | same swap, 50 B | **stage copy of `/t.bin` holds `b`'s bytes, clean; after flush the overlay reads `t.bin`=`bbbb` while the source holds `aaaa`; `assert_converged` fails** | `a`'s bytes; converges |
| f | user `rename /data→/moved` mid-open (non-goal) | `EngineError: No resident copy of /data/big.bin` | same |

In every case the baseline leaves no pending intent (d1 and d2 roll back), and every case except e2
converges. A variant of e that only renames, with no flush, raises `FileNotFoundError` on `project/a.bin`.

**Test hint (R5).** Inject by wrapping `_ensure`, as the probes do, not with `Interleaved`. The fix-removal
mutant takes no source lock in `open`, so `Interleaved` would never fire there. The `_ensure` hook fires
on both the mutant and the fix.

**Verdict:** R1 and R2 reproduce exactly as GOAL states. R4 reproduces silently, and e2 corrupts state.
The draft passes all of them.
