---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Many writers on one overlay

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The maintainer's statement of 2026-11-11: something that has come up several times in user
consultations over recent months, and not a goal for the first release. Mount the same spec, over the
same source, on many nodes at once. Each node keeps node-local performance in its own fast tiers. The
nodes negotiate at the durable tier, with directory locks, and changed files become consistent
eventually. Which conflicts are resolved how, and which tier types and orders are allowed, are limits
to define and to guard. He names high-throughput computing with HyperShell as the case it would
change most: thousands of tasks across many nodes reading one dataset, each writing outputs of its
own.

**Ruled the same day, and it sets this aside.** Everything lives in one database per overlay, in a
state directory on durable storage every host of the workflow sees: a home filesystem on the
maintainer's clusters. In his words: "I understand multi-node read-write doesn't work then. We'll
scope that when we get to it." And: "full multi-node writers is a challenge for another day."

The reading half is not set aside. A top durable tier shared read-only between mounts, each with
tiers of its own below, is [`issues/shared-staged-tier.md`](shared-staged-tier.md).

## Why it was deferred

By the ruling above. One database that is the authority for every tier has one writer, and SQLite's
documentation says of the journal mode the engine uses: "All processes using a database must be on
the same host computer; WAL does not work over a network filesystem." Many writers therefore means
changing what is authoritative for the durable tier, which is a design of its own.

## Outcome / vision

A user submits a thousand tasks. Each mounts the same spec on its own node, reads the shared dataset
at node-local speed, and writes its results. The results reach the source as bundles, and no task
waits on another for longer than a copy.

## Sketch of the acceptance criteria

Not drafted. What a first scoping pass should decide:

- **What is authoritative for the durable tier** when no single database can be: the tier's own
  files (staged bundles, altered files, a record of removals), with each mount's database an index of
  its node-local tiers.
- **The first limit to accept.** One writing mount per bundle at a time, taken as a lock on the
  bundle, serves tasks that write outputs of their own and needs no conflict rule. Writers within one
  bundle need one (the last rename wins, both are kept, or the second fails) and may never be wanted.
- **Staleness.** How long a node may serve its local copy of a file another node has changed.
- **Stack shapes.** Which are allowed, refused at mount otherwise. A first rule to test: exactly one
  shared tier, directly beneath the source, and every tier below it node-scoped.

## Notes

- Nothing in the first release is bent toward this. Two habits of the first release keep it
  reachable at no cost: changes to the durable tier made by rename into place, never in place, and
  staged bundles that are a function of their source alone
  ([`issues/shared-staged-tier.md`](shared-staged-tier.md)).
- Do not compare modification times across nodes to decide who wrote last. Clocks differ.
- A lock held across a long operation by a mount that is then killed blocks every other node, and a
  killed mount is the ordinary end of a batch job. Operations under a lock have to be short, or
  resumable by whoever comes next.
- Related: `PLAN.md` § *Vision and scope* (non-goals), § *Future work* ("Multi-client coordination …
  a research project of its own").
- Sources: SQLite, *Write-Ahead Logging*, <https://sqlite.org/wal.html>.
- Found by: the maintainer's statement and ruling of 2026-11-11.
