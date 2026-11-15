---
status: unshaped
kind: refactor
appetite: small
lane: public
---

# The copy's lifecycle is declared in one place, not spread over five fields

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

A copy of an entry on a tier is at any moment absent, clean, dirty, being committed, or being evicted.
Nothing stores that as one value and nothing states who may move it from one to another or under which
lock. The state is read off several places at once:

- `residency.state` (`clean` or `dirty`) in `src/terrace/state/schema.sql`;
- the queued `data` row in `writeback`;
- an unresolved intent row, while a copy or an eviction is in flight;
- a handle's `marked_dirty` and `written` fields, and its `changing` count;
- a hop's own record of the changes that met it (`_hops`, under the handle mutex).

The transitions sit in `create`, `TransferPool.copy`, `Journal.mark_dirty`, `_commit_hop`,
`commit_upstream`, `evict_copy`, `_remove`, `truncate` and `_record`. A handle-less `truncate` and
`_record` write the same rows as the others and belong in the same table.

The fix for `racing-write-marked-clean` added one more transition: a hop opens a record of the changes
that meet it, and the holder goes clean only when none did. It is correct and it is tested, and it is
one more rule that lives in a function body. `AGENTS.md` § 6 states it in prose; no table lists the
states and the moves between them, so a reviewer has to rebuild the machine from nine call sites to
decide whether a new path is allowed.

`writeback.state` admits `'active'` (`src/terrace/state/schema.sql`, the `CHECK` on the column) and
nothing ever sets it. A commit in flight is the state the vocabulary was reserved for, and
`issues/invariant-audit-gaps.md` records the value as dead.

## Why it was deferred

`spec/racing-write-marked-clean/GOAL.md` § Non-goals and Q6 put the explicit state machine and the
reserved `'active'` state out of that cycle, and said they belong to `issues/namespace-sync-redesign.md`.
That seed does not carry them: it declares the lifecycles of an entry and of a queued row, and its
"What it does not touch" lists the data path's clean transition. This seed is where the obligation lives.
It is **pre-existing**: the spread is as old as the data path and the write-back scheduler, and the
shipped fix neither repairs nor widens it.

## Outcome / vision

A reader finds, in one module, the states a copy can be in, the transitions between them, who may make
each one and under which lock, and the engine's changes go through that table. `'active'` is either set
by a commit in flight or removed from the schema. The race the shipped fix closed is a row in the table,
not a paragraph.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — THE engine SHALL declare the states of a copy and the permitted transitions in one module,
  and every call site that moves a copy SHALL go through it.
- **R2** — THE table SHALL state, for each transition, the lock its caller holds, and a test SHALL
  fail when a transition is taken from a state the table does not permit.
- **R3** — WHEN a commit is in flight, the write-back row SHALL read `active`, or `'active'` SHALL be
  removed from the schema's `CHECK`. A schema version bump is a product decision (`AGENTS.md` § 2);
  weigh it with `issues/namespace-sync-redesign.md`, which already proposes one.
- **R4** — THE behaviour the existing tests pin SHALL not change, including the hop's record of
  changes that met it.

## Notes

- Shapes to weigh at promotion: an enum and a transition function in `engine/`; or a schema column
  that holds the state, with the others derived from it. The second touches the high-blast-radius
  `state/schema.sql` and `state/journal.py` rows.
- Related: `spec/racing-write-marked-clean/` (the hop's record, as shipped);
  `issues/namespace-sync-redesign.md` (entry and row lifecycles, the same style of declaration, and the
  schema version question); `issues/invariant-audit-gaps.md` (the dead `'active'`
  value); `issues/pump-liveness-and-loss-windows.md`; `AGENTS.md` § 6 and § 7.
- Found by: `racing-write-marked-clean`, shaping (2026-11-11); carried here at its retirement.
