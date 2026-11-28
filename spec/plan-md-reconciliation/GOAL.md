# GOAL — Drop the unused `rich` dependency, and hold `PLAN.md` to the redraw's reconciliation

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** plan-md-reconciliation
- **kind:** docs
- **appetite:** small

In this file a bare `PLAN.md` is the repository's design document at the root, and "the base" is
`origin/main` at `3e4ea4d`, where this cycle's branch was cut.

## Problem

`PLAN.md` is the canonical design, and `code terrace review` grades every cycle against a contract
shaped from it, so an inaccuracy there can fail correct code or credit coverage that does not exist.
The invariant audit of 2026-08-22 found five groups of them (`issues/plan-md-reconciliation.md`,
items 1 to 5): an `fsck` command the CLI table calls `audit`, a `umount` signature behind the
implementation, `rich` and `frozendict` named as decisions with no code behind them, forward `Phase N`
pointers that no longer resolve, and four testing-strategy bullets that described coverage no machine
runs.

The redraw of `PLAN.md` (`spec/plan-the-bundle-loop/`, landed) resolved all five in `PLAN.md`, item by
item (the seed's note of 2026-11-16). Shaping re-ran each of the seed's draft criteria against the
base and every one still holds; the one commit to touch `PLAN.md`, `src/terrace/__init__.py` or
`pyproject.toml` since (`821bc6d`) added a sentence about permission bits to § *FUSE adapter* and
moved none of them.

What the redraw could not do, because its GOAL forbade touching `pyproject.toml` or `uv.lock`, is the
packaging half of item 3. `pyproject.toml:30` declares `rich>=13.7` as a runtime dependency. Nothing
imports it: `git grep -n -w rich -- src tests` returns nothing at the base, and logging, exceptions and
platform output all format through `cmdkit.ansi`. In `uv.lock`, `terrace` is the only package that
depends on `rich`. A user installing the wheel downloads and installs a library the program never
loads, and the wheel's metadata advertises it. `AGENTS.md` § *Code style* (`AGENTS.md:956-957`)
records the fact as it stands today ("`rich` is a declared dependency but is **not used**"), so the
day the dependency goes, that line becomes false. This is pre-existing, not a live defect: no test
fails and nothing misbehaves.

## Outcome / vision

The package declares only what it imports, and the documents that describe the tree say so. `PLAN.md`
stays as true as the redraw left it: a reviewer holding it as the contract does not fail the `umount`
signature, look for an `fsck` command, or assume fault-injection coverage that does not exist. The
seed's five items are closed by this cycle's landing, with nothing of them left parked.

## Acceptance criteria (the contract)

R1 to R3 are this cycle's change. R4 to R7 are guards: each was met by the redraw, re-confirmed at the
base during shaping, and must still hold at the branch head, so a reviewer checks that this cycle did
not undo them. None needs a mount.

- **R1** — `pyproject.toml`'s `[project] dependencies` SHALL NOT name `rich`, and `uv.lock` SHALL hold
  no `rich` package entry: `git grep -n -w rich -- pyproject.toml uv.lock` SHALL return nothing. No
  other declared dependency, in `[project]` or in a dependency group, SHALL change, and every package
  that remains in `uv.lock` SHALL keep the version it has at the base: the lock change only removes.
- **R2** — At the branch head, `uv sync --locked` and `uv build` SHALL both succeed, and the profile's
  `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`, `AGENTS.md` § *Factory* → Profile) SHALL
  pass with the collected unit count unchanged from the base: `AGENTS.md` § *Verification* records
  851, and `uv run pytest -m unit --collect-only -q` collected 851 of 870 at the base.
- **R3** — No file outside `spec/`, `issues/` and `ROADMAP.md` SHALL state or imply that `rich` is a
  declared dependency; `git grep -n -w rich -- ':!spec' ':!issues' ':!ROADMAP.md'` lists every line to
  check. In particular `AGENTS.md` § *Code style* SHALL still say that formatting goes through
  `cmdkit.ansi`, in the same commit as R1 (`AGENTS.md` § *Conventions*, same-commit rule).
