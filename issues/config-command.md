---
status: unshaped
kind: feature
appetite: small
lane: public
---

# `sosfs config` — get, set, locate and edit the application config

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The layered application config is fully implemented and has no user-facing surface. `sosfs config` is
`ConfigApp(StubApp)` (`src/sosfs/__init__.py:174-178`) and raises `RuntimeError('Not implemented:
sosfs config (see ROADMAP.md, Phase 9)')`. An operator who wants to know why their overlay is logging
at `info` has to read `src/sosfs/core/config.py` and guess which of four files won, or export
`SOSFS_LOGGING_LEVEL` and hope.

Everything the command needs already exists in `core/config.py`. `config` is a cmdkit `Configuration`
built at import; `blame` (`config.py:137-148`) answers "where did this value come from", rendering
either `from: /etc/sosfs/config.toml` or `from: SOSFS_LOGGING_LEVEL`; `update(scope, partial)`
(`config.py:215-233`) does a tomlkit round-trip that preserves comments, writes a standard header
into a file it had to create, and copies the previous file to `.config.{YYYYmmdd-HHMMSS}.toml`
alongside it first; and `path.{system,user,local}.config` (`src/sosfs/core/platform.py:40-53`) names
the three files. hypershell has the command pattern to copy — a nested `ApplicationGroup` with
`get`/`set`/`which`/`edit`, `--system | --user | --local` site selection, and `--site` on `which` to
print the originating layer alone. This cycle is assembly, not design.

Two facts about the substrate are load-bearing and a builder who does not know them will ship
something subtly wrong.

**The precedence chain.** Effective values resolve as environment (`SOSFS_*`) > `.sosfs.toml` in the
current working directory > `~/.config/sosfs/config.toml` > `/etc/sosfs/config.toml` > the logging
style preload > built-in defaults. It is implemented by keyword order in `partial_load`
(`config.py:109-120`) — cmdkit gives later namespaces higher precedence — over the roots in
`platform.py`. Measured: `SOSFS_LOGGING_LEVEL=trace` yields `from: SOSFS_LOGGING_LEVEL`. One special
case breaks the ladder rather than joining it: `SOSFS_CONFIG_FILE` is not a merge. `reload_local`
(`config.py:192-195`) passes `system=None, user=None`, so that variable *replaces* the whole file
layer with one file. A `config which` that describes the ladder without that case will report
confidently wrong provenance for anyone using it.

**The config object is a process-wide singleton built at import, and its failure path calls
`sys.exit`.** The module body at `config.py:198-206` calls `load()` (or `reload_local()` when
`SOSFS_CONFIG_FILE` is set) and, on any exception, writes a traceback and exits with
`exit_status.bad_config`. Every module in the package imports `sosfs.core.logging`, which imports
this, so a malformed `/etc/sosfs/config.toml` terminates the interpreter before `main()` runs. Two
consequences for this command: `config set` writes to disk and does **not** mutate the live
singleton, so nothing already running picks the change up — that had better be stated in the help
text, because the alternative is an operator who sets a value, sees no effect on their mounted
overlay, and concludes the setting does nothing. And a `set` that writes a file the loader cannot
parse makes the *next* `sosfs` invocation of any kind exit before it starts, which is a strong
argument for writing through `update`'s tomlkit round-trip and never by hand.

## Why it was deferred

Unbuilt work, scheduled as Phase 10 and never reached. No defect and no data at risk: the config
system itself works, only the CLI surface over it is missing. It sits last among the operations
cycles because it is the smallest and because it has the lowest operational value — and because
completing the CLI surface leaves the documentation cycle with nothing stubbed to describe.

## Outcome / vision

`sosfs config get logging.level` prints the effective value and, on request, where it came from.
`sosfs config which logging.level` names the winning layer. `sosfs config set logging.level trace
--user` writes `~/.config/sosfs/config.toml` with comments intact and a timestamped backup beside it.
`sosfs config edit --user` opens the right file in `$EDITOR`. The help text says plainly that `set`
changes a file and not a running process.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. Every one of these is a CLI-surface criterion, so it is
graded by a drive under `tools/temp_site.sh` against a throwaway site with `SOSFS_*`
scrubbed and XDG redirected — never in-process, because `core/platform.py` captures the XDG variables
into `Final` constants at import and a test that imports `sosfs.core.config` in-band reads the
developer's real `~/.config/sosfs/config.toml`.

