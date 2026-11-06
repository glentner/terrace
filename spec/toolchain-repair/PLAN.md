# PLAN — Repair the toolchain so the verify gate runs on a fresh machine

> **Status:** Draft for review · **Last updated:** 2026-11-05
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). The gates are drafted in
> [`verify/toolchain_gate.py`](verify/toolchain_gate.py). There is no `research/`: the root causes are
> known and the appetite is small, so the evidence is recorded here (§ 4).

## 1. Summary

Four test-and-tooling repairs and one CI change. No file under `src/` changes. The dev group drops
`moto[server]` for `moto[s3]` plus the HTTP packages the in-process server needs, which takes
`pydantic-core` out of the lock: every locked package then installs from a wheel on 3.15 except the
project and `pyyaml`. The S3 fixture's imports become optional, so a missing package skips the S3 tests
by name and leaves the POSIX tests running. The ownership assertion compares against what a plain create
in the same tier receives. `tests/fuse` skips each of its tests, saying why, where libfuse cannot be
loaded. CI then runs on cycle branches, requires libfuse on Linux, and is read from a run before review.

## 2. Design

### 2.1 Dependencies (`pyproject.toml`, `uv.lock`) — R1, R6

`moto[server]` is the only path to `pydantic-core`. The chain runs `moto[server]` → `cfn-lint` →
`aws-sam-translator` → `pydantic~=2.13.3`, and `moto[server]` → `openapi-spec-validator` →
`pydantic`. pydantic 2.13.x pins pydantic-core 2.46.x, and pydantic-core publishes cp315 and cp315t
wheels only from 2.48.0 on (PyPI JSON, 2026-11-05: 2.48.0, 2.49.0, 2.50.0–2.50.4). `aws-sam-translator`'s
newest release, 1.116.0, still requires `pydantic~=2.13.3`, so no re-lock with `moto[server]` can reach a
wheel. A `uv lock --upgrade-package pydantic` measured it: it stops at 2.13.5 and pydantic-core 2.46.5.

The tests use none of what `cfn-lint` and `openapi-spec-validator` serve. `tests/tier/conftest.py`
starts `moto.server.ThreadedMotoServer` and speaks S3 to it. That server needs moto's core, its `s3`
extra (`PyYAML`, `py-partiql-parser`) and `flask` with `flask-cors`, which are the HTTP half of the
`server` extra. The dev group becomes:

```toml
dev = [
    "pytest>=8",
    "pytest-xdist>=3",
    "hypothesis>=6",
    "boto3",          # tests/tier/conftest.py imports it directly
    "flask",          # moto.server's HTTP layer
    "flask-cors",
    "moto[s3]>=5",
]
```

`uv lock` then removes 35 packages and adds or upgrades none (measured on a scratch copy). A targeted
`uv lock --upgrade-package cffi --upgrade-package markupsafe` moves `cffi` 2.0.0 → 2.1.1 and `markupsafe`
3.0.3 → 3.0.4, the first releases with cp315/cp315t wheels. After both, the only sdists left are the
project itself and `pyyaml` 6.0.3, which publishes no cp315 wheel at any version and builds with a C
compiler or falls back to pure Python. Nothing in the lock needs Rust on regular 3.15. The S3 tests keep
the same server and the same moto version (5.2.2), so their coverage is unchanged.

### 2.2 S3 fixture imports (`tests/tier/conftest.py`) — R5

`boto3` and `moto.server.ThreadedMotoServer` move into a `try`/`except ImportError` at module scope.
The handler records one reason string, `f'S3 tier tests need {error.name}: {error}'`, which names the
module that failed. The `need <module>` slot is what the gate checks, so the reason must not hard-code a
package name. The session-scoped `moto_endpoint` fixture calls `pytest.skip(reason)` when it is set. Every S3 test
reaches the server through `moto_endpoint`, directly or through `s3_bucket`, `s3_spec`, `store` or
`tier`, so each one is reported as skipped by name: `test_s3.py` (21), `test_s3_engine.py` (8), and the
S3-backed tests in `test_archive.py` (7). `test_posix.py` and the other modules import nothing from
the guard and run. Only `ImportError` is caught. Any other failure while importing moto stays a loud
collection error.

