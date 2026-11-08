---
status: adopted:source-served-open-race
kind: fix
appetite: small
lane: public
---

# `open` and promotion read the committed source path outside the source lock

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`open` serves a copy held on the source from `entry.source_path`, and `_serve` promotes from it
(`AGENTS.md` § 5, "Reading the source resolves through it too"). Both read `source_path` from the entry
that `_ensure` returned, under the file's path lock only. Replay takes no path lock: the scheduler's
`commit_row`, `flush`, `drain`, and `_vacate` reached from another path's `create`, `mkdir` or
`rename` all move source objects under the source lock alone (§ 10). A replay that commits the
entry's queued rename, or an ancestor's, between that read and the tier call moves the object away
from the place the call is about to use.

Evidence, at `0a88f30` (the `source-path-truncate-and-stat` plan's research, brief 01 § 6; tmpdir
tiers, a stage with `bypass_size=100`, a 1000-byte `data/f.bin`, `rename /data → /moved` queued, a
`flush()` injected from a second thread):

- injected between `_serve` and `open_direct` in `open`, the call raises `FileNotFoundError` on the
  pre-rename path, `project/data/f.bin`, for a file that exists at `project/moved/f.bin`;
- injected before `_serve` on a file a faster tier admits, the promotion's `transfer.copy` raises
  `TierError: No file at /data/f.bin`.

Both fail loudly, and the probe found no case where one acts on another entry's object. `truncate`
without a handle has the same window at `0a88f30`. The `source-path-truncate-and-stat` cycle closes
it there (its `spec/` PLAN § 2.1, review cycles 1 and 2): the call takes the source lock for every cut,
whatever copy serves, and decides under it, once, the entry at its path (path, id and kind), the copy
that serves it, promoting first, and the place that copy holds its bytes. It raises `ENOENT` when the
entry has left the path or a non-file is found there, and it holds the lock through the cut and the
recording. `_record` reads the entry again by id under the same lock.

A related window: a path lock covers the path it names, not its ancestors, so a directory rename can
move an entry's logical path while another call holds the old one, and other calls reach the entry
through the new path meanwhile. In the handle-less `truncate` this reproduced by injection (review
cycle 1 of that cycle, at `c3f31ff`): during the wait for the source lock, another thread renamed the
directory away, rewrote the file through a handle, opened it so the stage took a clean copy, and
renamed the directory back. The call cut the source, and the stage copy, still marked clean, kept the
old bytes and served them. Review cycle 2 found that deciding again under the lock for the source alone
left the reverse: a stage copy evicted during the wait made the call cut the source with the lock free,
and in one injected interleaving cut another file's object. That cycle closed both by deciding the
serving copy only under the lock, which every handle-less cut now takes. The cut's catalog writes are
keyed by the path it was given, and a rename of an ancestor already past its `_vacate` can still move
the entry before the cut is recorded: those writes then match no row, while its stage-counter write,
keyed by tier, lands. Reproduced by injection, and recorded in
`issues/truncate-recording-after-ancestor-rename.md`. Other calls that write the catalog by the path
they were given are argued from the code, not reproduced.

A residual gap in that cycle's identity check, reproduced by injection. No identity token exists short
of a schema change. Inside one call's wait for the source lock, on a stage with `bypass_size=100` and a
1000-byte `data/big.bin`, another thread renamed `/data` to `/moved`, unlinked `/moved/big.bin`,
flushed (the purge frees the entry's id), created `/moved/big.bin` with 500 bytes (it took the id),
flushed, and renamed `/moved` back to `/data`. The path-plus-id check accepts the new file: the call
returns success and cuts it to 4 bytes. It is silent: the caller named a file whose bytes were never
cut, and another file lost its bytes. At `c3f31ff` the cut landed on the source while a stage copy
kept the 500 bytes, and the tree did not converge after a flush. Since the serving copy is decided
under the lock, the cut lands on the copy that serves and the tree converges, with the new file cut.
At `ea38083` a symbolic link that took the id the same way was followed, and its target, a third file,
was cut; a directory that took it raised `AttributeError`. The kind is now decided under the lock as
well, so both are refused with `ENOENT`, and the cut's open never follows a link at the file itself (the
last path component; `O_NOFOLLOW`). The gap stays open for a file. `AGENTS.md` § Known open violations
carries it.

## Why it was deferred

Out of scope for `source-path-truncate-and-stat` (its GOAL's non-goals keep other open violations
in the same regions with their own seeds). It is pre-existing: the unlocked read predates the factory
(`open`'s serve-path line and `_serve`'s promotion both read `source_path` without the lock at
`141aa86`). Taking the source lock across a promotion is not a one-line change. A promotion is a
whole-file copy, and § 10 already records that a copy under the source mutex delays every `create`,
`mkdir` and `rename` that reaches `_vacate`. Shaping has to choose between holding the lock for the
copy, re-resolving and retrying on `ENOENT`, or copying out of a pinned place. The handle-less
`truncate` took the first for its own promotion (its cycle's review cycle 2): it promotes under the
source lock, and pays that delay only when it promotes.

## Outcome / vision

`open` and a promotion off the source either reach the object where the source holds it at the
moment of the tier call, or retry against the entry's current place, under every interleaving of a
replay. No call reports `ENOENT` for a file that exists.

## Sketch of the acceptance criteria

- **R1** — WHEN a replay commits a source-served file's queued rename, or an ancestor's, between `open`
  resolving the entry and opening the copy, `open` SHALL return a handle on the file's bytes.
- **R2** — WHEN the same replay lands before a promotion off the source, the promotion SHALL copy the
  file's bytes from where the source holds them.
- **R3** — Both SHALL be pinned by deterministic injected-interleaving tests in
  `tests/engine/test_replay.py`, each with a fix-removal mutant that turns it red.
- **R4** — Shaping SHALL decide the handle-less `truncate`'s identity gap: an identity token that tells
  an entry from a file that took its id (a schema change), or `accepted-behaviour` with the reason.

## Notes

- Related: `issues/freethreaded-concurrency-stress.md` (the mutex is argued, not tested under threads);
  `issues/source-commit-staged-publish.md` (narrowing a commit's hold on the source mutex, which bears
  on whether a promotion can hold it).
- Found by: `source-path-truncate-and-stat` plan, research brief 01 (2026-11-06).
