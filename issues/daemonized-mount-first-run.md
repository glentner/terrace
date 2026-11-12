---
status: unshaped
kind: fix
appetite: small
lane: public
---

# The default mount fails on a first-ever mount

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`terrace mount spec.toml /mnt/space` — no flags, the documented happy path, the default of the only
fully-implemented subcommand — exits 1 with a `FileNotFoundError` the first time an overlay is mounted
against a state directory that does not exist yet.

The ordering is four lines apart. `MountApp.run` (`src/terrace/__init__.py:101`) passes
`foreground=self.foreground or self.debug`, so without `-f` or `-d` `Mount.run` takes
`_daemonize_and_serve` (`src/terrace/fuse/mount.py:364`). That method double-forks, then calls
`self._detach_streams()` at `mount.py:383` — *before* `self.start()` at `mount.py:385`.
`_detach_streams` (`mount.py:394`) opens `Path(self.spec.state) / LOG_NAME` with
`O_WRONLY | O_CREAT | O_APPEND` at `mount.py:400`. `O_CREAT` creates the file; it does not create the
directory holding it, and nothing has created that directory yet. The two places that do are both
downstream of `Mount.start()` (`mount.py:205`): `ExclusiveLock.acquire()` at
`src/terrace/state/db.py:181` and `OverlayEngine.start()` at `src/terrace/engine/overlay.py:98`, each
calling `mkdir(parents=True, exist_ok=True)`.

Measured: against a fresh `state =` path the documented command exits **1** with
`FileNotFoundError: .../state/mount.log`; `mkdir -p` that directory first and the identical command
exits **0** and mounts.

The failure lands outside the readiness pipe's compensating block. `_daemonize_and_serve` wraps only
`self.start()` in the `try` that writes `b'\x00'` to the pipe on failure (`mount.py:384-388`), so the
grandchild dies before that guard is reachable, the launching process's `os.read(read_fd, 1)` returns
empty, and it exits 1 having learned nothing but that the pipe closed. The traceback is visible on the
terminal only because the redirection is the very thing that failed and stderr is still attached — the
one accidental mercy in the sequence.

`-f` is unaffected: `Mount.run` calls `serve()` directly, and `serve` reaches `start()` with the
directory created in the ordinary order.

**The daemonized branch has zero coverage.** `tests/fuse/test_mount.py:67` constructs
`Mount(spec, mountpoint, clock=clock, runner=runner, breaker=breaker, ...)` and drives `start()` and
`shutdown()` directly; `tests/fuse/test_integration.py:69` constructs
`Mount(spec, point, foreground=True, ...)`. `grep -rn '\.run()' tests/` returns nothing. The double
fork, `setsid`, the readiness pipe and the stream redirection are executed by no test in the suite, so
the branch every real user takes is the one branch nothing checks.

## Why it was deferred

Found by the invariant audit run while porting the software factory — specifically while probing the
CLI, not from a `PLAN.md` divergence. The port was a harness change and not the place to edit
`fuse/mount.py`, which is a high-blast-radius region: `AGENTS.md` § *Invariants*
§ *High-blast-radius paths* lists `_daemonize_and_serve` alongside the signal lifecycle
(`shutdown`, `_on_sigint`, `_on_sigterm`, `_serve_after_start`), and `_detach_streams` is the callee
it fails in. Any edit there is unverifiable by the current suite.

This is a **live defect on `main`**, on the first command a new user runs. It is recorded in
`AGENTS.md` § *Invariants* § *Known open violations* as the final row, "The daemonized (default)
mount fails on a first-ever mount — the log file is opened before the state directory exists. No test
covers the daemonized branch."

## Outcome / vision

A first-ever `terrace mount spec.toml /mnt/space` mounts. The daemonized path is exercised by a test, so
the next edit to the double-fork sequence has something to break. And when the daemon genuinely cannot
open its log, the launching process says so instead of exiting 1 on a closed pipe.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — When `terrace mount <spec> <mountpoint>` runs without `-f` and `spec.state` names a directory
  that does not exist, the mount command shall exit 0, the overlay shall be mounted, and `mount.log`
  shall exist inside the created state directory.
- **R2** — While the state directory already holds a `mount.log` from a previous run, a subsequent
  daemonized mount shall append to it rather than truncate it, so the record of the previous mount
  survives.
- **R3** — If the daemon cannot open its log file, then the launching process shall exit non-zero and
  report the failing path on stderr, rather than exiting on an empty readiness pipe with no diagnosis.
- **R4** — The suite shall exercise `Mount.run()` with `foreground=False` against a fresh state path
  and assert that the readiness pipe reported success — a test of the daemonized branch itself, not
  only of the corrected ordering. Whether this is reachable mount-free through the injectable `runner`
  and `breaker` that `tests/fuse/test_mount.py:67` already uses, or needs a real mount on Linux and so
  an `integration` marker, is a shaping question; if it is integration-only, say so in the criterion,
  because CI runs no integration job today (`issues/ci-integration-lane.md`).

## Notes

- Related: `issues/ci-integration-lane.md` (if R4's test can only run against a real mount, it has no
  machine to run on until that lane exists); `issues/shutdown-rescue-gaps.md` (the other open defect in
  `fuse/mount.py`, in the signal lifecycle rather than the daemonize path — the two touch the same
  high-blast-radius file and should not be in flight simultaneously). `PLAN.md` § *Shutdown*;
  `AGENTS.md` § *Invariants* § 8 and § *High-blast-radius paths*.
- Open — **where the directory gets created.** Creating it inside `_detach_streams` is the two-line
  fix and puts a filesystem side effect in a stream-redirection helper. Creating it in `Mount.__init__`
  or in `_validate()` makes it a precondition of the object rather than of the daemon — but
  `_validate()` runs inside `start()`, already too late. A third option is to redirect after `start()`
  and accept that the daemon's first moments log to the inherited terminal.
- Open — **should the daemon write a log before it knows it can take the lock.** Today a second mount
  attempt against an already-mounted overlay appends its `DatabaseLocked` traceback into the *running*
  mount's log file, interleaved with the live daemon's output. Deferring the redirect until after the
  lock is held fixes that and costs the ordering above.
- Found by: the invariant audit run during the software-factory port (2026-08-22), FUSE/CLI/layering
  brief, divergence 8.
