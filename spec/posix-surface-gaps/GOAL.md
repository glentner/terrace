# GOAL — Entries created through the mount belong to their creator, and `link` is refused plainly

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** posix-surface-gaps
- **kind:** fix
- **appetite:** small

## Problem

**Every file and directory made through a mount is recorded as owned by root.** `OverlayEngine.mkdir`
and `create` take optional `uid` and `gid` and default both to 0
(`src/terrace/engine/overlay.py:356-372`, the default at `:367`; `:374-412`, the default at `:404`).
The FUSE adapter passes neither and never reads the calling process's identity
(`src/terrace/fuse/adapter.py:146-149`, `:189-192`). `getattr` reports the cataloged owner
(`adapter.py:88-89`), so `ls -l` shows root on a user's own new files for as long as the entry is
cataloged, and tools that refuse a tree owned by someone else refuse this one; a version-control
checkout inside the mount is the common case. Anchors re-confirmed at `3e85305` (2026-11-12).

**The catalog then disagrees with the source it commits to.** The catalog is the authority for
ownership (`AGENTS.md` § Invariants § 5; `PLAN.md` § Overlay engine: "Ownership is best-effort").
When a file commits, the POSIX tier `chown`s the source object to the cataloged `0:0`; an unprivileged
daemon cannot, the `PermissionError` is logged at trace level and swallowed
(`src/terrace/tier/posix.py:161-172`), and the object keeps the daemon's identity. Measured by the
seed at `64faf27` through the engine API (two-tier POSIX stack, tmpdir tiers, process `501:20`):
`create` and `mkdir` both record `0:0`; after a flush the source object is `501:0` while the catalog
says `0:0`. The queued `mkdir` row carries only the mode (`overlay.py:370`). The adapter half is read
from the code: no mount was driven.

**The group matters more than the owner on shared project storage**, where a setgid project
directory is the normal layout and the group decides quota accounting. A created entry's group should
follow its parent directory when that directory is setgid, and the caller otherwise. Nothing records
either today.

**Two unsupported surfaces fail without a decision behind them.** `link` is out of scope by design
(`PLAN.md` non-goals: "hard links across tiers"), yet the adapter defines no `link` callback, so a
caller gets whatever the binding's default returns rather than a chosen, documented errno. No `access`
callback exists and the mount passes no `default_permissions` (`src/terrace/fuse/mount.py:310`), so
permission bits are stored and reported and nothing enforces them; that is not written down as a
decision.

Pre-existing: the defaults date from the Phase 4 namespace work and the adapter from Phase 6, both
before the factory was adopted. No test asserts the owner or group of a created entry.

## Outcome / vision

A file or directory a user creates through the mount is theirs in every view: `getattr` (and so
`ls -l`), the catalog, and the POSIX source once it commits. Its group is the caller's, or the
parent's under a setgid parent. `link` fails with one errno the documentation names. `PLAN.md` says in
one sentence that the mount records permission bits and does not enforce them, and the mount options
match. The open-violations table no longer lists the defect.

## Acceptance criteria (the contract)

- **R1** — WHEN a process creates a file or a directory through the FUSE adapter, the adapter's
  `getattr` for that path SHALL report `st_uid` equal to the calling process's uid. (Broken: `0`.)
- **R2** — WHEN a process creates a file or a directory through the FUSE adapter, `getattr` SHALL
  report `st_gid` equal to the parent directory's gid where the parent's recorded mode carries the
  setgid bit, and equal to the calling process's gid otherwise. (Broken: `0` in both cases.)
- **R3** — WHILE the engine runs unprivileged, WHEN a file or a directory created through the engine
  with the engine process's own uid and gid commits to a POSIX source, the source object's uid and gid
  SHALL equal the catalog's for that entry. (Broken: catalog `0:0`, source `501:0`.)
- **R4** — WHEN `link` is called through the FUSE adapter, the adapter SHALL fail it with one errno,
  the same for every call, and the documentation SHALL name that errno.
  [NEEDS CLARIFICATION: where "the documentation" is. `PLAN.md` is limited to one sentence on
  permission bits (Clarifications), so its hard-link non-goal cannot carry the errno. (a) `README.md`
  gains a line; (b) `AGENTS.md` § Out of scope for v1 names it, beside its "cross-tier hard links";
  (c) `PLAN.md` after all, a second sentence in its non-goals, relaxing the one-sentence ruling.]
- **R5** — `PLAN.md` § FUSE adapter SHALL state, in one added sentence, that the mount records
  permission bits and does not enforce them, and the mount SHALL start without `default_permissions`.
  No other line of `PLAN.md` SHALL change.
- **R6** — `AGENTS.md` § Invariants § Known open violations SHALL no longer carry the row for this
  defect (§ 5, 9, "Entries created through the mount are recorded `0:0`"), and the measured figures
  `AGENTS.md` states (the unit-test count in § Verification, the section's count of rows from the
  2026-11-11 review) SHALL move in the same commit.

Evidence for R1–R4 is unit tests: at the adapter (mount-free, with a stand-in for the caller's
identity) for R1, R2 and R4, and at the engine for R2's setgid case and R3. A real mount cannot be
driven, because the integration lane runs on no machine; the real-mount check of R1, R2 and R4 is
recorded in the review as **not run**.

