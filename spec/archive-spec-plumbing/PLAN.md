# PLAN — Make archive sources reachable from a spec file

> **Status:** Draft for review · **Last updated:** 2026-11-28
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) ([`00-digest.md`](research/00-digest.md) first). Every design element traces to a GOAL R-ID.

## 1. Summary

Open the archive adapter from the engine side first and from the parser last. Three engine slices
come first, and none of them is reachable from a spec file yet:

- the engine builds an `ArchiveTier` over the spec's inner location and registers it with no marker;
- a tar.gz bundle is pulled, under the journal and counted, into the durable tier's `.terrace/`;
- every mutating engine call over an archive source fails `EROFS`.

The parser then admits the archive types and keys and refuses every stack the engine can't serve. The
bundle loop's stages 1 and 2 pass, and the stops of stages 3–9 move in the same phase. A last phase
adds the FUSE test and makes the documents and figures true. Five phases fit the big appetite. The
order means the gate opens only once reads work and writes are refused.

## 2. Design

### 2.1 Spec parser — `src/terrace/tier/spec.py` (R1–R5)

- **Keys and types.** `TIER_KEYS` gains `location`, `sync` and `sharded`. `staging` stays out of it, so
  `TierSpec.from_data` called alone still refuses `staging` as unknown, and `OverlaySpec.from_data` scans
  the raw tables for it first (below; adversarial review L2). `SUPPORTED_TYPES`
  becomes the five types, and the dead "not yet supported" branch is deleted (the constant stays, as it
  is exported). New constants `ARCHIVE_TYPES = {'zip', 'tar', 'tar.gz'}` and `SYNC_KEYS =
  {'max_frequency'}`. `spec.py` does not import `tier/archive.py`.
- **`TierSpec`** gains `location: Optional[LocationSpec] = None` and `max_frequency: Optional[Rate] =
  None`. Six test files build `TierSpec` by keyword, so both default. For an archive tier, `path` is
  `location.path` (POSIX) or `location.url` (S3), the same convention an S3 tier's url follows. `url`
  and `s3` stay `None`.
- **Tier-local refusals** run in `TierSpec.from_data`, in this order, after the unknown-key and type
  checks:
  1. `sharded`: a non-bool is `Invalid boolean for tier 'X'.sharded`. `true` raises the exact text
     `Sharded archive sources are not yet supported (tier 'X'); see issues/sharded-archive-sources.md`.
     `false` is accepted on an archive type. This runs before the location is read, because the loop's
     sharded url carries a `*`.
  2. **R3**: an archive type whose `role` is not `source` is refused, naming the tier.
  3. The location: an archive tier requires `location` (a table, through `LocationSpec.from_data`) and
     refuses top-level `path`, `url` and `s3`. A `posix`/`s3` tier refuses `location`.
  4. `sync`: allowed only on `tar.gz`, which carries `max_frequency` through a `_rate` wrapper that
     type-checks before `parse_rate`. `parse_rate` raises `AttributeError` on a non-string. On `zip` or
     `tar`, `sync` is refused naming the tier, and on any other type too.
- **Stack-level refusals** run in `OverlaySpec.from_data` and `_validate_stack`:
  - **R2 `staging`** is checked first, from the raw tables, before any `TierSpec` is built. The message names `staging`, the tier that
    carries it, and the tier that holds a staged bundle under Decision 1: the shared POSIX stage tier
    directly below the source. When the stack has no such tier, the message says so (deviation row D3).
  - **R4** runs only over an archive source. `tiers[1]` must exist, be `role = stage`,
    `scope = shared` and `type = posix`, and no tier in `tiers[2:]` may be shared. Each refusal names
    the tier at fault, or the missing one.
- **R5.** The overlap and state-directory checks compare `os.path.realpath` keys for POSIX tiers, POSIX
  archive files and the state directory. S3 keys (a tier's url, or an archive's S3 url) stay
  `PurePath(url)` and are never resolved. An archive's key is its file path, so a stage root that
  equals or contains the file overlaps it, and a sibling directory does not. Stored paths stay
  unresolved, and the existing message prefixes stay; the resolved paths may be appended.

### 2.2 Engine construction and registration — `engine/overlay.py` `start`, `state/registry.py` (R6)