- **R1** — WHEN `sosfs config get logging.level` is invoked, the command SHALL exit 0 and print the
  effective value on stdout with no decoration.
- **R2** — WHEN the provenance flag is passed with `get`, the command SHALL additionally print the
  line `blame` produces — a config file path, or `SOSFS_LOGGING_LEVEL` when the environment won.
- **R3** — WHEN `sosfs config set logging.level trace --user` is invoked, the command SHALL exit 0,
  the user config file under the throwaway site SHALL contain the new value, comments already in that
  file SHALL survive verbatim, and a timestamped backup of the previous contents SHALL exist beside
  it.
- **R4** — WHEN `sosfs config which logging.level` is invoked, the command SHALL exit 0 and name the
  winning layer; WHILE `SOSFS_CONFIG_FILE` is set, it SHALL name that file rather than any layer the
  file would otherwise have merged with.
- **R5** — IF a `set` targets an unknown key, THEN the command SHALL exit non-zero and name the key,
  rather than writing a setting nothing will ever read.
- **R6** — WHEN `sosfs config edit --user` is invoked with no editor configured, the command SHALL
  exit non-zero naming the environment variable it looked for, and SHALL NOT create or truncate the
  file.
- **R7** — `sosfs config set --help` SHALL state that the change is written to a file and does not
  affect an already-running process.

## Notes

- Related seeds: `issues/rename-to-terrace.md` moves the `SOSFS_*` environment prefix, the
  `SOSFS_CONFIG_FILE` variable and the `~/.config/sosfs/` and `/etc/sosfs/` paths — every one of
  which this command prints — so landing this cycle after the rename avoids writing the old names
  into a new help surface. `issues/documentation-and-v0.1.0.md` consumes the finished CLI reference
  and is the reason this cycle is placed where it is.
- `PLAN.md` § *Configuration* defines the two TOML surfaces and the precedence chain, and its
  § *CLI surface* table gives the command its one line: "Application config management (hypershell
  pattern)". The `SOSFS_CONFIG_FILE` replacement case is in the code and not in `PLAN.md`; if this
  cycle makes it user-visible, it goes into `PLAN.md` in the same commit.
- **CLI wiring.** Adding a real `config` touches four places: the `Application` subclass, `APP_HELP`
  (`src/sosfs/__init__.py:191`), the `commands` registry (`:219`), and `tests/test_cli.py`. Missing
  one is silent in exactly one direction — a command in `commands` but absent from `APP_HELP` fails
  `tests/test_cli.py:31`, while one added to `APP_HELP` and not to `commands` passes and fails only at
  invocation. `tests/test_cli.py:42` parametrizes the stub list and must lose `config` in the same
  commit. Because `config` is itself an `ApplicationGroup`, the same discipline applies again to its
  own nested registry of `get`/`set`/`which`/`edit`. If this is the last operations cycle to land, it
  also deletes `StubApp` and the last of the stale "Phase 9" pointers (`__init__.py:40-53`), which
  name a phase that no longer exists.
- Open questions for shaping, each with its real trade-off:
  - Does `set` validate the key against the `default` namespace (`config.py:69-80`)? Validating makes
    a typo an error, which is what R5 asks for, and forbids setting a key a newer version will
    understand. Accepting anything is forward-compatible and lets a misspelled setting sit in a file
    doing nothing for a year.
  - Does `get` with no key dump the whole effective configuration, as hypershell's does with a bare
    `.` varpath? Useful for a bug report, and it prints whatever an operator has put in their config
    file, which is a small disclosure question worth deciding deliberately rather than by default.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the remaining-work
  topic (dossier 7) and the FUSE/CLI layering topic (the config singleton's import-time `sys.exit`,
  the measured precedence chain, and the subprocess-isolation constraint on any config test).
