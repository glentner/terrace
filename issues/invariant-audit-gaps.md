---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Small gaps behind inaccurate invariants

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

Six independent findings from one audit. Each is too small to justify a cycle alone, and each is a
place where a property the repository asserts — in a schema comment, a docstring, a spec key, a
`PLAN.md` sentence — is not the property the code has. They are grouped because they share an origin
and a verification substrate, not a mechanism; see the last open question.

**1. `residency.checksum` is never written.** The column is reserved at
`src/sosfs/state/schema.sql:95` ("blake2b hex, when checksum recording is enabled"), it is in
`RESIDENCY_FIELDS` (`src/sosfs/state/catalog.py:88`) and in the insert
(`src/sosfs/state/catalog.py:196-203` (holds at `141aa86`)), and `OverlaySpec` parses `checksum = true` from the
`[overlay]` table (`src/sosfs/tier/spec.py:287-289`, key at `:27`). Nothing computes a digest:
`blake2b` occurs in `src/` only in that schema comment, and `spec.checksum` has no reader anywhere
under `src/sosfs/engine/` or `src/sosfs/fuse/`. An operator who sets the flag gets an inert
configuration key and no error. The cost lands downstream — `PLAN.md` line 348 has `audit --full`
verify clean copies against the checksum, line 322 has `sosfs commit` skip a file whose recorded
checksum already matches upstream, and line 389's config comment advertises both. Neither has anything
to compare against, so any criterion in `issues/audit-command.md` or
`issues/offline-commit-command.md` that mentions checksums has to fund the recording first. **Unbuilt
work**, not a live defect — but a spec key that silently does nothing is a defect on its own.

**2. `tier.generation` never advances in production.** `TierRegistry.bump_generation`
(`src/sosfs/state/registry.py:151-157`) has no caller under `src/`; the only call sites are
`tests/state/test_registry.py:92-97`. `MarkerStatus.GENERATION_MISMATCH` therefore only ever arises
from someone editing a marker file out of band, never from a re-initialization sosfs performed itself.
The status is implemented, tested and turned into an `EngineError` by `src/sosfs/engine/overlay.py:128` (holds at `141aa86`),
so it reads as a live safety check on a code path that has never fired. Whatever operation was meant to
bump it — a deliberate tier reset, a re-register after an evaporation — does not exist. **Unbuilt
work**; the risk is that a future reader trusts the mismatch branch as exercised.

**3. `intent.temp_name` is dead, and the temps that exist are recognized inconsistently.**
`Journal.begin` writes the column when the caller passes `temp_for`
(`src/sosfs/state/journal.py:229-247`); no production call site passes it — only
`tests/state/test_journal.py:111,152` and `tests/state/test_survival.py:57`. Meanwhile two kinds of
temp are actually created. `PosixTier.put` writes `.sosfs/partial/put-{uuid4().hex}-{name}`
(`src/sosfs/tier/posix.py:133-135`), which `is_temp_name` recognizes because it classifies by the first
two path components rather than by filename (`src/sosfs/state/journal.py:42-45`).
`PosixInnerLocation.atomic_write` writes `.{archive}.{hex}.tmp` as a *sibling of the archive file*
(`src/sosfs/tier/archive.py:99-109`), which the convention does not recognize at all — and because it
sits beside the archive rather than inside a tier root, no sweep and no `usage()` walk will ever see
it. So an audit written to "find the temp named in the intent row" finds `NULL` in every row, the
prefix convention is the only one that works, and a crash mid-re-pack leaves debris nothing cleans.
**Unbuilt work waiting on `issues/audit-command.md`**, except for the archive temp, which is live
crash debris today.