- **`TierRegistry.enlist(name, *, position, type, role, scope, meta)`** sits beside `_attach` and
  leaves `_attach`, `attach` and `attach_tier` untouched. It calls `register` (spec drift is caught as
  today), and on a first registration it writes `meta` (JSON) into `tier.meta`. On a re-registration it
  compares the stored `meta` with the one given and raises `SpecDrift`, naming the tier and both
  values, on a mismatch. It returns `(record, MarkerStatus.OK)`: an archive source carries no marker,
  and registration records it by type (Q1, Option A). `meta` is
  `{"location": inner.identity, "signature": [size, mtime]}`. Comparing `signature` is open question
  O1; if it is declined, only `location` is compared.
- **`start`**, for a tier whose type is in the archive types:
  1. Function-local import of `ArchiveTier`, `inner_location` and `bundle_path` (§ 9).
  2. Build `inner = inner_location(tier_spec.location)`. If `inner.signature()` is `None`, raise
     `EngineError('Source archive does not exist: <identity>')`, mirroring `Source root does not
     exist`.
  3. For tar.gz, the bundle path is `bundle_path(Path(spec.tiers[position + 1].path), tier_spec.name)`,
     computed with no disk access. R4 guarantees that tier.
  4. Build `ArchiveTier(name, inner, db, type, bundle=…, max_frequency=tier_spec.max_frequency)` and
     call `registry.enlist(...)` before anything indexes. `archive_index` is FK-bound to the `tier`
     row.
- **No marker, no writes beside the archive.** No `PosixTier` and no `.terrace/` is made for an
  archive source. `PosixInnerLocation.atomic_write` is reached only by `_repack`/`resync`, which R9
  makes unreachable.

### 2.3 The staged bundle — `tier/archive.py`, `engine/overlay.py`, `tier/posix.py` (R7)

- **Place.** `<durable root>/.terrace/bundles/<source tier name>.tar`. A constant `BUNDLE_DIR =
  'bundles'` sits beside `PARTIAL_DIR` in `state/journal.py`, and `bundle_path(root, source)` lives in
  `tier/archive.py`. `.terrace` is the only root name no overlay entry can take (`_member_path`, the
  POSIX `_internal`), and the engine's `readdir` lists only what the source supplied.
- **Adapter.** The constructor keyword `staging` becomes `bundle`, and tar.gz still requires it. A
  public `stage() -> bool` runs `_populate` and returns whether it created the file. `_populate` and
  `atomic_write` are unchanged, so the pull's temp is a sibling inside `.terrace/bundles/`, hidden and
  inside the tier root.
- **Engine.** After every tier is attached and before the root `stat`, `start` calls
  `_stage_bundle()` when the source is tar.gz. It runs on every start:
  1. `intent = journal.begin('promote', durable, now=…, detail={'bundle': …, 'source': …})`, with no
     entry. That is the existing vocabulary, so no `SCHEMA_VERSION` bump.
  2. `created = source.stage()`. If it raises, roll the intent back and re-raise.
  3. In one `db.transaction()`: `journal.confirm(intent)` and, only if `created`,
     `registry.adjust_usage(durable, bytes=<bundle st_size>, files=1)`.

  The bundle gets no residency row: the evictor walks residency rows and must never see it.

  The intent's `detail` is `{'kind': 'bundle', 'bundle': <path>, 'source': <tier>}`. Before staging,
  `_stage_bundle` checks for a pending intent naming this bundle: an earlier pull that landed but crashed
  before its confirm. It counts the bundle if the file is there, and resolves that intent in the same
  transaction, so a crash in that window cannot leave the bundle uncounted forever (adversarial review
  M3).
- **Oracle.** `PosixTier.usage()` also counts the regular files named `*.tar` directly in
  `.terrace/bundles/`. It still excludes `marker`, `partial/` and dot-named temps. AGENTS § 4's
  `usage()` bullet is narrowed in the same commit (deviation row D2).

### 2.4 The read path — `tier/archive.py` (R8)

The engine's read path serves an archive source as it serves an S3 source (brief 06): `stat` and
`list` from the index, promotion through `open_read`, and `pread` on the stage. The changes are all
in the adapter:

- `_stat_of` (and the synthesized ancestors) report `0o755` for a directory with no mode. Without
  it, every member below such a directory fails `EACCES` at promotion.
- `_scan_zip` and `_scan_tar` keep the last member of a duplicated name, as extraction does. That
  removes a `sqlite3.IntegrityError` escaping `readdir`.
