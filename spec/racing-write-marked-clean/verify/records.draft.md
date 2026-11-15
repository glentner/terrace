# Record edits, as the prototype carried them

Drafted by the plan step and dry-run against the gate's `records` and `ledger` modes on the planning
prototype. The build may reword them; the gate checks the facts, not these sentences. Nothing here
names the package, so the text holds across the rename. Dates are the build's to set.

## `AGENTS.md` § 6 — replaces the bullet "After a copy, `changed` detection re-arms rather than settles"

- **A copy goes clean on the engine's own record of the hop, never on a stat.** A hop opens a record for
  its holder when its copy starts reading (`TransferPool.copy` calls the hop's `before_read` before its
  stat of the holder), and every change to a holder's bytes is written to it under `_handles_mutex`: a
  `write` or a `truncate` through a handle is admitted before its bytes land (`_change`) and counted in
  flight until it returns, and a `truncate` with no handle marks the record before its cut (`_meet`). The
  record opens already met when a change is in flight on the holder. After the copy the hop stats the
  holder and then, in one hold of the mutex, reads and closes the record and resets `marked_dirty` on the
  holder's open handles. The holder goes clean only when no change met the hop *and* the stat still
  equals what was copied; otherwise the row is re-clocked and the holder stays dirty. The stat can only
  veto: it catches bytes changed behind the engine's back, and an equal size and mtime are not evidence
  that nothing was written. A handle's flag is read and its change admitted in one hold of the mutex, so
  a change either meets the hop or finds its flag reset and re-marks on the path lock the commit holds.
  Reading the flag outside the mutex, admitting a change after its bytes land, closing the record before
  the stat, or skipping the reset each lets a write land on a copy recorded clean: no journal row, no
  residency evidence, nothing for the audit to reconcile against. The reset is unconditional: after a
  hop a change met, it is what makes the writer's next change wait on the path lock, which is what lets
  `commit_upstream`'s loop end under a writer that never pauses. While one admitted change is still in
  flight the record opens met, so `commit_upstream` copies the file again, with the path lock held, until
  that call returns; nothing bounds it but the call.

## `AGENTS.md` § 10 — a new bullet, before "The purge-watch mutex"

- **`OverlayEngine._handles_mutex`** guards the open-handle table, each handle's `marked_dirty` and
  `changing`, and the open hop records, `_hops` (§ 6). It is held for a few statements and never across
  I/O, SQL or a wait; nothing is acquired while it is held, and nothing takes it inside a transaction.
  Its holders beyond the table's own (`open`, `release`, `getattr`, `evict_copy`): `write` and `truncate`
  through a handle, twice each and with no other lock (`_change`); `_mark_handle_dirty`, under the path
  lock, after its `mark_dirty` has committed; a hop, to open its record from inside `TransferPool.copy`
  (after the intent's insert has committed) and to close it after its stat; and `truncate` with no
  handle, under the path lock and the source lock. Its place in the order is unchanged.

## `AGENTS.md` § High-blast-radius paths — the row, and the short list's cell

In the table, the row for `write`, `_mark_handle_dirty`, `truncate` becomes:

| `engine/overlay.py` — `write`, `_change`, `_meet`, `_mark_handle_dirty`, `truncate` | Metadata-before-ack, and the admission a hop's record depends on. Reordering two statements is undetectable in review and unrecoverable in production. |

In the short list, `write`/`_mark_handle_dirty`/`truncate` becomes
`write`/`_change`/`_meet`/`_mark_handle_dirty`/`truncate`.

## `AGENTS.md` § Known open violations

Remove the row whose seed is `issues/racing-write-marked-clean.md`.

## `_commit_hop`'s docstring, first paragraph

```
        Copy the dirty copy of `path` at `holder` one hop to `upstream` and
        record the outcome (the caller holds the file lock). The holder goes
        clean only when no change met the hop, by the engine's own record, and
        its stat still equals what was copied; otherwise it stays dirty and the
        boundary is re-clocked. Open handles on the holder re-arm their dirty
        marking either way, so a change after the hop re-queues.
```

## `issues/freethreaded-concurrency-stress.md` — one dated note

- 2026-11-12 (`racing-write-marked-clean`): `Handle.marked_dirty` is now read and written only under
  `_handles_mutex`, with a new `Handle.changing` and the hop records; `Handle.written` is still unguarded.
  Every change through a handle now takes that engine-wide mutex twice. Measure its contention under
  the stress suite on the free-threaded build before anything else is put behind it.

## `issues/pump-liveness-and-loss-windows.md` — one dated note

- 2026-11-12 (`racing-write-marked-clean`): `commit_upstream` now copies the file again for as long as a
  change admitted through a handle is in flight on the holder, with the path lock held (26 copies for a
  write held inside `os.pwrite` across 25). It ends when that one call returns. Bound it, or wait on the
  change's return with a finite timeout, when the pumps get their liveness.
