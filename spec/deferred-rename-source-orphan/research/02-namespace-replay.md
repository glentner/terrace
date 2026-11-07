# Research 02: namespace replay, why `flush()` diverges and whether current-path replay converges

Read-only brief for `code-plan`. The lab was a throwaway copy of `src/` and `tests/` at HEAD, outside the
repo, run on macOS under Python 3.15.2. Line numbers are HEAD `src/sosfs/engine/overlay.py` unless a file is named.

## 1. Reproductions at HEAD (all five fail)

| Case | Result at HEAD | Mechanism |
|---|---|---|
| **C1** EACCES | `flush()` raises `PermissionError` | `/d1` is a *file* (`b.dat` renamed onto it) by flush time. The historical `rename /d2 → /d1/s1` row runs `materialize_dirs(…, detail['new'])` (925), which looks the ancestor up **by path** (`transfer.py:materialize_dirs`). It gets the file's mode `0o644`, makes `project/d1` with that mode, and the rename into it fails at 928. |
| **C2** stray dir (both variants) | `flush()` ok, source keeps `d1`, and a remount adopts it | `rmdir /d1` purged the never-flushed dir and **cancelled its mkdir row** (`_remove` 504). The child's mkdir replays at its queue-time path (`op_path = detail['path']`, 905; `journal.py:170-172`). `materialize_dirs` (910) re-creates `/d1`, which no entry owns afterwards. |
| **C3** vacate ENOTEMPTY | `mkdir /d1` raises 66 | `_vacate` runs only the tombstone's own `rmdir` (983-993). The child's earlier queued `rmdir /d1/s1` has not run. |
| **P4** rename over an empty dir whose child's rmdir is queued | **raises 66, where POSIX succeeds** | `_remove(target, replaced=True)` (424) → `_purge_source` rmdirs `source_path` at once (523). The child is still physically there. |
| **P5** moved-out child, then rmdir and mkdir of the parent | **raises 66** | Same as C3. The child's queued `rename /d1/s1 → /d2` hasn't moved it out when `_vacate` rmdirs `/d1`. A rename-target variant also fails: `rename /a.dat → /d1` over a removed `/d1` whose child was renamed out. |

Further HEAD defects found on the way:
- **Intent leak.** `_vacate` (985-997) and `_purge_source` (516-527) catch only `FileNotFoundError`. After C3 fails, `journal.pending()` still holds `('commit', {'op': 'rmdir', 'source_path': '/d1'})`. That breaks "queue drains" (R3, R5).
- **P6b: wrong-tombstone purge.** Sequence: `create /a.dat`, `rename /a.dat → /b.dat`, `create /a.dat`, `commit_upstream /a.dat`, `unlink /a.dat`, `flush`. The first rename row purges *whatever* tombstone sits at `detail['old']` (959-960). Here that is the new file's unlink tombstone, so the purge cascades away its queued `unlink` row. `flush` then raises `LookupError` from `settle` at 892-893 (the entry is gone and so is the row), and `a.dat` stays orphaned on the source. Only R7's commit rule can reach this, so the model will hit it once R7 lands.
- **D2 (R2 shape).** `commit_upstream /d2/a.dat` inside a renamed `/d1 → /d2` writes at the logical path. `transfer.copy` calls `materialize_dirs` at the logical path (`transfer.py:91`), which leaves a stray `/d2`. The queued dir rename then fails with ENOTEMPTY in `flush`.

## 2. The candidate, prototyped and attacked

I built a lab prototype of the planner's design, about 150 lines: `_target` (T), `_place_ancestors`, current-path `rename` and `mkdir`, `meta` only at a non-NULL `source_path`, the pure-tombstone purge rule, a generalized `_vacate(path, mover)` that replays the queue in order through the bound, a `_purge_source` pre-replay for directories, and data commits to the source at `source_path`, else T(E). The commit side went through a `dest_path` argument on `transfer.copy`.

**Results**
- All five cases above converge. So do D1 (the seed probe), D2, a three-rename swap, an `a→b→c` chain with both names reused, a rename back to the original name, a dir moved into a later-created dir and then renamed, and the child-before-parent case below.
- Existing `tests/engine -m unit`: 111 passed.
- `test_model`: passes under `CI=true` and under `--hypothesis-seed` 1-20.
- An extended machine added three rules (commit one entry; one data hop; one poll of the oldest namespace row), invariants (unique `source_path`; drained ⇒ source == oracle) and a remount-adoption check at teardown. It passed seeds 1-13 at 300-500 examples.
- Caveat: that machine is **weak evidence**. HEAD also passed it on 8 seeds (about 2.7k examples), yet the same machine fails D1 at once when driven step by step (`create, flush, overwrite, rename, commit`). Uniform rule choice almost never builds the four-step window. R7 needs deterministic example tests, or rule weighting, alongside the rules. Preconditions would be the wrong tool because R8 forbids them.

