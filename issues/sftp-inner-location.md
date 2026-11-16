---
status: unshaped
kind: feature
appetite: small
lane: public
---

# A bundle source over SFTP

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The maintainer's user story of 2026-11-11 ([`spec/local-bundle-loop/GOAL.md`](../spec/local-bundle-loop/GOAL.md))
names the source as "S3/SFTP tape or object store". Two inner locations exist, a POSIX path and an S3
object (`src/terrace/tier/archive.py:77-166`). An archive tape reached over SFTP is storage a research
computing center already runs, and it is the storage that most wants bundles: few large objects,
written rarely, read whole.

`InnerLocation` is three methods, `signature`, `open_read` and `atomic_write`, and a bundle is fetched
and replaced whole. [`issues/extended-archive-sources.md`](extended-archive-sources.md) records that
as a limit of an SFTP backing ("a download-the-whole-blob adapter"). For a bundle on tape it is the
fit, so the adapter is small. What is unverified is everything around it:

- **The client.** No SFTP library is a dependency, and none has been tried on the free-threaded
  build.
- **Atomic replacement.** A temp name and a rename, which depends on what rename the server offers
  over an existing name.
- **Recall latency.** A bundle that has migrated to tape can take minutes to come back. An `open`
  that triggers the pull blocks for that long, which is acceptable; what else waits is the question.
  A promotion off the source holds one engine-wide mutex for its whole copy today (`AGENTS.md` § 10),
  so one recall would stall every call that touches the source.
- **Credentials.** Keys and agents, host-key checking, and nothing secret in a spec file.
- **A local server for tests.** The loop runs against a local object store with no container; the
  same is wanted here, and which server provides it is not chosen.

## Why it was deferred

`PLAN.md` schedules SFTP and Globus inner locations after v0.1.0, and the roadmap carried both in
`issues/extended-archive-sources.md` with "no user asking for it yet". Split out on 2026-11-11: the
user story names it beside S3. Globus and synthetic-subtree wrapping stay in that seed.

## Outcome / vision

A spec names a bundle, or a prefix of bundles, on an SFTP endpoint, and the loop runs over it as it
does over an object store: pulled lazily, re-aggregated on a delay, replaced whole.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a spec names `location = { type = "sftp", ... }`, the parser SHALL admit it and the
  engine SHALL construct the tier.
- **R2** — WHEN a bundle is pulled, its bytes SHALL stream to the staging path without the whole
  bundle resident in memory.
- **R3** — WHEN a bundle is pushed, a reader of the remote name SHALL see the old bundle or the new
  one and never a partial one, and a failed push SHALL leave the old bundle in place.
- **R4** — WHILE one bundle's pull is in flight, calls on files of bundles already staged SHALL NOT
  wait for it.
- **R5** — THE bundle loop's scenario (`tests/loop/`) SHALL pass over an SFTP source started on
  the developer machine.

## Notes

- R2 shares the streaming read [`issues/sharded-archive-sources.md`](sharded-archive-sources.md)
  names as its prerequisite; whichever cycle comes first builds it for both.
- R4 depends on the lock model
  ([`issues/namespace-sync-redesign.md`](namespace-sync-redesign.md), decision 4).
- Tape punishes small frequent writes, which is what a bundle's re-aggregation interval is for. A
  default interval for a tape source may want to be longer than for an object store.
- Related: `PLAN.md` § *Phase outline* (phase 12), § *Tier protocol* → ArchiveTier.
- Found by: comparing the maintainer's user story of 2026-11-11 with the tree at `64faf27`.
