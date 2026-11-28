# Research 02: engine construction and registry attach for an archive source (R6)

Tree: worktree `archive-spec-plumbing`. A probe ran a hand-patched `start` (Option A below) over a ZIP
and a tar.gz with one POSIX stage. Its lab was outside the repo and has been removed.

## 1. How `_attach` decides today, and how to make attach read OK by type

`_attach` (`state/registry.py:118-139`) calls `register` first (`:129`) and then reads the marker with
the caller's `read`. `MarkerError` gives CORRUPT (`:132-133`). No marker on a registered tier gives
MISSING (`:135-136`). No marker on a new registration writes one and gives OK (`:137-138`). Otherwise
it returns `Marker.status`, which can be FOREIGN, TIER_MISMATCH, GENERATION_MISMATCH or OK
(`state/lifecycle.py:194-202`).
- **POSIX:** `attach` passes `Marker.read(root)` and `marker.write(root)` (`registry.py:106-110`).
- **Owns its marker I/O:** `attach_tier` passes `tier.read_marker` and `tier.write_marker`
  (`:112-116`). Only S3 implements them (`tier/s3.py:290-300`).
- **Caller:** `start` refuses any status but OK (`engine/overlay.py:157-158`). It is the only caller
  of either attach; `grep` finds no other reader of a marker under `src/`.

Options:
- **(A) Branch in `start`.** Call `self.registry.register(...)` and set the status to OK. This
  touches neither `_attach` nor registry.py. `start` is not on the overlay.py high-blast list
  (`AGENTS.md:236-238`). The probe ran this successfully.
- **(B) New registry method beside `_attach`.** For example
  `register_unmarked(name, ..., meta) -> (TierRecord, MarkerStatus.OK)`. It leaves `_attach`
  untouched but edits registry.py, which is on the short list. Its gain: "registration records it,
  OK by type" lives in one tested place (`tests/state/test_registry.py`). It is also the natural
  home for the identity check under "Gap" below.
- **(C) Rejected: `ArchiveTier.read_marker` / `write_marker`.**
  - A no-op write followed by a `None` read gives MISSING on the second start (`registry.py:135`).
  - A read that returns a fabricated Marker needs the overlay uuid and the generation, and it forges
    exactly the identity that § 4 (`AGENTS.md:386-388`) and § 8 (`AGENTS.md:600-603`) protect.
  - Any real write lands at or beside the inner location, which R6 forbids.

(A) and (B) never call `write` for an archive tier, and every other tier still goes through
`_attach` unchanged. So § 8 ("never rewritten for an already-registered tier") holds everywhere.

**Spec drift.** `register` compares `(position, type, role, scope)` (`registry.py:98-103`). A change of
type such as zip to tar, a change of role, or a moved position raises `SpecDrift` for an archive
tier too.

**Gap: no swap detection.** `register` does not record the inner location. For a POSIX source, the
marker is what catches a swapped root: a new root has no marker, so attach gives MISSING. Under
Option A, nothing catches an archive tier whose `location` changes while its name stays the same.
The second start would then serve the old catalog and the old `archive_index` rows (keyed by tier
name, `archive.py:319`) against the new archive's bytes. TAR reads are by offset (`archive.py:408-410`),
so they return wrong bytes silently. ZIP reads are by name and raise a raw `KeyError`.

The fix needs no schema change. `tier.meta` exists and is commented "JSON adapter state (e.g., archive
file identity)" (`state/schema.sql:42`), and nothing under `src/` writes it today. Record
`{"location": inner.identity}` there at first registration, and refuse a mismatch on re-attach. Use
`SpecDrift`, or an `EngineError` that names both identities. No `SCHEMA_VERSION` bump is needed.

A changed signature (the same file rewritten out of band) is a stale index too. That is
`resync`/rebuild territory and a Non-goal. Record the signature at most; don't act on it.

## 2. What `start` must build

