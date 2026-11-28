# Research 05 — the bundle loop: stages, helper, tests (R8, R2 stage-9 pin, re-pin of 3-9)

Base: worktree tree as checked out 2026-11-28. Paths relative to the worktree.

## 1. Stages 1-2 and the spec the helper writes

**Shared step `reach`** (`tests/loop/stages.py:131-137`): `OverlaySpec.load(loop.spec)` inside
`blocked(PARSER, SpecError, REFUSAL[scene.loop.form])`, then `OverlayEngine(loaded, clock=clock)` and
`engine.start()`. Today every stage stops here: `REFUSAL['single'] = "Unknown key(s) in tier 'bundles':
location, staging, sync"`, `'sharded'`: `"… location, sharded, sync"` (`:80-83`).

- **Stage 1 reach** (`:277-286`): `scene(bundles=1)`, `reach`, assert `engine.started` and
  `engine.spec.source.type == 'tar.gz'`; `engine.stop()` in `finally`.
- **Stage 2 read** (`:289-297`): `reach`, then `assert_tree` (`:158-162`): the oracle tree is non-empty
  and `oracle.difference(want, listing(engine)) == ''`. `listing` (`:140-155`) walks
  `engine.readdir` from `/` (dirs by `entry.kind == 'dir'`), and reads each file with
  `engine.open(path, O_RDONLY)` → `engine.read(handle, entry.size + 1, 0)` → `engine.release`. So it needs
  `readdir` → `_populate` → `source.list`/`source.stat` (`overlay.py:299-316, 352-359`), and `open` →
  `_serve` → `eligible_tier` (shm, 64 MiB) → `transfers.copy` using `source.open_read`
  (`transfer.py:109`; `ArchiveTier.open_read` exists, `archive.py:503`), then `pread` on the stage copy.
  Expected tree: `/bundle-000`, `/bundle-000/sub`, six members `member-00{0..5}.txt` of 512+128·m bytes,
  even ones at top, odd under `sub/` (`bundle_loop.py:180-186`).
- **Tap:** neither stage opens a `tap.window()`; no request count is asserted in 1 or 2. `Scene.__post_init__`
  (`stages.py:112-115`) only asserts the source's `location.s3.endpoint` is the tap.

**Spec written today** (probe `uv run python tools/bundle_loop.py [-n 2] -- sh -c 'cat "$BUNDLE_LOOP_SPEC"'`):

```toml
[overlay]
name = "loop"
state = "<base>/state"

[[tier]]
name = "bundles"
type = "tar.gz"
role = "source"
location = { type = "s3", url = "s3://<bucket>/bundles/bundle-000.tar.gz", s3 = { endpoint = "http://127.0.0.1:<tap>", region = "us-east-1", access_key_id = "test", secret_access_key = "test", allow_http = true } }
staging = "<base>/scratch/bundle-000.tar"          # single form only
sync = { max_frequency = "24/day" }

[[tier]]  # scratch: type posix, path <base>/scratch/space, scope "shared", quota { size = 67108864, files = 10000 }, writeback { delay = "60s" }
[[tier]]  # tmp:     same, scope "node", path <base>/tmp/space
[[tier]]  # shm:     same, scope "node", path <base>/shm/space (size = --fastest-size when given)
```

Sharded (`-n 2`): `sharded = true` before `location`, url `s3://<bucket>/bundles/*.tar.gz`, no `staging`,
same `sync` and stage tiers. Three stage tiers; exactly one `shared`, it is directly below the source and
`posix` (`bundle_loop.py:49`), so the stack already satisfies R4. Roots are `base/<name>/space` and state
`base/state` with `base` `.resolve()`d (`:190`), so R5's symlink-resolved overlap check finds nothing.
All `s3` keys are in `S3_KEYS` (`spec.py:30-31`).

**Dropping `staging`** — helper: `staging: Path | None` attribute (`bundle_loop.py:138`),
`self.staging = None` (`:155`), comment + assignment + `staging = …` line (`:201-204`; the single branch
keeps only the `location` line). `test_helper.py`: `:76` (`Path(source['staging']) == loop.staging`),
`:86` (`loop.staging` in the roots loop), `:92-93` (staging outside every root and the state dir), `:98`
(`loop.staging is None`). `:100` (`'staging' not in source`) stays. Replace with: `'staging' not in source`
and `not hasattr(loop, 'staging')` for both forms; optionally `OverlaySpec.load(loop.spec)` succeeds for
the single form (`source.type == 'tar.gz'`, the tier below the source is `scratch`, shared, posix) and
raises `SpecError` naming `issues/sharded-archive-sources.md` for the sharded form. A test may import
the package; only the helper may not (§5). Stage 1 already proves the load, so the parser check in
`test_helper` is optional; the no-`staging` assertion is the direct R8 evidence.

