---
status: unshaped
kind: fix
appetite: big
lane: public
---

# A second SIGINT settles `detached` without rescuing

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`PLAN.md`'s Shutdown table (line 287) says a second SIGINT mid-drain downgrades the drain to a
detach, and a detach is defined one row above as a mode in which "dirty data on `scope = "node"`
tiers is first *rescued* one hop to the nearest shared durable tier" (`PLAN.md:286`). The code
downgrades and does not rescue. `Mount.shutdown` is the whole decision:

```python
if mode is Shutdown.DRAIN:
    result = drain(self.engine, cancel=lambda: self._sigint >= 2)
    state = 'clean' if result.complete else 'detached'
elif mode is Shutdown.DETACH:
    rescue(self.engine, budget=self.spec.drain_timeout, clock=self.engine.clock)
    state = 'detached'
else:
    state = 'detached'                  # FORCE: skip the rescue; metadata stays consistent
```

`src/sosfs/fuse/mount.py:252-259`. `rescue()` appears only in the `DETACH` branch. A cancelled drain
falls straight through to `engine.stop('detached')` with whatever was still queued left exactly
where it was — on `/tmp`, on `/dev/shm`, on whatever node-scope tier the scheduler had not yet
reached.

**Losing the data is not the worst part; the state string is.** `shutdown_state` is a three-valued
dirty bit, and `detached` is a promise: `PLAN.md:286` — "Metadata is consistent — no audit needed" —
and `PLAN.md:319` — after a detach `sosfs commit` "proceeds directly; metadata is trusted, no audit
walk". Settling `detached` therefore instructs every later recovery step *not* to go looking. When
the allocation ends and the node is reclaimed, the dirty copies evaporate and nothing anywhere
records that they existed. Compare the honest failure: had the mount settled `mounted`, the next
mount would treat it as a crash and the audit would report the loss by hostname (`PLAN.md:332`). The
current path converts a reportable loss into an unreported one, which is precisely what
`AGENTS.md` § *Invariants* § 8 names as "silent data loss with a clean exit code".

The suite is green, and the test that covers this path is why. `tests/fuse/test_mount.py:182-206`,
`test_second_sigint_mid_drain_downgrades_to_detach`, sets up two node-local dirty files, fires the
second SIGINT from inside the first `commit_upstream`, and then asserts `settled_state(...) ==
'detached'` and that exactly one of the two files reached the source. It never asserts anything
about the second file, which is the one that was lost. The behaviour is pinned rather than caught.
No probe was needed to confirm the defect: the branch is five lines and the repository's own test
demonstrates the outcome.

Two related gaps live in the same five lines and belong in the same cycle.

**`umount --detach` is bounded by `drain_timeout` where `PLAN.md` says it completes.** `PLAN.md:286`
distinguishes them explicitly — "`--detach` completes the rescue; SIGTERM bounds it by
`drain_timeout` (schedulers send SIGKILL shortly after)". The daemon cannot tell them apart, because
`request_shutdown(detach=True)` sends SIGTERM (`src/sosfs/fuse/mount.py:129`) and the single SIGTERM
branch passes `budget=self.spec.drain_timeout` unconditionally. `drain_timeout` defaults to 300 s
(`DEFAULT_DRAIN_TIMEOUT = '5m'`, `src/sosfs/tier/spec.py:35`, `spec.py:251`) and is never `None`, so
the unbounded path exists only in tests. An operator who runs `sosfs umount --detach` on more than
five minutes' worth of node-local dirty data — and rescue transfers are whole-file and serial, since
`TransferPool.submit` has no caller in `src/` — strands the remainder on a node they are about to
give back, having run the command `PLAN.md` documents as the one that finishes the job.

**An incomplete rescue is discarded rather than recorded.** `rescue()` returns
`RescueResult(rescued, stranded, complete)` and `Mount.shutdown` throws it away; `state =
'detached'` is set unconditionally. The stranded count reaches the operator as one `log.warning` at
`src/sosfs/engine/rescue.py:66` and as nothing else — no exit status, no row, no field a later
`sosfs commit` could read. `PLAN.md:294` states that "after a detach every newest version lives in a
shared durable tier"; with a budget cutoff that sentence is false and there is no artifact that says
so.