- A tar hard link reads as a regular file with its target's bytes, and a sparse member reads through
  `tarfile`'s own extraction rather than by offset. Device, FIFO and character members are still
  skipped, under v1's special-files exclusion (deviation row D4).

### 2.5 Read-only over an archive source — `engine/overlay.py` (R9)

- `self.read_only = spec.source.type in ARCHIVE_TYPES`, set in `__init__`. It is named generically so
  `issues/read-only-mount.md` can set it later.
- A private `_refuse(path)` raises `OSError(errno.EROFS, os.strerror(errno.EROFS), path)`. One guard
  statement, `if self.read_only: self._refuse(path)`, is the first statement of `mkdir`, `create`,
  `unlink`, `rmdir`, `rename` (before `getattr(old)`, so the synchronous `_purge_source` path is never
  reached), `write`, `truncate` (both forms) and `_set_meta` (which covers `chmod`, `chown` and
  `utimens`). No existing statement moves.
- `open` refuses `flags & O_ACCMODE != O_RDONLY`, or `O_TRUNC`, before `_ensure`/`_serve`, so a write
  open promotes nothing and leaks no handle. An `O_RDONLY` open and its promotion are unchanged.
- `fsync`, `release`, `commit_*`, `flush` and `evict_copy` stay unguarded: with nothing admitted, they
  have nothing to act on, and the R9 test proves it by running them.
- **FUSE.** mfusepy returns `-errno` for an `OSError`, so the kernel sees `EROFS`. `link` stays
  `EPERM`, and `setxattr`/`removexattr` stay `ENOTSUP` (mfusepy's defaults). The documents say so
  rather than claiming `EROFS` for every write.

### 2.6 The bundle loop — `tools/bundle_loop.py`, `tests/loop/` (R8, R2)

- **Helper.** It drops the `staging` attribute and line. The stack is unchanged: it already meets R4
  and R5.
- **`test_helper.py`.** The `staging` assertions (`:76`, `:86`, `:92-93`, `:98`) become: `'staging'
  not in source`, and no `staging` attribute, in both forms.
- **`stages.py`.**
  - Stages 1 and 2 lose `@owned`, and their `STAGES` entries become `spec/archive-spec-plumbing/GOAL.md`,
    which survives landing. The audit checks that every entry's file exists.
  - `reach` pins only the sharded form, as `blocked('issues/sharded-archive-sources.md', SpecError,
    <the exact R2 text>)`. `PARSER` and `REFUSAL` are replaced, and `__all__` follows.
  - `change()` pins its first mutating call as `blocked('issues/archive-journaling-and-locking.md',
    OSError, "[Errno 30] Read-only file system: '/bundle-000/member-000.txt'")`, which stops stages 4–8.
  - Stage 3 pins its `pushed` assertion with `blocked('issues/eviction-one-hop.md', AssertionError,
    <the measured text>)`.
  - Stages 3, 7 and 8 call `engine.stop()` in a `finally`.
  - Stage 8 checks the bundle at `bundle_path(<scratch root>, 'bundles')`.

  Stage owners (`STAGES` for 3–9) are unchanged.

### 2.7 Mount test — `tests/fuse/` (R10)

- `start_mount` takes an optional `spec`. Its thread target records an exception from `Mount.serve`,
  and the wait fails the test with it instead of skipping on the timeout. This holds for every
  integration test.
- `tests/fuse/test_integration.py::test_zip_source_mount_readdir_lists_top_level_members` builds a ZIP
  with explicit modes (`top.txt`, `sub/`, `sub/nested.txt`) and parses a TOML spec through
  `OverlaySpec.parse`: a zip source over a posix location, plus one shared POSIX stage. It mounts and
  asserts `sorted(os.listdir(point)) == ['sub', 'top.txt']`.
- `tests/fuse/test_adapter.py::test_zip_source_readdir_through_the_adapter`, under `unit`, does the
  same through `OverlayOperations` with no mount, and also asserts `getattr('/')` and `statfs('/')`.

### 2.8 Documents (R11)

- **`PLAN.md` § Configuration** (`:1225-1233`). The "no archive example loads" paragraph says the two
  single-bundle examples load and the sharded one is refused, naming its seed. The working-copy
  paragraph is removed.
- **ArchiveTier** (`:653-655`). The `staging` note is replaced by the tree's actual behaviour: the
  bundle lives under `.terrace/bundles/` and is pulled at mount.
- **§ What the tree does not yet do.** The Decision 1 row (`:290`) narrows to the reclaim rule, owned
  by `issues/eviction-one-hop.md`. The Decision 7 row (`:295`) narrows to the tier a soft unmount
  commits to, owned by `issues/shutdown-rescue-gaps.md`.
- **§ Managed locations** (`:825-831`). An archive source carries no marker while writes over it are
  refused, and what the overlay owns over a tar.gz source sits under the durable tier's marker.
- **`AGENTS.md`.**
  - The § Known open violations row for `issues/archive-spec-plumbing.md` is removed. The two archive
    rows owned by `issues/archive-journaling-and-locking.md` say the defects are unreachable while R9's
    refusal stands, and "staging copy" becomes "staged bundle".
  - § 4's `usage()` bullet changes (in P2).
  - § Verification's loop paragraph, the `tests/` row and § Commands' helper paragraph state the new
    stops (in P4).
  - The figures change: the test counts, the integration count 19 → 20, and the timings.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | § 2.1 keys, `TierSpec.location`/`max_frequency`, the `_rate` wrapper, `sync` refused on zip/tar; the two examples load verbatim |
| R2 | § 2.1 stack-level `staging` refusal; the `sharded = true` refusal with its fixed text; § 2.6 stage-9 pin |
| R3 | § 2.1 refusal 2 in `from_data` |
| R4 | § 2.1 stack rule in `_validate_stack`; § 2.4 means a loaded spec always has a passthrough POSIX stage |
| R5 | § 2.1 realpath comparison keys; archive key is the file path; S3 unresolved |
| R6 | § 2.2 `enlist`, the archive branch of `start`, the missing-archive refusal; § 2.5 makes "nothing written beside" hold |
| R7 | § 2.3 `bundle_path`, `stage()`, `_stage_bundle` (journaled, counted), `PosixTier.usage` |
| R8 | § 2.4 adapter fixes; engine tests over three formats; § 2.6 stages 1–2 unmarked, helper without `staging` |
| R9 | § 2.5 guards and the `open` refusal; the R9 test enumerates public engine methods |
| R10 | § 2.7 the integration test, the fail-not-skip `start_mount`, the unit companion |
| R11 | § 2.8 |

## 3. Invariant gate (AGENTS.md constitution check)

**Gate #1, before research.** The change touches § 1 (intent → act → confirm: the bundle pull
mutates a managed location), § 4 (adapter atomicity; the `.terrace` tree; the `usage()` oracle),
§ 8 (marker identity; never rewritten), § 9 (lazy obstore import; layering), § 11 (failures are
`OSError`), § Conventions (same-commit rules, no R/P ids, `pytestmark`, integration gates assert
counts) and § Known open violations. High-blast-radius paths it touches:

