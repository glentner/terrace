---
status: unshaped
kind: docs
appetite: big
lane: public
---

# Redraw `PLAN.md` around the user story

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`PLAN.md` and `AGENTS.md` § *Invariants* describe a different product from the one the maintainer
described on 2026-11-11 ([`issues/local-bundle-loop.md`](local-bundle-loop.md) quotes the story), and
every cycle is graded against them. `PLAN.md` outranks the code on intent, and a violation of a
numbered invariant is graded CRITICAL, so a cycle that builds toward the story today is building
against its own contract.

| The story | `PLAN.md` and `AGENTS.md` today |
|---|---|
| Pressure or delay moves a copy one tier up, and no further | Eviction commits a dirty copy to the source (§ 7), and drops a clean one |
| The bundles and altered files on scratch are the durable set; a halted job recovers from them | The state database is the authority for every tier; a killed mount needs an audit that does not exist |
| A commit from a login node rebuilds what changed | The state directory must be on shared storage for that, and SQLite's documentation rules its journal mode out there |
| A bundle is repacked on its own delay, under quota pressure, or at a commit | The adapter re-packs on every call; the working copy sits outside every tier and every quota |
| A change inside a bundle | Queued and replayed against the source one operation at a time (§ 5) |
| One bundle or ten thousand, on an object store or on tape over SFTP | Sharding and SFTP are both scheduled after v0.1.0 |
| The same spec on many nodes, later | The first explicit non-goal |

The cycles since adoption show what building against the wrong contract costs
([`issues/namespace-sync-redesign.md`](namespace-sync-redesign.md)). This cycle writes the right one
before the next line of engine code.

## Why it was deferred

It was not possible before 2026-11-11: the story had not been told in one place. The plan-drift rule
already requires it: a design that moves is reconciled in `PLAN.md` first, with the maintainer.

## Outcome / vision

`PLAN.md` opens with the user story and every section under it can be traced to a sentence of it. A
builder knows what each tier holds, what moves a copy and how far, what the durable tier's files are,
what a rebuild reads, what a signal does, and what a later multi-node mode will need left alone. A
reviewer grades against that, and knows which invariants are scheduled to change and by which cycle.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. Each is a decision recorded with its reason and the
alternative turned down.

- **R1** — **The story.** `PLAN.md` SHALL open with it.
- **R2** — **The tier model.** Every stage tier has a size limit, a count limit and a delay. Pressure
  and delay each move a copy one tier up. What happens to a clean copy under pressure
  ([`issues/eviction-one-hop.md`](eviction-one-hop.md)).
- **R3** — **The durable tier.** What it holds (staged bundles, their indexes, altered files, the
  record of removals), what is authoritative for it, which steps change it and why each is atomic,
  and what its limits count.
- **R4** — **How a source catches up.** A bundle is rebuilt from the durable tier: unchanged members
  from the staged bundle, changed ones from the files beside it, removed ones left out. Its triggers
  are its delay, pressure on the durable tier, and a commit. What a rename inside a bundle is.
- **R5** — **The lifecycle.** A signal commits node-local changes to the durable tier and releases the
  node. A commit run from any host that sees the durable tier rebuilds what changed and leaves the
  staged bundles in place for the next mount. What a mount does after a kill.
- **R6** — **Identity and local state.** Row ids that are never reused, as a schema bump taken before
  anything is deployed. What a mount's database holds once the durable tier describes itself.
- **R7** — **Many nodes.** Designed for and not built: the five choices in
  [`issues/multi-node-mounts.md`](multi-node-mounts.md), and the non-goals rewritten to match.
- **R8** — **What the tree does not yet do.** Each decision the code contradicts SHALL be listed with
  the seed that owns the change, and `AGENTS.md` § *Known open violations* SHALL gain a row for each
  invariant a decision overturns, so a review of a later cycle grades against the new rule.
- **R9** — **Two prototypes** on the local loop's helper, measured against a plain-directory oracle
  and thrown away afterwards: a rebuild of one bundle after members are changed, created, renamed and
  removed, a directory rename among them; and two processes sharing one durable tier that pull one
  bundle once.

## Notes

- **Prior art for R3 and R4.** The kernel's overlay filesystem keeps a writable upper directory over
  a read-only lower one. A removal is a whiteout in the upper directory; a directory that replaces a
  lower one is marked opaque. Renaming a directory that lives in the lower layer fails with `EXDEV`
  by default, and `mv` falls back to copying; an optional mode records a redirect instead. That
  default is worth weighing for a directory inside a bundle: it replaces the hardest case of the
  replay with an error every tool already handles.
- **Prior art for the product.** `ratarmount` mounts archives read-only at speed, and has an
  experimental write overlay: changes go to a folder, deletions are tracked, and a separate
  command applies them to the archive with GNU tar's delete and append. It does not commit to a
  compressed archive. What this project adds is the cascade, the delayed and automatic
  re-aggregation into compressed bundles on a remote source, many bundles, and the batch lifecycle.
- This cycle absorbs most of [`issues/plan-md-reconciliation.md`](plan-md-reconciliation.md)'s
  residue; what it leaves is that seed's.
- It writes no engine code. R9's prototypes exist to stop a decision being made by reading, which is
  how the last seven cycles were steered.
- Sources: Linux kernel documentation, *Overlay Filesystem*,
  <https://docs.kernel.org/filesystems/overlayfs.html>; `ratarmount`,
  <https://github.com/mxmlnkn/ratarmount> (README; issue 120 on committing to compressed archives);
  SQLite, *Write-Ahead Logging*, <https://sqlite.org/wal.html>.
- Found by: the maintainer's user story and clarification of 2026-11-11, compared with the tree at
  `64faf27`.
