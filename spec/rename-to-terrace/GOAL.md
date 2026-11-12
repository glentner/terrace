# GOAL — Rename `sosfs` to Terrace

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** rename-to-terrace
- **kind:** refactor
- **appetite:** big

## Problem

The project has carried a placeholder name since its first commit. The root `GOAL.md:5` says "We
only have the name 'simple overlay service' as a funny place-holder until a better name is chosen."
The name is now chosen: Terrace. The repository is already `glentner/terrace` and the factory
already addresses it by that name, while the tree still says `sosfs` everywhere. This is unbuilt
work, not a defect: nothing on `main` is broken by the current name. The cost of waiting only grows.
Every later cycle writes more occurrences, and the on-disk half of the change is free only while
nothing has been released and no overlay is deployed.

`sosfs` is not one string. It is five contracts that share a spelling:

1. **The distribution, the console script and the import package** (`pyproject.toml:6`, `:35`,
   `:67`; the 34 tracked files under `src/sosfs/`, imported by every module in `src/` and `tests/`).
2. **Identifiers:** the application class `SOSFSApp` (`src/sosfs/__init__.py:212`) and the base
   exception `SOSFSError` (25 occurrences), plus the logger and application names.
3. **On-disk artifacts inside every managed tier root:** the marker directory, the partial
   directory and the S3 sentinel.
4. **The configuration surface:** the `SOSFS_` environment prefix, the configuration file names,
   and the per-user and system-wide paths.
5. **A proper noun in prose:** "Simple Overlay Service" in `README.md`, `PLAN.md` and the package
   metadata.

Organizing the work by kind of occurrence is the difficulty; the volume is not. Measured with
`git grep` over tracked files at the cycle's base commit `bf438b4`: 1195 case-insensitive
occurrences of `sosfs` in 167 of 199 tracked files, 1098 lowercase and 97 uppercase, with no
mixed-case form. Under the census scope this GOAL adopts (R1), 788 matching lines in 116 files:

| Area | Lines | Files |
|---|---:|---:|
| `issues/` (live seeds) | 282 | 34 |
| `src/` | 195 | 32 |
| `tests/` | 192 | 40 |
| `PLAN.md` | 43 | 1 |
| `AGENTS.md` | 32 | 1 |
| `tools/` | 21 | 2 |
| `ROADMAP.md` | 8 | 1 |
| `pyproject.toml`, `docker-compose.yml`, `README.md`, `uv.lock`, `.github/workflows/ci.yml` | 15 | 5 |

The substring `sos` occurs nowhere in tracked files except inside `sosfs` (the only other hits are
the seed quoting the check), so a blanket substitution corrupts no unrelated identifier. The hazards
are elsewhere, and each was traced by reading the code path named. Nobody has performed the rename
and watched a mount refuse.

**Case collapse.** One lowercase rule turns `SOSFSApp` into `TERRACEApp`. The class names, the
upper-case prefix and the lower-case name are three different substitutions.

**Literals a directory move does not fix, which fail at runtime and not at import-check time.**
`src/sosfs/__init__.py:27` `get_version('sosfs')` raises `PackageNotFoundError` at import if the
distribution is renamed and this is not; `:32` and `src/sosfs/core/logging.py:191` name the logger;
`:183` is `APP_NAME`; `src/sosfs/core/exceptions.py:86` defaults `modname`;
`src/sosfs/state/db.py:52` finds `schema.sql` through `resources.files('sosfs.state')`;
`tests/__init__.py:23` runs `-m sosfs`; and two monkeypatch targets are dotted strings
(`tests/fuse/test_adapter.py:222`, `tests/fuse/test_mount.py:259`).

**The marker directory.** `src/sosfs/state/lifecycle.py:125` is `MARKER_DIR = '.sosfs'`, and every
managed tier root holds `.sosfs/marker`. Once the constant moves, a root written under the old name
reads as having no marker. `Registry._attach` (`src/sosfs/state/registry.py:118-139`) then branches
on whether the state database already holds the tier's row. If it does, `_attach` returns
`MarkerStatus.MISSING` (`:136`) and the engine raises `Tier '<name>' marker is missing — audit
required` (`src/sosfs/engine/overlay.py:145-146`): loud, and no data touched. If it does not,
`_attach` writes a fresh marker (`:137`) and adopts the root as new.

