# REVIEW — Drop the unused `rich` dependency, and hold `PLAN.md` to the redraw's reconciliation

> Adversarial QA by `code-review`, run in an isolated context (a fresh subagent). The correctness pass
> graded the branch diff against [`GOAL.md`](GOAL.md) and the repo's `AGENTS.md` § Invariants only;
> it did not see this cycle's `PLAN.md` or `TECH.md`. Every finding cites an executed command.

- **Reviewed commit:** `31b0b707b99e66da59bad1f1580c16c4be53e9dc`  ·  **Base:** `origin/main` at `3e4ea4d`  ·  **Date:** 2026-11-27
- **Verdict:** approved
- **Cycle:** 1 of ≤3

The reviewer's evidence is in the worktree's git-ignored `.local/review1/`; publish copies it to the
main checkout's `.local/plan-md-reconciliation/`.

## Verification run

- **Spec-held gate, run by the orchestrator before the reviewer launched:**
  `dependency_gate.py lock text guards ledger build` at `31b0b70` → exit 0. Every mode was green. The
  profile's verify ran 842 passed and 9 xfailed in 69 s; JUnit recorded 851 cases with 0 failures and
  0 errors. The wheel and sdist require only cmdkit, mfusepy, obstore and tomlkit. Log:
  `.local/review1/spec-gate.log`.
- **The profile's verify, run by the reviewer in its own venv:**
  `tools/lint.sh && uv run --locked pytest -n auto -m unit -rfEs` → lint passed all five checks. Of
  851 items, 842 passed and 9 xfailed (the strict xfails of the bundle-loop progress record) in
  69.08 s, exit 0. Log: `verify.log`.
- **Throwaway-environment drive:** `tools/temp_site.sh` running `uv run terrace --help` printed the
  seven subcommands. All 32 `terrace` modules import, and none of `rich`, `markdown_it` or `mdurl`
  appears in `sys.modules`. Logs: `drive.log`, `import-all.log`.
- **Build:** `uv sync --locked` and `uv lock --check` both exit 0. `import rich` raises
  `ModuleNotFoundError`. `uv build --out-dir <scratch>` produces a wheel and an sdist whose
  `Requires-Dist` names cmdkit[toml], mfusepy, obstore and tomlkit only. Log: `r2-head.log`.
- **Base control:** an exported tree at the base collects 851/870 unit tests in its own venv, the
  same count as the head. Log: `mutants-and-base.log`.

## Requirement → evidence matrix

Every row was verified by the blind reviewer; no R-ID is an artifact deliverable.

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `pyproject.toml`, `uv.lock` (`31b0b70`) | `git grep -n -w rich -- pyproject.toml uv.lock` returns nothing. A tomllib comparison of the two locks: 66 packages at the base and 63 at the head. Removed: `rich`, `markdown-it-py`, `mdurl`, exactly the packages reachable only through `rich`. None added, no version changed. The only retained entry that changed is `terrace`, which lost only `rich`. `pygments` stays, still required by other packages (`r1-lock-compare.log`) | ✅ |
| R2 | same | `uv sync --locked` and `uv build` succeed. Verify is green with 851 collected at both head and base (above) | ✅ |
| R3 | `AGENTS.md:956-957` (`31b0b70`) | `git grep -n -w rich -- ':!spec' ':!issues' ':!ROADMAP.md'`, case-sensitive and case-insensitive, returns nothing. The § Code style line names `cmdkit.ansi`, and `core/exceptions.py:21`, `core/logging.py:25` and `core/platform.py:17` import it. `AGENTS.md`, `pyproject.toml` and `uv.lock` change in the same commit (`r3-same-commit.log`, `cmdkit-ansi.log`) | ✅ |
| R4 | guard (`PLAN.md` byte-identical to the base) | No `fsck`. `PLAN.md:359` and the CLI surface table name the same seven subcommands as `--help`. The `umount` row names the spec file, the pidfile, the mounting host and the cross-host guard (`r4-r7-guards.log`) | ✅ |
| R5 | guard | `PLAN.md:1275` marks the four features as intent. `frozendict` appears in neither `src` nor `pyproject.toml`, and `rich` does not appear in `PLAN.md` | ✅ |
| R6 | guard | The four bullets each name a test path or seed, and all 12 named paths exist at the head | ✅ |
| R7 | guard | Five `Phase [0-9]` matches (lines 1278, 1346, 1390, 1473, 1475), all dated phases 7–9. Line 1482 calls `ROADMAP.md` the ordered index of seeded cycles; no `wip` | ✅ |

Unmapped changes: none. `issues/plan-md-reconciliation.md`'s `status: adopted:plan-md-reconciliation`
(`d2ad8f6`) is the lifecycle bookkeeping § Factory prescribes.

## Findings

None, CONFIRMED or PLAUSIBLE. Each candidate the reviewer refuted was dropped:

- **Another route loads `rich`.** No such route: there is no token in `src/` or `tests/`, and the
  import of every module stays clean.
- **No CHANGELOG entry for the dropped dependency.** No R-ID or convention asks for one.
- **Other `AGENTS.md` lines go stale.** No other line names `rich` or the dependency list.

**Mutation grading.** The diff changes no Python. The reviewer planted four mutants, each in a
`git archive` copy under scratch (`mutants-exit.log`, `mutants-and-base.log`):

| Mutant | Result |
|---|---|
| M1, re-declare `rich` with the head lock | killed: `uv lock --check` and `uv sync --locked` exit 1 |
| M2, the head `pyproject.toml` with the base lock | killed: `uv lock --check` and `uv sync --locked` exit 1 |
| M4, `import rich` in `terrace/__init__.py` | killed: `--help` exits 1 and collection errors |
| M3, the base lock's orphan `mdurl` block put back | **survives**: `uv lock --check` and `uv sync --locked` both exit 0 |

**Coverage observation, not seeded.** M3 breaks none of R1's checks, and the head lock is correct by
direct comparison. The survivor is a property of uv's lock check, which accepts an unreachable
package entry. It is not a property of the project's code, and no project gate would earn its keep
guarding against it. If such an orphan ever matters, `uv lock` rewrites it away.

## Human-gate triggers

None. No CONFIRMED finding, and the diff touches no path on § Invariants' high-blast-radius list.
