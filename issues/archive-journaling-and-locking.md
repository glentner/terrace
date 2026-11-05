---
status: unshaped
kind: fix
appetite: big
lane: public
---

# Bring archive mutations under the journal and a lock

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs are negotiated with a human.

## Problem

Four findings in one region of `src/sosfs/tier/archive.py`. The first is a violation of a sacred
invariant; the rest share its blast radius, which for an archive source is the entire dataset.

### Re-pack and re-sync mutate a managed location outside any intent bracket

`PLAN.md:245-247` names archive re-sync and compaction among the mutations that must be journaled
*intent → act → confirm*. `ArchiveTier._repack` (`archive.py:529-538`) reads the current archive,
writes a complete replacement through `InnerLocation.atomic_write`, and reindexes. `resync`
(`archive.py:427-451`) recompresses the staging `.tar` and atomically pushes it to the source.
Neither touches `Journal`. `'compact'` and `'resync'` sit in `ACTIONS` (`state/journal.py:30`) and in
the schema's `CHECK` constraint (`schema.sql:125`) with **no producer anywhere in `src/`** —
`grep -rn "resync" src` returns those two vocabulary declarations and `archive.py` itself, nothing
else.

A crash mid-re-pack therefore leaves no record that anything was in flight. It also leaves debris the
audit will misfile: `PosixInnerLocation.atomic_write` (`archive.py:97-109`) writes its temp as
`.{name}.{hex}.tmp` **beside** the archive, while `journal.is_temp_name` (`journal.py:42-44`)
classifies a temp by its first two path components being `.sosfs/partial`. So `PLAN.md:352`'s triage
— recognizable transfer temps discarded, true orphans quarantined and reported — puts the temp in
the wrong bucket, and `PLAN.md:355`'s "interrupted archive re-syncs restart from the extracted
staging copy" has nothing to restart from, because nothing recorded that a re-sync began. The
catalog goes on believing the source is current while the source silently holds its last version.

The old ROADMAP recorded this as shipped: the Phase 9 staged-re-sync item claimed a "journaled
`resync`". It was never journaled.

### `ArchiveTier` has no tier-wide lock, and the engine's locks are path-keyed

`engine/locks.py` is a `LockRegistry` of per-path reentrant locks — `_locks: Dict[str, ...]`, and
`hold(*paths)` sorts and acquires one per path. That is right for POSIX and S3, where two writes to
different paths touch different objects. An archive is one object. `_current_specs`
(`archive.py:519-527`) snapshots `self._members`; `_repack` writes the full member list and then
`reindex()`s. Two concurrent `put`s to different paths in the same archive take *different* locks,
both snapshot the same member set, both re-pack, and the second `os.replace` wins: the first
member is absent from the archive afterwards and **both calls returned success**. No error, no
report line, nothing for the catalog to reconcile against — the residency row says the member is
there.

Inferred from reading rather than reproduced, because there is no archive concurrency test to
reproduce it with: `tests/tier/test_concurrency.py` covers `PosixTier` only
(`test_concurrent_puts_wire_counters_exactly:74`, `test_concurrent_puts_same_path_atomic:95`).

On a free-threaded build the same code races harder. `self._members` and `self._children`
(`archive.py:254-255`) are plain dicts with no guard, replaced wholesale by `_build_maps`
(`archive.py:309-315`) on every `reindex()` while another thread may be iterating them in
`_current_specs`, `list` or `usage`.

### A stale staging copy silently wins over a newer source

`_populate` (`archive.py:414-425`) returns the moment the staging `.tar` exists —
`if self._loc.signature() is not None: return False` — with no comparison against the source. If the
source `.tar.gz` was updated out of band, or the staging file is a leftover from an earlier overlay
on the same scratch path, the tier serves the stale tree and the next `resync` (`archive.py:443-447`)
recompresses that tree over the newer source. This is the archive analogue of the rollback the state
store exists to prevent, and `PLAN.md` does not sanction it.

The mechanism to prevent it is already present and unused. `InnerLocation.signature()` returns
`(size, mtime)` and the protocol docstring at `archive.py:59-60` advertises it as change detection —
the tier "tells whether it exists and has changed via `signature`" — but all five call sites
(`archive.py:337`, `:416`, `:418`, `:439`, `:531`) use it as a bare existence test. `:416` is the
`_populate` early return above, the one R6 targets.

### The index cache is trusted unconditionally and keyed by tier name alone

`index()` (`archive.py:290-299`) rescans only when `_load_cache()` comes back empty. `archive_index`
is keyed `PRIMARY KEY (tier, member)` (`schema.sql:135-145`) and records nothing about *which*
archive the offsets were read from. `_extract` (`archive.py:402-410`) serves a TAR member by
`handle.seek(member.offset)` then `handle.read(member.size)` with no header check, so a stale offset
returns arbitrary in-range bytes and no error. For ZIP the same staleness is loud — `_extract` reads
by name and raises — but for TAR it is silent and wrong.

`tests/tier/test_archive.py:173-188` pins the current behavior as intended: it rewrites the backing
ZIP under a fresh tier instance and asserts the cache still reports the old member, commented "the
backing archive changes out from under us … a fresh instance loads from the cache, no rescan".

## Why it was deferred

Found by the invariant audit run while porting the software factory (2026-08-22), which was a
harness change and not the place to rework adapter atomicity.

Whether this counts as a **live defect** depends on one fact: nothing in `src/` constructs an
`ArchiveTier` today, because the spec parser rejects archive tiers
([`archive-spec-plumbing.md`](archive-spec-plumbing.md)). So no user is losing data right now. It
becomes live the moment archives are reachable, which is the very next cycle — and the invariant it
violates is one of the two `PLAN.md` calls sacred, so it is recorded as a violation rather than as a
gap.

