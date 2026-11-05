---
status: unshaped
kind: feature
appetite: small
lane: public
---

# A `TransferPool` priority lane

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`,
> and that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped`
> means none of that has happened yet.

## Problem

**A `TransferPool` priority lane.** `src/sosfs/engine/transfer.py:62` builds one flat
`ThreadPoolExecutor(max_workers=workers, thread_name_prefix='sosfs-transfer')`, and `submit`
(`transfer.py:113-115`) is a bare `executor.submit`. The queue is FIFO with no notion of urgency, so
an on-demand promote — a user's `open()` blocking on a synchronous whole-file copy — waits behind
every background warming copy already queued. This is not an archive feature at all; it is a property
of the transfer pool that a POSIX-only four-tier stack has exactly as much reason to want.

## Why it was deferred

Scheduled post-v0.1.0 by design: `PLAN.md` § *Phase outline* lists it in phase 12, "TransferPool
priority lane (on-demand pulls preempt background warming)" (`PLAN.md:580-581`), filed with the
extended archive sources. It was carried in
[`issues/extended-archive-sources.md`](extended-archive-sources.md) until 2026-11-04, when it was
split out on that seed's own recommendation: filed inside an archive bucket it was invisible, and it
is the only one of those capabilities a site running no archives at all would ask for. Pre-existing on
`main`.

## Outcome / vision

A user's `open()` on a cold file does not wait behind a warming campaign, on any stack, archive or
not.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHILE background warming has filled the transfer queue, an on-demand promote SHALL be
  dispatched ahead of every queued warming copy, asserted by queue position rather than by wall time.

## Notes

- Split from [`issues/extended-archive-sources.md`](extended-archive-sources.md) on 2026-11-04,
  together with [`issues/sharded-archive-sources.md`](sharded-archive-sources.md). R1 is that seed's
  R6. `appetite: small` was recorded at the split and has not been negotiated.
- **Who calls `submit` today matters for the shape.** `TransferPool`'s own docstring
  (`transfer.py:51-53`) says `copy` runs on the calling thread for the synchronous promote-on-open
  path and `submit` runs on a worker for write-back and rescue. An on-demand promote that never enters
  the queue cannot be starved by it, so the first step at shaping is to establish which on-demand path
  actually queues, and whether the lane is needed before warming exists as a producer.
- Open question — **how many lanes?** On-demand versus warming is two. Drain and rescue arguably want
  a third, and once there are three the pool needs a policy rather than a flag.
- Related: [`issues/sharded-archive-sources.md`](sharded-archive-sources.md) — per-shard lazy pulls
  are the heaviest on-demand transfers the product will make;
  [`issues/benchmark-harness.md`](benchmark-harness.md) matters because the lane's whole claim is a
  latency claim. Root `PLAN.md` → § *Phase outline* (phase 12).
- Found by: scheduled post-v0.1.0 work carried forward from `PLAN.md` § *Phase outline*. The flat
  `TransferPool` queue was confirmed by the invariant audit run during the software-factory port
  (2026-08-22) — tier-layer and remaining-work reviews.
