---
slug: local-bundle-loop
title: Drive the bundle loop end to end on a local object store
kind: test
appetite: small
status: in_progress
branch: test/local-bundle-loop
base: main
current_phase: P2
last_updated: '2026-11-12'
phases:
- id: P1
  name: Stand the loop up with one command
  status: done
  satisfies:
  - R1
  - R2
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && uv run python -I spec/local-bundle-loop/verify/check_outcomes.py p1
- id: P2
  name: Nine stages, the oracle and the tap
  status: pending
  satisfies:
  - R3
  - R4
  - R5
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && uv run python -I spec/local-bundle-loop/verify/check_outcomes.py p2
- id: P3
  name: The MinIO variant, the measured lane and the ledger
  status: pending
  satisfies:
  - R7
  - R8
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: true
  hill: uphill
  verify: tools/lint.sh && uv run python -I spec/local-bundle-loop/verify/check_outcomes.py p3
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — Drive the bundle loop end to end on a local object store

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/local-bundle-loop/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** none; lean path. Drafts are in [`draft/`](draft/), the gate in [`verify/`](verify/).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `code-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- `branch`, `base`: the cycle's branch (prefix per GOAL `kind`, from the profile's `branches`) and the
  profile's `base`.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `code-review`).
  **Artifact-deliverable R-IDs.** Some requirements are met by a *committed document* — a research
  audit, a protocol doc, an assessment — not by CLI-observable behaviour. Attach such an R-ID to the
  phase that produces and commits the artifact, and give that phase a content `verify:`
  (`test -f …`, a `grep -q` for the sections that must exist) rather than forcing a CLI drive.
  **Say so in the phase body**, because it changes who can grade it: `code-review` blinds its reviewer
  to all of `spec/`, so an R-ID whose evidence lives there is graded by the *orchestrator*, not the
  blind reviewer. Naming it in `TECH.md` is what lets the review skill route it correctly instead of
  reporting an unverifiable requirement.
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (docs, tests, isolated leaf
  modules). Any phase touching the profile's `coupled core` is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase (prefer driving the real CLI, not just tests —
  wrap CLI drives in the profile's `throwaway environment` helper, `<helper> sh -c "…"`, so they hit
  a disposable instance, never the developer's real data). A gate carrying `$( )` or quoting the
  calling shell would mangle is set with `set_phase.py --verify-file PATH`, not `--verify`.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`;
  REVIEW.md's "Cycle {n}" mirrors it and the ≤3-cycle bound is graded against it. `--verdict approved`
  also clears `review.blocked_reason` unless one is passed explicitly.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from the repo's `AGENTS.md` — it
  is the constitution. Walk the sections of its § Invariants that this change touches; that is the
  curated footgun checklist.
- One phase per `code-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. The branch commit subject is code-build's (`$KB/skill/code-build.md` →
  Step 6), not restated here; branch commits are squashed away at `code-publish` per the profile's
  `landing`. Trailers follow the profile's `branch commits`.
- The last phase carries the standing last-phase items: the deferral ledger (`code-plan` Step 6.8),
  and every item the previous cycle's last phase carried for a project-level obligation (mpsearch's
  `## Times` quote, taken as `code-build` Step 4 says). A plan that drops one says why in `PLAN.md`.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

## What every phase of this cycle shares

- **Names.** This cycle is built on the tree after the rename to Terrace: `src/terrace/`,
  `import terrace`, `TERRACE_*`. Nothing under `src/` changes in any phase. If a step seems to need
  a change there, stop and report it as a question.
- **The gate.** `uv run python -I spec/local-bundle-loop/verify/check_outcomes.py <mode>`, from the
  worktree root. Phase modes are sums of part modes (`rows-p1`, `rows-p2`, `rows-p3`, `probe`,
  `collect`, `variant`, `ledger`, `figures-p1`, `figures-p2`, `figures-p3`, `lane`), and each part runs alone
  for a fail-once mutation. The gate judges each test by id from pytest's JUnit report and never
  reads an exit code. `lane` runs the whole unit lane six times and takes about four minutes; give
  the command a ten-minute limit.
