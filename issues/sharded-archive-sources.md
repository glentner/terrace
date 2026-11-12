---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Sharded archive sources

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`,
> and that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped`
> means none of that has happened yet.

## Problem

**The flagship use case.** The owner's statement of intent (chat, 2026-11-04), paraphrased: this is
what the project is for. An HPC center should be able to refuse a 100-million-inode quota request
and still serve the researcher, by letting an AI training dataset live as, say, ten thousand
`.tar.gz` shards of about ten thousand images each. A spec such as `s3://bucket/data/*.tar.gz` at the
top of the tier list cascades down just in time: a staged copy of a shard lands on durable storage
below the archive tier (the archive itself on HPSS or S3 above); each shard is parsed for metadata
only, on the first crawl, and that index persists; the shard is unpacked alongside the staged `.tar`
only when its subtree is actually used; and an individual file is pulled into the lowest tier on its
first read. Ten thousand files on the shared filesystem stand in for a hundred million.

**Sharded sources.** `PLAN.md` § *Tier protocol* specifies them fully: `prefix/*.tar.gz`, each shard
maps to one top-level directory whose name matches the shard's stem, strictly enforced — the mount is
rejected if a shard lacks that directory, and for a sharded source the root holds only shards, so
root-level files are refused. The shard set is dynamic: glob at mount, and a top-level create or
delete adds or removes a shard in the source's default format. Shards are pulled and indexed lazily,
one at a time, only when their subtree is first entered, and the index persists across remounts, so
an unread shard among thousands costs only the initial listing. `TIER_KEYS`
(`src/sosfs/tier/spec.py:28`) carries no `sharded` key today, so this needs spec surface on top of
whatever [`issues/archive-spec-plumbing.md`](archive-spec-plumbing.md) adds.

The owner's cascade and `PLAN.md`'s design agree on the shape and differ in one step. `PLAN.md`
(`PLAN.md:154-155`) pulls and indexes a shard together, when its subtree is first entered; the
owner's version separates a metadata-only parse on first crawl from the unpack on first use. A
`.tar.gz` is a gzip stream with no random access, so a metadata-only parse still reads and
decompresses the whole shard once. What the separation can save is the unpacked `.tar` on durable
storage for shards that are listed but never read, not the transfer.

**The memory-shape constraint that runs underneath all of it.** Archive I/O is whole-blob in memory
today. `S3InnerLocation.open_read` (`archive.py:148-151`) does
`io.BytesIO(bytes(obstore.get(...).bytes()))`; `atomic_write` (`:153-159`) buffers the whole
replacement before the PUT; `_repack` holds both at once; and `ArchiveTier.read`
(`archive.py:486-501`) extracts the entire member and then slices, re-opening the inner location for
every member read — a full object GET per file read against an S3-backed archive. An S3-backed
`.tar.gz` source larger than RAM therefore takes the mount daemon out at index time. Sharding is
partly a response to this: it bounds both the reconstruct cost and the resident footprint to the
touched shard. It does not remove the constraint, because one shard still has to fit in memory, and
the other half of the answer — a streaming or ranged read — is a change to the `InnerLocation`
contract rather than a new implementation of it.

**The ranged/streaming read is a prerequisite, not an optimization.** At the owner's scale a shard of
ten thousand images is gigabytes, and the cascade above stages it to durable storage precisely so
that no tier holds it in memory. Today's `InnerLocation` (`archive.py:54-75`) offers only
`open_read()` returning a whole buffered handle, so the staging copy itself passes through RAM. This
cycle either lands a streaming `open_read` (and a ranged read for member access into the staged
`.tar`) or depends on a cycle that does.

## Why it was deferred

Scheduled post-v0.1.0 by design: `PLAN.md` § *Technology decisions* ("Sharded sources (`*.tar.gz`)
and SFTP/Globus inner backings are post-v0.1.0", `PLAN.md:481`), § *Phase outline* (phase 12,
`PLAN.md:578-581`) and the worked spec example (`PLAN.md:442`, "post-v0.1.0").

The owner's 2026-11-04 statement makes it the flagship use case, and the triage of the same date
places it before the operations surface (`audit`, `commit`) and before v0.1.0. **That contradicts a
recorded decision.** Under the plan-drift rule (`AGENTS.md` § *Project*, the `PLAN.md` entry of the
document map) the cycle that promotes this seed reconciles `PLAN.md` first — the § *Technology
decisions* row, § *Phase outline* and the example's comment — with the maintainer, before any code.

It also depends on machinery that does not exist yet: an archive tier is unreachable from a spec file
([`issues/archive-spec-plumbing.md`](archive-spec-plumbing.md)), archive mutations are outside the
intent journal and any tier-wide lock
([`issues/archive-journaling-and-locking.md`](archive-journaling-and-locking.md)), and nothing
batches finalization ([`issues/archive-finalization-integration.md`](archive-finalization-integration.md)).
Sharding extends all three. Pre-existing on `main`; nothing here was introduced by a cycle.

## Outcome / vision

A dataset that lives as ten thousand `.tar.gz` shards on S3 or an archive system mounts in the time
it takes to glob a prefix. A crawl of the tree costs one metadata parse per shard, once, persisted.
Reading one image stages and unpacks exactly one shard on durable storage and promotes exactly one
file to the fastest tier. The shared filesystem holds shards, not members, so the inode count a
center has to grant scales with the shard count. No step holds a whole shard in memory.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes. Several of these can only be
observed against MinIO or a real endpoint, and CI runs no integration job.