## 2. Stages 3-9 once the parser accepts, reads work, writes fail EROFS (R9), sharded refused (R2)

`change()` (`stages.py:165-198`) opens first: `engine.open('/bundle-000/member-000.txt', os.O_RDWR)`
(`:175`; `files` sorted puts top-level `member-000/002/004` before `sub/`). Every stage that calls it
stops there or at the following `write`.

| Stage | Next stop (predicted) | Type | Probable text |
|---|---|---|---|
| 3 pressure (`:300-319`) | `assert pushed, …` (`:315`) | `AssertionError` | `nothing was pushed out of the fastest tier\nassert []` (verified rendering under assert rewriting) |
| 4 change (`:322-331`) | `change` → `open(O_RDWR)` (`:175`) | `OSError` | `[Errno 30] Read-only file system: '/bundle-000/member-000.txt'` |
| 5 reaggregate (`:334-354`) | same, inside the `early` window | `OSError` | same |
| 6 read back (`:357-371`) | same | `OSError` | same |
| 7 release (`:374-389`) | same, inside the window | `OSError` | same |
| 8 offline commit (`:392-410`) | same | `OSError` | same |
| 9 several bundles (`:413-428`) | `reach` → `OverlaySpec.load` | `SpecError` | R2's text, naming `bundles` and `issues/sharded-archive-sources.md` |

- **Stage 3 arithmetic.** shm quota 2048; reads in oracle order 512, 768, 1024, 640, 896, 1152 admit by
  `usage + size > quota.size` (`overlay.py:204-231`): shm takes 512, 768, 640 = 1920; the others go to
  tmp. `EvictionManager` reclaims only above `DEFAULT_HIGH = 0.95` (1945.6 bytes; `evict.py:26-27, 159-168`),
  so nothing is evicted and `pushed == []`. (Had it evicted, `evict_copy` deletes and does not demote,
  `overlay.py:1536-1570`, so `:316` would fail next.) Owner `issues/eviction-one-hop.md` stays correct.
- **Stages 4-8.** If R9 refuses at `open` with write access, the text carries the path as the engine's
  other `OSError`s do. If R9 refuses only at `write`, `open` promotes to shm and the stop is `write`
  (`:177`), with the text build chooses. `OSError(errno.EROFS, …)` has no subclass, so
  `type(caught) is OSError` holds for `blocked`.
- **Stage 9.** The pin holds only if the sharded refusal comes before any other refusal the sharded spec
  could hit (e.g. the `*` in the url): order the `sharded` check ahead of location parsing, or confirm the
  glob url parses.
- **No stage passes outright**, so there are no XPASS(strict) risks beyond 1 and 2, which will XPASS
  and fail the run unless their `@owned` markers are removed.
- **Stage 8's `staging` line (`:402`)** must stop reading `loop.staging`. It is unreachable while 8 is
  pinned at `change`, but it is the assertion for "the staged bundle stays". Replace it with a check
  in the durable tier's root `stage.loop.tiers['scratch']`, at the name R7's build uses (Decision 1:
  "in the tier's root … under a name no entry of the overlay can take"). Best through a package function
  or constant naming that path, which `stages.py` may import (it already imports `terrace`).
- **Teardown gap.** Stages 3, 7 and 8 do not stop the engine when a step raises: 3 calls `engine.stop()`
  inside the window without `finally` (`:318`), and 7 and 8 have no `finally` (`:378-381`, `:396-398`).
  Today `reach` raises before `start`, so nothing leaks. After this cycle a pinned stop leaves a started
  engine (DB open, transfer workers).

## 3. Markers and the audit

- `blocked(seed, error, message)` (`stages.py:59-77`): exact `type(...) is error` and `str(...) == message`
  give `Blocked(f'{seed}: {message}')`. It accepts only `TerraceError`/`OSError`/`AssertionError` types.
  `owned(name, what)` (`:99-101`) gives `mark.xfail(raises=Blocked, strict=True, reason=f'{STAGES[name]}: {what}')`.
  `PARSER`/`REFUSAL` are used only in `reach` (`:133`), and both appear in `__all__` (`:49`).
