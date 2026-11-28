# Research 07 — the FUSE integration test for R10

Measured at `62225d2` in this worktree, on the macOS dev host. Read-only; no Linux host used.

## 1. How the existing integration tests mount

- **Gate.** `tests/fuse/test_integration.py:30-38` `_linux_fuse()` needs `platform.system() == 'Linux'`,
  an existing `/dev/fuse` and an importable `mfusepy`. `pytestmark` (`:41-42`) is
  `[mark.integration, mark.skipif(not _linux_fuse(), reason='requires Linux with libfuse and /dev/fuse')]`,
  decided at import. A second gate, `tests/fuse/conftest.py:23-41`, prepends
  `skipif(True, reason='libfuse cannot be loaded: …')` to every item under `tests/fuse` when
  `import mfusepy` raises `OSError`. The chown test adds its own `'chown to another owner requires privilege'` (`:140`).
- **Spec.** Not TOML: `build_spec` (`tests/engine/conftest.py:45-61`) builds `TierSpec`/`OverlaySpec`
  dataclasses directly (source `project`, stages `scratch` shared, `tmp`/`shm` node), state at
  `tmp_path/'state'`. No test helper writes spec TOML except `spec_text` in `tests/tier/test_spec.py:70`
  (unit, module-local) and the loop helper (`tools/bundle_loop.py:198-211`, tar.gz only).
- **Mount.** `start_mount` (`:59-81`): `Mount(spec, point, foreground=True, writeback_tick=0.2,
  evict_tick=0.5)`, `mount.serve` on a daemon thread, polls `os.path.ismount(point)` for
  `MOUNT_TIMEOUT = 15.0` s (`:45`). If `mount._loop_error` is set it skips `'FUSE mount unavailable: …'`
  (`:76`); on timeout it force-stops and skips `'FUSE mount did not come up within the timeout'` (`:80`).
  `serve` runs `start()` then the libfuse loop on its own thread (`mount.py:329-357`);
  `_default_runner` calls `FUSE(OverlayOperations(engine), point, foreground=True, debug=…)` with no
  mount options (`mount.py:306-310`).
- **Teardown.** `stop_mount` (`:84-94`) sets `mount._mode`, sets `_stop`, joins for `JOIN_TIMEOUT = 30.0`
  (`:46`), then `unmount(point)` if still mounted (`fusermount3`, `fusermount`, `umount`; `mount.py:62-80`).
  Default mode `Shutdown.DRAIN`.
- **Least new code.** Generalize `start_mount` with an optional `spec: OverlaySpec | None = None` (use
  it instead of `build_spec` when given) and reuse `stop_mount` unchanged. Seeding stays out of the way
  (`seeds=()`; `project/` creation is harmless).

## 2. The minimal R10 test

```python
def test_zip_source_mount_readdir_lists_top_level_members(tmp_path: Path) -> None:
    archive = tmp_path / 'data.zip'
    write a ZIP with explicit modes: 'top.txt' (0o644), 'sub/' dir (0o755), 'sub/nested.txt' (0o644)
    spec = OverlaySpec.parse(f'''
    [overlay]
    name = "zipspace"
    state = "{tmp_path / 'state'}"
    [[tier]]
    name = "archive"
    type = "zip"
    role = "source"
    location = {{ type = "posix", path = "{archive}" }}
    [[tier]]
    name = "scratch"
    type = "posix"
    path = "{tmp_path / 'scratch'}"
    ''')
    mount, _, point, server = start_mount(tmp_path, spec=spec)
    try:
        assert sorted(os.listdir(point)) == ['sub', 'top.txt']
    finally:
        stop_mount(mount, server, point)
```

Parsing TOML through `OverlaySpec.parse` (`spec.py:266`) exercises the user path R1 opens, not a
hand-built `TierSpec`. Set modes via `ZipInfo.external_attr = mode << 16` as `write_zip` does
(`tests/tier/test_archive.py:45-51`); `writestr` without it gives mode 0. Omitted parent directories
are synthesized by the adapter (`archive.py:390`), but an explicit `sub/` entry keeps the case plain.

Paths hit before and during the `readdir`:
- `os.path.ismount` (the readiness poll) does `lstat` on the mount point, so `getattr('/')`
  (`adapter.py:80-92` → `engine.getattr` → `_ensure`, `overlay.py:326-342`) runs before anything else.
  `ArchiveTier.stat` says the root is always a directory (`archive.py:460-461`).
- `readdir` (`adapter.py:94-98`) → `engine.readdir` → `_populate('/')` over every tier
  (`overlay.py:352-359`), so the stage root's `.terrace/` must stay hidden, as it is for POSIX stacks.
- `os.listdir` makes no per-entry `getattr` (names-only readdir, no readdirplus attrs).
- `statfs` is not on this path: neither mount nor `listdir` calls it, only `df`/`statvfs`. Latent
  issue: `statfs` (`adapter.py:104-114`) does `os.statvfs(source.path)`. If an archive tier's
  `TierSpec.path` is the inner file path (as R5 implies), that succeeds and reports the archive's host
  filesystem. If it is a url or a placeholder, it falls back to `_quota_statvfs`, which reports 100 %
  full with no quota (`issues/invariant-audit-gaps.md` item 4). Out of scope for R10. Name it in TECH.
- Pumps: `start()` runs the write-back and eviction pumps (`mount.py:220-224`). With nothing dirty
  they do nothing. The default DRAIN teardown runs `drain` and `engine.stop('clean')`, the same path as R6.

