# REVIEW — Drive the bundle loop end to end on a local object store

> Adversarial QA by `code-review`, run in an isolated context (a fresh reviewer subagent). The
> correctness pass grades the branch diff against [`GOAL.md`](GOAL.md) and the repo's `AGENTS.md`
> § Invariants only; it does not see `PLAN.md` or `TECH.md`. Every finding cites an executed
> command, not an assertion.

- **Reviewed commit:** see `review.last_reviewed_commit` in `TECH.md` (the head after this review's
  seed commit; the code graded is `14a2b56`)  ·  **Base:** `main`  ·  **Date:** 2026-11-12
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3, mirroring `review.cycle` in `TECH.md`
- **Mode:** a fresh blind pass over the full spec-excluded diff (`git diff origin/main...HEAD -- .
  ':(exclude)spec/'`, 15 files, +1673/−18), with one reviewer. No `debate`, no completeness sub-pass.
  The subagent route ran; the sequential fallback did not.
- **Contract:** `GOAL.md` as it stands at head, including commit `213fd9c`, two clarifications dated
  2026-11-12 made at the plan sign-off and confirmed by the foreman before grading.

The reviewer's evidence lives in the worktree's gitignored `.local/review1/` (named `R/` below).

## Verification run

- `tools/lint.sh && uv run pytest -n auto -m unit` (the profile's `verify`), one full run at
  `14a2b56` (`R/verify.log`): lint passed all checks, including "SPDX header pair on all 85 .py
  files under src/, tests/ and tools/"; the suite gave **772 passed, 9 xfailed in 31.35 s**, with no
  failed, error, skipped or xpassed. This matches what the code implies: 731 before, plus 50 new
  unit tests, of which 41 pass and 9 are the stages.
- The unit lane with and without `tests/loop`, three runs each (`R/lane_timing.log`,
  `R/lane_with_{1,2,3}.log`, `R/lane_without_{1,2,3}.log`): with, 32.84 s, 31.11 s, 32.86 s; without,
  31.15 s, 29.57 s, 34.00 s. About 0.7 s added on means, within run-to-run noise of about ±2 s.
  **Run 2 with `tests/loop` was red: `1 failed, 771 passed, 9 xfailed`** (F1).
- `uv run pytest -m integration --collect-only` (`R/collect_integration.log`): the nine
  `test_scenario_minio` items are listed, 19 of 800 collected. With no endpoint the module reports
  `9 skipped` (`R/minio_skip.log`); its fixtures over the helper's moto store, standing in for MinIO,
  give `9 xfailed`, all stopped at `SpecError` (`R/minio_on_moto.log`). That is not a run against
  MinIO, and the variant was not run.
- Helper drives, as a person at a terminal (`R/drive_helper.log`, `R/drive_orphans.log`,
  `R/drive_uv.log`): the spec path is the first line of standard output; on Ctrl-C to the process
  group, SIGTERM, SIGHUP, a command exiting 0 and 7, a bad command, `-n 0`, and under `uv run` with
  SIGINT and SIGTERM, the root directory was gone and the endpoint refused a connection afterwards.
  The process search after the drives could not use `ps` or `pgrep`, which the sandbox refuses
  (`R/process_search.log`); it ran `kill(pid, 0)` on every pid the drives started instead:
  `final search: any marked pid alive? []`.
- Oracle (`R/probe_oracle.log`): importing `tests.loop.oracle` loads no `terrace` module; it rejects
  a changed byte, a missing member, an extra member, a renamed member, an extra empty directory, a
  member held twice and an appended second archive.
- Tap (`R/probe_engine_tap.log`): the real engine through the tap shows list, stat, read and write
  traffic; pointed past the tap it records zero requests, and the scene refuses a spec that does not
  name the tap (`test_scene_refuses_a_spec_that_bypasses_the_tap`).
- Mutants planted by the reviewer (`R/mutants_summary.log`, `R/mutants2_summary.log`, per-mutant logs
  in `R/mutants/`): 35 killed; S3, S4, S10b, C2 and C4+S6 survived (coverage observations below).
  Every in-place mutant was restored byte for byte and the restore asserted.
- `grep` for spec R/P ids under `tests/loop/` and `tools/`: no match (exit 1).
- CI: **not observed** (this step has no CI access; `code-publish` reads the rollup).

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Who | Status |
|------|----------------|--------------|-----|--------|
| R1 | `tools/bundle_loop.py`; `tests/loop/__init__.py`, `conftest.py` build every scene through it | `R/drive_helper.log`: spec path on stdout; single form with `staging` and `sync`, sharded form with `sharded = true` and `*.tar.gz`, as `PLAN.md` § Configuration documents; scratch (shared), tmp and shm (node); bundles 2–16 KB; mutants H4, H5 killed | reviewer | ✅ |
| R2 | `tools/bundle_loop.py` | Every exit path above ends with the root gone and the endpoint refusing; mutants H1, H2, H3, H6 killed; no marked pid alive | reviewer | ✅ |
| R3 | `tests/loop/oracle.py` | `R/probe_oracle.log` (no `terrace` import; seven wrong archives rejected); mutants O1–O5 killed | reviewer | ✅ |
| R4 | `tests/loop/stages.py`, `test_scenario.py` | `R/stages.xml`: nine stages, each driving the engine API in process over the helper's spec; stages 1–8 use one bundle, stage 9 three | reviewer | ✅ (see F2 for stages 3 and 8) |
| R5 | `stages.py` (`owned`, `blocked`), `conftest.py` | Every marker `xfail(raises=Blocked, strict=True)`, reason names the owner of R4's table; all nine seeds on `origin/main` (`git ls-tree`); stage 1 stops at `Unknown key(s) in tier 'bundles': location, staging, sync`; mutants S1, S2, S5–S10, S10c, S11–S13, S16–S18 killed | reviewer | ✅ |
| R6 | `tests/loop/tap.py`, `stages.py` | Stage 5 counts two windows (none before expiry, exactly one `PUT` of the bundle key after); stages 3 and 7 assert no write; stage 8 no read of a bundle key; mutants T1–T5, C3 killed | reviewer | ✅ (see F2) |
| R7 collection, marking, defaults, skip | `tests/loop/test_scenario_minio.py` | `R/collect_integration.log`, `R/minio_skip.log`, `R/minio_on_moto.log`; defaults and skip identical to `tests/tier/test_s3_integration.py`; nothing in the graded diff claims a run | reviewer | ✅ |
| R7 record ("the variant was not run") | `TECH.md` P3 (*Amended at build*), with `issues/ci-integration-lane.md` and `AGENTS.md` § Verification | Read: TECH.md P3 states the module "has not run against MinIO on any machine", and that the `variant` gate ran its fixtures against moto, not MinIO | orchestrator | ✅ |
| R8 bound | `tests/loop/` | Reviewer's own lane measurement: about 0.7 s added, budget about 10 s | reviewer | ✅ |
| R8 record (time and counts before and after) | `TECH.md` P3 *Measured* | Read: before 30.6 s, `{'passed': 731}`; after 31.7 s, `{'passed': 772, 'xfailed': 9}`; added 1.0 s. Consistent with the reviewer's independent run (731 → 772 passed + 9 xfailed, about 0.7 s added). No gate log is committed; the record was checked against the independent measurement, not against the build's log | orchestrator | ✅ |
| R9 | `AGENTS.md` (Commands, Repository map, § Verification) | Each build commit moved its own figures (747 → 780 → 781 unit tests; 45, 50, 51 files; 79, 84, 85 `.py` files; 19 integration tests); collection agrees: 800 total, 781 unit | reviewer | ✅ |
| Clarification 2026-11-12, stage 1's refusal | `stages.py` `REFUSAL` | `R/stages.xml`: the unknown-key message naming the archive keys | reviewer | ✅ |
| Clarification 2026-11-12, no push before the interval | `stages.py:346-352` | The two windows of stage 5 | reviewer | ✅ |
| GOAL outcome: "`verify` reports no failure" | — | `R/lane_with_2.log`: one failure on a clean tree | reviewer | ❌ (F1) |

Unmapped changes (minor, supporting the helper, none harmful): `ruff` and the SPDX check extended to
`tools/` in `tools/lint.sh` and `.github/workflows/ci.yml`; helper options beyond the GOAL
(`--endpoint`, `--access-key`, `--secret-key`, `--region`, SIGHUP handling, the
`BUNDLE_LOOP_ENDPOINT` and `BUNDLE_LOOP_BUCKET` variables); the seed's status set to
`adopted:local-bundle-loop`.

## Findings

### F1 [HIGH/CONFIRMED] The profile's `verify` fails at random on a clean tree: the tap's chunked-body refusal races the client

- **Where:** `tests/loop/tap.py:98-103` and `tests/loop/test_support.py:243-253`
  (`test_tap_refuses_a_chunked_body_and_fails_the_window`).
- **Failure scenario:** the tap answers a chunked request with `send_error(501)` and closes the
  connection without reading the body. The test sends its body as a two-chunk iterator. When the tap
  closes the socket before the client writes the second chunk, the client raises `BrokenPipeError`
  instead of reading the 501, and the test fails.
- **Evidence:** run 2 of `uv run pytest -n auto -m unit` with `tests/loop`: `1 failed, 771 passed,
  9 xfailed`, `BrokenPipeError: [Errno 32] Broken pipe` raised through `test_support.py:249`
  (`R/lane_with_2.log`), one red run in four lane runs that included the loop tests.
  `R/probe_chunked_flake.py` repeats the test's client steps against a `Tap`
  (`R/probe_chunked_flake.log`): `N=300 busy_threads=0: {'status 501': 299, 'BrokenPipeError': 1}`;
  `N=300 busy_threads=4: {'status 501': 294, 'BrokenPipeError': 6}`.
- **Touches:** the GOAL's outcome ("the profile's `verify` reports no failure"), and through it the
  landing gate and CI on every run. Not a § Invariants section; no human-gate path.
- **Route (the property, both sides):** every test under `tests/loop/` has an outcome that does not
  depend on thread scheduling or socket timing. On the completeness side, the tap still refuses and
  records every body it cannot replay, never forwards it, and the window still fails on a refusal;
  on the liveness side, the client observes that refusal as a definite outcome on every run. Sweep
  the domain, not the instance: every socket-level exchange in `tests/loop/` (the tap's other error
  paths, the helper tests' refused-connection and teardown checks) for the same race. Pin it with a
  repetition that would have caught this one (hundreds of iterations, under load), and run the lane
  enough times to show it green.

### F2 [MEDIUM/PLAUSIBLE] Stages 3 and 8 assert a source request the archive tier, as built, would not make

- **Where:** `tests/loop/stages.py:319` (`assert seen.of('read'), 'the engine read nothing from the
  source through the tap'`) and `tests/loop/stages.py:411` (`assert seen.requests, ...`). R6 asks for
  neither.
- **Failure scenario:** `OverlayEngine.start` calls `source.stat('/')` (`src/terrace/engine/overlay.py:148-149`);
  on the existing `ArchiveTier` that runs `index()` and `_populate()`, so the whole bundle is pulled
  at engine start, before stage 3's window opens, and member reads are then local. Once the archive
  tier is plumbed in as built, stage 3 would fail at `:319` for a reason unrelated to its owner, and
  stage 8 at `:411` even when R6's property ("pulls no bundle") holds.
- **Evidence:** `R/probe_archive_tap.py` (`R/probe_archive_tap.log`), a staged tar.gz over S3 through
  the scenario's tap: `at stat(/): [HEAD …bundle-000.tar.gz, GET …bundle-000.tar.gz]`, `member reads
  after index: []`, `fresh tier, staging present: []`. Not reproduced through the engine, which has no
  archive branch on this tree; the failure is in a future tree, and it is loud (an `AssertionError`
  outside `Blocked` fails the run), not a green that lies.
- **Touches:** R4 stages 3 and 8, R6. **Awaiting the foreman's triage.** It cites R6's wording, so
  the call is *fix* or *drop*, never *defer*.

### Coverage observations (not CONFIRMED)

- **C-a. Stage code past the block never runs today.** Mutant S3 (`stage.loop.keys` → `keyz` after
  `reach` in stage 5) survives (`41 passed, 9 xfailed`); S4 (an undefined name after the block)
  survives pytest and is killed by `tools/lint.sh` (`F821`). Structural to a scenario whose stages
  all stop at step one. Its home already exists: `AGENTS.md` § Verification requires the cycle that
  moves the block to run every stage behind it to its next stop. No seed.
- **C-b. The no-self-excuse audit covers `stages.py` only.** `test_blocked_is_raised_in_one_place`
  parses `stages.py` alone. `pytest.xfail(...)` planted in the `conftest.py` scene factory survives as
  `36 passed, 14 xfailed` (C2); a `pytest_collection_modifyitems` hook in `conftest.py` that prepends
  `xfail(raises=Exception)` absorbs a drifted refusal with identical counts, `41 passed, 9 xfailed`,
  visible only in the JUnit `marker` property (C4+S6); `getattr(pytest, 'xfail')()` inside a stage
  survives (S10b), while the plain `pytest.xfail('x')` is killed (S10c). Defence in depth against a
  future edit; it breaks no R-ID check on this tree. Seeded as
  [`issues/loop-excuse-audit-scope.md`](../../issues/loop-excuse-audit-scope.md), indexed in
  `ROADMAP.md`.

### Out of Scope

- The sandbox refuses `ps` and `pgrep`, so the marked process search ran as `kill(pid, 0)` over the
  drives' pids. A host limit, not a defect of the diff.
- Leftover `bundle-loop.*` directories in the session temp directory: nine dated 01:43 were leaked by
  the reviewer's own teardown-disabling mutants H1 and H2 (not by the tree); three dated 01:16
  predate head and are not from this tree's clean runs (12 before and 12 after six clean lane runs).
  `del` refused them with "Operation not permitted"; they remain.

## Human-gate triggers

- None. F1 sits in `tests/loop/`, which is not on § Invariants' high-blast-radius list, and violates
  no § Invariants section tagged *[human gate]*.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (the orchestrator's ruling for this pass).
