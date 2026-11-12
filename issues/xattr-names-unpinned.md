---
status: unshaped
kind: test
appetite: small
lane: public
---

# No unit test pins the extended-attribute names

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The two extended attributes are the one user-facing API the mount adds: `user.terrace.tiers` and
`user.terrace.dirty`, defined at `src/terrace/fuse/adapter.py:32-34` as `XATTR_TIERS`, `XATTR_DIRTY`
and `XATTRS`. A script that asks "is this file committed yet?" spells the name literally.

The unit tests that cover them, `tests/fuse/test_adapter.py:234-270`, import those three constants
(`:25`) and query the adapter with them. They prove the adapter answers to whatever the constants
hold, and nothing about what the constants hold. The only test that spells the names literally is
`tests/fuse/test_integration.py:120-122`, which runs on no automated machine.

Measured on 2026-11-12 at the rename's head `3a34ff2`: an untracked pytest plugin that rebinds the
three constants in memory to a different name pair before the tests import them leaves the six
extended-attribute tests green, `6 passed`, the same count as the unmodified run. A typo in either
constant, or a rename that moves one of them and not the documentation, passes the unit lane and
breaks every script that reads the attribute.

## Why it was deferred

Found by the blind review of `rename-to-terrace`. That cycle held the unit pass count to its base
commit's and changed names only, so tightening a test was outside its contract, and its review graded
the attribute names by driving the adapter with the literal strings instead. **Pre-existing**: the
tests compared the constants to themselves in the same way at the rename's base commit `bf438b4`.

## Outcome / vision

A wrong attribute name fails the unit lane on every machine, without a mount.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN either extended-attribute constant in `fuse/adapter.py` names anything other than
  `user.terrace.tiers` or `user.terrace.dirty`, the unit lane SHALL fail. Observable by rebinding each
  constant in turn and running `uv run pytest -m unit tests/fuse/test_adapter.py`.
- **R2** — `listxattr` SHALL be asserted against the literal pair, not against `XATTRS`.

## Notes

- Related: `issues/ci-integration-lane.md`, which would run the one literal check that exists today.
- The fix can tighten an existing assertion rather than add a test, so the unit count need not move.
- Found by: `rename-to-terrace` review cycle 1 (2026-11-12), a surviving mutant recorded as a coverage
  observation.