Listed twice in [`AGENTS.md`](../AGENTS.md) § *Invariants* § *Known open
violations*: row § 1/4 (no intent bracket; `'compact'` and `'resync'` have no producer) and row
§ 4/10 (no tier-wide lock, per-call re-pack, stale staging copy).

## Outcome / vision

Every mutation of an archive that is a managed location is bracketed by an intent that survives a
`SIGKILL`, and the temp a crash leaves behind is recognizable to the audit. Concurrent writers to one
archive serialize, so no member disappears behind a successful call, and the member maps are not
torn under a free-threaded build. Populate compares the staging copy's recorded provenance against
the source's signature instead of assuming presence means currency. The index records what it
indexed, so a stale offset causes a rescan rather than a silent wrong read.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN an archive source is re-packed, the mutation SHALL be bracketed: a `Journal.begin`
  intent naming the tier commits before the replacement archive is written, and `confirm` composes
  into the same transaction as the metadata recording the effect. A process killed between the two
  SHALL leave exactly one unresolved `intent` row for that tier.
- **R2** — WHEN a staged archive pushes to its source, the same bracket SHALL apply with
  `action = 'resync'`, so `journal.pending()` names the interrupted push after a crash.
- **R3** — WHEN a crash leaves a re-pack or re-sync temp behind, that temp SHALL satisfy
  `journal.is_temp_name`, so the audit discards it rather than quarantining it as an orphan.
- **R4** — WHEN N threads `put` N distinct paths into one archive tier concurrently, all N members
  SHALL be present in the archive afterwards and the member count SHALL equal N. Asserted on both
  the GIL and the free-threaded interpreter.
- **R5** — WHILE an archive tier is being re-indexed, concurrent `stat`, `list` and `usage` calls
  SHALL observe a consistent member map: no `RuntimeError: dictionary changed size during
  iteration`, and no path that `list` reports and `stat` denies.
- **R6** — IF a staging `.tar` is present but its recorded provenance does not match the source
  archive's current `signature()`, THEN the tier SHALL re-populate from the source rather than serve
  the staging copy, and a subsequent `resync` SHALL NOT overwrite the newer source with the stale
  tree.
- **R7** — IF the cached `archive_index` was built from an archive whose signature no longer
  matches, THEN `index()` SHALL rescan, and a TAR member read SHALL never return stale-offset bytes.
  This reverses the behavior `tests/tier/test_archive.py:173-188` currently pins as intended; that
  test is rewritten by this cycle, deliberately and in the same diff, not deleted quietly.

## Notes

- Second of four archive cycles. It can technically land before
  [`archive-spec-plumbing.md`](archive-spec-plumbing.md), since the adapter is testable in isolation,
  but R4 and R6 are cheaper to observe once an archive stack can be built from a spec. It must land
  **before** [`archive-finalization-integration.md`](archive-finalization-integration.md): that cycle
  adds the drain and commit callers to exactly these paths, and batching re-packs on top of an
  unjournaled, unlocked re-pack widens the crash window instead of closing it.
- [`audit-command.md`](audit-command.md) is the consumer. `journal.pending()` has no consumer today,
  so an intent row this cycle writes is recovered by nobody until the audit exists. That is not a
  reason to defer the bracket — the row is the evidence, and recovery can follow — but the two
  cycles have to agree on what "restart an interrupted re-sync from the extracted staging copy"
  (`PLAN.md:355`) actually does.
- [`invariant-audit-gaps.md`](invariant-audit-gaps.md) carries the sibling `.tmp` naming finding and
  the repo-wide absence of a directory `fsync`; R3 overlaps the first, and promotion should decide
  which cycle owns it rather than letting both claim it.
- [`freethreaded-concurrency-stress.md`](freethreaded-concurrency-stress.md) names
  `ArchiveTier._members` / `_children` as unguarded shared state. R4 and R5 are the targeted version;
  that seed is the stress version, and it needs this one to have happened first or it will only
  rediscover the same race.
- `PLAN.md` → State store, Intent journaling (:245-249); → ArchiveTier (:125-147); → Audit,
  step 3 (:355).
- Open questions a shaping conversation has to settle:
  1. **Where does the lock live?** A `threading.RLock` inside `ArchiveTier` is local and simple but
     cannot compose with the engine's path locks — a rename holds two path locks and then wants the
     tier lock, so the order has to be fixed or it deadlocks, and under FUSE a deadlock hangs the
     mount and needs a forced unmount. A tier-scoped entry in `LockRegistry` composes with `hold()`'s
     sort but puts tier-shaped knowledge in `engine/`.
  2. **Does the bracket live in the tier or in the engine?** Every other intent is opened by
     `engine/transfer.py:79` and the tiers stay journal-free. A tier that writes intents inverts
     that, and gives `tier/` a `Journal` dependency — legal, since `tier/posix.py` already imports
     `state/journal` and the two are peers, but new. The alternative is an engine-level "finalize
     this archive" operation, which only exists once
     [`archive-finalization-integration.md`](archive-finalization-integration.md) lands.
  3. **What gets recorded so a signature comparison is possible?** `archive_index` has no per-tier
     header row; adding one is a schema change, and `SCHEMA_VERSION` has no migration machinery, so a
     bump makes every existing state database unopenable. Storing the signature on the `tier` row, or
     in a sidecar beside the staging `.tar`, avoids the bump at the cost of a second home for the
     fact.
  4. **Does `'compact'` keep its slot?** Re-pack makes compaction implicit, so the action may be dead
     vocabulary — but removing it from the schema `CHECK` carries the same migration cost as adding
     anything else.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the
  tier-invariants and state-invariants briefs.
