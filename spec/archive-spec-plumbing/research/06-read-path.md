# 06 — The engine read path over an archive source (R7 listing, R8)

Probe run 2026-11-28 on the worktree: a spec from `tests/engine/conftest.py` `build_spec`, the engine
started, then `engine.tiers['project']` swapped for an `ArchiveTier` over a ZIP, a TAR and a tar.gz
(staging at `<scratch root>/bundle.tar`), with 2 and 3 tiers. Lab removed after.

## 1. Call trace (S3 precedent; an `ArchiveTier` source answers the same calls)

- **start** (`overlay.py:160-164`): on a first start only, `source.stat('/')` builds the root entry;
  `None` mode/uid/gid/ctime/atime fall back to `0o755`/0/0/mtime. `ArchiveTier.stat('/')` returns
  `TierStat('dir', 0, 0.0)` (`archive.py:464-465`) and calls `index()` first (`:462`), so a tar.gz
  bundle is pulled **at the first start**, not on first read (`_populate`, `:414-425`); a second start
  skips the stat (the root entry exists) and indexes lazily.
- **readdir(p)** → `getattr` → `_ensure` (`:235-256`: `_named`, recursive parent `_ensure`,
  `source.stat`, `_adopt`) → `_populate` (`:299-316`): `source.list(p)`, then per child `_adopt`
  with `item.stat` (archive `list` always supplies one, `archive.py:483`), then `set_listed` →
  `catalog.children`. The source sees `stat` per path segment, then `list`.
- **getattr(member at depth 2), cold**: `_ensure('/a/b')` → `_ensure('/a')` → `_ensure('/')`
  (catalog) → `source.stat('/a')` → `_adopt` → `source.stat('/a/b')` → `_adopt` (adds a source
  residency for non-dirs, `:289-291`). No `list` call.
- **open** (`:686-729`): `_ensure` → `_serve` (`:746-757`): `fastest_copy` = the source residency;
  `eligible_tier(size)` (`:204-231`) picks the fastest admitting stage (no source passthrough needed:
  `_passthrough` is `hasattr(tier, 'open_direct')`, `:200-202`, which `ArchiveTier` lacks, so it
  never serves); promotion under `_source_lock` → `transfers.copy(..., src_path=_held_path)`.
- **transfer.copy** (`transfer.py:88-129`): `journal.begin` → `source.stat(read_path)` (must be a
  `file`, else `TierError`, `:99-100`) → `materialize_dirs` on the **dest** with catalog modes
  (`:29-39`) → `source.open_read(path)` → `dest.put(stream)` → residency + usage + confirm.
  `ArchiveTier.open_read` is `BytesIO(self.read(path))` (`archive.py:503-505`): the whole member
  in memory; `_extract` reads ZIP by name, TAR by `seek(offset)`/`read(size)` (`:402-410`).
- **read** is `os.pread` on the stage fd (`:808-810`); **release** closes and `_record`s against the
  stage tier (`:956-998`). The source is never opened for a handle.

Contract fit: `stat`/`list`/`read`/`open_read`/`usage` match `protocol.py:91-131` in name, signature
and return type. Mode `None` (zip without unix attrs, synthetic dirs) is legal per `TierStat`; uid/gid
are always `None` → catalog 0/0. mtime: TAR header mtime; ZIP DOS time as local time
(`_zip_mtime`); synthetic dir takes its first child's mtime (`:397-398`). Symlink members carry
`target` (tar size 0, zip size = target length). Root entry is synthetic `dir`, mtime 0.0.

## 2. Probe results

Works, all three formats (2 and 3 tiers): `readdir('/')` (names exact; the tar.gz `bundle.tar` in
the stage root is **not** listed), `readdir` of a subdir, cold `getattr` at depth 3, `open`+`read`
of top-level, empty (0 B), depth-2/3 and 9 MiB members — byte-identical; `readlink` of a tar
symlink; served tier `scratch` (2 tiers) / `tmp` (3 tiers, fastest admitting). Archive bytes
unchanged after all reads.

Fails:
- **ZIP whose directories have no unix mode (no dir entries — synthetic — or entries without
  `external_attr` mode bits)**: `open('/data/a.txt')` → `PermissionError` EACCES at the stage
  `put`'s rename (`posix.py:146`); depth 3 → EACCES at `posix.py:97`. Cause: `_adopt` defaults a
  `None` mode to `0o644` for **every kind** (`overlay.py:285`), so `/data` is cataloged `0o644`;
  `materialize_dirs` creates the stage dir with that mode (`transfer.py:37` → `PosixTier.mkdir`
  `chmod`s exact bits, `posix.py:189-192`), and nothing can be created inside a non-searchable
  dir. Same with a ZIP carrying dir entries with mode bits: passes. S3 shares the defect (its dirs
  are mode `None`, `s3.py:122,130,149`); no S3 test promotes a nested source-adopted file. Root
  hides it on a host running tests as root.
- **Duplicate member names in a TAR**: `readdir('/')` → `sqlite3.IntegrityError: UNIQUE constraint
  failed: archive_index.tier, archive_index.member` from `_persist` (`archive.py:330`,
  PK at `schema.sql:144`), reached via `list` → `index` (`:473`, `:298`). A non-`OSError` escaping
  the API (§ 11). Would also hit a ZIP with duplicate names, or `a` and `a/` in one archive.
