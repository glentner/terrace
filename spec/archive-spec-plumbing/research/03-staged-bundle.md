# 03 — Decision 1's staged bundle inside the durable tier (R7)

Read-only research brief for the plan step. Line numbers are on the worktree as of this cycle's base.

## 1. Placement and what "recorded usage" is

**How `.terrace` is hidden.** `PosixTier._internal` matches the first path component `.terrace`
(`tier/posix.py:62-64`, `MARKER_DIR` at `state/lifecycle.py:125`). `stat` returns `None` for it
(`posix.py:93`), `list` skips it at the root (`:107`), every mutator refuses it (`:73,130,155,177,186,198,206`),
and `usage()` prunes it from the walk (`:215-216`). `ArchiveTier` does the same, and `_member_path`
drops any archive member under `.terrace` (`archive.py:262-286`), so no overlay entry can be named
`/.terrace/...`. **`.terrace` is the only root name the overlay reserves**: any other root-level name
(e.g. `/.bundle.tar`) can be taken by an archive member, and a promotion would `os.replace` over the
bundle through `PosixTier.put` (`engine/transfer.py:109-110`). A root-level name therefore fails
Decision 1's "a name no entry of the overlay can take" unless `_internal` is widened, which is the
same blast radius as option (a) below with a second reserved name.

**Can the engine's root listing surface it?** No, whatever the name. `readdir` serves
`catalog.children` (`engine/overlay.py:352-359`), filled only by `_populate` from `self.source.list`
(`:307`). No engine path lists a stage tier.

**"Recorded usage"** is the `tier_usage` row (`state/schema.sql:47-51`: `size_bytes`, `file_count`),
read by `TierRegistry.usage` and changed by `adjust_usage` and `set_usage` (`state/registry.py:161-185`).
Production writers: `transfer.copy` (`transfer.py:119,124`) and the engine (`overlay.py:423,582,941,997,1568`).
**Nothing in `src/` calls `set_usage` or any tier's `usage()`.** The audit does not exist (Known open
violations), so no path today rebuilds the counters and wipes the bundle's count. Only tests compare
counters to the oracle: `tests/engine/test_stacks.py:94` (every tier) and `:195`.

