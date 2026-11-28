# REVIEW — Archive sources reachable from a spec file, read-only

> Adversarial QA by `code-review`, run in an isolated context (a fresh subagent). The correctness pass
> graded the branch diff against [`GOAL.md`](GOAL.md) and the repo's `AGENTS.md` § Invariants only;
> it did not see this cycle's `PLAN.md` or `TECH.md`. Every finding cites an executed command.

- **Reviewed commit:** `fcb11dcf47dd787c2cdd59ae2e5e2d16d913f499`  ·  **Base:** `origin/main` at `184115f`  ·  **Date:** 2026-11-28
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3
- **Human gate:** triggered. F1, F2 and F3 are CONFIRMED in paths on § Invariants' high-blast-radius
  list. No remediation starts without the maintainer's sign-off.

Evidence is in the worktree's git-ignored `.local/review1/` (probes and `logs/`). Publish copies it to
the main checkout's `.local/archive-spec-plumbing/`.

## Verification run

- **Spec-held gate, run by the orchestrator before the reviewer launched:** `tools/lint.sh &&
  check_outcomes.py p5` at `fcb11dc` → `gate: p5 ok`. The unit lane ran 947 passed and 7 xfailed
  (floor 946). The four planted mutants were killed (read-only 51/51, oracle 2/2, counter 1/1,
  realpath 6/6). Stages: 2 passed and 7 xfailed against their pins. Harness, examples, collect,
  docs, ledger, figures and ids were all ok. This gate's green covers only the mutants it lists; it
  does not close the classes below. Log: `.local/review1/spec-gate.log`.
- **The profile's verify, run by the reviewer in its own venv:** lint passed. 954 collected, 947
  passed, 7 xfailed, 0 skipped (`logs/verify.log`).
- **Throwaway-environment drives:** every probe ran through `tools/temp_site.sh`, never against
  real state:
  - `probe_parser`: 44/44 parser cases as expected;
  - `probe_engine`: engine start, reads, `EROFS`, and the oracle against the counter;
  - `probe_escape`, `probe_dirmode`, `probe_edges`, `probe_s3loc`, `probe_staging_holder`,
    `probe_failed_pull` and `probe_failed_start`;
  - the bundle-loop scenario: stages 1 and 2 pass with no marker, and 3 to 9 xfail.
- **Mutation sweep by the reviewer:** 41 hand mutants in `git archive` copies under scratch. 37 were
  killed and 4 survived, and each survivor also passes the full unit suite
  (`logs/survivors_fullsuite.log`).
- **R10:** ungraded. The test is `tests/fuse/test_integration.py::test_zip_source_mount_readdir_lists_top_level_members`,
  under the `integration` marker. It skips here ("requires Linux with libfuse and /dev/fuse",
  `logs/r10.log`) and is graded on a Linux host with `/dev/fuse` by its pass count.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `tier/spec.py` (keys, types, `_archive_location`, `_sync`, `_rate`) | `probe_parser`: both `PLAN.md` examples load as written; type, location and rate are carried; `sync` on zip/tar is refused naming the tier | ✅ |
| R2 | `_refuse_staging`, `_refuse_sharded` | `probe_parser`: four `staging` cases, the sharded example, and `sharded` on posix. S2: the holder-naming check is untested | ✅ (S2) |
| R3 | role check in `TierSpec.from_data` | `probe_parser`: 7 cases refused, naming the tier | ✅ |
| R4 | `_validate_stack` | `probe_parser`: refused with no stage tier, a node tier below, S3 below, two shared tiers, or node-then-shared; shared posix plus a node tier loads. The `eligible_tier` error is unreachable from a loaded spec | ✅ |
| R5 | resolved roots and state directory in `_validate_stack` | `probe_parser`, 8 cases; `tests/tier/test_spec.py:546`. S1: the S3-location clause is untested | ✅ (S1) |
| R6 | archive branch of `OverlayEngine.start`; `TierRegistry.enlist` | `probe_engine`: an `ArchiveTier` over the named location; the second start is OK; the archive is unchanged; a changed archive is refused (`probe_edges` B) | ✅ |
| R7 | `_stage_bundle`, `PosixTier.usage` | `probe_engine`: one `.terrace/bundles/src.tar` at its uncompressed size; the oracle equals the counter; the listing equals the members. **F3** breaks it for some tier names | ❌ (F3) |
| R8 | `tier/archive.py`, the read path | `probe_engine` reads all three types; loop stages 1 and 2 pass. **F1** and **F2** break it on reachable inputs | ❌ (F1, F2) |
| R9 | guards in `engine/overlay.py` | `probe_engine`: 16 by-path and 2 by-handle calls fail `EROFS`; the archive is unchanged | ✅ |
| R10 | `tests/fuse/test_integration.py:202` | collected under `integration`; skips on this host | ungraded |
| R11 | `PLAN.md`, `AGENTS.md` | `PLAN.md:1238`, `:643`, `:653`, `:290`, `:295`, `:836`; the `AGENTS.md` seed row is removed and the archive rows are narrowed. **F4**: the operating manual misses new symbols | ✅ (F4) |