**4. `statfs` reports an entirely full filesystem for an unquota'd non-POSIX source.**
`OverlayOperations.statfs` calls `os.statvfs(source.path)` and falls back to `_quota_statvfs` on
`OSError` (`src/sosfs/fuse/adapter.py:104-114`). For an S3 source `TierSpec.path` holds the url string
(`src/sosfs/tier/spec.py:189`), so the `statvfs` raises and the fallback fires, which is the intended
design (`PLAN.md` line 264: "configured quota for unbounded sources like S3"). But
`src/sosfs/fuse/adapter.py:119` reads `total = source.quota.size or 0`, and nothing in
`OverlaySpec._validate_stack` (`src/sosfs/tier/spec.py:304-323`) requires a source to declare a quota —
`PLAN.md` line 67 calls the source "typically unquoted". The result is `f_blocks = f_bfree = f_bavail =
0`: `df` reports 100 % full, and every tool that pre-checks free space before writing refuses to touch
a mount that works fine. This is a **live defect on `main`** with a user-visible wrong answer. The
adapter runs mount-free in `tests/fuse/test_adapter.py`, so the wrong return value is checkable in a
unit test; the `df` behaviour itself needs a real mount on Linux and an S3 source.

**5. There is no directory `fsync` anywhere.** Three sites write a temp, `fsync` the file descriptor
and then `os.replace`, and none of them fsyncs the containing directory:
`src/sosfs/tier/posix.py:144-146`, `src/sosfs/tier/archive.py:106-107` and
`src/sosfs/state/lifecycle.py:182-183`. The data is durable; the *name* is not. On a power loss the
rename can be lost while the content is present, so the tier reverts to the previous file while the
catalog records the new residency as clean — after which eviction is licensed to drop the copy it
believes is redundant, and a promotion copies stale bytes downstream. Treat this as a **repo-wide
omission and a design decision, not a drive-by fix**: the state store already concedes the same
power-loss class deliberately (`synchronous = NORMAL`, `src/sosfs/state/db.py:94-97`, justified at
`:60-63` on the grounds that a power loss evaporates the volatile tiers anyway and the audit runs
regardless). Making the tier adapters stricter than the database is a claim about which failure model
the project stands behind, and belongs in `PLAN.md` before any of the three sites change.

**6. `S3Tier.rename`'s docstring claims an atomicity S3 does not have.**
`src/sosfs/tier/s3.py:245` reads "files rename atomically, directories move their whole prefix (not
atomic)". S3 has no server-side rename; obstore implements `obstore.rename` as copy-then-delete, so a
crash between the two leaves the object live at both keys. The second clause is correct and the first
is wrong. The cost is not the sentence but what is built on it: `AGENTS.md` § *Invariants* § 4
asserts "`put` and `rename` are all-or-nothing" per adapter, `s3.py` is the deferred-rename replay
target, and there is no audit to sweep the duplicate. A future change that treats an S3 rename as one
crash-atomic step will be graded correct against a docstring that licensed it. **Live documentation
defect**, and the repair is a sentence plus a decision about whether replay must tolerate the object
existing at both keys.

## Why it was deferred

Found by the invariant audit run while porting the software factory into this repository
(2026-08-22). The port was a harness change, and six unrelated product edits in that diff would have
been reviewed as harness work by nobody looking at engine behaviour. Findings 1, 2 and 3 additionally
have no consumer to satisfy until `issues/audit-command.md` lands, and finding 5 is a `PLAN.md`
decision under the plan-drift rule.

Recorded in `AGENTS.md` § *Invariants* § *Known open violations* as the unnumbered "Smaller
measured gaps" row, which names this file.

## Outcome / vision

Every property the repository asserts about itself is one the code has. A configuration key either
does something or does not exist. A column is either written or dropped. A docstring describing an
adapter's atomicity describes what the backend actually provides. Where the honest answer is "we accept
this failure mode", it is written down in `PLAN.md` as an accepted trade rather than left as an
apparent oversight.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed at promotion — and likely redistributed if the seed is split.

- **R1** — Where an overlay declares `checksum = true`, the engine SHALL record a blake2b digest in
  `residency.checksum` for every copy it writes: after a commit to the source, the residency row
  carries a digest equal to that of the bytes on that tier. Where the flag is absent, it stays `NULL`.