**Recommendation: `<durable root>/.terrace/bundles/<source tier name>.tar`**, with a constant beside
`PARTIAL_DIR` (`state/journal.py:34`). Decision 1's counting comes from one `adjust_usage(durable,
bytes=st_size, files=1)` at the pull. Two ways to keep the oracle honest:

- **(a, preferred)** Amend `PosixTier.usage()` to count regular files directly in `.terrace/bundles/`
  (not temps), still excluding `partial/` and `marker`. Then counters equal `usage()` and a future
  audit rebuild agrees, and the R7 test can assert `registry.usage(d) == tiers[d].usage()`, as
  `test_stacks.py:94` does. Cost: `tier/posix.py` `usage` is on the high-blast-radius table (human
  gate). AGENTS § 4's line "`usage()` … must exclude managed internals" needs narrowing in the same
  diff, and `tests/tier/test_posix.py:190-198` needs a case for it.
- **(b)** Leave `usage()` alone and record a deviation: the oracle under-counts by the bundle, and
  `issues/audit-command.md` must add it. This is harmless today because nothing calls the oracle,
  but it builds in a counter–oracle disagreement that § 4 forbids.

**Idempotence.** `_populate` returns `False` when the bundle exists (`archive.py:416-417`), so a
second start adds nothing. Residual: if the bundle is deleted out of band between runs, the re-pull
counts it again (the counter drifts high). With (a) the audit's rebuild heals that, and AGENTS § 7
assigns reconciliation to the audit. Give the bundle **no residency row**: the evictor would see it
(next section).

## 2. Is a bundle under `.terrace/` at risk from a sweep?

No. `is_temp_name` classifies only `('.terrace', 'partial')` (`journal.py:42-44`) and has **no
caller** in `src/`. No code deletes under `.terrace/`. `Marker.write` only replaces
`.terrace/marker` through `marker.partial` (`lifecycle.py:174-183`). Tests that walk tier roots are
blind to `.terrace` (`tests/engine/test_model.py:46`, `test_replay.py:167`). A future audit must
treat `.terrace/bundles/` as kept. It is not under `partial/`, so the § 4 convention already says so.

## 3. `_populate`'s write path and the intent bracket

`_populate` (`archive.py:414-425`) streams gunzip output into `self._loc.atomic_write()`.
`PosixInnerLocation.atomic_write` (`:92-105`) writes a **sibling temp `.<name>.<uuid>.tmp`**, fsyncs
it, `os.replace`s it, and unlinks it on exception. Under the recommended placement the temp lands in
`.terrace/bundles/`. That is inside the tier root and out of listings, but not under `.terrace/partial/`.
That is the known gap "the archive's sibling `.tmp` is outside the orphan convention"
(`issues/invariant-audit-gaps.md`). Under (a), `usage()` must skip it. Better: give
`PosixInnerLocation` an optional temp directory and write the pull's temp to `.terrace/partial/`
(same filesystem, so `os.replace` stays atomic). That puts a crashed pull's debris inside the
convention.

**Bracket.** There is none today: `_populate` runs inside `index()`. The pull mutates a managed
location (the durable tier), so AGENTS § 1 applies. Bracketing it needs **no schema change**:
`intent.entry` is nullable for tier-level actions (`schema.sql:126`), and `Journal.begin` accepts
`path=None` (`journal.py:259-281`). Use an existing action (`'promote'` is closest: source to stage)
with `tier=<durable>` and `detail={'bundle': …, 'source': <archive tier>}`. **Do not add an action**:
the `CHECK` vocabulary (`schema.sql:125`) would force a `SCHEMA_VERSION` bump. Then run `confirm` and
`adjust_usage` in one `db.transaction()`, as `transfer.copy` does (`transfer.py:115-127`). Put this
in the engine, not the adapter: the engine owns counters and the journal, and layering (§ 9) allows
it. **This cycle should bracket the pull.** It is the only mutation R9 leaves reachable, the work is
small, and it keeps the Known-open-violations row ("re-pack and re-sync … no intent bracket") from
growing to cover the pull. Re-pack and resync stay with `issues/archive-journaling-and-locking.md`
and stay unreachable under R9.

## 4. When the bundle is first read

The pull happens at **engine start on a first start**. After the attach loop, `start` calls
`self.source.stat('/')` when the catalog has no root (`overlay.py:160-161`). That runs `index()` and
then `_populate` (`archive.py:290-299, 450-452`). The durable tier is already attached by then, so
its marker and `.terrace/` exist. On a later start the catalog root exists and the pull is lazy, on
the first `readdir` or `getattr` miss. If the bundle is already there, nothing is pulled and the
cached `archive_index` loads. Recommendation: the engine calls an explicit `ArchiveTier.stage()`
(bracketed and counted) **on every start**, before line 160, so that no counted pull happens lazily.
A pull that `index()` still makes mid-mount (bundle deleted while mounted) stays unbracketed. Record
it as a residual owned by `issues/eviction-one-hop.md`, which builds reclaim and re-pull. The R7 test
need not observe absence before the read. It should assert: no file outside the tier roots and the
state directory; exactly one regular file under the durable root's `.terrace/bundles/`; counters equal
`(st_size, 1)`, checked **before any member read**, or with reads landing on a faster tier as in the
loop stack, since `eligible_tier` (`overlay.py:217-224`) goes fastest-first; and
`readdir('/')` names equal the archive's top-level members.

## 5. Eviction

The evictor walks **catalog residency rows** (`catalog.least_recent`, `engine/evict.py:125,143`) and
deletes by entry path (`evict_copy`). It never lists a tier, so it can neither find nor delete the
bundle. It does read the **counters** for pressure (`evict.py:97-107`). If the bundle alone exceeds
the durable tier's low watermark (`quota.size * low`), every tick evicts every clean copy on that tier
and stays above low. `_drop_clean` stops when it makes no progress, and `on_evict` returns `WAIT`
when nothing was evicted (`evict.py:183-186`). The result is copy thrash on the durable tier, not a
spin. `_drop_dirty` finds nothing because R9 refuses writes. Admission (`overlay.py:217-221`) skips
the full durable tier, and the fallback (`:226-229`) stages on the fastest passthrough tier anyway,
so reads still succeed. The loop is unaffected: the helper gives `scratch` 64 MiB
(`tools/bundle_loop.py:52,210`) against a bundle of about 10 to 20 KB, and stage 3's `--fastest-size`
applies only to `shm` (`:210`; `tests/loop/stages.py:303`). Whether a bundle larger than the quota is
refused or warned about belongs to `issues/eviction-one-hop.md`, which is out of scope. At most, log
a warning at start.

## 6. Adapter tests if `staging` is redefined

Construction sites: `tests/tier/test_archive.py:362` (the `staged()` helper, used at `:378`, `:390`,
`:406`) and `:512` (S3). `:365-368` asserts the `ValueError` when the working path is missing.
`:380` and `:513` assert `work.exists()`. No other caller exists in `src/`, `tests/` or `tools/`.
**Recommended shape:** rename the keyword to `bundle: Path` (the full path), and add one derivation
function (e.g. `bundle_path(durable_root, source_name)` next to the constant) that the engine and the
loop helper both call. The adapter stays ignorant of tier layout, the test edits are a keyword rename
plus the `:365` message, and the tests keep their own `tmp_path` work file. Passing a `PosixTier`
or root and deriving inside the adapter instead couples `tier/archive.py` to the durable tier's
layout and changes every site's arguments. Downstream of the rename: `tools/bundle_loop.py:200-204`
must stop writing `staging` (R8). Its `loop.staging` attribute, which stage 8 reads
(`tests/loop/stages.py:402`) and `tests/loop/test_helper.py:76,85,92-93` assert lies in no tier root,
should become the derived path inside `scratch`'s root, and those assertions flip.

## Recommendations

1. Bundle at `<durable root>/.terrace/bundles/<source tier>.tar`, with a named constant and one
   derivation function. Do not use a root-level name.
2. Count it with one `adjust_usage(durable, bytes=st_size, files=1)`, in the same transaction as the
   pull's `confirm`, only when the pull created the file. Give it no residency row.
3. Amend `PosixTier.usage()` to count `.terrace/bundles/*.tar` (human gate). Otherwise record option
   (b) as a deviation owned by `issues/audit-command.md`. Narrow AGENTS § 4's `usage()` line to match.
4. Bracket the pull in the engine (`'promote'`, entry NULL, tier = durable). This needs no schema
   change. Run an explicit `stage()` on every start, before the root `stat`.
5. Route the pull's temp through `.terrace/partial/` (optional temp-directory argument on
   `PosixInnerLocation`). Otherwise `usage()` must skip `.*.tmp` under `bundles/`.
6. Rename `staging` to `bundle` on `ArchiveTier`. Update `test_archive.py:362,365-368,512`; the helper
   derives `loop.staging` inside the durable root, and the helper test's "in no tier root" assertions flip.
7. Record as residuals: a mid-mount lazy re-pull is unbracketed and recounted
   (`issues/eviction-one-hop.md`); a bundle larger than the durable quota thrashes that tier's copies
   (same seed).