Unmapped changes:
- The new seed `issues/source-directory-mode.md` and its `ROADMAP.md` entry.
- Residual notes in five seeds.
- The `AGENTS.md` figures.
- `try`/`finally` hygiene in loop stages 7 and 8.
- The stage-3 pin.

All are ledger or loop-rule bookkeeping and creep nothing.

## Findings

### [CRITICAL/CONFIRMED] F1 — A damaged or encrypted ZIP member raises a non-`OSError` out of the VFS API
- **Where:** `src/terrace/tier/archive.py:438-441`, the ZIP branch of `_extract`. It is reached
  through `ArchiveTier.read` (`:544`) from the promotion in `OverlayEngine.open`
  (`engine/overlay.py:762`) and from `_serve` (`:812`).
- **Failure scenario:** a ZIP source holds a member with a bad CRC-32, or an encrypted member.
  - `open` raises `zipfile.BadZipFile: Bad CRC-32` or `RuntimeError: File 'bad.txt' is encrypted,
    password required for extraction`.
  - Through FUSE, mfusepy logs an uncaught exception and returns `EINVAL`.
  - On the base no spec could reach `_extract`; this diff makes the escape reachable.
- **Evidence:** `probe_escape.py`, `logs/probe_escape.log` (`NON-OSError ESCAPED`).
- **Touches:** § 11 (the engine API contract: every failure surfaces as an `OSError`) and R8. The
  Known open violations row for § 11 names only `EngineError` and `LookupError`. The note this diff
  adds to `issues/engine-error-mapping.md` covers only the start-time scan.

### [HIGH/CONFIRMED] F2 — A directory member without owner write makes every read below it fail `EACCES`
- **Where:** the promotion path. `engine/transfer.py:37` reaches `tier/posix.py:189-192`, which
  creates the stage ancestor with the archive's exact mode bits; the rename into it is then refused.
  The diff fixes only directories with no mode (`archive.py:52`, `:502`).
- **Failure scenario:** a zip, tar or tar.gz source whose directory member is `0o555` (typical of an
  archive of a read-only tree). Opening any member below it as a non-root user fails `EACCES`. The
  same archive with `0o755` reads fine. The failure leaves nothing behind: no temp, intent or
  counter.
- **Evidence:** `probe_dirmode.py`, `logs/probe_dirmode.log`, `logs/probe_dirmode_after.log`.
- **Touches:** R8 ("every member read SHALL return the archive's bytes"). The diff files the general
  case as `issues/source-directory-mode.md`, pre-existing for POSIX and S3 sources. Whether R8
  covers it for archive sources is a ruling for the maintainer (below).

### [MEDIUM/CONFIRMED] F3 — The source tier's name becomes the bundle's file name unchecked
- **Where:** `bundle_path` (`archive.py:165-167`), called at `overlay.py:158`. The parser accepts any
  non-empty tier name, and this diff introduced the first path built from one.
- **Failure scenario:**
  - A source named `.cold` or `a/b`: the engine counts 10240 B and 1 file on `scratch`, but
    `PosixTier.usage()` counts 0 (it counts only non-dot `*.tar` directly in `bundles/`), so an
    audit rebuild would "correct" the counter to a wrong value.
  - A source named `../../../outside`: the bundle is written outside every tier root and still
    counted against `scratch`.
- **Evidence:** `probe_edges.py`, `logs/probe_edges.log` § A (`AGREE=False`; the `deep-dotdot`
  path).
- **Touches:** § 4 (`usage()` as the rebuild oracle) and R7 ("none outside any tier root").

