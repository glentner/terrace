---
status: unshaped
kind: refactor
appetite: big
lane: public
---

# Rename `sosfs` to Terrace

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`sosfs` is not one string. It is five contracts that share a spelling: a distribution and
console-script name, a Python package path that 62 files import, a set of user-visible on-disk
artifacts inside every managed tier root, a configuration surface, and a proper noun in prose. A
single `sed` migrates the first two correctly, corrupts nothing textually, and silently changes the
meaning of the third. Organizing the work by *kind of occurrence* is the whole difficulty; the volume
is not.

Measured with `git grep` over tracked files at `6419966`, and re-verified while writing this seed:
**498 case-insensitive occurrences across 83 of 93 tracked files** — 447 lowercase `sosfs`, 51
uppercase `SOSFS`, no mixed-case forms.

```
git grep -oIi sosfs 6419966 -- . | wc -l          # 498
git grep -oI  sosfs 6419966 -- . | wc -l          # 447
git grep -oI  SOSFS 6419966 -- . | wc -l          #  51
```

The counts are measured. The failure modes below are traced by reading the code paths named; nobody
has performed the rename and watched a mount refuse.

### Kind 1 — string substitution: safe, and most of the volume

| Bucket | Count | Files |
|---|---:|---:|
| Dotted module references of any form (`git grep -oIE 'sosfs\.[a-z]'`) | 230 | 65 |
| Test modules, helpers, fixtures (`git grep -oIi sosfs -- tests`) | 185 | 40 |
| `src/sosfs` as a literal path | 21 | 3 |
| Base exception `SOSFSError` | 25 | 10 |
| CLI class `SOSFSApp` | 5 | 3 |
| Schema comments (`schema.sql` lines 4 and 41) | 2 | 1 |
| CI workflow, docker-compose comments | 6 | 2 |
| Prose "Simple Overlay Service" | 7 | 6 |
| `PLAN.md` / `AGENTS.md` / `README.md` | 61 | 3 |

The buckets overlap by construction — a dotted reference in a test module is counted twice — so the
column does not sum to 498. It is a map of where the work is, not a partition.

**The bare-`sos` false positive was checked and is zero.**
`git grep -oIiE 'sos[^f]|sos$|sosf[^s]' -- . | wc -l` returns `0`: there is no tracked occurrence of
the substring `sos` that is not part of `sosfs`. A blanket substitution is textually safe, so the
usual "grep will eat unrelated identifiers" caution is not the one to spend care on here.

The real hazard is **case collapse**. One lowercase rule turns `SOSFSApp` into `TERRACEApp` and
`SOSFSError` into `TERRACEError`. The migration needs three ordered rules — `SOSFSApp`/`SOSFSError`
first, then `SOSFS` → `TERRACE`, then `sosfs` → `terrace` — and only then is a single pass correct.

**Eighteen string literals a directory move alone does not fix, and each fails at runtime rather than
at import-check time:** `src/sosfs/__init__.py:27` `get_version('sosfs')` (import-time
`PackageNotFoundError` if the distribution is renamed and this is not), `:32`
`Logger.with_name('sosfs')`, `:183` `APP_NAME = 'sosfs'`; `src/sosfs/core/logging.py:191`
`getLogger('sosfs')`; `src/sosfs/core/exceptions.py:86` `modname: str = 'sosfs'`;
`src/sosfs/state/db.py:52` `resources.files('sosfs.state')` (which is how `schema.sql` is found);
`src/sosfs/core/platform.py:42-52` (nine path components in one literal block); `tests/__init__.py:23`
`'-m', 'sosfs'`; and the monkeypatch targets at `tests/fuse/test_adapter.py:217` and
`tests/fuse/test_mount.py:259`.

### Kind 2 — file and directory moves

`src/sosfs/` → `src/terrace/`, with `git mv` so history follows, plus the 207 import lines it
invalidates (95 under `src`, 112 under `tests`, across 62 files) and `pyproject.toml`'s four
occurrences: `name = "sosfs"` (`:6`), the console script `sosfs = "sosfs:main"` (`:35`), and
`packages = ["src/sosfs"]` (`:64`). The distribution name and `get_version('sosfs')` must move
together or the package raises before argv is parsed — total, immediate, and caught by
`uv run pytest -m unit`, which is why this is not the one to worry about.

