# Research 01 — the data-commit path and where a commit to the source writes

Read at the worktree HEAD (code identical to `141aa86` in these regions). Every probe below ran in
a throwaway lab (two POSIX tiers, `tests/engine/conftest.py` helpers, no mount). "P-proto" means
HEAD with `_commit_hop`'s source hop monkeypatched to write at P(E), materializing missing ancestors
at their own P() with catalog mode, each mkdir intent-bracketed, and recording `source_path` in the
confirming transaction. Its mkdir replay was also patched to settle without acting when
`source_path` is already set.

## 1. What must change for P(E)-based writing

- **`transfer.copy` writes and materializes at the logical path, for every destination.** It
  materializes at `transfer.py:91` and puts at `:93`. `src_path` (`:65`, `:76`) only covers the
  read side. It needs a `dest_path` (default `path`) used for the `put`. Stage destinations must
  keep the logical path, since stage copies move with the catalog at rename time
  (`overlay.py:436-447`). Only the source hop takes P(E).
- **`materialize_dirs` cannot simply take `dest_path`.** It looks up modes with
  `catalog.get(str(ancestor))` (`transfer.py:35`), and a physical path like `/d` maps to a masked
  tombstone or nothing, so the mode falls back to `0o777`. Source materialization has to walk the
  entry's catalog ancestors, computing each ancestor's P() and taking its mode from the catalog.
  The existing logical call has to be skipped for the source hop. Otherwise it creates the logical
  `/e` on the source next to the physical `/d`, as HEAD does in case (c).
- **Today the source-path update is not in the transaction that confirms the copy.** `copy`
  confirms with residency and usage in its own transaction (`transfer.py:97-108`). `_commit_hop`
  then opens a second one for holder clean, settle and `source_path = path` (`overlay.py:794-808`).
  That breaks § 1 and `PLAN.md:210`. If a crash lands between the two, the source holds the bytes
  while `source_path` is NULL. An `unlink` then purges the entry with no queued removal
  (`overlay.py:500-510`), and the bytes are orphaned.
  - Under P(E) the update is needed only when `source_path` is NULL; when it is set, P(E) equals
    it and nothing changes.
  - Minimal hook: give `copy` a callable, or a `{field: value}` dict, that runs inside the confirm
    transaction before `journal.confirm`.
  - `copy`'s intent has no detail (`transfer.py:79`). Add `{'dest_path': P}` so a future audit
    knows where the act landed.
- **Residency and usage need no change.** Residency rows are keyed by entry
  (`catalog.py:195-205`). The source counter takes the delta against the previous source residency
  (`transfer.py:98-107`).
  - HEAD makes that counter drift: an adopted, renamed and committed file overwrites the residency
    size while the orphan stays on disk, so the `usage()` oracle counts two files.
  - P(E) overwrites in place, which keeps the counter truthful.
- **A materialized ancestor makes its queued `mkdir` row stale.** `_execute_namespace` mkdirs at the
  queue-time path. `Journal.queue` always records `detail.path` (`journal.py:177`), so the replay
  uses that recorded path, not the current one (`overlay.py:905`, `:909-914`). It swallows
  `FileExistsError` and then overwrites `source_path = op_path` (`:954`).
  - Probe `mkdir /n; rename /n /m; create+write /m/f; commit_upstream('/m/f'); flush()` raises
    ENOTEMPTY (`/n -> /m`). At HEAD it does so because of the logical write. Under P-proto it does
    so too, unless the mkdir replay settles as a no-op when `source_path` is set.
  - With that skip, P-proto converges.

## 2. Callers that reach a source commit

All of them funnel through `_commit_hop`, so a change there covers every route:

- `fsync` under `"commit"` → `commit_upstream` (`overlay.py:707-708`). HEAD orphans the old
  object (probe: source `{x.dat: original, y.dat: FSYNCED!}`). P-proto leaves `{y.dat}`.
- `EvictionManager._drop_dirty` → `commit_upstream` (`evict.py:151`).
- Scheduler `poll`/`on_commit` → `commit_row` → `_commit_hop` (`writeback.py:94-97`, `:113-116`;
  `overlay.py:767`). Probe: write at t=105, rename at t=138, `poll(now=166)` settles only the
  data row.
  - HEAD ends with `{x.dat: original, y.dat: NEWDATA!}` and an empty queue.
  - P-proto ends with `{x.dat: NEWDATA!}`, then `{y.dat: NEWDATA!}` after the rename replays.
- `commit_to_shared` → `_commit_hop` (`overlay.py:864`), and through it `rescue.py:68`. It reaches
  the source only when the source is the first shared tier.
- `flush` (`overlay.py:876-886`) and `drain` (`drain.py:94-106`) run every namespace row first, so
  P(E) equals the logical path by the time they commit. They benefit, but only trivially.

## 3. Physical-path collisions (probed at HEAD and under P-proto)

- **(a) Reuse of the renamed-away name.** Adopt `/x.dat`, rename it to `/y.dat`, create and write
  a new `/x.dat`, `commit_upstream('/x.dat')`, `flush()`.
  - **HEAD: silent loss of the original bytes.** The commit overwrites source `/x.dat`, whose
    original bytes `/y.dat` still points at. The rename replay then moves the new bytes to `y.dat`.
  - `advance_source_path` (`catalog.py:188`) also rewrites the innocent new entry's `source_path`
    to `/y.dat`. Final state: source `{y.dat: NEW-X}`, both entries read `NEW-X`, both have
    `source_path = /y.dat`, and the queue is empty.
  - **P-proto: identical.** The new entry has a NULL `source_path` under `/`, so P = `/x.dat`.
