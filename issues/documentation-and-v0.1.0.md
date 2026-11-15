---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Documentation and v0.1.0

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

There is no documentation and there has never been a release. `docs/` does not exist, so
`uv run sphinx-build docs docs/_build` — the command `AGENTS.md` lists under development commands —
cannot run. The `docs` dependency group is declared (`pyproject.toml:47-50`, `sphinx>=8` and `furo`)
and has never been installed by anything: `[tool.uv] default-groups = ["dev"]` (`pyproject.toml:52-53`),
and `.venv/bin` contains no `sphinx-build`. Neither dependency has been exercised even to the extent
of proving it resolves on 3.15.

The rest of the release surface is at scaffolding level. `README.md` is 22 lines. `CHANGELOG.md`'s
unreleased section has one `### Added` bullet — "Project scaffolding: packaging, license,
documentation stubs" — for eight completed phases, and one `### Changed` entry, the rename to
Terrace. `pyproject.toml:7` reads `version = "0.1.0.dev0"`, single-
sourced and read at runtime through `importlib.metadata`; `uv run terrace --version` prints
`terrace v0.1.0.dev0 (CPython 3.15.0a3)`.

What the documentation has to cover: a quickstart, an overlay-spec reference for every key in
`OVERLAY_KEYS` and `TIER_KEYS` (`src/terrace/tier/spec.py:27-28`), an architecture page derived from
`PLAN.md`, an operations guide built around the job-script detach-then-commit pattern — the
differentiating lifecycle, and the one thing a site operator will read first — and a CLI reference
for all seven subcommands (`tests/test_cli.py:31` pins the list: `mount`, `umount`, `status`, `flush`,
`commit`, `audit`, `config`).

Two things a release cycle will discover, both cheap to record now and annoying to hit at tag time.

- **There is no PyPI publish workflow.** `.github/workflows/ci.yml` has three jobs — `unit`,
  `unit-macos` and `checks` — and none of them publishes anything. `checks` runs `uv build`
  (`ci.yml:72`) and throws the artifacts away. `code terrace release` stops at the pushed, signed
  tag — the release recipe in `AGENTS.md` § *Factory* has `publish: none` — so PyPI is not wired; whether this cycle wires it is a decision, not an
  oversight to fix in passing.
- **sdist hygiene is unchecked.** `pyproject.toml` configures only
  `[tool.hatch.build.targets.wheel] packages = ["src/terrace"]` (`:63-64`). There is no
  `[tool.hatch.build.targets.sdist]` section, so the sdist takes hatchling's default and nobody has
  looked at whether `spec/` and `issues/` — the in-flight cycle records and the deferral queue —
  end up shipped inside it. The mirror-image question is worth one
  assertion in the same pass: `src/terrace/state/schema.sql` is not a `.py` file and is read at runtime
  through `importlib.resources.files('terrace.state')` (`src/terrace/state/db.py:52`), so a wheel that
  omits it installs cleanly and fails on the first `Database.initialize()`.

This is unbuilt work throughout. Nothing here is a defect.

## Why it was deferred

Documentation of unbuilt commands is fiction. Five of the seven subcommands are `StubApp` subclasses
that raise `RuntimeError` today, so a CLI reference written now would document error messages. This
cycle sits last for that reason: it consumes [`issues/config-command.md`](config-command.md),
[`issues/control-channel-and-status.md`](control-channel-and-status.md),
[`issues/audit-command.md`](audit-command.md) and
[`issues/offline-commit-command.md`](offline-commit-command.md) closing the CLI surface, and
[`issues/benchmark-harness.md`](benchmark-harness.md) producing the overhead numbers the
architecture page needs.

Not deferred by the audit — scheduled by `PLAN.md` § *Phase outline* and carried forward. What the
audit added is the two release-readiness gaps above.

## Outcome / vision

A site operator can go from `uv pip install` to a mounted overlay from the quickstart alone, then
find every spec key in one reference page, then copy the job-script pattern — mount, run,
`umount --detach`, `terrace commit` from a login node — out of the operations guide. The architecture
page explains the tier stack, the write-back clocks and the two sacred invariants from `PLAN.md` without
being a second copy of `PLAN.md` that drifts. `CHANGELOG.md` says honestly what 0.1.0 does and does
not do. The tag is reproducible from a clean checkout and the artifacts are inspected rather than
assumed.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHEN `uv run --group docs sphinx-build -W docs docs/_build` is run, it SHALL exit 0 with
  zero warnings.