### 2.3 Ownership expectation (`tests/tier/test_posix.py`) — R7

`put` passes no `uid`/`gid` in `FileMeta`, so `_apply_meta` never calls `chown`. The file keeps the
ownership the kernel gave its temp at create time under `.sosfs/partial/`. That is the process's uid,
and a group that depends on the directory: the directory's group under BSD semantics (macOS) or on a
set-gid directory, otherwise the process's effective gid. `os.getgid()` encodes only the last case.

The test keeps its kind, size, mode and mtime assertion as it is. It still asserts `uid` against
`os.getuid()`, and asserts `gid` against the group of a reference file it creates with a plain
`touch()` in the tier root. In every one of the three semantics the reference and the temp's directory
resolve to the same group: the partial directory is created inside the root by the same process, so
it inherits under BSD and set-gid rules and takes the effective gid otherwise. The expectation is
computed independently of `PosixTier`, so a `put` that chowned the file anywhere else would fail it.
§ Invariants § 4 makes ownership best-effort; this assertion checks that a `put` asking for no
ownership changes none.

Rejected: the tier root's own `st_gid`. On Linux, a root whose group differs from the process's without
the set-gid bit gives a new file the process's gid, not the root's, and the test would fail there.

### 2.4 libfuse skip (`tests/fuse/conftest.py`, `tests/fuse/test_adapter.py`) — R3, R4

`tests/fuse/conftest.py` probes once at import, with `import mfusepy` inside `try`/`except OSError`.
`mfusepy` raises `OSError` both when it finds no library ("Unable to find libfuse") and when `dlopen`
fails. The planning Mac shows the second case: a leftover `libfuse.dylib` whose framework is gone. The
reason string starts `libfuse cannot be loaded:` and carries the error.

The skip is a `pytest_collection_modifyitems` hook in that conftest. It filters items to those under
its own directory and prepends `mark.skipif(True, reason=…)` with `add_marker(..., append=False)`.
Prepending matters. `test_integration.py::test_chown_propagates_to_source_after_flush` carries its own
`skipif(os.getuid() != 0, reason='chown … requires privilege')`. pytest reports the first true
`skipif` it meets, so an autouse-fixture skip (drafted first) reported that reason instead of libfuse
on a run without `-m`. The prepended marker is evaluated first. The `operations` fixture imports
`OverlayOperations` inside its body, with a `TYPE_CHECKING` import for the annotation.

`test_adapter.py` imports `mfusepy.ENOATTR` and five adapter names at module scope, and every test uses
them. Those two imports move into `try`/`except OSError: pass`, with a comment pointing at the conftest.
Where libfuse loads, the import runs exactly as today and R4 holds unchanged. Where it doesn't, the
module still collects, so each test can be reported as skipped by name.

Measured on the prototype on the planning Mac: 44 skipped under `-m unit` and under
`-m 'not integration'`, 51 under no `-m`, every reason naming libfuse, 0 errors. The unchanged tree
errors at collection.

### 2.5 CI (`.github/workflows/ci.yml`) — R9, plus CI evidence for R1, R3, R4, R6

- `on.push.branches` becomes `[main, '*/**']`: `main` plus every branch with a slash, which is every
  `{kind}/{slug}` cycle branch in the profile's open set. The retired `wip` is dropped.
  `pull_request` and `workflow_dispatch` stay.
- Both unit jobs run `uv sync --locked` instead of `uv sync`. The run then fails if the lock would
  change, so R1's "uv.lock unchanged" can be read from the run on Linux and on macOS.
- The Linux matrix job gets a step named `Assert libfuse loads` after `Install project`:
  `uv run python -c "import mfusepy"`. The skip path in § 2.4 is silent by design, so without this
  step a runner image that lost libfuse would retire all 44 adapter and mount tests and the job would
  stay green. If the step fails because the runner lacks the library, the remedy is an `apt-get install`
  step for the FUSE 3 runtime ahead of it, made in P5's Run A loop. The test package keeps its skip.
