# PLAN — Drive the bundle loop end to end on a local object store

> **Status:** Draft for review · **Last updated:** 2026-11-12
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Lean path: no `research/` (appetite small).
> Every design element traces to a GOAL R-ID.

This cycle is built after the rename to Terrace lands. Every path, import, command and variable
below is written with the new names (`src/terrace/`, `import terrace`, `TERRACE_*`). The tree was
read, and the drafts were dry-run, at `bf438b4` under the old names; § 4 says what was run where.

## 1. Summary

A helper under `tools/` stands the loop up with one command: a moto object store in its own
process, a bucket of generated `tar.gz` bundles, an overlay spec in the archive-tier form, and three
POSIX stage tiers under one temporary directory, all removed on every exit path. A scenario under
`tests/loop/` drives the engine API over that spec as nine ordered stages. Each is a strict expected
failure that accepts one exception only, raised at the one step this tree is known to stop at, so a
stage that breaks for any other reason fails the run and so does a stage that starts to pass. An
oracle made of a directory tree and `tarfile` says what the bucket should hold, and a forwarding tap
in front of the store counts the requests the source receives. Nothing under `src/` changes.

## 2. Design

### 2.1 The helper: `tools/bundle_loop.py`

One Python file, run as a script and imported by the tests. It imports the standard library, and
`boto3` and `moto` inside the functions that need them. It imports nothing from `terrace` (a test
reads its imports), so it works whatever the spec parser accepts and the first expected failure is
the parser's, never the helper's.

**Command line.**

```
uv run python tools/bundle_loop.py [-n N] [--fastest-size BYTES]
                                   [--endpoint URL --access-key K --secret-key S --region R]
                                   [--] [COMMAND [ARG...]]
```

- Starts the store (unless `--endpoint` names one), builds the loop, and prints the spec's path as
  the first line of standard output. Nothing else of the helper's goes to standard output.
- With no `COMMAND` it then waits. An interrupt or a termination signal tears everything down and
  the helper exits 0. It installs its own handlers for `SIGINT`, `SIGTERM` and `SIGHUP`, so it stops
  on an interrupt even when started with that signal ignored, as a shell starts a background job.
  Once teardown begins the three are ignored, so a second signal cannot cut it short.
- With a `COMMAND` it runs it with `BUNDLE_LOOP_SPEC`, `BUNDLE_LOOP_ENDPOINT` and
  `BUNDLE_LOOP_BUCKET` set, tears down when it ends, and exits with its status. This is the shape
  `tools/temp_site.sh` already has, and it is the helper's normal exit. The variables carry no
  `TERRACE_` prefix on purpose: the application reads every `TERRACE_*` variable as a config key.
- `SIGKILL` cannot be caught. It ends the store with the process and leaves the directory, and the
  usage text says so.

**Python interface.**

| Name | What it is |
|---|---|
| `Store` | A context manager around `ThreadedMotoServer(ip_address='127.0.0.1', port=0, verbose=False)`. `endpoint` after `start()`; `stop()` shuts the server down, after which the port refuses a connection. `verbose=False` keeps moto's banner off standard output. |
| `Loop(endpoint, *, bundles=1, fastest_size=None, access_key, secret_key, region, parent=None)` | Builds everything below and is a context manager. A failure part-way through the constructor closes what was already made before it re-raises. |
| `Loop.root` | The one temporary directory, `mkdtemp(prefix='bundle-loop.', dir=parent)`. |
| `Loop.content` | `root/content/<stem>/…`: one generated tree per bundle. The bundle is packed from it, and it is the oracle's starting tree. |
| `Loop.keys` | `{stem: 'bundles/<stem>.tar.gz'}` for `bundle-000`, `bundle-001`, … |
| `Loop.spec`, `Loop.state`, `Loop.tiers`, `Loop.staging`, `Loop.form` | The spec's path, the state directory, the stage roots by tier name, the staging path (single form only), and `'single'` or `'sharded'`. |
| `Loop.resync_interval`, `Loop.delay` | The seconds behind the `sync.max_frequency` and `writeback.delay` the spec states. |
| `Loop.respec(label)` | A second spec over the same bucket with a fresh state directory and fresh stage roots under `root/label`. Stage 6 uses it. |
| `Loop.close()` | Deletes the bucket's objects and the bucket, and removes `root`, making its directories writable first. Safe to call twice. If `root` cannot be removed the error propagates and the helper exits non-zero: a directory left behind is never reported as a clean exit. |
| `main(argv)` | The command line. `Store` is stopped in a `finally` that also covers a failure while the loop is being built. |

