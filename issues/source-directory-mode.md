---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A source directory with no mode, or without owner search, blocks every promotion below it

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs are negotiated with a human.

## Problem

`_adopt` (`src/terrace/engine/overlay.py:349`) catalogs a source entry whose mode is `None` as `0o644`,
whatever its kind. A promotion then creates the file's ancestors on the stage with the cataloged mode
(`materialize_dirs`, `src/terrace/engine/transfer.py:37`, through `PosixTier.mkdir`, which sets the exact
bits, `src/terrace/tier/posix.py:192`). A stage directory of `0o644` cannot be searched, so the promotion
of any file below it fails `EACCES` for a user who is not root.

- **An S3 source.** `S3Tier` reports every directory with no mode (`src/terrace/tier/s3.py:122`, `:130`,
  `:149`), so each is cataloged `0o644`, and opening a file one level down or deeper fails as above.
  This is read from the code, by the same mechanism measured over a ZIP whose directories carry no
  mode (`spec/archive-spec-plumbing/research/06-read-path.md` § 2). It is not reproduced on S3: no S3
  test promotes a nested file adopted from the source.
- **A directory whose own mode lacks owner `w` or `x`** (`0o555`, say), at any source. The stage copy
  gets the exact bits, and nothing can be created inside it, so every promotion below it fails the
  same way (brief 06 § 3).

A host that runs the tests as root hides both.

## Why it was deferred

Pre-existing on `main` for any source. `archive-spec-plumbing` met it over a ZIP and fixed it in the
archive adapter, which now reports `0o755` for a directory with no mode, so its read path stays open
without touching `_adopt`. `engine/overlay.py` is on the high-blast-radius list, and the S3 source and
the owner-bits case are outside that cycle's GOAL.

## Outcome / vision

A file below any source directory can be promoted and read, whatever mode the source reports for
the directories above it, and the overlay still presents each directory's own mode.

## Sketch of the acceptance criteria

- **R1** — WHEN a source reports a directory with no mode, the engine SHALL catalog it with a mode
  that lets its owner search it (`0o755`, as the engine's root has).
- **R2** — WHEN a file is promoted below a source directory whose mode lacks owner `w` or `x`, the
  promotion SHALL succeed, and the directory SHALL keep its own mode in what the overlay presents.
- **R3** — A unit test SHALL promote a nested file below a mode-less S3 directory, against moto, as a
  user who is not root.

## Notes

- Shaping decides whether the stage's ancestors take the source's bits with owner `wx` added, or are
  created searchable and given their bits after the promotion, and what an eviction that empties such a
  directory leaves behind.
- Related: `issues/archive-tier-test-coverage.md`; `spec/archive-spec-plumbing/` (the archive case, fixed
  in the adapter).
- Found by: `archive-spec-plumbing`'s research brief 06, at its plan step (2026-11-28).
