# GOAL — Repair the toolchain so the verify gate runs on a fresh machine

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** toolchain-repair
- **kind:** fix
- **appetite:** small

## Problem

Every cycle in this repo is graded by the profile's `setup` (`uv sync`) and `verify`
(`tools/lint.sh && uv run pytest -n auto -m unit`), and on a fresh machine with Python 3.15 final
neither half of that gate can be trusted to run. A gate that cannot run is one each later cycle
would have to replace by hand, and a cycle graded against a hand-made substitute is graded against
something nobody agreed to. The last environment known to pass was a stale `.venv` on a 3.15
pre-release interpreter that no longer exists, so nothing about the current toolchain has been shown
by a run.

Measured on macOS arm64 with Python 3.15.2 (2026-11-04/05). All of it is **pre-existing** on
`main`, landed before the factory was adopted:

1. **The dev environment does not install.** `uv sync --frozen --python 3.15` fails building
   `pydantic-core` 2.46.4 from source (maturin). It arrives through the `dev` group's
   `moto[server]>=5` (`pyproject.toml:45`) → `openapi-spec-validator` 0.9.0 (`uv.lock:633-634`) →
   `pydantic` 2.13.4 (`uv.lock:695-696`) → `pydantic-core` 2.46.4 (`uv.lock:710-711`), which
   publishes no cp315 wheel. `default-groups = ["dev"]` (`pyproject.toml:52-53`) puts the group in
   every `uv sync`. CI's Linux and macOS jobs run the same `uv sync`
   (`.github/workflows/ci.yml:38`, `:56`); their current result has not been read.
2. **The whole tier test package errors when the S3 fixture's packages are absent.**
   `tests/tier/conftest.py:14-16` imports `boto3` and `moto.server.ThreadedMotoServer` at module
   scope, so `tests/tier/test_posix.py`, which needs neither, cannot be collected without them.
3. **One POSIX test fails where a new file does not take the process's group.**
   `tests/tier/test_posix.py:59` (`test_put_and_stat_applies_meta`) asserts
   `(result.uid, result.gid) == (os.getuid(), os.getgid())`. Measured: got gid 0 against 20, in a
   scratch directory whose group is `wheel`. The same mismatch is expected on Linux in a setgid
   directory, the usual configuration of shared HPC project spaces, which is where this product is
   meant to run.
4. **The FUSE test package errors at collection on a machine without libfuse.** Collecting
   `tests/fuse` raises `OSError: Unable to find libfuse`; `tests/fuse/conftest.py` imports the FUSE
   adapter at module scope with no availability guard.

With `moto` (no `server` extra), `flask`, `flask-cors` and `boto3` installed by hand,
`pytest -m 'not integration' -n 4` gave **404 passed, 1 failed** (finding 3) outside `tests/fuse`,
plus the `tests/fuse` collection errors.

How to get past `pydantic-core`, and what the metadata test should assert in place of
`os.getgid()`, are design questions for the plan, bounded by the criteria below. `AGENTS.md`
§ Invariants § 4 (ownership is best-effort; mode and mtime are not) is the relevant contract for the
second.

## Outcome / vision

On a fresh macOS or Linux machine with Python 3.15 final, `uv sync` succeeds and the profile's
`verify` runs to a reported pass count with no collection errors and no failures except the one
deferred engine defect R2, R8 and R9 name (amended 2026-11-05). A machine without
libfuse skips the FUSE tests cleanly and says why; a machine without a moto server still runs the
POSIX tier tests. Nothing that ran before is skipped, deselected or loosened to get there. CI's jobs
are green apart from that same test, and the result has been read from a run.

## Acceptance criteria (the contract)

"Fresh machine" means Python 3.15 final with no prior uv cache and no existing `.venv`. Counts are
the numbers pytest reports in its summary line, asserted as numbers (`AGENTS.md` § Verification:
exit 0 is necessary but not sufficient).

- **R1** — WHEN the profile's `setup` (`uv sync`) runs on a fresh macOS or Linux machine, it SHALL
  exit 0 and leave the committed `uv.lock` unchanged.
- **R2** — WHEN the profile's `verify` runs after R1 on a fresh macOS machine without libfuse, `tools/lint.sh`
  SHALL exit 0 and pytest SHALL report a nonzero passed count and 0 errors, with no failed test other
  than `tests/engine/test_model.py::TestOverlayModel::runTest`. (Amended 2026-11-05; see Clarifications. That one test may pass or fail; the exception is
  the deferred namespace-replay defect, the same one R9 tolerates.)
