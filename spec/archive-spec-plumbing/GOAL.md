# GOAL — Make archive sources reachable from a spec file

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** archive-spec-plumbing
- **kind:** feature
- **appetite:** big

## Problem

The archive adapter is built and nothing can reach it. `src/terrace/tier/archive.py` (679 lines)
implements the tier protocol over ZIP, TAR and tar.gz, and every one of its tests constructs it by
hand. No overlay spec can name it, so the capability the root `PLAN.md` documents as a v1 source
type, with worked examples in § *Configuration* (`PLAN.md:1185-1219`), does not exist for a user.
A shipped-but-unreachable adapter reads as done; `AGENTS.md` § *Known open violations* carries it as
"`ArchiveTier` is unreachable from a spec file".

Three gates stand in the way, re-anchored on `184115f`:

- **The parser.** `TIER_KEYS` (`src/terrace/tier/spec.py:28`) has no `location`, `sync` or `sharded`,
  and the unknown-key check (`spec.py:204`) runs before the type is read. `SUPPORTED_TYPES`
  (`spec.py:25`) is `posix` and `s3`, so `zip`, `tar` and `tar.gz`, which `TIER_TYPES` (`spec.py:24`)
  names, are refused as "not yet supported" (`spec.py:209-210`). Reproduced on `184115f`: the
  direct-update ZIP example fails with `Unknown key(s) in tier 'archive': location`, the staged tar.gz
  example with `Unknown key(s) in tier 'cold': location, sync`, and the ZIP tier given a plain `path`
  with `Tier type 'zip' is not yet supported`. `LocationSpec` (`spec.py:150-180`) parses the
  `location` table correctly and nothing outside the tests calls it.
- **The engine.** `OverlayEngine.start` (`src/terrace/engine/overlay.py:139-158`) builds an `S3Tier`
  for `type == 's3'` and a `PosixTier` for everything else, so an archive spec past the parser would
  become a POSIX tier rooted at the archive file. `inner_location()` (`archive.py:162-166`) has no
  caller under `src/`.
- **The marker.** `start` refuses any tier whose marker status is not `OK` (`overlay.py:157-158`). A
  POSIX tier's marker is `.terrace/marker` under its root (`state/registry.py:106-110`); a tier that
  owns its marker I/O implements `read_marker`/`write_marker` (`registry.py:112-116`), which only
  `S3Tier` does (`tier/s3.py:290`, `:298`). `ArchiveTier` has neither, and refuses any write under
  `.terrace` (`_guard`, `archive.py:272-275`). An archive source has no root to hold a marker.

Widening the gate exposes rules the parser does not yet state, each of which today fails late:

- An archive source with no POSIX stage tier parses, mounts, and then fails every `open` with
  `EngineError: No passthrough-capable tier …` (`eligible_tier`, `overlay.py:230-231`).
- Decision 1 of the root `PLAN.md` (`PLAN.md:136-157`) retires the `staging` key: a staged bundle
  lives in the durable tier's root and is counted by its limits. The adapter still takes a working
  path of its own and refuses construction without it (`archive.py:235-251`, raise at `:240-241`);
  the loop helper still writes that key (`tools/bundle_loop.py:201-204`), its test reads it back and
  asserts it lies in no tier root (`tests/loop/test_helper.py:76`, `:92`), and the loop's pinned
  refusal names it (`tests/loop/stages.py:82`).
- Decision 7 (`PLAN.md:253-268`, and § *The tier model* (a), `:449-460`) says that over a bundle source
  a stack has exactly one shared stage tier, directly below the source, and that a spec that breaks
  this is refused. No parser rule says so (`PLAN.md:295`).
- Nothing describes an archive at a stage position, node-scoped or not.
- **Pre-existing, live on `main` for any stack:** the tier-root overlap check is symlink-blind. It
  compares `PurePath` values built from `os.path.abspath` (`spec.py:317-321`, roots from `:241` and
  `:180`), which normalizes `..` and does not resolve symlinks, so a stage root that is a symlink into
  the source passes, and eviction from that "stage" deletes source data. The state-directory check
  beside it (`spec.py:322-324`, built at `:299`) has the same blindness. `_validate_stack` is on the
  high-blast-radius list (`AGENTS.md` § *Invariants*); this cycle changes it anyway, so it fixes the
  overlap check once rather than leaving a second cycle to reopen the function.