- `tier/spec.py` `_validate_stack`, `SUPPORTED_TYPES`, `TIER_KEYS`;
- `engine/overlay.py` `write`, `truncate`, `create`, `rename`, `open` (guards only), with `start`,
  `mkdir`, `unlink`, `rmdir` and `_set_meta` off the list;
- `state/registry.py`: a new method beside `_attach`, with `_attach` itself unchanged;
- `tier/posix.py` `usage`;
- `tier/archive.py`: `_populate` is called but unchanged, and `index` is unchanged.

So trigger 2 fired. Trigger 3 fired too: the loop's progress record, the `start_mount` fixture and the
phase gates are verification harness. The intent is sane against each section: nothing is at odds
with an invariant.

**Gate #2, after design:**

- **§ 1.** The bundle pull is bracketed: `begin` before any bytes move, then `confirm` and
  `adjust_usage` in one transaction. Re-pack and re-sync stay unbracketed and unreachable (R9), and
  stay with `issues/archive-journaling-and-locking.md`.
- **§ 2.** Untouched: no pragma or `SCHEMA_VERSION` change. `tier.meta` and a NULL-entry intent are
  already in the schema.
- **§ 4.**
  - The bundle is written by the adapter's existing temp+fsync+rename.
  - The `.terrace` tree still reads as absent and refuses mutation; the bundle is inside it.
  - `usage()` now counts the bundle, which Decision 1 says the tier's limits count. The § 4 text
    changes with it (D2).
  - `delete` idempotency is unchanged.
