---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Share the staged bundles between read-only mounts

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The maintainer's ruling of 2026-11-11 keeps everything in one database per overlay, and with it sets
many writers on one overlay aside for a later day
([`issues/multi-node-mounts.md`](multi-node-mounts.md)). In the same breath: "For the time being,
maybe we can support 'shared' for read-only on the top durable storage with independent tiers
(node-local or otherwise) below it."

That is the half of the multi-node wish a dataset needs. A multi-node job mounts on every node, and a
sweep runs many jobs over one dataset. Each mount is its own overlay, with its own spec, database and
lower tiers. As built each would also pull and decompress its own copy of every bundle it touches:
as many staged copies of the dataset on scratch as there are mounts, and as many pulls from the
source, which for a tape is the expensive part. Sharing the staged bundles makes the large, unchanging
part common and leaves everything that changes private: each mount's database, its lower tiers, its
eviction clocks.

**Why this half is tractable.** A staged bundle is a function of its source object and nothing else:
every mount that pulls it produces the same bytes. So concurrent pulls are safe without a lock, as
long as each writes under a temporary name and renames into place. A lock on the bundle saves doing
the work twice and guards nothing else, so one left by a killed mount can lapse at a timeout with no
risk. That matters because a killed mount is the ordinary end of a batch job, and the lock rules a
sibling project arrived at on shared filesystems (`uv-manager`: only the maker removes a lock, nobody
asks whether a holder is alive, a stranded lock waits for a person) are affordable only where a
stranded lock blocks no one.

**What forbids it today:**

- a tier root carries a marker naming one overlay, and a second overlay reads it as `FOREIGN`
  (`src/terrace/state/lifecycle.py:197-198`) and is refused at start;
- a bundle's member index lives in the overlay's own database (`archive_index`,
  `src/terrace/state/schema.sql:135-145`), so a second mount would scan every bundle again;
- a tier's limits and its eviction assume one owner;
- a bundle's working copy sits at a path of its own outside every tier
  ([`issues/archive-spec-plumbing.md`](archive-spec-plumbing.md)).

## Why it was deferred

`PLAN.md` puts multi-client coherence first among its non-goals, which was written with writers in
mind. Raised by the maintainer on 2026-11-11 as something the first release may be able to carry.

## Outcome / vision

A thousand tasks mount the same dataset. The first to need a bundle pulls it; the rest read it. Each
task has node-local speed in tiers nobody else sees. Nothing any one of them does, or fails to do, can
harm another.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — A spec SHALL be able to mark its top durable tier as shared for reading. The key SHALL NOT
  be `scope`, whose `shared` already means "survives the node".
- **R2** — A mount over a tier so marked SHALL be read-only: every mutating call fails with `EROFS`.
- **R3** — WHEN N mounts need one bundle, each SHALL read the same staged bytes, and once one pull has
  finished no later mount SHALL make a request to the source for that bundle. Pulls that overlap
  SHALL be harmless: correctness SHALL NOT depend on any lock.
- **R4** — WHEN a mount is killed during a pull, nothing it leaves SHALL be taken for a staged bundle,
  and nothing it leaves SHALL delay another mount's pull beyond a stated timeout.
- **R5** — A staged bundle's member index SHALL be stored beside it once and read by every mount.
  Each mount's database still records every copy that mount holds and where its fastest one is.
- **R6** — No mount SHALL evict from the shared tier or count its limits against it. Reclaiming it
  SHALL be an explicit command.
- **R7** — WHEN a bundle changes at the source, its staged copy SHALL be recognized as stale by a
  signature recorded beside it, and the new one SHALL be staged under another name, so mounts reading
  the old one are not disturbed.
- **R8** — Two mounts sharing a tier SHALL share nothing else: separate state directories, separate
  lower tiers, and no file either writes that the other reads, outside the shared tier.

## Notes

- Pairs with [`issues/read-only-mount.md`](read-only-mount.md), whose R6 asks this question, and
  follows the read side of the bundle loop
  ([`issues/sharded-archive-sources.md`](sharded-archive-sources.md)).
- The plan cycle's second prototype, two processes pulling one bundle onto one tier, is this seed's
  R3 and R4 in miniature ([`issues/plan-the-bundle-loop.md`](plan-the-bundle-loop.md) R9).
- Open question — **staging ahead of time.** A command that pulls a dataset's bundles before any job
  starts would make every mount a pure reader and remove pulls from job time altogether.
- Open question — **"or otherwise".** The tiers below the shared one may be node-local "or
  otherwise". A mount with a private durable tier beneath the shared one could take writes there, but
  re-aggregating them into bundles other mounts are reading is many writers at the source, which is
  the day this is not.
- 2026-11-16 (`spec/plan-the-bundle-loop/`): the root `PLAN.md` § *The tier model* (g) states what
  sharing needs, from the plan cycle's second prototype (`spec/plan-the-bundle-loop/prototype/RECORD.md`:
  504 of 504 processes read the oracle's bytes). A marker of its own kind whose text is a function of
  the source alone, since with a writer id and a time in it one mount read a marker that was then
  replaced; one member index beside each staged bundle, renamed into place before the bundle, which
  is the index `issues/durable-tier-record.md` writes; a staged name carrying the source object's
  signature, a fetch to a name unique to the process and a rename into place, and a lock file that only
  saves a second fetch. **Work that moves here:** what it costs a mount to learn a bundle's signature
  (a request per bundle, one list per mount, or none if it trusts what is staged) is left open by that
  record, and is this seed's to choose. Not tested there, and this seed's too: more than two mounts,
  several waiters timing out at once, and a source with no ETag, such as SFTP, where the signature must
  come from something else (`issues/sftp-inner-location.md`).
- Sources: `uv-manager`, `README.md` § *Design notes* (the lock rules).
- Found by: the maintainer's ruling of 2026-11-11.
