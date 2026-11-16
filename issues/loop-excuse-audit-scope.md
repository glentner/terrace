---
status: unshaped
kind: test
appetite: small
lane: public
---

# The bundle loop's self-excuse audit reads one file

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs are negotiated with a human.

## Problem

The bundle loop's stages in `tests/loop/stages.py` are strict expected failures that accept one
exception, `Blocked`, raised in one place. `test_blocked_is_raised_in_one_place`
(`tests/loop/test_support.py:84`) guards that by parsing `stages.py` and nothing else. An excuse
placed anywhere else that pytest loads for the scenario passes the audit:

- `pytest.xfail(...)` in the scene factory in `tests/loop/conftest.py`: every test that uses the
  scene becomes an expected failure (`36 passed, 14 xfailed` instead of `41 passed, 9 xfailed`).
- A `pytest_collection_modifyitems` hook in `tests/loop/conftest.py` that prepends
  `xfail(raises=Exception)` to the stages: a stage that drifts off its pinned refusal is absorbed
  with unchanged counts, `41 passed, 9 xfailed`. Only the `marker` property `conftest.py` records on
  each report shows it.
- `getattr(pytest, 'xfail')()` inside a stage: the audit matches call names textually, so the
  plain `pytest.xfail('x')` is caught and this spelling is not.

Each was a mutant planted in the blind review of `local-bundle-loop` (review cycle 1, 2026-11-12) and
survived the loop's own tests. None is present in the tree; the audit is defence against a future
edit, and `AGENTS.md` § Verification states that "a stage that fails anywhere else fails the run".

Introduced by `local-bundle-loop`, which added the scenario and the audit.

## Why it was deferred

A coverage observation, not a defect: it breaks no requirement's check on the tree as it stands. The
cycle that found it was in review, and hardening the audit was outside its contract.

## Outcome / vision

No edit to any file the scenario loads can turn a stage's failure into an expected one, or widen
what a stage's marker accepts, without failing the run.

## Sketch of the acceptance criteria

- **R1** — WHEN any module or `conftest.py` under `tests/loop/` raises `xfail` or `skip`, adds an
  `xfail` or `skip` marker, or changes a stage's marker at collection, the loop's tests SHALL fail.
- **R2** — WHEN the scenario runs, a check SHALL assert from each stage's report that it was
  collected with exactly the marker `stages.py` declares and stopped at `Blocked`.

## Notes

- Related: `spec/local-bundle-loop/`; `tests/loop/conftest.py` already records `stopped`,
  `cause` and `marker` on each stage's report, which R2 could read.
- Found by: `local-bundle-loop` review cycle 1 (mutants C2, C4 with S6, S10b).
