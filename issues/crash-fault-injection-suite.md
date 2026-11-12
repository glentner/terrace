---
status: unshaped
kind: test
appetite: big
lane: public
---

# Fault injection around every crash point

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`PLAN.md` § *Testing strategy* states the obligation: fault-injection kill points "SIGKILL between
intent, act, and confirm of every mutation type; mid-drain; mid-rescue; mid-re-sync", each "followed
by audit + remount, asserting convergence and loss-report accuracy", plus tier-evaporation tests that
"wipe a staging tier wholesale, assert the audit attributes it to the marker and rolls back
correctly". That paragraph is the proof that the two sacred invariants — metadata before
acknowledgment, and intent → act → confirm — actually hold under the only conditions that matter. It
is the largest gap between the testing strategy `PLAN.md` describes and the one that exists.

What exists is `tests/state/test_survival.py`: 130 lines, three tests, one subprocess harness. The
shape is right and the scope is a fraction of what is promised.

- **Two kill points, both in the state layer.** `crash_after(db_path, 'committed')` kills the child
  after a batch of committed rows (`test_survival.py:82`, `:101`); `crash_after(db_path,
  'uncommitted')` kills it inside one open `BEGIN IMMEDIATE` (`:120`). Nothing else.
- **No tier is ever touched.** The child program (`test_survival.py:32-67`) writes catalog, journal,
  registry and `archive_index` rows and then `signal.pause()`s. It calls `journal.begin('commit',
  'project', path='/data/x.bin', temp_for='/data/x.bin', now=4.0)` — an intent whose act never
  happens and whose bytes never existed on any tier. So no kill point in the suite has a real byte
  move on either side of it, and the whole intent → act → confirm bracket is exercised only as a row
  in a table.