**Where the candidate as written breaks, and the amendments it needs**
1. **The `mkdir` row must place ancestors too.** Sequence: `mkdir /d2, mkdir /d1, rename /d2 → /d1/s1`. Flush replays B's mkdir first, at T(B) = `/d1/s1`, before A's mkdir row. Placing missing ancestors at their T **by entry id and mode**, and recording `source_path`, makes A's later mkdir a no-op. Never resolve an ancestor's mode by path; that is C1's cause.
2. **The tombstone rule ("deleted, NULL source_path, no queued rows") still unmasks (P6a).** Present at HEAD and in the candidate as written. Sequence: `mkdir /d1`, flush, `create /a.dat` (staged), `rename → /b.dat` (r1), `create /a.dat` G, `commit_upstream` G, `rename /a.dat → /d1/a.dat` (r2). Running r1 alone, as a scheduler poll does, purges G's *pure* rename tombstone while G's object still sits at `/a.dat`. A `getattr('/a.dat')` under an unlisted root then adopts a phantom. Fix: also require that no entry has `source_path == old`; the prototype's strict mode converges. Alternatively, store the tombstone's id in the row detail and purge only that id.
3. **Tombstones never carry rename rows.** `_remove` cancels every row before it queues the removal (504). So a removal tombstone has exactly its removal row, and a rename tombstone has none. T() of a deleted entry is never needed. The candidate can say "a tombstone's only row is its removal" rather than handling tombstone renames, and the prototype's skip-deleted flag was moot.
4. **Locking.** Generalized vacate executes *other* entries' rows under only the caller's path lock. A concurrent `_commit_hop` for entry F can compute `dest_path = F.source_path`, lose a race to vacate moving F, then `put` at the stale path and overwrite `source_path`. That is a fresh orphan. Replay needs one namespace-replay mutex, which data commits to the source also take, or the per-entry lock of each row's entry. `commit_row` must also tolerate a row someone else settled. HEAD's `flush` takes no locks either; the candidate widens the window into the foreground.
5. **Rollback on failure.** `_vacate` and `_purge_source` must roll back on `BaseException` (the intent leak above). `_execute_namespace`'s `entry is None` branch must not raise on a cascaded row.
6. **Cost.** Any rename or create onto a path whose T is owned by an entry with a queued departure replays the whole queue prefix synchronously. That includes unrelated mkdirs and renames, and S3 copies. Swaps and chains now move objects on the foreground path. This is the "unbounded namespace work" GOAL Q4 warns about, in a new place. It is bounded by queue length and correct, but it should go in the deviation table.

No break found in the core claim. I checked swaps, chains, rename-back, a dir moved into a later dir, subtree moves with pending child rows, and P4/P5-style physical children. In each, every live entry's T(E) stays free or self-owned, and in-order replay never collides. The reason is that T changes only through vacate-guarded occupation or through an ancestor's subtree move, which carries its physical contents along.

## 3. Alternatives

- **Historical replay, patched per class.** Not convergent by construction. `_remove` cancels a purged entry's rows (504), so the queue is a *lossy* history. Replaying it faithfully re-creates ancestors nobody owns (C2), and the kind or mode of reused paths has changed (C1). Fixing C1's mode leaves `materialize_dirs`'s stray `/d1` in place, so the next row's `rename /b.dat → /d1` collides with a dir. Each patch needs ownership tracking for materialized ancestors, which is current-path replay by another name.
- **"Every data commit runs the namespace queue first."** It fixes D1 only if replay is already correct, and HEAD replay isn't (C1, C2). It does nothing for the synchronous paths (C3, P4, P5). It gives every data commit unbounded namespace work and overrides the namespace rows' policy clocks: the same objection as GOAL Q4. With current-path replay, writing data at `source_path`, else T(E), makes it unnecessary.

## 4. AGENTS.md § 5 sentences the candidate changes

- "Committing a rename upstream moves the object from `source_path` to the new path and only then advances it" → *the new path* becomes the entry's current target T(E), not the row's `detail['new']`. A chain collapses into one move.
- "Adoption sets it to the entry path. `create` sets it only when the file lands straight on the source." → incomplete. A data commit sets it (`_commit_hop`, 808, today to the logical path), and under the candidate so do mkdir rows and ancestor placement.
- "**`_vacate` is the one place a namespace op jumps its policy clock**, executing the queued source removal synchronously" → it now executes a queue *prefix*, including other entries' renames and mkdirs, and `_purge_source` also jumps clocks. "The one place" survives only if both go through a single replay-through helper.
- "`_populate` may safely list the *logical* path — a renamed directory is never re-listed" → still holds. An unlisted dir is adopted and never renamed, nor is any ancestor (`_populate_tree`, 260), so its logical path equals its `source_path`.
- Outside § 5, these are overturned: the `journal.queue` docstring ("reproducing the namespace history faithfully", `journal.py:170-172`); the `_execute_namespace` comment on mkdir's "queue-time path" (900-904); and PLAN.md Deferred-op replay's "moves the source object from the committed source path to the new path".