**The partial directory and the S3 sentinel.** `src/sosfs/state/journal.py:34` is
`PARTIAL_DIR = '.sosfs/partial'`, and the recognizer at `:44` compares against a second, unlinked
literal `('.sosfs', 'partial')`: move one without the other and a live transfer temporary is taken
for user data. `src/sosfs/tier/s3.py:38` is `KEEP = '.sosfs.keep'`, the zero-byte object that makes
an empty prefix a directory. The three adapter guards (`tier/posix.py:63-64`, `tier/s3.py:108-109`,
`tier/archive.py:269-270`) now compare against `MARKER_DIR`, but their docstrings and the two test
oracles (`tests/engine/test_model.py:46`, `tests/engine/test_replay.py:167`) carry the literal. An
oracle left behind makes the stateful model report divergence for a reason unrelated to the
operation under test.

**The extended attributes** are the one user-facing API in the diff:
`src/sosfs/fuse/adapter.py:32-33` define `user.sosfs.tiers` and `user.sosfs.dirty`, and `:136-142`
raise `ENOATTR` for every other name. A script that reads "is this file committed yet?" through the
old name gets an error after the rename.

**The environment prefix is three unlinked literals** in `src/sosfs/core/config.py`: the prefix at
`:100`, the provenance string at `:145`, and the bootstrap override `SOSFS_CONFIG_FILE` at `:199`. A
stale provenance string survives a green test run. Three test-isolation literals depend on the same
spelling (`tests/conftest.py:21`, `tests/__init__.py:22`, `tests/test_cli.py:68`), and so does the
scrub in `tools/temp_site.sh:61`. Miss one and a developer's exported variables leak into every
test: green on a clean machine, red on the maintainer's.

**The paths** are one literal block, `src/sosfs/core/platform.py:42-52`: nine entries for the
system, user and local configuration, log and state locations. An off-by-one there leaves
configuration read from one project's directory and logs written to another's. The per-user
entries already derive from `XDG_CONFIG_HOME` and `XDG_STATE_HOME` (`:35-36`); the code has no cache
or data directory.

**What must not move.** The state database is `state.db` (`src/sosfs/engine/overlay.py:44`) and no
table, column or value embeds the project name; `schema.sql` mentions it in two comments (`:4`,
`:41`). `PRAGMA user_version` is 2 (`schema.sql:14`, `state/db.py:34`) and there is no migration
machinery: `db.py:86` refuses a mismatch outright, so a version bump would refuse databases that are
fully compatible. `[overlay].state` is a required spec key with no default
(`src/sosfs/tier/spec.py:286`), so the state database lives where the user's spec says and is not
moved by renaming a default directory.

**A recorded decision is overturned.** `PLAN.md:500` records the package and entry point as a
technology decision: `sosfs`, "Per GOAL.md; repo name is a placeholder and unimportant." The rename
contradicts that row, so under the plan-drift rule (`AGENTS.md:47-50`, and the pending-rename notice
at `AGENTS.md:10-16`) the cycle reconciles `PLAN.md` before it touches code. The rename also passes
textually through every file on the high-blast-radius list in `AGENTS.md` § Invariants, which is why
its plan runs research and its review carries the human gate.

## Outcome / vision

One name, everywhere, in one cycle. The proper noun is **Terrace**. The distribution, the import
package, the console script, file names and code identifiers are `terrace`; the classes are
`TerraceApp` and `TerraceError`; the environment prefix is `TERRACE_`. A managed tier root holds
`.terrace/`, a file's tiering state reads from `user.terrace.*`, and configuration and state live
under `terrace` directories that follow the XDG convention.

