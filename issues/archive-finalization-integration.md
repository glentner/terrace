---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Batch archive finalization at drain and commit

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs are negotiated with a human.

## Problem

`PLAN.md:135-136` states the requirement in one clause: an archive commit re-packs "one re-pack per
commit/flush, never per-file". The adapter re-packs per file. Every mutating call ends in
`_repack` — `put` (`archive.py:594`), `set_meta` (`:608`), `delete` (`:621`), `mkdir` (`:640`),
`rmdir` (`:655`), `rename` (`:677`) — and `_repack` (`archive.py:529-538`) opens the current
archive, writes a complete replacement through `InnerLocation.atomic_write`, renames it over the
original, then calls `reindex()`, which rescans every member and replaces every `archive_index` row
in one transaction (`archive.py:325-333`). Nothing in the tree batches. The cost of a drain is
O(members × operations): 1000 files committed to an archive source performs 1000 whole-archive
rewrites and 1000 full re-indexes. On a 200 GB tar that is the difference between a five-minute
drain and hours, and the drain is what stands between a job and the end of its allocation.

The staged half of the design is not merely unbatched — it is unreachable. `grep -rn "resync" src`
returns the vocabulary declarations in `journal.py:30` and `schema.sql:125` and the definition in
`archive.py`; **no module under `engine/` calls `resync` at all.** `resync(now, force=True)` exists at
`archive.py:427` precisely so drain and commit can waive the `max_frequency` rate limit, and nothing
invokes it. `drain()` (`engine/drain.py:74-109`) replays queued namespace ops, commits dirty rows
most-volatile-first, and returns without touching the source tier's adapter. Its own docstring
(`drain.py:77-81`) asserts that "re-sync rate limits [are] waived by construction … (archive re-sync
limits arrive with Phase 8 and take the same path)" — a stale phase pointer describing a path that
does not exist. The old ROADMAP checked the waiver off.

`flush` (`overlay.py:870` (holds at `141aa86`)) and the eviction manager's commit-upstream will drive the same per-file
`put` into an archive source the moment archives are reachable, so this is not only a drain concern.

**The memory shape is a constraint on whatever design comes out of this, not a separate problem.**
`S3InnerLocation.open_read` (`archive.py:148-151`) buffers the whole object into a `BytesIO`;
`atomic_write` (`:153-159`) buffers the whole replacement into another and PUTs `buffer.getvalue()`,
which is a third copy; and `_repack` (`:531-537`) holds the read handle open across the write, so a
re-pack of an S3-backed archive costs roughly three copies of the archive resident at once. An
archive larger than a fraction of RAM OOMs the mount daemon at index time, before any batching
question arises. Separately, `_extract` (`archive.py:402-410`) re-opens the location for every member
read, which for an S3-backed archive is a full object GET per file read. Batching changes how often
the re-pack cost is paid; it does not change the shape, and a design that accumulates N member writes
must not accumulate N member *bodies* in memory to do it.

## Why it was deferred

This is the last unchecked code item of the old Phase 9 — honestly unchecked, unlike the journaling
claim above it in the same phase. The four preceding Phase 9 items built the adapter bottom-up and
this is the top of the stack that was never attached.

**Unbuilt work, not a live defect.** Nothing constructs an `ArchiveTier` today, so nobody is paying
the per-file re-pack. It is not listed in
[`AGENTS.md`](../AGENTS.md) § *Invariants* § *Known open violations* for
that reason; the two archive rows there belong to the sibling cycles.

## Outcome / vision

The drain and commit paths know that an archive source is finalized once, at the end, rather than
continuously. A drain against a direct-update ZIP source rewrites the archive exactly once no matter
how many files it commits. A drain against a staged tar.gz source pushes exactly once, with the
rate limit waived, and says so in its result. Between finalizations, member writes land somewhere
cheap — staged mode already has the working `.tar`; direct-update needs the equivalent — and the
reconstructed archive is indistinguishable from what a per-file sequence would have produced.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. The observable in most of them is a count at the drain or
commit boundary, not a wall-clock time.

- **R1** — WHEN 50 files are committed to a ZIP archive source in one drain, the source archive
  SHALL be rewritten exactly once, observed as a single change in the inner location's `signature()`
  across the drain, and all 50 members SHALL be present afterwards.
- **R2** — WHEN that drain runs, exactly one `intent` row lifetime for the source tier SHALL be
  observable, not one per file. Depends on
  [`archive-journaling-and-locking.md`](archive-journaling-and-locking.md) for the bracket to exist.
- **R3** — WHEN a staged `tar.gz` overlay is drained, the drain SHALL push to the source exactly
  once with `max_frequency` waived, and `DrainResult` SHALL report the push as a counted field.