**`drain_timeout = 0` is inverted against its own documentation.** The comment in `PLAN.md:388` on
the spec example reads `drain_timeout = "5m"  # SIGTERM rescue budget (SIGINT always drains fully; 0
disables)`. The code treats `0.0` as a zero budget: `rescue`'s check is `if budget is not None and
tick() - start >= budget` (`src/sosfs/engine/rescue.py:64`), which is true on the first iteration,
so it moves nothing and returns `stranded = len(worklist)` — pinned by
`tests/engine/test_rescue.py:160-169`, `test_rescue_zero_budget_rescues_nothing`. Nothing validates
the value: `_duration` accepts `0`, `'0'` and `'0s'` (all parse to `0.0`) and `_validate_stack`
never looks at it. An operator who reads "0 disables" as "no timeout" configures total node-local
exposure and gets a clean `detached` for it.

## Why it was deferred

Found by the invariant audit run during the software-factory port, which was a harness change and
not the place to redesign the shutdown state machine. These are **live defects in the shipped tree**
(confirmed at `6419966` by reading the branch and the test that pins it), not unbuilt work.

`AGENTS.md` § *Invariants* § *Known open violations* names this in the § 8 row, and
`src/sosfs/fuse/mount.py` → `shutdown`, `_on_sigint`, `_on_sigterm` is a high-blast-radius region:
the only code deciding drain versus rescue versus neither, and the settled `shutdown_state` is what
tells the next mount whether to trust its metadata.

There is also a **plan-drift obligation** on this cycle. Reaching `PLAN.md`'s `--detach` semantics
needs a way for the daemon to distinguish "complete the rescue" from a scheduler's SIGTERM, and
recording a partial rescue may need a fourth `shutdown_state` value or a new record. Both are design
changes, so `PLAN.md` is reconciled with the maintainer *before* the code moves.

## Outcome / vision

Every path that settles `detached` has actually rescued node-local dirty data, and a path that could
not finish says so in a form a later `sosfs commit` and a human both read — not one warning line at
default verbosity. `umount --detach` finishes the rescue, as documented, because the daemon can tell
it from a scheduler signal. `drain_timeout` means what the spec comment says it means, or the spec
refuses the value. After any clean-looking shutdown, the set of files that did not make it to
durable storage is either empty or enumerated.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. Prefer EARS phrasing (see
the KB's EARS notes).

- **R1** — When a drain is cancelled by a second SIGINT, the mount shall rescue node-local dirty
  data before settling `shutdown_state`: every path in the rescue worklist that the budget allowed
  shall have its newest copy resident and dirty on a `scope != "node"` tier, and no dirty residency
  shall remain on a node-scope tier for a rescued path.
- **R2** — If a rescue ends with `stranded > 0`, then the mount shall not settle a state that
  asserts metadata is trusted and no audit is needed; the settled state and the recorded stranded
  set together shall be enough for a later `sosfs commit` to know it must look.
- **R3** — When a shutdown strands node-local dirty data, the mount shall record the stranded count
  and the affected paths durably, and a foreground mount shall exit non-zero — the count shall be
  readable after the process is gone, not only in the mount log.
- **R4** — When `sosfs umount --detach` is issued, the rescue shall run to completion irrespective
  of `drain_timeout`, and after the command returns no `scope = "node"` tier shall hold a dirty
  residency. Distinguishing this from a scheduler SIGTERM is a design change; `PLAN.md` records the
  mechanism before the code does.
- **R5** — Where `drain_timeout = 0` is configured, the overlay spec shall either reject it at parse
  time with a named `SpecError` on stderr and a non-zero `sosfs mount` exit, or treat it as an
  unbounded rescue budget; the behaviour and `PLAN.md`'s spec comment shall agree.
- **R6** — When a real mount on Linux receives two SIGINTs during a drain over a node-scope tier,
  the node tier shall hold no dirty copy once the process exits. **Integration-only**: this needs
  `/dev/fuse` and a live daemon, which CI does not run today — see
  [`ci-integration-lane.md`](ci-integration-lane.md).

## Notes

- Related: the other reproduced silent-data-loss defect, a commit before a queued rename orphaning
  the source object, fixed by the `deferred-rename-source-orphan` cycle (landed 2026-11-06,
  `spec/deferred-rename-source-orphan/`).
  [`offline-commit-command.md`](offline-commit-command.md) — `sosfs commit` is the consumer of
  `shutdown_state` and the reason a wrong settle costs anything; whatever R2 and R3 record has to be
  something that cycle can read, so the two want a shared shape.
  [`audit-command.md`](audit-command.md) — the walk that `detached` promises is unnecessary, and the
  only thing that could recover from a wrong settle. It does not exist, which is why the settle is
  the whole defence. [`ci-integration-lane.md`](ci-integration-lane.md) — the drain, detach and
  force-umount surface is exactly the ten tests that run on no automated machine.
  [`pump-liveness-and-loss-windows.md`](pump-liveness-and-loss-windows.md) owns the other unbounded
  loss windows (`max_interval = None`, a dead scheduler); `drain_timeout = 0` sits here because it
  is the same five lines.
- `PLAN.md` → Shutdown (lines 286-297), `sosfs commit` (line 319), loss reporting by hostname (line
  332), the spec comment (line 388), and the Detach/Rescue definitions (lines 81-83).
  `AGENTS.md` § *Invariants* § 8 and its § 8 row under *Known open violations*.
- Open question — **what signals "complete the rescue".** A control-channel request is the clean
  answer and makes this cycle depend on
  [`control-channel-and-status.md`](control-channel-and-status.md), which is unbuilt and sits later
  in the order. A second signal number (SIGUSR1) or a sentinel file the daemon reads at shutdown are
  both available today and both are a private protocol between two halves of the same program.
  `PLAN.md` has to name the choice before the code does.
- Open question — **how a partial rescue is recorded.** A fourth `shutdown_state` value is the
  clearest signal to a later `sosfs commit`, and it changes the `CHECK` vocabulary in `schema.sql`.
  There is no migration machinery and `db.py` refuses a `SCHEMA_VERSION` mismatch outright, so a
  bump makes every existing state database unopenable — a product decision about existing overlays,
  not a schema detail. Recording the stranded set as rows under the existing vocabulary avoids the
  bump and is a weaker signal.
- Open question — **whether the second SIGINT should rescue at all.** A user pressing Ctrl-C twice
  wants out, and a rescue is more work, not less. The rescue is bounded and moves one hop rather
  than to the source, so it is far cheaper than the drain they cancelled; a third SIGINT could be
  the real escape hatch, settling a state that admits the loss. That needs a budget decision and a
  `PLAN.md` line.
- Open question — **`drain_timeout = 0`: reject or unbound.** Making it unbounded matches the
  documented wording and converts a scheduler's SIGTERM into an unbounded stall before the SIGKILL
  that follows it, which is worse for the case the knob exists to serve. Rejecting it at parse time
  is safer and breaks any spec file already carrying the value.
- 2026-11-11 (whole-tree review at `64faf27`): two additions.
  - **Three paths settle `detached` without the rescue, not one.** `Shutdown.FORCE` does it by design
    (`src/sosfs/fuse/mount.py:258-259`), beside the second SIGINT and a rescue cut short by its budget.
    One three-valued flag cannot carry "metadata is trusted" and "node-local data was rescued" as a
    single promise. The residency rows already say which dirty copies sit on a node-scope tier and
    `overlay.host` says which node, so `detached` can mean the first alone and `commit` can read the
    second.
  - **The shutdown is a state machine held in a latched mode, a counter and an event** (`_mode`,
    `_sigint`, `_stop`). Declared as one (serving, draining, rescuing, settled, and the signal each
    transition takes), the missing transition from a cancelled drain to a rescue is a hole in a table
    instead of a branch nobody wrote. `Mount` is not a `StateThread`, though the house rule makes
    long-running components state machines.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — FUSE/CLI
  layering brief, divergences 1 and 2, and the engine-policy brief, divergences 1, 2 and 3.
