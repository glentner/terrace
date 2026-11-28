# Research 04 — read-only enforcement over an archive source (R9)

Read on the worktree at its current head. Line numbers are `src/terrace/engine/overlay.py` unless named.
No probe was run: an archive engine cannot be built until this cycle's `start` change lands, so the
reach paths below are read from the code.

## 1. Public engine API, classified

An archive tier has no `open_direct`, so `_passthrough(source)` is False (`:200-202`) and
`eligible_tier` always returns a POSIX stage (`:214-229`). So every write lands on the stage first and
reaches the archive later, through the write-back queue or a synchronous replay.

| Method | Class | How it reaches the archive today |
|---|---|---|
| `mkdir` `:368` | mutating | queues `mkdir` (`:383`) → `commit_row`/`flush`/drain → `_execute_namespace_locked` → `source.mkdir` (`:1236`) → `ArchiveTier.mkdir` → `_repack`. `_vacate` (`:375`) can replay rows synchronously. |
| `create` `:387` | mutating | empty file on the stage, `mark_dirty` (`:425`) → `_commit_hop` copies to source (`:1084`) → `ArchiveTier.put` (`archive.py:585`) → `_repack`. |
| `unlink` `:430`, `rmdir` `:442` | mutating | `_remove` drops stage copies now and queues `unlink`/`rmdir` (`:592`) → `source.delete`/`rmdir` (`:1243-1245`). |
| `rename` `:460` | mutating | queues `rename` (`:501`) → `source.rename` (`:1254`). **Synchronous reach:** a rename onto an existing source file runs `_remove(replaced=True)` → `_purge_source` → `source.delete` inside the call (`:495`, `:586`, `:617`). Moves stage copies at once (`:508-519`). |
| `chmod` `:627`, `chown` `:631`, `utimens` `:644` | mutating | all go through `_set_meta` (`:648`), which queues `meta` (`:672`) → `source.set_meta` (`:1264`); also calls `set_meta` on the stage copies (`:680`). |
| `write` `:812` | mutating | `_change` → `_mark_handle_dirty` → `mark_dirty` (`:871`) → data commit to source → `put`. |
| `truncate` `:877`, both forms | mutating | the handle form goes through `_change` (`:909`). The path form promotes, marks dirty (`:928`) and cuts the stage copy, which a commit then carries to the source. |
| `open` `:686` | mutating only with write flags | O_RDONLY promotes from the archive to the stage (`_serve` `:755`, which reads the archive) plus `catalog.touch` (`:725`). That is not a change to the archive, and R8 needs it. A write-flag open hands back an RDWR fd on the stage; O_TRUNC calls `truncate` **after** the handle is registered (`:722-727`). |
| `fsync` `:943` | not mutating | `_record`. It calls `commit_upstream` only when `handle.written` (`:953`), which a refused write never sets. |
| `release` `:956` | not mutating | closes the fd and runs `_record` (catalog and stage only). It must keep working. |
| `commit_row` `:1014`, `commit_upstream` `:1119`, `commit_to_shared` `:1144`, `flush` `:1178` | pump/admin | each reaches the source only by running queued rows. With nothing admitted they are no-ops. |
| `evict_copy` `:1536` | pump | refuses the source tier (`:1548`) and drops only clean stage copies. |
| `getattr`, `readdir`, `readlink`, `residencies`, `read`, `eligible_tier`, `upstream_of`, `source`, `start`, `stop` | read-only | `_adopt` writes catalog rows and `ArchiveTier.index` persists `archive_index`, both in the state DB. A tar.gz `_populate` writes the staged bundle. None of them writes the archive. |

The engine has no `symlink`, `link`, `mknod`, `setxattr` or `removexattr`, so those are not engine API.

**Latent trap.** Today `write` on an O_RDONLY handle marks the stage copy dirty and queues a data row
(`_change` → `_mark_handle_dirty`) *before* `os.pwrite` fails with EBADF. Over an archive, the next
drain then re-packs the archive. So the guard must come before `_change`, not rely on `open` refusing
write flags.