## Non-goals (no-gos)

- Creating symlinks (the seed's draft R3). It is a feature with a write-back path of its own and is
  cut to `issues/symlink-create.md`. No `symlink` callback in this cycle.
- An `access` callback, or passing `default_permissions`: the mount records permission bits and does
  not enforce them (Clarifications).
- A POSIX conformance suite; its natural home is `issues/ci-integration-lane.md`.
- Any change to the ownership of entries adopted from the source: they keep the source's owner and
  group, as today.
- The rest of the VFS surface's error mapping (`issues/engine-error-mapping.md`).

## Clarifications

- **Q:** What is in this cycle? — **A:** Ownership of created entries (the seed's draft R1 and R2),
  the refusal of `link` with one documented errno (draft R4), and the written decision about
  permission bits (draft R5). Each criterion is the observable broken-to-fixed behaviour. (resolved
  2026-11-12, the foreman's ruling)
- **Q:** Creating symlinks (draft R3)? — **A:** Cut to its own seed, `issues/symlink-create.md`
  (`status: unshaped`, `kind: feature`, `appetite: small`), written in this cycle's shape commit with
  its `ROADMAP.md` entry beside this cycle's. It is a feature with a write-back path of its own.
  (resolved 2026-11-12, the foreman's ruling)
- **Q:** Enforce permission bits or only record them? — **A:** Record, do not enforce; the mount does
  not pass `default_permissions`. A mount serves one user's job, and enforcement by the kernel needs
  ownership to be right first, which this cycle makes it; revisit when a mount serves more than one
  user. The root `PLAN.md` gains one sentence saying so, in the section that describes the mount, and
  nothing else there changes, because another cycle is redrawing that file. (resolved 2026-11-12, the
  foreman's ruling)
- **Q:** Where does the caller's identity come from, and who applies the group rule? — **A:** The
  plan designs where the identity comes from. `AGENTS.md` § Invariants § 9 stays: the FUSE adapter
  holds no policy, and reading the caller's identity is translation, not policy. The group rule is the
  seed's: the caller's group, or the parent directory's when that directory is setgid. (resolved
  2026-11-12, the foreman's ruling)
- **Q:** What evidence closes the cycle? — **A:** Unit tests at the adapter and the engine, and an
  engine-level check that after a commit by an unprivileged process the catalog and a POSIX source
  agree on owner and group. The real-mount check is recorded as not run. (resolved 2026-11-12, the
  foreman's ruling)
- **Q:** The open-violations row? — **A:** Removed in this cycle, with the measured figures moving in
  the same commit. (resolved 2026-11-12, the foreman's ruling)
- **Q:** Where did the defect come from (`escaped`)? — **A:** Recorded as `pre-factory`. The `0`
  defaults blame to `437e27f` (2026-07-02, Phase 4 WIP); `fe26962` (`deferred-rename-source-orphan`)
  only re-indented the `create` line. The adapter's `mkdir` and `create` blame to `07ceb25` and the
  mount's `FUSE(...)` call to `3d41899` (2026-07-11, Phase 6 WIP). All three are ancestors of the
  factory adoption at `6a046b4` and carry no `spec/`. Correct it at the gate if that reading is wrong.
  (recorded 2026-11-12, shaper)

Questions for the plan (not blocking; the plan answers them in `PLAN.md`):

- **Does a directory created under a setgid parent inherit the setgid bit?** The confirmation reads
  show the engine *keeps* the bit where it is given: the POSIX tier's `stat` reports
  `stat.S_IMODE` (`tier/posix.py:87`), which carries `0o2000`, and adoption records that mode
  (`overlay.py:150`, `:273`); `chmod` keeps it (`:603`). But a directory created through the overlay
  records only the mode its caller passed (`:366`), and nothing sets the bit from a setgid parent. Under
  R2 as written a new subdirectory takes the parent's group but not the bit, so files made inside it
  take the caller's group, not the project's. Whether inheriting the bit is in R2's reach is for the
  plan to settle, and to bring back to the foreman if it widens the cycle.
- R3 runs on Linux and macOS CI. A BSD-derived source gives every new object its parent directory's
  group whatever the setgid bit says, so the engine check has to hold on both semantics.

## Related materials

- Seed: `issues/posix-surface-gaps.md` (found by an out-of-cycle review of the whole tree at
  `64faf27`, 2026-11-11).
- Cut from this cycle: `issues/symlink-create.md`.
- Related seeds: `issues/engine-error-mapping.md`, `issues/hpc-deployment.md` (setgid project
  directories), `issues/ci-integration-lane.md` (a conformance suite against a real mount).
- `AGENTS.md` § Invariants § 5 (the catalog is the authority for ownership), § 9 (the adapter holds no
  policy), § Known open violations.
- `PLAN.md` § Overlay engine (ownership is best-effort), § FUSE adapter, the v1 non-goals.
- Source: `src/terrace/engine/overlay.py`, `src/terrace/fuse/adapter.py`, `src/terrace/fuse/mount.py`,
  `src/terrace/tier/posix.py`, `src/terrace/engine/transfer.py`.
