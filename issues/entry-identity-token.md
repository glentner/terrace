---
status: adopted:entry-identity-token
kind: fix
appetite: big
lane: public
---

# A lookup by path, id and kind accepts a file that took a purged entry's id

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The handle-less `truncate` looks its entry up again under the source lock and tells it from another
only by path, id and kind (`AGENTS.md` § 5). Entry ids are reused: the catalog has no `AUTOINCREMENT`, so
once an entry is purged its id goes to the next entry created. If, while the cut waits for the source
lock, the entry is removed and purged, a new file elsewhere takes its id, and a directory rename carries
that file onto the path, the check accepts the new file: the call cuts it and returns success. It is
silent. The caller named a file whose bytes were never cut, and another file lost its bytes.

Reproduced by injection in the `source-path-truncate-and-stat` cycle (its `spec/` review cycle 1), on a
stage with `bypass_size=100` and a 1000-byte `data/big.bin`. Inside one call's wait for the source lock,
another thread renamed `/data` to `/moved`, unlinked `/moved/big.bin`, flushed (the purge frees the
entry's id), created `/moved/big.bin` with 500 bytes (it took the id), flushed, and renamed `/moved` back
to `/data`. The call returned success and cut the new file to 4 bytes. A symbolic link or a directory
that takes the id the same way is refused with `ENOENT`, because the kind is decided under the lock as
well, and the cut never follows a link at the file itself (`O_NOFOLLOW`). The gap stays open for a file.

Every route to it needs a rename of an ancestor during the call: the call holds the file's path lock,
which blocks a removal or rename of the file at that path, and a path lock does not cover ancestors
(`issues/freethreaded-concurrency-stress.md`, "Path locks do not cover descendants").

- 2026-11-08: since `source-served-open-race`, the `open` of a copy served off the source and a
  promotion off the source make the same lookup under the source lock (`_lookup_again`), so they share
  the gap: such a file carried onto the path is opened, or its bytes promoted into the stage, and the
  call succeeds. Reproduced by injection for `open` in that cycle's planning: the cut's sequence run
  inside `open`'s wait, then a flush, and `open` returned the new file's 500 bytes.

## Why it was deferred

Closing it needs an identity token that a reused id cannot forge: a generation column on `entry`, or an
id that is never reused (`AUTOINCREMENT`). Either is a state-database schema change, and `SCHEMA_VERSION`
has no migration path (`AGENTS.md` § Invariants, the `state/db.py` and `state/schema.sql` rows), so a
bump makes every existing state database unopenable. `source-path-truncate-and-stat` recorded the gap
(plan-gate ruling, 2026-11-06), and `source-served-open-race` re-homed it here when that cycle's seed
retired (its GOAL Q2, 2026-11-08). Not recorded as accepted behaviour: that would accept a silent,
successful cut of another file.

## Outcome / vision

A call that looks its entry up again under the source lock acts on that entry or raises `ENOENT`, never
on a file that took its id. No existing state database becomes unopenable on the way.

## Sketch of the acceptance criteria

- **R1** — WHEN, during a handle-less `truncate`'s wait for the source lock, the entry is purged, a new
  file takes its id and a directory rename carries that file onto the path, the call SHALL raise `ENOENT`
  and cut nothing.
- **R2** — Every other lookup that tells an entry by path, id and kind under the source lock SHALL do the
  same.
- **R3** — The identity token SHALL survive a remount, and a state database written before it SHALL
  still open, by a migration or a documented one-time rebuild.
- **R4** — A deterministic injected-interleaving test in `tests/engine/test_replay.py` pins each, with a
  fix-removal mutant that turns it red.

## Notes

- Related: `issues/freethreaded-concurrency-stress.md` (the ancestor rename that reaches the gap);
  `issues/audit-command.md` (a rebuild would go through the audit); `issues/plan-md-reconciliation.md`
  (the schema has no migration story at all).
- Found by: `source-path-truncate-and-stat` plan, adversarial review (2026-11-06); re-homed by
  `source-served-open-race` (2026-11-08).