Construction order inside the loop (`overlay.py:139-159`):
1. `inner = inner_location(tier_spec.location)` (`archive.py:162-166`).
   - A POSIX location never imports obstore.
   - An S3 location imports `terrace.tier.s3`, which imports obstore at module scope (`s3.py:17-19`),
     only when the S3 location is built. archive.py imports no obstore at module scope.
   - Importing `ArchiveTier` and `inner_location` function-locally keeps § 9 style, as the S3 branch
     does at `:141`.
2. Refuse a missing archive, mirroring `Source root does not exist` at `:149-150`:
   `if inner.signature() is None: raise EngineError(f'Source archive does not exist: {inner.identity}')`.
   - Without this check, `_scan` indexes an absent archive as empty (`archive.py:337-338`), and
     `_populate` returns False (`:418-419`). The mount then succeeds and shows an empty root.
   - For an S3 location the check is one HEAD request.
   - The probe confirmed the refusal.
3. Build the tier and register it:
   `ArchiveTier(name, inner, self.db, tier_spec.type, staging=<bundle path>, max_frequency=tier_spec.sync rate)`,
   then register (A or B). `self.db` already exists (`:129`).
   - The constructor does no I/O (`archive.py:235-255`).
   - `index()` needs the registered row, because `archive_index.tier` references `tier(name)`
     (`schema.sql:136`). Register before anything calls stat, list or read.
   - The cycle may change the constructor's signature, since `staging` is retired by Decision 1.
     Either way the engine supplies the bundle path for tar.gz.
4. Where the bundle path comes from. The durable tier is `self.spec.tiers[position + 1]`; R4
   guarantees it is the single POSIX shared stage directly below.
   - Its root is not created until its own loop iteration (`:152`). Either derive the bundle path
     only, without touching disk, or build the archive tier after the loop.
   - `PosixInnerLocation.atomic_write` would `mkdir` the parent on its own (`archive.py:100`).
   - Choosing the name is R7's topic. Two constraints apply:
     - A path under `.terrace/` is invisible to the overlay (`archive.py:268-270`), but
       `PosixTier.usage()` excludes it, and `usage` is the rebuild oracle (§ 4,
       `AGENTS.md:389-391`). So R7 must count the bundle explicitly through
       `registry.adjust_usage`; the probe showed it uncounted.
     - The populate temp is a sibling `.<name>.<hex>.tmp` (`archive.py:101`). It is not under
       `.terrace/partial/`, so the audit would not sweep an orphan.

## 3. Populating the root entry at the end of `start`

On first start, `self.source.stat('/')` runs when the catalog has no `/` (`overlay.py:160-164`).
`ArchiveTier.stat` calls `self.index()` before it checks for ROOT (`archive.py:462-465`). So the first
start:
- scans the ZIP central directory or the TAR;
- for tar.gz, pulls and decompresses the whole archive into the bundle. The probe printed
  "bundle exists: True" right after the root stat.

On a later start the catalog already has `/`, so indexing is lazy (first `_ensure` or `_populate`,
`overlay.py:253`, `:307`).

The eager pull contradicts "No eager full scan at mount" (`PLAN.md:833-835`) and "on first access"
(`PLAN.md:643-644`). R7's "when members are first read" reads most naturally as lazy.

Smallest fix: in `ArchiveTier.stat`, return the constant root stat before calling `index()`; that
means swapping two lines (`archive.py:462-465`). `stat` is not in archive.py's table row. The engine
then defaults the root's mode to 0o755 (`overlay.py:162`).

Keep the first `index()` outside any open transaction. `_persist` opens `BEGIN IMMEDIATE`
(`archive.py:327`, `state/db.py:133`), which fails if nested. The lazy callers (`_ensure`,
`_populate`) are not inside a transaction.

## 4. Second start after a clean stop

- **Cached:** the catalog (root, the adopted entries, `listed` flags) and the `archive_index` rows for
  the tier.
- **Not cached:** the new `ArchiveTier` object has `_members=None`. Its first `index()` calls
  `_populate`, which returns False when the bundle exists (`archive.py:416-417`), and then
  `_load_cache` (`:295`). An empty cache rescans (`:296`).
- **Probe:** start #2 succeeded for both formats, and the listing matched.