**Read-path metadata** (`issues/read-path-metadata-writes.md`: `touch`, `_record`) is state-DB
bookkeeping, not a change to the archive. R9 does not cover it; leave it alone.

## 2. Placement

There is no shared funnel below the API that avoids the high-blast-radius bodies:

- `_change`, `_mark_handle_dirty`, `_remove`, `_vacate` and `journal.mark_dirty` are all on the list.
- `_set_meta` is not on the list and covers chmod, chown and utimens.

Recommended:

- **A top-of-method guard before any lock or lookup**, either inline or as a decorator (e.g.
  `@_mutating`) that raises `OSError(errno.EROFS, 'Read-only file system', path)` when
  `self.read_only` is set.
- **Methods that must change:** `mkdir`, `create`, `unlink`, `rmdir`, `rename`, `write`, `truncate`,
  `open` (conditional, see below), and `_set_meta` (or its three callers).
  - Of these, `write`, `truncate`, `create`, `rename` and `open` are high-blast-radius. Their change is
    one statement or one decorator line ahead of the existing body. No ordering inside them moves.
  - `rename`'s guard must precede `self.getattr(old)` (`:472`).
- **`open`:** refuse at the top when `flags & os.O_ACCMODE != os.O_RDONLY` or `flags & os.O_TRUNC`,
  before `_serve`. Refusing there means no stage promotion for a write open and no handle leaked by a
  late O_TRUNC refusal. O_RDONLY (with or without O_APPEND/O_CREAT on an existing file) proceeds
  unchanged.
- **The flag:** `read_only`, derived in `__init__` from `spec.source.type in {'zip', 'tar', 'tar.gz'}`
  (no disk access). Name it generically so `issues/read-only-mount.md` can set it later from a mount
  option or spec key.
- **Leave unguarded:** `fsync`, `release`, `commit_*`, `flush`, `evict_copy`, which are reached by
  drain and eviction at shutdown. Guarding them would add high-blast edits for calls that are no-ops
  once nothing is admitted.
- **Defense in depth (optional, outside the listed bodies):** the engine builds `ArchiveTier` with a
  read-only switch. `put`, `set_meta`, `delete`, `mkdir`, `rmdir` and `rename` (`archive.py:585-669`,
  not on the list; `_repack` is) then raise `OSError(EROFS)`. Any path missed above fails loudly
  instead of re-packing. The adapter's own write tests keep the default.

## 3. Pumps

None of them writes the archive on its own:

- **Write-back scheduler:** only `commit_row` over queued rows (`writeback.py:87-98`, `:116`).
- **Drain:** namespace rows, then `dirty_copies` from queued data rows (`drain.py:95-106`).
- **Rescue:** `commit_to_shared` over the same worklist (`rescue.py:60-68`).
- **Eviction:** `evict_copy` drops clean stage copies; `commit_upstream` runs only for dirty ones
  (`evict.py:126-152`).

With every mutation refused, the queue is empty and nothing is ever dirty, so all four do nothing to
the source. `engine.stop()` drains nothing (`:168-176`); drain and rescue run from `fuse/mount.py:250-258`.

**Nothing re-packs or recompresses unprompted.** `ArchiveTier.resync` has no caller under `src/`, and
`_populate` only reads the inner archive (`archive.py:414-425`, `:427-450`). An eviction of a promoted
copy touches only the stage.

## 4. FUSE errno path

The adapter maps no errors itself (`adapter.py:45-47`). mfusepy's `_wrapper` returns `-e.errno` for
any `OSError` with a positive errno (`mfusepy.py:1489-1510`), so an engine `OSError(EROFS)` reaches the
kernel as EROFS.

| Adapter callback | Result over an archive |
|---|---|
| `mkdir`, `rmdir`, `unlink`, `rename`, `chmod`, `chown`, `utimens`, `write`, `truncate` (`adapter.py:146-218`) | EROFS from the engine guard |
| `create` (`:194-198`) | EROFS from `engine.create` before its `open` |
| `open` with write flags (`:200-202`) | EROFS from the engine guard |
| `link` (`:167-169`) | EPERM, whatever the source (not engine API) |
| `symlink`, `mknod` (not overridden) | mfusepy defaults EROFS |
| `setxattr`, `removexattr` (not overridden) | mfusepy defaults ENOTSUP |