Making an archive source reachable also makes its write path reachable. The adapter re-packs a whole
archive per call, with no intent bracket and no tier-wide lock (`AGENTS.md` § *Known open
violations*, rows owned by `issues/archive-journaling-and-locking.md`), and the tree's replay of
queued operations into a staged bundle is the design Decisions 2 and 3 overturn. That seed records
that its data-loss defect "becomes live the moment archives are reachable".

## Outcome / vision

A user can write the archive source forms `PLAN.md` § *Configuration* documents and mount them. The
spec parser accepts the archive types and keys, refuses at load every stack the engine cannot serve
with a message naming what is at fault, and refuses the retired `staging` key with a message naming
the tier that now holds the bundle. The engine constructs an `ArchiveTier` over the inner location
the spec names, registers it, and serves the archive's members: a tar.gz source is pulled once into a
staged bundle in the durable tier's root, counted by that tier's limits, and read at its offsets. The
bundle loop's first two stages (reach, read) pass. This is the read side of the archive work and the
prerequisite for the three archive cycles after it; the documents say exactly what the tree now does.

## Acceptance criteria (the contract)

- **R1** — WHEN a spec's source tier declares `type = "zip"`, `"tar"` or `"tar.gz"` with a `location`
  table of type `posix` or `s3`, the spec parser SHALL load it without `SpecError`;
  `OverlaySpec.source.type` SHALL read the declared type, and the source's tier spec SHALL carry the
  parsed `LocationSpec`. WHEN a `tar.gz` source declares `sync = { max_frequency = … }`, the parsed
  rate SHALL be carried. The two archive examples of `PLAN.md` § *Configuration* that are not sharded
  (direct-update ZIP, staged tar.gz on S3) SHALL load with their `[[tier]]` tables unchanged, inside an
  `[overlay]` table and, where the example shows no stage tier, above one POSIX shared stage tier.
  IF a `zip` or `tar` tier carries `sync`, THEN the parser SHALL refuse it at load, naming the tier.
- **R2** — IF a tier carries the retired `staging` key, THEN the spec parser SHALL refuse it at load
  with a message naming `staging` and the tier that holds the staged bundle under Decision 1. IF a
  tier declares `sharded = true`, THEN the parser SHALL refuse it at load as a known key whose feature
  is not yet supported, with a message naming the tier and `issues/sharded-archive-sources.md` (not
  as an unknown key), which the bundle loop's stage 9 can pin.
- **R3** — IF a tier at a stage position declares `zip`, `tar` or `tar.gz`, THEN the spec parser SHALL
  refuse the spec at load with a message naming that tier.
- **R4** — IF a spec has an archive source of any of the three types and its stack does not have
  exactly one shared stage tier, directly below the source, of type `posix`, THEN the spec parser
  SHALL refuse it at load with a message naming the tier at fault (or, with no stage tier, naming
  the missing one). No spec the parser loads SHALL reach the `EngineError` of `eligible_tier`
  (`overlay.py:230-231`) for want of a POSIX stage tier.
- **R5** — IF two tier roots overlap once symlinks are resolved, or the state directory resolves
  inside a tier root, THEN the spec parser SHALL refuse the spec at load, naming both tiers (or the
  tier and the state directory). For an archive tier the root compared is its inner location's file
  path; an S3 location keeps its url as today. A unit test SHALL build a stage root that is a symlink
  into the source root and observe the refusal.
- **R6** — WHEN an overlay engine starts over a spec with an archive source, `engine.source` SHALL be
  an `ArchiveTier` over the inner location the spec names, not a `PosixTier`; the start SHALL succeed;
  and a second start over the same state directory, after a clean stop, SHALL also succeed, the
  archive tier's registration reading `OK` both times although the archive carries no marker of its
  own; no file or object SHALL be written at or beside the archive's inner location.
