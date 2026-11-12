---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Everything created through the mount is owned by root, and symlinks cannot be created

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

**Created entries are recorded as `0:0`.** `OverlayEngine.create` and `mkdir` take optional `uid` and
`gid` and default both to 0 (`src/sosfs/engine/overlay.py:367`, `:404`). The FUSE adapter passes
neither (`src/sosfs/fuse/adapter.py:148`, `:191`) and never reads the calling process's identity, so
every file and directory made through a mount is cataloged as owned by root, and `getattr` reports
that for as long as the entry is cataloged. Measured at `64faf27` through the engine API, two-tier
POSIX stack, tmpdir tiers, Python 3.15.2 on macOS, process `501:20`:

```
create -> entry = 0:0        mkdir -> entry = 0:0
after flush: source object owner = 501:0   catalog = 0:0
```

The source object gets the daemon's own uid because the commit's `chown` to 0 fails unprivileged and
is swallowed (`src/sosfs/tier/posix.py`, `_apply_meta`), so the catalog and the source disagree, and
the catalog is the authority for ownership (`AGENTS.md` § 5). The adapter half is read from the code:
no mount was driven. What a user sees: `ls -l` shows root on their own new files, and tools that
refuse a tree owned by someone else refuse this one. A version-control checkout inside the mount is
the common case.

**The same gap on the group side matters more on shared project storage**, where a setgid project
directory is the normal layout and the group decides quota accounting. A created file's group should
follow the parent directory when it is setgid, and the caller otherwise. Nothing records either.

**Symlinks can be read and never made.** The engine adopts a source symlink and serves `readlink`,
and the adapter defines no `symlink` callback, so `ln -s` through the mount fails with whatever the
binding's default returns. `PLAN.md` puts special files and hard links out of scope and says nothing
against symlinks; environments, build trees and "latest" pointers all make them.

**Unsupported operations do not say so.** `link` is out of scope by design and is not refused with a
chosen errno. No `access` callback exists and the mount does not pass `default_permissions`
(`src/sosfs/fuse/mount.py:310`), so permission bits are stored and reported and nothing enforces them.
That may be the right behaviour for a single-user mount; it is not written down as a decision.

## Why it was deferred

Not deferred: found by an out-of-cycle review of the whole tree at `64faf27` (2026-11-11).
Pre-existing; the defaults date from the Phase 4 namespace work and the adapter from Phase 6. No test
asserts the owner of a created entry.

## Outcome / vision

A file a user creates through the mount is theirs in every view: `ls -l`, the catalog, and the source
once it commits. `ln -s` works. An operation the overlay does not support fails with one documented
errno.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a file or directory is created through the FUSE adapter, its cataloged uid SHALL be
  the calling process's, and its gid the caller's, or the parent directory's when that directory is
  setgid.
- **R2** — WHEN that entry commits to a POSIX source, the source object's owner and group SHALL equal
  the catalog's wherever the daemon's privileges allow, and a test SHALL assert the catalog and the
  source agree for an unprivileged daemon.
- **R3** — WHEN `symlink(target, path)` is called, the engine SHALL create a symlink entry that
  `readlink` returns, and it SHALL reach the source through the write-back queue like any created
  entry.
- **R4** — WHEN `link` is called, the adapter SHALL fail with one errno named in the documentation.
- **R5** — `PLAN.md` SHALL state whether the mount enforces permission bits (`default_permissions`) or
  only records them, and the mount options SHALL match.

## Notes

- A conformance suite would find the rest of this class by measurement rather than by reading. Running
  one against a real mount is a natural first payload for `issues/ci-integration-lane.md`, with each
  failure triaged as a fix or a documented non-goal.
- Entries adopted from the source keep the source's ownership, so the defect shows only on entries
  created through the overlay.
- Related: `issues/engine-error-mapping.md` (the other place the VFS surface breaks POSIX);
  `issues/hpc-deployment.md` (setgid project directories); `AGENTS.md` § *Invariants* § 5, § 9 ("the
  adapter holds no policy": reading the caller's identity is translation, not policy).
- Found by: an out-of-cycle review of the whole tree at `64faf27` (2026-11-11).