### [MEDIUM/CONFIRMED] F4 — The operating manual misses what this diff adds to gated paths
- **Where:** `AGENTS.md:243`, the high-blast-radius row for `engine/transfer.py — copy`.
- **Failure scenario:** the next cycle's plan and review read `AGENTS.md`'s gated list and miss the
  new places that carry the same risk:
  - The row calls `copy` "the single place an intent bracket wraps a real byte move, and where tier
    counters are adjusted". `_stage_bundle` (`overlay.py:188-220`) now does both and is on no row.
  - Two Known open violations rows rest on "every write over an archive source is refused
    `EROFS`", but four of the guard sites are on no row: `mkdir` (`:434`), `unlink` (`:500`),
    `rmdir` (`:514`) and `_set_meta` (`:732`).
  - `OverlayEngine.read_only` and `TierRegistry.enlist` (`registry.py:142`), the archive source's
    only swap detection, appear nowhere in `AGENTS.md`.
- **Evidence:** `grep -n 'enlist\|read_only\|_stage_bundle\|signature' AGENTS.md` returns nothing.
- **Touches:** § Conventions (the operating manual changes with the code). The high-blast-radius
  list is the maintainer's to change.

### Surviving mutants (the code at HEAD is correct; a test is missing)

Each survivor routes as a sweep over every decision in the function it sits in.

| # | Severity | Where | What survives | Evidence |
|---|---|---|---|---|
| S1 | MEDIUM, blocks | `tier/spec.py:432` (`_validate_stack`, high-blast-radius) | Dropping the S3-location branch from the overlap key lets a zip source at `s3://b/p/a.zip` load beside a node-scoped S3 stage at `s3://b/p`. HEAD refuses it. Reachable R5 input | `logs/probe_s3loc.log` |
| S2 | MEDIUM, blocks | `spec.py:389` (`_refuse_staging`) | Dropping the POSIX type check makes the refusal name a shared S3 tier as "the shared POSIX stage tier". This breaks R2's PLAN-gate Clarification | `logs/probe_staging_holder.log` |
| S3 | LOW | `overlay.py:214` | Without the rollback, a failed pull (a truncated gzip raises `EOFError` at start) leaves a `promote` intent unresolved (§ 1, bracket discipline) | `logs/probe_failed_pull.log` |
| S4 | LOW | `spec.py:289` | `sync` on a posix or s3 tier is accepted silently. R1 names zip and tar only, and both are still refused | `probe_parser` |

**Checked and dropped by the reviewer:**
- A refused restart over a changed archive leaves the overlay `mounted`. A POSIX source whose root
  went missing behaves the same, so this is pre-existing for every start failure
  (`logs/probe_failed_start.log`).
- Stage 3's stop at the eviction check is not this diff's defect.

## Human-gate triggers

**Triggered.** F1 cites `tier/archive.py` and `engine/overlay.py` (`open`, `_serve`). F2 cites
`engine/transfer.py` and `tier/posix.py`. F3 cites `archive.py` and `posix.py` `usage`. All of these
are on § Invariants' high-blast-radius list. The gate holds until publish: a later clean cycle still
needs the maintainer's sign-off recorded here.

## Routing proposed to the maintainer (not yet ruled)

- **F1, as a class:** every non-`OSError` an archive read can raise at a member read becomes an
  `OSError` (`EIO`) at the adapter boundary, naming the member. That includes `BadZipFile`, the
  encrypted-member `RuntimeError`, `zlib.error`, `EOFError`, `tarfile.ReadError` and the
  unsupported-compression `NotImplementedError`. One pinned test per shape, for all three archive
  types, through the engine API.
- **F2, a ruling first:** does R8 cover a source directory without owner write? The foreman
  recommends yes, for archive sources: R8's wording is "every member read".
  - Proposed fix: a stage-side directory made by promotion always carries owner `wx`, while the
    catalog keeps the archive's recorded mode.
  - `issues/source-directory-mode.md` keeps the POSIX and S3 cases.
- **F3:** tier names that become path components are refused at load. A name SHALL NOT be empty,
  start with `.`, or contain `/` or `..`. The rule binds every tier, since any name may later build a
  path. `bundle_path` asserts that its result lies inside `bundles/`.
- **F4:** an `AGENTS.md` diff adding `_stage_bundle`, the four guard sites, `read_only` and `enlist`
  to the high-blast-radius table, and fixing the "single place" wording. The table is the
  maintainer's, so the diff is shown to him before it lands.
- **S1 and S2:** a mutant sweep over every decision in `_validate_stack`'s overlap keys and in
  `_refuse_staging`, with a test that kills each mutant.
- **S3:** a test that a failed pull resolves its intent.
- **S4:** `sync` is refused on every tier but `tar.gz`, which is the message P4's refusal already
  writes for other tier types.