The GitHub repository slug `simple-overlay-service` (2 occurrences: `pyproject.toml:38`,
`src/sosfs/__init__.py:28`) is a separate act with a GitHub-side redirect. Doing it after the code
cycle lands keeps the code change off a network dependency.

### Kind 3 — public and on-disk contracts: the part that is not a rename

Five artifacts here are not strings in a source file, they are the format an existing deployment is
already written in. Changing them is a **compatibility break that needs a decision**, not an edit.

**The marker directory is the data-loss path.** `src/sosfs/state/lifecycle.py:125` —
`MARKER_DIR: Final[str] = '.sosfs'`. Every managed tier root on disk contains `.sosfs/marker`, a TOML
file whose first line is `# sosfs managed-location marker — do not edit` (`lifecycle.py:158`).
Rename the constant with no migration and `Marker.read` returns `None` for every pre-existing root.
`Registry._attach` (`state/registry.py:133-138`) then branches on the `new` flag — whether a `tier`
row already exists in the state database:

* **State DB survives.** `new` is false, `_attach` returns `MarkerStatus.MISSING`, and the engine
  raises at `engine/overlay.py:127-128`: `Tier 'project' marker is missing — audit required`. Every
  existing overlay refuses to mount. Loud, correct, recoverable, no data touched. This is the *safe*
  failure and the cycle must preserve it.
* **State DB does not survive.** `new` is true, `_attach` takes the `write(Marker(...))` branch at
  `registry.py:137`, and a fresh overlay UUID is stamped over a tier root that still holds the
  previous overlay's uncommitted data. The new catalog is empty, so no fast-tier copy is known to be
  dirty, nothing is ever written back, and the next eviction discards uncommitted bytes. **Silent
  data loss.** Mitigating fact, measured: `[overlay].state` is a required spec key with no default
  (`src/sosfs/tier/spec.py:284-286` raises `SpecError('[overlay] requires a state directory')`), so
  the state DB lives where the user's spec says and is not moved by renaming `~/.local/state/sosfs`.
  **The rename must not introduce a default state path that varies with the project name.**

**`.sosfs/partial` is the crash-recovery contract and a separate constant.**
`src/sosfs/state/journal.py:34` — `PARTIAL_DIR: Final[str] = '.sosfs/partial'`, with the recognizer
at `:44` comparing `PurePosixPath(name).parts[:2] == ('.sosfs', 'partial')`. This shape is how the
audit tells an orphaned temp from an interrupted transfer apart from a user file. Rename the
constant and old-format temps become permanent litter counted against tier quota; rename the
recognizer without the writer and a live temp is treated as user data and promoted into the
namespace.

**`.sosfs.keep` is a third on-disk literal, S3 only.** `src/sosfs/tier/s3.py:38` —
`KEEP = '.sosfs.keep'`, the zero-byte sentinel that makes an empty prefix behave as a directory.
Rename it and every pre-existing empty directory in an S3-backed tier vanishes from listings while
the old sentinels reappear as ordinary files, because they no longer match the guard.

These three travel with **three hand-written guard predicates**, one per adapter —
`tier/posix.py:63`, `tier/s3.py:108`, `tier/archive.py:269` — each with its own docstring naming
`'.sosfs'`, plus the model oracle's filter at `tests/engine/test_model.py:43`. Change two of the
three and a managed internal is user-visible in one tier and hidden in another, and the hypothesis
model reports divergence for a reason unrelated to the operation under test.

**The xattr namespace is the only user-facing API in the diff.** `src/sosfs/fuse/adapter.py:32-33` —
`XATTR_TIERS = 'user.sosfs.tiers'`, `XATTR_DIRTY = 'user.sosfs.dirty'`, asserted end-to-end at
`tests/fuse/test_integration.py:114-122`. A job script doing `getxattr(path, 'user.sosfs.dirty')`
gets `ENOATTR` (`adapter.py:136` raises it for every unrecognized name). A guard that reads "is this
file committed yet?" now reads as an error, and a script that treats the error as "not dirty" deletes
uncommitted data. Breaking this is defensible; it has to be announced in `CHANGELOG.md`, not
discovered.

