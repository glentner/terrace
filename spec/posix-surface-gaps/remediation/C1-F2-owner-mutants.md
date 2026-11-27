# Owner mutant sweep — review cycle 1, F2

The record of `C1-F2-owner-mutants.py --before`, run 2026-11-27 on macOS (unprivileged, process gid 20, the tier
roots moved into supplementary group 12), on the remediation tree before its commit. Log:
`.local/remediation1/C1-F2-sweep-before.log` in the cycle worktree (gitignored).

## The class

Every place this cycle's diff hands an owner to a tier as the engine creates an entry there:

| Site | Where | What it creates |
|---|---|---|
| M1 | `OverlayEngine.create`, the `FileMeta` it gives `dest.put` | a new file on the fastest eligible tier: the source on a one-tier stack, a stage otherwise |
| M2 | `OverlayEngine._execute_namespace_locked`, the replayed mkdir's `self.source.mkdir` | a directory its own queued `mkdir` places on the source |
| M3 | `OverlayEngine._place_parents`, its `self.source.mkdir` | ancestors a commit (`commit_upstream`) or a direct create places on the source before their own `mkdir` replays |
| M4 | `PosixTier.mkdir`, its ownership step (`_apply_meta` with `uid`/`gid`) | every directory M2 and M3 ask a POSIX tier for |

`materialize_dirs` is not a site: this cycle did not change it and it carries no owner (mode only). On the source
it never creates a catalog directory, because `create` onto the source and every commit to it run `_place_parents`
first, which places each catalog ancestor (M3); a probe of a one-tier stack, mkdir `/p`, mkdir `/p/q`, create
`/p/q/r.dat`, found every ancestor already placed with the catalog's owner before `materialize_dirs` ran. What it
creates without an owner are stage directories, which § 5 does not grade. `S3Tier.mkdir` and `ArchiveTier.mkdir`
accept and ignore the owner by design (their docstrings say so), so they have no owner to drop.

## The pins

Five tests in `tests/engine/test_ownership.py`, all named `test_foreign_root_*`. Each moves every tier root into a
supplementary group of the process (no setgid bit) and drives each site twice: with the default identity (the
catalog records the process's gid, which a BSD parent directory would not give) and with that supplementary group
given (which a Linux process would not give). A dropped owner therefore fails one of the two on either kind of
system: on macOS the default-identity case, on Linux the given-group case. A process with no supplementary group
skips them with that reason.

## Result

Each mutant drops the owner at one site, planted inside its own function by the AST and restored byte for byte.
"Without these pins" is every other unit test under `tests/engine`, `tests/fuse` and `tests/tier` (609).

| Mutant | Site (owner dropped) | Killed by | Without these pins |
|---|---|---|---|
| M1 | `OverlayEngine.create` | `test_foreign_root_ancestors_placed_by_a_direct_create`, `test_foreign_root_file_created_on_a_stage`, `test_foreign_root_file_created_on_the_source` | killed by `test_explicit_group_reaches_the_source` |
| M2 | `OverlayEngine._execute_namespace_locked` | `test_foreign_root_directory_made_by_its_replay` | killed by `test_explicit_group_reaches_the_source` |
| M3 | `OverlayEngine._place_parents` | `test_foreign_root_ancestors_placed_by_a_commit`, `test_foreign_root_ancestors_placed_by_a_direct_create` | survived (609 passed) — the review's survivor |
| M4 | `PosixTier.mkdir` | `test_foreign_root_ancestors_placed_by_a_commit`, `test_foreign_root_ancestors_placed_by_a_direct_create`, `test_foreign_root_directory_made_by_its_replay` | killed by `test_explicit_group_reaches_the_source` |

4 of 4 mutants killed; the unmutated tree passed all five pins first; every file restored and compared.

Not run here: the sweep on Linux. There the default-identity half passes under any mutant (Linux gives the process's
gid, which is what the catalog records) and the given-group half is what kills; that half is the same mechanism
`test_explicit_group_reaches_the_source` already relies on in CI, which kills M1, M2 and M4 without these pins.