- **R2** — While the source tier declares no quota and exposes no filesystem, `statfs` SHALL report a
  non-zero available capacity rather than an entirely full filesystem — checkable mount-free by
  calling `OverlayOperations.statfs` against an S3-source spec; the corresponding `df` output needs a
  real mount on Linux with `/dev/fuse` and MinIO.
- **R3** — The archive adapter's re-pack temp SHALL be recognizable as crash debris by the same
  convention as the POSIX transfer temp — either by moving under `.sosfs/partial/` or by extending
  `is_temp_name` — so that after a `SIGKILL` mid-re-pack a sweep can name the file. Whether the sweep
  itself exists depends on `issues/audit-command.md`.
- **R4** — `intent.temp_name` SHALL either be written by the production paths that create temps, or be
  removed from `schema.sql`, `Intent` and `Journal.begin`, so that no code reads a column that is
  `NULL` by omission. Observable either way: no path in `src/` depends on a value nothing sets.
- **R5** — The repository SHALL state one answer for rename durability: either every temp-plus-rename
  site fsyncs its containing directory, or `PLAN.md` records the accepted failure model and the three
  sites agree with it. A reviewer can check the three sites against the stated decision.
- **R6** — `S3Tier.rename`'s docstring SHALL describe copy-then-delete and the both-keys crash window,
  and `AGENTS.md` § *Invariants* § 4 SHALL carry the same qualification in the same commit — the
  same-commit rule applies because the checklist currently asserts the stronger property.
- **R7** — The repository SHALL either name the production path that bumps `tier.generation`, or
  record that `GENERATION_MISMATCH` detects external tampering only, so the branch is not read as
  covering a case sosfs itself produces.

## Notes

- Related: `issues/audit-command.md` is the consumer for findings 1 and 3 — the checksum comparison,
  the intent-resolution walk and the orphan sweep all live there, and the recording half has to exist
  before those criteria can be written. `issues/offline-commit-command.md` needs the
  checksum for its match-and-skip step (`PLAN.md` line 322).
  `issues/archive-journaling-and-locking.md` touches `PosixInnerLocation.atomic_write` directly and
  should either fix the temp naming in finding 3 or say why not. `issues/plan-md-reconciliation.md`
  holds `PLAN.md` inaccuracies no other cycle corrects. `issues/ci-integration-lane.md` gates R2's
  mount-level half. `PLAN.md` § *State store*, § *FUSE adapter* (line 264), § *Audit* and
  § *Configuration* (line 389).
- Open question — **whether this stays one cycle.** `code terrace feature` may well split it into three or
  four. The natural seams: findings 1, 2 and 3 are audit substrate and could fold into
  `issues/audit-command.md` rather than land twice; finding 4 is a small fix with a mount-free test;
  finding 5 is a `PLAN.md` decision with a three-site follow-through; finding 6 is a docstring and a
  checklist line. Splitting costs four ROADMAP entries for work that would otherwise be one small
  cycle; keeping it whole risks the two-line fix waiting behind the design decision.
- Open question — **what `statfs` should report when the source is unquota'd.** Reporting a large
  synthetic capacity makes `df` and pre-flight checks work and lies about the number. Reporting the
  quota when one exists and something else when it does not means two behaviours to document. Making
  a quota mandatory on non-POSIX sources contradicts `PLAN.md` line 67 and forces every S3 operator to
  invent a figure. Whichever is chosen, `PLAN.md` § *FUSE adapter* records it.
- Open question — **checksum cost.** Recording blake2b at commit time means hashing every byte on
  every hop, which for a multi-gigabyte file on a source-facing boundary is real time. Decide whether
  the digest is computed during the copy that is already reading the bytes, or in a separate pass, and
  whether the flag stays overlay-wide or becomes per-boundary.
- Found by: the invariant audit run during the software-factory port (2026-08-22). Findings 1, 2 and 3
  from the state-store topic; 3 (the archive temp), 5 and 6 from the tier-atomicity topic; 4 from the
  FUSE-adapter and layering topic.
