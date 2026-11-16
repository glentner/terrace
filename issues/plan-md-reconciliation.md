---
status: unshaped
kind: docs
appetite: small
lane: public
---

# Reconcile `PLAN.md` against the implementation

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`PLAN.md` is the canonical design and outranks both the code and `AGENTS.md` on questions of intent,
and `code terrace review` grades a cycle against a contract shaped from it. An inaccuracy there is not a
cosmetic defect: it is a chance to grade correct code as wrong, or to accept an unimplemented
promise as coverage that exists. The audit found `PLAN.md` internally inconsistent in one place and
behind the code in several others. All citations below were re-verified while writing this seed.

**1. `fsck` versus `audit` — `PLAN.md` contradicting itself.** `PLAN.md:50` summarizes the CLI as
`terrace mount|umount|status|flush|fsck|config`. Its own CLI-surface table a hundred lines later
(`PLAN.md:452-465`) lists seven commands including `audit` and no `fsck`, and the code implements
`AuditApp` (`src/terrace/__init__.py:167-171`, registered at `:219-224`). No `fsck` command exists or
is planned. The same line also omits `commit`, which is the project's differentiating feature. This
one needs no judgment about code: the document disagrees with itself, and the table is right.

**2. `umount` takes a spec file, not a mountpoint, and the code is the better design.** The CLI table
reads `terrace umount <mountpoint>`. The implementation takes `<spec>` (`src/terrace/__init__.py:104-108`
usage, `:128-129` argument) and resolves the daemon through the pidfile in `spec.state`. That
indirection is what makes the cross-host guard possible — `fuse/mount.py:125-127` reads the recorded
host out of the pidfile and refuses with `Overlay 'name' is mounted on <host>, not this host —
unmount it there, or finalize with 'terrace commit'`. Reconciling in the other direction would delete a
working guard, so `PLAN.md` is the document that is wrong and the reconciliation has to record *why*,
or a later reviewer holding the plan as the contract will file the working behavior as a defect.

**3. `rich` and `frozendict` are recorded intent with nothing behind them.** `pyproject.toml:30`
declares `rich>=13.7`; `grep -rn rich src tests` returns nothing — `core/logging.py:25`,
`core/exceptions.py:21` and `core/platform.py:17` all format through `cmdkit.ansi`. `frozendict`
appears at `PLAN.md:475` inside the Python-3.15 rationale ("modern innovations: lazy imports
(PEP 810), `frozendict` (PEP 814), sampling profiler (PEP 799)…") and appears in neither the
dependency list nor `src/`. `AGENTS.md:303-304` already records the `rich` fact correctly; `PLAN.md`
does not, and a declared-but-unimported dependency is also a packaging fact that ships in the wheel's
metadata. Two reconciliations are available and they differ in cost — strike the unused names from
the rationale, or keep them as stated intent and let a later cycle earn them.

**4. Phase attribution, and a phase vocabulary that no longer resolves.** `PLAN.md:517` schedules the
benchmark harness in "Phase 10" and `:531` puts the stress suite in "the Phase 10 stress suite"; the
checklist that those numbers referred to scheduled both in Phase 11. That drift was harmless while
the checklist existed. It does not any more: the factory port replaced `ROADMAP.md` with an ordered
index of seeded cycles carrying no phase numbers, while `PLAN.md:552` still says "Expanded with
discrete checklist steps in ROADMAP.md". Ten `Phase N` references survive in `PLAN.md`, split between
historical records that remain true (`:478` "verified Phase 7", `:538` "resolved, Phase 8") and
forward pointers that now resolve to nothing (`:213` and `:546` "the audit's job (Phase 10)", `:481`
and `:591` "Phase 12"). In the same section, `PLAN.md:555` lists "repo hygiene (AGENTS.md, skills,
wip branch)", and the workflow this port installs has no `wip` branch.

**5. Four of the eight testing-strategy bullets overstate what exists.** `PLAN.md:487-509`. The other
four are accurate and should be left alone: engine-first (`:490`), model-based hypothesis against a
directory oracle (`:492`), moto for unit and MinIO for integration (`:499`), and the xattr residency
assertions in the FUSE integration tests (`:498`, matching `tests/fuse/test_integration.py:114`).
The four that do not hold:

* `:497` places the FUSE integration tests in "Linux CI". No job in `.github/workflows/ci.yml`
  invokes `-m integration` and nothing starts MinIO. Ten tests — the whole drain, detach,
  force-umount, xattr and metadata-propagation surface plus the only real-S3 round trip — execute on
  no automated machine. `issues/ci-integration-lane.md` owns building the lane; this seed owns the
  sentence that claims it already exists.
* `:500-503` promises fault injection "between intent, act, and confirm of every mutation type;
  mid-drain; mid-rescue; mid-re-sync" followed by audit and remount, plus tier-evaporation tests.
  What exists is `tests/state/test_survival.py` — 130 lines, three tests, two SIGKILL points, no
  audit, no remount, no tier evaporation. `issues/crash-fault-injection-suite.md` owns the work.
