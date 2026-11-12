---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Stage a source commit's bytes outside the source mutex, publish inside it

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`OverlayEngine._source_lock` is one re-entrant source-namespace mutex (`AGENTS.md` § *Invariants*
§ 10). It serializes every change to the source's namespace: namespace replay, `_vacate`'s collect
and replay, `_purge_source`, `create` straight onto the source, and a data commit whose destination is
the source. For a data commit it is held for the **whole copy**: `_commit_hop` takes it
(`src/terrace/engine/overlay.py:835-840` (holds at `0e6d14c`)), resolves where the source holds the entry, then runs
`transfers.copy` to the source inside it. The `deferred-rename-source-orphan` cycle shipped it that way
as deviation D4 of its plan, accepted at the PLAN/TECH sign-off (2026-11-06) with this seed as the
follow-up.

The cost is foreground latency on a mount. While one large file commits to the source (the write-back
scheduler, the eviction manager, or an `fsync`), every FUSE `create`, `mkdir` and `rename` that reaches
`_vacate`, and every `fsync` under `fsync_policy = "commit"`, waits for the copy to finish. So does
every other source commit: before that cycle the scheduler, eviction and fsync could commit different
files to the source in parallel. On a multi-GB file that is a callback stalled for the transfer's
duration.

The mutex exists for a reason, and the reason stands. Without it the scheduler's replay and a data
commit on the eviction or fsync route race on `source_path`: the replay reads `source_path = P1`, the
commit writes the new bytes at `P2` and records `source_path = P2`, and the replay's `rename(P1, P2)`
then replaces them with the old bytes while the residency already reads clean (silent data loss;
`spec/deferred-rename-source-orphan/research/04-concurrency.md` § 1). Resolving the place, writing
there and recording it must sit in one region with `advance_source_path`.

**The starting design: stage outside, publish inside** (research 04 § 3's recommendation, rejected for
that cycle by D4):

- `PosixTier.put` already writes to `<root>/.terrace/partial/put-*`, fsyncs, applies metadata, then
  `os.replace`s into the final path, which it resolves *before* the copy (`src/terrace/tier/posix.py:127-150`).
  Split it into `stage(path) → temp` (the bytes, no mutex) and `publish(temp, place)`.
- Under the mutex: re-resolve the entry's place (`source_path`, else its target), replay or vacate
  whatever occupies it, place ancestors by entry, `publish`, then record `source_path` for the entry and
  each created ancestor in the copy's confirming transaction.
- The mutex's hold time drops to a few metadata syscalls and one transaction.

**Why that cycle rejected it** (D4), which is this seed's problem statement:

- It changes the `Tier` protocol in `src/terrace/tier/posix.py` and `src/terrace/tier/s3.py`, two more
  high-blast-radius regions (§ 4, tier atomicity), on top of a cycle that already edited the coupled
  core.
- S3 has no cheap publish. `S3Tier.put` is one atomic PUT with no temp (`src/terrace/tier/s3.py:181-190`),
  so S3 either holds the mutex across the PUT (no gain) or stages to a temp key and publishes with a
  server-side copy plus delete: a second full-size write on the provider and a temp-key orphan
  convention to define, recover and audit.
- The optimistic alternative, put first and revalidate after, works for one file and breaks for a file
  inside a directory whose rename is queued: a put whose ancestor was renamed mid-flight re-creates the
  old ancestor and leaves a stray directory on the source.

## Why it was deferred

Ruled at the `deferred-rename-source-orphan` PLAN/TECH sign-off (2026-11-06): the whole-copy mutex is
accepted as shipped, because it is correct and the cycle's appetite did not stretch to a `Tier`
protocol change; the staged form is seeded here. It is introduced by that cycle (the mutex did not
exist before it), as a cost, not a defect.

## Outcome / vision