**The environment prefix is the only silent consequence in the set.** `src/sosfs/core/config.py:100`
— `return Environ(prefix='SOSFS').expand()`. Two more literals are unlinked from it: the blame string
at `:145` (`'from: SOSFS_' + …`) and the bootstrap override at `:199`
(`os.getenv('SOSFS_CONFIG_FILE', …)`). Three test-isolation literals depend on the same spelling:
`tests/conftest.py:21`, `tests/__init__.py:22`, `tests/test_cli.py:68`. After a rename with no
fallback, `Environ(prefix='TERRACE')` ignores every `SOSFS_*` variable and `path.user.config` points
at `~/.config/terrace/config.toml`, which does not exist — and `reload_file` returns an empty
namespace for a missing file (`config.py:84-85`) with no warning. A user who had set
`SOSFS_LOGGING_LEVEL=trace` loses it and is never told. Miss the conftest scrub and you get the
mirror-image failure: the developer's real `SOSFS_*` no longer matches the scrub filter while
`TERRACE_*` leaks into every test, so tests pass on a clean machine and fail on the maintainer's.

**Config and state paths.** `.sosfs.toml`, `~/.config/sosfs/config.toml`, `/etc/sosfs/config.toml`
(the precedence chain at `PLAN.md:379-380`), and `~/.local/state/sosfs` with `/var/{log,lib}/sosfs`
(`core/platform.py:42-52`). The state directory move is free: measured, nothing reads
`default_path.state` except the `makedirs` at `core/platform.py:60`, and the only real consumer of
that namespace is `default_path.log` via `traceback_filepath` (`core/exceptions.py:57`). The cost of
abandoning it is orphaned exception logs.

### What does not move, and one thing that must not

`src/sosfs/engine/overlay.py:43` is `DB_NAME = 'state.db'`. The SQLite filename carries no project
name, and no column, table or value embeds it — `schema.sql` mentions `sosfs` in two comments only
(lines 4 and 41). `PRAGMA user_version` is 2 (`schema.sql:14`, `state/db.py:34`) and **must not be
bumped for the rename**. There is no migration machinery: `db.py:86` refuses a mismatch outright, so
a bump turns `SchemaMismatch` into a hard refusal for databases that are in fact fully compatible,
while doing nothing at all about the marker, which is the artifact that actually moved.

### The plan-drift obligation

`PLAN.md:484` records the package and entry point as a **technology decision with a rationale** — the
row reads `sosfs`, justified by "Per GOAL.md; repo name is a placeholder and unimportant."
`GOAL.md:5` says "We only have the name 'simple overlay service' as a funny place-holder until a
better name is chosen" — the plan read that as licence to keep the name, and the rename reads it as
licence to change it. Both are defensible, and the point is procedural: the rename **contradicts a
recorded decision**, so under the plan-drift rule this cycle reconciles `PLAN.md` before it touches
code. That is a one-row edit plus the path and prefix strings at `PLAN.md:251, 265-266, 344, 379-380,
387`. `PLAN.md:50` also names a `fsck` command that does not exist, on a line this cycle rewrites
anyway; see `issues/plan-md-reconciliation.md` for who carries it.

### Done is verifiable

```bash
git grep --untracked -nIi -e sosfs -e 'simple.overlay.service' -- \
  ':!GOAL.md' ':!CHANGELOG.md' ':!uv.lock'
```

This is the criterion: done is when that command returns nothing. It grades the working tree,
harness included — `--untracked` while anything it should grade is uncommitted, plain `git grep`
otherwise. When this seed was written it returned **868 lines across 122 files**; that count
included the in-repo factory harness, which has since moved to the operator's KB, so re-measure at
promotion. The pre-port tip is the durable
baseline for the scale of the code-and-docs half: at `6419966`, `git grep -nIi -e sosfs -e
'simple.overlay.service' 6419966 -- ':!GOAL.md' ':!CHANGELOG.md' ':!uv.lock'` returns **478 lines
across 81 files**. The difference is the factory harness, and its occurrences are part of the rename
rather than an exclusion: the skills, briefs and seeds cite package paths, `uv run sosfs`
invocations and `SOSFS_*` variables that stop resolving the moment the code moves. Each exclusion
earns its place: `GOAL.md` is the historical record and is never edited (its single occurrence, the
placeholder-name sentence at line 5, is the *justification* for this cycle and must survive
verbatim); `CHANGELOG.md` will legitimately carry the string in the `### Changed` entry announcing
the rename; `uv.lock`'s one occurrence (line 984, `name = "sosfs"`) is generated and disappears on
the next `uv lock`.

