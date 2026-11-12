---
status: unshaped
kind: feature
appetite: small
lane: public
---

# A read-only mount

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

A large class of use is a dataset that is read and never written
(`issues/sharded-archive-sources.md`), and every mount today is read-write. That puts the whole
write path on that dataset's critical path for no benefit: deferred namespace replay, the clean
transition, drain and rescue, the audit's loss report and the offline commit all have open defects or
do not exist (`ROADMAP.md`), and a training job touches none of them on purpose.

A mount that refuses every mutation has a much smaller contract:

- no copy is ever dirty, so nothing is queued, nothing is drained or rescued, and every stage copy is
  a cache that can be dropped;
- every shutdown settles `clean`, whichever signal ends it;
- recovery after a `SIGKILL` is not a loss question. In-flight promotion intents are resolved, temps
  are discarded, and the stage copies are verified or dropped.

The last point is on the deployment path whether or not this seed is taken. A batch job that reaches
its time limit is killed, `shutdown_state` stays `mounted`, and the next mount of the same overlay
raises `CrashDetected` with nothing to catch it (`issues/audit-command.md`). For a job-scoped mount
the kill is the ordinary ending, so an overlay whose stage tiers outlive the job is unusable after its
first job until some recovery exists. Read-only recovery is the small case of it.

**One mount per overlay does not fit the workload either.** An overlay's tiers are owned by one mount
at a time (`PLAN.md` § *Explicit non-goals*: multi-client coherence). A multi-node training job needs
the dataset on every node, and a parameter sweep runs many jobs over one dataset at once. Under the
current rule each node and each job needs its own overlay, its own state database and its own staged
copy of every shard it touches, so nothing staged on shared storage is shared. The non-goal was
written for writers. For read-only mounts over immutable content the question is open: whether several
mounts can share a staged tier and its index, and what coordinates the staging. This seed states the
question; it does not answer it.

## Why it was deferred

Never proposed. `PLAN.md` and the original phase outline describe one read-write lifecycle. Found by
an out-of-cycle review of the whole tree at `64faf27` (2026-11-11), from the order of the roadmap: the
read path to a read-only dataset was queued behind write-path repairs it does not need.

## Outcome / vision

A researcher mounts a dataset read-only at the top of a job script, on as many nodes as the job has,
and nothing the job does or fails to do can lose data or leave the overlay unmountable. The read-write
lifecycle keeps its own schedule and ships when it can be trusted.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN an overlay is mounted read-only (a mount option, a spec key, or both: shaping decides),
  every mutating call SHALL fail with `EROFS`, and no residency SHALL ever be `dirty`.
- **R2** — WHILE mounted read-only, the write-back scheduler SHALL NOT run, and pressure SHALL be the
  only thing that moves a copy, one tier up, as [`issues/eviction-one-hop.md`](eviction-one-hop.md)
  settles it for a clean copy.
- **R3** — WHEN a read-only mount ends by `SIGINT`, by `SIGTERM` or by an external unmount, it SHALL
  settle `shutdown_state = clean`.
- **R4** — WHEN a read-only mount was killed and the same overlay is mounted read-only again, the
  mount SHALL succeed: unresolved promotion intents resolved, transfer temps removed, each recorded
  stage copy verified by size or dropped, and usage counters rebuilt.
- **R5** — WHEN an overlay holds dirty state from an earlier read-write mount, a read-only mount SHALL
  refuse with a message naming `commit`.
- **R6** — `PLAN.md` SHALL record whether read-only mounts of one dataset may share a staged tier, and
  if so what owns the staging. [`issues/multi-node-mounts.md`](multi-node-mounts.md) takes this up:
  shared reading is its first step and needs no conflict rule.

## Notes

- R4 is the read-only slice of `issues/audit-command.md`'s five-step walk (marker check, intent
  resolution, counter rebuild) without the loss report or quarantine. Building it first gives the full
  audit a tested core.
- An archive source that is only read needs none of `issues/archive-journaling-and-locking.md`'s
  re-pack bracket or `issues/archive-finalization-integration.md`'s batching. The read side of
  `issues/archive-spec-plumbing.md` is enough to reach a read-only archive from a spec file.
- 2026-11-11, the maintainer: a read-only mount is a large part of the project and not its flagship.
  What sets the project apart is write-back with delayed re-aggregation into compressed bundles at
  the source. This mode therefore follows a first pass through that loop
  ([`issues/local-bundle-loop.md`](local-bundle-loop.md)); it does not lead.
- Open question — **per mount or per overlay.** A spec key makes the overlay read-only for good and
  lets the engine skip constructing the write path. A mount option lets one overlay be staged
  read-write and served read-only.
- Related: `issues/sharded-archive-sources.md`, `issues/hpc-deployment.md`, `issues/audit-command.md`,
  `issues/read-path-metadata-writes.md`; `PLAN.md` § *Vision and scope* (non-goals), § *Lifecycle and
  recovery*.
- Found by: an out-of-cycle review of the whole tree at `64faf27` (2026-11-11).