A data commit to the source holds the source mutex only to resolve, publish and record, never for the
copy, so a FUSE namespace callback and an `fsync` under the commit policy wait for metadata work, not
for another file's transfer. The correctness that cycle established (one mover, one holder per place,
the source equal to the overlay view once the queue drains) holds unchanged, on POSIX and S3 sources.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHILE a data commit copies a file to a POSIX source, a `create`, `mkdir` or `rename` on
  another path SHALL complete without waiting for the copy to finish.
- **R2** — WHEN a commit's place moves while its bytes are staged (a queued rename of the entry or of
  an ancestor replays in between), the published object SHALL land at the entry's current place, and
  once the queue drains the source SHALL equal the overlay view with nothing at the old place.
- **R3** — WHEN a staged commit is interrupted before publish, the staged bytes SHALL be recognizable
  as an orphan by the convention the audit uses, on every source type that stages.
- **R4** — An S3 source SHALL either stage and publish with its egress and storage cost stated, or keep
  the whole-copy hold, by an explicit decision recorded in the GOAL.
- **R5** — The `deferred-rename-source-orphan` regressions (`tests/engine/test_replay.py`) and the
  stateful model SHALL pass unchanged.

## Notes

- Open question — **is S3 in scope?** Staging to a temp key doubles the provider-side writes; holding
  the mutex across the PUT keeps today's latency on S3 only.
- Open question — **how to show R1.** The suite is single-threaded. A latency claim needs threads,
  which [`issues/freethreaded-concurrency-stress.md`](freethreaded-concurrency-stress.md) builds; the
  two should share a driver.
- Related: `spec/deferred-rename-source-orphan/PLAN.md` § 2.5 and D4,
  `spec/deferred-rename-source-orphan/research/04-concurrency.md` § 3; `AGENTS.md` § *Invariants* § 4
  and § 10; [`issues/transfer-priority-lane.md`](transfer-priority-lane.md) (foreground latency in the
  transfer pool); [`issues/audit-command.md`](audit-command.md) (orphan recovery);
  [`issues/ci-integration-lane.md`](ci-integration-lane.md) (where an S3 publish would be exercised).
- *Added 2026-11-08, retiring `source-served-open-race`:* a promotion off the source now holds the
  mutex for its whole copy too, as the handle-less `truncate`'s promotion already did (`AGENTS.md`
  § *Invariants* § 10; `spec/source-served-open-race/PLAN.md` deviation row 2, which names narrowing
  that hold as this seed's question). On an S3 or archive source the copy is the whole object. Staging
  and publishing does not apply to a read, so shaping decides whether the promotion's hold is in scope
  here; that plan rejected copying out of a pinned, opened source object because `transfer.copy` would
  have to take one, and S3 and archive sources buffer the whole object anyway.
- 2026-11-08: the `overlay.py:835-840` cite under Problem holds at `0e6d14c`; at `5b8dcaf` it is
  `src/terrace/engine/overlay.py:900-905`, and at `d557db2` `:915-920`.
- *Added 2026-11-09, retiring `truncate-recording-after-ancestor-rename`:* a user `rename` now holds
  the mutex too, from its `_vacate` through its catalog-move transaction, including that transaction's
  wait for SQLite's write lock and the `UPDATE` that moves every descendant's path (`AGENTS.md`
  § *Invariants* § 10; `spec/truncate-recording-after-ancestor-rename/PLAN.md` deviation row 1). So
  renaming a large directory delays every `create`, `mkdir` and `rename` that reaches `_vacate`, every
  `fsync` under the commit policy, and every replay and commit to the source, for that long. The hold is metadata work, not a copy, so staging and publishing does not shorten it;
  shaping decides whether narrowing it is in scope here, and any narrowing must still keep an
  ancestor's catalog move out of a handle-less `truncate` from its lookup under the mutex to its
  recording (that cycle's GOAL R4).
- 2026-11-11 (whole-tree review at `64faf27`): held. What excludes a directory rename, and how long the
  source mutex is held, is decision 4 of
  [`issues/namespace-sync-redesign.md`](namespace-sync-redesign.md) R1. Shape this after that is ruled.
- Found by: `deferred-rename-source-orphan`'s plan (D4), seeded in its P5.
