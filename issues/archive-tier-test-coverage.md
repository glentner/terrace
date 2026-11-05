---
status: unshaped
kind: test
appetite: small
lane: public
---

# Archive coverage at the engine and mount level

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs are negotiated with a human.

## Problem

The archive adapter is well covered in isolation and not covered at all in context.

`tests/tier/test_archive.py` is 544 lines and 36 tests, passing in 0.85 s;
`uv run pytest -m unit -k archive` selects 37, and `tests/tier/` as a whole is 127. What those tests
cover is most of what the old Phase 9 checklist item asked for: both modes over POSIX
(`test_tar_direct_update_round_trip:346`, `test_staged_populate_and_offset_read:371`), tar.gz over S3
against moto (`test_staged_source_on_s3:505`), index persistence across instances
(`test_put_leaves_no_temp_and_survives_new_instance:271`,
`test_index_is_cached_and_reindex_refreshes:173`) and compaction
(`test_put_replaces_and_stays_compact:260`). Transcribing that checklist item would mostly re-buy
coverage that exists, which is why this seed re-scopes it.

What does not exist is any test that drives an archive **through the engine or a mount**.
`grep -rl archive tests/engine tests/fuse` returns nothing. No `tests/engine/` module builds an
archive stack, and none can: `OverlaySpec` rejects the tier type and the archive keys
(`tier/spec.py:25`, `:28`) and `OverlayEngine.start` has no archive branch
(`engine/overlay.py:109-126`). The gap is structural, not neglect.

The consequence is that every property that only appears above the adapter is unverified. Nothing
checks promote-on-open from an archive source, cascade commit into an archive, the eviction
manager's interaction with a source that cannot hand out file handles, `archive_index` surviving a
remount as a mount-level fact rather than a fresh-instance fact, or whether a batched finalization
reconstructs the same tree a per-file sequence would. Each of those is a place a regression in
`archive.py` would pass the whole unit suite.

## Why it was deferred

Last of four archive cycles by construction: nothing here can be written until
[`archive-spec-plumbing.md`](archive-spec-plumbing.md),
[`archive-journaling-and-locking.md`](archive-journaling-and-locking.md) and
[`archive-finalization-integration.md`](archive-finalization-integration.md) land, because until then
no test can build the stack the tests are about.

**Unbuilt work, not a live defect**, and not listed in
[`AGENTS.md`](../AGENTS.md) § *Invariants* § *Known open violations*. It
closes the old Phase 9 immediately after the code that makes it possible, while the design is still
in the author's head.

## Outcome / vision

An archive source is a first-class fixture in `tests/engine/`, driven the way a POSIX stack already
is, so a regression in the archive write path fails the fast suite in under a second rather than
surfacing on someone's dataset. The one thing that genuinely needs a kernel — a real mount over an
archive source — is marked `integration`, and the fact that it runs nowhere yet is written down
rather than assumed away.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a four-tier stack with a ZIP source is driven through the engine API and flushed,
  the resulting source archive's member set and contents SHALL equal a plain-directory oracle driven
  with the same sequence of operations.
- **R2** — WHEN a staged `tar.gz` source over S3 is driven through the engine and drained, the
  pushed source SHALL decompress to the oracle's tree. Runnable under `-m unit` against moto-server,
  which the `tests/tier/` fixtures already provide; the MinIO version belongs to `-m integration`.
- **R3** — WHEN an overlay with an archive source is stopped and a fresh `OverlayEngine` is started
  over the same state directory, `readdir` SHALL be served from the persisted `archive_index` without
  reopening the inner location, asserted by patching `InnerLocation.open_read` to raise.
- **R4** — WHEN a batched finalization commits N files at once, the reconstructed archive SHALL
  equal the archive that the same N operations produce one at a time, member for member, including
  modes and mtimes. This is the observable that
  [`archive-finalization-integration.md`](archive-finalization-integration.md) is graded against, and
  the reason this cycle follows it immediately.
- **R5** — WHEN `uv run pytest -m unit -k archive` runs, it SHALL report at least one engine-level
  test for each mode and backing combination the adapter supports — posix/zip, posix/tar,
  posix/tar.gz, s3/zip, s3/tar.gz — and a pass count strictly greater than today's 37.
- **R6** — WHEN a mount is served over a ZIP source, reading a file through the mount SHALL return
  that member's bytes, and unmounting SHALL leave the archive holding the writes. Requires
  `/dev/fuse` on Linux, so it carries the `integration` marker and runs on no automated machine until
  [`ci-integration-lane.md`](ci-integration-lane.md) lands.
- **R7** — Every module this cycle adds SHALL declare `pytestmark` with `unit` or `integration`. A
  module without one is collected and then deselected by every gate, which is a test that is written,
  reviewed, merged and permanently green because it never executes.

## Notes

- Strictly after the other three archive cycles. Feeds
  [`crash-fault-injection-suite.md`](crash-fault-injection-suite.md) and
  [`benchmark-harness.md`](benchmark-harness.md), both of which want an archive stack fixture they do
  not have to build themselves.
- [`ci-integration-lane.md`](ci-integration-lane.md) is what makes R6 mean anything. Note the trap it
  documents: `uv run pytest -m integration` exits 0 when every test skipped, so any gate written
  against R6 must assert a pass count rather than an exit code.
- `PLAN.md` → Testing (the engine-first discipline and the hypothesis oracle); → ArchiveTier
  (:125-147).
- Open questions a shaping conversation has to settle:
  1. **Does `tests/engine/test_model.py` get a second parametrization with an archive source?** It is
     the strongest equivalence check in the suite and already the slowest. Against an archive source
     it re-packs per operation unless the batching from
     [`archive-finalization-integration.md`](archive-finalization-integration.md) also covers the
     model's op mix, so the cost could be far worse than double. The alternative is the smaller
     hand-written equivalence test in R1 plus a documented gap.
  2. **What must not be traded away to fit that parametrization.**
     [`AGENTS.md`](../AGENTS.md) § *Invariants* names
     `tests/engine/test_model.py` and `test_replay.py` as high-blast-radius: weakening the model's op
     mix, or reintroducing a skip guard, silently retires the coverage that justified closing the
     Phase 8 replay risk. Shrinking the model to accommodate archives would be exactly that.
  3. **Where the archive stack fixture lives.** `tests/engine/conftest.py`'s `Clock`, `seed` and
     `build_spec` are imported across test packages and are effectively an interface; adding an
     archive variant there is convenient and widens that interface for every later cycle.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the
  remaining-work brief (dossier 3 and the ROADMAP checkbox-accuracy table).