* `:495-496` claims rate-limit behavior is tested under the virtual clock. `parse_rate` and `Rate` are
  unit-tested (`tests/core/test_units.py:93`), and no write-back rate limiting is implemented, so
  there is nothing to exercise. Quiescence delay, max-interval and watermark hysteresis are real
  (`tests/engine/test_policy.py`). No queued cycle builds the missing half, which makes this the one
  bullet whose reconciliation is a decision rather than a status update.
* `:508-509` says "macOS engine-only" and that "free-threaded jobs run the same suite plus targeted
  concurrency stress tests". The macOS job runs the identical `uv run pytest -n auto -m unit`
  (`ci.yml:58`) and is engine-only only as a side effect of no mount test carrying the `unit` marker;
  the `3.15t` job runs the same command with no additional stress tests, and
  `tests/tier/test_concurrency.py` runs on every job equally.
  `issues/freethreaded-concurrency-stress.md` owns the missing tests.

**What this seed does not hold.** Anything a later cycle changes by doing its own work is reconciled
in that cycle's diff, under the same-commit rule — not parked here. The rename to Terrace
(`spec/rename-to-terrace/`, landed 2026-11-12) rewrote the package row in Technology decisions and
the path and prefix strings, and passed through line 50 changing only the name: its GOAL left the
command list as it was, so item 1 stays here.
`issues/shutdown-rescue-gaps.md` owns the `umount --detach` completion semantics, which is a design
change and belongs in `PLAN.md` as part of that cycle rather than as a documentation edit here.
`issues/audit-command.md` owns the CLI table's promise that `mount` auto-audits after an unclean
shutdown. A reader who finds a new inaccuracy should ask first whether a queued cycle already passes
through it; if one does, it belongs in that cycle.

## Why it was deferred

Found by the invariant audit run while porting the software factory. A documentation-only pass
through `PLAN.md` was outside a harness change, and two of the items are not edits an agent should
make unaccompanied: item 2 overwrites a recorded design statement with the implementation, which is
precisely the move the plan-drift rule exists to prevent, and item 3 may pull `pyproject.toml` into a
`docs` cycle.

**This is not a live defect.** Nothing in `src/` misbehaves because of any of it, and no test fails.
The cost is misgrading and misplaced confidence: a reviewer can mark the `umount` signature wrong
because the plan says mountpoint, or design a `verify:` gate that assumes fault-injection coverage
that does not exist. `AGENTS.md` § *Invariants* § *Known open violations* closes by pointing at
this file for exactly the rows that are `PLAN.md` inaccuracies rather than code defects.

## Outcome / vision

`PLAN.md` is true about what exists and explicit about what does not, with intent and status
distinguishable in the reading. Someone shaping a `verify:` gate from it does not assume coverage no
machine runs, and someone reviewing against it does not fail correct code.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. All of these are checkable by reading the diff and running
`git grep`; none needs a mount.

- **R1** — `git grep -n fsck PLAN.md` SHALL return nothing, and the summary line and the CLI-surface
  table SHALL name the same subcommands as `uv run terrace --help` — including `commit`.
- **R2** — The CLI table's `umount` row SHALL name a spec file, and SHALL state that the pidfile in
  `spec.state` is what makes the cross-host guard possible, so the design decision is recorded and
  not merely overwritten.
- **R3** — WHERE `PLAN.md` names a dependency or a language feature as a decision, the name SHALL
  either be present in `pyproject.toml` and imported under `src/`, or be marked as intent with no
  code behind it. `git grep -n frozendict PLAN.md src pyproject.toml` and `grep -rn rich src tests`
  SHALL agree with whatever the row says.
- **R4** — Each of `PLAN.md:495-496`, `:497`, `:500-503` and `:508-509` SHALL either name the test
  path that provides the coverage it describes, or be marked as unbuilt with the `issues/` filename
  that owns it, so a reader can tell which of the eight bullets are claims about today.
- **R5** — `git grep -n 'Phase [0-9]' PLAN.md` SHALL return only dated records of past work; no
  surviving reference SHALL point forward at a phase `ROADMAP.md` cannot resolve.
- **R6** — `PLAN.md:552` SHALL describe `ROADMAP.md` as the ordered index of seeded cycles it now is,
  and `PLAN.md:555` SHALL NOT name a `wip` branch the workflow does not have.
- **R7** — `uv run pytest -m unit` SHALL pass with an unchanged collected count, because nothing here
  is a behavior change; IF `rich` is dropped from `pyproject.toml`, THEN `uv sync` and `uv build`
  SHALL both still succeed.

## Notes

