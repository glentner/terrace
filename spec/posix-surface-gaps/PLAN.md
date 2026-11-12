# PLAN — Entries created through the mount belong to their creator, and `link` is refused plainly

> **Status:** Draft for review · **Last updated:** 2026-11-12
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Lean plan (`skip research`, the foreman's
> ruling): no `research/`. Every design element traces to a GOAL R-ID.

## 1. Summary

The adapter reads the calling process's identity from the binding and passes it to the engine's
`create` and `mkdir`; the engine alone applies the group rule (the parent's group under a setgid
parent, the caller's otherwise) and the setgid inheritance for directories. Ownership reaches a
POSIX source on both commit routes: files already carry the catalog's owner through `put`, and
directories gain it through an optional owner on the tier's `mkdir`. `link` is refused with `EPERM`.
Documents move in the same commit. One phase; it fits the small appetite.

## 2. Design

**Adapter** (`src/terrace/fuse/adapter.py`). Import `fuse_get_context` from `mfusepy` at module scope
(the module already imports `mfusepy` there). `mkdir` and `create` call `fuse_get_context()` by its
module-level name at call time, take `(uid, gid, _pid)`, and pass `uid=` and `gid=` to the engine.
No new state, no constructor change, no group logic: translation only (§ 9). A module-level name is
the test seam: adapter tests replace `terrace.fuse.adapter.fuse_get_context` with a stand-in
(`monkeypatch.setattr(..., raising=False)`, so the same test runs, and fails on its assertion, against
the merge base, where the name does not exist). A new `link(self, target, source)` raises
`FuseOSError(errno.EPERM)` on every call. No `symlink`, no `access` callback.

**`link` errno: `EPERM`.** POSIX and Linux `link(2)` both name `EPERM` for a file system that does not
support hard links, so callers that fall back to a copy already handle it; `EXDEV` would claim a
cross-device link that is not the cause, and `ENOTSUP` is not in `link()`'s list.

**Engine** (`src/terrace/engine/overlay.py`, `create` and `mkdir`). Both already hold the parent
`Entry` from `_require_parent`; keep it. Resolve ownership once, in one private helper used by both
(`_owner(parent, uid, gid)` or inline, the builder's call):

- `uid` / `gid` left `None` default to the engine process's own (`os.getuid()`, `os.getgid()`), not `0`.
  For a privileged daemon that is unchanged; for an unprivileged one the catalog now names the identity
  the source object actually gets (R3's reading, row 2 below).
- When the parent's recorded mode carries `stat.S_ISGID`, the new entry's `gid` is the parent's `gid`,
  whatever the caller passed (R2).
- `mkdir` under such a parent ORs `stat.S_ISGID` into the recorded mode, and the queued `mkdir` row's
  `detail['mode']` carries the same mode. `create` adds no bit (R7).
- `create` passes `uid` and `gid` in the `FileMeta` it hands `dest.put`, so a file created straight onto
  a POSIX source is chowned (best-effort, as today) like a committed one. A staged file already takes
  the catalog's owner at commit (`engine/transfer.py` `copy`); no change there.

**Directories on the source** (`src/terrace/tier/protocol.py`, `posix.py`, `s3.py`, `archive.py`;
`overlay.py` `_execute_namespace_locked` and `_place_parents`). `Tier.mkdir` gains keyword-only
`uid: Optional[int] = None, gid: Optional[int] = None`. `PosixTier.mkdir` applies them after
`os.mkdir` and **before** its exact-mode `os.chmod`, through `_apply_meta` with only the owner set (so
`PermissionError` is swallowed exactly as for files, § 4); chown first because a BSD chown that
changes the group clears the setgid bit. `S3Tier` and `ArchiveTier` accept and ignore them, as they
ignore ownership on `put` today. The two engine call sites that create a source directory pass
`uid=entry.uid, gid=entry.gid`. `materialize_dirs` (staging tiers) is unchanged.

**Mount** (`src/terrace/fuse/mount.py`): no change; `FUSE(...)` already passes no
`default_permissions`. A test pins it (R5).

**Documents**, in the phase's commit: `README.md` gains a two-line `Limitations` list (heading and one
bullet: hard links are not supported and `link` fails with `EPERM`). Root `PLAN.md` § FUSE adapter
gains one sentence as its own new line after the paragraph, a pure addition: the mount records
permission bits and does not enforce them (no `default_permissions`, no `access` callback).
`AGENTS.md`: delete the § Known open violations row "§ 5, 9 — Entries created through the mount are
recorded `0:0`"; change the section's lead "four a review of the whole tree reproduced on 2026-11-11"
to "three"; move every unit and total test count and the test-file count (§ Commands, § Repository
map, § Conventions' integration line, § Verification) by the delta this phase measures against its
merge base, never to absolute numbers taken from this plan.

**Tests.** Two new modules, both `pytestmark = mark.unit`:

- `tests/fuse/test_surface.py` (adapter, mount-free, `operations` fixture, a stand-in context such as
  `(4242, 4343, 1)`): file and directory get the caller's uid (R1) and gid under a plain parent (R2);
  under a parent made through the adapter and then `chmod`ed `0o2775` with a different caller gid, a
  file and a directory take the parent's gid (R2), the directory's mode carries `S_ISGID` and the
  file's does not (R7), and a file one level further down takes the same gid (R7's purpose); `link`
  raises `EPERM` on two different calls (R4); `OverlayOperations.access is Operations.access` and the
  mount's default runner hands `FUSE` no `default_permissions` (R5, monkeypatched `mfusepy.FUSE`
  recorder, no mount).
- `tests/engine/test_ownership.py` (engine): R2 and R7 setgid cases through `engine.chmod` and
  `engine.mkdir`/`create` with explicit identities; R3: on a two-tier stack an engine-created file and
  directory (and a directory nested under a new one, so `_place_parents` runs), created with the
  defaults, flushed (`engine.flush()`), have source `os.stat` uid and gid equal to the catalog's; and
  on a one-tier stack a file created straight onto the source. Where `os.getgroups()` offers a group
  other than the source root's, a second R3 case creates a directory (two-tier, flushed) and a file
  straight onto a one-tier source with `gid=` that group and asserts the source holds it (the case
  that holds on Linux and BSD group semantics alike, since the platform's default group is not what is
  asserted); it skips, with that reason, on a single-group host. A staged file with an explicit group
  is left out: `transfer.copy` already chowns it on the merge base, so it could not show red.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | Adapter `mkdir`/`create` pass `fuse_get_context()`'s uid; engine records it; `getattr` reports it. Test: `tests/fuse/test_surface.py`. |
| R2 | Engine group rule in `create`/`mkdir` from the parent's recorded mode and gid; adapter passes the caller's gid. Tests: both modules. |
| R3 | Process-identity defaults; `FileMeta` owner on `create`'s `put`; owner on `Tier.mkdir` from `_execute_namespace_locked` and `_place_parents`; `PosixTier.mkdir` chowns. Test: `tests/engine/test_ownership.py`. |
| R4 | Adapter `link` raises `EPERM`; `README.md` `Limitations` line. Test: `tests/fuse/test_surface.py`; README line graded by the orchestrator (a committed document). |
| R5 | Root `PLAN.md` one-sentence addition; `mount.py` unchanged; tests pin no `default_permissions` and no `access` override. |
| R6 | `AGENTS.md` row removed, the review-row count and the measured counts moved in the same commit. Graded by the orchestrator (a committed document). |
| R7 | Engine `mkdir` ORs `S_ISGID` under a setgid parent; `create` does not. Tests: both modules. |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before design (from the GOAL) and again against this draft.

- **§ 4 Tier atomicity** — `PosixTier.mkdir` gains a best-effort chown through `_apply_meta`, which keeps
  "ownership is best-effort; mode and mtime are not": the `except PermissionError` stays around the
  chown only, and the exact-mode `chmod` stays unguarded and runs last. `put`'s metadata-before-rename
  order is untouched. S3 and the archive gain no POSIX metadata (no second authority).
- **§ 5 Committed source path** — no change to where an entry is placed or when `source_path` is set;
  the two placement sites only add an owner to a `mkdir` they already make. Catalog stays the authority
  for ownership; the tier now applies it to directories too.
- **§ 9 Layering** — the adapter imports only `mfusepy` (already module-scope there) and the engine;
  its one added name is a binding function, not state. The group rule and setgid inheritance live in
  the engine. The engine imports nothing from `fuse/`.
- **§ 11 Engine API** — still synchronous and mount-free; `create`/`mkdir` keep their signatures, only
  the `None` default's meaning changes from `0` to the process identity. Failures stay `OSError`
  (`link` raises `FuseOSError`, an `OSError`).
- **§ 1, 6, 7** — untouched: no write, dirty-mark, eviction or clean-transition path changes.
- **High-blast-radius paths touched**: `overlay.py` `create`, `_execute_namespace_locked`,
  `_place_parents`; `tier/posix.py` (`mkdir`, which calls `_apply_meta`, itself unchanged). Trigger 2
  was overridden by the foreman's `skip research`; the edits there add an owner argument and a group
  computation and reorder nothing.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `Tier.mkdir` gains optional keyword `uid`/`gid` (protocol and all three adapters). | R3 for directories: without it a source directory takes the daemon's default group, which on a BSD source is the parent's whatever the catalog says. | Calling `set_meta` after `mkdir` from the engine: on an archive source that is a whole-archive re-pack per directory, and on S3 it is a stat round trip for nothing. |
| `create`/`mkdir` default `uid`/`gid` change from `0` to the engine process's own. | R3 reads "created through the engine with the engine process's own uid and gid" as the identity the engine runs under; with `0` the catalog names root while an unprivileged source object never can. | Keeping `0` and passing identities explicitly everywhere leaves every engine-level caller recording root, the defect the GOAL measured. |

Each row read against R1–R7: neither bends an R-ID; row 2 is the reading the R3 test asserts.

## 4. Rabbit holes (resolved)

- Where the caller's identity comes from → `mfusepy.fuse_get_context()` returns `(uid, gid, pid)` for
  the request being served; it is only valid inside a callback, hence the module-level seam for tests.
- What `link` does today → the adapter defines none; `mfusepy` marks its `Operations.link` default
  `libfuse_ignore`, so no callback is registered and libfuse answers with its own default, and called
  directly that default raises `EROFS`. The new test asserts `EPERM` on two calls and that the adapter's
  `link` carries no `libfuse_ignore` mark (so it is registered); both are red on the merge base.
- BSD group semantics (the GOAL's constraint on R3) → resolved by applying the owner explicitly on both
  commit routes rather than relying on the platform's default group.
- No adversarial review: no gate script, a small change (the foreman's ruling 6); trigger 3 does not
  fire (the deliverable is not a gate).

## 5. Risks & open questions

- A test fake implementing `Tier.mkdir(path, mode)` without the new keywords, used as an engine
  source, would raise `TypeError`; the full unit lane in `verify:` catches it.
- On a BSD source, a non-root chown that changes a directory's group clears its setgid bit; chown runs
  before the exact-mode chmod so the bit is restored where the platform allows. Source-side fidelity
  of the setgid bit is not an R-ID (R7 grades the recorded mode).
- The kernel's own umask and setgid handling of the mode it hands `mkdir`/`create` is not observable
  without a real mount; the real-mount check of R1, R2 and R4 is **not run** (no machine runs the
  integration lane), and the review records it so.
- The second R3 case skips on a host whose user has one group; the build records whether it ran.

## 6. Verification strategy

No CLI surface changes, so no `tools/temp_site.sh` drive. The phase's `verify:` is lint, the two new
modules by node id, and the full unit lane. Assert the counts: the new modules all pass (one skip
allowed, the single-group case, with its reason), and the unit lane reports no failure and its pass
count equals the merge base's plus the new tests.

**Red on the merge base**, before the source edits are written: with
`MB=$(git -C "$WT" merge-base HEAD origin/main)`, add a detached throwaway worktree
`git -C "$REPO" worktree add --detach "$REPO/.worktrees/posix-surface-gaps-base" "$MB"`, sync it into its
own environment (a `UV_PROJECT_ENVIRONMENT` beside the cycle's, suffixed `-base`, never the cycle's own,
which `uv run` from another project would re-point; `uv sync` from that worktree), confirm `uv run python -c 'import terrace; print(terrace.__file__)'` prints
a path in it, then, from it as the working directory, run
`uv run pytest -p no:cacheprovider -m unit <WT>/tests/fuse/test_surface.py <WT>/tests/engine/test_ownership.py`
by absolute path. Nothing is copied in. Every ownership test (R1, R2, R3, R7) and the `link` test fail
on an assertion; the two R5 tests pass, because the mount already passes no `default_permissions` (they
are guards). Quote the per-test outcome, then `git -C "$REPO" worktree remove "$REPO/.worktrees/posix-surface-gaps-base"`.