- **R4** — *(guard; the seed's R1 and R2)* `git grep -n fsck PLAN.md` SHALL still return nothing. The
  CLI line of § *Vision and scope* and the § *CLI surface* table SHALL still each name exactly the
  seven subcommands `uv run terrace --help` prints: `mount`, `umount`, `status`, `flush`, `commit`,
  `audit`, `config`. The table's `umount` row SHALL still take a spec file and say that the daemon's
  pidfile in the spec's state directory records the mounting host, which is what makes the cross-host
  guard possible.
- **R5** — *(guard; the seed's R3)* The Python-baseline row of § *Technology decisions* SHALL still
  mark the language features it names, `frozendict` included, as intent with no code behind them;
  `git grep -n frozendict -- src pyproject.toml` SHALL still return nothing, and `git grep -n -w rich
  PLAN.md` SHALL return nothing.
- **R6** — *(guard; the seed's R4)* In § *Testing strategy*, the policy bullet's rate-limit sentence,
  the FUSE-integration bullet, the crash-recovery bullet and the CI-matrix bullet SHALL each still
  either name the test path that provides the coverage it describes or mark the coverage unbuilt and
  name the `issues/` seed that owns it, and every test path and seed so named SHALL exist in the tree.
- **R7** — *(guard; the seed's R5 and R6)* Every match of `git grep -n 'Phase [0-9]' PLAN.md` SHALL
  still be a record of past work that § *Phase outline* dates, with none pointing at a phase ahead;
  § *Phase outline* SHALL still describe `ROADMAP.md` as the ordered index of seeded cycles, and
  `PLAN.md` SHALL NOT name a `wip` branch.

## Non-goals (no-gos)

- **Shortening the namespace-replay account.** `PLAN.md` § *Risks and mitigations* keeps the replay
  account as the record of a mechanism Decisions 2 and 3 retire, and `AGENTS.md` restates it.
  `issues/namespace-sync-redesign.md` (status `unshaped`, not declined) owns shortening it, as its
  draft R8. The seed's 2026-11-11 note hands that residue here only if that seed is declined, and it
  has not been.
- **Re-doing the redraw.** Items 1 to 5 are resolved; R4 to R7 guard them and ask for no edit. This
  cycle does not reword `PLAN.md`.
- **Any behaviour change.** No edit to `src/` or `tests/`, the CLI surface or `ci.yml`.
- **Building what the testing bullets mark unbuilt**: `issues/ci-integration-lane.md`,
  `issues/crash-fault-injection-suite.md`, `issues/freethreaded-concurrency-stress.md`,
  `issues/archive-finalization-integration.md`, `issues/offline-commit-command.md`.
- **Design reconciliations other seeds own**: the `umount --detach` completion semantics
  (`issues/shutdown-rescue-gaps.md`) and the mount's recovery after a kill (`issues/audit-command.md`).
- **Adopting `frozendict`, PEP 810 lazy imports or the other named language features.** They stay
  recorded intent.
- **Refreshing or upgrading the lock** beyond removing `rich` and whatever only `rich` required (R1).
- **References to this seed elsewhere** (the closing paragraph of `AGENTS.md` § *Invariants* → Known
  open violations, and several seeds). `code terrace roadmap` repairs them when it retires the seed
  after this cycle lands.

## Clarifications

- **Q:** Does `rich` leave `pyproject.toml` and `uv.lock` in this cycle, given that it puts a build
  change and a lock diff into a `docs` cycle? — **A:** Yes: it leaves both. The foreman's ruling at
  shaping. A bounded grep confirmed the premise at the base: `git grep -n -w rich -- src tests`
  returns nothing, and in `uv.lock` only `terrace` depends on `rich` (resolved 2026-11-27).
- **Q:** Does the residue of the seed's 2026-11-11 note (the namespace-replay account) come into this
  cycle? — **A:** No. `issues/namespace-sync-redesign.md` has not been declined, and its R8 owns it; it
  is a Non-goal. The foreman's ruling at shaping (resolved 2026-11-27).
- **Q:** Do the historical `Phase N` references stay, and may an agent rewrite the `umount` row to
  match the implementation? (the seed's other two open questions) — **A:** Both were settled by the
  redraw under its own signed-off GOAL: the phase numbers stay as dated records of past work, and the
  `umount` row now records the spec-file design and why. Recorded in the seed's note of 2026-11-16;
  this GOAL only guards them (R4, R7) (resolved 2026-11-16).

## Related materials

- Seed: [`issues/plan-md-reconciliation.md`](../../issues/plan-md-reconciliation.md), and its
  `ROADMAP.md` entry, *Reconcile `PLAN.md` against the implementation*.
- [`spec/plan-the-bundle-loop/`](../plan-the-bundle-loop/GOAL.md): the redraw that resolved items 1 to
  5 and left the `rich` question here.
- `pyproject.toml:30`, `uv.lock` (the `rich` package and the `terrace` entry's dependency on it),
  `AGENTS.md:956-957` (§ *Code style*), `AGENTS.md` § *Verification* (the unit count).
- `PLAN.md` § *Vision and scope*, § *CLI surface*, § *Technology decisions*, § *Testing strategy*,
  § *Phase outline*.
- [`issues/namespace-sync-redesign.md`](../../issues/namespace-sync-redesign.md): owns the replay
  account's shortening (its R8).
