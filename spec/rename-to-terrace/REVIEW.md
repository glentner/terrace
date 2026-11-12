# REVIEW — Rename the project to Terrace

> Adversarial QA by `code-review`. The correctness pass ran in a fresh subagent and graded the diff
> against [`GOAL.md`](GOAL.md) and `AGENTS.md` § Invariants **only**. It did not see `PLAN.md` or
> `TECH.md`, which avoids grading the plan's own homework. Every finding cites an **executed**
> command, not an assertion.

- **Reviewed commit:** `126eb0d`. That is the build head `3a34ff2` plus this review's deferral seed;
  nothing under `src/`, `tests/` or `tools/` differs between the two. **Base:** `main` (`origin/main`
  at `bf438b4`). **Date:** 2026-11-12.
- **Verdict:** approved.
- **Cycle:** 1 of ≤3, mirroring `review.cycle` in `TECH.md`. It was a fresh blind pass over the full
  spec-excluded diff: 125 files, +999/−865.
- **Contract drift:** the GOAL moved once after the shape commit. `a445832` added two clarifications
  at the plan sign-off: R6's prefix match and R1's `ROADMAP.md` entry. The foreman confirmed them, and
  the review graded the GOAL as it stands at head.
- **Mode:** one blind reviewer. The `debate` variant and the completeness sub-pass were not run,
  following the foreman's ruling for a mechanical rename. The sequential fallback did not run. Depth
  went to the places a rename can be wrong without a test failing:
  - the on-disk literals and their recognizers on all three tier adapters;
  - the environment prefix and its unlinked literals;
  - the `core/platform.py` path block;
  - the extended-attribute names;
  - packaging, CI and `tools/`.

  The 34 reworded seeds were sampled and censused rather than read line by line.

Evidence: every script and log named below is in the worktree's gitignored `.local/review1/`.

## Verification run

- `uv sync`, then the profile's `verify`, `tools/lint.sh && uv run pytest -n auto -m unit`.
  - Lint: `lint: all checks passed` (ruff, layering, the SPDX headers on 75 files, the version
    single-source, the `CLAUDE.md` symlink).
  - Unit lane: **731 passed** in 30.40 s, with 0 failed, 0 errors and 0 skipped. Of 741 collected, 10
    were deselected (the integration tests). This equals the base commit's 731 (`verify.log`).
- R1's census, run exactly as the GOAL writes it, exits 1 with no output (`r1_census.log`).
  - `git ls-files | grep -i sosfs` prints nothing.
  - Root `GOAL.md` is blob `9726779` at both base and head.
  - The orchestrator re-ran the census at the re-pinned head `126eb0d`: still empty.
- CLI drive (`r2_cli.log`):
  - `uv run terrace --version` and `uv run python -m terrace --version` both print
    `terrace v0.1.0.dev0 (CPython 3.15.2)` and exit 0.
  - `--help` lists all seven subcommands.
  - The old console script and the old import both fail.
- `uv build -o .local/review1/dist` builds exactly one sdist and one wheel, `terrace-0.1.0.dev0`
  (`r3_build.log`).
  - The wheel's METADATA has `Name: terrace` and
    `Project-URL: Repository, https://github.com/glentner/terrace`.
  - The entry point is `terrace = terrace:main`, and `schema.sql` is packaged.
  - `uv lock --check` exits 0.
  - `dist/` was never touched: it was absent before the build and after it.
- Engine drives over empty roots on the POSIX, S3 and archive tiers, and the model and replay tests
  (287 passed) (`r4_drive.log`, `collect_and_model.log`).
- Old-name drives, which the GOAL's preamble asks for from untracked scripts:
  - R5: `r5_drive.sh` → `r5_drive.log`.
  - R6 and R7, run under `tools/temp_site.sh`: `r6_r7_drive.sh` → `r6_r7_drive.log`.
  - R8: `r8_drive.py` → `r8_drive.log`.
  - R9: `r9_drive.log`, which opens a database built by the base commit's source. That source was
    extracted with `git archive bf438b4`.

  Their results are in the matrix.
- **Not run:** the real-mount check of R8 (`tests/fuse/test_integration.py`, `-m integration`). The
  integration lane runs nowhere, as the GOAL rules.
- **Not observed:** CI on this branch. This step has no CI access, and `code-publish` reads the
  checks.

## Requirement → evidence matrix

