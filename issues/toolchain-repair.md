---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Repair the toolchain so the verify gate runs on a fresh machine

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`,
> and that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped`
> means none of that has happened yet.

## Problem

The profile's `verify` is `tools/lint.sh && uv run pytest -n auto -m unit`. On a fresh machine with
Python 3.15 final, the second half cannot run. Four findings, measured by the foreman on macOS on
2026-11-04:

1. **The locked dev environment is uninstallable on 3.15 final.** `uv sync --frozen` on Python
   3.15.2 fails. The chain is `moto[server]` (the `dev` group, `pyproject.toml:45`) →
   `openapi-spec-validator` (`uv.lock:569`, locked at 0.9.0, `uv.lock:633-634`) → `pydantic`
   (2.13.4, `uv.lock:695-696`) → `pydantic-core` 2.46.4 (`uv.lock:710-711`), which publishes no
   cp315 wheel, and its maturin source build fails. `[tool.uv] default-groups = ["dev"]`
   (`pyproject.toml:52-53`) puts the group in every `uv sync`, so there is no install of the project
   that skips it. CI's 3.15 jobs run the same `uv sync` (`.github/workflows/ci.yml:38`, `:56`) and
   presumably fail the same way. **Unverified:** nobody has read a CI run since 3.15 went final.
2. **The committed workflow was proven only on a pre-release.** The last green environment is a
   stale `.venv` whose interpreter is 3.15.0a3 and no longer exists on the machine. Nothing about the
   present toolchain has been demonstrated by a run.
3. **Every tier test errors without the server extra.** `tests/tier/conftest.py:14-16` imports
   `boto3` and `moto.server.ThreadedMotoServer` at module scope, so collection of anything under
   `tests/tier/` — the POSIX tests included, which need neither — errors when those packages are
   absent. The S3 fixture's needs leak into the POSIX adapter's coverage.
4. **One POSIX test asserts Linux group semantics.**
   `tests/tier/test_posix.py::test_put_and_stat_applies_meta` asserts
   `(result.uid, result.gid) == (os.getuid(), os.getgid())` (`tests/tier/test_posix.py:59`). A new
   file takes the creating process's group only on System V semantics. Under BSD group inheritance
   (macOS) it takes the parent directory's group: measured, 0 against 20. It would fail the same way
   on Linux in a setgid directory, which is the normal configuration of HPC shared project spaces —
   the deployment this product targets.

With `moto` (no server extra), `flask` and `boto3` installed by hand into a scratch environment, the
suite minus the FUSE tests gives **404 passed, 1 failed** — the failure is finding 4. The FUSE tests
need libfuse, which is not on the Mac, so they were not run.

## Why it was deferred

Found while adopting the KB software factory (2026-11-04), which commits only process artifacts and
changes no product or test code. Pre-existing on `main` and `wip`: findings 1 and 2 are drift
between a lockfile and a Python release that went final after it was written; findings 3 and 4 have
been in the tree since Phase 7 (`06a02f0`) and Phase 3 (`162625d`).

It goes first in the roadmap because every later cycle's `verify` depends on it. A gate that cannot
run is a gate every cycle would have to replace by hand, and a cycle that does so is graded against a
substitute nobody agreed to.

## Outcome / vision

On a fresh machine with Python 3.15 final, `uv sync` succeeds and the profile's `verify` runs and
reports its pass count. CI's jobs are green, and someone has read them. A host without a moto server
still runs the POSIX tier tests, and the metadata test asserts what `PosixTier.put` actually
guarantees rather than what Linux defaults happen to do.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHEN `uv sync --frozen` runs on Python 3.15 final with no prior cache, it SHALL exit 0, and
  `uv run pytest -n auto -m unit` SHALL then report a pass count, asserted as a number.
- **R2** — WHEN the CI workflow runs on a push to `main`, the `unit` (3.15 and 3.15t), `unit-macos`
  and `checks` jobs SHALL conclude `success`, read from `gh run list` rather than assumed.
- **R3** — IF `moto.server` or `boto3` cannot be imported, THEN the S3 tier tests SHALL skip with a
  reason naming the missing package, and every test in `tests/tier/test_posix.py` SHALL still be
  collected and run.
- **R4** — WHEN `PosixTier.put` writes a file into a directory whose group differs from the process's
  primary group, `test_put_and_stat_applies_meta` SHALL pass, asserting the group the platform's
  semantics produce (the parent directory's, or the process's) rather than `os.getgid()`.
- **R5** — THE suite SHALL report the same total under `-m 'not integration'` on macOS as the scratch
  run above (405 collected outside `tests/fuse`), with zero failures.

## Notes

- Open question — **how to get past `pydantic-core`.** Options in rough order of cost: wait for a
  cp315 wheel and re-lock; narrow `moto[server]` to what the S3 fixture needs (the scratch run above had
  `ThreadedMotoServer` working with `moto`, `flask` and `boto3` alone, no `openapi-spec-validator`);
  or pin an older chain.
  Narrowing the extra is the only one that removes the dependency rather than moving it. Which one is
  a shaping decision.
- Open question — **what finding 4's test should assert.** `PosixTier.put` applies ownership
  best-effort (`AGENTS.md` § *Invariants* § 4: "Ownership is best-effort; mode and mtime are not"), so
  the test arguably should assert only that ownership was not changed to something unexpected, or run
  in a directory whose group it controls.
- Related: [`issues/ci-integration-lane.md`](ci-integration-lane.md) builds the integration lane on
  top of a working install; [`issues/hpc-deployment.md`](hpc-deployment.md) cares about finding 4,
  because setgid project directories are where the product will run.
- Found by: the foreman during the factory-adoption session (2026-11-04), on macOS with Python
  3.15.2.