- **§ 5, § 6, § 7.** No queue or eviction behaviour changes. The evictor walks residency rows, so it
  never sees the bundle.
- **§ 8.** No marker is written or rewritten for any tier. The archive source's identity check stands
  in for a marker's swap detection, without I/O at the location.
- **§ 9.** Archive classes are imported inside `start`. `tier/` does not import `engine/`.
  `posix.py` reads a constant from `state/journal.py`, an existing peer import.
- **§ 10.** No module-global mutable state. The read-only flag is set once in `__init__`.
- **§ 11.** Every refusal over the API is an `OSError` (`EROFS`). The `IntegrityError` escape on
  duplicate names is removed.
- **Conventions.** No spec ids in `src/` or `tests/` (the gate scans added lines). New modules declare
  `pytestmark`. Figures change in P5. `AGENTS.md` text changes in the commit that changes the
  behaviour.

### Deviation justifications

| # | Deviation | Why needed | Simpler alternative rejected because |
|---|-----------|-----------|--------------------------------------|
| D1 | One guard statement added at the top of five high-blast-radius engine methods (`write`, `truncate`, `create`, `rename`, `open`); no existing statement moves (R9). | R9 requires every mutating call to fail; no funnel below the API avoids the listed bodies (`_change`, `_remove`, `_vacate` are listed too). | A read-only switch in the adapter alone lets the engine admit and mark dirty first: a later drain fails mid-replay instead of the call failing. |
| D2 | `PosixTier.usage()` (high-blast) counts `*.tar` directly in `.terrace/bundles/`; AGENTS § 4's "`usage()` … must exclude managed internals" is narrowed to except the staged bundle (R7). **Flagged: invariant text change, human gate.** | R7 and Decision 1 count the bundle in the tier's limits; § 4 makes `usage()` the oracle the counters are rebuilt from, so a counter that counts it and an oracle that does not disagree by construction. | Leaving `usage()` alone: the first audit rebuild would drop the bundle's count, and the cycle would ship a counter-oracle disagreement § 4 forbids. |
| D3 | R2's `staging` refusal, when the stack has no shared POSIX stage tier directly below the source, says the stack has none, where otherwise it names that tier (R2). **Flagged: R2 reads "naming … the tier that holds the staged bundle"; with no such tier there is none to name.** | The refusal itself is unconditional and always names `staging` and the carrying tier; only the holder's name is absent when it does not exist. | Running R4 first would refuse such a spec without ever naming `staging`, which R2 requires. |
| D4 | Device, FIFO and character members of a tar are not listed. Neither are members the adapter already drops as unsafe: names under `.terrace/`, names with `..`, and the root itself (`_member_path`) (R8). **Flagged: R8 says "the archive's member names".** | `AGENTS.md` § Out of scope for v1 excludes special files from the overlay. `.terrace` is the reserved managed tree on every tier (§ 4), and a `..` name escapes the root. Hard links and sparse members, which are regular data, are served. | Listing special files would admit what the engine cannot serve. Listing `.terrace` or `..` members would let an archive forge the managed tree or escape it. |
| D5 | `TierRegistry.enlist` records the location identity and signature in `tier.meta` and refuses a mismatch on re-attach (R6). **Flagged: open question O1.** | With no marker, nothing else notices a spec that points an existing tier at another archive, or an archive rewritten between mounts; the cached index then serves wrong bytes silently (TAR reads by offset). | Option A (register, treat as OK, record nothing): the swap reads OK and serves the old index. |
| D7 | `fsync`, `release`, `flush`, `commit_row`, `commit_upstream`, `commit_to_shared` and `evict_copy` are not refused over an archive source; they succeed as no-ops (R9). **Flagged: a reading of "every mutating call".** | They mutate nothing a user asked for. They act only on admitted writes, and with every write refused there are none; the R9 test runs each one and checks the archive is unchanged. Refusing them would break `release` after a read, and the drain and eviction pumps at shutdown. | Refusing them too: a read-only mount could not close a file it read, or unmount. |
| D6 | The tar.gz bundle is pulled at engine start (journaled, counted) when absent, not lazily on the first member read (R7). | The engine owns the journal and the counters; a pull made inside the adapter's `index()` cannot be bracketed or counted. The tree already pulls at a first start. | Lazy pull (brief 02): unbracketed, uncounted, or an adapter-to-engine callback. |

