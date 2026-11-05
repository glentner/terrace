---
status: unshaped
kind: fix
appetite: big
lane: public
---

# Nothing notices when a policy pump dies

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The two policy pumps — `WritebackScheduler` and `EvictionManager` — are `StateThread` subclasses, and
`StateThread.run` (`src/sosfs/core/fsm.py:74-83`) catches `Exception`, stores it in `self.exc`, logs
one line, and exits the loop. The exception surfaces only on `join`
(`src/sosfs/core/fsm.py:100-104`), and nothing joins until shutdown. `Mount._serve_after_start` polls
`fuse_thread.is_alive()` and nothing else (`src/sosfs/fuse/mount.py:339`), so the FUSE session is the
only liveness the process watches. When the unmount finally arrives, `Mount._stop_pumps` wraps
`pump.stop()` in `try/except Exception` and logs — deliberately, with a comment
(`src/sosfs/fuse/mount.py:226-235`).

Neither pump isolates a failure to the item that caused it. `WritebackScheduler.on_commit` iterates
`self._due` calling `self.engine.commit_row(row)` with no guard
(`src/sosfs/engine/writeback.py:112-118`); `EvictionManager.on_evict` calls `self.reclaim(spec)` the
same way (`src/sosfs/engine/evict.py:178-189`). One `OSError` out of a tier adapter kills the machine.
The reachable triggers are ordinary: `ENOSPC` or `EPERM` from `PosixTier.put` when the source
filesystem fills or a directory is not writable by the mount owner; a `sqlite3.OperationalError` when
`busy_timeout` expires under contention, for which there is no retry loop anywhere
(`src/sosfs/state/db.py:94-97`); any obstore error surfacing from an S3 source during a network
partition.

The resulting state is the dangerous part. The FUSE mount stays up. Reads and writes succeed. The
catalog keeps flipping copies to dirty and queueing write-back rows, and nothing drains them. Every
bound in the table below stops applying, because the code that enforces the bound is not running. The
sole evidence is one line — `sosfs-writeback died in COMMIT: …` at ERROR, which under the default
daemonized mount goes to `mount.log` inside the state directory, where nothing reads it. `sosfs
status`, the natural place to surface it, is a stub. A related gap sits one line away: the two
`.start()` calls at `src/sosfs/fuse/mount.py:222-223` are outside the compensating `except` that
releases the lock, so a pump that fails to start leaves the lock held, the pidfile on disk and
`shutdown_state = mounted`.

This is a **live defect on `main`**, latent rather than reproduced: the audit read the paths and
confirmed there is no supervisor, no restart, and no per-item error isolation, but did not kill a pump
under a running mount.

### The loss-window table

This is what the cycle is protecting. Every row is a window between a write being acknowledged and its
bytes reaching a durable upstream tier; paths in the third column are relative to `src/sosfs/`.

| Window | Bound | Bound lives at |
|---|---|---|
| Crash, dirty data on a stage tier | `delay`, or `min(delay, max_interval)` | `writeback.py:79-82` |
| Crash, `max_interval = None`, writes closer than `delay` | **unbounded** — never fires | `writeback.py:80-82` |
| Node loss after a SIGTERM detach | `drain_timeout` plus one whole file | `rescue.py:64`, `mount.py:256` |
| Node loss with `drain_timeout = 0` | **total** — still settles `detached` | `rescue.py:64`, `mount.py:256-257` |
| **Dead write-back pump** | **unbounded and unreported** | `fsm.py:81-83`, `mount.py:339` |
| Second SIGINT mid-drain | the remainder stays queued | `drain.py:98-105`, `mount.py:253-254` |
| Scan-to-commit skew | one commit pass; commits *early*, never late | `writeback.py:107-118` |

Rows three, four and six belong to `issues/shutdown-rescue-gaps.md`, not to this cycle. This cycle owns
rows two and five.

### `max_interval` defaults to `None`

