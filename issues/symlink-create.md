---
status: unshaped
kind: feature
appetite: small
lane: public
---

# Symlinks can be read through the mount and never made

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

**Symlinks can be read and never made.** The engine adopts a source symlink and serves `readlink`
(`src/terrace/engine/overlay.py:349-354`), and the adapter defines no `symlink` callback
(`src/terrace/fuse/adapter.py`), so `ln -s` through the mount fails with whatever the binding's
default returns. `PLAN.md` puts special files and hard links out of scope and says nothing against
symlinks; environments, build trees and "latest" pointers all make them. Anchors confirmed at
`3e85305` (2026-11-12).

Creating one is more than a callback: a symlink entry made through the overlay has to reach the source
through the write-back queue like any created entry, under the deferred-op replay rules of
`AGENTS.md` § Invariants § 5.

## Why it was deferred

Cut from `posix-surface-gaps` at its shaping (2026-11-12, the foreman's ruling): it is a feature with
a write-back path of its own, where that cycle is a small fix to ownership and to unsupported
operations. Pre-existing: the adapter has never had a `symlink` callback since Phase 6.

## Outcome / vision

`ln -s` works through the mount, the link reads back at once, and it reaches the source on the
write-back clock like any other created entry.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN `symlink(target, path)` is called, the engine SHALL create a symlink entry that
  `readlink` returns, and it SHALL reach the source through the write-back queue like any created
  entry.

## Notes

- Ownership: a symlink created through the overlay should follow the owner and group rule that
  `posix-surface-gaps` settles for files and directories.
- Related: `spec/posix-surface-gaps/` (where this was cut from); `issues/engine-error-mapping.md`;
  `issues/ci-integration-lane.md` (a conformance suite against a real mount would measure this class).
- Found by: an out-of-cycle review of the whole tree at `64faf27` (2026-11-11), recorded in
  `issues/posix-surface-gaps.md`.