- **R7** — WHEN a `tar.gz` source's members are first read, the staged bundle SHALL be one file inside
  the durable tier's root (none outside any tier root); the durable tier's recorded usage SHALL count
  it as one file at its uncompressed size; and a listing of the overlay root through the engine API
  SHALL name exactly the archive's top-level members, the staged bundle not among them.
- **R8** — WHEN the engine serves an archive source of each of the three types, a listing through the
  engine API SHALL give the archive's member names and every member read SHALL return the archive's
  bytes. Stages 1 (reach) and 2 (read) of the bundle loop (`tests/loop/stages.py`) SHALL be plain
  passing tests under the `unit` marker, with no expected-failure marker, over the spec the loop
  helper writes, which SHALL carry no `staging` key.
- **R9** — WHILE the source is an archive, every mutating call of the engine API SHALL fail with
  `OSError` `EROFS` and leave the archive's bytes unchanged.
- **R10** — WHEN a real FUSE mount is served over a ZIP source, a `readdir` of the mount root SHALL
  list the archive's top-level members. Observable only on Linux with `/dev/fuse`: CI runs no
  integration job (`issues/ci-integration-lane.md`), so the test is under the `integration` marker
  and skips elsewhere. Graded by running `uv run pytest -m integration` on a Linux host with
  `/dev/fuse` and asserting this test in the pass count, never by exit status (`AGENTS.md`
  § *Conventions*); a review without such a host records R10 as ungraded, not passed.
- **R11** — The root `PLAN.md` and `AGENTS.md` SHALL say what the tree does after this cycle, in the
  same diff: § *Configuration*'s paragraph that no archive example loads (`PLAN.md:1229-1233`); the
  `staging` note under ArchiveTier (`PLAN.md:653-655`); the Decision 1 and Decision 7 rows of § *What
  the tree does not yet do* (`PLAN.md:290`, `:295`), each narrowed to what remains; § *Managed
  locations* (`PLAN.md:825-831`), saying an archive source carries no marker while writes over it are
  refused; and the § *Known open violations* row for this seed in `AGENTS.md`, removed or narrowed.

## Non-goals (no-gos)

- **Writing through an archive source**: lifting R9's refusal, and with it settling where the
  marker of a direct-update archive the overlay re-packs in place lives (Q1, write case). No seed of
  its own: the cycle that first lets a write reach an archive source does both, as the note of
  2026-11-28 in `issues/archive-journaling-and-locking.md` records. It waits on that seed (bracket,
  lock) and, for a staged bundle, on Decisions 2 and 3 (`issues/namespace-sync-redesign.md`,
  `issues/archive-finalization-integration.md`).
- Sharded sources (`issues/sharded-archive-sources.md`); this cycle only settles how the key is
  refused (Q2).
- Archives at stage positions, in v1 (R3).
- The pressure rule that reclaims a staged bundle (`issues/eviction-one-hop.md`, Decision 1's
  addition of 2026-11-15); whether a spec must state limits (`issues/eviction-one-hop.md`).
- The member index and bundle record on the durable tier (Decision 4, `issues/durable-tier-record.md`):
  the index stays in `archive_index` as the tree has it.
- `resync`, rebuilds and their triggers (`issues/archive-finalization-integration.md`).
- An S3 source with no POSIX stage tier, which reaches the same `EngineError` and which
  `issues/engine-error-mapping.md` names; R4 covers archive sources only.
- SFTP and any inner location beyond `posix` and `s3` (`issues/sftp-inner-location.md`).
- A read-only mount mode as a user choice (`issues/read-only-mount.md`).
- An integration job in CI (`issues/ci-integration-lane.md`).

## Clarifications

- **Q:** May an archive tier sit at a stage position, and what would a node-scoped one mean? —
  **A:** No. Archive tiers are source-only in v1: a spec that puts `zip`, `tar` or `tar.gz` at a stage
  position is refused at load, naming the tier (R3). This also closes the node-scope question, since
  `_validate_stack` already refuses a node-scoped source (`spec.py:315-316`). (The foreman's ruling
  at shaping; resolved 2026-11-28.)
