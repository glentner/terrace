---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Mount one overlay on many nodes at once

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The maintainer's statement of 2026-11-11: not a goal for the first release, and something that has come
up several times in user consultations over recent months. Mount the same spec, over the same source, on
many nodes at once. Each node keeps node-local performance in its own fast tiers. The nodes negotiate at
the durable tier, with directory locks, and changed files become consistent eventually. Which conflicts
are resolved how, and which tier types and orders are allowed, are limits to define and to guard. He
names high-throughput computing with HyperShell as the case it would change most: thousands of tasks
across many nodes reading one dataset, each writing outputs of its own.

The tree forbids it at every level:

- `PLAN.md` § *Explicit non-goals* opens with multi-client coherence;
- one SQLite database per overlay is the authority for every tier, the shared one included;
- one process holds an exclusive lock on the state directory, and `shutdown_state` is one flag for
  the whole overlay, so a second mount reads `mounted` and raises `CrashDetected`
  (`src/sosfs/state/lifecycle.py:100`);
- SQLite cannot be the shared piece. Its documentation says of the journal mode the engine depends
  on: "All processes using a database must be on the same host computer; WAL does not work over a
  network filesystem."

## What it asks of the first release

This seed is filed now because five choices the first release has to make anyway decide whether this
is a feature later or a rewrite. None of them costs the first release anything it does not want for
other reasons.

1. **The durable tier describes itself.** The staged bundle, its member index, the altered files and
   the record of what was removed are files on that tier. A mount's database indexes its own
   node-local tiers and caches the rest. This is also what lets a halted job recover from scratch
   alone, and a commit run on a login node that never saw the mount's database.
2. **Every change to the durable tier is one atomic filesystem step**: a rename into place, or a
   directory made. Nothing is written in place, and no state needs two files to change together.
3. **Work that must happen once is guarded by a lock on its bundle** and leaves evidence a waiter can
   test: pulling and decompressing a bundle, and rebuilding and pushing one.
4. **A mount's lifecycle flag belongs to the mount**, not to the overlay.
5. **Staleness is bounded and stated**: how long a node may serve its local copy of a file another
   node has since changed.

## The lock, and what a sibling project learned about it

`uv-manager` serializes installs on shared filesystems with `mkdir`, which is atomic on the parallel
and network filesystems a center runs and does not depend on `flock`, which is not enabled on all of
them. Its rules were each arrived at by a defect, and they carry over:

- only the process that made a lock removes it;
- nobody asks whether a holder is alive. Over a shared filesystem the question has no reliable
  answer: attribute caches serve an old owner line, clocks differ between nodes, and a pid names a
  number on one host;
- a waiter does not take over. It tests whether the work it wanted is already done, and fails at a
  timeout if it is not;
- a lock left behind by a killed holder is reported, and a person clears it.

The last rule is the hard one here. A killed mount is the ordinary end of a batch job
([`issues/hpc-deployment.md`](hpc-deployment.md)), so a design in which a killed holder blocks every
other node until a person acts is not acceptable for a lock held across a long operation. The
operations have to be short, or resumable by whoever comes next from the evidence left behind, or
both.

## Outcome / vision

A user submits a thousand tasks. Each mounts the same spec on its own node, reads the shared dataset
at node-local speed after the first touch, and writes its results. One bundle is pulled once however
many nodes ask for it. The results reach the source as bundles. Nobody stages anything by hand, and
no task waits on another's lock for longer than a copy.

## Sketch of the acceptance criteria

Draft R-IDs, in the order they would be built.

- **R1** — WHEN N mounts of one spec on N hosts read members of one bundle, the bundle SHALL be pulled
  from the source once, and every mount SHALL read the same bytes.
- **R2** — WHEN one of those mounts is killed at any point, the others SHALL keep serving, and a
  mount started afterwards SHALL succeed without a person's action.
- **R3** — WHEN mounts write to different bundles, each bundle SHALL have one writing mount at a
  time, taken as a lock on the bundle, and each SHALL reach the source complete.
- **R4** — WHEN two mounts change the same file, the outcome SHALL be the one `PLAN.md` names (the
  last rename wins, both are kept, or the second fails), and the loser SHALL be told.
- **R5** — WHEN one mount changes a file, every other mount SHALL serve the new bytes within the
  bound `PLAN.md` states.
- **R6** — IF a spec's stack is not one this mode supports, THEN a second mount SHALL be refused at
  start with the reason. A first rule to test: exactly one shared tier, directly beneath the source,
  and every tier below it node-scoped.

## Notes

- **Read-only first.** R1 and R2 alone serve the dataset half of the workload and need no conflict
  rule at all ([`issues/read-only-mount.md`](read-only-mount.md) R6). R3 serves tasks that write
  their own outputs, where conflict is excluded by construction. R4 is the only step that needs a
  conflict policy, and may never be wanted.
- Do not compare modification times across nodes to decide who wrote last. Clocks differ. A
  generation carried in a name or a marker orders writes without a clock.
- Open question — **a directory rename seen from another node**, and a removal racing a read there.
- Open question — **`commit` while mounts are live.** With per-bundle locks it is one more writer.
- Related: [`issues/plan-the-bundle-loop.md`](plan-the-bundle-loop.md) (the five choices above are
  decided there); [`issues/namespace-sync-redesign.md`](namespace-sync-redesign.md);
  [`issues/sharded-archive-sources.md`](sharded-archive-sources.md); `PLAN.md` § *Vision and scope*
  (non-goals), § *Future work* ("Multi-client coordination … a research project of its own").
- Sources: SQLite, *Write-Ahead Logging*, <https://sqlite.org/wal.html>; `uv-manager`, `README.md`
  § *Design notes* (the lock rules).
- Found by: the maintainer's statement of 2026-11-11.