- **Row names are the contract with the gate.** The gate lists the tests it expects by name
  (`HELPER_ROWS`, `SUPPORT_ROWS`, `VARIANT_ROWS`, `STAGE_ROWS`). Write the tests under those names.
  More tests are welcome; a listed one that is missing, or anything in `tests/loop` that fails,
  errors or skips, is red.
- **Figures move with the change that moves them** (R9). Every phase adds test files, so every
  phase edits the counts in `AGENTS.md`: the two `uv run pytest` lines under § Commands, the
  `tests/` row of § Repository map, the "skipped, deselected" line in § Conventions, and the opening
  of § Verification. `figures-p<n>` compares each with the tree.
- **Drafts.** [`draft/stages.py`](draft/stages.py), [`draft/oracle.py`](draft/oracle.py) and
  [`draft/tap.py`](draft/tap.py) were dry-run at `bf438b4` under the old package name. Start from
  them. `PLAN.md` § 2 is what binds; the rest of a draft is yours to change.
- **Planning dry runs** used a prototype at `bf438b4` (old names; a copy of the gate with one import
  and the `TERRACE_TEST_MINIO_` prefix renamed). Before the design review: `p1` passed on a tree cut
  back to the helper and its tests; `rows-p2`, `probe` and `figures-p2` on one cut back to P1 and
  P2; `lane` and `tools/lint.sh` on the full tree. After the review's changes, on the full tree:
  `rows-p1`, `rows-p3`, `variant`, `collect`, `ledger` and `figures-p3` passed. The cut-back trees
  and `lane` ran only before the review, and every dry run was under the old names.

---

## Phase P1 — Stand the loop up with one command
**Satisfies:** R1, R2 · **Depends on:** —
**Goal:** `uv run python tools/bundle_loop.py` starts a local object store, fills a bucket with
generated bundles, writes the spec and the directories, prints the spec's path, and leaves nothing
behind on any exit path; its own tests prove each of those.

- [x] `tools/bundle_loop.py` as `PLAN.md` § 2.1 specifies: `Store`, `Loop` (`root`, `content`,
      `keys`, `spec`, `state`, `tiers`, `staging`, `form`, `resync_interval`, `delay`, `respec`,
      `close`), `main` with `-n`, `--fastest-size`, `--endpoint` and its credentials, and an
      optional command. SPDX header pair first. No import of `terrace` or `tests`.
- [x] Member bytes come from `random.Random` seeded by bundle and member, so an object is a few
      kilobytes after compression. Each bundle is one top-level directory named for its stem, six
      files in two directories, no empty directory.