- The pytest steps add `-rs`, so the run record lists each skip reason. The macOS job's log then shows
  that its only skips are the libfuse ones. This adds a report flag and nothing else. The release
  recipe's `gate`, which mirrors CI, keeps its command, because selection and outcome are the same.
  - 2026-11-05 (P5 Run A): the flag is `-rfEs`. `-r` replaces pytest's default `fE`, so `-rs` alone
    would drop the `FAILED` lines that name a unit job's only failure under amended R9.
  - 2026-11-05 (review cycle 1, F3): the record lists one `SKIPPED [n] file:line: reason` line per
    skip location, `n` folding the tests skipped there, not one per reason.
- Job names do not change.

The same commit updates `AGENTS.md`, as the same-commit rule requires: the § Factory profile's `ci` row
gains "and every cycle branch", and the § Commands paragraph on CI gains a sentence on the libfuse
assertion and the macOS job's skips.

### 2.6 Pushing and reading the run (P5) — R9

Per the GOAL-gate ruling, a build phase pushes the cycle branch under the standing approval for cycle
branches, and review grades R9 from that run's record. The push waits on outside jobs, so P5 is two
runs (code-foreman → "External waits split the run"). Run A edits the workflow and `AGENTS.md`,
commits, runs the `prepush` gate, pushes `fix/toolchain-repair`, and stops with the phase
`in_progress`. Run B starts once the run for that commit has finished. It runs the `ci` gate against
the run record for `HEAD`, which must be the pushed commit, and then the ledger check.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | § 2.1 dev group and lock; P1 `deps` gate (fresh cache, lock byte-identical, no pydantic-core, source builds limited to the project and pyyaml); § 2.5 `uv sync --locked` on CI |
| R2 | §§ 2.2–2.4 together; P4 `suite` gate runs the profile's verify with 0 failed, 0 errors, nonzero passed |
| R3 | § 2.4 prepended skip marker; P3 `fuse` gate on three selections; CI macOS log via `-rs` |
| R4 | § 2.4 import unchanged where libfuse loads; § 2.5 `Assert libfuse loads`; Linux run summaries with no skip (`ci` gate) |
| R5 | § 2.2 guarded imports and fixture skip; P2 `tier` gate with `boto3`, then `moto.server`, made unimportable |
| R6 | § 2.1 keeps the server and moto version; P1 `deps` gate (0 skipped in the S3 modules); Linux and macOS run summaries (`ci` gate) |
| R7 | § 2.3 reference-file expectation; P2 `tier` gate runs the test under a `--basetemp` whose group differs from the process's |
| R8 | §§ 2.2–2.4; P4 `suite` gate: ≥ 405 passed outside `tests/fuse` under `-m 'not integration'`, 0 failed |
| R9 | §§ 2.5–2.6; P5 `ci` gate: the four named jobs `success` for the pushed `HEAD` |

## 3. Invariant gate (AGENTS.md constitution check)

Checked before design and again against the drafted design and the prototype. Sections touched:

- **§ 4 Tier atomicity: "Ownership is best-effort; mode and mtime are not."** R7 keeps the mode and
  mtime assertion as it is and asserts ownership against the create-time default, which is what an
  unprivileged `put` without `uid`/`gid` must leave. `PosixTier.put` and `_apply_meta` do not change.
- **§ 9 Layering and import discipline: "libfuse and obstore load only when actually used."** The test
  side now follows the same rule. The FUSE conftest imports the adapter inside the fixture, and the
  probe is the only module-scope load, guarded. No `src/` import moves, and `tools/lint.sh`'s
  layering check is untouched.
- **§ 10 Free-threaded safety.** The `3.15t` job keeps its Rust toolchain and its GIL assertion,
  which imports `sosfs` and `obstore`, neither touched here. The lock change removes packages and adds
  cp315t wheels (`cffi`, `markupsafe`), so 3.15t compiles less, not more. `obstore` and `cryptography`
  still compile from source there, as they do today, because neither publishes a cp315t wheel.
