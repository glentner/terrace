---
status: unshaped
kind: feature
appetite: big
lane: public
---

# The durable tier records what each staged bundle now holds

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The maintainer's story calls the staged bundles and the altered files beside them on scratch the
durable set a halted job recovers from. Read by its files alone, that set is wrong by exactly the
removals and the renames: a removed member is still in the staged bundle, and a renamed member is
there under its old name. The root `PLAN.md` decides how the durable tier records them (Decision 4,
signed 2026-11-15, and § *The tier model* (b), (c) and (h)): beside each staged bundle a **member
index**, a function of the bundle written once, and a **bundle record**, one small file that names
what the staged bundle's members are now called, which are removed, and which altered file holds a
member's bytes. With the staged bundle and the altered files it is enough to rebuild the bundle
without the database. It is written by rename into place when the durable tier is brought up to date
for that bundle (a push into the tier, a soft unmount, before a rebuild), not on every change, so it
may be behind the database and is never ahead of it by more than the change being recorded.

The tree has neither file. `ArchiveTier` indexes every member into the state database
(`archive_index` in `src/terrace/state/schema.sql:135`, full metadata in the catalog), and nothing on
any tier records an index, a removal or a rename. The staged `.tar` itself sits at a `staging` path
outside every tier (`src/terrace/tier/archive.py:236-241`), which `issues/archive-spec-plumbing.md`
moves into the durable tier by Decision 1.

The plan cycle measured the decision on the local loop's helper
(`spec/plan-the-bundle-loop/prototype/RECORD.md`, `research/02-bundle-rebuild-prototype.md`): with
the state database deleted, a rebuild from the tier's files and the record equalled the oracle; from
the tier's files alone it did not (at 6 members, 5 names missing and 6 extra). The record file was
913 bytes at 6 members and 33,616 at 300, about 110 bytes a member each time it is written.

## Why it was deferred

The plan cycle `spec/plan-the-bundle-loop/` writes the contract and no engine code (its GOAL's
non-goals). No other seed owns these files: `issues/archive-finalization-integration.md` owns the
rebuild and its triggers, `issues/audit-command.md` the recovery walk that reads them, and
`issues/shared-staged-tier.md` the index a second mount reads. Unbuilt work, not a defect: nothing
stages a bundle on a tier yet.

## Outcome / vision

A durable tier a person can hand to a later job, or to a recovery with no database, and from which
each bundle can be rebuilt to the bytes the overlay showed. The database stays the authority while
it exists; the tier's files are what a rebuild takes its bytes from, what a mount after a kill checks
the database against, and what a second mount reads instead of scanning.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a bundle is staged on the durable tier, its member index SHALL be renamed into place
  before the bundle itself, so a bundle under its staged name always has its index.
- **R2** — WHEN the durable tier is brought up to date for a bundle (a push into the tier, a soft
  unmount, before a rebuild), the bundle record SHALL be written by rename into place, and SHALL name
  every member's current name, every removed member, and the altered file that holds each changed or
  created member's bytes.
- **R3** — WHEN the state database is deleted after a soft unmount, a rebuild from the staged bundle,
  the altered files and the record SHALL equal a plain-directory oracle in names and bytes.
- **R4** — WHEN the process is killed between the rename of a record (or of a staged bundle) and the
  database commit that follows it, the next mount SHALL find the record at most one change ahead of
  the database and SHALL reconcile it without loss.
- **R5** — The tier's limits SHALL count the index and the record (root `PLAN.md` § *The tier model*
  (d)).

## Notes

- The record's format is this seed's to choose; the root `PLAN.md` fixes what it says and when it is
  written, not how. Whether an altered file is named on the tier by path or by entry id is
  `issues/namespace-sync-redesign.md`'s decision 4, and the record's content depends on it.
- The two kills of R4 were argued in the plan cycle and not injected; the prototype injected kills
  around landing an altered file and around a rebuild only (root `PLAN.md` § *The tier model* (c)).
  R4 is where they are first injected; `issues/crash-fault-injection-suite.md` is the suite such kill
  points join once it exists.
- The member index is the same file a second mount reads in `issues/shared-staged-tier.md` (root
  `PLAN.md` § *The tier model* (g)); build it once, here or there, whichever lands first.
- Related: `issues/archive-finalization-integration.md` (writes the record before a rebuild and reads
  it), `issues/audit-command.md` (checks the durable tier against the records, Decision 5),
  `issues/shutdown-rescue-gaps.md` (a soft unmount writes the record, Ruling E).
- Found by: `spec/plan-the-bundle-loop/` (2026-11-16), root `PLAN.md` § *Decisions* → *What the tree
  does not yet do*.
