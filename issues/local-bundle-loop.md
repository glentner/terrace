---
status: unshaped
kind: test
appetite: small
lane: public
---

# Drive the bundle loop end to end on a local object store

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The loop that sets the project apart has never run. In the maintainer's words of 2026-11-11, what
makes this different from other virtual archive filesystems is write-back with delayed re-aggregation
into the compressed bundles at the source. End to end that is: bundles at an object-store source; a
bundle staged and indexed below it; members read through the overlay; members written, created,
renamed and removed; the bundle rebuilt on a delay and pushed back as one object; and a fresh engine
reading the result from the bucket.

**The story, as the maintainer told it on 2026-11-11:**

> The big user-story I want to tell is that we define a TOML config that points to S3/SFTP tape or
> object store which contains either a single bundle or a multi-bundle (think 10k tar.gz from full
> imagenet), and the config defines a cascade down to Lustre Scratch where the bundles would be
> lazily pulled and decompressed (but not unpacked in full yet), the node-local /dev/shm lands single
> files from that .tar bundles as necessary, and writes them back up to node-local /tmp, and then
> back up to Lustre Scratch along-side the .tar sources. As changes landed to working copies in
> /dev/shm, those altered files either due to eviction or write-back delay get committed back up the
> stack. The source bundles and altered versions of files alongside them on Scratch represent durable
> storage (if a job halts we're okay and can recover). The source bundles are repacked either due to
> quota (eviction) or write-back delay themselves and put back to the source over S3/SFTP. A 'commit'
> flushes everything back up the stack.

**His clarifications of the same date**, paraphrased:

- every stage tier has a size limit, a count limit and a write-back delay. Lack of room in a tier
  pushes a copy up the stack, which may cause the same in the tier above; on a read-only workload
  that is the only force. A changed copy left alone for its delay is pushed up as well, whatever the
  tier's room, and that may cascade for the upper tier's own reasons;
- a push goes one tier up. A write-back in the lowest tier never goes "all the way up" to repack the
  bundle;
- a signal, from the user or the batch scheduler, commits what has changed up to the lowest durable
  tier, so the mount can stop and the node be released. Later, from a login node, a full commit
  rebuilds the bundles that changed, and does not delete the staged bundles, so the next mount does
  not pull them again;
- a clean copy pushed out of a tier is kept one tier up when that tier has room, so a second read
  comes from node-local disk and not from the bundle. The database tracks every copy and knows where
  the fastest one is;
- a soft unmount, asked for directly or by a signal, ends with the database recording every modified
  file on the first durable tier and nothing on any tier below it;
- mounting one spec on many nodes at once is wanted and is not a goal for the first release
  ([`issues/multi-node-mounts.md`](multi-node-mounts.md)).

The scenario below is that story at the size of a laptop: an object store for the source, a
directory for scratch, two more for the node-local tiers.

Every piece exists alone and none can be joined:

- `ArchiveTier` has 36 unit tests, some over an S3 inner location (`tests/tier/test_archive.py`), and
  the engine runs over an S3 prefix source in 8 (`tests/tier/test_s3_engine.py`), both against a
  moto server the suite starts itself (`tests/tier/conftest.py:35`);
- the spec parser refuses every archive type (`SUPPORTED_TYPES`, `src/sosfs/tier/spec.py:25`);
- the engine's tier construction has no archive branch (`src/sosfs/engine/overlay.py:127-147`);
- nothing under `engine/` calls `ArchiveTier.resync`, the one call that pushes a bundle to its source.

So nobody can drive the loop: not the maintainer at a terminal, not an agent in a cycle, not CI. Every
design decision about it is being made by reading, and the cycles since adoption show what that costs:
they hardened a mechanism for a source type the loop does not use.

What a developer machine already has: the moto server, which is pure Python and needs no container;
a MinIO compose file for the integration marker (`docker-compose.yml`); and, on the maintainer's Mac,
a FUSE library the binding loads under Python 3.15 (checked 2026-11-11 by importing the binding; no
mount was attempted, so a working local mount is unverified).

## Why it was deferred

Never proposed. The old phase outline built archives bottom-up and stopped below the engine, and the
roadmap after it queued their test coverage behind the code (`issues/archive-tier-test-coverage.md`).
Found by an out-of-cycle review of the whole tree at `64faf27` and the maintainer's statement of what
the project is for (2026-11-11).

## Outcome / vision

One command stands the loop up on a laptop, and one scenario says how far the tree gets through it.
The scenario goes green stage by stage as the cycles behind it land, so the file is the loop's
progress record. An oracle that is nothing but a directory tree and `tarfile` says what the bucket
should hold.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — A helper under `tools/` SHALL start a local object store, fill a bucket with N generated
  `tar.gz` bundles, write an overlay spec over it with POSIX stages in a temporary directory, and
  print the spec's path; and SHALL tear all of it down on request. It SHALL need no container.
- **R2** — A scenario test SHALL, on that spec: list and read members and match the oracle's bytes;
  modify one member, create one, rename one and remove one inside a single bundle; advance the
  injected clock past that bundle's re-aggregation interval and poll; then download and unpack the
  bundle's object and match the oracle's tree, with every other bundle's object byte-identical to
  before; then start a second engine on fresh state over the same bucket and read the same tree.
- **R2a** — THE scenario SHALL include a pressure stage: with the fastest tier's limits set below the
  bytes read, every read SHALL still succeed, each copy pushed out SHALL be found one tier up, and the
  stage SHALL make no write to the source.
- **R2b** — THE scenario SHALL include a release stage: after changes in the fastest tier, the
  engine is told to detach, and every change SHALL then be on the durable tier with the source
  untouched, and no copy recorded on any node-scope tier. A second process, given the spec and the
  durable tier and nothing of the first
  process's node-local tiers, SHALL run the full commit; the bundle's object SHALL then match the
  oracle, and the staged bundle SHALL still be on the durable tier, so a mount after it pulls
  nothing.
- **R3** — Each stage of the scenario the tree cannot yet pass SHALL be a strict expected failure
  that names the seed owning it, so a stage that starts passing fails the run until its marker is
  removed.
- **R4** — THE scenario SHALL count the requests each stage makes to the source by kind, and SHALL
  assert that one re-aggregation of one dirty bundle costs one write of one object.
- **R5** — THE scenario SHALL also run against MinIO behind the integration marker, and through a
  real mount where a machine provides one, asserting a pass count.

## Notes

- This is the before to `issues/archive-tier-test-coverage.md`'s after. That seed covers the adapter
  through the engine once the code exists; this one states the target the code is built toward.
- The stages map onto seeds in order: reaching a bundle from a spec file
  (`issues/archive-spec-plumbing.md`); a clean transition that can be trusted on the copies a
  re-aggregation reads (`issues/racing-write-marked-clean.md`); what pressure does
  (`issues/eviction-one-hop.md`); the synchronization model
  (`issues/namespace-sync-redesign.md`, first stage); the journal bracket and the lock around a
  rebuild (`issues/archive-journaling-and-locking.md`); the delayed re-aggregation itself
  (`issues/archive-finalization-integration.md`); many bundles (`issues/sharded-archive-sources.md`).
- A local bundle is kilobytes, so the whole-blob reads the sharding seed calls a prerequisite do not
  block this. R4's request counts are where that cost shows up first.
- Open question — **which mount.** The engine API drives the loop on any machine. A real mount needs
  the integration lane (`issues/ci-integration-lane.md`) or a development machine whose FUSE library
  and kernel extension both work.
- Related: `PLAN.md` § *Tier protocol* → ArchiveTier (staged re-sync), § *Testing strategy*.
- Found by: an out-of-cycle review of the whole tree at `64faf27` (2026-11-11).