- **§ Conventions.** Same-commit rule: the `ci.yml` change and its `AGENTS.md` lines ship in one
  commit (P5). Every test module keeps its `pytestmark`. No spec ids in `tests/`. SPDX, import groups
  and the comment voice per § Code style. `tests/tier` stays a non-package.
- **§ Verification.** Every gate asserts counts from pytest's JUnit report, never an exit code alone.
- **High-blast-radius paths.** None touched: the change edits tests next to `tier/posix.py`, not
  `tier/posix.py`, and neither listed test module.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| The dev group names `boto3`, `flask`, `flask-cors` and `moto[s3]` where it named `moto[server]` (four entries for one) | `moto[server]` forces `pydantic~=2.13.3` through `aws-sam-translator`, and 2.13.x has no cp315 wheel | Overriding pydantic (`override-dependencies`) installs a combination `aws-sam-translator` declares unsupported, for a package the tests never import. Keeping `moto[server]` and building pydantic-core from source needs Rust on every machine. Waiting for `aws-sam-translator` has no date |
| CI gains an `Assert libfuse loads` step in the Linux unit jobs | § 2.4's skip is silent by design. On Linux it would hide the 44 adapter and mount tests behind a green job | A run-summary check holds only for this cycle's review run and guards nothing after landing |
| CI unit jobs use `uv sync --locked` and `pytest -rs` | They make R1's unchanged lock and R3's skip reasons readable from the run record, the only Linux evidence the cycle has | Grading R1's lock clause on the Mac alone leaves R1's "or Linux" unobserved |
| `test_adapter.py` imports `mfusepy` and the adapter inside `try`/`except OSError: pass`, leaving names unbound where libfuse is absent | R3 requires each test to be reported as skipped. A module that fails import reports nothing per test | `importorskip` at module scope reports one skip per module. A module-level `skip` in the conftest reports one skip for the directory, and `pytest tests/fuse` then aborts with a traceback (both measured) |

Each row was read against the R-IDs it touches. Row 1 (R1, R6): the S3 tests still run against
`ThreadedMotoServer` in the environment R1 installs, as R6 words it. Row 2 (R4, R9): the step makes a
job fail only where R4's precondition, a loadable libfuse, is missing on Linux. It changes none of the
four job names R9 grades. Row 3 (R1, R3, R9): stricter, and graded the same way. Row 4 (R3, R4):
reporting per test is R3's own wording, and where libfuse loads the import runs as today. No row
changes what an R-ID SHALL do.

## 4. Rabbit holes (resolved)

Research triggers: lean path (`kind: fix`, `appetite: small`). Trigger 1 did not fire, because the
GOAL measures every root cause. Trigger 2 did not fire, because no high-blast-radius path is touched.
**Trigger 3 fired**: the deliverables are test infrastructure, a CI step and gate scripts. Evidence came
from targeted reads and scratch experiments, with no research fan-out.

- **Can a re-lock reach a pydantic-core wheel?** No, while `moto[server]` is in the group: see § 2.1.
  PyPI's JSON API lists cp315 wheels from pydantic-core 2.48.0. pydantic 2.14.0 is the first to pin
  2.50.x, and `aws-sam-translator` 1.116.0 caps pydantic below 2.14.
- **Does the server need more than flask?** No. The GOAL's baseline ran the S3 tests with `moto`,
  `flask`, `flask-cors` and `boto3`. On the prototype, `moto` 5.2.5 with those packages runs all 36
  S3-backed tier tests.
- **What does the lock look like after the swap?** 66 packages, down from 101. No pydantic or
  pydantic-core, no Rust sdist. The remaining sdists are the project and pyyaml.