The break is deliberate and written down. There is no compatibility code: an overlay written under
the old name is refused loudly where the code can already tell, and `CHANGELOG.md` names each thing
that moved. The schema is untouched. The seeds, the roadmap and the agent guide keep resolving
because their paths, commands and variables moved with the code. `PLAN.md` records the new name and
why the earlier decision was overturned.

## Acceptance criteria (the contract)

Terms used below. **Old name:** `sosfs` in any letter case, and "simple overlay service" in any
letter case with any single character between the words. **Base commit:** `bf438b4`, the commit the
cycle branch was cut from.

Grading an old name that is no longer honoured. R1 covers `tests/` and `tools/`, so no tracked test
or script may spell an old name. Where a criterion says an old-named thing has no effect, the rule
the code implements is the general one (a name outside the new namespace is not honoured), and the
review drives the old spelling itself from an untracked script and records the result in this
cycle's `spec/` records. R10 holds the unit count to the base commit's, so a behaviour that has no
unit test today is graded by such a drive and not by a new test.

- **R1** — On the cycle's final commit, the following SHALL print nothing:

  ```
  git grep -nIi -e sosfs -e 'simple.overlay.service' -- . \
    ':!GOAL.md' ':!CHANGELOG.md' ':!spec' ':!issues/rename-to-terrace.md'
  ```

  `git ls-files | grep -i sosfs` SHALL print nothing, and the root `GOAL.md` SHALL be byte-identical
  to the base commit. The scope is tracked files; `uv.lock` is regenerated with `uv lock` and is in
  scope.
- **R2** — The import package SHALL be `terrace` (`src/terrace/`), its application class
  `TerraceApp` and its base exception `TerraceError`, with no identifier of the collapsed form
  `TERRACE` followed by a letter. WHEN `uv run terrace --version` is invoked after `uv sync`, the
  CLI SHALL exit 0 and print a line matching `terrace v\d+\.\d+\.\d+`; `uv run python -m terrace
  --version` SHALL print the same line; and `uv run terrace --help` SHALL exit 0 and list the seven
  subcommands `mount`, `umount`, `status`, `flush`, `commit`, `audit` and `config`.
- **R3** — WHEN `dist/` is empty and `uv build` is run, the build SHALL produce exactly two files:
  one sdist `terrace-0.1.0.dev0.tar.gz` and one wheel `terrace-0.1.0.dev0-*.whl`. The wheel's
  metadata SHALL name the distribution `terrace` and give the repository URL
  `https://github.com/glentner/terrace`, as SHALL `pyproject.toml` and the package's `__website__`.
  `uv lock --check` SHALL exit 0.
- **R4** — WHEN an overlay's stack is attached over empty tier roots, each root SHALL hold its
  managed-location marker at `.terrace/marker`; a transfer temporary SHALL be written under
  `.terrace/partial/` and recognized as one by that name; and an S3 tier SHALL mark an empty
  directory with a `.terrace.keep` object. On the POSIX, S3 and archive tiers alike, `.terrace` at
  the tier root SHALL read as absent, be excluded from listings and usage counts, and refuse
  mutation, and the stateful model test SHALL report no divergence.