| R-ID | Verified by | How | Status |
|------|-------------|-----|--------|
| R1 | reviewer, then orchestrator at `126eb0d` | The census is empty. `ls-files` has no match. Root `GOAL.md` is byte-identical. The only collapsed-form hits (`TERRACE` followed by a letter) are in the excluded `issues/rename-to-terrace.md`. | ✅ |
| R2 | reviewer | Package `src/terrace/`, with `TerraceApp` and `TerraceError`. `--version` gives the same line both ways, exit 0. `--help` lists `mount umount status flush commit audit config`. | ✅ |
| R3 | reviewer | Exactly two build files. The wheel names `terrace` and the repository URL, as do `pyproject.toml` and `__website__`. `uv lock --check` exits 0. The `uv.lock` diff moves only the name lines, plus the block re-sorted to follow them. | ✅ |
| R4 | reviewer | `.terrace/marker` is written on attach. The POSIX temp lands under `.terrace/partial/` and `is_temp_name` recognizes it. Raw S3 keys are `.terrace/marker` and `emptydir/.terrace.keep`. `.terrace` reads as absent and is left out of listings and usage. The model test shows no divergence. | ✅ |
| R5 | reviewer (drive) | The base code builds the overlay, which leaves `.sosfs/marker` with its row. Attaching with the renamed engine raises `EngineError: Tier 'project' marker is missing — audit required`. The tier trees are byte-identical before and after, no `.terrace` appears, and residency rows stay 1 → 1. Through the CLI, `mount -f` exits 5 and the daemonized path exits 1, with the same message. Complement: with no database the root is adopted as new, as the non-goals require. | ✅ |
| R6 | reviewer (drive under `temp_site.sh`) | `TERRACE_LOGGING_LEVEL=debug` gives level `debug` with provenance `from: TERRACE_LOGGING_LEVEL`, and `TERRACE_CONFIG_FILE` loads as the local configuration. `SOSFS_LOGGING_LEVEL`, `SOSFS_CONFIG_FILE`, `LOGGING_LEVEL` and `FOO_LOGGING_LEVEL` have no effect. The scrub works. The fixture, the CLI helper and the S3 suite use `TERRACE_*`. | ✅ |
| R7 | reviewer (fresh process, and a `temp_site.sh` first run) | With XDG set and with it unset, every per-user, system-wide and local path is as written in R7. The old `./.sosfs.toml` and `…/sosfs/config.toml` are ignored. The first run creates `state/terrace/log`. | ✅ |
| R8, attribute names and ENOATTR | reviewer (drive) | `user.terrace.dirty` reads `b'1'` when dirty and `b'0'` when clean. `user.terrace.tiers` reads `b'scratch'`. `user.sosfs.*`, `user.TERRACE.dirty`, `user.terrace` and `user.something.else` all raise errno 93 (ENOATTR). See coverage observation C1: the unit tests alone do not pin the names. | ✅ |
| R8, records clause | orchestrator | `PLAN.md:163` and `TECH.md:162` state that the real-mount check was **not run**, and this record states it under *Verification run*. | ✅ |
| R9 | reviewer | `schema.sql` with comments stripped is identical to base. `user_version` is 2 and `DB_NAME` is `state.db`. A database built by the base commit's code passes `Database.initialize()` with no `SchemaMismatch`. A spec without `state` is refused with `[overlay] requires a state directory`. | ✅ |
| R10 | reviewer | `verify` exits 0 with 731 passed, the base commit's count, and no failure, error or skip. | ✅ |
| R11 | reviewer | `CHANGELOG.md` `[Unreleased]` › `### Changed` marks the rename as breaking with no migration path and names all eleven moved items, old → new. The `PLAN.md` package/entry-point row names `terrace` and says why the earlier decision was overturned. `AGENTS.md` no longer calls the rename pending. | ✅ |

**Operating manual:** no drift. `AGENTS.md` has no remaining old name. The repository map, the
`coupled core` row and the throwaway-environment contract use the new paths. The high-blast-radius
table has 34 rows at both base and head, and only its stated base moved to `src/terrace/`
(`hbr_bf438b4.txt`, `hbr_HEAD.txt`). No spec ids are embedded in `src/`, `tests/` or `README.md`.

**Unmapped changes:** none that block.
- The new seed `issues/xdg-empty-and-relative-values.md` and its `ROADMAP.md` entry are a deferral
  record. The reviewer graded them as prose and reproduced the seed's table (`seed_xdg_check.log`).
- The dated note on `issues/config-command.md` is the one the GOAL's R6 clarification names.

## Findings

No CONFIRMED finding and no PLAUSIBLE finding.

### Coverage observation C1 — no unit test pins the extended-attribute names

- **Where:** `tests/fuse/test_adapter.py:25,234-270`.
- **Observation:** the tests import `XATTR_TIERS`, `XATTR_DIRTY` and `XATTRS` from the adapter and
  query the adapter with those same constants.
- **Evidence:** the reviewer's untracked plugin `mutant_xattr_plugin.py` rebinds the constants in
  memory to the old name pair. The six attribute tests still pass, `6 passed, 21 deselected`, the same
  as the unmodified run (`r8_drive.log`).
- **Why it is not CONFIRMED** (rubric → *Verdict & loop*, coverage observation):
  - The mutant breaks no clause of R8 that says SHALL. Those clauses are behavioural, and the drive
    shows the literals are right.
  - R8's sentence that the adapter tests "grade the attribute names" describes how R8 is graded and
    places no requirement on the tests. This review closed that gap for this cycle by driving the
    literals.
  - The gap is pre-existing: the tests used the constants the same way at `bf438b4`.
- **Home:** seeded as `issues/xattr-names-unpinned.md`, with its `ROADMAP.md` entry, in `126eb0d`.

### Out of Scope — an empty or relative `XDG_*_HOME` is used as written

The reviewer reproduced it. With both variables empty, the per-user configuration path is relative and
`./terrace/log` is created in the working directory. This is present at the base commit. The GOAL's
clarification rules that this cycle only renames, so no R-ID asks for it. It already has its home: the
cycle filed `issues/xdg-empty-and-relative-values.md` and its `ROADMAP.md` entry, so no new seed is
needed.

## Human-gate triggers

Not triggered. No finding is CONFIRMED, so none touches a path on the high-blast-radius list in
`AGENTS.md` § Invariants, and none violates a `[human gate]` section. The rename passes textually
through every path on that list, which is why the GOAL gave this review the gate. For this run the
foreman is the judge of the gate.

## Optional completeness sub-pass

Not run, by the foreman's ruling.