Each row was re-read against the wording of R2, R6, R7, R8 and R9. D7 reads "mutating call" as a call that changes the namespace, the bytes or the metadata of an entry. D3 and D4 choose a reading of an
edge that R2's and R8's wording does not reach, and they go to the sign-off as flagged rows. D5 adds a
refusal to R6's second start for a changed archive only, so it goes as open question O1.

## 4. Rabbit holes (resolved)

- **Unknown-key ordering** → no reorder; three keys are admitted and refused by name, and `staging` is found by a raw scan
  ([01](research/01-parser-and-stack.md) § 1).
- **Symlink-resolved overlap without breaking S3 or missing paths** → realpath keys for POSIX only;
  non-strict realpath resolves the existing prefix ([01](research/01-parser-and-stack.md) § 3).
- **An archive with no root for a marker** → Option B, `enlist`, and identity in `tier.meta`
  ([02](research/02-engine-attach.md) § 1).
- **Where the bundle lives, how it is counted and hidden** → `.terrace/bundles/`, `adjust_usage` at the
  confirm, `usage()` amended ([03](research/03-staged-bundle.md)).
- **Lazy against eager pull** → eager, journaled (digest decision 4).
- **Every mutating entry point** → eight guarded methods plus `open`; a `write` on a read-only handle
  marks dirty before `pwrite` fails, so it is guarded too ([04](research/04-read-only.md) § 1).
- **The read path** → works; one blocker (directory mode) and one escape (duplicate names) fixed in the
  adapter ([06](research/06-read-path.md)).
- **The loop's next stops** → stage 3 at the eviction assertion, 4–8 at EROFS in `change()`, 9 at the
  sharded refusal; the audit's seed-existence check needs a surviving path for stages 1–2
  ([05](research/05-bundle-loop.md)).
- **R10 on macOS** → skips by the module's platform gate; a start failure on the worker thread would
  have read as a skip on Linux, now it fails ([07](research/07-fuse-integration.md)).
- **Adversarial review (trigger 3).** It ran once, on the drafted PLAN, TECH and gate. It found two
  HIGH, four MEDIUM and seven LOW findings, and every one was folded in.
  - *H1:* the count floor could be met by deleting tests, and the loop's harness was unguarded. The lane
    now requires every test that passed at the base to pass (`verify/base-passed.txt`), and `harness`
    freezes the loop's support files and shared steps.
  - *H2:* a one-sided R5 resolution passed every row. The symlink rows now cover the source side, the
    stage side, both sides, an archive's parent directory and a tier holding the state directory, and
    the `realpath` mutant must fail all of them.
  - *M1:* the example rows could test edited copies. The `examples` mode now parses the two blocks from
    `PLAN.md` itself.
  - *M2:* R11 missed passages. P5 now covers `AGENTS.md:31-32`, ArchiveTier's "on first access" and
    "re-packs" sentences, and the two decision rows, and `docs` checks the retired sentences.
  - *M3:* a crash between a pull and its count left the bundle uncounted forever. § 2.3 now recovers it.
  - *M4:* no mutation check. Four mutants now run, each confirmed to take effect.
  - *L1:* D7 added and D4 widened. *L2:* `staging` kept out of `TIER_KEYS`. *L3:* the `ids` pattern
    widened. *L5:* a ledger note. *L6:* the drive and stage checks made exact. *L7:* the `_loop_error`
    skip kept.
  - *L4* needed no change: `parse_rate` already refuses `0/day` (brief 01).
- **No prototype.** The skill asks for one in a detached worktree, and no prototype was built. The
  rows each gate names are tests build writes, so the gates were dry-run on the plan tree only (red, by
  named-row absence, as § 6 records). The adversarial review is the only check of their green direction.

## 5. Risks & open questions

- **O1 (for the sign-off).** Should `enlist` also refuse a changed **signature** (the same file,
  rewritten between mounts)? *Recommendation: yes.* Read-only sources make an out-of-band rewrite the
  only way the cached index goes stale, and TAR offset reads then return wrong bytes silently. The
  refusal names the tier and points at a fresh state directory. The recovery (reindex) stays with
  `issues/archive-finalization-integration.md`. If declined, P1 drops the signature half of
  `enlist`'s comparison and its test row, and P5 records the risk in that seed's notes.
