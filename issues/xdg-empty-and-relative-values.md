---
status: unshaped
kind: fix
appetite: small
lane: public
---

# An empty or relative `XDG_*_HOME` is used as written

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`src/terrace/core/platform.py:35-36` reads the two per-user base directories with
`os.getenv('XDG_CONFIG_HOME', <default>)` and `os.getenv('XDG_STATE_HOME', <default>)`. `os.getenv`
returns its default only when the variable is absent, so a value that is set is used as written,
whatever it holds. The XDG Base Directory specification differs on two counts: a variable that is
"either not set or empty" takes the default, and a relative path is invalid and is ignored.

Both cases leave directories in whatever directory the command ran from, because the module creates
the log and state directories as it is imported (`platform.py:60-63`). Measured on 2026-11-12 with the
renamed tree, in an empty working directory:

| Environment | Per-user configuration file | Created at import |
|---|---|---|
| `XDG_CONFIG_HOME=` and `XDG_STATE_HOME=`, both empty | `terrace/config.toml` | `./terrace/log` |
| `XDG_CONFIG_HOME=conf`, `XDG_STATE_HOME=var/state` | `conf/terrace/config.toml` | `./var/state/terrace/log` |

In both rows the specification's answer is `~/.config/terrace/config.toml` and
`~/.local/state/terrace/log`. Nothing errors and nothing is logged.

An empty value is what a login script leaves behind when it clears a variable by assigning the empty
string instead of unsetting it, and a cluster login environment is assembled from several such
scripts. A user in that state gets a `terrace/` directory in every directory they run a command from,
and their per-user configuration file is looked for relative to the working directory, so it is not
read.

The two values feed the whole per-user half of the path namespace: `path.user.config`,
`path.user.log` and `path.user.state`, and `path.local.log` and `path.local.state`, which reuse the
state base (`platform.py:46-52`).

No test and no drive sees either case. `tools/temp_site.sh:69-70` and the `temp_site` fixture
(`tests/conftest.py:23-24`) always set both variables to absolute paths.

## Why it was deferred

Found while shaping and planning the rename to Terrace (`spec/rename-to-terrace/`), whose GOAL asked
whether `core/platform.py` already honours the XDG variables. It does when they hold an absolute path,
so that cycle renamed the directory component and nothing else. Treating an empty or relative value as
unset changes which paths the program reads and writes, and the rename's contract was that nothing
changes but the names. **Pre-existing**: lines 35-36 read the same at the rename's base commit.

## Outcome / vision

The per-user paths follow the specification in each case it defines. Unset, empty and relative all
give the default, and an absolute value is honoured. No command leaves a directory behind in the
working directory.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHILE `XDG_CONFIG_HOME` is set to the empty string, the per-user configuration file SHALL
  be `~/.config/terrace/config.toml`. WHILE `XDG_STATE_HOME` is set to the empty string, the per-user
  state and log directories SHALL be `~/.local/state/terrace` and `~/.local/state/terrace/log`.
- **R2** — WHILE either variable holds a relative path, the path namespace SHALL ignore it and give
  the same defaults.
- **R3** — WHEN a command runs with either variable empty or relative, it SHALL create no directory
  under the current working directory. Observable under `tools/temp_site.sh`, by setting the variable
  inside the drive.
- **R4** — WHILE either variable holds an absolute path, the paths SHALL be the ones the rename fixed:
  `$XDG_CONFIG_HOME/terrace/config.toml`, `$XDG_STATE_HOME/terrace` and `$XDG_STATE_HOME/terrace/log`.

## Notes

- Related: `spec/rename-to-terrace/GOAL.md`, whose R7 fixes the paths for an absolute value and for an
  unset one; `issues/config-command.md`, which prints `path.user.config` as a provenance string, so a
  relative value would appear there as a relative path; `issues/hpc-deployment.md`, for the
  environment a batch job starts with.
- Open question — **whether an ignored value is worth a message.** The specification says to ignore a
  relative path and says nothing about telling the user. The namespace is built at import, so shaping
  should check what can be logged at that point before promising a warning.
- Not in scope: `XDG_CACHE_HOME` and `XDG_DATA_HOME`. The code has no cache or data directory.
- Found by: `rename-to-terrace`, shaping and plan (2026-11-11); measured on the plan's prototype tree.
