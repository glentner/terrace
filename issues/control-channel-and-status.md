---
status: unshaped
kind: feature
appetite: small
lane: public
---

# Control channel, `sosfs status` and `sosfs flush`

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

A mounted overlay is opaque. There is no way to ask a running mount what it is doing, and no way to
tell it to commit now. `sosfs status` and `sosfs flush` are both stubs (`src/sosfs/__init__.py:146`
and `:153`) that raise `RuntimeError('Not implemented: …')`, and `src/sosfs/control/` — the package
`AGENTS.md` lists in the repository layout and `PLAN.md` § *Control channel* specifies — does not
exist on disk. An operator debugging a mount that is not committing has one instrument: the log file.

`PLAN.md` asks for a Unix domain socket in the overlay's state directory serving a small
line-oriented JSON protocol, with `sosfs status` and `sosfs flush [path]` talking to the live mount
through it, and `status` also able to read the SQLite state directly when no mount is running. Every
piece of data those two commands need is already queryable: `TierRegistry.usage`
(`src/sosfs/state/registry.py:161`) gives per-tier bytes and file counts, `Journal.queued`
(`src/sosfs/state/journal.py:187`) gives the pending write-back rows, `Journal.pending`
(`journal.py:275`) gives unresolved intents, and `OverlayEngine.flush`
(`src/sosfs/engine/overlay.py:870`) already pushes everything queued to the source ignoring policy
clocks, namespace ops first. Nothing in this cycle invents a new engine capability; it exposes the
ones that exist.

The server has a natural home and a defined shape. `Mount.start` (`src/sosfs/fuse/mount.py:205-224`)
already starts two `StateThread` pumps after the pidfile is written, and a third fits the same slot.
`core/fsm.py:28` defines the contract those threads follow — a nested `states` enum containing
`START` and `FINAL`, one `on_<state>` method per non-`FINAL` member checked at class-definition time,
every blocking operation inside a state bounded by a finite timeout, and `stop()` re-raising whatever
killed the machine. A socket server written to that contract inherits the shutdown-latency bound the
rest of the mount already has; one written as an ad-hoc daemon loop with a blocking `accept()` hangs
the unmount forever, which leaves `shutdown_state = 'mounted'` and forces an audit on the next mount.

The socket also has to be torn down as reliably as the pidfile. `Mount.shutdown`
(`mount.py:249-263`) releases the lock and removes the pidfile in a `finally`; a socket file left
behind by a crashed daemon has to be distinguishable by the client from a socket nobody is listening
on, because those two cases mean different things to an operator — "no mount running" versus "mount
running but unreachable" — and the second is a reason to go look at the daemon.

Offline `status` needs a read-only engine construction path: attach the stack and read the catalog
without `lifecycle.begin_mount`, which `OverlayEngine.start` calls unconditionally
(`overlay.py:108`) and which bumps the generation and sets the crash flag. That path is exactly what
`issues/audit-command.md` builds. Building it twice is the failure mode this cycle's position in the
queue is meant to avoid.

## Why it was deferred

Unbuilt work, scheduled as Phase 10 and never reached. Not a defect: nothing regresses because the
control channel is absent, and the observability xattrs (`user.sosfs.tiers`, `user.sosfs.dirty`,
`src/sosfs/fuse/adapter.py:32-33`) cover the per-file case for tests today. It stays queued behind the
audit and commit cycles because its offline read path is theirs, and because a `flush` command is
most useful once the write-back defects ahead of it in the queue are fixed — a `flush` that reports
success while a queued rename orphaned its source object
(`issues/deferred-rename-source-orphan.md`) is a more convincing lie than no `flush` at all.

## Outcome / vision

`sosfs status spec.toml` prints a per-tier table — used bytes against quota, file count, dirty count,
transfer activity — sourced over the socket when a mount is live and read straight from SQLite when
one is not, with the offline form additionally reporting the recorded `shutdown_state` and mounting
host so an operator can tell "cleanly unmounted" from "crashed on node042". `sosfs flush spec.toml
[/sub/tree]` forces the upstream commit now, for the whole overlay or one subtree, and reports what
it settled. The socket server is a `StateThread` started and stopped with the other pumps, and the
client is a small library in `src/sosfs/control/` that both commands and future tooling use.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHILE a mount is live, `sosfs status spec.toml` SHALL exit 0 and print one row per tier
  carrying used bytes, quota, file count and dirty count, sourced over the control socket. "Live"
  here can mean a `Mount` started without FUSE; if the criterion is instead graded against a real
  mount it is Linux-only and CI runs no integration job, so name the pass count rather than the exit
  status.
