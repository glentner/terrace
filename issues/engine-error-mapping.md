---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Non-`OSError` exceptions escape the VFS API

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The engine's contract is written down twice. `OverlayEngine`'s docstring
(`src/sosfs/engine/overlay.py:74-76`) says failures "raise standard `OSError` subclasses so the
adapter maps errno directly", and `OverlayOperations`' docstring (`src/sosfs/fuse/adapter.py:44-46`)
draws the conclusion: "the engine raises standard `OSError` subclasses, which mfusepy turns into
`-errno`, so error mapping needs no code here." The adapter is built on that promise — its only
errno-raising sites are the unknown-handle `EBADF` at `adapter.py:75` and the missing-xattr `ENOATTR`
at `adapter.py:142`. There is no `except` clause translating an engine exception anywhere in the file.

Three paths break the promise.

**`_serve` raises `EngineError` when no resident copy exists.** `overlay.py:627` raises
`EngineError(f'No resident copy of {entry.path}')` when `catalog.fastest_copy` returns `None` for a
cataloged entry. `EngineError` subclasses `SOSFSError`, which subclasses `Exception`
(`overlay.py:46`, `src/sosfs/core/exceptions.py:32`) — not `OSError`. It reaches libfuse through
`open` as an unhandled exception, and what the application sees is the binding's fallback rather than
an errno the engine chose. Found by reading the call graph, not reproduced: it requires a catalog
entry whose residency rows have all vanished, which is a state `sosfs audit` is meant to repair and
today nothing repairs.
- 2026-11-09: the `overlay.py` line numbers above hold at `6a046b4`.

**Writing through a handle to an unlinked file raises `LookupError`.** `write` calls
`_mark_handle_dirty` before the bytes land (`overlay.py:650-651`, the metadata-before-acknowledgment
ordering), which calls `journal.mark_dirty` at `overlay.py:662`. Once the entry's residency rows are
gone, the residency `UPDATE` at `src/sosfs/state/journal.py:149-150` matches zero rows and
`journal.py:152` raises `LookupError(f'No copy of {path} in tier {tier}')`. Reproduced: against a file
that already exists in the source, the sequence open → unlink → write yields
`LookupError('No copy of /x.dat in tier scratch')`.
- 2026-11-09: the `overlay.py` line numbers above hold at `6a046b4`.

**The same write on a file the mount itself created raises `AttributeError`.** `_remove` tombstones
an unlinked entry only when it has a source copy and purges it outright when it does not
(`overlay.py:500-510`), so the two orderings fail one frame apart. With the entry row gone,
`catalog.by_id` returns `None` and `overlay.py:661` dereferences it in
`with self.locks.hold(entry.path)`, before `mark_dirty` is reached. Reproduced: the sequence
create → open → unlink → write yields `AttributeError("'NoneType' object has no attribute 'path'")`.
- 2026-11-09: the `overlay.py` line numbers above hold at `6a046b4`.

That last sequence is `tempfile.TemporaryFile`. Create a file, keep the descriptor, unlink the name,
write through the descriptor — the standard way every POSIX program makes a scratch file that cannot
leak, and POSIX requires the write to succeed because the inode stays live until the last descriptor
closes. So the unlinked-write path is a **correctness gap, not only an error-mapping one**: giving the
`LookupError` and the `AttributeError` errnos would still fail a write that must succeed. Any program
using `mkstemp`-and-unlink inside the mount hits it, and the failure arrives at the point of the write,
far from the unlink that caused it.

Existing coverage stops one call short. `tests/engine/test_datapath.py:213`,
`test_unlink_while_open_posix_semantics`, opens `O_RDONLY`, unlinks, reads through the handle, releases
and asserts `getattr` then raises `FileNotFoundError`. The read case is pinned; the write case is not
tested and does not work.

A fourth site belongs to the same fix, found by inspection while writing this seed: `eligible_tier`
raises `EngineError` at `overlay.py:200` when no tier is passthrough-capable, and it is called from
both `create` (`overlay.py:337`) and `_serve` (`overlay.py:629`) — callback paths.
`OverlaySpec._validate_stack` (`src/sosfs/tier/spec.py:304`) does not require an S3 or archive source
to be backed by a POSIX staging tier, so that configuration parses and then throws a non-`OSError` out
of the first `open`.
- 2026-11-09: the `overlay.py` line numbers above hold at `6a046b4`.

*Added 2026-11-08 by `truncate-recording-after-ancestor-rename`, review cycle 1:* an instance outside
that cycle's scope (it lands before the cut's lookup under the source lock, and the escape is that
GOAL's Non-goal).

