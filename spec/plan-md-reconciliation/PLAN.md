# PLAN — Drop the unused `rich` dependency, and hold `PLAN.md` to the redraw's reconciliation

> **Status:** Draft for review · **Last updated:** 2026-11-27
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). There is no `research/` brief (the lean
> path); [`research/prototype.patch`](research/prototype.patch) is the prototype's diff. Every design
> element traces to a GOAL R-ID.

In this file a bare `PLAN.md` is the repository's design document at the root, and "the base" is
`git merge-base HEAD origin/main`, `3e4ea4d` when this plan was written.

## 1. Summary

One phase removes `rich` with uv's own remove operation, which re-locks without upgrading anything,
and rewrites the one `AGENTS.md` § *Code style* bullet that calls `rich` a declared dependency, in
the same commit. `PLAN.md` is not edited: R4 to R7 are guards, and the phase gate re-checks each
against the phase's tree. The work is three files and 39 changed lines, which fits `appetite: small`;
the gate is most of the plan.

## 2. Design

**The package.** `uv remove rich --no-sync`, run in the cycle worktree with the step's `UV_*`
environment. It deletes `"rich>=13.7",` from `pyproject.toml`'s `[project] dependencies` and re-locks.
uv keeps every existing pin when a lockfile is present and nothing asks it to upgrade, so the lock
loses the `rich` package, the `terrace` entry's two references to it (`dependencies` and
`metadata.requires-dist`), and the two packages only `rich` required, `markdown-it-py` and `mdurl`.
`pygments` stays: `pytest`, `sphinx` and `accessible-pygments` require it. On the prototype this was
36 deleted lines and no added line in `uv.lock`, one deleted line in `pyproject.toml`, and 63 of the
base's 66 packages kept at their base versions. A hand edit of `uv.lock` and any `--upgrade` flag are
ruled out: either can move a kept package.

**The document.** `AGENTS.md:956-957` becomes:

```
* Logging through `Logger.with_name(__name__)`; TRACE for per-operation detail; formatting goes
  through `cmdkit.ansi`.
```