Three footguns for whoever re-runs the census. `git grep '.sosfs'` without `-F` or a backslash also
matches `_sosfs`, `/sosfs` and `-sosfs`, which makes the marker bucket absorb the 207 import lines —
every count here uses `\.sosfs`. `ROADMAP.md`'s 32 occurrences (19 of them the literal `src/sosfs`)
were replaced wholesale by the factory port, so scoping them into this cycle double-counts. And
`.venv/bin/sosfs` and `dist/sosfs-0.1.0.dev0*` exist and are gitignored: `uv sync` regenerates the
console script, and stale `dist/` artifacts must be cleared with `del` (`rm` is aliased away) or
`uv build` output contains two distributions.

## Why it was deferred

Found by the invariant audit run while porting the software factory. That port was a harness change
and not the place to move 498 occurrences of the project's own name through five contract surfaces.

**This is unbuilt work, not a live defect.** Nothing on `main` is broken by the current name. The
urgency is entirely that the cost grows monotonically: every cycle queued below this one in
`ROADMAP.md` writes more occurrences, and the compatibility question gets more expensive the moment
`0.1.0` is published. It is not listed in `AGENTS.md` § *Invariants* § *Known open violations*
because it is not a violation of anything.

## Outcome / vision

The distribution, the package, the CLI, the on-disk artifacts and the prose all say `terrace`, in one
cycle, with the compatibility decision for existing overlays made deliberately and written down
rather than discovered by a user whose mount refused. `PLAN.md`'s technology row records the new name
and the reasoning that overturned the old one. The census command returns nothing, the unit suite is
green at the same count, and `uv build` produces exactly one pair of `terrace-0.1.0.dev0` artifacts.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — `git grep --untracked -nIi -e sosfs -e 'simple.overlay.service' -- ':!GOAL.md'
  ':!CHANGELOG.md' ':!uv.lock'` SHALL return no lines, and `GOAL.md:5` SHALL be unchanged.
- **R2** — WHEN `uv run terrace --help` is invoked, the CLI SHALL exit 0 and print a version line
  matching `terrace v\d+\.\d+\.\d+` (the assertion at `tests/test_cli.py:24`), and the seven
  registered subcommands SHALL each be listed.
- **R3** — WHEN `dist/` is cleared and `uv build` is run, the build SHALL produce exactly one sdist
  and one wheel named `terrace-0.1.0.dev0*`, and importing the installed package SHALL NOT raise
  `PackageNotFoundError`.
- **R4** — WHILE a tier root carries a pre-rename `.sosfs/marker` and its state database still holds
  the tier row, a mount SHALL fail with `Tier '<name>' marker is missing — audit required`, SHALL
  exit non-zero, and SHALL leave the tier root unmodified — no new marker written, no residency row
  adopted. (If the shaping conversation chooses the dual-read option instead, this criterion inverts:
  the mount SHALL succeed and the root SHALL afterwards hold `.terrace/marker` and no
  `.sosfs/marker`.)
- **R5** — WHEN a managed internal exists under the renamed marker directory, `readdir` SHALL omit it
  on the POSIX, S3 and archive tiers alike, and the hypothesis model oracle SHALL report no
  divergence. The S3 leg needs moto-server; the archive leg is unit-testable.
- **R6** — WHEN a CLI drive under `tools/temp_site.sh` sets `TERRACE_LOGGING_LEVEL`,
  the effective config SHALL reflect it and the provenance string SHALL name the `TERRACE_` variable
  rather than a `SOSFS_` one; WHILE only `SOSFS_LOGGING_LEVEL` is set, the effective config SHALL
  fall back to the default.
