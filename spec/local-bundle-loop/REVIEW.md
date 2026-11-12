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

## Review cycle 2 — changes-requested (2026-11-12)

- **Reviewed commit:** `ae420d1` (the one remediation commit for F1 and F2)  ·  **Base:** `main`
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3, mirroring `review.cycle` in `TECH.md`
- **Mode:** **scoped**, by the foreman's ruling: verifying the remediation of F1 and F2, plus the two
  sites the remediation changed beyond them (the `refuses()` probe in `tests/loop/test_helper.py` and
  the helper's signal handler in `tools/bundle_loop.py`). Not a fresh pass over the full diff. One
  fresh blind reviewer, given `GOAL.md`, the rubric, `AGENTS.md` by path and the view
  `git diff 14a2b56..ae420d1 -- . ':(exclude)spec/'`; the seed and `ROADMAP.md` hunks in that range
  were out of its scope. The subagent route ran; the sequential fallback did not. No `debate`, no
  completeness sub-pass.
- **Contract:** `GOAL.md` unchanged since cycle 1 (`git log` shows only the shape and the plan
  sign-off commits).
- **Triage of cycle 1's F2:** the foreman's call was **fix**; it was remediated in `ae420d1` and is
  verified below.

The reviewer's evidence lives in the worktree's gitignored `.local/review2/` (named `R2/` below).

### Verification run

- The profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`), one full run at
  `ae420d1` (`R2/verify.log`): lint "all checks passed", ruff, layering, SPDX on 85 files, version
  and symlink checks; the suite gave **773 passed, 9 xfailed in 30.91 s**, no failed, error, skipped
  or xpassed. This matches the remediation's claim. Not hollow, not aggregated: pytest ran after lint
  exited 0, and the counts were read from the log.
- F1, repeat under load (`R2/f1_drive.py`, `R2/f1-head.log`): 20 processes on a 10-core host, four
  exchanges at the tap (short chunked, 4 MiB chunked, chunked with `Expect: 100-continue`, 4 MiB
  with `Content-Length`), 10,000 each: **0 failures** (95 % upper bound 0.03 %, against cycle 1's
  ~4 %); 0 refused bodies in the bucket, 0 counted as forwarded. Negative control
  (`R2/f1-old.log`): the same drive with the tap's draining disabled fails 23–100 %, so the drive
  sees the defect. The two committed tap tests, 400 × 2 under 16-way load: 0 failures, bound 0.75 %
  (`R2/f1-pytest-repeat.log`). The tap's other early-close paths need a malformed request (bad
  `Content-Length`, unknown method, oversize line); none is reachable from the scenario.
- F2 (read against R4 and R6): stages 3 and 7 assert no write to the source; stage 5 asserts an empty
  window before the interval's expiry and exactly one `PUT` of the changed bundle's key after it;
  stage 8 asserts no read of a bundle key by the engine started after the commit; stage 9 asserts
  R4's byte-identity only. The diff removes exactly the two over-asks of cycle 1 and nothing R6 asks
  for.
- Helper signals (`R2/helper_drive.py`, `R2/helper-signals.log`): 122 trials, 0 failures across
  SIGINT, SIGTERM, SIGHUP, a second signal at 0–100 ms, 20-signal bursts, SIGINT started ignored,
  command exit 0 and 3, a long command killed, and a process-group Ctrl-C; each checked a fresh empty
  `TMPDIR` and a refusing endpoint afterwards. Default botocore retry mode, a signal during bucket
  setup: 230 trials, no hang, teardown complete (`R2/helper-startup.log`). Standard retry mode: A1.
- `refuses()` negative control (`R2/refuses_control.py`, `R2/refuses-control.log`): live listeners
  give 0 of 1,200 reported refused; a full backlog raises, never reports a refusal; closed and free
  ports give 2,200 of 2,200 refused.
- `AGENTS.md` figures (`R2/figures.log`, `R2/serial-unit.log`): 801 collected, 782 unit,
  19 integration (`-m integration`: 19 skipped, 782 deselected), 51 `.py` files under `tests/`,
  serial unit lane 64.3 s, `-n auto` 30.9 s. Each figure the commit changed equals the tree.
- `grep` for spec R/P/F ids in the changed source: no match.
- CI: **not observed**.

### Scoped matrix

| Item | Verdict | Verified how | Who |
|---|---|---|---|
| F1 (cycle 1, HIGH/CONFIRMED) | ✅ remediated | Load drive, 40,000 exchanges, 0 failures; negative control fails; no refused body forwarded | reviewer |
| F2 (cycle 1, MEDIUM/PLAUSIBLE, triaged *fix*) | ✅ remediated | Stages 3, 8, 9 read against R4 and R6; no R6 assertion lost | reviewer |
| Helper signal handler (`tools/bundle_loop.py`) | ❌ A1 | 122 trials clean on the named paths; hangs under standard retry mode | reviewer |
| `refuses()` probe (`tests/loop/test_helper.py`) | ✅ | Negative control: no live endpoint reported refused | reviewer |
| Full `verify` | ✅ | 773 passed, 9 xfailed, 30.91 s | reviewer |
| `AGENTS.md` figures | ✅ | Each changed figure measured equal | reviewer |
| R2 | ❌ (A1) | — | reviewer |
| R4, R6 | ✅ | — | reviewer |
| R8 bound | ✅ | Lane 30.9 s | reviewer |
| R9 | ✅ | — | reviewer |
| GOAL outcome: "`verify` reports no failure" | ✅ | One full run green; the F1 flake not reproduced under load | reviewer |

R1, R3, R5 and R7 were not re-graded in this scoped pass; cycle 1's grades stand for code the
remediation did not touch.

### Findings

#### A1 [MEDIUM/CONFIRMED] A signal during bucket setup can leave the helper unkillable but by SIGKILL, and SIGKILL leaks the directory

- **Where:** `tools/bundle_loop.py:228-232` (`on_signal`, which now calls `quiet()` before raising
  `Stop`), introduced by `ae420d1`.
- **Failure scenario:** a signal lands while boto3 is sending a setup request. botocore wraps the
  raised `Stop` in `HTTPClientError`; under standard retry mode (`AWS_RETRY_MODE=standard`, or
  `retry_mode` in the user's AWS config) that error is retried, so `Stop` is swallowed. Setup
  completes and the helper reaches `signal.pause()` with SIGINT, SIGTERM and SIGHUP all ignored. Only
  SIGKILL ends it, and that leaves the temporary directory behind: R2's interrupt and termination
  paths fail.
- **Evidence:** at head, 8 of 12 trials hung through two signals
  (`R2/helper-startup-retry-standard-head.log`); an earlier, stopped run's SIGKILLed trials left
  their `bundle-loop.*` directories (`R2/helper-startup-retry-standard-leftovers.log`; the reviewer
  removed them). Control at `14a2b56` (`R2/helper-startup-retry-standard-control-old.log`): the same
  8 of 12 swallowed the first signal, and each tore down cleanly on the second, status 0, nothing
  left. So the swallowing predates the cycle, and `ae420d1` turned it from "needs a second signal"
  into "cannot be stopped".
- **Touches:** R2. Not a § Invariants section; `tools/` is not on the high-blast-radius list. Edge
  path (non-default retry mode and a signal inside the setup window), hence MEDIUM.
- **Route (the property, both sides):** every interrupt or termination signal the helper receives,
  at any point from start to the end of teardown, ends in teardown and exit, whatever library code
  is on the stack when it lands (liveness: no layer may absorb the stop, and the first signal must
  suffice, under every retry mode botocore offers); and a signal that arrives once teardown has begun
  never cuts teardown short (completeness, the property `ae420d1` was protecting). Sweep the domain:
  every window in which the helper calls into boto3 or the store (setup, `close()`), under default
  and standard retry modes. Pin it with a drive that signals inside the setup window under standard
  retry mode and asserts teardown on the first signal, and keep a pin for the second-signal-during-
  teardown case.

#### Out of Scope

- Under the default retry mode, a signal in the setup window exits 1 with a traceback
  (`R2/helper-startup-stderr.log`), with teardown complete; it predates the cycle (`Stop` subclasses
  `Exception`). R2 asks for teardown, not an exit status. It sits in A1's window, so A1's sweep will
  meet it; no separate seed.
- The twelve `bundle-loop.*` directories in the session temp directory are cycle 1's (dated 01:16
  and 01:43); the reviewer's cycle 2 trials left none.

### Human-gate triggers

- None. A1 sits in `tools/bundle_loop.py`, which is not on § Invariants' high-blast-radius list, and
  violates no § Invariants section tagged *[human gate]*.