- **(b) Unlink after rename, then reuse.** Same setup, then `unlink /y.dat`.
  - `_remove`'s `journal.cancel` drops the queued rename (`overlay.py:504`, `journal.py:194-198`).
    It queues an unlink that targets `source_path = /x.dat`.
  - The new `/x.dat` is created; `_vacate` purges only the rename tombstone, which has a NULL
    `source_path` (`overlay.py:433`, `:978-997`). The new file is committed.
  - **HEAD: silent data loss, through `flush` and through a scheduler `poll` alike.** The unlink
    deletes the new bytes from source `/x.dat`, and the source becomes `{}`.
  - The catalog still says the project residency is clean. After the scratch copy is evicted, the
    next `open` raises `TierError('No file at /x.dat')`.
  - **P-proto: identical.**
- **(c) R2, a file inside a renamed directory.** Source `/d/{a,b}`, `rename /d /e`, write `/e/a`,
  `commit_upstream('/e/a')`.
  - HEAD materializes the logical `/e` and writes `/e/a` there. `flush()` then raises
    `OSError(66, ENOTEMPTY)` on `/d -> /e` (`overlay.py:928`).
  - P-proto writes `/d/a`, which `/e`'s `source_path = /d` resolves to. `/e/b` stays readable from
    `/d/b`. After `flush()` the source holds `{e/a: ALPHA2, e/b: beta}`, no `/d` remains, and every
    `source_path` is advanced. R2 holds.
- **(d) The collision is on P(X), not X.** Source `/d/a`; `rename /d /e`; `rename /e/a /z`;
  create and write `/e/a`; commit; `flush()`. P(`/e/a`) = `/d/a`, which `/z` still occupies.
  - HEAD raises ENOTEMPTY.
  - P-proto plus a uniqueness check on the logical X loses `/z`'s bytes: `/z` and `/e/a` both read
    `NEW-A`.
  - P-proto plus the check on P(X) converges: `{e/a: NEW-A, e/b: beta, z: alpha}`.

**The uniqueness rule must be enforced when the user op runs, and it must be keyed on P(X).** The
rule: no two entries, live or tombstoned, share a `source_path`, and no new name's P() equals an
existing `source_path`. Prototype: extend `_vacate(X)` to find any entry F ≠ X with
`source_path = P(X)`, through the `entry_source_path` index (`schema.sql:81`), and execute F's queued
`rename`/`unlink`/`rmdir` row now. Under P-proto this fixes (a), (b) and (d), with an empty queue
and a correct remount view. The commit is the wrong place to enforce it, for three reasons:

- `create` straight onto a passthrough source writes at the logical path when the user op runs
  (`overlay.py:337-352`), so a check at commit time never sees that write.
- The mkdir replay silently adopts an occupant's directory (`overlay.py:912-914`, `:954`). This is
  the same family as GOAL defect 2's stray directory.
- Once the rule holds whenever a name is introduced, P(E) is collision-free whenever a commit runs.
  At commit time it only needs an assertion.

**Costs for the plan:**

- Running an occupant's *rename* early is a new clock jump. § 5 currently allows only "the queued
  source removal", so this goes in the deviation table or the § 5 text.
- A rename runs out of queue order. Its replay target is the queue-time logical `detail['new']`,
  which it materializes logically (`overlay.py:925-928`), so the plan must either recursively vacate
  that target's P() first, or flush the queue prefix up to and including F's row (order-safe, but
  unbounded work).
- `create`, `mkdir`, `symlink` (if present) and rename-into all reach `_vacate`. Each needs P(X)
  computed before the new entry exists, by walking parents up to the nearest ancestor with a
  `source_path`.

## 4. S3 source

- `put` at a physical key is one atomic PUT with no parent requirement (`s3.py:181-192`).
  Ancestor materialization writes `.sosfs.keep` sentinels (`s3.py:212-226`), and `mkdir` raises
  `FileExistsError` on any existing prefix, which callers already catch. The later `rename` is
  `obstore.rename`, a copy plus a delete per object (server-side, so no client egress).
  `_rename_prefix` (`s3.py:260-270`) moves the committed object along with its siblings, but not
  atomically.
- Writing at P(E) costs one PUT plus a later copy and delete. Collapsing the move into the commit
  would cost one PUT plus one DELETE. That is the egress trade Q2 names.
- **Hazard, pre-existing and also reachable under P(E): a same-path rename destroys the object on
  S3.** `S3Tier.rename(p, p)` on an obstore `MemoryStore` leaves `stat(p) == None` for a file, and
  for a directory and its children (copy-then-delete onto itself). Real S3 either rejects a
  copy-to-self or deletes the object (inferred; not run against S3).
  - HEAD reaches it for create → rename → commit before replay. `_commit_hop` sets
    `source_path = new` (`overlay.py:808`), then the replay calls `rename(new, new)` (`:928`).
  - P(E) reaches it for the same sequence, and for a renamed queued-mkdir directory.
  - POSIX `os.replace(p, p)` is a no-op, so the suite cannot see it. The replay should skip the
    tier call when `source_path == detail['new']`.