- **How do the skips report?** Measured with pytest 9.2 in scratch projects. A module-level `skip` in
  a conftest gives `1 skipped` for the directory and a traceback when that directory is the argument.
  A `pytest_collection_modifyitems` marker reports each test. An autouse-fixture skip loses to a test's
  own true `skipif` (the no-`-m` run's `test_chown_…`).
- **R7's oracle.** See § 2.3, including the rejected root-gid variant.
- **3.15t.** See § 3, § 10.
- **Adversarial design review (trigger 3).** See the review record below.

### Adversarial review record

A fresh reviewer had the GOAL, this design, the drafted gate and the prototype. It ran the `tier`,
`fuse`, `ci` (offline) and `ledger` modes against mutants built in scratch copies. Twelve findings came
back. Each is folded in as follows:

1. **`prepush` matched its own source** (HIGH). The home-path pattern contained the literal it searches
   for, so it was red as soon as the gate was committed. The pattern is now written so its line cannot
   match itself. It was checked against the gate and every spec file: no hits.
2. **R7's assertions had no teeth in the gate** (HIGH). A tautological oracle (`result.gid` against
   itself) and a test with every metadata assertion deleted both passed `tier`. `tier` now loads two
   probe plugins (`-p`, from a scratch `PYTHONPATH`, with no tree edit). One chowns the file to the
   primary gid after `_apply_meta`; the other sets a wrong mode and mtime. Under each probe the test
   must **fail**. Both reviewer mutants are now red, and the prototype is green. The probes replace
   TECH's manual `src/` mutation.
3. **Offline `ci` was bound to nothing** (HIGH). The `--jobs-json` input carried no SHA, branch or event,
   so a fabricated file passed. `--run-json` now requires `gh run view --json
   databaseId,headSha,headBranch,event,status,conclusion,jobs`, and the run must match the pushed SHA,
   the branch and `push`, and be completed. A jobs-only file is "could not run". Dry-run: red on a
   wrong SHA, could not run on a jobs-only file.