**What it generates.** Each bundle is one top-level directory named for the bundle's stem, holding
six files in two directories and no empty directory, filled from `random.Random(seed)` with a seed
of the bundle and member numbers. Seeded random bytes do not compress, so each object is a few
kilobytes as R1 asks, and no two members share content. The single top-level directory is what
`PLAN.md` § *Tier protocol* requires of a shard and allows of a lone archive, so one generator
serves both forms and the overlay's tree equals `Loop.content` in both.

**The spec it writes** follows `PLAN.md` § *Configuration* key for key. For one bundle:

```toml
[overlay]
name  = "loop"
state = "<root>/state"

[[tier]]
name = "bundles"
type = "tar.gz"
role = "source"
location = { type = "s3", url = "s3://<bucket>/bundles/bundle-000.tar.gz", s3 = { endpoint = "…", region = "…", access_key_id = "…", secret_access_key = "…", allow_http = true } }
staging  = "<root>/scratch/bundle-000.tar"
sync     = { max_frequency = "24/day" }

[[tier]]                       # the durable tier
name = "scratch"
type = "posix"
path = "<root>/scratch/space"
scope = "shared"
quota = { size = 67108864, files = 10000 }
writeback = { delay = "60s" }

[[tier]]
name = "tmp"                   # node scope
…
[[tier]]
name = "shm"                   # node scope, fastest; --fastest-size sets its quota.size
…
```

For several bundles the source tier carries `sharded = true`, a `location.url` of
`s3://<bucket>/bundles/*.tar.gz`, and no `staging`, as the sharded example has it. `staging` sits
beside the durable tier's root, as `/scratch/space.tar.gz` sits beside `/scratch/space` in the
example; it is outside every tier root and outside the state directory. Every stage tier states a
size limit, a count limit and a delay, which is the maintainer's model of a tier.

### 2.2 The scenario: `tests/loop/`

`tests/loop/` is a package (`__init__.py`), like `tests/engine/`. `tests/loop/__init__.py` loads
`tools/bundle_loop.py` by path and exports it, so the tests and a person at a terminal run one
implementation (R1).

