# Research 01 — the spec parser and `_validate_stack` (R1–R5)

Baseline: `uv run pytest -m unit tests/tier/test_spec.py` → 31 passed. Probes ran in a lab outside the repo (removed).

## 1. Minimal parser change (R1)

- **Keys.** `TIER_KEYS` (`spec.py:28`) gains `location`, `sync`, `sharded`, `staging` in one edit (Q2). With all four
  admitted, the unknown-key check (`spec.py:204`) can stay first: it still catches typos, and the
  per-type checks after it give named refusals, the pattern `_location` already uses for `url`/`s3` on a POSIX
  tier (`spec.py:236-237`). Moving the type read ahead of it isn't needed. Admitting `staging` stops it reading as
  "Unknown key", and R2 needs that.
- **Types.** `SUPPORTED_TYPES` (`spec.py:25`) becomes equal to `TIER_TYPES`, which makes the "not yet supported"
  branch (`:209-210`) dead. Keep the constant (it's exported) and drop or keep the branch, but say which. Add
  `ARCHIVE_TYPES = {'zip','tar','tar.gz'}` and `STAGED_TYPES = {'tar.gz'}` in `spec.py`. Don't import
  `archive.FORMATS`/`STAGED` (`archive.py:46-47`): archive imports spec only under `TYPE_CHECKING`, and the
  import shouldn't flip direction.
- **Fields** (all defaulted). Six test files build `TierSpec(...)` by keyword: `tests/engine/conftest.py:55,60`,
  `test_rescue.py:40`, `fuse/test_adapter.py:209`, `test_s3_engine.py:35`, `test_s3_integration.py:71`. The new fields:
  `location: Optional[LocationSpec] = None` and `max_frequency: Optional[Rate] = None`. Name it to match the
  `ArchiveTier(..., max_frequency=)` kwarg at `archive.py:236`. A `SyncSpec` table adds nothing, since PLAN names
  only `max_frequency` (`PLAN.md:705, 1201, 1218`).
- **`TierSpec.path` for an archive tier** is `location.path` when the location is POSIX, else `location.url`, the
  same convention as `path=url` for an S3 tier (`spec.py:235`). Leave `url`/`s3` as `None`: the engine branches on
  `type == 's3'` (`overlay.py:140`), and the S3 settings live in `location.s3`. `statfs` reads `source.path`
  (`fuse/adapter.py:108`). For a file path, `os.statvfs` works. For a url it raises `OSError` and falls back to the
  quota, the same as an S3 source.
- **sync.** `parse_rate` exists (`core/units.py:95-112`). It takes `N/<unit>` with the unit S|SEC…|M|MIN…|H|HR…|D|DAY…|W|WEEK…,
  or a duration (`1/6h`). It returns `Rate(count, period)`, which has `min_interval`. Probed: `'4/day'` →
  `Rate(4, 86400.0)`. `'0/day'` and `'4'` raise `ValueError`. **A non-string such as `4` or `None` raises
  `AttributeError`**, so the spec's `_rate` wrapper must check `isinstance(value, str)` or catch `AttributeError`
  as well as `(TypeError, ValueError)`, then report `Invalid rate for {where}.sync.max_frequency`. Use
  `SYNC_KEYS = {'max_frequency'}` and `_reject_unknown(..., f'{where}.sync')`; `sync` must be a table. Whether
  `max_frequency` is optional is open. The adapter accepts `None` (no limit), and every PLAN example sets it.
- **Other keys on an archive tier:**
  - Refuse top-level `path`/`url`/`s3`; the archive names its file through `location`.
  - Refuse `location` on `posix`/`s3`.
  - `quota` stays allowed: the source quota feeds the statfs fallback (`adapter.py:116-125`).
  - `scope = "node"` is refused at stack level (`:315-316`), and `writeback` on any source (`:218-219`). Both carry over.
  - `role = "stage"` is refused under R3.

## 2. Refusals: wording and where each lives

Tier-local checks go in `TierSpec.from_data`, in this order after the type check. Read `role` before `_location`,
so an archive at a stage position isn't first reported as a missing `location`.

| Check | Where | Wording (suggested) |
|---|---|---|
| `sharded` | from_data, right after type | non-bool → `Invalid boolean for {where}.sharded`. `true` → `Sharded archive sources are not yet supported ({where}); see issues/sharded-archive-sources.md`. `false` is the default: accept it silently on archive types (refusing it buys nothing). |
| R3 | from_data, after role | `Archive {where} can only be the source: type {kind!r} is not valid at a stage position` |
| location | from_data | `Archive {where} names its file with location = {{...}}, not path/url/s3` and `The location setting is only valid for archive tiers ({where})` |
| R1 sync | from_data | zip/tar: `A {kind} archive is updated in place and has no re-sync to rate-limit — sync is only valid for tar.gz tiers ({where})`. posix/s3: `sync is only valid for tar.gz tiers ({where})` |
| R2 staging | `OverlaySpec.from_data`, after the tiers parse and before (or passed into) `_validate_stack` | The holder is known only at stack level: `tiers[1]` if it exists with `role == 'stage'` and `scope == 'shared'`. Wording: `The staging key is retired ({where}): a staged bundle now lives in the root of {holder!r}, the shared stage tier directly below the source, and counts against its limits`. With no holder: `… lives in the shared stage tier directly below the source, which this spec does not have`. Run it **before** R4, so a user migrating an old spec sees the retired key first. Use the raw tables (`'staging' in table`) or a `retired=` keyword into `_validate_stack`. |
| R4 | `_validate_stack`, after the existing source checks | Archive source only. No `tiers[1]`: `Archive source {src!r} needs one shared POSIX stage tier directly below it; the spec has none`. `tiers[1]` node-scoped or not posix: `… the tier below it, {name!r}, is {scope}/{type}`. Any other `scope == 'shared'` in `tiers[2:]`: `… found a second shared stage tier, {name!r}` |

The sharded check belongs in from_data: the loop's sharded spec puts `sharded = true` beside a valid location
(`tools/bundle_loop.py:205-207`), and stage 9 pins the text exactly (`str(caught) == message`,
`tests/loop/stages.py:71-72`). Keep it one deterministic string with no paths in it. It would be the first `src/`
message to name an `issues/` path; that's allowed, since only R#/P# ids are banned (`AGENTS.md` § Conventions).
The loop stack (`STAGES`, `bundle_loop.py:49`: scratch shared, tmp node, shm node) passes R4.

## 3. R5: symlink-resolved overlap

Today the check builds `PurePath(abspath)` (`spec.py:317-324`). Probed: a stage root that is a symlink into the
source root parses clean, which confirms the defect. The design:

- **Compare resolved, store unchanged.** Build the comparison keys in `_validate_stack` with
  `os.path.realpath(tier.path)`, but only for POSIX-backed roots: `type == 'posix'`, or an archive whose
  `location.type == 'posix'`. Leave `TierSpec.path`, `LocationSpec.path` and `OverlaySpec.state` as abspath. Nothing
  persists the root: `registry.register` records only (position, type, role, scope) (`registry.py:83-104`), and the
  marker holds uuid/tier/generation under the root (`lifecycle.py:174-190`). Storing resolved paths wouldn't drift a
  state DB, but it would break `test_location_posix` (`test_spec.py:313-317`, which compares `loc.path` to
  `expanduser`) on any host whose home is a symlink. It would also change what the error messages and statfs show.
- **Non-existent paths:** in non-strict mode `realpath` resolves the existing prefix and appends the rest. Probed:
  `<symlink>/new/x` → `<target>/new/x`, and `/tmp/x/y` → `/private/tmp/x/y`. So a stage root the engine creates
  later still resolves through a symlinked parent. On 3.15.2 a symlink loop returns the path without raising
  (probed).
- **S3 urls must not be resolved.** Probed: `realpath('s3://b/k')` → `<cwd>/s3:/b/k`. Keep `PurePath(url)` (→ `s3:/b/k`,
  prefix containment still works). POSIX and S3 keys never relate to each other.
- **Archive file path:** compare the file path itself (R5). A stage root that equals or contains the ZIP is an
  overlap, which covers a stage rooted at the archive's parent directory. A sibling stage directory in the same
  parent is not an overlap. (`PosixInnerLocation.atomic_write` puts its temp beside the file (`archive.py:96`), but
  R9 keeps writes off this cycle.)
- **State dir:** resolve it the same way before the `is_relative_to` test (`:322`).
- **macOS:** pytest resolves its base temp (`_pytest/tmpdir.py:162`, `.resolve()`), so `tmp_path` is already
  `/private/var/...`. Both sides go through `realpath`, so `/tmp` vs `/private/tmp` can't cause a false overlap or
  hide a real one. The parse-only tests use non-existent `/data`, `/fast`, `/project`, `/scratch`, `/dev/shm`, which
  `realpath` leaves unchanged; `test_parse_full_spec` (`:81`) asserts `spec.state` and still passes if the state
  dir stays stored unresolved.
- **Messages:** keep the prefixes `Tier roots overlap: {a!r} and {b!r}` and `The state directory cannot live inside a
  managed tier ({name!r})`, which tests match on (`test_spec.py:265,267,273`). Appending the resolved paths
  is optional and helps the symlink case.
- **R5 test:** in `tmp_path`, `src/`, then `stage -> src/sub` via `os.symlink`; parse with `path = stage` and expect
  `roots overlap`. Add one for the state dir via a symlink too.

## 4. Existing tests that change

- `tests/tier/test_spec.py`: **1 assertion**, in `test_tier_type_validation` (`:146-148`): `type = "tar"` at a stage
  position expects `not yet supported` and becomes the R3 refusal. Unchanged: `test_unknown_keys_rejected`
  (none of its keys are new) and `test_location_rejects_crossed_settings` (`staging` inside a `location` table is
  still unknown, since `LOCATION_KEYS` doesn't change). The other 29 tests don't change.
- `tests/loop/stages.py:81-83`: both `REFUSAL` entries. `'single'` disappears once stages 1 and 2 pass (R8).
  `'sharded'` takes the new message, and its seed is `issues/sharded-archive-sources.md`, not `PARSER`. But
  `reach()` wraps every form in `blocked(PARSER, …)` (`:133`), so the seed has to vary with the form. Stages 3–8 pass
  the parser now and stop somewhere new; each must be re-pinned (module docstring, `:13-17`).
- `tests/loop/test_helper.py:76, 86, 92-93` assert the `staging` key and `loop.staging`; they change with
  `tools/bundle_loop.py:199-204` (R8).

## 5. Other parse paths

The only file loaders are the CLI's `mount` and `umount` (`src/terrace/__init__.py:100, 142`), both
`OverlaySpec.load`. `tests/engine/conftest.py` `build_spec` and the four test files above construct
`OverlaySpec(...)` directly, which skips `_validate_stack`. An engine test that builds an archive spec by hand gets
no stack validation; route those tests through `OverlaySpec.parse` where the stack rule matters.
`tools/bundle_loop.py:respec` is the only generator of spec text.

## 6. Risks

- All three edited symbols are high-blast-radius (`AGENTS.md:252`), so the human gate applies. Weakening the
  overlap check is the one that matters: if resolving is applied to S3 urls, or the `is_relative_to` symmetry is
  dropped, it silently passes a stage root over source data.
- `realpath` adds filesystem I/O to parsing, so a parse can now give different results on different hosts.
  That's acceptable, but say so in the docstring. Case-insensitive macOS volumes aren't normalized by `realpath`;
  out of scope.
- The exact-text pin in stage 9 turns the sharded message into a contract.
- `SUPPORTED_TYPES` becomes vacuous; a later reader may take it for a live gate.

## Recommendations

1. Add the four keys to `TIER_KEYS` once. Keep `_reject_unknown` first. Add per-type refusals in `from_data` in this
   order: sharded, then R3 (read role early), then location/path, then sync.
2. Add `TierSpec.location` and `TierSpec.max_frequency: Optional[Rate]`, both defaulting to `None`. For an archive
   tier, `path` = the location's file path or url, and `url`/`s3` = `None`.
3. Parse the rate through a `_rate` wrapper that type-checks before `parse_rate`.
4. Refuse `staging` at stack level, ahead of R4, naming `tiers[1]` when it's a shared stage tier. Put R4 in
   `_validate_stack`.
5. R5: compare `os.path.realpath` keys for POSIX roots, POSIX archive files and the state dir. Never resolve S3
   urls. Leave stored paths unresolved and keep the existing message prefixes.
6. Fix the sharded refusal text early. It's what stage 9 pins, and `reach()` needs a per-form seed.
