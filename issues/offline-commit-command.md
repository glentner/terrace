---
status: unshaped
kind: feature
appetite: big
lane: public
---

# `sosfs commit` — finalize the source offline

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

This is the feature the whole detach lifecycle was built for, and it does not exist. `PLAN.md`
§ *Deferred commit* opens with the motivating pattern: a GPU job stages and writes against fast
tiers, computation finishes, and the write-back clocks have not pushed the recent outputs upstream. A
full drain would finalize the source but burn expensive allocation time on data movement — worse when
the source is an S3 bucket or an archive that has to be re-packed. So the job ends with
`sosfs umount --detach spec.toml`, which rescues node-local dirty data one hop to a shared tier and
stops in seconds, and a later single-CPU job runs `sosfs commit spec.toml` to finalize the source off
the GPU bill.

The first half ships. `sosfs umount --detach` works, `rescue()` works, and `shutdown_state =
'detached'` is written to say metadata is trusted. The second half is `CommitApp(StubApp)`
(`src/sosfs/__init__.py:160-164`), which raises `RuntimeError('Not implemented: sosfs commit (see
ROADMAP.md, Phase 9)')`. The documented job-script pattern therefore terminates in a detach that
nothing can finalize, and the data sits on scratch indefinitely with the source stale. An operator
who follows `PLAN.md` today gets the cheap half of a two-command workflow.

The moves themselves are built and tested. `drain()` (`src/sosfs/engine/drain.py:74-109`) commits
namespace ops first and then dirty files; `dirty_copies()` (`drain.py:50-71`) computes the loss-risk
ordering — most volatile holder first, scope dominating position — and is shared with the rescue path
so the two cannot drift; `commit_upstream` (`src/sosfs/engine/overlay.py:811`) does the hop. What is
missing is everything around them:
- 2026-11-06: `overlay.py:811` above holds at `141aa86`; on the `deferred-rename-source-orphan` tree
  `commit_upstream` is at `src/sosfs/engine/overlay.py:867`.

* **The exclusive lock and the offline construction path.** `ExclusiveLock`
  (`src/sosfs/state/db.py:157`) records the holder as `host:pid` in the lockfile (`db.py:192`), and
  `Mount.start` (`src/sosfs/fuse/mount.py:212-223`) is the template for the lock → engine → work
  ordering with a compensating release. But `OverlayEngine.start` calls `lifecycle.begin_mount`
  unconditionally (`overlay.py:108`), which bumps the generation and sets
  `shutdown_state = 'mounted'`. A commit that used it would set the crash flag on an overlay that is
  merely being finalized, and a crash during the commit would be indistinguishable from a crashed
  mount. Commit needs the tier attach without the lifecycle transition — the same offline path
  `issues/audit-command.md` introduces.
  - 2026-11-06: `overlay.py:108` above holds at `141aa86`; on the `deferred-rename-source-orphan`
    tree the `begin_mount` call is at `src/sosfs/engine/overlay.py:117`.
* **The audit hand-off.** After a detach, commit proceeds directly: metadata is trusted, no walk.
  After a crash it runs the audit first, automatically. Neither branch can be written before the
  audit exists, and the branch condition is the `shutdown_state` value `fuse/mount.py` settles —
  which is itself unreliable today for a reason tracked in `issues/shutdown-rescue-gaps.md`, where a
  second SIGINT settles `detached` without having run the rescue.
* **The efficiency clauses.** `PLAN.md` promises four: policy clocks and rate limits do not apply;
  transfers run in parallel; a checksum-recorded file whose content already matches upstream is
  skipped; archive sources are finalized with one batched re-pack and one re-sync at the end. The
  first is already true by construction (the immediate `commit_upstream` path honors no clock). The
  other three are all unfunded, and each is a dependency on another cycle.
* **`--direct`.** Collapse the hops: copy the newest version of each file straight to the source and
  invalidate the stale intermediate copies, instead of cascading hop by hop and leaving the shared
  intermediate tiers warm. Neither the collapse nor the invalidation has an implementation.