| File | Holds |
|---|---|
| `__init__.py` | The helper, loaded from `tools/`. |
| `conftest.py` | `store` (session: the helper's `Store`), `clock`, and `scene`, a factory that puts a tap in front of the store, builds a `Loop` through the tap, and checks every bundle's object against the oracle before a stage sees it. A scene refuses a spec whose endpoint is not the tap. Also the report hook of § 2.3, and assertion rewriting for `stages` and `oracle`. |
| `oracle.py` | R3. `tree(directory)`, `unpacked(blob)`, `difference(expected, found)`. Standard library only. |
| `tap.py` | R6. The counting forwarder. Standard library only. |
| `stages.py` | `Blocked`, `blocked(...)`, the shared steps and the nine stage functions with their markers. Not a test module: pytest collects nothing from it. |
| `test_scenario.py` | `pytestmark = mark.unit`; imports the nine stages. |
| `test_scenario_minio.py` | R7. `pytestmark = [mark.integration, mark.skipif(...)]`; imports the same nine functions and overrides `scene`. |
| `test_helper.py` | R1, R2: the helper's own tests. |
| `test_support.py` | The tests of `blocked`, the marker audit, the oracle and the tap. |

Drafts of `stages.py`, `oracle.py` and `tap.py` are in [`draft/`](draft/). They are the design at
code altitude, dry-run as § 4 describes; the build phase owns the final files and may change
anything in them that this section does not state.

**Which store the unit lane uses (R1's open point).** The helper's own. `store` starts one
`bundle_loop.Store` per pytest worker, so the scenario builds the store, the bucket, the spec and
the directories through the helper. `tests/tier/conftest.py`'s `moto_endpoint` is not visible from
`tests/loop/` and is left alone. Where `boto3` or `moto.server` cannot be imported, `store` skips
with a reason naming the package, as the S3 tier tests do; every gate of this cycle asserts no test
skipped.

### 2.3 Strict expected failures that mean one thing

Every stage fails today at the same place, the spec parser. `xfail(strict=True)` alone would accept
any failure at all: a helper that wrote an overlapping tier root, a typo in a stage, a broken
oracle. The design narrows what the marker accepts to a single exception with a single cause.

```python
class Blocked(Exception): ...

@contextmanager
def blocked(seed, error, message):
    if not issubclass(error, (TerraceError, OSError, AssertionError)):
        raise TypeError(...)                      # a slip in a stage is never a refusal
    try:
        yield
    except Exception as caught:
        if type(caught) is error and str(caught) == message:
            raise Blocked(f'{seed}: {message}') from caught
        raise

def owned(name, what):
    return mark.xfail(raises=Blocked, strict=True, reason=f'{STAGES[name]}: {what}')
```

- A stage's marker is `owned(...)`: `raises=Blocked`, `strict=True`, and a reason that begins with
  the path of the seed that owns the stage (R5). `STAGES` maps each stage to its owner.
- `Blocked` is raised by `blocked(...)` and nowhere else. `blocked` wraps the one step the tree is
  known to stop at and names the exact exception type and the exact text. A subclass, another
  text, or any other exception passes through and fails the stage.
- Only a refusal can be pinned: one of the package's errors, an `OSError`, or a stage's own failed
  assertion. `blocked` raises `TypeError` for anything else, so a later cycle cannot record an
  `AttributeError` in a stage body as that stage's expected failure.
- On this tree that step is `OverlaySpec.load(spec)` inside the shared `reach` step, and the text is
  the parser's, measured against the spec the helper writes:

  | Form | The parser says |
  |---|---|
  | single | `Unknown key(s) in tier 'bundles': location, staging, sync` |
  | sharded | `Unknown key(s) in tier 'bundles': location, sharded, sync` |

- Everything before that step runs today in every stage and must pass: the helper builds the loop,
  and the oracle unpacks each bundle's object and compares it with the tree it was packed from.

What each outcome means:

| What happens | The run |
|---|---|
| The step raises the pinned exception | stage is an expected failure |
| The helper writes a spec the parser refuses for any other reason (an overlap, a typo, bad TOML) | fails |
| The bundle in the bucket is not the oracle's tree | fails |
| A stage body raises anything else, anywhere | fails |
| The stage passes | fails (`strict`) until its marker is removed |
| A later cycle lifts the block | every stage behind it runs on to its next stop and fails there |

The last row is the protocol the file imposes on the cycles queued behind this one. A cycle that
moves the block either removes the marker of a stage that now passes, or wraps the stage's new
stopping step in `blocked(...)` with the exception it raises. It cannot land with the scenario
unread. `AGENTS.md` says so where it describes the scenario.

**The run records what stopped each stage, and the gate reads it.** A marker's reason is a string
any stage could earn by excusing itself: by calling `pytest.xfail`, by raising `Blocked` by hand, or
through a marker added at collection. The review found all three pass a gate that reads the reason
alone (§ 4). So a `pytest_runtest_makereport` hook in `tests/loop/conftest.py` puts three
properties on every stage, and they reach the JUnit report:

| Property | Value on this tree |
|---|---|
| `stopped` | `Blocked: issues/archive-spec-plumbing.md: <the parser's text for the form>` |
| `cause` | `SpecError`, the type of the exception `Blocked` was raised from |
| `marker` | `xfail ['raises', 'reason', 'strict'] raises=Blocked strict=True args=0`, read from the collected item, so a marker added or replaced at collection shows |

The hook appends to `item.user_properties` during set-up and call; the JUnit plugin takes a test's
properties from the item at teardown. `rows-p2`, `rows-p3` and `variant` require all three on each
of the nine stages, exactly. A cycle that moves the block changes the expected values in its own
gate, which is the same act as re-pinning the stage.

A marker audit in `test_support.py` keeps the markers honest: every stage in `STAGES` carries either
no marker or exactly one `xfail` with `strict is True`, `raises is Blocked`, no condition, and a
reason starting with its owner's path; the seed file exists (R5); `stages.py` defines the nine
stage functions and no other test; and each collecting module holds exactly those nine objects.
The marker's keywords are exactly `raises`, `reason` and `strict` (no `run=False`, no condition). A
second test reads `stages.py`'s syntax tree: one `raise` of `Blocked`, inside `blocked`; one call of
`mark.xfail`, inside `owned`; no call of a skip or of `pytest.xfail`.

### 2.4 The stages

All nine build their own scene, so they are independent under `pytest -n auto`. A later stage runs
the earlier steps it needs through the shared functions in `stages.py`. Stages 1 to 8 use one
bundle (the single form); stage 9 uses three (the sharded form).

| # | Test | Steps after `reach` | Counted (R6) | Owner |
|---|---|---|---|---|
| 1 | `test_stage_1_reach` | The engine is started and its source tier's type is `tar.gz`. | | `archive-spec-plumbing.md` |
| 2 | `test_stage_2_read` | `assert_tree`: walk `readdir` from `/`, read every file, compare names, kinds and bytes with the oracle's tree. | | `archive-spec-plumbing.md` |
| 3 | `test_stage_3_pressure` | Built with `fastest_size=2048`: every member fits and all six together do not. Each file is read and compared, and the stage notes which reads left a copy on `shm`. After one `EvictionManager.poll()`, a file is pushed out if it was on `shm` and no longer is. At least one was, and each is held on `tmp`. Then `assert_tree` again, and the engine stops. | the window covers all of it through the stop: at least one read reached the source, and no write | `eviction-one-hop.md` |
| 4 | `test_stage_4_change` | `change`: modify, create, rename and remove one member each inside `bundle-000`, each mirrored on the oracle's directory with a file operation. `assert_tree`. | | `namespace-sync-redesign.md` |
| 5 | `test_stage_5_reaggregate` | `change`, then `settle`: let each tier's delay expire, a hop at a time and well inside one interval; then cross the bundle's interval once and poll until idle. The object unpacks to the oracle's directory. | the window covers `change` and `settle`: its writes are exactly `[('PUT', bundle-000's key)]` | `archive-finalization-integration.md` |
| 6 | `test_stage_6_read_back` | As 5, stop, then a second engine over `respec('second')`: `assert_tree`. | | `archive-finalization-integration.md` |
| 7 | `test_stage_7_release` | `change`, `detach` (`rescue`, then `stop('detached')`), remove the node-scope tier directories, start an engine on the same state: `assert_tree`, and no residency on `tmp` or `shm` for any path. | no write from the change through the detach | `shutdown-rescue-gaps.md` |
| 8 | `test_stage_8_offline_commit` | As 7 through the removal, then `python -m terrace commit <spec>` in a second process with its XDG paths redirected. The object unpacks to the oracle's directory and `Loop.staging` is still a file. Then an engine starts and `assert_tree` holds. | in that last window, at least one request, and no read of any bundle's key | `offline-commit-command.md` |
| 9 | `test_stage_9_several_bundles` | `assert_tree`, `change` in `bundle-000`, `settle`. `bundle-000`'s object unpacks to its directory; the other two objects are byte-identical to before. | the window covers `change` and `settle`: its writes are exactly `[('PUT', bundle-000's key)]` | `sharded-archive-sources.md` |

Each owner was confirmed against its seed: the read side is `archive-spec-plumbing.md`'s note of
2026-11-11; keeping a pushed-out copy one tier up is `eviction-one-hop.md`'s R2; the bundle as the
unit of change is `namespace-sync-redesign.md`'s decision 3; the rebuild and push is
`archive-finalization-integration.md`; "nothing in the lower tiers" is `shutdown-rescue-gaps.md`'s
note on a soft unmount; rebuilding only what changed and keeping the staged bundle is
`offline-commit-command.md`'s note of the same date. No owner changes from R4's table.

No stage is added for `racing-write-marked-clean.md` or `archive-journaling-and-locking.md`: the
first needs a second thread and the second a crash or a second writer, and nothing deterministic in
a single-threaded scenario shows either.

The steps after `reach` call the engine as it is today (`readdir`, `open`, `read`, `write`,
`create`, `rename`, `unlink`, `residencies`, `EvictionManager.poll`, `WritebackScheduler.poll`,
`rescue`, `stop`). They cannot run on this tree. § 5 says what that leaves open.

### 2.5 The oracle (R3)

`oracle.tree(directory)` maps every path below a directory to its bytes, or to `None` for a
directory, and raises on a symlink or special file. `oracle.unpacked(blob)` extracts a `tar.gz` with
`tarfile` (`filter='data'`) into a scratch directory and returns `tree` of it. `difference` returns
one line per path that is missing, unexpected, of another kind, or of other bytes. Names, kinds and
bytes are compared; modes and times are not, and belong to
`issues/archive-finalization-integration.md`'s own criteria. The stages keep every directory
non-empty, so a tar with and without directory entries unpacks to the same tree. A test reads
`oracle.py`'s imports and fails on any that is not in `sys.stdlib_module_names`.

Extraction keeps the last of two members with one name, so an archive rebuilt by appending would
unpack to the right tree while holding every superseded member. `unpacked` therefore raises on a
name held twice, and opens with `ignore_zeros=True` so a second archive behind an end-of-archive
marker is read as well. Both were shown to compare equal before the change (§ 4).

Guards against an oracle that agrees with anything: `assert_tree` and `Scene.assert_object` refuse
an empty expectation, and `test_support.py` feeds `difference` six wrong archives (a missing member,
an extra one, changed bytes, a longer file, a file where a directory was, an empty directory) and
expects the exact line for each, plus an archive with a member held twice and one with a second
archive appended.

### 2.6 Counting requests (R6)

`tap.Tap(upstream)` is a `ThreadingHTTPServer` on a loopback port. The spec names the tap as the
endpoint, and the tap forwards every request to the store with its headers and body unchanged and
records `(kind, method, bucket, key, query)`. `Tap.window()` is a context manager that yields the
requests that arrived inside it.

- **Kinds.** `HEAD` is `stat`. `GET` on a key is `read`; `GET` on the bucket is `list`. Every other
  method is `write`, whatever its target. The dry run showed why the rule has to be that broad:
  obstore deletes with `POST /<bucket>?delete` and the key in the body, so a rule keyed on the path
  would have missed a write.
- **Why on the wire.** It counts what the source received from any client in any process, which
  stage 8's second process needs, and it is the same code in front of moto and MinIO (R7).
- **What it does not carry.** A request with `Transfer-Encoding: chunked` is refused with 501 and
  remembered, and leaving a window in which one was refused raises. A body the tap cannot replay is
  never counted as if it had been forwarded.
- **A count means nothing unless the engine goes through the tap.** A scene asserts that the
  endpoint in the spec it hands a stage is the tap's. Stage 3's and stage 8's windows also assert
  that something arrived. A window is read only after its block ends; `Window.of` raises inside it,
  where it would read as empty and "no write" would always hold.
- **Every method is forwarded and recorded**, `PATCH` and `OPTIONS` included.
- **The oracle bypasses it.** A scene's `fetch` reads objects from the store itself, so the oracle
  neither adds to a window nor depends on the tap.

### 2.7 Lint reaches `tools/`

`tools/bundle_loop.py` is the first Python file under `tools/`. `tools/lint.sh` and the `checks` job
in `.github/workflows/ci.yml` run ruff over `src tests`, and the SPDX check walks the same two
directories. P1 adds `tools` to all three, in the commit that adds the file (deviation row 2).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `tools/bundle_loop.py`: `Store`, `Loop`, `main`; the single and sharded spec forms (§ 2.1). `tests/loop/__init__.py` loads that file, and `conftest.py` builds every scene through it (§ 2.2). The unit lane uses the helper's own store. |
| R2 | `Loop.close`, the constructor's own clean-up, `main`'s `finally`, and the handlers for `SIGINT`, `SIGTERM` and `SIGHUP`. Six subprocess tests cover a command that ends, a command that fails, an interrupt, a termination, an interrupt when the helper was started with it ignored, and a second signal during teardown; three in-process tests cover a failure while the loop is being built and a directory that will not go. Each asserts the endpoint refuses a connection and the directory is gone. |
| R3 | `tests/loop/oracle.py` (§ 2.5), and the test that reads its imports. |
| R4 | `tests/loop/stages.py` (§ 2.4), collected by `tests/loop/test_scenario.py`. |
| R5 | `Blocked`, `blocked`, `owned`, `STAGES`, the pinned parser text, and the marker audit (§ 2.3). |
| R6 | `tests/loop/tap.py` and the windows in stages 3, 5, 7, 8 and 9 (§ 2.6). |
| R7 | `tests/loop/test_scenario_minio.py`: the same nine function objects, `TERRACE_TEST_MINIO_*` with the defaults and the skip of `tests/tier/test_s3_integration.py`. The `collect` gate counts its nine ids, and `variant` runs the module's wiring over the helper's own store. `TECH.md`, `AGENTS.md` and `issues/ci-integration-lane.md` state that it has not run against MinIO. |
| R8 | The `lane` gate (§ 6): the unit lane with and without `tests/loop`, three times each, alternating; the difference of the medians is at most 10 s. It measures everything this cycle adds under `tests/loop`, the helper's tests included, which is no looser than R8. `TECH.md` P3 records the lines the gate prints. The time `AGENTS.md` then states is copied from that log and graded by the orchestrator; no gate reads it. |
| R9 | `AGENTS.md` § Commands and § Repository map gain the helper in P1, the scenario in P2 and the MinIO variant in P3. The `figures` gate compares every count the file states with the tree in each phase. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against `AGENTS.md` § Invariants before the reads and again after this design was drafted.
The change touches no path on the high-blast-radius list and nothing under `src/`.

- **§ 7 Eviction.** Stage 3 asserts that a copy pushed out is kept one tier up, which is not what
  § 7 says today. It asserts it as an expected failure owned by `issues/eviction-one-hop.md`, the
  cycle that will overturn § 7 in front of the maintainer. Nothing here passes against § 7, and
  § 7 is not edited.
- **§ 8 Lifecycle.** `detach` runs `rescue` to completion and then settles `detached`, the order
  § 8 requires. The scenario starts no pump thread; it calls `poll` on the calling thread, so
  nothing races a drain.
- **§ 9 Layering.** The helper and the oracle import nothing from the package. The stages import
  `terrace.engine` and `terrace.tier`, as `tests/tier/test_s3_engine.py` does.
- **§ 11 The engine API contract.** The stages call the synchronous API with an injected `Clock`
  and never sleep.
- **§ Conventions.** `test_helper.py`, `test_support.py` and `test_scenario.py` declare
  `pytestmark = mark.unit`; `test_scenario_minio.py` declares `integration`. `tests/loop/` is a
  package, so its module names cannot collide. `Clock` is imported from `tests/engine/conftest.py`
  as the interface it is. No R- or P-id appears in a test. The integration gate counts collected
  ids and asserts no pass it cannot observe. Each phase moves the figures it changes in `AGENTS.md`
  in its own commit.
- **§ Known open violations.** Stage 1's stop is the row "`ArchiveTier` is unreachable from a spec
  file". The scenario records it; it does not re-file it.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| 1. **R5's wording.** R5 says stage 1's expected failure is "the spec parser's refusal of the archive type", and GOAL § Problem places it at `tier/spec.py:209`. For the documented form the parser stops five lines earlier, at `_reject_unknown` (`:204`), on the archive tier's keys: `Unknown key(s) in tier 'bundles': location, staging, sync`. The design pins that text and reads R5 as "the spec parser's refusal of the archive tier". **For the sign-off: a clarification to GOAL R5.** | R1 requires the documented form, and the documented form carries `location`. `issues/archive-spec-plumbing.md` § Problem names the same two gates in the same order. | Pinning the type refusal (`Tier type 'tar.gz' is not yet supported`) needs a spec with no archive key, which is not the form R1 asks for and names no bundle. That refusal already has a test, `tests/tier/test_spec.py::test_tier_type_validation`. Accepting either text would let a helper that dropped `location` pass as expected. |
| 2. **Lint reaches `tools/`.** `tools/lint.sh` and `.github/workflows/ci.yml` gain `tools` in the ruff paths, and the SPDX loop gains it too. No R-ID asks for this. | The helper is a deliverable no static check would read. Its signal paths run only in a subprocess, where an undefined name shows up as a hung or failed test, not as a lint line. | Leaving the file outside the gate is one line less in two files and leaves the only Python under `tools/` unchecked for good. |
| 3. **A forwarding tap in the tests.** About a hundred lines of HTTP forwarding, with tests of its own. | R6 needs counts by kind, for a second process too, and the same on moto and MinIO. | Patching the S3 client's functions in process counts only the calls that were patched, sees nothing of stage 8's second process, and ties the count to one client library. A missed call path reads as "no write". |

## 4. Rabbit holes (resolved)

- **Does `xfail(raises=Blocked, strict=True)` do what § 2.3 claims?** Yes, measured. A prototype of
  the helper, the drafts and the tests was run at `bf438b4` under the old names (`sosfs`), in a
  detached worktree, `.worktrees/local-bundle-loop-prototype`. Clean: 9 expected failures and every
  other test passed (31 then, 41 after the review's additions). Five faults were planted one at a time and all nine stages failed each time: a stand-in
  parser and engine that let stage 1 pass (`XPASS(strict)` on stage 1, `AttributeError` on the
  rest); the pinned text changed; a typo key added to the spec; the archive tier rewritten as a
  POSIX tier that overlaps `scratch`; and a bundle packed with a member missing (the oracle's check
  in `scene` failed).
- **What the parser says.** Measured at `bf438b4`: the two texts in § 2.3, and
  `Tier type 'tar.gz' is not yet supported (tier 'bundles')` only for a tier with no archive key.
- **Can a tap sit between obstore and moto?** Yes. Through the tap, a put, get, head, list and
  delete each arrived once and the bytes came back unchanged. The delete arrived as a `POST` on the
  bucket, which settled the rule for `write` (§ 2.6). The tap's default half-second shutdown poll
  made every test's teardown take 0.45 s; the draft sets `poll_interval=0.02`.
- **What the helper's store costs.** Importing `moto` and `boto3` takes 0.8 s, once per worker
  and already paid by `tests/tier/`. Starting the server takes 2 ms; stopping it takes 0.5 s, once
  per worker. A subprocess test of the helper takes about 1 s.
- **The unit lane's time.** The `lane` mode ran once on the prototype, before the review's
  additions (31 passing tests under `tests/loop`): without `tests/loop` 37.3 s (median of 45.9,
  37.3, 32.8), 731 passed; with it 37.4 s (median of 37.4, 39.7, 31.2), 762 passed and 9 expected
  failures; added 0.1 s against a budget of 10 s. The spread between runs is larger than what the
  cycle adds. The final prototype has 41 passing tests there and ran them alone in 3.6 s under
  `-n auto`; the lane was not timed again. The per-test times summed over workers came to 14 s,
  since every worker starts and stops its own store, which is why the gate judges wall time and
  only prints the sum.
- **Adversarial design review (trigger 3).** It ran: one pass by a fresh subagent with the GOAL,
  this design, the drafts, the gate and the prototype, planting faults through pytest plugins and
  editing nothing. What it found and what changed:
  - *A stage can excuse itself* (HIGH, run). With `reach` replaced by a call of `pytest.xfail` or a
    hand-raised `Blocked`, and again with a collection hook that swapped in a lax marker over a
    stage raising `RuntimeError`, the gate printed ok. → The report hook and the three pinned
    properties of § 2.3; the syntax-tree test; the keyword check in the audit. Replayed: each plant
    now fails `rows-p2` with 18 to 27 problems.
  - *The tap was never shown to be in the path* (HIGH, run). A scene whose spec named the store
    passed. → The scene's endpoint assertion and the "something arrived" assertions of § 2.6.
    Replayed: red.
  - *`settle` crossed the interval every round* (MEDIUM, reasoned), so a conforming engine could
    push twice, and the window opened after `change`, so an eager push went uncounted. → Two-phase
    `settle`; stages 5 and 9 count from `change`.
  - *Stage 3 counted files that were never on the fastest tier* (MEDIUM, reasoned). → The stage
    tracks which reads landed on `shm`, sizes the quota so every member fits, reads the tree again
    after the eviction, and counts through the stop.
  - *`blocked` would pin any type* (MEDIUM, reasoned). → Refusals only.
  - *The oracle accepted a superseded member and an appended archive* (MEDIUM, run). → § 2.5.
  - *Three exit paths left the store or the directory behind with the tests green* (MEDIUM, run):
    started with the interrupt ignored; a read-only directory inside the root; a second signal
    during teardown. → § 2.1 and three more helper tests.
  - *A window read inside its block is empty* (MEDIUM, run). → `Window.of` raises until it closes.
  - LOW: `run=False` passed the audit (→ keyword check); `PATCH` was answered 501 and not recorded
    (→ forwarded); the ledger knew seven phrases (→ eleven, and the wider scan caught a checklist
    item of this plan's own); pointing the variant at the helper's store runs its wiring (→ the
    `variant` mode).
  - Taken as risks, not changes (§ 5): stage 7 sees node-scope copies only for names that still
    exist; stage 8 does not open the staged bundle; stage 5 does not assert that nothing is pushed
    before the interval; no gate reads the lane time `AGENTS.md` states.
  - Found sound: `blocked`'s exactness; a teardown error after `Blocked` and a `pytest.skip` in a
    body both turn the gate red; a dropped or renamed stage; the JUnit parse; `fetch` bypassing the
    tap; the engine methods the bodies call exist.

## 5. Risks & open questions

- **The stage bodies past `reach` do not run on this tree.** They are written against the engine as
  it is today and have never executed. This cannot fail green: when a cycle lifts the block, each
  body either passes, and `strict` fails the run until someone removes the marker and reads the
  stage, or stops, and the run fails until the stop is pinned. What it can do is cost the first
  owning cycle some repair of a body nobody has run. The assertions that would decide a pass are
  built from `difference`, which is tested against wrong archives, and from the tap, which is
  tested against known requests; and each refuses an empty expectation.
- **Three stage claims are weaker than they could be, on purpose.** Stage 7 asks the restarted
  engine for copies on node-scope tiers by the names that exist after the change; a row left
  against a removed or renamed-away name is not visible through `residencies`, and the stage does
  not read the database. Stage 8 asserts the staged bundle is a file and that the next engine pulls
  nothing and reads the right tree; it does not open the staged bundle, whose layout
  `issues/archive-spec-plumbing.md` still has open. Stage 5 asserts one write across the change and
  the interval; it does not assert that nothing is pushed before the interval, which GOAL R4 and R6
  do not say and `issues/archive-finalization-integration.md`'s own R4 does. Each is the owning
  cycle's to tighten when its marker comes off.
- **A stage that passes for a thin reason.** When a marker comes off, the stage becomes a plain
  test and its body is the whole claim. That reading belongs to the owning cycle's review. This
  cycle cannot test it.
- **The archive spec form may change.** `issues/archive-spec-plumbing.md` (note of 2026-11-11) asks
  whether `staging` stays a key. The form lives in one function of the helper, and stage 8 reads
  `Loop.staging`; the cycle that changes the form changes both, and the pinned text with them.
- **The tap in front of MinIO is unproven.** MinIO checks request signatures, moto does not. The tap
  forwards the `Host` header and the body as sent, which is what a signature covers, but no run
  here shows it. The first machine to run the variant finds out;
  `issues/ci-integration-lane.md` carries the note (P3).
- **The lane bound is a wall-clock figure.** Three alternating pairs and medians keep one loaded run
  from deciding it, and the measured addition is far below the bound. A host under heavy load can
  still miss it; the gate prints all six times so a miss can be read.
- **Open for the sign-off:** deviation row 1 (GOAL R5's wording) and row 2 (whether lint may reach
  `tools/` in this cycle).

## 6. Verification strategy

The gates are one script, [`verify/check_outcomes.py`](verify/check_outcomes.py), run as
`uv run python -I spec/local-bundle-loop/verify/check_outcomes.py <mode>`. It reads pytest's JUnit
report and judges each test by id; it never reads an exit code. Each phase's `verify:` is
`tools/lint.sh` and one phase mode, and every part of a phase mode runs alone under its own name.

| Mode | What it asserts |
|---|---|
| `rows-p1` | Each named helper test passed; nothing in `tests/loop/test_helper.py` failed, errored, skipped or was an expected failure. |
| `rows-p2`, `rows-p3` | Each named helper and support test passed. Each of the nine stages in `tests/loop/test_scenario.py` is an expected failure whose reason starts with its owner's path, whose seed exists, and whose `stopped`, `cause` and `marker` properties are exactly those of § 2.3. Exactly nine expected failures, no skip, no failure. `rows-p3` adds the audit of the MinIO module. |
| `probe` | The real command line, inside `tools/temp_site.sh`: `bundle_loop.py -n 1` and `-n 3` each run a command that hands `BUNDLE_LOOP_SPEC` to `terrace.tier.spec.OverlaySpec.load`. The parser's answer is the pinned text for that form, and the helper's directory is gone afterwards. This is R5's last sentence, checked from outside the test code. |
| `collect` | `pytest -m integration --collect-only` lists exactly the nine stages under `tests/loop/test_scenario_minio.py`. A count of ids, never an exit code. |
| `variant` | The MinIO module's wiring: the helper's command line starts its store and runs `pytest -m integration tests/loop/test_scenario_minio.py` with `TERRACE_TEST_MINIO_*` pointed at it. Nine expected failures with the same three properties. It shows the module's fixtures, skip and tap work; it is not a run against MinIO and no record calls it one. |
| `figures-p1`, `-p2`, `-p3` | Every count `AGENTS.md` states (unit, integration, files, the sentence on tests CI never runs, read as a digit or a number word) equals the tree's, and the helper, the scenario and the variant are named from the phase that adds them. |
| `ledger` | Every deferral phrase in a `TECH.md` phase body (prose and list items, never headings) names an `issues/` seed that exists and that `ROADMAP.md` indexes, and `issues/ci-integration-lane.md` records the variant that has not run. |
| `lane` | R8. The unit lane without and with `tests/loop`, three alternating pairs. Outcomes outside `tests/loop` are identical in both; `tests/loop` shows nine expected failures and no failure, error or skip; median with minus median without is at most 10 s. Prints both medians, all six times and the counts by outcome. |

Phase modes: `p1` = `rows-p1` + `figures-p1`; `p2` = `rows-p2` + `probe` + `figures-p2` + `lane`;
`p3` = `rows-p3` + `collect` + `variant` + `ledger` + `figures-p3` + `lane`.

**Mutations the build phases plant** (code-build's fail-once, and the decision list for the gate's
own choices; each is planted, seen red, and reverted):

| # | Plant | Must turn red |
|---|---|---|
| M1 | The helper's spec gains a key the parser does not know. | all nine stages (`rows-p2`), `probe` |
| M2 | A stand-in parser and engine let stage 1 pass. | stage 1, as `XPASS(strict)` |
| M3 | A stage's marker loses `strict=True`, or `raises=Blocked`. | the marker audit |
| M4 | A stage's reason names a seed that does not exist. | the marker audit, `rows-p2` |
| M5 | `difference` compares names only. | `test_oracle_reports_each_kind_of_difference[bytes]` |
| M6 | The tap classes `POST` as a read. | `test_tap_counts_each_kind_once_and_forwards_bytes` |
| M7 | `Loop.__init__` stops closing what it made when a step raises. | `test_failed_setup_leaves_nothing` |
| M8 | `main` stops handling `SIGTERM`. | `test_terminate_tears_down` |
| M9 | One stage is deleted from `test_scenario_minio.py`. | `collect`, the marker audit |
| M10 | A stage excuses itself: `reach` calls `pytest.xfail`, or raises `Blocked` by hand. | `rows-p2`, on `stopped` and `cause`; the syntax-tree test |
| M11 | The scene builds its loop on the store's endpoint, not the tap's. | every stage, at the scene's assertion |
| M12 | `Loop.close` ignores a directory it cannot remove. | `test_close_fails_loudly_when_the_directory_survives` |

**Dry runs.** On the prototype, at `bf438b4`, under the old names, with a copy of the gate in which
one import and the `TERRACE_TEST_MINIO_` prefix were renamed. Before the review: `p1` on a tree cut
back to the helper and its tests; `rows-p2`, `probe` and `figures-p2` on one cut back to P1 and P2;
`lane` on the full tree; `tools/lint.sh` on the full tree. After the review's changes, on the full
tree: `rows-p1`, `rows-p3`, `variant`, `collect`, `ledger` and `figures-p3` passed, and four of the
reviewer's plants were replayed through `rows-p2`, all red. Not re-run after the review: the
cut-back trees and `lane`. Not dry-run at all: `tools/lint.sh` with `tools` added to its paths
(ruff over `tools` alone passed), and anything under the new names.

---

*No backing research: lean path. The drafts are in [`draft/`](draft/) and the gate in
[`verify/`](verify/).*
