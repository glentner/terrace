---
status: unshaped
kind: fix
appetite: small
lane: public
---

# The write-back scheduler dies on a source-only stack

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

On a stack with one tier, the source alone, the write-back scheduler raises `KeyError: 'project'` (the
source tier's name) at its first scan after any namespace op, and its thread dies. A spec with a single
`[[tier]]` is valid: `OverlaySpec.from_data` requires at least one (`src/sosfs/tier/spec.py:296-297`), and
`_validate_stack` asks only that the first be the source (`src/sosfs/tier/spec.py:311-314`). The mount
starts the scheduler whatever the stack (`src/sosfs/fuse/mount.py:220-223`).

`boundary_policies` keys each boundary by the tier receiving the commit, and builds one entry per stage
(`src/sosfs/engine/writeback.py:35-36`); with no stage the table is empty. Namespace rows are queued
straight to the source, so `WritebackScheduler.deadline` looks up the source's name in that empty table
(`src/sosfs/engine/writeback.py:76`) and raises. `due` calls `deadline` for every queued row
(`src/sosfs/engine/writeback.py:87`), so one `mkdir`, `rename`, `unlink`, `rmdir` or `meta` row is enough.
The pump dies as `issues/pump-liveness-and-loss-windows.md` describes: nothing notices, and every later op
stays queued until a `flush` or a drain runs it. A restart does not: the new scheduler's first scan raises the
same `KeyError`, and the rows stay queued.

Reproduction, on the engine with no mount (`tests/engine/conftest.py` helpers):

```python
engine = OverlayEngine(build_spec(root, tiers=1), clock=Clock())
engine.start()
engine.mkdir('/x')
WritebackScheduler(engine).poll(10**9)       # KeyError: 'project'; the mkdir row stays queued
```

The defect blames to `8d2265f` (2026-07-03, before the factory): `boundary_policies` and `deadline` have
had this shape since the scheduler was written. The `nested-rmdir-replay-recursion` cycle found it while
mapping its own shape (2026-11-09): every scheduler poll on a source-only stack with a namespace row
queued raised it, 429 of 429 such cases, whether or not the queue held that cycle's shape. That cycle
records it and does not change `writeback.py`; its scheduler criteria are graded on stacks with a stage.

## Outcome / vision

A source-only overlay runs its write-back scheduler like any other stack: namespace rows age on a
policy and commit, and the pump stays alive.

## Open questions

- Which policy governs a source-only stack's namespace rows: the source's own `writeback` table, the
  default `WritebackSpec()`, or passthrough? The boundary model has no stage-to-source boundary to own it.
- Should the mount start the scheduler at all on a source-only stack, where data never queues?

## Related materials

- `issues/pump-liveness-and-loss-windows.md`: a dead pump is unreported.
- `src/sosfs/engine/writeback.py`: `boundary_policies`, `deadline`, `due`.