`WritebackSpec.max_interval` is `Optional[float] = None` (`src/sosfs/tier/spec.py:90`) and
`WritebackScheduler.deadline` only applies the cap when it is set
(`src/sosfs/engine/writeback.py:80-82`). A file written faster than `delay` therefore never reaches its
deadline: each write pushes `last_write + delay` out again, forever. This is coded intentionally and
pinned as such by `tests/engine/test_policy.py:148-160`
(`test_delay_only_boundary_defers_under_constant_writes`, which asserts the residency stays `dirty`
through three writes 50 s apart under a 60 s delay and commits only once writing stops). It is the
single most-likely-unnoticed unbounded window in the system, because an unconfigured boundary silently
gets `WritebackSpec()` — delayed, 60 s, no cap — from the `or WritebackSpec()` in `boundary_policies`
(`src/sosfs/engine/writeback.py:35`). An operator who never wrote a `writeback` table has an unbounded
source-facing window and no way to see it. `PLAN.md` § *Risks and mitigations* tells that operator to
"tighten `delay`/`max_interval` … where the loss window matters", which presumes they know the window
exists.

### The eviction watermarks are documented as tunable and are not

`PLAN.md` § *Overlay engine* → *Eviction* (line 216) reads "high/low watermarks (default 95%/85%)". The
word *default* implies a setting. `DEFAULT_HIGH = 0.95` and `DEFAULT_LOW = 0.85` are module constants
(`src/sosfs/engine/evict.py:26-27`); the only override is the `high=`/`low=` keywords on
`EvictionManager.__init__` (`src/sosfs/engine/evict.py:69-70`), and neither construction site passes
them — `Mount.start` builds `EvictionManager(self.engine, tick=self.evict_tick)`
(`src/sosfs/fuse/mount.py:221`). There is no `watermarks` key in `TIER_KEYS`, `QUOTA_KEYS` or
`OVERLAY_KEYS` (`src/sosfs/tier/spec.py:27-30`), so an operator with a 64 GB `/dev/shm` tier and a
workload that writes in 8 GB bursts cannot lower the high mark to give eviction more headroom. The same
is true of the two `tick` values (`writeback_tick=1.0`, `evict_tick=5.0` at
`src/sosfs/fuse/mount.py:154`), which are constructor arguments with no configuration surface. This one
is **unbuilt work**, not a defect: the code does what it says, and the documentation says something
else.

## Why it was deferred

Found by the invariant audit run while porting the software factory into this repository
(2026-08-22). That port was a harness change; fixing product defects inside it would have put
engine-behaviour changes in a diff nobody was reviewing for engine behaviour.

Beyond scope, the dead-pump half needs a decision that does not exist yet: what a supervisor should
*do*. Restarting, failing the mount and degrading to read-only are three different promises to a
running job, and choosing among them changes what `PLAN.md` § *Lifecycle and recovery* claims. Under
the plan-drift rule that reconciliation comes first. The `max_interval` default is likewise a product
decision — changing `None` to a finite value changes the source write amplification of every existing
spec — and the watermark keys are a configuration-surface addition that should land with the tick
values rather than a second time.

Recorded in `AGENTS.md` § *Invariants* § *Known open violations*, the § 10 row: "Nothing monitors
pump liveness; a dead scheduler is unbounded, unreported loss."

## Outcome / vision

A pump that dies is a fact the operator learns about while it still matters, not one they infer from
missing data after the node is gone. The write-back scheduler survives a transient tier error the way a
mount survives a transient tier error — the row stays queued, the residency stays dirty, the next pass
tries again — and a boundary that is failing persistently says so by name. Every loss window in the
table above is either bounded by a number in the spec file or announced at mount time as unbounded, and
the two eviction watermarks `PLAN.md` calls defaults are keys an operator can set.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed at promotion. R2's shape is the open question in *Notes* — the criterion
below names one observable outcome so it is worth negotiating, not because that outcome is settled.

- **R1** — When a policy pump's state machine exits its loop on an exception, the mount SHALL observe
  the failure within one supervision interval, without waiting for `shutdown` — observable as a
  distinct line naming the pump and the exception, emitted while the mount is still serving rather
  than from `_stop_pumps`.
- **R2** — If the write-back scheduler is not running, then the overlay SHALL NOT keep acknowledging
  writes as though they were scheduled for commit: it reaches an operator-visible terminal condition
  (candidates: a non-zero daemon exit, `EIO` on write, a read-only degrade). Whichever is chosen, the
  post-condition is observable at the process or the syscall, not only in a log file. The syscall half
  can only be checked with a real mount on Linux with `/dev/fuse`.