- **R5** — IF a tier root holds a managed-location marker only under the old directory name and the
  state database still holds that tier's row, THEN the mount SHALL fail with `Tier '<name>' marker
  is missing — audit required`, SHALL exit non-zero, and SHALL leave the tier root unmodified: no
  `.terrace/` directory created, the old marker's bytes unchanged, and no residency row adopted.
  This is gradeable at the engine without a FUSE mount, where attaching the stack raises that
  error.
- **R6** — WHEN `TERRACE_LOGGING_LEVEL` is set inside a `tools/temp_site.sh` drive, the loaded
  configuration SHALL carry that level and its provenance string SHALL read `from:
  TERRACE_LOGGING_LEVEL`. WHEN `TERRACE_CONFIG_FILE` names a file, the configuration SHALL load that
  file as the local configuration. A variable outside the `TERRACE_` prefix SHALL have no effect on
  the configuration. `tools/temp_site.sh`, the `temp_site` fixture and the CLI subprocess helper
  SHALL scrub `TERRACE_*`, and the S3 integration suite SHALL read its endpoint from
  `TERRACE_TEST_MINIO_*`.
- **R7** — The per-user paths SHALL follow the XDG Base Directory convention under the directory
  name `terrace`. WHILE `XDG_CONFIG_HOME` is set, the user configuration file SHALL be
  `$XDG_CONFIG_HOME/terrace/config.toml`; WHILE it is unset, `~/.config/terrace/config.toml`. WHILE
  `XDG_STATE_HOME` is set, the user state directory SHALL be `$XDG_STATE_HOME/terrace` and the user
  log directory `$XDG_STATE_HOME/terrace/log`; WHILE it is unset, `~/.local/state/terrace` and
  `~/.local/state/terrace/log`. The system-wide paths SHALL be `/etc/terrace/config.toml`,
  `/var/log/terrace` and `/var/lib/terrace`, and the local configuration file SHALL be
  `./.terrace.toml`. Observable as the path namespace of `terrace.core.platform` in a fresh process,
  and as the directories a first run creates under `tools/temp_site.sh`.
- **R8** — WHEN a file in a mounted overlay is dirty against its upstream boundary,
  `getxattr(path, 'user.terrace.dirty')` SHALL return `b'1'` and `user.terrace.tiers` SHALL list the
  tiers holding a copy; every other attribute name SHALL raise `ENOATTR`. The unit-level adapter
  tests (`tests/fuse/test_adapter.py`) grade the attribute names and the `ENOATTR` rule. The check
  on a real mount (`tests/fuse/test_integration.py`, `-m integration`) cannot be run in this cycle,
  because the integration lane runs nowhere today: that test is renamed with the rest, and the
  cycle's records SHALL state that the real-mount check was **not run**.
- **R9** — The state store SHALL be unchanged. `PRAGMA user_version` SHALL read 2, `schema.sql`
  SHALL differ from the base commit in comments only, the database file SHALL still be named
  `state.db`, and a state database created by the base commit's code SHALL open under the renamed
  code without `SchemaMismatch`. `[overlay].state` SHALL remain a required spec key with no default:
  a spec without it SHALL be refused with `[overlay] requires a state directory`.
- **R10** — After `uv sync`, the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m
  unit`) SHALL exit 0 with no failure and no error, and the unit lane SHALL report the same passed
  count as on the cycle's base commit. A rename changes no test's verdict, so the count is the
  evidence that nothing was dropped or skipped on the way.
- **R11** — `CHANGELOG.md` SHALL carry, under `[Unreleased]`, a `### Changed` entry that announces
  the rename as a breaking change with no migration path and names each thing that moved, old name
  and new: the distribution and import package, the console script, the environment prefix, the
  marker directory, the partial directory, the S3 sentinel, the two extended attributes, the local
  configuration file, and the per-user and system-wide configuration, state and log paths.
  `PLAN.md`'s technology-decision row for the package and entry point SHALL name `terrace` and state
  why the earlier decision was overturned, and `AGENTS.md` SHALL no longer describe the rename as
  pending.

## Non-goals (no-gos)

- **Migration or compatibility code of any kind.** No dual-read of an old marker, no fallback to an
  old environment variable, configuration file, path or attribute name, no rename-on-mount, and no
  warning that recognizes an old name. It follows that a tier root holding only an old-named marker,
  whose state database did not survive, is treated as any unmarked root is today: attached as new.
  Nothing has been released and no overlay is deployed.
- **A schema change.** No `PRAGMA user_version` bump and no stable row ids; the latter belongs to
  `issues/namespace-sync-redesign.md`.
- **Any behaviour change other than the names.** Stub subcommands stay stubs, the path layout keeps
  its shape, `[overlay].state` gains no default, and no cache or data directory is introduced (the
  code has none).
- **Renaming the maintainer's local checkout directory,** or anything on the hosting side. The
  repository is already `glentner/terrace`.
