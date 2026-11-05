---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Extended archive sources

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

Two capabilities sit behind one entry, deliberately post-v0.1.0 in both `PLAN.md` § *Technology
decisions* ("Sharded sources (`*.tar.gz`) and SFTP/Globus inner backings are post-v0.1.0") and
§ *Phase outline*. None of it is a defect; all of it is unbuilt work whose shape is already decided.
Sharded sources and the transfer priority lane were split out on 2026-11-04 into
[`issues/sharded-archive-sources.md`](sharded-archive-sources.md) and
[`issues/transfer-priority-lane.md`](transfer-priority-lane.md).

**SFTP and Globus inner locations.** `InnerLocation` (`src/sosfs/tier/archive.py:53-75`) is a
`runtime_checkable` Protocol with one attribute and three methods — `signature()`, `open_read()`,
`atomic_write()` — and `inner_location()` (`archive.py:162-166`) is the two-branch selector that
builds one from a parsed `location` spec. Both backings are additive: a class and a branch. Globus is
a submit-wait shim on the top source rather than a streaming backing, which makes it a different kind
of thing from SFTP wearing the same interface.

**Synthetic-subtree wrapping.** `PLAN.md` § *Future work* lists it as a Phase 12 refinement: wrap a
non-conforming archive or shard — one with no single top-level directory matching its stem — into a
synthetic subtree instead of refusing the mount.

## Why it was deferred

Not deferred by the audit: scheduled post-v0.1.0 by design, in `PLAN.md` § *Technology decisions* and
§ *Phase outline*. It also depends on machinery that does not exist yet — an archive tier is
unreachable from a spec file at all today
([`issues/archive-spec-plumbing.md`](archive-spec-plumbing.md)), nothing batches finalization
([`issues/archive-finalization-integration.md`](archive-finalization-integration.md)), and archive
mutations are outside the intent journal and outside any tier-wide lock
([`issues/archive-journaling-and-locking.md`](archive-journaling-and-locking.md)). Everything here
extends those three.

The whole-blob memory shape of archive I/O, recorded in
[`issues/sharded-archive-sources.md`](sharded-archive-sources.md), constrains an SFTP backing as much
as it constrains sharding: a three-method `InnerLocation` makes SFTP a download-the-whole-blob
adapter.

## Outcome / vision

An archive on an SFTP or Globus endpoint mounts and serves member reads like one on POSIX or S3. An
archive that does not match the shard convention is served rather than refused. The `InnerLocation`
protocol has grown by implementations, not by special cases.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes. Several of
these can only be observed against a real remote endpoint or MinIO, and CI runs no integration job.

- **R1** — WHEN a spec names `location = { type = "sftp", ... }`, the mount SHALL index the remote
  archive and serve member reads without materializing a local copy of the whole source. Needs a
  reachable SFTP endpoint; not checkable in CI as it stands.
- **R2** — WHEN an archive with no single top-level directory matching its stem is mounted, the mount
  SHALL serve its contents under a synthetic subtree named for the stem rather than refusing it
  (`PLAN.md` § *Future work*, the Phase 12 refinement).

## Notes

- **Split on 2026-11-04.** This seed carried five capabilities until then. Its own Notes recommended
  splitting the priority lane out; the triage of that date also split sharded sources out, because
  the owner named sharding the flagship use case. Both now have their own seeds:
  [`issues/sharded-archive-sources.md`](sharded-archive-sources.md) (with the memory-shape constraint
  and the shard-identity question) and [`issues/transfer-priority-lane.md`](transfer-priority-lane.md)
  (with the lane-count question). What stays here is SFTP/Globus and synthetic-subtree wrapping. R1 is
  unchanged; R2 is new, drafted from `PLAN.md` § *Future work*.
- Related: [`issues/archive-spec-plumbing.md`](archive-spec-plumbing.md) adds the `location` spec
  surface this extends with new backing types;
  [`issues/archive-finalization-integration.md`](archive-finalization-integration.md) builds the
  batching machinery per-shard reconstruction plugs into;
  [`issues/archive-journaling-and-locking.md`](archive-journaling-and-locking.md) adds the tier-wide
  lock that a per-shard lock generalizes;
  [`issues/archive-tier-test-coverage.md`](archive-tier-test-coverage.md) builds the engine-level
  archive fixtures these tests need;
  [`issues/documentation-and-v0.1.0.md`](documentation-and-v0.1.0.md) is the release this sits after;
  [`issues/sharded-archive-sources.md`](sharded-archive-sources.md) owns the ranged-read prerequisite
  the next question turns on. Root `PLAN.md` → § *Tier protocol* (`ArchiveTier` and sharded sources),
  § *Technology decisions* (the Archives row), § *Phase outline* (phase 12), § *Future work*.
- Open question — **does `InnerLocation` stay three methods?** An SFTP backing done honestly wants a
  ranged read, and adding one changes the contract for all four backings and rewrites `_extract`.
  Keeping the three-method protocol makes SFTP a download-the-whole-blob adapter, which for a remote
  multi-gigabyte `.tar.gz` is the behaviour sharding exists to avoid.
- Open question — **Globus semantics.** A submit-wait transfer takes minutes to hours. Does a promote
  block on it, fail with `EAGAIN` and let the caller retry, or serve a stale copy and reconcile later?
  That is a filesystem-semantics decision, not an adapter detail, and it belongs in `PLAN.md` first.
- Found by: scheduled post-v0.1.0 work carried forward from `PLAN.md` § *Phase outline*. The
  memory-shape constraint and the flat `TransferPool` queue were confirmed by the invariant audit run
  during the software-factory port (2026-08-22) — tier-layer and remaining-work reviews — and moved
  with the split.