- **R3** — If a single write-back row fails to commit because its destination tier raised, then the
  scheduler SHALL remain in its loop and retry the row on a later pass: after the failing `put`, the
  row is still present in `journal.queued()`, the residency is still `dirty`, and a subsequent `poll`
  with the tier restored returns 1 and lands the bytes at the source.
- **R4** — When a boundary has been failing to commit for a bounded number of consecutive passes, the
  scheduler SHALL report the boundary by name and the consecutive-failure count, rather than retrying
  silently forever.
- **R5** — When an overlay spec declares a `delayed` boundary with no `max_interval`, `sosfs mount`
  SHALL make the unbounded window explicit — either by applying a finite default (whose value is a
  shaping question) or by emitting a named warning identifying the boundary — so that no boundary is
  unbounded without the operator having been told.
- **R6** — Where an overlay spec sets eviction watermarks, the eviction manager SHALL use them; the
  startup line SHALL report the values in effect (`Eviction manager started (watermarks=90%/70%, …)`),
  and a spec with `low >= high` SHALL be rejected by `sosfs mount` with a non-zero exit and a message
  naming the tier, at parse time rather than at construction.
- **R7** — Where a mount is running, `sosfs status` SHALL report each pump as alive or dead. This
  depends on `issues/control-channel-and-status.md`; if that cycle has not landed, the criterion is
  out of scope rather than met by a log line.

## Notes

- Related: `issues/shutdown-rescue-gaps.md` owns rows three, four and six of the loss-window table and
  should not be re-derived here. `issues/control-channel-and-status.md` is the reporting surface R7
  needs. `issues/audit-command.md` is the other half of the recovery story — an unbounded window is
  survivable if something reconciles afterwards, and today nothing does.
  `issues/ci-integration-lane.md` gates whether R2's mount-level half runs anywhere automatically.
  `issues/plan-md-reconciliation.md` holds the residue of `PLAN.md` inaccuracies this cycle does not
  itself correct. `PLAN.md` § *Overlay engine* → *Eviction* (the 95%/85% claim), § *Overlay engine*
  → *Write-back*, § *Lifecycle and recovery*, and § *Risks and mitigations* (the volatile-tier
  bullet, which is the paragraph that assumes a live pump).
- Open question — **what a dead pump should do.** Restart with a bounded retry count keeps a long job
  alive through a transient outage but crash-loops against a genuinely broken source, burning I/O and
  filling the log. Failing the mount turns a five-second network hiccup into an unmount, which for a
  48-hour allocation is worse than the loss window it prevents. Degrading to read-only preserves the
  data already written and breaks every writer at once. The answer probably differs per pump: a dead
  eviction manager fills a tier and eventually returns `ENOSPC` to the application, which is loud; a
  dead write-back scheduler is silent, which is why it is the one that matters.
- Open question — **per-row isolation versus supervision.** R3 makes the scheduler nearly unkillable,
  which removes most of the urgency behind R1 and R2 but also converts a permanently broken boundary
  into an infinitely retried one. R4 exists to cover that, and the two together may be enough that a
  supervisor is not worth building. Decide whether this cycle ships isolation only, supervision only,
  or both.
- Open question — **the `max_interval` default.** A finite default (15 m is the value `PLAN.md`'s
  worked example uses) bounds the window for every existing spec without the operator acting, at the
  cost of changing the source write amplification of specs written against the current behaviour.
  Warn-only is cheaper and leaves the window open for anyone who does not read warnings. There is no
  third option that is both safe and silent.
- Open question — **where the watermark keys live.** Per-tier inside `quota = { … }` is what an
  operator wants for a tight `/dev/shm` next to a roomy `/scratch`; an overlay-level pair is one key
  and one default. Decide also whether `writeback_tick` and `evict_tick`
  (`src/sosfs/fuse/mount.py:154`) join this cycle — they have the same gap and would otherwise need a
  second configuration-surface change later.
- Out of scope by construction: `drain_timeout = 0` meaning *rescue nothing* rather than *no limit*
  is the same class of knob defect and belongs to `issues/shutdown-rescue-gaps.md`.
- Found by: the invariant audit run during the software-factory port (2026-08-22), engine-policy topic
  — FSM threads, write-back, eviction, drain and rescue.
