---
status: unshaped
kind: fix
appetite: small
lane: public
---

# The state store opens a rollback journal with `synchronous = FULL`

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The one state database lives in a state directory on storage every host of the workflow sees, by the
maintainer's ruling of 2026-11-11 (root `PLAN.md`, Ruling D); on his clusters that is the home
filesystem, which is a network filesystem. The tree opens every connection in WAL with
`synchronous = NORMAL` (`src/terrace/state/db.py:94-95`), and `AGENTS.md` § 2 states it as an
invariant: "WAL is what makes committed transactions survive `SIGKILL`". SQLite's documentation says
WAL "does not work over a network filesystem", because its readers and writers share memory through
a `-shm` file; this workflow has one host at a time, but no page says a `-shm` file on a network
filesystem works from one host. A rollback journal is the one mode the documentation names as
mitigating a network filesystem, and it needs no shared memory.

The root `PLAN.md` decides it (Decision 6, signed 2026-11-15): `journal_mode = DELETE` with
`synchronous = FULL`, and of the filesystem, locks on the database file alone. `FULL`, because a
released node is nearer a system crash than an application crash, and with `NORMAL` a commit is not
synced. **Ruled by the maintainer at sign-off:** for this workload the safer mode is the right
default; the design enforces it now, and this seed builds without waiting on the network-filesystem
check `issues/hpc-deployment.md` carries.

The plan cycle measured both modes on a laptop's local filesystem
(`spec/plan-the-bundle-loop/research/01-state-store-on-shared-storage.md`; root `PLAN.md`
§ *Architecture* → *The state store on shared storage*): every commit a process acknowledged before
`SIGKILL` was read by the next process, 300 of 300 in the assumed mode; a second process is refused
the write lock; a connection per thread still works, and a reader does not wait on an open write
transaction. Two costs differ. A commit waits on every open read on another connection, 1.010 s
against a reader's 1.0 s open transaction, which WAL never does. And the median write transaction
took 0.285 ms against WAL's 0.022 ms.

## Why it was deferred

The plan cycle `spec/plan-the-bundle-loop/` writes the contract and no engine code, and `db.py`'s
pragmas are on `AGENTS.md`'s high-blast-radius list. `AGENTS.md` § 2 and § 10 keep their text and
carry a note *Scheduled to change* naming this seed, with a row each in § *Known open violations*,
until this cycle lands. Pre-existing: the tree has configured WAL since Phase 2.

## Outcome / vision

Every connection opens the mode the design assumes, and `AGENTS.md` states why. No engine read holds
a cursor open long enough to make a commit wait, so the cost of the safer mode is the cost of a
synced commit and nothing more.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN the state store opens a connection, it SHALL set `journal_mode = DELETE` and
  `synchronous = FULL` before any other statement, on every connection, and a test SHALL read both
  pragmas back.
- **R2** — WHEN a process that acknowledged N commits is killed with `SIGKILL`, the next process SHALL
  read all N (the check of `tests/state/test_survival.py`, run in the new mode).
- **R3** — THE engine's reads that walk a cursor SHALL be listed with how long each holds it, and
  none SHALL hold one across I/O, a lock wait or another transaction. Two were named and not audited:
  `src/terrace/state/journal.py:309` (`pending`) and `:318` (`queued_marks`).
- **R4** — `AGENTS.md` § 2 SHALL state the journal mode, the `synchronous` level and the reason for
  each; § 10 SHALL argue the purge-watch read without "a WAL reader does not wait on a writer's open
  transaction", or say what holds in its place; both notes and both rows SHALL go.
- **R5** — The suite's wall time under `-n auto` SHALL be measured before and after, and the change
  reported.

## Notes

- High blast radius: `state/db.py`, the four pragmas.
- **A later, separate concern: throughput.** By the same sign-off ruling, measuring throughput
  across SQLite's options (journal modes, `synchronous` levels, `locking_mode`, the cost of a commit on
  a network filesystem) waits until the project is more mature. It is not this seed's acceptance and
  does not hold it back. Where it is measured, it belongs beside `issues/hpc-deployment.md`'s check,
  on the filesystem that holds the state directory, and beside `issues/read-path-metadata-writes.md`,
  whose three writes per open of a resident file are each a synced commit in this mode.
- If the maintainer's check on his filesystem fails, the root `PLAN.md` says what changes (§ *The
  state store on shared storage*, "What changes if it fails there"); that is a revision of Ruling D,
  his to make, and not this seed's.
- `issues/namespace-sync-redesign.md` retires the purge watch (Ruling G), which removes one of the two
  § 10 sentences R4 touches.
- Found by: `spec/plan-the-bundle-loop/` (2026-11-16), root `PLAN.md` § *Decisions* → *What the tree
  does not yet do*.
