---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Pressure or delay moves a copy one tier up, and no further

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The maintainer set out the tier model on 2026-11-11. Every stage tier, from the fastest up to the
durable tier beneath the source, has a size limit, a file-count limit and a write-back delay. Two
things move a copy, each on its own:

- **Pressure.** A tier out of room, by size or by count, evicts. Eviction pushes the copy one tier up
  the stack, which may cause the same thing there. On a read-only workload this is the only force:
  nothing is modified, so no delay ever fires.
- **Delay.** A copy that has changed and then gone unchanged for the tier's delay is pushed one tier
  up, whether or not the tier is under pressure, and that push may cascade for the upper tier's own
  reasons.

Neither goes further than one tier. In his words: a write-back in the lowest tier should not "go all
the way up and cause a repack of its parent source bundle. We should only push it one layer up the
stack." A bundle is repacked on triggers of its own
([`issues/archive-finalization-integration.md`](archive-finalization-integration.md)).

The tree does two things differently.

**Eviction of a dirty copy goes all the way to the source.** `EvictionManager._drop_dirty` calls
`commit_upstream` (`src/sosfs/engine/evict.py:151`), which hops until the source holds the bytes, and
`AGENTS.md` § 7 states it as a rule: "to the *source*, not one hop". For a bundle source that turns
pressure in a RAM tier into a re-aggregation and a push to an object store or to tape, once per
evicted file. The reason § 7 gives is that a single hop "leaves the copy clean against a stage tier
that is itself over quota, and both copies can then be reclaimed in the same pass". Read against the
code, that does not follow: `_commit_hop` marks the upstream copy dirty against its own boundary as
it records it (`src/sosfs/engine/overlay.py:1008-1010`), and `evict_copy` never drops a dirty copy
(`overlay.py:1449`). `commit_to_shared`, the rescue's primitive, already moves one hop at a time on
that argument. Argued from the code; no test drives eviction across several pressured tiers.

**Eviction of a clean copy drops it.** `evict_copy` deletes the bytes and the residency
(`overlay.py:1430-1465`), and a later read goes back to wherever the next copy is, which after a
promotion straight from the durable tier to the RAM tier is the durable tier again. As the model is
read here, a clean copy pushed out of a tier is kept one tier up when that tier does not already
hold it, so node-local disk catches what falls out of RAM and a second read does not go back to the
shared filesystem. **That reading wants the maintainer's confirmation**: "pushes up the stack and may
cause similar eviction in the above tier" says it of the read-only path, where every copy is clean,
and a drop could cause nothing above. At the durable tier the rule needs no special case. A member
the staged bundle holds is already one tier up, so its copy is dropped.

The delay half is as built. `_commit_hop` moves one hop and queues the next boundary on its own clock
(`src/sosfs/engine/writeback.py`), which is the cascade the model describes.

## Why it was deferred

Not deferred: the behaviour is as built in Phase 5 and as `AGENTS.md` records it. Found by comparing
the maintainer's user story and his clarification of it (2026-11-11) with the tree at `64faf27`. On a
POSIX source the first difference costs one more copy and is easy to miss.

## Outcome / vision

One rule a user can hold in their head: each tier has two limits and a delay, and either one moves a
copy exactly one tier up. Data reaches the source on the source's own clock.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN eviction reclaims a dirty copy, it SHALL commit that copy one tier up and drop it once
  it is clean, and SHALL make no request to any tier above that one.
- **R2** — WHEN eviction reclaims a clean copy and the tier above holds no copy of it, the copy SHALL
  be placed there before it is dropped; WHEN the tier above already holds it, it SHALL be dropped.
  (Subject to the confirmation above.)
- **R3** — WHEN a copy has gone unchanged for its tier's delay, it SHALL be committed one tier up, and
  the tier above SHALL start its own delay for it.
- **R4** — WHEN every stage tier of a four-tier stack is over its high watermark at once, a reclaim
  pass SHALL lose no write: after a flush the source SHALL equal the oracle.
- **R5** — No copy SHALL be clean unless the tier above it holds its bytes, before and after every
  move.
- **R6** — `AGENTS.md` § 7 and `PLAN.md` § *Eviction* SHALL state the rule and the reason for it.

## Notes

- `_drop_dirty` is on the high-blast-radius list: it is the only path that deletes bytes the source
  may not yet hold.
- **Every tier has both limits and a delay in the model. In the tree all three are optional.** A
  stage tier with neither `quota.size` nor `quota.files` is never reclaimed and fills its device, and
  `max_interval` defaults to none (`AGENTS.md` § 6, "Know which windows are unbounded";
  [`issues/pump-liveness-and-loss-windows.md`](pump-liveness-and-loss-windows.md)). Shaping should
  decide whether a spec must state them or gets defaults.
- Open question — **a full neighbour.** A push into a tier that is itself full moves the pressure up.
  The model accepts the cascade. `_commit_hop` checks no quota today, and admission for a pushed copy
  has no rule.
- Open question — **eviction does I/O.** Moving a clean copy up is a write on the tier above, paid
  under pressure. It buys a second read from node-local disk; a workload that never re-reads pays it
  for nothing. A per-tier switch is possible and is one more knob.
- Related: `AGENTS.md` § *Invariants* § 6, § 7, § 8 ("Rescue stops at the first shared tier");
  [`issues/sharded-archive-sources.md`](sharded-archive-sources.md) (a staged bundle as a unit the
  durable tier's limits count); [`issues/plan-the-bundle-loop.md`](plan-the-bundle-loop.md).
- Found by: comparing the maintainer's user story of 2026-11-11 with the tree at `64faf27`.