The kernel-level `ro` mount option (`FUSE(...)`, `mount.py:310`) belongs to `issues/read-only-mount.md`.

## 5. R9 test design (`tests/engine/test_archive_readonly.py`, `unit`)

**Fixture.** Parametrize over `zip`, `tar` and `tar.gz`. Build the archive with stdlib in `tmp_path`,
with members `top.txt`, `data/a.txt`, `data/b.txt` and an explicit empty `empty/` directory, so that
`rmdir` has an argument that would succeed on a writable engine. Use a spec of the archive source plus
one shared POSIX stage, and start the engine.

**Snapshot** the archive file: sha256, `st_size`, `st_mtime_ns`, `st_ino` (an atomic re-pack changes
the inode), and the sorted listing of its parent directory (no temp files or marker beside it, which
also covers R6).

**Calls**, each asserting `raises(OSError)` with `errno == EROFS`:

- `mkdir('/new', 0o755)`
- `create('/new.txt', 0o644)`
- `unlink('/top.txt')`
- `rmdir('/empty')`
- `rename('/top.txt', '/moved.txt')`
- `rename('/data/a.txt', '/top.txt')` (the synchronous `_purge_source` path)
- `chmod('/top.txt', 0o600)`
- `chown('/top.txt', os.getuid(), os.getgid())`
- `utimens('/top.txt', 1.0, 2.0)`
- `truncate('/top.txt', 0)`
- `open('/top.txt', f)` for each `f` in `O_WRONLY`, `O_RDWR`, `O_RDONLY|O_TRUNC`, `O_WRONLY|O_APPEND`
- With `h = open('/data/a.txt', O_RDONLY)`: `write(h, b'x', 0)` and `truncate('/data/a.txt', 0, handle=h)`.
  Then `fsync(h)` and `release(h)` succeed.

**Then:**

1. `read` of every member still returns the archive's bytes.
2. `journal.queued() == []`, and no residency is `dirty`.
3. Run `flush()`, `WritebackScheduler(engine).poll(now=1e12)`, `drain(engine)` and `stop()`.
4. Re-snapshot and compare equal.
5. Restart and read one member again.

**Enumeration guard.**

- The test holds explicit `MUTATING` and `NON_MUTATING` sets and asserts that their union equals
  `{n for n in vars(OverlayEngine) if not n.startswith('_')}`, so a new public method fails until it is
  classified.
- If a decorator is used, expose its registry (e.g. `OverlayEngine.MUTATING`) and assert it equals the
  test's set.
- Optionally, in `tests/fuse` (skips without libfuse), assert that every write callback
  `OverlayOperations` overrides is in a classified set.

## Recommendations

1. Add `read_only` to the engine, derived from the source type in `__init__`, named for reuse by
   `issues/read-only-mount.md`.
2. Guard, at the top and before any lock or lookup: `mkdir`, `create`, `unlink`, `rmdir`, `rename`,
   `write`, `truncate` (both forms), and `_set_meta`. This is one statement or decorator line in each
   high-blast method, with no reordering. Record the touch in PLAN's deviation table.
3. `open`: refuse a write access mode or O_TRUNC before `_serve`. Keep O_RDONLY open and promotion
   unchanged for R8.
4. Don't guard `fsync`, `release`, `commit_*`, `flush` or `evict_copy`. The test proves them inert.
5. Optional second line: a read-only switch on `ArchiveTier`'s six write methods, so a missed path
   raises EROFS instead of re-packing.
6. Write the parametrized R9 test above, with the inode and sibling-listing snapshot, an explicit
   drain and restart, and the public-method enumeration guard. Include `write` on an O_RDONLY handle,
   since it would otherwise queue a data commit to the archive.
7. FUSE needs no change. `link` stays EPERM and `setxattr` ENOTSUP; say so in the docs if R11's text
   claims "EROFS for every write".