- **A promotion from a slower stage in a rename's stage-move gap raises `TierError`.** A user `rename`
  of a directory moves its stage copies after it releases the source lock. In that gap the catalog
  holds the file at its new path while the stage still holds the bytes at the old one. A handle-less
  `truncate` at the new path whose `_serve` promotes the file from a slower stage to a faster one that
  admits it copies from the logical new path, and `transfer.copy` raises
  `TierError('No file at /moved/s.txt in tier scratch')` (`engine/transfer.py:91`). `TierError` is a
  `SOSFSError`, not an `OSError`. Nothing is marked dirty and no intent is left open. Reproduced by
  injection on a three-tier stack at `9169405`, and identically with `overlay.py` as at `630a406`.

## Why it was deferred

Found by the invariant audit run while porting the software factory. Fixing it means choosing an errno
for each site and changing the behaviour of the write path on a dead entry, which is product design
inside a high-blast-radius region (`overlay.py::write` / `_mark_handle_dirty` are on
`AGENTS.md` § *Invariants* § *High-blast-radius paths* for metadata-before-acknowledgment), and
the port was a harness change.

This is a **live defect on `main`**, and the unlink-then-write half is reproduced. It is recorded in
`AGENTS.md` § *Invariants* § *Known open violations* as the § 11 row, "`EngineError` and
`LookupError` escape the VFS API; writing through a handle to an unlinked file fails where POSIX
requires success."

## Outcome / vision

Every failure out of the VFS-operations API is an `OSError` subclass carrying the errno the caller
should see, so the adapter's claim that it needs no mapping code is true rather than aspirational. The
tempfile pattern works through a mount, and a program that unlinks its scratch file keeps writing to it
until it closes.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — When a file is created, opened, unlinked and then written through the still-open handle, the
  overlay engine shall complete the write and return the byte count, and a read through the same handle
  shall return the written bytes.
- **R2** — When the last handle on an unlinked file is released, the engine shall leave no residency
  row for the entry and no bytes for it on any tier, and shall queue no write-back toward the source —
  the bytes die with the last close, and a commit to a purged source path resurrects a deleted name.
- **R3** — The VFS-operations API shall raise only `OSError` subclasses. Observable by driving each
  public engine operation through its failure paths and asserting `isinstance(error, OSError)`; the
  four sites known today are `overlay.py:200`, `:627`, the un-guarded `entry` dereference at `:661`
  and the `mark_dirty` path at `:662`.
  - 2026-11-09: the `overlay.py` line numbers in this item hold at `6a046b4`.
- **R4** — If the engine cannot serve an entry because no resident copy exists, then it shall raise an
  `OSError` subclass whose `errno` the adapter forwards unchanged to libfuse. The errno is named at
  promotion; see the open question below.
- **R5** — While a mount is live, the shell sequence that creates a file, unlinks it and writes to the
  open descriptor shall succeed and read back its own bytes. Checkable only with a real mount on Linux;
  CI runs no integration job today (`issues/ci-integration-lane.md`).

## Notes

- Related: `issues/audit-command.md` (the "no resident copy" state R4 covers is one the audit is
  supposed to repair, and until the audit exists the engine is the only thing that meets it);
  `issues/ci-integration-lane.md` (R5 has no machine to run on until that lane exists);
  `issues/invariant-audit-gaps.md` (the other small measured gaps from the same audit). `PLAN.md`
  § *Overlay engine* and § *FUSE adapter*; `AGENTS.md` § *Invariants* § 11 *The engine API
  contract*.
- Open — **which errno for "no resident copy".** `ENOENT` lies to a caller whose file demonstrably
  exists in the catalog and will confuse a program that just stat'd it. `EIO` is honest and gives the
  user nothing to act on. `ESTALE` matches the NFS precedent for a handle whose backing object went
  away and is the one most likely to be handled correctly by a retrying client.
- Open — **whether `EngineError` should subclass `OSError` wholesale or each raise site should pick its
  own.** A blanket subclass is one line and makes every `EngineError` an errno the adapter forwards,
  including the start-up failures at `overlay.py:120` and `:128` that never occur inside a callback and
  would then carry a meaningless errno into a CLI error message. Per-site errnos are more code and say
  something true at each site.
  - 2026-11-09: the `overlay.py` line numbers in this item hold at `6a046b4`.
- Open — **what the write path does on a dead entry.** POSIX wants the write to land in the resident
  copy; the residency row is also what the eviction manager and the quota counters read, so "no row"
  needs its own handling rather than an early return. Whether the entry keeps a residency row until the
  last release, or the handle carries enough state to write without one, is the actual design decision
  in this cycle.
- Found by: the invariant audit run during the software-factory port (2026-08-22), engine-core-invariants
  brief, divergence D.