- Related: `spec/rename-to-terrace/` (landed 2026-11-12; renamed `PLAN.md:50` and rewrote the
  package row, leaving item 1 here); `issues/ci-integration-lane.md`, `issues/crash-fault-injection-suite.md` and
  `issues/freethreaded-concurrency-stress.md` own the *work* behind three of the four overstated
  testing bullets; `issues/shutdown-rescue-gaps.md` and `issues/audit-command.md` own the design
  reconciliations deliberately excluded above; `issues/documentation-and-v0.1.0.md` must consume a
  reconciled `PLAN.md` rather than propagate its inconsistencies into the architecture page, and the
  remaining-work brief left open whether the docs cycle or a plan-drift cycle does the correcting —
  this seed is the answer.
- `PLAN.md` sections in scope: the summary bullet at `:50`, the CLI surface table (`:452-465`),
  Technology decisions (the Python-baseline rationale at `:475`), Testing strategy (`:487-509`),
  Risks (`:517`, `:531`), and the Phase outline (`:550` onward).
- Open question — **does this cycle touch `pyproject.toml` to drop `rich`?** Keeping it
  documentation-only makes the cycle trivially safe and leaves a declared dependency nobody imports
  in the published metadata, which a user installing the wheel pays for. Dropping it makes a `docs`
  cycle carry a build change and puts `uv.lock` in the diff, which is a larger review than the
  appetite suggests.
- Open question — **do the historical `Phase N` references stay?** They are the only record of when a
  decision was verified ("obstore free-threaded wheels — verified 2026-07-11, Phase 7 start"), and
  stripping them costs that provenance. Keeping them means the word "Phase" survives in a repository
  whose roadmap has no phases, which reads as drift to a new reader. A middle option is to keep them
  only where they carry a date.
- Open question — **is item 2 an edit an agent may make alone?** The plan-drift rule says reconcile
  with the maintainer when the change is substantive. Rewriting a CLI signature in the canonical
  design to match the implementation looks substantive by definition, but the alternative reading is
  that the plan recorded a placeholder and the guard at `fuse/mount.py:125-127` settled the question
  in code. Whether that counts as substantive is the thing to settle before the cycle starts, not
  during it.
- 2026-11-11 (whole-tree review at `64faf27`): `PLAN.md` § *Risks and mitigations* → the namespace-replay
  bullet now runs to about sixty lines narrating six cycles, and `AGENTS.md` § 5 and § 10 restate the
  same mechanism at the grain of a docstring. Each engine cycle paid a review round to keep the three in
  step. [`issues/namespace-sync-redesign.md`](namespace-sync-redesign.md) R8 owns shrinking them if it is
  adopted. If it is declined, that residue is this seed's.
- 2026-11-11 (the maintainer's clarification, [`spec/local-bundle-loop/GOAL.md`](../spec/local-bundle-loop/GOAL.md)):
  [`issues/plan-the-bundle-loop.md`](plan-the-bundle-loop.md) redraws `PLAN.md` around the user story and
  absorbs most of the residue listed here. Shape this after it, from what it leaves.
- 2026-11-16 (`spec/plan-the-bundle-loop/`): the redraw rewrote or amended every section items 1 to 5
  cite, and met the draft criterion for each. Item by item:
  1. **Resolved.** § *Vision and scope* names the seven commands `uv run terrace --help` prints,
     `commit` and `audit` included, and the root `PLAN.md` holds no `fsck` (R1).
  2. **Resolved.** § *CLI surface*'s `umount` row takes a spec file and says the daemon's pidfile in
     the spec's state directory records the mounting host, which is what makes the cross-host guard
     possible (R2).
  3. **Resolved in `PLAN.md`; one part left here.** The Python-baseline row of § *Technology
     decisions* marks the four language features it names, `frozendict` included, as intent with no
     code behind them, and the root `PLAN.md` names no `rich` (R3). Whether `rich` leaves
     `pyproject.toml` stays with this seed: that cycle's GOAL forbade changing `pyproject.toml` or
     `uv.lock`. So does R7.
  4. **Resolved.** No forward `Phase N` pointer remains; the phase numbers left are dated records of
     past work (§ *Phase outline* dates each phase, and § *Risks* keeps "verified 2026-07-11, Phase 7
     start"). § *Phase outline* calls `ROADMAP.md` the ordered index of seeded cycles and names no
     `wip` branch (R5, R6).
  5. **Resolved.** Each of the four overstated bullets of § *Testing strategy* names its test path or
     is marked unbuilt with its seed (R4).

  What is left here: the `rich` question and R7, and, if `issues/namespace-sync-redesign.md` is
  declined, the residue of the 2026-11-11 note above. Its R8 still owns shortening the replay account,
  which the root `PLAN.md` keeps in § *Risks and mitigations* as the record of a mechanism Decisions 2
  and 3 retire.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the
  FUSE/CLI/layering brief (items 1-3), the verification-substrate brief (item 5), and the
  remaining-work brief (item 4).
