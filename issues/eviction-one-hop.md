---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Evicting a dirty copy commits it all the way to the source

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

When a tier over its quota has no clean copy left to drop, the eviction manager commits each dirty
copy to the source before dropping it. `EvictionManager._drop_dirty` calls `commit_upstream`
(`src/sosfs/engine/evict.py:151`), which hops until the source holds the bytes, and `AGENTS.md` § 7
states it as a rule: dirty copies are committed "to the *source*, not one hop".

The maintainer's user story of 2026-11-11 ([`issues/local-bundle-loop.md`](local-bundle-loop.md))
describes something else. A file altered in the RAM tier is committed back up the stack, by eviction
or by its write-back delay: to node-local disk, then to shared scratch, where it sits beside the
staged bundle as durable storage. The bundle is repacked and put back to its source on triggers of
its own: its delay, quota pressure on the tier that holds it, or a commit.

For a bundle source the two differ by the whole cost model. Under the rule as written, quota pressure
in a RAM tier turns each evicted dirty file into a commit to the source, which for a bundle is a
re-aggregation and a push to an object store or to tape. Under the story it is one local copy to the
next tier.

§ 7 gives a reason for the rule: a single hop "leaves the copy clean against a stage tier that is
itself over quota, and both copies can then be reclaimed in the same pass". Read against the code,
that does not follow. `_commit_hop` records the upstream copy and at once marks it dirty against its
own boundary (`src/sosfs/engine/overlay.py:1008-1010`), and `evict_copy` never drops a dirty copy
(`overlay.py:1449`), so the upstream copy cannot be reclaimed until it has been committed in turn.
`commit_to_shared`, the rescue's primitive, already moves data one hop at a time on exactly that
argument. Argued from the code; no test drives eviction across several pressured tiers.

## Why it was deferred

Not deferred: the behaviour is as built in Phase 5 and as `AGENTS.md` records it. Found by comparing
the maintainer's user story with the tree at `64faf27` (2026-11-11). It costs nothing visible on a
POSIX source, where a commit to the source is one more copy.

## Outcome / vision

Pressure on a tier is relieved by the cheapest move that keeps the bytes safe: one hop up. Data
reaches the source on the source's own clock.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN eviction must reclaim a dirty copy, it SHALL commit that copy one hop upstream and
  drop it once it is clean, and SHALL NOT commit it further than the reclaim needs.
- **R2** — WHEN every stage tier of a four-tier stack is over its high watermark at once, a reclaim
  pass SHALL lose no write: after a flush the source SHALL equal the oracle.
- **R3** — No copy SHALL be clean unless its immediate upstream holds its bytes, before and after
  every reclaim.
- **R4** — WHEN the tier being reclaimed holds altered files beside a staged bundle, reclaiming them
  SHALL cost one re-aggregation of that bundle and not one per file
  ([`issues/archive-finalization-integration.md`](archive-finalization-integration.md)).
- **R5** — `AGENTS.md` § 7 and `PLAN.md` § *Eviction* SHALL state the rule and the reason for it.

## Notes

- `_drop_dirty` is on the high-blast-radius list: it is the only path that deletes bytes the source
  may not yet hold.
- Open question — **a full neighbour.** One hop into a tier that is itself full moves the pressure
  up. Either the pass cascades tier by tier, or the commit goes to the nearest upstream tier with
  room. `_commit_hop` checks no quota today.
- Related: `AGENTS.md` § *Invariants* § 7, § 8 ("Rescue stops at the first shared tier");
  [`issues/sharded-archive-sources.md`](sharded-archive-sources.md) (staged bundles as units a quota
  counts); [`issues/pump-liveness-and-loss-windows.md`](pump-liveness-and-loss-windows.md) (the
  watermarks have no configuration key).
- Found by: comparing the maintainer's user story of 2026-11-11 with the tree at `64faf27`.