- **R1** — WHEN a sharded source is mounted, `readdir` at the mount root SHALL list one directory per
  shard from the glob alone, and no shard SHALL be opened until its subtree is first entered —
  asserted by patching `InnerLocation.open_read` to raise.
- **R2** — IF a shard lacks a single top-level directory matching its stem, THEN the mount SHALL be
  refused at parse or start time with a message naming the shard and the entries it found, rather
  than failing at first open.
- **R3** — WHEN a shard's subtree is first listed, the shard's member index SHALL be persisted, and
  WHEN the overlay is remounted, listing that subtree SHALL NOT open the shard again — asserted by
  patching `InnerLocation.open_read` to raise on the second mount.
- **R4** — WHEN a file under a shard is first read, the file SHALL be resident on the lowest tier and
  exactly one shard SHALL have a staged copy on the durable tier, asserted from the residency rows.
- **R5** — WHEN files under one shard's subtree are written and the overlay drains, exactly one shard
  object SHALL change, asserted by comparing every shard's `signature()` before and after.
- **R6** — WHEN a top-level directory is created in a sharded source and the overlay drains, a new
  shard SHALL appear in the source's default format; WHEN that directory is removed and the overlay
  drains, the shard SHALL be gone.
- **R7** — WHEN an S3-backed shard is pulled and indexed, the mount daemon's peak resident size SHALL
  scale with neither the shard nor the shard set but stay bounded by a fixed buffer, measured over a
  synthetic multi-shard source whose largest shard exceeds that bound.

## Notes

- Split from [`issues/extended-archive-sources.md`](extended-archive-sources.md) on 2026-11-04, with
  [`issues/transfer-priority-lane.md`](transfer-priority-lane.md), as that seed's own Notes
  recommended. The sharded-sources text, the memory-shape constraint and the shard-identity question
  moved here verbatim; R1–R2 and R5–R6 are that seed's R2–R5, and R7 tightens its R7 to match the
  ranged-read prerequisite.
- Open question — **is a shard a managed location?** If each shard carries its own marker and identity
  the audit can attribute a vanished shard precisely, at the cost of a marker inside every archive; if
  the shard set is one location, a partially-populated set is indistinguishable from an evaporated
  one. The audit's tier-evaporation path depends on the answer.
- Open question — **an archive system as the top tier.** The owner names HPSS alongside S3. No
  `InnerLocation` backing for an archive system exists (`inner_location`, `archive.py:162-166`, has
  POSIX and S3 branches only), and recall latency from tape is minutes, which raises the same
  blocking-promote question [`issues/extended-archive-sources.md`](extended-archive-sources.md)
  records for Globus. Whether that backing belongs here or in that seed is for shaping.
- Open question — **"unpacked alongside the staged `.tar`".** Whether first use extracts members to
  files on the durable tier, or keeps PLAN's offset reads into the staged `.tar`, decides the inode
  cost on the durable tier and therefore whether the quota argument above holds there too.
- Related: [`issues/transfer-priority-lane.md`](transfer-priority-lane.md) — per-shard lazy pulls
  touch the transfer pool, and an interactive read should not wait behind a warming campaign;
  [`issues/archive-tier-test-coverage.md`](archive-tier-test-coverage.md) builds the engine-level
  archive fixtures these tests need; [`issues/hpc-deployment.md`](hpc-deployment.md) is where the
  researcher this serves meets the product. Root `PLAN.md` → § *Tier protocol*, § *Technology
  decisions*, § *Phase outline*.
- 2026-11-11 (whole-tree review at `64faf27`): three notes.
  - **Reading is half of it.** The maintainer's statement of 2026-11-11: the read side is a large
    part of the project, and what sets it apart is write-back with delayed re-aggregation into the
    compressed bundles at the source. A shard is therefore also the unit of write-back: a change
    inside it marks that shard dirty, and a re-aggregation rebuilds that shard alone
    ([`issues/archive-finalization-integration.md`](archive-finalization-integration.md)). A read-only
    mount ([`issues/read-only-mount.md`](read-only-mount.md)) is a mode of this, not the goal.
  - **A member can be served without being unpacked.** The staged shard is an uncompressed tar and
    `archive_index` records each member's data offset. A handle that is the tar's descriptor plus a base
    offset and a length serves `read` with one `pread`, with no promotion, no per-member transaction and
    no inode on any lower tier. Unpacking a shard to files spends on each lower tier the inodes the
    shards were made to save. The owner's cascade pulls each file down on first read; whether the unit
    that cascades is the member or the shard is worth deciding with a measurement
    ([`issues/read-path-metadata-writes.md`](read-path-metadata-writes.md) has the per-file cost).
  - **Many mounts read one dataset.** A multi-node job mounts on every node and a sweep runs many jobs at
    once. One mount per overlay means one staged copy per mount
    ([`issues/read-only-mount.md`](read-only-mount.md) R6).
- Found by: scheduled post-v0.1.0 work carried forward from `PLAN.md` § *Phase outline*, promoted in
  priority by the owner's statement of intent (2026-11-04). The memory-shape constraint was confirmed
  by the invariant audit run during the software-factory port (2026-08-22) — tier-layer review.