* **The cross-host story.** The state directory must be on shared storage for commit to run
  elsewhere; `tier/spec.py:322-323` already refuses a state directory inside any managed tier root,
  which is stricter than `PLAN.md` asks and helps here. The `overlay` row records the mounting host
  (`state/lifecycle.py:103-105`), so a `scope = "node"` tier seen from another machine must read as
  *unreachable* rather than evaporated — the distinction matters because an audit or a mount back on
  the original host may still recover the data, so rolling it back to the last committed version has
  to require an explicit `--forfeit`. Nothing consumes the recorded host today.

One promise in `PLAN.md` is contradicted by the code as it stands rather than merely unimplemented.
"Transfers run in parallel" at deferred commit: `TransferPool` builds a `ThreadPoolExecutor`
(`src/sosfs/engine/transfer.py:62`) and exposes `submit` (`transfer.py:112-115`), and **`submit` has
no caller under `src/`** — the only reference is `tests/engine/test_transfer.py:113`. Every copy in
the system runs on the calling thread through `TransferPool.copy`, and `drain()` iterates
`dirty_copies()` calling `commit_upstream` one at a time. Commit is the one path where parallelism is
unambiguously safe and valuable — no mount, no FUSE callbacks, an exclusive lock, and a worklist known
up front — so this cycle either adopts the pool for it or `PLAN.md` stops promising it. Under the
plan-drift rule that decision is made in `PLAN.md` before the code.
- 2026-11-06: `transfer.py:112-115` above holds at `141aa86`; on the `deferred-rename-source-orphan`
  tree `submit` is at `src/sosfs/engine/transfer.py:122-125`, and it still has no caller under `src/`.

## Why it was deferred

Scheduled as Phase 10 and never reached; genuinely downstream of the audit, which supplies both the
auto-audit branch and the offline engine construction it needs. Unbuilt work, not a live defect —
with the qualification that the *documented* workflow is half-shipped, so a user who follows
`PLAN.md` today strands their data rather than getting an error at the point of the mistake.

The parallel-transfer contradiction and the dead `TransferPool.submit` were measured by the invariant
audit run while porting the software factory; the rest is ordinary sequencing.

## Outcome / vision

A batch job ends with a detach that takes seconds, and a later cheap allocation runs one command that
finalizes the source from any host that can see the shared tiers and the state directory. The command
refuses cleanly when a mount holds the lock, audits itself when the last shutdown was a crash, moves
data in loss-risk order, skips what already matches upstream, re-packs an archive source exactly
once, and reports by path anything it could not reach — naming the host that holds it.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHILE a mount daemon holds the state lock, `sosfs commit spec.toml` SHALL exit non-zero
  with `DatabaseLocked` naming the holder as `host:pid`, and SHALL move no data and write no row.
- **R2** — WHEN an overlay was detached with queued dirty state, `sosfs commit spec.toml` SHALL exit
  0, leave zero rows in `writeback`, leave every file's newest bytes present at the source, and settle
  `shutdown_state = 'clean'` — without performing an audit walk.
- **R3** — WHILE the recorded `shutdown_state` is `mounted`, `sosfs commit` SHALL run the audit first
  and say on stderr that it did, before moving any data.
- **R4** — WHEN `--direct` is passed, the source SHALL end byte-identical to what the default cascade
  produces from the same starting state, and no residency row for a skipped intermediate tier SHALL
  still claim a current copy.
- **R5** — IF a `scope = "node"` tier is unreachable from this host, THEN `sosfs commit` SHALL name
  the mounting host recorded in the `overlay` row, report each affected path, and exit 2 without
  rolling anything back; WHEN `--forfeit` is also passed, it SHALL roll those entries back to their
  last committed version and name each rollback by path.
- **R6** — WHEN an archive source is finalized, the whole commit SHALL perform one re-pack
  (direct-update mode) or one reconstruct-and-push (staged mode) regardless of how many files were
  committed, observable as a single change to the inner location's `signature()`. Depends on
  `issues/archive-finalization-integration.md`; against an S3 inner location this is only checkable
  against moto or MinIO.