- **R3** — IF libfuse cannot be loaded, THEN every test under `tests/fuse` that the run selects SHALL
  be reported as skipped, with a skip reason that names libfuse, and none SHALL be reported as a
  collection error.
- **R4** — WHERE libfuse can be loaded, the `-m unit` tests under `tests/fuse` SHALL be collected and
  run, with none of them skipped.
- **R5** — IF `boto3` or `moto.server` cannot be imported, THEN the S3 tier tests SHALL be reported as
  skipped with a reason naming the missing package, and every test in `tests/tier/test_posix.py`
  SHALL still be collected and run.
- **R6** — WHILE the environment is the one R1 installs, the S3 tier tests SHALL be collected and run,
  with none of them skipped.
- **R7** — WHEN `test_put_and_stat_applies_meta` runs in a directory whose group differs from the
  process's primary group, it SHALL pass; it SHALL still assert the file's kind, size, mode and
  mtime as it does today, and SHALL still assert the file's owning uid and gid against an expected
  value.
- **R8** — WHEN `uv run pytest -n auto -m 'not integration'` runs after R1 on a fresh macOS machine
  without libfuse, pytest SHALL report no failed test outside `tests/fuse` other than `tests/engine/test_model.py::TestOverlayModel::runTest`, and at
  least 405 tests outside `tests/fuse` SHALL pass, counting that one test whether it passes or fails
  (the baseline above: 404 passed plus the 1 failure R7 repairs). (Amended 2026-11-05; see
  Clarifications.)
- **R9** — WHEN the CI workflow runs on this cycle's tree, as read from the run record rather than
  assumed: `Lint & build` SHALL conclude `success`; each of `Unit (Linux, 3.15)`,
  `Unit (Linux, 3.15t)` and `Unit (macOS, engine-only)` SHALL either conclude `success` or fail with
  `tests/engine/test_model.py::TestOverlayModel::runTest` as its only failed or erroring test; and
  in both Linux jobs libfuse SHALL load and the `tests/fuse` unit tests SHALL run with none skipped.
  (Amended 2026-11-05; see Clarifications. The `test_model` exception is the namespace-replay defect
  handed to `issues/deferred-rename-source-orphan.md`, not a test this cycle may touch.)

## Non-goals (no-gos)

