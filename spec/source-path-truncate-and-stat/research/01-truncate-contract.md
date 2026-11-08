# Research 01: `truncate`'s contract, lock order, and other logical-path touches of the source

At `0a88f30`. Line numbers refer to `src/sosfs/engine/overlay.py` unless a file is named. Probes ran in
throwaway tmpdir labs (`Clock`, `build_spec`, `seed`), with `truncate` monkeypatched to the design
below. Nothing was committed.

## Findings

1. **Current no-handle contract (702-723).** It takes `locks.hold(path)` (702) and runs `_ensure`
   (703), the ENOENT and EISDIR checks (704-707), and `_serve(entry)` (708), which may promote. It
   then runs `upstream_of(serving)` (709) and `now` (710), and calls `journal.mark_dirty` only when
   an upstream exists (711-712). Next comes `open_direct(_pure(path), O_WRONLY)` on the logical path
   (713), then `ftruncate` and `close` (714-717), then `get_residency(path, serving)` (718). One
   `db.transaction()` (719-723) holds `update_meta(path, size, mtime)`, `update_residency(path,
   serving, …)` and, for a non-source tier only, `adjust_usage`. Callers: the FUSE adapter calls the
   no-handle branch when the kernel gives no `fh` (`src/sosfs/fuse/adapter.py:206-211`; `:209` is
   the handle branch). Engine `open` calls `truncate(path, 0, handle=handle)` for O_TRUNC (647), so
   O_TRUNC never reaches the no-handle branch. No other caller exists in `src/`.
2. **Lock order is safe.** The order is path locks → source mutex → `_handles_mutex` → SQLite (97-100,
   AGENTS § 10). Truncate already holds the path lock, so taking `_source_lock` next follows that
   order. Under the source lock the branch would run `catalog.by_id` and `get_residency` (reads),
   `open_direct`, `ftruncate` and `close` (filesystem calls), and `db.transaction()` around
   `update_meta`, `update_residency` and `adjust_usage`. None of these takes a path lock or calls
   `_ensure`, `getattr` or `readdir`. The lock is taken outside the transaction, never inside it.
   This is exactly `_record`'s shape (764-779). `mark_dirty` never runs for a source-served file:
   `upstream_of` returns None at position 0 (172-176), so 711 skips it. Keep `_ensure` and `_serve`
   outside the source lock (`_ensure` is forbidden under it). `mark_dirty` stays where it is,
   before the bytes change (§ 1). It only fires on the staged branch.
3. **Who writes `source_path`, and under which lock:**
   - `_adopt` sets it to `path` on insert (242), under the caller's path lock only.
   - `create` straight onto the source sets it to `where`, under `_source_lock` (354-369).
   - Replay sets a `mkdir`'s placement (1044) and calls `advance_source_path` for a rename (1047).
     That call rewrites every descendant's prefix (`src/sosfs/state/catalog.py:184-196`). Both run
     under `_source_lock` (956) and take **no path lock**.
   - `_place_parents` sets it under `_source_lock` (1107).
   - A commit to the source sets it through `transfer.copy` (`src/sosfs/engine/transfer.py:116-117`,
     from 838-840), under `_source_lock`.

   Replay is reachable while truncate holds only its own path lock: the scheduler's `commit_row`
   (802-804), `flush` (932-934) and `drain`, and `_vacate`/`_replay_through` from another path's
   `create`, `mkdir` or `rename`. `_vacate` runs a queue **prefix** (1163-1169), so it can carry an
   unrelated ancestor rename. An ancestor's rename can therefore move the object and rewrite
   `entry.source_path` between `_ensure` and the open. **The re-read under the lock is necessary.**
   Probe: a queued `/data → /moved` rename, replayed between `_serve` and the open. Resolving from
   the `_ensure`-time entry raises `FileNotFoundError …/project/data/big.bin`. Re-reading by id
   inside the lock succeeds (4, 4). After the open, the fd follows the inode across a rename, so the
   lock must cover resolve→open at minimum.
4. **Can serving be the source while `source_path` is NULL?** Not on a live file through current
   writers. `_serve` returns the source only when the fastest copy is the source (658-665). A
   source residency is created only alongside a non-NULL `source_path`: adoption (242-245),
   create-on-source (366-371), a commit (`transfer.py:105-117` with `source_path=where`), or the
   rename replay (1048-1054, which requires `held is not None`). Replay never NULLs a live entry's
   `source_path`; only a purge of a removal does. **Fallback:** `entry.path`, identical to `open`
   (640) and `_stat_held` (788). It covers degenerate or old state only.
5. **The queued swap.** Probe at `bypass_size=100`, 1000-byte `a` and `b`. The second rename's
   `_vacate('/a.bin', mover=b)` (442, 1179) replays `a`'s row, so `t`'s object moves to `/t.bin`
   before `b` takes the name. Then `/t.bin` has source_path `/t.bin` and `/a.bin` has source_path
   `/b.bin`, and only `b`'s rename stays queued. With the fix, truncating `/a.bin` cuts `b.bin` to 4
   bytes and leaves `t.bin` at 1000. After flush, `a.bin` holds `BBBB` and `t.bin` holds `AAAA…`:
   entry b's own object, and no other entry's. At HEAD the call raises ENOENT on `project/a.bin`.
   The first rename cannot stay unvacated, because `_vacate` always runs before a name is taken
   (326, 348, 442). A logical-path wrong-object hit would need another entry's object at the
   logical path. `_vacate` frees `_target(path)` before any name is taken. A place `S/n` can be
   claimed only by an entry named `n` whose logical parent holds `S`, and the logical prefix of a
   renamed subtree was vacated when its outermost ancestor took it. **I could not build a
   wrong-object case.** The hazard at HEAD is ENOENT, which matches the GOAL's own finding. Fix it
   on § 5 grounds anyway.