- **R7** — WHEN a file is dirty against its upstream boundary, `getxattr(path, 'user.terrace.dirty')`
  SHALL return `b'1'` and `getxattr(path, 'user.sosfs.dirty')` SHALL raise `ENOATTR`. Observable only
  on Linux with a real mount (`-m integration`); CI runs no integration job today, so state plainly
  whether this was checked or taken on trust.
- **R8** — A state database created before the rename SHALL open without `SchemaMismatch`, and
  `PRAGMA user_version` SHALL still read 2.

## Notes

- Related: `issues/plan-md-reconciliation.md` holds the `PLAN.md` residue this cycle does not touch,
  and owns the `fsck`-versus-`audit` inconsistency at `PLAN.md:50` — a line this cycle rewrites
  anyway, so whichever lands first should carry it and the other drops the item.
  `issues/documentation-and-v0.1.0.md` owns the `CHANGELOG.md` entry announcing the xattr and
  environment-prefix breaks, and must not ship a `0.1.0` under the old name. Every other queued cycle
  writes new occurrences, which is why this one is first.
- `PLAN.md` sections in scope for the reconciliation: Technology decisions (the package row at `:484`
  and the CLI/config/logging row at `:483`), the marker description at `:251`, the xattr names at
  `:265-266`, the audit's marker check at `:344`, the config precedence chain at `:379-380`, and the
  worked overlay spec at `:387`. Everything the census checked in those sections is implemented as
  described; only the name changes.
- Open question — **is anything shipped that needs a migration path?** The distribution has never
  been published and `CHANGELOG.md` shows `0.1.0.dev0` with no released version, which argues for a
  hard break plus a documented three-line migration (`mv .sosfs .terrace` per tier root,
  `mv ~/.config/sosfs ~/.config/terrace`, re-export `SOSFS_*` as `TERRACE_*`): compatibility code for
  a pre-release format is permanent cost bought against a user population of one. The concession
  worth making if the maintainer has a live mount anywhere is dual-read **for the marker only**, for
  exactly one release — `Marker.read` tries `.terrace/marker` then `.sosfs/marker`, and a successful
  old-path read triggers an atomic rewrite to the new path, which the existing temp+fsync+rename
  writer at `lifecycle.py:176-183` already makes safe. That is roughly fifteen lines and one test,
  and it converts the loud mount failure into a self-healing upgrade. It should **not** extend to
  `PARTIAL_DIR` or `KEEP`, because a dual-read there means each of the three adapter guards has to
  exclude two names, which is exactly where the bugs live. The third option, bumping `SCHEMA_VERSION`
  as a version gate, should be rejected: the schema is unchanged, so it refuses databases that need
  no migration while doing nothing about the marker.
- Open question — **does the GitHub repository get renamed, and the local checkout with it?** The
  slug appears twice (`pyproject.toml:38`, `src/sosfs/__init__.py:28`) and GitHub leaves a redirect,
  but the local directory is `…/glentner/simple-overlay-service` and every absolute path
  recorded against it, this seed's included, points at it. Renaming the directory
  invalidates those paths in one stroke; leaving it means the checkout and the project disagree
  indefinitely. Sequencing the repository rename *after* the code cycle keeps the code change off a
  network dependency.
- Open question — **one cycle or two?** The census recommends one: splitting the on-disk literals out
  leaves the tree with a `terrace` package and a `.sosfs` marker, which is defensible but doubles the
  review of the three adapter guards. Against that, one cycle puts four high-blast-radius regions in
  a single diff (`state/lifecycle.py` `MARKER_DIR` and `Marker.write`/`read`; `state/registry.py`
  `_attach`; `state/journal.py` `PARTIAL_DIR`/`is_temp_name` with the three adapter guards and
  `tier/s3.py` `KEEP`; `fuse/adapter.py` `XATTR_TIERS`/`XATTR_DIRTY`), each of which forces the human
  sign-off gate. Also worth a reviewer's eye below that bar: `core/platform.py:40-53`, where an
  off-by-one in a nine-entry literal block leaves config reading from one project's directory and
  logs writing to another's, and `core/config.py:100,145,199`, where the prefix, the blame string and
  the bootstrap override are three unlinked literals and a stale blame string survives a green test
  run.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the rename-census
  brief.