- **FUSE on macOS.** Installing or supporting macFUSE or FUSE-T, or running the FUSE tests on a Mac,
  is out of scope (`brew install libfuse` fails: Homebrew's libfuse is Linux-only).
- **Running the integration suite** (`-m integration`, the docker-compose services, real mounts).
  That lane belongs to `issues/ci-integration-lane.md`.
- **Weakening any test**: deleting, skipping, deselecting or loosening a test that runs today on a
  machine that can run it. R4, R6, R7 and R8 hold this line.
- **Product behavior changes.** Nothing under `src/sosfs/` changes behavior; in particular
  `PosixTier.put` and its ownership handling stay as they are.
- **Renaming** anything: package, distribution, modules, tests, CI jobs.

## Clarifications

- **Q:** Is getting the FUSE tests running on macOS in scope? — **A:** No. macOS FUSE is a non-goal;
  the owner tried `brew install libfuse` and it failed (Homebrew's libfuse is Linux-only; macOS needs
  macFUSE or FUSE-T). Running FUSE and integration tests belongs to `issues/ci-integration-lane.md`.
  This cycle's gate is the unit suite with `tests/fuse` either running or cleanly skipping when
  libfuse is absent; turning today's collection error into a clean, reported skip is in scope
  (observable: a unit run on a libfuse-less machine produces no collection errors). (resolved
  2026-11-05, foreman ruling)
- **Q:** What is the scope? — **A:** Make the profile's `setup` and `verify` work on a fresh machine
  with Python 3.15 final (macOS and Linux CI), without weakening any test. In scope: the
  dev-dependency install failure (`moto[server]` → `pydantic-core`, no cp315 wheel), the
  module-scope `moto.server`/`boto3` import in `tests/tier/conftest.py`, the gid assertion in
  `tests/tier/test_posix.py::test_put_and_stat_applies_meta`, and CI's 3.15 and 3.15t jobs going
  green. Out of scope: running the integration suite, renaming, any product behavior change.
  (resolved 2026-11-05, foreman ruling)
- **Q:** Appetite? — **A:** Stays `small`. (resolved 2026-11-05, foreman ruling)
- **Q:** How are the criteria phrased? — **A:** As observable broken→fixed behavior (exit status,
  pass counts, collection errors), never the suspected mechanism. (resolved 2026-11-05, foreman
  ruling)
- **Q:** R9 names `Unit (macOS, engine-only)` and `Lint & build` beyond the 3.15/3.15t jobs — keep
  them? — **A:** Yes, all four jobs. (resolved 2026-11-05, foreman ruling at GOAL sign-off)
- **Q:** CI runs only on pushes to `main`/`wip`, PRs and manual dispatch, so R9 can't be graded before
  landing. How is it graded? — **A:** The cycle may change the workflow's `push` trigger to cover the
  profile's cycle-branch prefixes and drop the retired `wip` branch; a build phase pushes the cycle
  branch (standing approval covers cycle-branch pushes), and review grades R9 from that run's record.
  Manual dispatch is not used. (resolved 2026-11-05, foreman ruling at GOAL sign-off)
- **Q:** `escaped` — `pre-factory` or `none`? — **A:** `pre-factory`, as recorded: two of the four
  defects are test code that fails independent of the interpreter release. (resolved 2026-11-05,
  foreman ruling at GOAL sign-off)

- **Q:** `main`'s CI run 42023846602 (`6a046b4`) fails both Linux unit jobs on one test,
  `tests/engine/test_model.py::TestOverlayModel::runTest` (448 passed, 1 failed). Shrunk case: mkdir
  `/d1`, `/d2`; rename `/d2` → `/d1/s1` and back; rmdir `/d1`; `flush()` raises `EACCES` from
  `_execute_namespace`'s rename replay (`os.replace` in `PosixTier.rename`), and a second shrink
  leaves a stray `d1` on the source. A fix is a product change in a high-blast-radius path, a
  non-goal here, yet the original R9 needs green Linux jobs. — **A:** Narrow R9 to accept that one
  test as the only failure in a unit job (it must still pass or fail on its own, untouched), keep
  every other R9 condition, and fold the defect into `issues/deferred-rename-source-orphan.md`, which
  moves to the next cycle with a human sign-off gate on the fix. (resolved 2026-11-05, Geoffrey at
  the plan gate)
- **Q:** The profile's sandbox hangs cross-parent directory renames and fresh-cache `uv sync`, so the
  `deps`, `suite` and `prepush` gates can't run in it. — **A:** Run those gates outside the sandbox.
  (resolved 2026-11-05, Geoffrey at the plan gate)

- **Q:** Review cycle 1 (F1) found `tests/engine/test_model.py::TestOverlayModel::runTest` failing on macOS too: always under `CI=true`
  (Hypothesis's fixed seed), in 2 of 12 random-seed local runs, and in both CI runs' macOS jobs. The
  R9 amendment assumed a Linux-only defect, so R2 and R8 hold only by luck. — **A:** Extend the same
  exception to R2 and R8: that one test may be the only failure, everything else stands. The fix stays
  with `issues/deferred-rename-source-orphan.md`, the next cycle, which must leave `verify` fully green.
  (resolved 2026-11-05, Geoffrey at the review gate)

## Related materials

- Seed: [`issues/toolchain-repair.md`](../../issues/toolchain-repair.md)
- Related seeds: [`issues/ci-integration-lane.md`](../../issues/ci-integration-lane.md) (integration
  lane on top of a working install), [`issues/hpc-deployment.md`](../../issues/hpc-deployment.md)
  (setgid project directories, finding 3)
- Profile: `AGENTS.md` § Factory (`setup`, `verify`, `ci`), § Verification, § Invariants § 4
- Sources: `pyproject.toml`, `uv.lock`, `tests/tier/conftest.py`, `tests/tier/test_posix.py`,
  `tests/fuse/conftest.py`, `.github/workflows/ci.yml`
- Landing commits of the faulty lines: `2813aee` (`moto[server]` dev dependency), `06a02f0`
  (Phase 7, tier conftest imports), `162625d` (Phase 3, gid assertion)
