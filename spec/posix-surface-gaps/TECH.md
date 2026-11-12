---
slug: posix-surface-gaps
title: Entries created through the mount belong to their creator, and link is refused plainly
kind: fix
appetite: small
status: in_progress
branch: fix/posix-surface-gaps
base: main
current_phase: P1
last_updated: '2026-11-12'
phases:
- id: P1
  name: Created entries take the caller's owner and the setgid group; link fails with EPERM
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && uv run pytest -m unit tests/fuse/test_surface.py tests/engine/test_ownership.py && uv run pytest -n auto -m unit
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — Entries created through the mount belong to their creator, and link is refused plainly

The **context engine and finite-state machine** for building this fix. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/posix-surface-gaps/TECH.md`); the
per-phase checklist below is the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** none (lean plan, `skip research`).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `code-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- `branch`, `base`: the cycle's branch (prefix per GOAL `kind`, from the profile's `branches`) and the
  profile's `base`.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `code-review`).
  **Artifact-deliverable R-IDs.** Some requirements are met by a *committed document* — a research
  audit, a protocol doc, an assessment — not by CLI-observable behaviour. Attach such an R-ID to the
  phase that produces and commits the artifact, and give that phase a content `verify:`
  (`test -f …`, a `grep -q` for the sections that must exist) rather than forcing a CLI drive.
  **Say so in the phase body**, because it changes who can grade it: `code-review` blinds its reviewer
  to all of `spec/`, so an R-ID whose evidence lives there is graded by the *orchestrator*, not the
  blind reviewer. Naming it in `TECH.md` is what lets the review skill route it correctly instead of
  reporting an unverifiable requirement.
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (docs, tests, isolated leaf
  modules). Any phase touching the profile's `coupled core` is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase (prefer driving the real CLI, not just tests —
  wrap CLI drives in the profile's `throwaway environment` helper, `<helper> sh -c "…"`, so they hit
  a disposable instance, never the developer's real data). A gate carrying `$( )` or quoting the
  calling shell would mangle is set with `set_phase.py --verify-file PATH`, not `--verify`.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`;
  REVIEW.md's "Cycle {n}" mirrors it and the ≤3-cycle bound is graded against it. `--verdict approved`
  also clears `review.blocked_reason` unless one is passed explicitly.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from the repo's `AGENTS.md` — it
  is the constitution. Walk the sections of its § Invariants that this change touches; that is the
  curated footgun checklist.
- One phase per `code-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. The branch commit subject is code-build's (`$KB/skill/code-build.md` →
  Step 6), not restated here; branch commits are squashed away at `code-publish` per the profile's
  `landing`. Trailers follow the profile's `branch commits`.
- The last phase carries the standing last-phase items: the deferral ledger (`code-plan` Step 6.8),
  and every item the previous cycle's last phase carried for a project-level obligation (mpsearch's
  `## Times` quote, taken as `code-build` Step 4 says). A plan that drops one says why in `PLAN.md`.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

---

## Phase P1 — Created entries take the caller's owner and the setgid group; link fails with EPERM
**Satisfies:** R1, R2, R3, R4, R5, R6, R7 · **Depends on:** —
**Goal:** a file or directory created through the adapter records the caller's uid, the caller's gid
or a setgid parent's gid, and (a directory) the parent's setgid bit; both reach a POSIX source on
commit; `link` fails with `EPERM`; the README, root `PLAN.md` and `AGENTS.md` say so in the same
commit. One phase: the same-commit rule ties `AGENTS.md`'s open-violations row and counts to the code
that retires the row, so the documents cannot split from the code.

R4's README line and R6's `AGENTS.md` edits are **committed documents**: the orchestrator grades them,
not the blind reviewer. R5's root `PLAN.md` sentence likewise.