- **Hard link in a TAR**: not listed; `getattr` → ENOENT (skipped at `archive.py:381-382`).
- `open` of a symlink → `OSError` ELOOP (`overlay.py:715`): by design (the kernel follows links).

## 3. Member edge cases against R8

| Case | Today | Bundle loop has it? |
|---|---|---|
| Empty file | OK | No (sizes 512+128·m, `bundle_loop.py:187`) |
| Large member | OK; whole member held in RAM per promotion (`archive.py:498,505`); ZIP/TAR over an **S3** inner location downloads the whole archive per `_extract` (`:151`) | No (≤ ~1.2 KiB) |
| Dir without header (synthetic) | listed OK; **promotion below it fails** (§2) for ZIP; TAR synthetic dirs same (mode `None`) | No: `tarfile.add` writes dir headers with disk modes |
| Mode `None` file | cataloged `0o644`, OK | No |
| ZIP dir entries | OK with mode bits; fails without | n/a (tar.gz only) |
| Duplicate names | **IntegrityError** | No |
| Hard link (TAR) | silently absent | No |
| Symlink | listed, `readlink` OK | No |
| Dir with mode lacking owner `wx` (e.g. `0o555`) | stage dir gets exact bits → child promotion EACCES (same mechanism; pre-existing for any source) | No |
| GNU/PAX sparse member | `isreg()` true, offset read returns the raw sparse map: wrong bytes | No |
| Archive changed between mounts | `index()` trusts `archive_index` without a signature check (`:295-296`): TAR offsets read wrong bytes silently; ZIP by stale name → `KeyError` | n/a |
| Corrupt archive | `zipfile.BadZipFile` / `tarfile.ReadError` (not `OSError`) escape, at `start` for a first start | n/a |

Bundle contents (`_generate`, `bundle_loop.py:180-186`; `pack`, `:120-125`): one top-level dir
`bundle-NNN/` with dir header, files at depth 2 and in `sub/` at depth 3, PAX format, real modes and
uids, no empty files, symlinks, hard links or duplicates. Stages 1-2 over that bundle pass the read
path as it stands (the tar probe is the same shape).

## 4. Writes to the source on the read path

None. `open` writes the catalog only (`catalog.touch`, `:725`); promotion writes the stage, the
journal and the counters; `release` → `_record` stats and updates the **stage** residency
(`:981-997`; `on_source` is never true for an archive). No `utimens` is issued by a read (the FUSE
adapter calls it only on an explicit setattr, `fuse/adapter.py:183-189`). An explicit
`engine.utimens` queues a `meta` row to the source (`overlay.py:672`) whose commit would re-pack via
`set_meta` — R9's concern, not the read path's.

## 5. Non-`OSError` escapes on this path

`sqlite3.IntegrityError` (duplicates, above); `TierError` at `transfer.py:100` if the member stats
`None`/non-file between `_ensure` and the copy (stale cache); `KeyError`, `BadZipFile`,
`tarfile.ReadError` from the adapter; `EngineError` at `eligible_tier` (`:230`) only without a
passthrough stage (closed by R4) and at `_serve` (`:748`) only on a race. `LookupError`
(`transfer.py:92`) is unreachable here (`_ensure` precedes the copy).

## Recommendations

1. **Fix the dir-mode defect** before R8 tests over ZIP: either make `_adopt`'s `None` default
   kind-aware (`0o755` for `dir`, matching `start`'s root at `:162`; high-blast-radius, fixes S3 too),
   or have `ArchiveTier` report `0o755` for synthetic dirs and mode-less ZIP dir entries (adapter-only,
   leaves S3's copy of the defect — record it). Prefer the former; say which in TECH.
2. **Dedupe members last-wins** in `_scan_zip`/`_scan_tar` (keyed by normalized path, before
   `_with_ancestors`): matches tar extraction and `zipfile`'s `NameToInfo`, and removes the
   `IntegrityError`.
3. State in TECH that hard links, devices and sparse members are not members in v1 (or resolve a
   `LNKTYPE` to its target's `offset_data`/`size`; small), so "every member" in R8 is well defined.
4. Leave the stale-cache and corrupt-archive escapes to `issues/archive-finalization-integration.md`
   / `issues/engine-error-mapping.md`, named in the § 11 row; not R8 blockers.
5. R7 aside: the staged bundle in the stage root can collide with a top-level member of the same name
   (promotion would overwrite it); a name under the stage's `.terrace/` avoids both collision and
   listing. The bundle's usage is not counted today (probe: 5 files = promoted members only).
6. **Fixtures for R8** (one per format, plus the tar.gz built from the same tar): top-level file, empty
   file, depth-2 and depth-3 files, a member ≥ 1 MiB, a directory with a header and one without
   (synthetic), a ZIP with no dir entries and no unix modes, a TAR symlink (assert `readlink`, not a
   read), and a TAR with a duplicated name (assert last wins). Assert names from `readdir` recursively
   and bytes via `open`/`read`/`release`, the archive file's bytes unchanged, and nothing promoted
   outside the stage root. Run as non-root or assert the stage dir modes, since root masks recommendation 1.