- `test_support.py` audits:
  - `Blocked` is raised only in `blocked`, and `mark.xfail` is built only in `owned`; there is no
    skip or other xfail (`:84-93`).
  - `len(STAGES) == 9`; each stage's marks are `[]` or `['xfail']`; strict with `raises=Blocked`;
    kwargs exactly `raises, reason, strict`; the reason starts with the `STAGES` seed (`:96-105`).
  - `(REPO / seed).is_file()` for **every** `STAGES` entry, marked or not (`:101`).
  - Both modules collect exactly the nine stage objects (`:108-121`).
  - The seed argument of `blocked` is **not** audited.
- **Trap:** stages 1-2 keep `STAGES` entries `issues/archive-spec-plumbing.md` (keys are needed:
  `conftest.py:43` and `:110`). When the cycle lands, `code terrace roadmap` deletes the seed
  (`AGENTS.md:1094-1095`), and `:101` then fails. Either point those two entries at a path that persists
  (`spec/archive-spec-plumbing/GOAL.md`), or check seed existence only for marked stages.
- **Stage 9's pin** today is in `reach` via `REFUSAL['sharded']` and `PARSER` (archive-spec-plumbing).
  After the change the pin stays in `reach`, sharded form only:
  `blocked('issues/sharded-archive-sources.md', SpecError, <R2 text>)`, with a plain load for the single
  form.

## 4. Integration lane and wall time

`test_scenario_minio.py` imports the same nine function objects (`:27-29`) under
`[integration, skipif(no endpoint)]`. Markers travel with the functions, so no edit is required there,
and the audit at `test_support.py:117-121` still holds. It has never run against MinIO (`AGENTS.md:873-876`).

`uv run pytest -m unit tests/loop -q -rfEsxX`: **`61 passed, 9 deselected, 9 xfailed in 25.04s`** (real
25.6 s). The 9 deselected are the MinIO stages. After the change, stages 1-3 start real engines and pull a
roughly 4 KB bundle each. The cost should be small, but 4-8 now run `reach` plus `start` before their
pin.

## 5. Helper invariants

- It imports nothing of the package or `tests`, enforced by AST at `test_helper.py:442-449` and stated in
  `AGENTS.md:122`. It cannot ask the parser or the archive code where the staged bundle goes.
- Every exit path cleans up: `Loop.__init__` closes on any `BaseException` (`bundle_loop.py:176-178`),
  `main` closes the loop and the store in `finally` (`:390-397`), and the tests are at `test_helper.py:132-362,
  526`. Dropping `staging` touches only `respec`/attributes and none of this.
- `AGENTS.md` § Commands lists every flag and `BUNDLE_LOOP_*` variable (`test_helper.py:426-439`). No flag
  changes.

## Recommendations

1. Helper: delete `staging` (`bundle_loop.py:138, 155, 201-204`). Keep the stack as is, since it already
   meets R4 and R5.
2. `test_helper.py:76, 86, 92-93, 98`: assert `'staging' not in source` and no `loop.staging` in both
   forms. Optionally load the single-form spec with `OverlaySpec.load`, and assert the sharded form's
   `SpecError` names `issues/sharded-archive-sources.md`.
3. Remove `@owned` from stages 1 and 2, so they are plain passing tests under `unit` (R8). Re-point their
   `STAGES` entries, or narrow the `:101` existence check to marked stages, before the seed is deleted.
4. `reach`: `blocked` only for the sharded form, seed `issues/sharded-archive-sources.md`, with R2's exact
   text. Replace `PARSER` with a sharded constant, make `REFUSAL` a single string, and update `__all__` and
   `AGENTS.md:121`.
5. Pin stages 4-8 once, inside `change()` around the first mutating call (`:175`), with
   `blocked(<seed>, OSError, <R9 text>)`. Seed label suggestion: `issues/archive-journaling-and-locking.md`,
   on which lifting R9 waits (GOAL Non-goals). Keep the stage owners. Prefer R9 refusing `open` with write
   access, with path-bearing text, so no promote happens on a write-open.
6. Pin stage 3 at `:315` with `blocked('issues/eviction-one-hop.md', AssertionError,
   'nothing was pushed out of the fastest tier\nassert []')`. Build must confirm the stop on the real tree.
7. Put `engine.stop()` in `finally` for stages 3, 7 and 8.
8. Stage 8 `:402`: assert the staged bundle in `loop.tiers['scratch']` at the name R7 fixes, through a
   package function or constant that `stages.py` imports.
9. Rewrite `AGENTS.md` § Verification ("Today they are 9 expected failures … each stopped at the same
   step") and the `tests/` row (`:121`) to state the new stops: 1-2 pass, 3 eviction, 4-8 EROFS, 9 sharded
   refusal.
10. Build runs `uv run pytest -m unit tests/loop -q -rfEsxX` and confirms the result is 2 passed stages
    and 7 xfailed, each with the expected `stopped` property.