- **The `PLAN.md` residue that `issues/plan-md-reconciliation.md` owns.** This cycle changes names
  in `PLAN.md` and the technology-decision row that records the package name, and nothing else. The
  `fsck`-versus-`audit` inconsistency at `PLAN.md:50` stays with that seed: this cycle changes the
  name on that line and leaves the command list as it is.
- **Editing frozen records.** The root `GOAL.md` is unchanged, and the `spec/` records of landed
  cycles keep the spelling they were written in.
- **Building or running the integration lane** (`issues/ci-integration-lane.md`), and any release:
  no `0.1.0`, no publication.

## Clarifications

Each of the seed's open questions was ruled before shaping. All are resolved 2026-11-11.

- **Q:** What are the names? — **A:** The proper noun is "Terrace". The distribution, the import
  package, the console script, file names and code identifiers use `terrace`. The classes become
  `TerraceApp` and `TerraceError`. The environment prefix is `TERRACE_`. "Simple Overlay Service" in
  prose becomes "Terrace". (The maintainer's direction, relayed by the foreman; resolved
  2026-11-11.)
- **Q:** Do the on-disk and user-visible names move too? — **A:** Yes, by the same rule: marker
  directory `.terrace/`, partial directory `.terrace/partial`, S3 sentinel `.terrace.keep`, extended
  attributes `user.terrace.tiers` and `user.terrace.dirty`, local configuration file
  `.terrace.toml`. (The foreman's ruling; resolved 2026-11-11.)
- **Q:** Is anything shipped that needs a migration path? — **A:** No. Hard break: no dual-read, no
  fallback to an old name, no migration code. Nothing has been released and no overlay is deployed.
  The seed's draft R4 holds in its first form (R5 here): a tier root carrying only the old marker,
  with a surviving state database, is refused loudly and left unmodified. `CHANGELOG.md` gets a
  `### Changed` entry announcing the rename and naming each thing that moved; this cycle writes it,
  which supersedes the seed's note leaving it to `issues/documentation-and-v0.1.0.md`. (The
  foreman's ruling; resolved 2026-11-11.)
- **Q:** Where do configuration, state, cache and data live? — **A:** A per-user path follows the
  XDG Base Directory convention: `$XDG_CONFIG_HOME/terrace` (default `~/.config/terrace`),
  `$XDG_STATE_HOME/terrace` (default `~/.local/state/terrace`), and likewise for a cache or data
  directory if the code has one. The `XDG_*_HOME` variables are honoured when set. If
  `core/platform.py` already honours them the cycle only renames; if it does not, making it honour
  them is in scope. System-wide paths become `/etc/terrace` and `/var/{log,lib}/terrace`.
  `[overlay].state` stays a required spec key with no default. Confirmed while shaping:
  `core/platform.py:35-36` already reads `XDG_CONFIG_HOME` and `XDG_STATE_HOME`, and the code has no
  cache or data directory, so the cycle only renames. (The maintainer's direction, relayed by the
  foreman; resolved 2026-11-11.)
- **Q:** Does the schema version move, and do stable row ids ride along? — **A:** No. The schema is
  untouched and `PRAGMA user_version` stays 2. Stable row ids are not part of this cycle. This
  supersedes the sentence in this cycle's `ROADMAP.md` entry that lets the schema bump ride with the
  format break. (The foreman's ruling; resolved 2026-11-11.)
- **Q:** One cycle or two, and does the repository get renamed? — **A:** One cycle, appetite big.
  The repository is already `glentner/terrace`; the project URLs in `pyproject.toml` and the package
  metadata point there. (The foreman's ruling; resolved 2026-11-11.)
- **Q:** What does "no old name remains" cover? — **A:** Tracked files, excluding the root `GOAL.md`
  (which is unchanged), `CHANGELOG.md`, everything under `spec/` (records of landed cycles are
  frozen, and this cycle's own records name the old spelling), and this cycle's own seed
  `issues/rename-to-terrace.md` (retired when the cycle lands). `uv.lock` is regenerated with
  `uv lock` and then carries the new name, so it needs no exclusion. Live seeds under `issues/` and
  `ROADMAP.md` are in scope: their path, command and variable references move to the new names so
  they keep resolving. So are `AGENTS.md` (§ Factory's profile rows and § Invariants' paths
  included), `PLAN.md`, `README.md`, `CLAUDE.md`, `tools/`, `docker-compose.yml` and
  `.github/workflows/ci.yml`. (The foreman's ruling; resolved 2026-11-11.)
- **Q:** What is the bar for the unit suite? — **A:** It passes with the same pass count as on the
  cycle's base commit. The foreman measures the baseline and gives the figure to the planner. (The
  foreman's ruling; resolved 2026-11-11.)
- **Q:** Can the extended-attribute criterion be checked on a real mount? — **A:** No. The
  integration lane runs nowhere today. The unit-level adapter tests grade the attribute names, and
  the real-mount check is recorded as not run. (The foreman's ruling; resolved 2026-11-11.)

## Related materials

- Seed: [`issues/rename-to-terrace.md`](../../issues/rename-to-terrace.md). Its line numbers predate
  several landed cycles; every anchor in this GOAL was re-read at `bf438b4`.
- The root [`GOAL.md`](../../GOAL.md), line 5: the placeholder-name sentence that licenses the
  rename, and the reason that file is never edited.
- `PLAN.md`, the lines that carry a name: the title (`:7`), the CLI summary (`:50`), the marker
  (`:267`), the extended attributes (`:281-282`), the audit's marker check (`:360`), the
  configuration precedence chain (`:395-396`), the worked overlay spec (`:403`), the CLI surface
  (`:471-481`), and the technology rows (`:499-500`).
- `AGENTS.md`: the pending-rename notice (`:10-16`), the repository map, § Invariants (the
  high-blast-radius paths are stated relative to `src/sosfs/`; the list keeps every entry and its
  stated base moves), § Verification, and § Factory (the `coupled core` row and the throwaway
  environment's contract). `CLAUDE.md` is a symlink to it.
- Source anchors, at `bf438b4`: `pyproject.toml:6,8,35,38,67`; `src/sosfs/__init__.py:27,28,32,183,
  212`; `src/sosfs/core/platform.py:35-36,42-52`; `src/sosfs/core/config.py:100,145,199`;
  `src/sosfs/core/exceptions.py:86`; `src/sosfs/core/logging.py:191`;
  `src/sosfs/state/lifecycle.py:125,158`; `src/sosfs/state/registry.py:118-139`;
  `src/sosfs/state/journal.py:34,44`; `src/sosfs/state/db.py:34,52,86`;
  `src/sosfs/state/schema.sql:4,14,41`; `src/sosfs/engine/overlay.py:44,145-146`;
  `src/sosfs/tier/posix.py:63-64`; `src/sosfs/tier/s3.py:38,108-109`;
  `src/sosfs/tier/archive.py:269-270`; `src/sosfs/tier/spec.py:286`;
  `src/sosfs/fuse/adapter.py:32-33,136-142`.
- Test and tooling anchors, at `bf438b4`: `tests/__init__.py:22-23`; `tests/conftest.py:21`;
  `tests/test_cli.py:24,68`; `tests/engine/test_model.py:46`; `tests/engine/test_replay.py:167`;
  `tests/fuse/test_adapter.py:222`; `tests/fuse/test_mount.py:259`;
  `tests/fuse/test_integration.py:120-122`; `tests/tier/test_s3_integration.py:33-36`;
  `tools/temp_site.sh:50,61`; `tools/lint.sh:67-78`; `docker-compose.yml:1-24`;
  `.github/workflows/ci.yml:47`; `uv.lock:645`.
- Related seeds: `issues/plan-md-reconciliation.md` (owns the `PLAN.md` residue and the `fsck` line);
  `issues/documentation-and-v0.1.0.md` (must not ship a `0.1.0` under the old name);
  `issues/namespace-sync-redesign.md` (owns the schema bump and stable row ids);
  `issues/ci-integration-lane.md` (owns the lane that would run R8's real-mount check).