6. **Other touches of the source tier:**
   - `_ensure`'s adoption stat (228) and `_populate`'s listing (258) use the logical path. This is
     documented-safe: § 5 makes `_populate_tree` load-bearing, renamed subtrees are `listed`, and
     tombstones mask old paths.
   - `readlink` (312-317) reads the catalog only. Safe.
   - `_set_meta` excludes the source from `holders` (598). The source is reached by the `meta` row
     at `source_path` (1017-1020). Safe.
   - `rename`'s staging moves (453-454) and `_remove` (494-495, 505) skip the source. Removals use
     `source_path` (517-550, 995-1001). Safe.
   - `_commit_hop` writes at `source_path` or the target, under the lock (836-840). Its holder stat
     (850) is never the source. `evict_copy` refuses the source (1203). Safe.
   - `transfer.copy` uses `src_path` and `dest_path` as passed. `materialize_dirs` looks up modes by
     physical ancestor path (`transfer.py:35`), but on a commit the ancestors already exist. Safe.
   - `getattr`'s unlocked live stat (283-292) is documented-safe in its docstring.
   - `evict.py`, `drain.py`, `writeback.py` and `rescue.py` touch no tier directly.
   - **One live defect, threaded and not single-thread reachable.** `open` (640) and `_serve`'s
     promotion (661) resolve `entry.source_path` from the entry `_ensure` read, **outside
     `_source_lock`**. Under the same injection, a replay between `_serve` and the open makes `open`
     raise `FileNotFoundError …/data/f.bin` on a source-served file. A replay before `_serve` on a
     promotable file makes `transfer.copy` raise `TierError: No file at /data/f.bin`. Both fail
     loudly; neither touched a wrong object. The open-violations table and the
     `freethreaded-concurrency-stress` seed do not name this. It is the same race this fix closes
     for `truncate`.
7. **No `SCHEMA_VERSION` change** (the `source_path` column and `by_id` already exist). **No new
   `_replay_through` caller:** the design reads a place and never forces a row. Neither needs
   escalation.

## Recommendation

In the no-handle branch, keep 702-712 unchanged: path lock, `_ensure`, the checks, `_serve`,
`upstream_of`, `now`, and `mark_dirty` when there is an upstream. Then:

```python
on_source = serving == self.source.name
with self._source_lock if on_source else contextlib.nullcontext():
    entry = self.catalog.by_id(entry.id)            # a replay may have moved it since _ensure
    where = entry.source_path if on_source and entry.source_path is not None else entry.path
    fd = self.tiers[serving].open_direct(_pure(where), os.O_WRONLY)
    ... ftruncate / close / get_residency / transaction (unchanged; adjust_usage keyed on `not on_source`)
```

The `where` expression is exactly `_stat_held`'s (788). A helper such as `_held_path(entry, tier) ->
str` could back both `_stat_held` and truncate. `open` (640) could adopt it too, but that is out of
scope unless the plan takes the race in finding 6. Span the lock over resolve, open, ftruncate and
the catalog write, as `_record` does. Only resolve→open is strictly needed, but the wider span costs
nothing and reads as the same rule. The re-read is necessary (finding 3). Fall back to `entry.path`.
This passed all five R1 rows, the swap, and the injected-replay race.

## Hazards for the plan

- **Do not move `mark_dirty` into or after the source-lock block,** and do not reorder it against
  `open` or `ftruncate`. `truncate` is high-blast-radius for metadata before acknowledgment.
- **Never call `_ensure` or `_serve` inside the source lock.** `_serve` can promote, and it must keep
  its current place.
- **Catalog writes keyed by `path`, not `entry.path`.** Path locks do not cover descendants (the
  `freethreaded-concurrency-stress` seed). A concurrent ancestor `rename` can change the logical
  path under truncate's lock. `update_meta` and `update_residency` keyed by a stale `path` then
  silently update nothing (`src/sosfs/state/catalog.py:156`). This predates the fix. `_record` keys
  on the re-read `entry.path`. Either choice is defensible, but pick one deliberately and do not
  widen the race.
- **Holding `_source_lock` makes truncate wait for any in-flight source-bound commit or replay**
  (§ 10's latency note). Add `truncate` to § 10's list of source-lock takers and to § 5's
  "Reading the source resolves through it too" paragraph.
- **The handle branch (696-700) is already correct,** because the fd was resolved at `open`. It
  needs no change.
- **For § Known open violations:** after this fix, the § 5 row can drop `truncate`. It keeps the
  wrong-size databases (handed to `issues/audit-command.md`, R4). The `open`/`_serve` unlocked
  `source_path` resolution in finding 6 is a remaining threaded § 5/§ 10 gap that is not recorded
  anywhere. Either name it, for example under `issues/freethreaded-concurrency-stress.md`, or take
  it into scope.