- **R2** — THE documentation SHALL contain a quickstart whose commands run verbatim under
  `tools/temp_site.sh`, a spec reference covering every key in `OVERLAY_KEYS` and
  `TIER_KEYS`, an architecture page derived from `PLAN.md`, an operations guide showing the job-script
  detach-then-commit pattern end to end, and a CLI reference for all seven subcommands.
- **R3** — THE CLI reference SHALL agree with `uv run terrace <cmd> --help` for every subcommand,
  asserted by a test rather than by review, so a later flag change fails the suite instead of rotting
  the page.
- **R4** — WHEN `uv build` runs, it SHALL produce an sdist and a wheel that pass
  `uvx twine check --strict`; the wheel SHALL contain `terrace/state/schema.sql`; and the sdist SHALL
  NOT contain `spec/` or `issues/`.
- **R5** — WHEN the release lands, `pyproject.toml` SHALL read `version = "0.1.0"`,
  `uv run terrace --version` SHALL print `terrace v0.1.0`, and `CHANGELOG.md` SHALL carry a dated
  `[0.1.0]` section enumerating the shipped capabilities.
- **R6** — THE `README.md` and the `CHANGELOG.md` entry SHALL state what 0.1.0 does not do:
  whole-file granularity, single-client coherence only, and no PyPI distribution.
- **R7** — `code terrace release` SHALL stop at the pushed, signed tag and SHALL print that no PyPI
  publish workflow exists.

## Notes

- Related: every cycle above this one in [`ROADMAP.md`](../ROADMAP.md) supplies something this one
  documents. The direct dependencies are [`issues/config-command.md`](config-command.md),
  [`issues/control-channel-and-status.md`](control-channel-and-status.md),
  [`issues/audit-command.md`](audit-command.md),
  [`issues/offline-commit-command.md`](offline-commit-command.md) and
  [`issues/benchmark-harness.md`](benchmark-harness.md). Root `PLAN.md` → § *CLI surface* (the
  table the CLI reference must match), § *Lifecycle and recovery* (the drain/detach semantics the
  operations guide explains), § *Phase outline*.
- The rename came first, as this note asked: the rename to Terrace landed on 2026-11-12
  (`spec/rename-to-terrace/`), so the entry point, environment prefix and xattr namespace this cycle
  documents are already `terrace`, and the ordering constraint is discharged. That cycle also wrote
  the `CHANGELOG.md` entry announcing the rename, which this seed was once to carry.
- Open question — **does the docs build join CI as a fourth job?** It catches rot, which is the whole
  reason R3 exists as a test rather than a review item; it also adds a Sphinx install to every pull
  request, and `-W` turns a Sphinx minor release into a red build on a change that touched no
  documentation.
- Open question — **autodoc or narrative only?** Running autodoc over `src/terrace/` publishes the
  engine API as a supported surface it is not, and `PLAN.md` names legibility as a reference
  implementation as the project's value, which argues for narrative pages that explain the design
  over generated pages that list signatures.
- Open question — **who bumps the version.** This cycle, or `code terrace release` as part of the tag?
  Splitting it means the diff that says "0.1.0" and the diff that documents 0.1.0 are two commits.
- Open question — **`PLAN.md` corrections.** The plan's § *Vision and scope* lists a `fsck` command
  that its own § *CLI surface* table calls `audit`, and no such command exists. Under plan drift that
  correction is formally a design change; [`issues/plan-md-reconciliation.md`](plan-md-reconciliation.md)
  owns it, and this cycle must not propagate the inaccuracy into the docs if it has not yet landed.
- Found by: scheduled work carried forward from `PLAN.md` § *Phase outline*. The missing PyPI
  workflow and the unchecked sdist hygiene were found by the invariant audit run during the
  software-factory port (2026-08-22) — remaining-work review of `pyproject.toml` and
  `.github/workflows/ci.yml`.