- **O2 (for the sign-off).** D2 changes § 4's text. *Recommendation: accept.* The alternative is a known
  counter-oracle split.
- **The stage-3 pin text** is pytest's assertion rendering (`nothing was pushed out of the fastest
  tier\nassert []`, measured by brief 05). The P4 gate checks its prefix and cause, not the exact
  rendering.
- **Residuals, recorded by P5's ledger:**
  - A bundle deleted out of band during a mount is re-pulled by `index()`, unjournaled and counted
    twice → `issues/eviction-one-hop.md`.
  - A bundle deleted between mounts is re-pulled and counted again → `issues/audit-command.md`.
  - A bundle larger than the durable tier's low watermark makes that tier's clean copies evict every
    tick (reads still succeed) → `issues/eviction-one-hop.md`.
  - A corrupt archive raises `BadZipFile`/`ReadError`, not `OSError` → `issues/engine-error-mapping.md`.
  - An S3 source's mode-less directories hit the same `EACCES` → the new seed
    `issues/source-directory-mode.md`.
- **The tar.gz pull at mount** costs one whole-bundle transfer on a first start (D6). PLAN's
  ArchiveTier text says "on first access"; § 2.8 rewords it to "at mount".
- **R10 is unproven on this host.** No Linux FUSE host was used, and none may be: R10 is graded
  later, on a Linux host the foreman arranges (TECH P5).

## 6. Verification strategy

One gate script, `spec/archive-spec-plumbing/verify/check_outcomes.py MODE`, in the style of
`spec/local-bundle-loop/verify/`. Each phase mode:

- runs the phase's scoped tests and requires every named row to have **passed**, by id;
- plants each mutant due by the phase and requires its guarding rows to fail;
- runs the whole unit lane and requires **no failure or error**, the exact expected-failure set, every
  test that passed at the base to pass, and a passed count no lower than the baseline (842) plus the
  rows the cycle added so far;
- scans the cycle's added lines in `src/`, `tests/` and `README.md` for spec ids.

From P4 on, `harness` holds the loop's support files and shared steps to the base's, and `examples`
parses the two `PLAN.md` examples as written.

From P4 on, it also reads each loop stage's recorded `stopped`, `cause` and `marker`. `tools/lint.sh`
runs before it in every `verify:`.

Driving the CLI: P4's mode loads the helper's single-form spec through `tools/temp_site.sh` with the
helper's command, and asserts the source type and the durable tier's name. A drive of a sharded spec
asserts the R2 refusal text.

**Dry run on the plan tree** (`62225d2` plus the plan files): every phase mode goes red, and each
part mode fails the way its predicate expects. The log is in the step's scratch directory,
outside the repository.

| Part | Base result |
|---|---|
| `rows-p1` | 0 of 17 named rows ran. |
| `lane-p1` | 842 passed and 9 xfailed, against a floor of 859. |
| `lane-p4` | 9 xfailed where 7 are expected, against a floor of 945. |
| `stages` | All nine stop at the parser's unknown-key refusal. The recorded `stopped`, `cause` and `marker` properties were present: the precondition holds. |
| `drive` | The parser refuses both forms with the unknown-key text. |
| `examples` | The ZIP block was found in `PLAN.md` and refused at parse. |
| `mutants-p5` | The `counter` plant fails for want of `ArchiveTier.stage`. The other mutants' rows are absent. |
| `collect`, `docs`, `ledger` | Each fails on what P5 has not yet written. |

Four parts pass on base, which shows their extraction and patterns hold:
- `harness`: 7 files and 8 shared steps found and equal.
- `ids`: 0 lines.
- `figures`: 851 unit, 19 integration, 437 engine and 54 files, matching `AGENTS.md`.
- `base-passed.txt`: 842 ids, all passing.

The green direction is checked only by the adversarial review and by build's first run.

What no gate on this host can see, and who checks it:

- **R10.** A person or the foreman's Linux run (`check_outcomes.py r10` on a host with `/dev/fuse`)
  must see the `PASSED tests/fuse/test_integration.py::test_zip_source_mount_readdir_lists_top_level_members`
  line. Without such a host the review records R10 as ungraded.
- **R11's prose.** The reviewer reads the four `PLAN.md` passages and the `AGENTS.md` rows against the
  tree. The gate checks only that the retired sentences are gone.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