- **R4** — WHILE a staged archive source's `max_frequency` interval has not elapsed and no drain or
  commit is running, a write-back poll SHALL leave the source archive's `signature()` unchanged while
  the staging `.tar` carries the change.
- **R5** — WHEN an overlay with an archive source is unmounted after N file operations, the number
  of whole-archive rewrites SHALL be independent of N, and the source archive's member set, modes
  and mtimes SHALL equal what the same operations produce against a plain-directory oracle.
- **R6** — WHEN `sosfs commit` finalizes an archive source offline, it SHALL perform one re-pack, or
  one reconstruct-and-push, for the whole commit — the same post-condition as R1 and R3 from the
  offline path. Implemented by
  [`offline-commit-command.md`](offline-commit-command.md); named here so the two cycles agree on one
  contract rather than two.
- **R7** — WHEN an S3-backed archive is finalized, the process SHALL NOT hold more than a stated
  number of whole-archive copies resident, asserted by a `tracemalloc` peak measurement in the test
  and recorded in `PLAN.md` so a reader can size the mount host. Observable against moto under
  `-m unit`; the MinIO version needs `docker compose up -d --wait` and the integration marker.

## Notes

- Third of four archive cycles. Requires [`archive-spec-plumbing.md`](archive-spec-plumbing.md) —
  nothing can construct an archive tier until it lands — and requires
  [`archive-journaling-and-locking.md`](archive-journaling-and-locking.md), because one intent
  bracket around one large re-pack is the point, and batching on top of an unjournaled, unlocked
  re-pack widens the crash window rather than narrowing it.
  [`archive-tier-test-coverage.md`](archive-tier-test-coverage.md) follows and proves it.
- [`offline-commit-command.md`](offline-commit-command.md) carries "batched archive finalization" as
  one of its own bullets; whichever lands second inherits the other's contract.
  [`crash-fault-injection-suite.md`](crash-fault-injection-suite.md) needs a mid-re-sync kill point,
  which cannot exist until `resync` has a caller.
- `PLAN.md` → ArchiveTier, direct-update and staged re-sync (:129-147); → Deferred commit;
  → `sosfs commit` (:300-334). The `drain.py:77-81` docstring is invalidated by this cycle and is a
  same-commit-rule target.
- Open questions a shaping conversation has to settle:
  1. **Where does batching live?** A `Tier`-protocol extension (`begin_batch` / `end_batch`, or a
     context manager) that POSIX and S3 implement as no-ops keeps `isinstance` out of `engine/`, at
     the cost of widening a protocol whose small size is a stated technology decision and of a
     partial-batch failure mode every adapter then has to describe. An archive-only escape hatch the
     drain special-cases keeps the protocol small and puts tier-shaped knowledge in `engine/`, which
     the layering rule tolerates and the design does not love.
  2. **Where do member writes land between finalizations for a direct-update archive?** Staged mode
     already has the working `.tar`. Giving direct-update the same working copy makes both modes one
     mechanism and doubles the disk footprint of a ZIP source; keeping direct-update writing through
     means the batch has to hold member bodies somewhere, and the memory shape above says that
     somewhere is not RAM.
  3. **Does the staging `.tar` become a managed location with its own marker?** If not, the audit
     cannot distinguish a current staging copy from a stale one. This is the same question
     [`archive-journaling-and-locking.md`](archive-journaling-and-locking.md) asks from the populate
     side, and the two need one answer.
  4. **Does a batch boundary need a new journal action or a new `writeback` op?** Both are schema
     `CHECK` vocabularies, and `SCHEMA_VERSION` has no migration machinery — a bump makes every
     existing state database unopenable.
  5. **Is the `reindex()` inside `_repack` separable from the re-pack?** A batch that re-packs once
     but still re-indexes per member has moved the wrong cost.
- 2026-11-11 (whole-tree review at `64faf27`): the maintainer names this the project's flagship:
  write-back with delayed re-aggregation into compressed bundles at the source. Two consequences. It
  leads the roadmap's first section, driven on a developer machine against a local object store before
  any cluster work ([`issues/local-bundle-loop.md`](local-bundle-loop.md)). And batching follows from
  the synchronization model; it is not an addition to the adapter. If a change marks its bundle dirty
  and a re-aggregation rebuilds the bundle from the catalog and the staged bytes
  ([`issues/namespace-sync-redesign.md`](namespace-sync-redesign.md), decision 3), nothing re-packs
  per file and the engine has one call to make, on a clock.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the
  remaining-work brief (dossier 2) and the tier-invariants brief.