- **Q:** Does the retired `staging` key still parse? — **A:** No; refused naming the tier that holds
  the bundle. Settled by Decision 1 of the root `PLAN.md`, signed 2026-11-15 (`PLAN.md:136-157`,
  `:1221-1223`); carried, not decided, here (R2).
- **Q:** Which stack does a bundle source need? — **A:** Exactly one shared stage tier, directly below
  the source. Settled by Decision 7, signed 2026-11-15 (`PLAN.md:253-268`); the stage tier being POSIX
  follows from `PLAN.md:333` ("Stage tiers are POSIX directories") (R4).
- **Q:** Does this cycle or `issues/invariant-audit-gaps.md` own the symlink-blind overlap check? —
  **A:** This cycle (R5). `issues/invariant-audit-gaps.md` carries no symlink or overlap item (checked
  on `184115f`), and this cycle changes `_validate_stack` for R3 and R4, so the function changes once.
  (Shaping finding, kept at the GOAL gate; resolved 2026-11-28.)
- **Q1:** An archive source has no root for `.terrace/marker` and `ArchiveTier` implements no marker
  I/O; what does its registration record, so that a first and a second start both read `OK`? —
  **A:** Option A. An archive source carries no marker of its own: registration records it and its
  attach reads `OK` by type. What the overlay owns over a tar.gz source, the staged bundle, sits in
  the durable tier under that tier's marker. This holds only while Q3's refusal of writes stands; the
  cycle that first lets a write reach an archive source settles where a direct-update archive's
  marker lives (options then on the table: beside the archive, a fourth `InnerLocation` method, or in
  the durable tier). (The foreman's ruling at the GOAL gate; resolved 2026-11-28.)
- **Q2:** Is `sharded` a known tier key that is refused, or left unknown? — **A:** Option A. Admitted
  to the tier keys and refused at load, naming the tier and `issues/sharded-archive-sources.md`, so
  `TIER_KEYS` changes once (R2). (The foreman's ruling at the GOAL gate; resolved 2026-11-28.)
- **Q3:** Once an archive source is reachable, do mutating calls reach the adapter as the tree has
  them? — **A:** Option A. No: every mutating engine call over an archive source fails `EROFS` (R9);
  this cycle is the read side. The tree's per-call re-pack has no bracket and no lock (a recorded
  member-loss defect) and replays into a staged bundle as Decisions 2 and 3 overturn. (The foreman's
  ruling at the GOAL gate; resolved 2026-11-28.)
- **Q4:** Does Decision 7's stack rule bind a `zip` or `tar` source, or only `tar.gz`? — **A:**
  Option A. Every archive source, with one rule and one message (R4); a load-time refusal can be
  loosened later without breaking a spec. (The foreman's ruling at the GOAL gate; resolved
  2026-11-28.)
- **Q5:** Is `sync` accepted on a `zip` or `tar` tier? — **A:** Option A. Refused at load, as the
  parser refuses `url`/`s3` on a POSIX tier (`spec.py:236-237`): direct-update has no re-sync to
  rate-limit (R1). (The foreman's ruling at the GOAL gate; resolved 2026-11-28.)
- **Q:** Accept 11 R-IDs, or split under the size circuit-breaker? — **A:** Accepted as one
  deliverable at 11 R-IDs; no split. (The foreman's ruling at the GOAL gate; resolved 2026-11-28.)
- **Q:** Does this cycle refuse an S3 source with no POSIX stage tier? — **A:** No; it stays with
  `issues/engine-error-mapping.md` (Non-goals). (The foreman's ruling at the GOAL gate; resolved
  2026-11-28.)
- **Q (R2, PLAN D3):** What does the `staging` refusal name when the stack has no shared POSIX stage
  tier directly below the source, so no tier can hold the bundle? — **A:** It still refuses, and still
  names `staging` and the tier that carries it. Instead of a holder's name, it says the stack has no
  such tier. (The foreman's ruling at the PLAN gate; resolved 2026-11-28.)
- **Q (R6, PLAN D5, O1):** With no marker, what notices an archive source whose location changes, or
  whose archive is rewritten, between mounts of one state directory? — **A:** Registration records the
  location's identity and signature in `tier.meta`, and a re-attach that finds either one changed is
  refused. The message names the tier and the archive, and says the archive changed since this state
  directory recorded it. R6's second start is over the same archive. (The foreman's ruling at the PLAN
  gate; resolved 2026-11-28.)
- **Q (R7, PLAN D6):** When is a tar.gz bundle pulled? — **A:** At engine start, when the durable tier
  does not already hold it, under an intent and counted. It is therefore in place and counted when
  members are first read. Pulling lazily across many bundles belongs to
  `issues/sharded-archive-sources.md`. (The foreman's ruling at the PLAN gate; resolved 2026-11-28.)
- **Q (R8, PLAN D4):** Which tar or zip entries are "the archive's member names"? — **A:** Regular
  files, directories and symlinks. A hard link and a sparse member read as regular files with their
  data. Device, FIFO and character members are not listed, under v1's special-files exclusion
  (`AGENTS.md` § *Out of scope for v1*). Neither are entries the adapter drops as unsafe: names under
  `.terrace/`, names containing `..`, and the root. (The foreman's ruling at the PLAN gate; resolved
  2026-11-28.)
- **Q (R9, PLAN D7):** Which engine calls are "mutating"? — **A:** A mutating call is a call that
  changes the namespace, the bytes or the metadata of an entry. `fsync`, `release`, `flush`,
  `commit_row`, `commit_upstream`, `commit_to_shared` and `evict_copy` are not refused: they act only
  on admitted writes, and with every write refused they do nothing. (The foreman's ruling at the PLAN
  gate; resolved 2026-11-28.)

## Related materials

- Seed: [`issues/archive-spec-plumbing.md`](../../issues/archive-spec-plumbing.md), notes of
  2026-11-11 and 2026-11-16.
- Root `PLAN.md`: Decisions 1 and 7 (`:136-157`, `:253-268`); § *What the tree does not yet do*
  (`:290`, `:295`); § *Vision and scope*, in scope (`:330-333`); § *The tier model* (a), (b), (d)
  (`:449-469`, `:500-511`); ArchiveTier (`:634-677`); § *Managed locations* (`:825-831`);
  § *Configuration* (`:1135-1233`).
- Earlier contract: [`spec/local-bundle-loop/GOAL.md`](../local-bundle-loop/GOAL.md) R4 (this seed owns
  stages 1 and 2) and R5 (a stage that starts passing fails the run until its marker is removed).
  That GOAL's R5 pinned stage 1 at the parser's unknown-key refusal on its own tree; R8 here replaces
  that pin by a pass, which its R5 requires.
- Adjacent seeds: `issues/archive-journaling-and-locking.md`,
  `issues/archive-finalization-integration.md`, `issues/archive-tier-test-coverage.md`,
  `issues/sharded-archive-sources.md`, `issues/engine-error-mapping.md`,
  `issues/invariant-audit-gaps.md`, `issues/read-only-mount.md`, `issues/eviction-one-hop.md`,
  `issues/durable-tier-record.md`, `issues/ci-integration-lane.md`.
- Source: `src/terrace/tier/spec.py`, `src/terrace/tier/archive.py`, `src/terrace/engine/overlay.py`,
  `src/terrace/state/registry.py`, `tools/bundle_loop.py`, `tests/loop/`, `tests/tier/test_archive.py`,
  `tests/tier/test_spec.py`.
- Profile: `AGENTS.md` § Factory, § Invariants (high-blast-radius: `tier/spec.py` `_validate_stack`,
  `SUPPORTED_TYPES`, `TIER_KEYS`; `engine/overlay.py`; `state/registry.py` `_attach`;
  `tier/archive.py`), § Known open violations, § Conventions.