- **R2** — WHILE no mount is running, `sosfs status spec.toml` SHALL exit 0 and print the same table
  read directly from SQLite, plus the recorded `shutdown_state` and mounting host. Gradeable
  mount-free against a state directory built by the engine API.
- **R3** — WHEN `sosfs flush spec.toml /sub/tree` is invoked against a live mount, the command SHALL
  report the number of rows settled, and every file under that subtree SHALL be present at the source
  with its current contents when the command returns.
- **R4** — IF the control socket is absent, THEN `sosfs flush` SHALL exit non-zero saying no mount is
  running; IF the socket exists but no process accepts on it, THEN it SHALL exit non-zero saying the
  mount is unreachable. The two messages SHALL differ.
- **R5** — WHEN the mount shuts down by any path — drain, detach or force — the socket file SHALL be
  gone from the state directory, and the control thread SHALL have exited within the same bound as
  the write-back and eviction pumps.
- **R6** — WHEN the control thread raises inside a state, `Mount.shutdown` SHALL still release the
  exclusive lock and remove the pidfile, and the exception SHALL surface rather than being swallowed.

## Notes

- **Layering.** The socket server sits *above* the engine, not inside it: `control/` may import
  `engine/`, and `engine/` may not import `control/`. Putting a listener inside `OverlayEngine` would
  make the engine unimportable in the mount-free tests that are the project's main test discipline,
  and `lint.sh` asserts the direction mechanically. `fuse/mount.py` is the wiring point — it owns the
  process lifecycle and is already the only place that knows a daemon exists.
- Related seeds: `issues/audit-command.md` introduces the offline engine construction path R2 depends
  on and should land first. `issues/offline-commit-command.md` shares it.
  `issues/pump-liveness-and-loss-windows.md` is adjacent in an interesting way — a control channel is
  the natural place to expose "is the write-back scheduler alive", and shaping should decide whether
  that answer belongs in the `status` payload now or waits for that cycle.
  `issues/deferred-rename-source-orphan.md` determines whether `flush` can be trusted at all.
- `PLAN.md` § *Control channel* is three sentences and the whole specification; § *CLI surface* gives
  the two commands their one-line contracts. Anything this cycle settles about framing, the socket
  location or the payload shape is a design decision and belongs back in `PLAN.md`.
- **CLI wiring.** Two subcommands, four places each: the `Application` subclass, `APP_HELP`
  (`src/sosfs/__init__.py:191`), the `commands` registry (`:219`), and `tests/test_cli.py`. Missing
  one is silent in exactly one direction — a command in `commands` but absent from `APP_HELP` fails
  `tests/test_cli.py:31`, while one added to `APP_HELP` and not to `commands` passes and fails only at
  invocation. `tests/test_cli.py:42` parametrizes the stub list `['status', 'flush', 'commit',
  'audit', 'config']` and loses both `status` and `flush` in this cycle's commit, or the suite goes
  red on landing. The cycle also removes two instances of the stale "Phase 9" pointer in
  `StubApp.run` (`__init__.py:53`), which names a phase that no longer exists and was wrong about the
  work even when it did.
- Open questions for shaping, each with its real trade-off:
  - Where does the socket live? `PLAN.md` says the state directory, alongside `mount.pid` — but the
    state directory is required to be durable and is *recommended* to be on shared storage so
    `sosfs commit` can run from another host, and a Unix domain socket on NFS or Lustre is not
    portable. Moving it to a host-local runtime directory makes it work everywhere and creates a
    discovery problem the client has to solve, probably by recording the path in the pidfile the
    daemon already writes.
  - One request per connection with line-oriented JSON, or a persistent connection with framing? One
    request per connection is trivial to implement and to test. A `flush` of a large overlay can run
    for minutes with no output, which argues for streaming progress — and streaming means framing,
    partial-read handling, and a client that can be interrupted.
  - Does `flush` block until the commit completes, or return a handle the client polls? Blocking is
    the honest report and makes R3 checkable; polling is what a long flush needs, and it splits the
    criterion in two.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the remaining-work
  topic (dossier 6) and the FUSE/CLI layering topic (the subcommand wiring rule and the FSM contract).