- **No audit, because there is none.** `test_crash_detected_and_settled_after_sigkill` performs the
  audit's job by hand: the comment at `test_survival.py:111` reads `# what the audit does: resolve
  intents, then settle the flag`, and the next three lines call `journal.rollback(intent)` and
  `lifecycle.end_mount('detached')`. The assertion the audit is supposed to make is written as test
  fixture code. `terrace audit` is a stub — `tests/test_cli.py:41-47` asserts `Not implemented: terrace
  audit` on stderr with `exit_status.runtime_error`.
- **No remount, no drain, no rescue, no re-sync, no tier evaporation.** None of the five scenarios
  `PLAN.md` enumerates by name has a test.

Two of the five mutation types in `journal.ACTIONS` (`src/terrace/state/journal.py:29` —
`{'promote', 'commit', 'evict', 'compact', 'resync'}`) have no producer in `src/` at all, so there is
no code path to kill between their intent and their act until the archive journaling lands.

This is unbuilt work, not a live defect: nothing regressed, the coverage was never written. The live
defect in the same region — a crashed overlay is unmountable, because `state/lifecycle.py:100-102`
raises `CrashDetected` and `fuse/mount.py` does not catch it — belongs to
[`issues/audit-command.md`](audit-command.md).

## Why it was deferred

Found by the invariant audit run while porting the software factory, which was a harness change and
not the place to write a subprocess crash suite. It is also genuinely blocked: "converged" is defined
as whatever `terrace audit` produces, and the audit does not exist. Writing convergence assertions
against a hand-rolled recovery routine, as `test_survival.py:111` does today, tests the test.

The scaffolding is the rabbit hole, and it lands in the highest-blast-radius code in the repository.
The injection point has to sit inside `Journal.begin`, `Journal.confirm` and `Journal.mark_dirty`
(`state/journal.py`) and inside `TransferPool.copy` (`engine/transfer.py:79-108`) — the four
functions where the two sacred invariants live. Anything that reorders statements to make a kill
point reachable breaks both invariants at once, and the failure is undetectable in review: the tests
still pass, and a `SIGKILL` in production leaves a clean residency over stale bytes. The design of the
injection mechanism deserves its own research phase before any test is written.

## Outcome / vision

Every mutation type has a kill point on each side of its act, and every one of them is followed by a
real `terrace audit` against real tier contents, then a remount, then an assertion about the source
tree and the loss report. A reviewer reading a drain change can point at a test that kills the process
in the middle of it. Tier evaporation is a case rather than a hypothesis: wipe `/dev/shm`, assert the
marker explains it, assert the loss report names what was on it and nothing else. The injection
mechanism is small enough to read in one sitting and provably inert when disabled.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHEN a child process is killed between `Journal.begin` and the act, for each of `promote`,
  `commit` and `evict`, `terrace audit` SHALL exit 1, leave zero rows in `intent`, and leave no file
  under `.terrace/partial/` on any tier.
- **R2** — WHEN a child process is killed between the act and `Journal.confirm`, the first
  `terrace audit` SHALL exit 1 and a second SHALL exit 0 reporting zero repairs, and every residency row
  SHALL agree with the bytes actually present on the tier it names.
- **R3** — WHEN a child is killed mid-drain, THEN after audit + remount + drain the source tree SHALL
  be byte-identical to the source produced by an uninterrupted drain from the same starting state.
- **R4** — WHEN a child is killed mid-rescue and the `scope = "node"` tier is then wiped, the loss
  report SHALL name every file that had not reached a shared tier, the reported count SHALL equal
  that number, and the audit SHALL exit 2.
- **R5** — WHEN a staging tier root is wiped wholesale between mounts, the audit SHALL attribute the
  loss to `MarkerStatus.MISSING` on one line naming the tier, and SHALL emit no per-file loss line for
  a copy that tier held cleanly.
- **R6** — WHILE an archive source is being re-synced, a kill between the recompress and the atomic
  push SHALL leave exactly one unresolved `intent` row with `action = 'resync'`, and the audit SHALL
  restart the re-sync from the extracted staging copy rather than from the source.
- **R7** — WITH injection compiled in and disabled, the existing metadata-before-acknowledgment and
  replay tests (`tests/engine/test_replay.py`, `tests/engine/test_model.py`,
  `tests/state/test_survival.py`) SHALL pass unchanged, and the statement order inside
  `Journal.begin`, `Journal.confirm` and `Journal.mark_dirty` SHALL be unchanged by the diff.
- **R8** — THE suite SHALL declare `pytestmark`, and SHALL either add under 60 s to
  `uv run pytest -n auto -m unit` or carry the `integration` marker — in which case its module
  docstring SHALL say so and any gate over it SHALL assert a pass count, because
  `uv run pytest -m integration` exits 0 when everything skipped.

## Notes

- Related: [`issues/audit-command.md`](audit-command.md) defines what "converged" means and is a hard
  prerequisite; [`issues/offline-commit-command.md`](offline-commit-command.md) owns the
  detach/commit scenarios in the same `PLAN.md` bullet list;
  [`issues/archive-journaling-and-locking.md`](archive-journaling-and-locking.md) and
  [`issues/archive-finalization-integration.md`](archive-finalization-integration.md) must land
  before R6 has a code path to kill; [`issues/shutdown-rescue-gaps.md`](shutdown-rescue-gaps.md)
  fixes the rescue path R4 exercises, and R4 is the regression guard that seed lacks;
  [`issues/ci-integration-lane.md`](ci-integration-lane.md) decides whether an `integration`-marked
  suite runs anywhere at all. Root `PLAN.md` → § *Testing strategy*, § *State store* (the two sacred
  invariants), § *Audit* (the five-step walk convergence is measured against).
- Open question — **the injection mechanism.** An environment variable read inside `Journal.begin`
  and `Journal.confirm` puts test scaffolding in production code that a reader of the two most
  load-bearing functions in the repository has to skip over, forever; monkeypatching inside a
  subprocess entry script costs production nothing but lets the injection point drift away from the
  real one, so the test eventually kills somewhere the code no longer is. A third option — a module-
  level hook object that is a no-op callable by default — splits the difference and is still a branch
  inside the transaction.
- Open question — **granularity.** Per-action kill points (five) are cheap and coarse; per-call-site
  kill points (every `journal.begin` in `engine/`) are exhaustive and turn the suite into dozens of
  process spawns. The middle ground is per-action with a call-site filter.
- Open question — **where the harness lives.** `crash_after` and the `CHILD` program are local to
  `tests/state/test_survival.py`. A shared harness that spawns a real engine over real tmpdir tiers is
  most of this cycle's work and becomes an interface three later cycles import, which puts it in the
  same category as `tests/engine/conftest.py`.
- Open question — **the marker.** Each case is a process spawn against a real tier tree. Inside
  `-m unit` the whole suite is 4.84 s serial today and every gate depends on that; behind
  `integration` the suite runs on no automated machine until the integration lane exists.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — verification-
  substrate review of `tests/` against `PLAN.md` § *Testing strategy*.
