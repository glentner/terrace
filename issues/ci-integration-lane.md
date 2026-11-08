---
status: unshaped
kind: ci
appetite: big
lane: public
---

# Run the integration suite somewhere

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

Ten tests execute on no automated machine. `.github/workflows/ci.yml` defines three jobs — `unit`
(Linux, matrix `3.15` and `3.15t`), `unit-macos`, and `checks` — and all three run either
`uv run pytest -n auto -m unit` or the ruff selection plus `uv build`. Nothing invokes
`-m integration`; nothing starts MinIO. Measured while auditing the verification substrate:
`grep -n 'integration\|minio\|docker' .github/workflows/ci.yml` returns nothing.

What sits behind the marker is the differentiating behaviour of the product. Seven tests in
`tests/fuse/test_integration.py` are the only place a real mount is exercised at all:
`test_posix_tooling_roundtrip:97`, `test_xattr_exposes_tier_residency:114`,
`test_metadata_propagates_to_source_after_flush:127`,
`test_chown_propagates_to_source_after_flush:141`, `test_drain_umount_finalizes_source:151`,
`test_detach_rescues_node_local_dirty:160` and `test_force_umount_skips_rescue:170`. Three in
`tests/tier/test_s3_integration.py` are the only real-S3 round trip:
`test_end_to_end_round_trip:95`, `test_promote_on_open_from_minio:112` and
`test_rename_and_unlink_commit_to_minio:130`. Drain, detach, force-umount, the xattr surface, and
metadata propagation to the source are all covered here and nowhere else — the whole lifecycle
`PLAN.md` presents as the reason the project exists.

The two reproduced data-loss defects the audit found live in exactly that region. One, a
commit-before-a-queued-rename orphaning the source object, was fixed by the
`deferred-rename-source-orphan` cycle (landed 2026-11-06, `spec/deferred-rename-source-orphan/`) with
engine-level coverage only; its GOAL sends the mount-level case here. The other,
`issues/shutdown-rescue-gaps.md`, is a second SIGINT settling `detached` without rescuing. The
tests nearest each of them are `test_rename_and_unlink_commit_to_minio` and
`test_detach_rescues_node_local_dirty`, and neither would have caught its defect even if it ran: the
rename test flushes between mutations, and `flush` runs namespace ops before data, which is precisely
what masks the orphan; the detach test drives `Shutdown.DETACH`, the SIGTERM path that does call
`rescue()`, not the second-SIGINT downgrade that skips it. So the lane is necessary and not
sufficient — it makes the region observable, and each defect still needs the case that pins it. The
rescue cycle owes its own; the orphan's mount-level case, which its cycle left at engine level, is
this cycle's (R7).

`PLAN.md:497` states that the FUSE integration tests run in "Linux CI". They run nowhere. That claim
is the reason a reader of the design believes this surface is covered.

**The obvious gate is a trap, and it was measured.** On a host with no `/dev/fuse` and no MinIO —
the maintainer's macOS machine — `uv run pytest -m integration -q` reports
`10 skipped, 449 deselected in 0.29s` and **exits 0**. A `verify:` gate written as the bare command
is signed off having proved nothing about mounts or S3. Any integration gate must assert a pass
count:

```
docker compose up -d --wait && uv run pytest -m integration -q 2>&1 | grep -qE '[0-9]+ passed'
```
- 2026-11-06: re-measured on macOS at `fe26962`, after `deferred-rename-source-orphan` added unit
  tests: `10 skipped, 474 deselected in 0.38s`, exit 0. The 449 above and in R2 matches the 449 unit
  tests at `141aa86` (that cycle's GOAL, R9).

The skips are decided at import time, in `pytestmark`, not per test. `tests/fuse/test_integration.py:30`
defines `_linux_fuse()`, which requires `platform.system() == 'Linux'`, an existing `/dev/fuse`, and an
importable `mfusepy`. `tests/tier/test_s3_integration.py:48` defines `_minio_available()`, which makes a
real boto3 `list_buckets()` call against `SOSFS_TEST_MINIO_ENDPOINT` (default `http://127.0.0.1:9000`)
with `connect_timeout=1`. Two consequences for whoever builds the lane. The predicate runs during
*collection*, so on a runner where the endpoint hangs rather than refuses, collection stalls a second
per attempt. And `docker-compose.yml` already publishes exactly those defaults — ports 9000/9001,
`minioadmin`/`minioadmin` — with a healthcheck, so `docker compose up -d --wait` blocks until the
predicate flips and no test file needs editing.

Nothing here is measured as *passing*. The audit measured the skip and the exit code; it did not run
the ten tests on a host that can. There is no automated record that they have ever passed together.

## Why it was deferred

Found by the invariant audit run while porting the software factory. That port was a harness change,
and adding a CI job that provisions `/dev/fuse` and a MinIO service is product infrastructure with its
own runner-cost and policy decisions, not something to slip into a documentation commit.

This is **unbuilt work**, not a live product defect — but it is a live *verification* defect, which is
why it sits above most of the feature cycles. Every statement anyone makes about the mount lifecycle
today is taken on trust. Recorded in `AGENTS.md` § *Invariants* § *Known open violations* as the
§ *Conventions* row, "The integration suite runs on no automated machine; CI has no integration job", and as
§ *Conventions*' "an `-m integration` gate exits 0 when nothing ran".

Placed after the two data-loss fixes rather than before them: the lane is worth most once it has
regressions to protect. The rescue fix should land carrying integration coverage that this cycle then
causes to run; the orphan fix landed without any, so its mount-level case is this cycle's (R7).

## Outcome / vision

A pull request that breaks drain, detach, force-umount, the residency xattrs, metadata propagation or
the S3 source round trip goes red before a human reads it. The lane reports the number of tests that
*ran and passed*, so a runner that lost `/dev/fuse` or a MinIO container that failed to come up is a
failure rather than a silent all-skip. `PLAN.md`'s "Linux CI" claim becomes true, and the factory's
integration gate idiom becomes a counted assertion documented once rather than rediscovered per cycle.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — When a pull request is opened, the CI workflow shall run `uv run pytest -m integration` on a
  Linux runner with `/dev/fuse` available and a reachable MinIO endpoint, and the job shall report at
  least 10 passed. Observable only on the runner; the maintainer's macOS host cannot check it.
- **R2** — If every integration test skipped, then the job shall fail. The gate asserts the pass count
  from the pytest summary line, not the process exit status, because `-m integration` exits 0 with
  `10 skipped, 449 deselected`.
- **R3** — The MinIO half shall be satisfied by the endpoint and credentials `docker-compose.yml`
  already publishes, with no edit to `tests/tier/test_s3_integration.py` and no new
  `SOSFS_TEST_MINIO_*` value invented by the workflow.
- **R4** — Where the FUSE half runs, `test_drain_umount_finalizes_source`,
  `test_detach_rescues_node_local_dirty` and `test_force_umount_skips_rescue` shall each report passed,
  and the job log shall carry the settled `shutdown_state` each asserts, so a reader can tell the
  lifecycle ran from the log alone.
- **R5** — `AGENTS.md` § *Invariants*, `AGENTS.md` § *Verification* and `PLAN.md` § *Testing
  strategy* shall state, in the same commit, which of the four verification layers CI now runs — so the
  next author does not have to read the workflow to learn whether "integration passes" is inferable
  from a green PR.
- **R6** — When the integration job fails, its output shall distinguish a test failure from an
  unavailable substrate (no `/dev/fuse`, MinIO unhealthy), because the two need different responses and
  the skip predicates make them look alike.
- **R7** *(added 2026-11-06, retiring `deferred-rename-source-orphan`)* — Where the FUSE half runs,
  the lane shall include a mount-level case that commits a file to the source while its rename is
  still queued, with no `flush` between the rename and the commit (for example `fsync` under
  `fsync_policy = "commit"`), and shall assert after the queue drains that the source holds the file
  only at its new path, with the committed bytes. That cycle's GOAL deferred this case here; its
  engine-level regressions are in `tests/engine/test_replay.py`.
- **R8** *(added 2026-11-08, retiring `source-path-truncate-and-stat`)* — Where the FUSE half runs,
  the lane shall include a mount-level case that calls `truncate(2)` on a path, with no open handle, for
  a source-served file whose rename is still queued, and shall assert that the call succeeds, that
  `stat` reports the cut size, and that after the queue drains the source holds the file only at its
  new path, at that size. That cycle's GOAL left mount-level coverage out of scope; its engine-level
  regressions are in `tests/engine/test_replay.py`.

## Notes

- Related: `spec/deferred-rename-source-orphan/` (landed; its mount-level case is R7 here),
  `spec/source-path-truncate-and-stat/` (landed; its mount-level case is R8 here) and
  `issues/shutdown-rescue-gaps.md` (its defect lives in the surface this lane covers, and it owes the
  case its own tests do not yet contain);
  `issues/archive-tier-test-coverage.md` and `issues/crash-fault-injection-suite.md` (both add tests
  that need a machine to run on); `issues/daemonized-mount-first-run.md` (the daemonized branch has no
  test anywhere, and a test for it likely needs a real mount); `issues/plan-md-reconciliation.md`
  (owns `PLAN.md`'s inaccuracies that no other cycle touches — this one's "Linux CI" claim is fixed
  here instead, in this cycle's own diff, per the same-commit rule). `PLAN.md` § *Testing strategy*
  (487–509); `AGENTS.md` § *Verification*; `AGENTS.md` § *Invariants* § *Conventions*.
- Open — **blocking or reporting.** A blocking lane is the only kind that prevents the regression it
  exists to catch, and it is also the one that stops all work when a MinIO image tag moves or a runner
  image drops `/dev/fuse`. A reporting lane never blocks a merge and is therefore ignorable, which is
  how a red integration job becomes permanent furniture.
- Open — **every PR or a schedule.** The `unit` job already carries a 25-minute timeout because 3.15t
  compiles obstore from source with a Rust toolchain; adding a fourth job with a service container and
  a mount is more minutes on every push. A nightly costs nothing per PR and finds the breakage a day
  after the author moved on.
- Open — **does the FUSE half need a privileged runner.** Unmeasured. The question is whether the
  default `ubuntu-latest` image gives `/dev/fuse` and a usable `fusermount` to an unprivileged job
  after installing libfuse, or whether the lane needs a privileged container or a self-hosted runner.
  This decides whether the lane is a fourth job or a project.
- Open — **one job or two.** Splitting FUSE and MinIO means a MinIO outage cannot mask a FUSE
  regression and the two halves can have different triggers; it also doubles the setup cost and the
  places a pass count has to be asserted.
- Found by: the invariant audit run during the software-factory port (2026-08-22), verification-substrate
  brief.