Writes at or beside the inner location:
- `inner.atomic_write` runs only in `_repack` (direct-update `_loc` is the inner location,
  `archive.py:251`, `:533`) and in `resync` (`:446`). Nothing in `engine/` calls `resync`.
- `_populate` writes beside the bundle, not beside the archive.
- No `PosixTier` and no `.terrace` directory is created for an archive source, so start and read
  write nothing beside the archive. The probe listed only the archive file after both starts.

Writes reach the archive through replay today. In the probe, `mkdir` and `create` succeeded; `flush`
then re-packed the ZIP in place (its bytes changed). For tar.gz they changed the bundle and not the
source. R9's EROFS gate is what makes R6's "nothing written" hold.

## 5. Other assumptions about the source type in `engine/` and `fuse/`

- No `isinstance` checks. `PosixTier` and `S3Tier` are named only in `start` (`overlay.py:33`,
  `:141-153`).
- `_passthrough` is `hasattr(tier, 'open_direct')` (`:200-202`). ArchiveTier has no `open_direct`,
  so `eligible_tier` falls through to the POSIX stage (`:226-229`). This is correct.
- `transfer.copy` reads via `source.open_read` (`transfer.py:109`); ArchiveTier buffers the whole
  member (`archive.py:503-505`). Correct for v1.
- `fuse/adapter.py:104-114` `statfs` calls `os.statvfs(spec.source.path)`. A POSIX archive path
  (a file) works and reports that file's filesystem. An S3 url raises OSError and takes the quota
  path, which works because the `tier_usage` row exists after `register`.
- `drain.py:95` and the source-name uses in overlay.py go by name only. `evict.py:85` covers stage
  tiers only. `rescue.py` does not reach the source.

**Blocker found by the probe (R8).** Reading any member below a directory fails:

```
PermissionError: [Errno 13] … scratch/.terrace/partial/put-…-a.txt -> …/scratch/data/a.txt
```

1. Synthetic ancestor directories, and ZIP directories with no unix mode, have `mode=None`
   (`archive.py:354`, `:397-398`).
2. `_adopt` defaults every kind to 0o644 (`overlay.py:285`).
3. `materialize_dirs` creates `scratch/data` with mode 0o644 (`transfer.py:37`). The promote's
   rename into it then fails with EACCES.

Top-level members read correctly (`b'top-level'`).

`_adopt` is on the high-blast list, so fix it on the adapter side instead. `_stat_of`
(`archive.py:455-458`) should report 0o755 for a directory with no mode, or `_with_ancestors` should
set it. This touches no row of archive.py's table.

## Recommendations

1. Attach by type with no marker I/O. Prefer (B): a registry method beside `_attach` that registers
   and returns OK, with its own test. Use (A) if the human gate on registry.py is unwelcome. Never
   (C). `_attach`, `attach` and `attach_tier` stay untouched.
2. Record `inner.identity` in `tier.meta` at first registration and refuse a mismatch on re-attach.
   That is the archive's stand-in for the marker's swap detection. No schema change is needed.
3. In `start`, refuse a missing archive with `EngineError('Source archive does not exist: …')` before
   registering.
4. Build `ArchiveTier` from `inner_location(tier_spec.location)` with a function-local import. Derive
   the bundle path from `spec.tiers[position+1].path` without touching disk, and register before any
   index.
5. Make `ArchiveTier.stat(ROOT)` answer before `index()`, so the first start neither scans nor pulls,
   and the tar.gz pull happens at first read (R7).
6. Default directory mode to 0o755 in the adapter's `_stat_of`. Without that, R8 fails for every
   nested member.
7. Tests:
   - R6: start, stop, start over one state directory. Assert that `engine.source` is an `ArchiveTier`,
     that the registry row exists, and that the archive's parent directory lists only the archive,
     with its bytes unchanged.
   - Registry: OK by type, and a refusal on location mismatch.
8. R9's EROFS gate is load-bearing for R6. Without it, `flush` re-packs a ZIP source in place (seen
   in the probe).