**Hazard: a broken start reads as a skip, not a failure.** `serve` calls `self.start()` on the worker
thread (`mount.py:355`). If `engine.start` raises (for example a marker or registration fault over the
archive), the exception dies in the thread, `_loop_error` stays `None`, and `start_mount` waits 15 s and
then `skip`s `'FUSE mount did not come up within the timeout'`. On a Linux host an R10 regression in
start would show as SKIPPED. The pass-count grade still catches it, but the test should fail.
Remedy: in the R10 path, call `mount.start()` on the test thread, so an exception fails the test, then
run `mount._serve_after_start` on the worker. Or wrap `serve` in the thread, record the exception, and
`fail()` on it instead of skipping.

## 3. The macOS skip line and how a Linux grader finds the test

- Placed in `tests/fuse/test_integration.py`, it inherits `pytestmark`. On macOS `-rfEs` shows
  `SKIPPED [1] tests/fuse/test_integration.py:<def line>: requires Linux with libfuse and /dev/fuse`.
- Test id: `tests/fuse/test_integration.py::test_zip_source_mount_readdir_lists_top_level_members`.
  It is distinctive: no existing test name contains `zip_source_mount`.
- Grader on a Linux host with `/dev/fuse`:
  ```
  uv run pytest -m integration tests/fuse/test_integration.py \
      -k test_zip_source_mount_readdir_lists_top_level_members -rA -q 2>&1 | tee r10.log
  grep -q '^PASSED tests/fuse/test_integration.py::test_zip_source_mount_readdir_lists_top_level_members$' r10.log \
      && grep -qE '^1 passed' r10.log
  ```
  Never grade on the exit status: an all-skip exits 0. In a full `uv run pytest -m integration -rA`
  without docker, the 12 MinIO tests skip (3 in `test_s3_integration.py`, 9 loop stages in
  `stages.py`) and the chown test skips unless root. With R10 added, expect `7 passed, 13 skipped` on
  an unprivileged Linux FUSE host. Grade on the `PASSED …::<name>` line, not the total.

## 4. The macOS dev host

`uv run pytest -m integration tests/fuse -q -rs` on this host gives `7 skipped, 50 deselected in 0.08s`,
exit 0:
```
SKIPPED [1] tests/fuse/test_integration.py:97: requires Linux with libfuse and /dev/fuse
SKIPPED [1] tests/fuse/test_integration.py:114: requires Linux with libfuse and /dev/fuse
SKIPPED [1] tests/fuse/test_integration.py:127: requires Linux with libfuse and /dev/fuse
SKIPPED [1] tests/fuse/test_integration.py:140: chown to another owner requires privilege
SKIPPED [1] tests/fuse/test_integration.py:151: requires Linux with libfuse and /dev/fuse
SKIPPED [1] tests/fuse/test_integration.py:160: requires Linux with libfuse and /dev/fuse
SKIPPED [1] tests/fuse/test_integration.py:170: requires Linux with libfuse and /dev/fuse
```
Note: this host *does* load libfuse (`/usr/local/lib/libfuse.dylib`; `import mfusepy` succeeds). So the
conftest libfuse gate does not fire, and the skip comes from `_linux_fuse()`'s platform check. The unit
`tests/fuse` suite runs here: `50 passed, 7 deselected`. The R10 test will therefore skip cleanly by
the same `pytestmark`. Full `-m integration`: `19 skipped, 851 deselected in 0.50s`.

## 5. AGENTS.md figures that move with one more integration test

- `AGENTS.md:66`: `# 19 tests: real mounts (Linux) + MinIO` becomes 20.
- `AGENTS.md:105`: "nineteen tests — the entire drain / detach / …" becomes twenty. The list should
  also name the archive-source readdir.
- `AGENTS.md:121`: "54 files, 870 tests (851 `unit`, 19 `integration`)" becomes 20 integration. Total
  and unit move with the cycle's other tests. Files are unchanged if R10 lives in `test_integration.py`.
- `AGENTS.md:800`: "(19 skipped, 851 deselected on a host with no `/dev/fuse` and no MinIO)" becomes
  20 skipped. The deselected count tracks the final unit count.
- Not numeric but adjacent: `issues/ci-integration-lane.md` names seven FUSE tests and "Ten tests"
  (stale already: 19 today). `PLAN.md:1302-1305` (FUSE integration bullet) needs no number change.

## Recommendations

1. Put R10 in `tests/fuse/test_integration.py` as
   `test_zip_source_mount_readdir_lists_top_level_members`, inheriting `pytestmark`. Add no new module.
2. Extend `start_mount` with an optional `spec=` and build the spec through `OverlaySpec.parse` from
   inline TOML (zip source, posix `location`, one shared POSIX stage).
3. Make an engine-start failure fail the test, not skip it: start on the test thread, or capture the
   `serve` exception (§2 hazard).
4. Assert exact equality of the sorted `os.listdir` result with `{'top.txt', 'sub'}`. Use the default
   DRAIN teardown.
5. Add a mount-free unit companion in `tests/fuse/test_adapter.py`: `OverlayOperations(engine)` over the
   same zip spec, asserting `readdir('/')` gives `['.', '..', 'sub', 'top.txt']` (sorted after the dots),
   `getattr('/')` is a directory, and `statfs('/')` returns without raising. It runs on this macOS host
   and in Linux CI, so the adapter path is covered before any Linux grader runs.
6. Grade R10 only by the `PASSED tests/fuse/test_integration.py::<name>` line plus `1 passed` under
   `-k`, on a Linux `/dev/fuse` host. Without that host the review records R10 as ungraded.
7. Move the four AGENTS.md figures (lines 66, 105, 121, 800) in the same diff that adds the test.