- **R7** — WHERE `checksum = true` recorded a digest at commit time, a file whose recorded digest
  already matches the upstream object SHALL be skipped and counted as skipped in the report. Depends
  on checksum recording, which nothing implements today (`issues/invariant-audit-gaps.md`).
- **R8** — WHEN a commit is interrupted and re-run, it SHALL converge without duplicating a transfer
  or leaving an unresolved `intent` row, and a following `sosfs audit` SHALL exit 0.

## Notes

- **Blast radius.** Commit writes to the source, drops intermediate copies under `--direct`, and
  rolls entries back under `--forfeit`. It runs unattended in a job script against data whose only
  current copy may be on the tier it is reading from. Any CONFIRMED review finding in the commit path
  or in `engine/drain.py`, `engine/overlay.py`'s `_commit_hop` / `commit_upstream`, or
  `engine/transfer.py` forces the human sign-off gate.
- Related seeds: `issues/audit-command.md` is the hard prerequisite — the auto-audit branch and the
  offline engine construction both come from it. `issues/archive-finalization-integration.md` funds
  R6. `issues/invariant-audit-gaps.md` funds R7. `issues/shutdown-rescue-gaps.md` matters because
  commit's fast path trusts `shutdown_state = 'detached'` to mean the rescue ran, and today a second
  SIGINT settles that state without rescuing. `issues/crash-fault-injection-suite.md` owns the
  interrupted-commit cases in R8. `issues/control-channel-and-status.md` shares the offline read
  path.
- `PLAN.md` § *Deferred commit* is the specification, including the cross-host caveats and the
  `--direct` / `--forfeit` semantics; § *Lifecycle and recovery* → *Shutdown* defines the detach the
  command finalizes. The parallel-transfer promise lives in the same section and needs reconciling
  either way this cycle goes.
- **CLI wiring.** Adding a real `commit` touches four places: the `Application` subclass, `APP_HELP`
  (`src/sosfs/__init__.py:191`), the `commands` registry (`:219`), and `tests/test_cli.py`. Missing
  one is silent in one direction only — a command in `commands` but absent from `APP_HELP` fails
  `tests/test_cli.py:31`, while one added to `APP_HELP` and not to `commands` passes and fails at
  invocation. `tests/test_cli.py:42` parametrizes the stub list and must lose `commit` in the same
  commit. The FUSE and obstore imports stay function-local inside `run()` (`__init__.py:96-101` is the
  model) — commit runs on login nodes with no libfuse, and hoisting either import makes it
  unrunnable there.
- Open questions for shaping, each with its real trade-off:
  - Does commit adopt `TransferPool.submit` for parallel transfers, or does `PLAN.md` drop the
    promise? Adopting it makes `transfer.py` a live concurrent path for the first time and puts
    ordering pressure on the loss-risk sequence, which only means anything if transfers complete in
    roughly that order. Dropping it costs the headline benefit on an S3 or archive source, where
    latency dominates and serial transfer is the slow case the feature exists to avoid.
  - Is `commit` against `shutdown_state = 'clean'` a no-op that exits 0, or a refusal? The no-op
    confirms the operator's mental model and is safe to put in a job script unconditionally; the
    refusal catches the case where someone believes there is uncommitted data and there is not.
  - Does `--direct` invalidate intermediate copies by deleting them or by marking the residency
    stale? Deletion is simple and cold-starts the next mount. Marking keeps the tier warm and adds a
    residency state the audit has to understand — which means coordinating with
    `issues/audit-command.md` and possibly a `SCHEMA_VERSION` bump (`db.py:34`), for which there is no
    migration machinery.
  - Settled: what commit does about a queued rename whose source object an earlier commit orphaned.
    The `deferred-rename-source-orphan` cycle fixed that defect ahead of this one (landed 2026-11-06,
    `spec/deferred-rename-source-orphan/`), so commit no longer inherits it. An orphan an older engine
    already left on a source is the audit's (`issues/audit-command.md`).
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the
  remaining-work topic (dossier 5) for the command surface, and the state-invariants topic for the
  offline construction constraint and the checksum gap.
