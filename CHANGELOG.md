# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Working toward `0.1.0` (`0.1.0.dev0`) — the v1 reference implementation described in
PLAN.md. See ROADMAP.md for phased progress.

### Added

- Project scaffolding: packaging, license, documentation stubs.

### Changed

- **Breaking: the project is renamed to Terrace, with no migration path.** Nothing reads an old
  name: there is no fallback, no dual read and no warning that recognizes one. An overlay written
  under the old names is not adopted. Where its state database survives, the mount is refused with
  `Tier '<name>' marker is missing — audit required` and the tier root is left as it was. What
  moved, old name then new:
  - distribution and import package: `sosfs` → `terrace` (classes `SOSFSApp`, `SOSFSError` →
    `TerraceApp`, `TerraceError`)
  - console script: `sosfs` → `terrace`
  - environment prefix: `SOSFS_` → `TERRACE_` (`SOSFS_CONFIG_FILE` → `TERRACE_CONFIG_FILE`)
  - marker directory in every managed tier root: `.sosfs/` → `.terrace/`
  - partial directory for transfer temporaries: `.sosfs/partial/` → `.terrace/partial/`
  - S3 empty-directory sentinel: `.sosfs.keep` → `.terrace.keep`
  - extended attributes: `user.sosfs.tiers`, `user.sosfs.dirty` → `user.terrace.tiers`,
    `user.terrace.dirty`
  - local configuration file: `./.sosfs.toml` → `./.terrace.toml`
  - per-user configuration: `$XDG_CONFIG_HOME/sosfs/config.toml` (default
    `~/.config/sosfs/config.toml`) → `$XDG_CONFIG_HOME/terrace/config.toml` (default
    `~/.config/terrace/config.toml`)
  - per-user state and logs: `$XDG_STATE_HOME/sosfs` and `$XDG_STATE_HOME/sosfs/log` (defaults
    `~/.local/state/sosfs` and `~/.local/state/sosfs/log`) → `$XDG_STATE_HOME/terrace` and
    `$XDG_STATE_HOME/terrace/log` (defaults `~/.local/state/terrace` and
    `~/.local/state/terrace/log`)
  - system-wide configuration, logs and state: `/etc/sosfs/config.toml`, `/var/log/sosfs`,
    `/var/lib/sosfs` → `/etc/terrace/config.toml`, `/var/log/terrace`, `/var/lib/terrace`

  The state database is unchanged: it is still `state.db`, at schema version 2, where the overlay
  spec's `[overlay].state` puts it.