The bullet no longer names `rich` at all, so R3's `git grep -n -w rich -- ':!spec' ':!issues'
':!ROADMAP.md'` returns nothing and leaves no line for a reviewer to judge. The same-commit rule
(`AGENTS.md` § *Conventions*) ties it to the package change: one phase, one commit.

**Left alone.** `PLAN.md`, `src/`, `tests/`, `.github/workflows/ci.yml`, `README.md` and
`CHANGELOG.md`. `AGENTS.md` § *Verification*'s 851 stays, because removing an unimported library
cannot change collection; the gate re-measures it. The closing paragraph of § *Known open violations*
still names the seed; `code terrace roadmap` repairs it when it retires the seed (GOAL Non-goals).

**The gate.** [`verify/dependency_gate.py`](verify/dependency_gate.py), one script with a mode per
concern (§ 6). It finds its base as `$GATE_BASE` or `git merge-base HEAD origin/main`, never a branch
commit, so it survives the landing squash. Its static modes read the index; `build` first requires the
working tree to equal it. Its log, JUnit report and `uv build` output go to `$GATE_SCRATCH`, or the main
checkout's git-ignored `.local/plan-md-reconciliation/`, never the worktree.

### Requirement → design map

| R-ID | Design element(s) that satisfy it | Gate mode |
|------|-----------------------------------|-----------|
| R1 | `uv remove rich --no-sync`: `pyproject.toml` loses one line; `uv.lock` loses `rich`, `markdown-it-py`, `mdurl` and the `terrace` entry's two references | `lock`: no `rich` in either file; `pyproject.toml` equals the base less the `rich` line, every other key and group included; both diffs from the base add no line; the lock's header is unchanged, every kept package has its base version and source, and the package set is exactly what the base lock's graph reaches from `terrace` with the `rich` edge cut |
| R2 | No code change; the edit removes a library nothing imports | `build`: `uv sync --locked`; `rich` not importable; `uv build --out-dir` with wheel and sdist metadata requiring the base's runtime dependencies less `rich`; 851 selected by `pytest -m unit --collect-only`; `tools/lint.sh`; `uv run pytest -n auto -m unit` with 851 JUnit cases, none failed or erroring; the tracked tree unchanged by the run |
| R3 | The rewritten § *Code style* bullet, in the same commit as R1 | `text`: the `git grep` R3 names is empty; § *Code style* says "formatting goes through `cmdkit.ansi`"; every commit touching `pyproject.toml` or `uv.lock` also touches `AGENTS.md`, and so does the staged change; the diff from the base holds only the three files, this cycle's `spec/`, `issues/*.md` and `ROADMAP.md`, and `AGENTS.md` changes only inside § *Code style* |
| R4 | `PLAN.md` not edited | `guards`: no `fsck`; the § *Vision and scope* CLI line and the § *CLI surface* table each name exactly the subcommands `terrace --help` prints, and those are the GOAL's seven; the `umount` row takes `<spec.toml>` and names the pidfile and the cross-host guard |
| R5 | `PLAN.md` not edited | `guards`: the Python-baseline row names `frozendict` and "These four are intent, with no code behind them"; no `frozendict` in `src/` or `pyproject.toml`; no `rich` in `PLAN.md` |
| R6 | `PLAN.md` not edited | `guards`: the policy bullet's rate-limit sentence and the FUSE-integration, crash-recovery and CI-matrix bullets each name a test path or seed, a seed wherever they mark coverage unbuilt, and every path named exists in the index |
| R7 | `PLAN.md` not edited | `guards`: every `Phase N` in `git grep -n 'Phase [0-9]' PLAN.md` is a phase § *Phase outline* dates (1 to 9); the outline calls `ROADMAP.md` the ordered index of seeded cycles; no `wip` in `PLAN.md` |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before design (gate #1) and again against the drafted design and gate (gate #2). The change
touches no numbered section (§§ 1 to 11) and no path on § *Invariants*' high-blast-radius list, so
research trigger 2 does not fire. It touches:

- **§ Conventions, same-commit rule.** A dependency change invalidates `AGENTS.md`'s statement that
  `rich` is declared; both land in one commit, and the gate's `text` mode checks each commit.
- **§ Conventions, version single-sourced.** `uv remove` does not touch `project.version` or the
  `terrace` entry's `version`; the `lock` mode's whole-file `pyproject.toml` comparison and its
  `terrace`-entry comparison would see either move.
- **§ Conventions, "this file carries measured figures".** The unit count does not move (851); the
  `build` mode re-measures it rather than trusting it.
- **§ 9, layering.** `tools/lint.sh` runs in `build`; nothing imports `rich`, so no import changes.

### Deviation justifications

Two readings of an R-ID's wording; neither bends an invariant.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| **R4**: the gate reads the subcommands from `tools/temp_site.sh uv run --locked terrace --help`, not a bare `uv run terrace --help`. | The profile drives the CLI through its throwaway helper (§ *Verification*), so the drive cannot write the developer's XDG state; `--locked` makes a stale lock fail the drive instead of being rewritten by it. The help text is the same: the subcommand list comes from `APP_HELP`, not from state. | A bare `uv run terrace --help` would write the real state directory's log path on import (`core/platform.py`) and could re-lock silently mid-gate. |
| **R2**: the gate runs the profile's verify with `--junitxml=<scratch>/…` appended to the pytest command. | R2 asks for the collected count, and § *Verification* says exit 0 is not a pass; the JUnit report gives the case count and the failure and error counts directly. The flag changes neither selection nor outcome. | Parsing the terminal summary line alone counts xfails and passes by text, which a changed pytest summary format would break silently. |

Read against the wording: R4 names the subcommands "`uv run terrace --help` prints", and the helper
runs that command; R2 requires the profile's verify to pass, which a reporting flag does not alter.

## 4. Rabbit holes (resolved)

The lean path (`appetite: small`, `kind: docs`): no fan-out. Trigger 1 (unknown root cause) does not
fire: the GOAL states the cause, and `git grep -n -w rich -- src tests` is empty at the base. Trigger 2
does not fire (§ 3). Trigger 3 does not fire: the deliverable is a dependency removal and one sentence,
not a gate, CI step or verification harness. So no adversarial reviewer ran; the planner's own mutants
below attacked the gate's decisions instead.

- **Does a removal-only re-lock exist?** → Yes. `uv remove rich --no-sync` on a detached prototype at
  the GOAL commit: `pyproject.toml` −1 line, `uv.lock` −36 lines, no added line, three packages gone
  (`markdown-it-py`, `mdurl`, `rich`), 63 kept at their base versions, the lock header (`version = 1`,
  `revision = 5`) unchanged under uv 0.13.9. Diff in [`research/prototype.patch`](research/prototype.patch).
- **Does the gate grade the right thing?** Dry runs, all on committed or staged state:
  - On the base (`3e4ea4d`): `lock` red (`rich` named), `text` red (`AGENTS.md:956`), `build` red
    (`rich` importable after `uv sync --locked`); `guards` green, as R4 to R7 hold at the base.
  - On the prototype with this plan's `TECH.md` and gate staged, then committed: every mode green
    together in one run (`lock text guards ledger build`), with `842 passed, 9 xfailed` in 68 s,
    JUnit 851 cases, 0 failed, 0 erroring, 9 skipped (the strict xfails); `prepush` green; `lock`
    green with `GATE_BASE=HEAD^`, the landing squash's setting.
  - 21 mutants planted on the prototype, one at a time, each restored by `git reset --hard`; all 21
    went red, 19 on the check they target. Lock: an extra package dropped by deletion alone, a kept
    package re-pinned, another dependency's bound changed. Text: `AGENTS.md` still naming `rich`, the
    `cmdkit.ansi` phrase gone, `AGENTS.md` edited outside § *Code style*, `PLAN.md` touched, a `src/`
    file touched, the dependency and `AGENTS.md` in two commits, and the dependency committed with
    `AGENTS.md` staged after. Guards (run alone, so scope cannot mask them): a `Phase 10` pointer,
    `fsck` named, the `config` row gone, `audit` gone from the CLI line, the `umount` row's pidfile
    clause gone, a testing bullet naming a missing test path, the crash bullet unbuilt with no seed,
    a `wip` branch, `frozendict` no longer marked as intent. Two first-round mutants were killed by an
    earlier check than the one they targeted (a split commit by the added-line check, a `Phase 10`
    pointer by scope), which is why each was re-planted in the second round.
- **The full suite on the prototype** ran once, inside `build` (above), as the skill requires.

## 5. Risks & open questions

- **A different uv at build time** could rewrite the lock's format (`revision`) or resolve markers
  differently. The `lock` mode goes red on any header change or any added line; the fix is to run the
  phase's `uv remove` with the uv the plan used (0.13.9) rather than to loosen the gate.
- **`origin/main` moving** before build: the gate's base follows `git merge-base`, so a foreman rebase
  re-grades against the new base; R1's "the version it has at the base" then means the rebased base.
- **`build` needs uv's cache or the network** (`hatchling`, the locked wheels). It ran inside the
  sandbox with PyPI reachable; a build step without it gets exit 3 (cannot run), never a green.
- **`CHANGELOG.md`** is not updated. The GOAL does not ask for it, the project is unreleased, and the
  changelog has been edited once since its scaffold (the rename). Recorded here so the reviewer does
  not read its absence as an oversight.
- No open questions.

## 6. Verification strategy

One phase, one `verify:`, the gate's modes in order, cheapest first:

`uv run --no-project --python 3.15 python -I spec/plan-md-reconciliation/verify/dependency_gate.py lock text guards ledger build`

- `lock` (R1) and `text` (R3, scope) read the index: the phase stages its three files first.
- `guards` (R4 to R7) drives the real CLI once, `terrace --help` inside `tools/temp_site.sh`.
- `ledger` walks `TECH.md`'s phase body for deferral phrasing; this plan defers nothing.
- `build` (R2) runs the profile's verify in full, not narrowed: the change is to the environment
  every test runs in.
- `prepush` (clean tree, `lock`, `text`, `guards`) is the mode publish can run before it pushes, with
  `GATE_BASE=HEAD^` after the squash.

Exit 0 green, 1 red, 3 could not run. Footprint: one subprocess at a time; `-n auto` peaks near 1.5 GB.

What no gate can see: whether the rewritten `AGENTS.md` bullet reads well in its list. That is the
reviewer's to read; it is one line.

---

*No `research/00-digest.md`: the lean path.*