- [x] The spec is the single-archive form for one bundle and the sharded form for several, key for
      key as `PLAN.md` § *Configuration* (the project's) documents them; `scratch` is shared scope,
      `tmp` and `shm` are node scope; every stage tier states a size limit, a count limit and a
      delay.
- [x] Teardown: `Loop.close()`; the constructor closes what it made if a later step raises; `main`
      stops the store in a `finally` that covers a failure while the loop is built; `SIGTERM` and
      `SIGHUP` are handled like an interrupt, and `SIGINT` gets a handler of its own so the helper
      stops when started with it ignored. All three are ignored once teardown begins. `close()` makes
      the root's directories writable before removing it and lets a failure to remove it propagate.
      Standard output carries the spec's path and the command's own output, nothing else
      (`ThreadedMotoServer(..., verbose=False)`).
- [x] `tests/loop/__init__.py` loads the helper by path and exports it. `tests/loop/conftest.py`
      has the session fixture `store`, which skips by name where `boto3` or `moto.server` cannot be
      imported.
- [x] `tests/loop/test_helper.py`, `pytestmark = mark.unit`, with the sixteen tests `HELPER_ROWS`
      names. Six run the helper in a subprocess and assert that the endpoint the spec names refuses
      a connection and that the spec's directory is gone: a command that exits 0, a command that
      exits 3 (and the helper exits 3), an interrupt, a termination signal, an interrupt when the
      helper was started with it ignored, and a second signal 5 ms after the first.
      Each such test waits for the process and kills it in a `finally`. The bundle test unpacks
      each object with `tarfile` itself; the oracle arrives in P2.
- [x] `tools/lint.sh`: add `tools` to the ruff paths and to the SPDX loop, and to the two notes that
      name the directories. `.github/workflows/ci.yml`: add `tools` to the ruff step (`PLAN.md`
      deviation row 2; drop this item if the sign-off declines it).
- [x] `AGENTS.md`: a line for the helper under § Commands; the `tools/` row of § Repository map
      names it; the counts move (see above).
- [x] Fail-once, each seen red on `rows-p1` and reverted: M7 (the constructor stops closing what it
      made), M8 (`main` stops handling `SIGTERM`) and M12 (`close` ignores a directory it cannot
      remove).
- **Amended at build (2026-11-12).** The helper carries no shebang and no exec bit: `tools/lint.sh`'s
  SPDX check requires the header pair on lines 1 and 2, and a shebang pushed it to 2 and 3 (the plan
  never dry-ran lint with `tools` in its paths). It runs as `uv run python tools/bundle_loop.py`.
  `.github/workflows/ci.yml` has no SPDX step of its own, so only its ruff step gains `tools`.
  `test_helper.py` skips by name, as `store` does, where `boto3` or `moto.server` is missing. The
  M12 test's stand-in `rmtree` honours `ignore_errors` as the real one does, so a `close` that passes
  `ignore_errors=True` is caught as well as one that swallows the error; both were planted and red.
  `AGENTS.md`'s SPDX file count (§ Code style) moves with the tree too.
- **Verify:** `tools/lint.sh && uv run python -I spec/local-bundle-loop/verify/check_outcomes.py p1`
- **Touches:** `tools/bundle_loop.py`, `tests/loop/__init__.py`, `tests/loop/conftest.py`,
  `tests/loop/test_helper.py`, `tools/lint.sh`, `.github/workflows/ci.yml`, `AGENTS.md`.

## Phase P2 — Nine stages, the oracle and the tap
**Satisfies:** R3, R4, R5, R6 · **Depends on:** P1
**Goal:** `uv run pytest -n auto -m unit tests/loop` reports nine expected failures, one per stage,
each naming its owner, and each recorded in the report as stopped by the spec parser's refusal and
by nothing else; the oracle, the tap, `blocked` and the markers each have passing tests that fail
when they are weakened.

- [ ] `tests/loop/oracle.py` from the draft: `tree`, `unpacked`, `difference`. Standard library
      only. `unpacked` raises on a member held twice and reads past an end-of-archive marker.
- [ ] `tests/loop/tap.py` from the draft: `Tap`, `Request`, `Window`. Every method other than `GET`
      and `HEAD` is a write. A chunked request is refused with 501 and fails the window it fell in. A
      window is read only after its block ends.
- [ ] `tests/loop/stages.py` from the draft: `Blocked`, `blocked`, `owned`, `STAGES`, the two pinned
      parser texts, the shared steps and the nine stages of `PLAN.md` § 2.4. `Blocked` is raised in
      `blocked` and nowhere else, and `blocked` pins a refusal only (`PLAN.md` § 2.3). Stage 8 runs `python -m terrace commit` with `TERRACE_*` removed
      from the environment and the XDG paths pointed into `temp_site`.
- [ ] `tests/loop/conftest.py`: `clock` and `scene`. `scene` is a factory: a tap in front of the
      store, a `Loop` built through the tap, a `fetch` that reads the store directly, and a check of
      every bundle's object against the oracle before the scene is returned. A scene refuses a spec
      whose endpoint is not the tap's. It registers assertion rewriting for `tests.loop.stages` and
      `tests.loop.oracle` before importing them.
- [ ] `tests/loop/conftest.py`: the `pytest_runtest_makereport` hook of `PLAN.md` § 2.3. For an item
      named in `STAGES` it appends `marker` at set-up, and `stopped` and `cause` when set-up or the
      call raised, to `item.user_properties` (not to the report: the JUnit plugin reads the item at
      teardown). The gate pins all three per stage.
- [ ] `tests/loop/test_scenario.py`: `pytestmark = mark.unit` and the nine stage functions,
      imported.
- [ ] `tests/loop/test_support.py`, `pytestmark = mark.unit`, with the twenty-four tests `SUPPORT_ROWS`
      names: seven for `blocked` (one reads `stages.py`'s syntax tree), the marker audit, the unit
      module's nine, ten for the oracle (one reads its imports), five for the tap and the scene. The tap's tests go through `obstore`, the
      client the engine uses.
- [ ] `AGENTS.md`: the `tests/` row of § Repository map and § Verification describe the scenario:
      what `tests/loop/test_scenario.py` is, that its nine expected failures are the loop's progress
      record, and the rule for a cycle that moves the block (remove the marker of a stage that
      passes; pin the new stop of one that does not). State "9 expected failures" in those words.
      The counts move.
- [ ] Mutations, each planted, seen red where `PLAN.md` § 6 says, and reverted: M1 to M6, M10 and
      M11.
- **Verify:** `tools/lint.sh && uv run python -I spec/local-bundle-loop/verify/check_outcomes.py p2`
- **Touches:** `tests/loop/oracle.py`, `tests/loop/tap.py`, `tests/loop/stages.py`,
  `tests/loop/conftest.py`, `tests/loop/test_scenario.py`, `tests/loop/test_support.py`, `AGENTS.md`.

## Phase P3 — The MinIO variant, the measured lane and the ledger
**Satisfies:** R7, R8, R9 · **Depends on:** P2
**Goal:** the same nine stages are collected under the integration marker against the endpoint the
`TERRACE_TEST_MINIO_*` variables name; the unit lane's time and counts before and after are
measured and recorded; `AGENTS.md` describes all of it with figures that match the tree.

R8 and R9 are met in part by what is committed here and in `AGENTS.md`, not by behaviour a blind
reviewer can drive: the lane figures below are graded by the orchestrator against the gate's log.

- [ ] `tests/loop/test_scenario_minio.py`: `pytestmark = [mark.integration, mark.skipif(...)]`, with
      the endpoint, credentials, region, defaults and skip reason of
      `tests/tier/test_s3_integration.py`; the same nine function objects imported from
      `tests/loop/stages.py`; and a `scene` fixture over that endpoint.
- [ ] `tests/loop/test_support.py`: add `test_the_minio_module_collects_the_same_stages`.
- [ ] `variant` passes: the module's wiring over the helper's own store, nine expected failures with
      the same three properties as the unit module. Say nowhere that this is a run of the variant;
      it is a run of its fixtures against moto.
- [ ] `issues/ci-integration-lane.md`: a dated note. `tests/loop/test_scenario_minio.py` is written
      and collected and has never run, on any machine; the lane that first runs it also learns
      whether the tap works in front of a store that checks request signatures.
- [ ] `AGENTS.md`: the integration count under § Commands, in § Repository map, in § Conventions and
      in the sentence on the tests no automated machine runs; a statement that the bundle loop's MinIO
      variant has not run on any machine, with `issues/ci-integration-lane.md` named as the seed that
      owns running it; the lane's measured time wherever the file states one.
- [ ] Run the phase verify. Copy its three `lane` lines here, under **Measured**, and say they are
      from the P3 verify run. Then run `figures-p3` and `ledger` again, since both read files this
      step edits. Run `uv run pytest -m unit` once for the serial time `AGENTS.md` states.
- [ ] The deferral ledger. Walk P1, P2 and P3 above for every "do not fix here", "known
      limitation", "follow-up", "left as" or "not run", and confirm each names its
      `issues/{slug}.md` and that `ROADMAP.md` indexes it. This cycle has one: the MinIO variant is
      not run in this cycle, and the machine that runs it is
      [`issues/ci-integration-lane.md`](../../issues/ci-integration-lane.md)'s to provide. An
      unrecorded deferral fails the phase.
- [ ] Fail-once: M9 (one stage deleted from `test_scenario_minio.py`), seen red on `collect`.
- **Measured:** to be filled from the P3 verify run. Planning's one `lane` run, on the prototype at
  `bf438b4` before the design review added ten tests: without `tests/loop` 37.3 s (median of 45.9,
  37.3, 32.8), 731 passed; with it 37.4 s (median of 37.4, 39.7, 31.2), 762 passed and 9 expected
  failures; added 0.1 s.
- **Verify:** `tools/lint.sh && uv run python -I spec/local-bundle-loop/verify/check_outcomes.py p3`
- **Touches:** `tests/loop/test_scenario_minio.py`, `tests/loop/test_support.py`,
  `issues/ci-integration-lane.md`, `AGENTS.md`, `spec/local-bundle-loop/TECH.md`.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[{category}]` commit; stop and report.