- [ ] **Tests first, red on the merge base.** Write `tests/fuse/test_surface.py` and
      `tests/engine/test_ownership.py` as `PLAN.md` § 2 *Tests* lists them (`pytestmark = mark.unit`;
      the adapter's caller identity is a stand-in set with
      `monkeypatch.setattr(terrace.fuse.adapter, 'fuse_get_context', …, raising=False)`). Before any
      source edit, show them red on the merge base exactly as `PLAN.md` § 6 says: a detached throwaway
      worktree of `git merge-base HEAD origin/main` at `$REPO/.worktrees/posix-surface-gaps-base`, its
      own `UV_PROJECT_ENVIRONMENT` (suffixed `-base`, never the cycle's), `terrace.__file__` confirmed
      inside it, the two new files run by absolute path from it as the working directory, nothing
      copied in, removed with plain `git worktree remove`. Expected: every R1, R2, R3, R7 test and the
      `link` test fail on an assertion; the two R5 guards pass. Quote the per-test lines in the commit
      body or the build report.
- [ ] **Adapter** (`src/terrace/fuse/adapter.py`): module-scope `from mfusepy import … fuse_get_context`;
      `mkdir` and `create` pass the caller's `uid` and `gid` from `fuse_get_context()` (called by its
      module-level name); add `link(self, target, source)` raising `FuseOSError(errno.EPERM)`. No other
      state, no group logic, no `access`, no `symlink`.
- [ ] **Engine** (`src/terrace/engine/overlay.py`): in `create` and `mkdir`, `None` identity defaults to
      `os.getuid()` / `os.getgid()`; under a parent whose recorded mode has `stat.S_ISGID`, `gid` is the
      parent's; `mkdir` there ORs `S_ISGID` into the recorded mode and the queued row's `detail['mode']`;
      `create` passes `uid`/`gid` in the `FileMeta` it hands `dest.put`. `_execute_namespace_locked`'s
      `mkdir` branch and `_place_parents` pass `uid=entry.uid, gid=entry.gid` to `self.source.mkdir`.
- [ ] **Tiers**: `Tier.mkdir` in `src/terrace/tier/protocol.py` gains keyword-only
      `uid: Optional[int] = None, gid: Optional[int] = None`; `PosixTier.mkdir` applies them through
      `_apply_meta` (owner only) after `os.mkdir` and before its exact-mode `os.chmod`; `S3Tier.mkdir`
      and `ArchiveTier.mkdir` accept and ignore them (say so in their docstrings). `_apply_meta` itself
      is unchanged. Update any test fake tier whose `mkdir` an engine calls.
- [ ] **Documents, same commit**: `README.md` gains a `Limitations` list with one bullet (hard links
      are not supported; `link` fails with `EPERM`). Root `PLAN.md` § FUSE adapter gains one sentence,
      on its own new line after the paragraph: the mount records permission bits and does not enforce
      them (no `default_permissions`, no `access` callback); check `git diff --numstat <merge base> --
      PLAN.md` shows deletions `0` and the added lines are that sentence and its blank line.
      `AGENTS.md`: remove the § Known open violations row for this defect; "four a review of the whole
      tree reproduced on 2026-11-11" becomes "three"; move every test and test-file figure
      (§ Commands, § Repository map, § Conventions' integration line, § Verification) by the delta
      between `uv run pytest -m unit --collect-only -q` at the merge base and at this commit (and the
      file count by the two new modules), never to numbers copied from the plan.
- [ ] **Deferral ledger** (last phase): this plan defers nothing new (symlink creation already sits in
      `issues/symlink-create.md` from the shape commit). Walk this phase for "do not fix here", "known
      limitation" or "follow-up" phrasing in list items and prose (not headings) and confirm each has an
      `issues/` file and a `ROADMAP.md` entry; the real-mount check of R1, R2 and R4 is recorded as
      **not run** in the build report and the review, not deferred.
- **Verify:** `tools/lint.sh && uv run pytest -m unit tests/fuse/test_surface.py tests/engine/test_ownership.py && uv run pytest -n auto -m unit`.
  Assert the counts from the log, not the exit code: both new modules pass with at most one skip (the
  single-group R3 case, with its reason), and the unit lane reports no failure with a pass count equal
  to the merge base's plus the new tests.
- **Touches:** `src/terrace/fuse/adapter.py`, `src/terrace/engine/overlay.py`,
  `src/terrace/tier/protocol.py`, `src/terrace/tier/posix.py`, `src/terrace/tier/s3.py`,
  `src/terrace/tier/archive.py`, `tests/fuse/test_surface.py`, `tests/engine/test_ownership.py`,
  `README.md`, `PLAN.md`, `AGENTS.md`.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[{category}]` commit; stop and report.