4. **The caller's `UV_*` could hollow the gates** (MEDIUM). `UV_FROZEN` makes R1's lock check vacuous,
   and `UV_NO_SYNC`/`UV_PROJECT_ENVIRONMENT` let a stale venv answer (the planner's dry-run used moto
   5.2.5 and the main checkout's sources). Every mode now refuses to run under `UV_FROZEN`, `UV_LOCKED`,
   `UV_NO_SYNC` or `UV_OFFLINE`, and strips `PYTHONPATH` from child processes. `tier`, `fuse` and
   `suite` first run `uv sync --locked`, which is exact, so the environment is the lock's and the
   editable install points at the tree under test.
5. **CI summaries had no pass floor** (MEDIUM). A Linux run with `tests/fuse` uncollected passed. Each
   Linux job must now report ≥ 449 passed (AGENTS.md's unit count), and macOS passed + skipped must
   equal Linux 3.15's passed. Dry-run: red on `405 passed, 54 deselected`.
6. **`ci` was bound to `HEAD`** (MEDIUM). Run B's own state commit moves `HEAD` off the pushed commit.
   `ci` now grades the branch's remote-tracking commit (the last push), or `--sha`, and every file
   changed between it and `HEAD` must sit under `spec/toolchain-repair/`. Dry-run: a code commit after the push is red.
7. **A skip hook leaking past `tests/fuse` passed `fuse`** (MEDIUM-LOW). `fuse` now also runs
   `tests/tier/test_posix.py` next to `tests/fuse` and requires it to pass in full with 0 skipped. The
   reviewer's unfiltered-hook mutant is now red.
8. **The R5 reason check passed trivially for boto3** (LOW). The check now requires `need <module>`, the
   `error.name` slot (§ 2.2).
9. **R7's Linux foreign-group, non-set-gid case runs nowhere** (LOW). Accepted as a residual risk
   (§ 5). The reasoning in § 2.3 covers it, and the reviewer checked that reasoning independently.
10. **`xfailed` and ANSI colour gave false reds** (LOW, safe direction). The failure match now excludes
    `xfailed`, and logs are stripped of ANSI before parsing.
11. **CI's "fresh machine" depends on a uv cache miss** (LOW). Accepted (§ 5): the lock changes in this
    cycle, so the first push misses the cache keyed on it.
12. **A § Factory row edited inside a `[fix]` cycle** (process). Raised as a question (§ 5).

The reviewer confirmed these hold: the `'*/**'` trigger, the CI ruff selection, the `Built`-line parse,
no rename and no `src/` change, R7's oracle across all three group semantics, and that R7 is not a
loosening. Its R7-teeth `src` mutant, an `append=True` skip marker and a short macOS skip count were all
caught by the drafted gate.

## 5. Risks & open questions

- **Gates and the agent sandbox.** During planning (2026-11-05), cross-parent directory renames and a
  fresh-cache `uv sync` hung, and the plan blamed the agent sandbox on the development Mac. It was a
  transient machine-wide kernel stall that hung the same renames outside the sandbox as well; P4 ran
  `suite` sandboxed in about 9 s. The sandbox is not known to block any mode; running `deps`, `suite`
  or `prepush` outside it stays approved if one is needed (GOAL Clarifications).
- **R7's Linux foreign-group, non-set-gid case is exercised nowhere** (review finding 9). The gate runs on
  the Mac (BSD semantics), and CI makes no foreign group. § 2.3's reasoning covers the case.
- **CI's fresh-machine evidence rests on a cache miss** (review finding 11). `setup-uv` restores a cache
  keyed on `uv.lock`. This cycle changes the lock, so the first push run is cold. A re-run would not be.
- **Linux runner libfuse.** If `ubuntu-latest` lacks a loadable libfuse, `Assert libfuse loads` fails
  and R9 is red until the FUSE 3 runtime is installed in the workflow (§ 2.5). The foreman's read of
  run 42023846602's Linux jobs, still running at planning time, answers this before P5.
- **3.15t compiles `cryptography` and `obstore` from source.** That predates this change, and the
  job's Rust toolchain covers it. A failure there in Run B is read and fixed within P5, or escalated
  if it is outside this GOAL.
- **R9 amended at the plan gate (2026-11-05).** `main`'s run 42023846602 shows both Linux unit jobs
  failing on `tests/engine/test_model.py` alone: a namespace-replay defect in `_execute_namespace`,
  outside this GOAL. R9 now accepts that one test as a unit job's only failure; the defect moves to
  `issues/deferred-rename-source-orphan.md`, which is next on the roadmap. P5 Run A amends the `ci`
  mode to match (TECH P5). The same run shows libfuse loading on the Linux runners, so the
  `apt-get` contingency in § 2.5 is not expected to be needed.
- **Code changes after the push.** R9 is graded on the pushed tree. A review remediation that touches
  code or CI needs another push and another read: P5's two runs again.
- **Question for the foreman (not blocking):** P5 edits the § Factory profile's `ci` row inside a
  `[fix]` cycle. The profile names `[factory]` as the category for § Factory changes, and landing is
  one squash commit. Either land as `[fix]`, with the row change riding along under the same-commit
  rule, or leave the row alone (it stays true, only incomplete) and file the wording for a harness pass.
- **Question for the foreman (not blocking):** R3 as worded skips `tests/fuse/test_mount.py`'s 17 unit
  tests on a host without libfuse, though they drive `Mount` through a fake runner and import only
  `sosfs.fuse.mount`, which loads libfuse lazily. They do not run on such a host today (collection
  error), so the plan weakens nothing. Running them on macOS would contradict R3's "every test under
  `tests/fuse`" and needs a GOAL change if wanted.

## 6. Verification strategy

One script, [`verify/toolchain_gate.py`](verify/toolchain_gate.py), with a mode per slice. It is run
as `uv run --no-project --python 3.15 python -I spec/toolchain-repair/verify/toolchain_gate.py <mode>`.
Every pytest run writes an xunit1 JUnit report, which carries each test's file, and the script counts
from that report. Exit 0 is green, 1 is red, 3 is "could not run" and never counts as green.

Every mode refuses to run under `UV_FROZEN`, `UV_LOCKED`, `UV_NO_SYNC` or `UV_OFFLINE`, and strips
`PYTHONPATH` from child processes. `tier`, `fuse` and `suite` start with `uv sync --locked`, so they run
in exactly the locked environment, with the editable install pointing at the tree under test.

- `deps` (P1): a Python 3.15 **final** interpreter is required. Then a fresh-cache, fresh-venv
  `uv sync`, with `uv.lock` byte-identical afterwards, no `pydantic-core` in it, and `Built` lines only
  for the project and pyyaml. Then the S3 modules run in that venv with 0 skipped and per-file floors
  (21, 8).
- `tier` (P2):
  - `test_posix.py` runs under a `--basetemp` whose group is not the process's. The gate makes such a
    directory with `chown` to a supplementary group and set-gid where needed, and checks afterwards
    that the test's own `tmp_path` had a foreign group.
  - Two probe plugins must each turn `test_put_and_stat_applies_meta` red: one with a wrong gid, one
    with a wrong mode and mtime.
  - `tests/tier` then runs twice, with `boto3` and then `moto.server` set to `None` in `sys.modules`.
    Every `test_s3*.py` test is skipped saying `need <module>`, every skip says so, and `test_posix.py`
    passes in full.
- `fuse` (P3): `tests/fuse` runs next to `test_posix.py`, which must pass in full with 0 skipped. Then
  the mode branches on whether `import mfusepy` succeeds. Without libfuse (R3): `-m unit`,
  `-m 'not integration'` and no `-m` each report every test skipped with "libfuse" in the reason, with
  floors of 44, 44 and 51. With libfuse (R4): `-m unit` reports ≥ 44 passed and 0 skipped.
- `suite` (P4): `tools/lint.sh`, then the profile's `pytest -n auto -m unit` with 0 failed, 0 errors
  and passed > 0, then `-m 'not integration'` with ≥ 405 passed outside `tests/fuse`.
  - 2026-11-05 (review cycle 1, F1, R2 and R8 amended): `tests/engine/test_model.py::TestOverlayModel::runTest`
    may fail, matched by JUnit classname and name; no other test may fail, errors stay 0, and it
    counts toward the 405 whether it passes or fails.
- `claims` (review cycle 1, F2 and F3): no live text says the `test_model` defect spares macOS, and
  `AGENTS.md` and `ci.yml` describe `-rfEs`'s folded `SKIPPED [n] file:line: reason` line.
- `prepush` (P5 Run A): a clean tree, then `suite`, then no home-directory path in the branch's diff
  or commit messages.
- `ci` (P5 Run B, and review; the commit defaults to `origin/fix/toolchain-repair`, the last push):
  - Files changed after the pushed commit may only be under `spec/toolchain-repair/`.
  - The newest push-event run of `ci.yml` for that SHA on the cycle branch, completed.
  - All four named jobs `success`.
    - 2026-11-05 (P5 Run A, amended R9): `Lint & build` `success`; a unit job may instead be `failure`
      in its `Unit tests` step alone, with `tests/engine/test_model.py::TestOverlayModel::runTest` its
      only `FAILED`/`ERROR` line. That test counts toward the Linux floor and the macOS parity sum.
  - The Linux jobs: `Assert libfuse loads` `success`, and ≥ 449 passed with no skip, failure or error.
  - macOS: no failure or error, passed + skipped equal to Linux 3.15's passed, and every `-rs` skip
    line under `tests/fuse` naming libfuse, those lines summing to the skipped count (≥ 44).
  - It reads the run with `gh`. `--run-json` and `--log-dir` take saved `gh run view --json …` output
    and job logs instead, and the same SHA, branch, event and status checks apply to them.
- `ledger` (P5 Run B): every deferral line in TECH's phase bodies names an `issues/` seed that
  `ROADMAP.md` lists.

Dry-run on the planning Mac, in scratch copies of the prototype:
- `tier` and `fuse`: green on the prototype, run with the initial sync stubbed because the sandbox
  stalls it. Red on the unchanged tree and on the reviewer's mutants.
- `ci`: offline fixtures, green and red as listed in § 4.
- `ledger`: green, with no deferrals.
- `deps`, `suite` and `prepush`: could not run here (§ 5).

---

*No backing research directory: lean path. Evidence is inline in § 4.*
