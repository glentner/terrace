---
slug: deferred-rename-source-orphan
title: A commit before a queued rename leaves no orphan, and the replay converges
kind: fix
appetite: big
status: done
branch: fix/deferred-rename-source-orphan
base: main
current_phase: done
last_updated: '2026-11-06'
phases:
- id: P1
  name: Replay converges on the current target and vacates through the queue
  status: done
  satisfies:
  - R3
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/deferred-rename-source-orphan/verify/replay_gate.py replay mutants
- id: P2
  name: A data commit to the source lands where the source holds the entry
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/deferred-rename-source-orphan/verify/replay_gate.py commit mutants
- id: P3
  name: The model reaches single commits, scheduler polls and drains
  status: done
  satisfies:
  - R7
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  attempts: 2
  verify: uv run --no-project --python 3.15 python -I spec/deferred-rename-source-orphan/verify/replay_gate.py replay commit model mutants
- id: P4
  name: Verify is green under the fixed CI seed and twenty random seeds
  status: done
  satisfies:
  - R9
  - R10
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/deferred-rename-source-orphan/verify/replay_gate.py suite seeds
- id: P5
  name: Records match what ships, and CI is read from a run
  status: done
  satisfies:
  - R11
  - R12
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/deferred-rename-source-orphan/verify/replay_gate.py ci ledger records
review:
  last_reviewed_commit: 16082e9ea71f2e38a5f90149e260c84ed50c0acf
  verdict: changes-requested
  blocked_reason: 'Review cycle 3 (last): F-1 one unpinned stale cite, freethreaded-concurrency-stress.md:175 (LOW); escalated to Geoffrey'
  cycle: 3
---

# TECH.md — A commit before a queued rename leaves no orphan, and the replay converges

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/deferred-rename-source-orphan/TECH.md`);
the per-phase checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`,
and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs.
- **Gates:** [`verify/replay_gate.py`](verify/replay_gate.py), one mode per phase (PLAN § 6).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `code-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- `branch`, `base`: the cycle's branch (prefix per GOAL `kind`, from the profile's `branches`) and the
  profile's `base`.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `code-review`).
  **Artifact-deliverable R-IDs.** Some requirements are met by a *committed document*. Attach such an
  R-ID to the phase that produces and commits the artifact, give that phase a content `verify:`, and
  **say so in the phase body**: `code-review` blinds its reviewer to all of `spec/`, so an R-ID whose
  evidence lives there is graded by the *orchestrator*, not the blind reviewer. Here that is R11 (the
  saved CI run record, P5).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (docs, tests, isolated leaf
  modules). Any phase touching the profile's `coupled core` is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3.
- `verify`: the exact command that proves the phase. A gate carrying `$( )` or quoting the calling
  shell would mangle is set with `set_phase.py --verify-file PATH`, not `--verify`.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from the repo's `AGENTS.md` — it
  is the constitution. Walk the sections of its § Invariants that this change touches; that is the
  curated footgun checklist.
- One phase per `code-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. The branch commit subject is code-build's (`$KB/skill/code-build.md` →
  Step 6), not restated here; branch commits are squashed away at `code-publish` per the profile's
  `landing`. Trailers follow the profile's `branch commits`.
- The last phase carries the standing last-phase items: the deferral ledger (`code-plan` Step 6.8),
  and every item the previous cycle's last phase carried for a project-level obligation. The previous
  cycle (`toolchain-repair`) carried the ledger and the split CI read; P5 carries both. Its `claims`
  mode graded that cycle's own text and is not a project-level obligation.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

**This cycle in particular.**

- **Coupled core and human gate.** P1 and P2 edit `src/sosfs/engine/overlay.py`,
  `src/sosfs/engine/transfer.py` and `src/sosfs/state/journal.py` (the profile's coupled core) and
  high-blast-radius regions (`_commit_hop`, `_execute_namespace`, `_purge_source`, `_vacate`,
  `transfer.copy`, both engine test modules). Geoffrey signs off on the fix at review whatever review
  finds (GOAL Constraints). No phase edits `writeback.py`, `evict.py`, `drain.py`, `posix.py`,
  `s3.py`, `schema.sql` or `db.py`; a phase that finds it must is a reason to stop and re-read PLAN,
  and a `SCHEMA_VERSION` bump is a stop-and-ask.
- **§ Invariants text moves with the behaviour.** PLAN § 3's rows D1-D3 amend `AGENTS.md` § 5, and
  § 2.5 adds to § 10. Each amendment lands in the phase whose code makes it true (P1: D1, D2, § 10;
  P2: D3), worded as PLAN § 3 gives it. They are graded at review as a revision of the standard, on
  their merits.
- **No spec ids in code.** Comments in `src/` and `tests/` name constraints, never R-IDs or phase ids.
- **Where the gates run.** The development Mac, inside the agent sandbox, with these settings in every
  Bash call that runs `uv` (as separate statements, not one long assignment):
  - `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and `UV_TOOL_DIR` set to per-session directories under
    `$TMPDIR` (the profile denies the shared cache). The Python install dir must already hold a
    3.15 interpreter: the sandbox blocks Python downloads.
  - `pypi.org` and `files.pythonhosted.org` in `allowed_domains`: `tools/lint.sh` runs an unpinned
    `uvx ruff`, and `seeds` builds two scratch environments.
  - Scratch environments set `UV_PROJECT_ENVIRONMENT` to a scratch venv, so no stray `.venv` lands
    in a tree that should not hold one. `suite` syncs the worktree's own `.venv` (gitignored), as the
    profile's `setup` does.
  - Gate scratch goes under `$TMPDIR`; nothing is deleted. The shell is zsh: `rm` is blocked (move
    scratch aside instead), quote globs and separator strings, never name a variable `path`.
  - Running a gate outside the sandbox is approved for this effort if the sandbox blocks it; if that
    is refused, stop and report rather than work around it.
  - Runtimes measured on the prototype: `replay` and `commit` about 3 s each; `model` about 30 s (the
    baseline run stops at its first falsifying example); `mutants` about 1.5 min; `suite` about 1 min;
    `seeds` about 5-6 min on two workers; `prepush` about 1 min. Use long Bash timeouts or a background
    run. A hang: list stuck processes with `ps -axo pid,stat,command | awk '$2 ~ /^U/'` and report them.
- **Hypothesis state.** `.hypothesis/` in the worktree is gitignored and replays found failures between
  runs. `seeds` never reads it (each seed runs in a scratch tree from `git archive HEAD`, and moves any
  `.hypothesis/` aside first, never deleting it). `CI=true` gives Hypothesis's fixed seed; any `CI`
  value does, so `seeds` unsets it.

**Review cycle 1 remediation (2026-11-06).** Review 1 requested changes (`REVIEW.md`: F1, F2, F3, F5,
and the F4 coverage observation); Geoffrey ruled F2 and F3 fixed now. P1 (F3, F1), P2 (F2), P4 (the
measured counts) and P5 (records, seeds, a fresh CI read) are reopened; P3 is not, because
`test_model.py` stays untouched (Hypothesis's fixed CI draw is a digest of its source). The four share
the same-commit rule (`AGENTS.md`, `PLAN.md` and the gate's lists move with the code), so they land in
one commit. P1's gate is retuned to `replay mutants` and P2's to `commit mutants`. Each distinct step
runs once on the final tree and stands for every phase that names it: `replay`, `commit`, `model` (for
P3's standing gate), `mutants` (for P1 and P2), `suite`; `seeds`, `ledger` and `records` read
committed state and run after the commit, then `prepush`. P5 runs as two runs again: this run builds,
commits and pushes; Run B reads the push run and closes it.

---

## Phase P1 — Replay converges on the current target and vacates through the queue
**Satisfies:** R3, R5, R6 · **Depends on:** —
**Goal:** `flush()` completes on both shrunk sequences from GOAL Problem → 2 and leaves the source equal
to the overlay view; `mkdir` over a removed directory whose child's removal is queued succeeds; a
scheduler snapshot that a user op has made stale (run, cancelled, cascaded, or its id reused) changes
nothing. Built as PLAN § 2.1-2.3, § 2.5 and § 2.6 describe.

- [x] `src/sosfs/engine/overlay.py`: `_source_lock` (`threading.RLock`, with a comment giving its
  order and the three rules of PLAN § 2.5); `_target`; `_place_parents` (ancestor modes from the
  catalog **by entry**, one intent per created ancestor, `source_path` recorded in the confirming
  transaction).
- [x] `_execute_namespace` under the mutex, returning when `journal.current(row)` is `None`; per-op
  behaviour as PLAN § 2.2 (mkdir at T(E) or a no-op when placed; rename `source_path → T(E)` with
  ancestor placement, skipping the tier call when equal, then `advance_source_path` and the
  source-residency branch at the physical path; `meta` only at a non-`NULL` `source_path`; the strict
  tombstone rule using `Catalog.holder`; the inner-target pre-replay, which raises `EngineError` rather
  than settle a row unmoved). Remove the reliance on `detail['path']`; keep writing it.
- [x] `_vacate(path, mover=None)` and `_replay_through(rows)` as PLAN § 2.3; `rename` passes
  `mover=entry.id`. `_purge_source` replays the rows holding anything under a replaced directory first,
  through `_replay_through`, and rolls its intent back on `BaseException`.
- [x] `commit_row` and `_settle_moot` take the row and settle only a current one (PLAN § 2.6).
- [x] `src/sosfs/state/journal.py`: `Journal.current(row)` (PLAN § 2.6; its docstring says why ids
  are reused). Amend `Journal.queue`'s docstring: the recorded path is kept for the audit, and replay
  resolves the entry's current target. `src/sosfs/state/catalog.py`: `Catalog.holder(source_path)`.
- [x] `tests/engine/test_replay.py`: the helpers `tree`, `view`, `walk`, `assert_converged` (PLAN § 2.8:
  queues drained, no live directory unlisted, live catalog paths taken before any `readdir` equal to the
  source tree's, source equal to the view, remount equal to the view), `rewrite`, `directory_sequence`;
  the eleven `REPLAY_TESTS` and the one `DESIGN_TESTS` named in `verify/replay_gate.py`, each walking
  the tree before it acts and ending in `assert_converged(engine)`. The R5 pair is the GOAL's two shrunk sequences
  (`test_flush_after_directory_round_trip_and_reuse` for EACCES; `test_flush_after_directory_round_trip`
  and `test_flush_after_child_moved_out_of_removed_parent` for the stray directory in both forms); R6
  is the GOAL's sequence verbatim. Update the module docstring's list of guarded classes.
- [x] `AGENTS.md` § 5: D1 (the `_vacate` sentence) and D2 (the "moves the object … to the new path"
  sentence) as PLAN § 3 words them. § 10: the source mutex's order and its three rules.
  *Amended in build (2026-11-06):* the clause that `_commit_hop` takes the mutex (never a path lock)
  when its destination is the source moves to P2, whose code makes it true (Conventions: each
  amendment lands in the phase whose code makes it true). P1 also widens § Invariants'
  high-blast-radius rows for `overlay.py` with the new homes of moved code (`_execute_namespace_locked`,
  `_replay_through`, `_clear_inner_target`, `_place_parents`): the list only widens.
- [x] *Review cycle 1 (2026-11-06), F3:* `Journal.settle_current(row)` deletes a row only while it is
  still the row read (`current`), check and delete in one write transaction. The class, as a predicate:
  a settle by id of a row read before a window in which a user op can cancel it and queue another under
  its id. Evaluated over every `settle` in `src/`: `_execute_namespace_locked`'s confirming transaction
  and its entry-gone branch, `_settle_moot` and `flush`'s settle of a data row whose entry is gone now
  use it; `_commit_hop` and `commit_upstream` read their rows inside the settling transaction and were
  exact. The act's effect is recorded either way, so a removal that landed mid-act resolves where the
  object now is; a row nobody cancelled still matches, so the queue drains. Regressions: the five
  `REVIEW_REPLAY_TESTS` (the scheduler's `commit_row`, `flush`, a drain, a vacate's `_replay_through`,
  and `flush`'s data-row settle), each injecting a user `unlink` right after the source tier's real
  `rename` (or `put`) and asserting the removal took the row's id; each fails on `67dfaee`. M9 (a
  replayed row settled by id alone) fails all five. A removal racing a replayed `mkdir` mid-act, and
  queue-row identity by value, stay open and are deferred to
  `issues/freethreaded-concurrency-stress.md` (R9); `AGENTS.md` § Known open violations carries the
  first. No schema change.
- [x] *Review cycle 1 (2026-11-06), F1:* both high-blast-radius tables in `AGENTS.md` name every
  function holding replay, placement or stale-row logic: `_target`, `_holder_rows`, `create`,
  `commit_row`, `_settle_moot`, `flush`, `getattr`, `_record`, `_stat_held`; `Journal.current`,
  `settle_current`, `settle`, `cancel`; `Catalog.holder`, `advance_source_path`;
  `WritebackScheduler.due`, `poll`, `on_scan`, `on_commit`; `drain`. Nothing is removed. § 10 says
  removals take no source mutex, which is why every settle is exact, and names `_record` as a holder.
- **Verify:** `replay_gate.py replay mutants` (retuned in review cycle 1). Dry-run green on the planning prototype cut back to this phase:
  the twelve pass, the four baseline replay tests still pass, and each of the eleven `REPLAY_TESTS`
  fails on the `141aa86` engine on an assertion or an engine error (the `DESIGN_TESTS` entry passes
  there; `mutants` gives it teeth in P3).
- **Touches:** `src/sosfs/engine/overlay.py`, `src/sosfs/state/journal.py`, `src/sosfs/state/catalog.py`,
  `tests/engine/test_replay.py`, `AGENTS.md`.

## Phase P2 — A data commit to the source lands where the source holds the entry
**Satisfies:** R1, R2, R3, R4 · **Depends on:** P1
**Goal:** committing an entry while its rename (or its directory's rename) is queued writes the new
bytes where the source already holds the entry, and the queued rename later moves them: one object,
at the catalog path, with the committed bytes, after the queue drains, through `fsync` under the commit
policy, the eviction manager, and a scheduler poll that settles the data row first. Built as PLAN § 2.4.

- [x] `src/sosfs/engine/transfer.py`: `TransferPool.copy` gains `dest_path` (physical write path,
  default the logical path) and `source_path` (recorded with `catalog.update_meta` inside the
  transaction that confirms the copy, before `journal.confirm`); the intent's detail records
  `dest_path`. Stage destinations pass neither.
- [x] `src/sosfs/engine/overlay.py`: `_commit_hop`, when `upstream` is the source, under the mutex:
  destination `entry.source_path`, else `_place_parents(path)`; pass both to `copy`; drop the
  separate `update_meta(path, source_path=path)`. Keep the re-arm, `changed` detection and next-hop
  `mark_dirty` as they are. `create` straight onto a passthrough source places at T(path). The copy's
  intent detail records `dest_path`.
  *Amended in build (2026-11-06):* `_commit_hop` reads the entry inside the mutex, so a replay that
  moved it just before cannot leave the commit writing at the old place. `create` onto the source
  holds the mutex from placement through the catalog record (PLAN § 2.5's "placement, copy and
  recording"); `_place_parents` requires it, and without it a replay could move the parent between
  placement and the `put`. A staged `create` takes no mutex beyond `_vacate`'s.
- [x] `tests/engine/test_replay.py`: the five `COMMIT_TESTS` named in `verify/replay_gate.py`
  (the seed probe; `fsync` under `"commit"`; the eviction manager under a quota; a scheduler poll
  that settles data before the rename; a commit inside a renamed directory, with a sibling read
  through the overlay before the flush), each walking first and ending in `assert_converged`, and the
  first asserting that the queued rename is still the only mover before the flush.
  *Amended in build (2026-11-06):* each route test also asserts the source between the commit and
  the flush (PLAN R-a), and `test_commit_inside_renamed_directory` also creates and commits a new
  file inside the renamed directory, which has no source place of its own and lands under the old
  prefix through `_place_parents`. The module docstring lists the class.
- [x] `AGENTS.md` § 5: D3 (who sets `source_path`) as PLAN § 3 words it. § 10 (moved from P1,
  2026-11-06): the source-mutex bullet adds a data commit whose destination is the source, and
  `_commit_hop` takes the mutex (never a path lock) then; the `_source_lock` comment in `overlay.py`
  names the commit too.
  *Amended in build (2026-11-06), same-commit rule:* the § 10 bullet and the `_source_lock` comment
  also name `create` onto the source and the whole-copy span (D4). § 6's sentence that data landing
  before its tree makes `materialize_dirs` invent parents with default modes is reworded to what is
  true now (PLAN § 3, § 6 row): the source side places ancestors by entry, stage tiers keep
  `materialize_dirs`. § 1's confirm bullet names the source path as part of the confirming
  transaction. § Known open violations' § 5 row drops the commit orphan this phase closes and keeps
  `truncate` and the live stat, still under this cycle's seed until P5 names the new one. The
  repo-root `PLAN.md` → Write-back replay paragraph is brought to what P1 and P2 ship (current-target
  replay, vacate-through, where a data commit writes), moved here from P5 because the behaviour has
  landed.
- [x] *Review cycle 1 (2026-11-06), F2:* `_stat_held(entry, tier)` stats the source at `source_path`
  (every other tier at the logical path); `getattr`'s live stat and `_record` use it, and `_record`
  holds the source mutex for a copy served off the source. The class, as a predicate: a stat or open of
  the bytes a handle serves, on the source, at a path other than `source_path`. Evaluated over every
  tier `stat`/`open_direct` in `src/sosfs/engine/`: `getattr` and `_record` fixed; `open` and `_serve`
  were right; `_commit_hop`'s holder stat never names the source; `truncate` without a handle is the
  one left, ruled out of this cycle and kept in `issues/source-path-truncate-and-stat.md`, which now
  says the live-stat half is closed (for `bypass_size` files too) and records the widened trigger (a
  file created on the source under an ancestor's queued rename). `assert_converged` compares sizes
  (`getattr` and every residency row against the byte count, on the engine and the remount), with no
  `bypass_size` configuration. Regression `REVIEW_COMMIT_TESTS`
  (`test_size_of_a_file_created_on_the_source_inside_renamed_directory`: size while open, after
  `release`, after `flush`) fails on `67dfaee`, as does `test_create_on_the_source_inside_renamed_directory`
  through the size check. M8 (a served copy stat'ed at its logical path) fails both.
- **Verify:** `replay_gate.py commit mutants` (retuned in review cycle 1). It re-runs the whole of
  `test_replay.py`, so P1's tests must still pass. Dry-run green on the planning prototype cut back to
  this phase.
- **Touches:** `src/sosfs/engine/transfer.py`, `src/sosfs/engine/overlay.py`,
  `tests/engine/test_replay.py`, `AGENTS.md`, `PLAN.md`.

## Phase P3 — The model reaches single commits, scheduler polls and drains
**Satisfies:** R7, R8 · **Depends on:** P2
**Goal:** the stateful model commits one entry, advances one scheduler poll (and splits a poll into the
pump's scan and commit), fsyncs under the commit policy, evicts, drains deadline by deadline and checks
convergence there, holds a one-holder invariant over the source, and remounts at teardown, while
keeping every baseline rule, strategy, setting and teardown statement. Built as PLAN § 2.7.

- [x] `tests/engine/test_model.py`: the additions in PLAN § 2.7's table, and nothing else. Baseline
  rules, `parity`, `real`, `snapshot`, the seven strategies, `pytestmark`, the `TestCase` lines and
  the teardown statements stay verbatim; `max_examples = 1000`, `stateful_step_count = 50`.
  No `assume`, precondition, filter, skip, profile, exception handler, or assignment to anything but
  `self.<attribute>` (and `self.clock.now`). The class docstring says what the model now reaches.
  *Amended in build (2026-11-06):* a third composite, `rename_scan_unlink_commit` (commit, rename,
  the pump's scan, unlink, the stale snapshot committed), adds reach for the stale-row class under
  parity (PLAN § 2.7, dated line). Hypothesis seeds the fixed CI draw from a digest of the whole
  `OverlayMachine` source, docstrings included, so the required docstring redrew it, and the first
  `mutants` run went red: test_model passed under `CI=true` with M2 planted, while M2 failed it on 5
  of 6 random seeds. The teardown's remount compares paths, kinds and bytes, and the oracle side of
  `write_and_fsync` fsyncs.
- [x] `tests/engine/conftest.py` is not edited (`Clock`, `seed` and `build_spec` are an interface).
- [x] `verify/replay_gate.py` `MUTANTS`: re-anchor each mutant to the built engine's text if P1/P2 worded
  a guarded line differently from the prototype (the mode is red when an anchor is not found exactly
  once). Never drop a mutant or a regression it names; never weaken what it must fail.
  *Built (2026-11-06):* each of M1-M5's anchors occurs once in the built engine, as written; none
  needed re-anchoring.
- [x] If `model` or `mutants` goes red on this tree, the failure is a defect P1 or P2 left, or a reach
  the model lacks: fix the engine (amend P1/P2's design in PLAN with a dated line) or add reach under
  parity, never by narrowing the model.
  *Amended in build (2026-11-06):* the redrawn model then went red on the unmodified engine (the
  second recorded attempt) with `ENOTEMPTY` from a replayed `rmdir`: removing an entry cancels its
  queued rename and queues the removal last, so `mkdir /d2, create /d2/a.dat, flush, rename
  /d2/a.dat → /a.dat, rmdir /d2, unlink /a.dat, flush` ran the parent's `rmdir` while the child still
  sat in `d2` on the source. Baseline rules alone reach it, and `141aa86` fails it the same way: R5's
  class, left by P1. The fix is P1's design extended (PLAN § 2.3, dated line): `_execute_namespace_locked`
  replays, through `_replay_through` and skipping its own row, the queued rows of entries holding
  anything under the directory's source object before the `rmdir`, as `_purge_source` does. The
  regression `test_flush_removes_a_moved_out_child_before_its_old_parent` joins `REPLAY_TESTS`
  (twelve) and fails on `141aa86` with `ENOTEMPTY`; with the fix's guard disabled it fails here too,
  and so does test_model under `CI=true`. AGENTS.md § 5's `_replay_through` bullet, PLAN D1's row and
  the repo-root `PLAN.md` replay paragraph name the new caller in the same commit.
- [x] *Added in build (2026-11-06), foreman ruling:* `tests/engine/test_replay.py` gains
  `test_create_on_the_source_inside_renamed_directory` (one tier: `rename /data → /moved`,
  `create /moved/c.txt`, a write, then `flush`), walking first, asserting the source before the flush
  and ending in `assert_converged`; `verify/replay_gate.py` lists it in `COMMIT_TESTS`. P2's design made
  it pass and no test covered it. It fails on `141aa86` at the pre-flush source check (the baseline
  creates `/moved/c.txt` on the source, and its flush then raises `ENOTEMPTY`), shown by a run of
  `replay_gate.py commit` in this phase. No mutant M1-M5 turns it red: M1 is `_commit_hop`'s
  placement, and `create` places through its own line.
- **Verify:** `replay_gate.py model mutants`. Dry-run green on the planning prototype: the static
  comparison with `141aa86`, a pass under `CI=true`, a failure under `CI=true` on the `141aa86` engine
  with a falsifying example, and each of M1-M5 failing the regressions it names (M1-M3 also failing
  test_model under `CI=true`).
- **Touches:** `tests/engine/test_model.py`; with the foreman's ruling, `tests/engine/test_replay.py`
  and `verify/replay_gate.py` (`COMMIT_TESTS`); with the rmdir fix, `src/sosfs/engine/overlay.py`
  (`_execute_namespace_locked`, high-blast-radius), `AGENTS.md` § 5, `PLAN.md`, and `REPLAY_TESTS`.

## Phase P4 — Verify is green under the fixed CI seed and twenty random seeds
**Satisfies:** R9, R10 · **Depends on:** P3
**Goal:** the profile's `verify` passes under `CI=true` with the counts `AGENTS.md` states, and
test_model passes for `--hypothesis-seed` 1-20 from committed state with no example database.

- [x] Run `suite` once to measure, then record in `AGENTS.md`, in this phase's commit, every figure
  the run changes: the unit count in § Commands (`# N tests`), § Repository map (`N tests (N unit,
  10 integration)`) and § Verification (`N unit tests in …`); the `-m integration` note's deselected
  count (§ Conventions); the serial and `-n auto` timings. The prototype measured 466 unit tests, about
  33 s serial and no faster under `-n auto`; record what the build's run measures, from the run named
  in the commit body. P3 added one regression the prototype lacked, so expect one more.
  *Built (2026-11-06):* P3 added two (the `rmdir` regression and the foreman's create test), so the
  build measured 468 unit tests under `CI=true`: 28.8 s serial, 32.0 s under `-n auto`; `-m
  integration` 10 skipped, 468 deselected; 478 collected over 42 files (none added since `141aa86`).
- [x] Commit, then run the gate (`seeds` reads HEAD). A red seed is a defect: fix it in the engine
  under P1/P2's design, re-run both modes, and never narrow the model.
- [x] *Added in build (2026-11-06), foreman ruling:* `verify/replay_gate.py` `MUTANTS` gains M6, a
  create onto the source lands at the logical path (`create`'s `where = path if upstream is not None
  else self._place_parents(path, now)` becomes `where = path`), which must fail
  `test_create_on_the_source_inside_renamed_directory`; the model is not required to fail. Every named
  fix gets a fix-removal mutant, and none of M1-M5 reached the test P3 added. The anchor occurs once in
  `overlay.py`, and the mutant fails its test and nothing else in `test_replay.py` (1 failed, 22
  passed); `mutants` ran green with M1-M6 in this phase, beside P4's own modes. PLAN § 2.8's mutant
  table names it.
- [x] *Review cycle 1 (2026-11-06):* the six regressions bring the unit count to 474. Measured on the
  remediation tree under `CI=true`, back to back: 474 passed in 28.5 s serial, 474 in 26.9 s under `-n
  auto`; `-m integration` 10 skipped, 474 deselected; 484 collected over 42 files. `AGENTS.md`'s four
  figures move in the same commit. Commit, then `seeds` (it reads HEAD).
- **Verify:** `replay_gate.py suite seeds`. Dry-run green on the planning prototype carrying this
  phase's `AGENTS.md` counts.
- **Touches:** `AGENTS.md`; with the foreman's ruling, `verify/replay_gate.py` (`MUTANTS`) and
  `PLAN.md` § 2.8.

## Phase P5 — Records match what ships, and CI is read from a run
**Satisfies:** R11, R12 · **Depends on:** P4
**Goal:** `PLAN.md` and `AGENTS.md` say truthfully which divergence classes are closed; the deferred
siblings have their own seed (`issues/source-path-truncate-and-stat.md`) and `ROADMAP.md` entry; the four CI jobs conclude `success` on the pushed
tree, read from the run record. R11's evidence is a saved run record handed over by the foreman, so
the orchestrator grades it, not the blind reviewer.

This phase waits on CI, so it is **two runs** (code-foreman → "External waits split the run").

**Run A — records, check, push, stop.**
- [x] `issues/source-path-truncate-and-stat.md` (new, from the KB's ISSUE template, `status: unshaped`,
  `kind: fix`, `lane: public`): the `truncate`-without-a-handle `ENOENT` and the live-stat/`_record`
  wrong size under a queued rename, plus correcting sizes existing state databases already hold wrong
  (GOAL Q1, Q4's recommendation, Q5). Carry the retiring seed's reproductions and evidence verbatim
  where they still hold: `FileNotFoundError` on `truncate('/big2.bin', 4)`, `getattr().size == 1000`
  for a 4-byte object, the `overlay.py:` anchors for `truncate`, `_record` and the live stat
  (re-confirmed on this tree), and the link to `issues/audit-command.md` for the size correction.
- [x] `ROADMAP.md`: an entry for `issues/source-path-truncate-and-stat.md`, ordered where this cycle's
  entry sits. Leave this cycle's own entry and seed in place: `code terrace roadmap` retires both
  after landing.
- [x] `issues/source-commit-staged-publish.md` (new, from the KB's ISSUE template, `status: unshaped`,
  `kind: feature`, `lane: public`) and its `ROADMAP.md` entry: the follow-up to PLAN D4 that stages a
  source-bound data commit's bytes outside the source-namespace mutex and publishes them inside it, so
  a FUSE `create`/`mkdir`/`rename` or an `fsync` under the commit policy no longer waits for a whole
  copy. Carry D4's rejected-alternative reasoning (a `Tier` protocol change in `posix.py` and `s3.py`;
  S3's temp-key-plus-copy publish) as the starting problem statement.
- [x] `issues/freethreaded-concurrency-stress.md`: a sketched R for replay against a concurrent data
  commit under threads (the source mutex, PLAN § 2.5), and the descendant-lock gap research 04 § 1
  names; its `ROADMAP.md` entry exists and stays.
- [x] `AGENTS.md` § Known open violations: the § 5 row names only what stays open (`truncate`
  without a handle and the live stat) and the seed `issues/source-path-truncate-and-stat.md`, and no
  longer this cycle's seed. *Amended in P2 (2026-11-06):* the row's text was narrowed there; this
  item swaps its seed column. § Commands' CI paragraph: each of the four jobs is expected to conclude
  `success`; no test failure is tolerated.
- [x] `PLAN.md` → Write-back → Deferred-op replay: current-target replay and vacate-through, in the
  repository plan's voice. *Amended in P2 (2026-11-06):* already done there, under the same-commit
  rule; this item re-reads it against the shipped code. § Risks' "Write-back namespace replay under path mutation" entry: no
  longer *resolved, Phase 8*; it says what this cycle closed (the data-commit orphan, the replay
  family, stale scheduler rows) and names `issues/source-path-truncate-and-stat.md` for what stays open.
- [x] *Added in build (2026-11-06), foreman ruling:* `verify/replay_gate.py` `MUTANTS` gains M7, an
  `rmdir` row runs before its moved-out children (`_execute_namespace_locked`'s guard `if entry is not
  None and row.op == 'rmdir' and entry.source_path is not None:` becomes `if False:`, which disables the
  `_replay_through` call P3 added), naming `test_flush_removes_a_moved_out_child_before_its_old_parent`
  and the model. Every named fix gets a fix-removal mutant, and none of M1-M6 removed P3's `rmdir`
  pre-replay. The anchor occurs once in `overlay.py`; the mutant fails its regression and nothing else
  in `test_replay.py` (1 failed, 22 passed) and fails test_model under `CI=true` with shrinking off, as
  P3 reported for the disabled guard, so its model flag is set. `mutants` ran green with M1-M7 in this
  run before the commit, and Run A re-runs it on the committed tree. PLAN § 2.8's mutant table names
  it.
- [x] Commit (phase stays `in_progress`; the body says "Run A"), run `replay_gate.py prepush` on the
  committed tree, and push `fix/deferred-rename-source-orphan` to `origin` under the standing
  cycle-branch approval. Stop and hand back the pushed SHA.
  *Built (2026-11-06):* committed `c737308`; `prepush` green on it; pushed, and
  `origin/fix/deferred-rename-source-orphan` is `c737308`.

**Run B — read, grade, close.**
- [x] The foreman saves `gh run view <id> --json databaseId,headSha,headBranch,event,status,conclusion,jobs`
  for the push run and the four job logs (`<job id>.log`); the builder has no `gh`. Run the gate with
  `--run-json=<file> --log-dir=<dir>`. `ci` takes the commit from `origin/fix/deferred-rename-source-orphan`
  unless `--sha=` names another; commits after it may touch only `spec/deferred-rename-source-orphan/`.
  No test is tolerated: every job `success`, Linux passed equals `AGENTS.md`'s count with no skip,
  macOS passed plus skipped equals Linux with every skip under `tests/fuse` naming libfuse.
  *Read (2026-11-06):* push run 42294832110 for `c737308` completed, all four jobs `success`. Both
  Linux jobs: `Assert libfuse loads` `success`, 468 passed, no skip, equal to `AGENTS.md`'s 468.
  macOS: 424 passed + 44 skipped = 468, every skip under `tests/fuse` naming libfuse. `ci` green.
- [x] Deferral ledger: walk every earlier phase body for deferral wording and confirm each names an
  `issues/` seed with a `ROADMAP.md` entry (`ledger` does the mechanical half; prose and list items,
  never headings). This plan's deferrals: the siblings and the already-wrong sizes
  (`issues/source-path-truncate-and-stat.md`), and the thread-level test of the mutex
  (`issues/freethreaded-concurrency-stress.md`).
  *Walked (2026-11-06):* `ledger` green, reaching all three seeds, each with a `ROADMAP.md` entry
  (`issues/source-path-truncate-and-stat.md`, `issues/source-commit-staged-publish.md`,
  `issues/freethreaded-concurrency-stress.md`). By hand, P1-P4's bodies hold no other unseeded
  deferral. P2's sentence that `truncate` and the live stat stay under this cycle's seed "until P5
  names the new one" is settled here, by the § 5 row naming `issues/source-path-truncate-and-stat.md`.
  P1's note that `mutants` gives the `DESIGN_TESTS` entry its teeth was met in P3. P3's and P4's
  gaps (no mutant reaching the create test, no mutant removing the `rmdir` pre-replay) were closed
  in-cycle by M6 and M7.
**Review cycle 1 remediation (2026-11-06).**
- [x] F5: `issues/audit-command.md` gains a dated note recording the sizes state databases already hold
  wrong, beside `issues/source-path-truncate-and-stat.md` R4 (GOAL's second non-goal names both).
- [x] F4: the coverage observation is deferred to `issues/model-commit-route-reach.md` (new, from the
  KB's ISSUE template, `status: unshaped`, `kind: test`, `lane: public`) with its `ROADMAP.md` entry,
  placed after the truncate seed's; `mode_ledger`'s required list names it.
- [x] Records for F2 and F3: `issues/source-path-truncate-and-stat.md` and its `ROADMAP.md` entry
  (this cycle's own, corrected in place) say the live stat is closed and `truncate` without a handle
  stays open; `issues/freethreaded-concurrency-stress.md` gains a dated block and R9;
  `AGENTS.md` § Known open violations narrows the § 5 row and adds the removal-vs-`mkdir` row; the
  repo-root `PLAN.md` replay paragraph and § Risks entry say what closed and what stays open.
- [x] Run A: commit, then `seeds`, `ledger`, `records` and `prepush` on the committed tree; push under
  the standing cycle-branch approval; hand back the pushed SHA with the phase `in_progress`.
  *Built (2026-11-06):* committed `2c4c3ac`; pushed, and `origin/fix/deferred-rename-source-orphan`
  is `2c4c3ac`.
- [x] Run B: the foreman saves the push run's record and job logs; `ci ledger records` on them; close
  the phase and set the cycle `in_review`.
  *Read (2026-11-06):* push run 42308408083 for `2c4c3ac` completed, all four jobs `success`. Both
  Linux jobs: `Assert libfuse loads` `success`, 474 passed, no skip, equal to `AGENTS.md`'s 474.
  macOS: 430 passed + 44 skipped = 474, every skip under `tests/fuse` naming libfuse. `ci ledger
  records` green; `ledger` reached all four seeds, `issues/model-commit-route-reach.md` included.

**Review cycle 2 remediation (2026-11-06).** Review 2 requested changes on F6 (LOW): seeds in the
branch diff cite `file:line` locations that no longer hold the cited code. P5 is reopened (its
records are the touched files); no other phase is, because nothing outside `issues/` and the
repo-root `PLAN.md` changes, and `src/`, `tests/`, `ci.yml`, `GOAL.md` and `test_model.py` are not
edited. The gate is unchanged: the class was checked by eye with a throwaway checker (not committed,
per the foreman's brief) that printed every `path:line` in the touched files at `HEAD`, and `ci`
needs a fresh run because `issues/` and `PLAN.md` lie outside `spec/`. This is again two runs.
- [x] F6, as a class: every `file:line` cite in the six seeds `git diff --name-only origin/main...HEAD
  -- issues/` names, plus `AGENTS.md`, `PLAN.md` and `ROADMAP.md` (no `file:line` cites there), points
  at the code it names on this tree or is pinned to a named commit that holds it. The branch's own
  text is corrected in place: `issues/freethreaded-concurrency-stress.md`'s descendant-lock cite
  (`overlay.py:421` to `:425`) and `issues/source-commit-staged-publish.md`'s `_commit_hop` cite
  (`:819-824` to `:835-840`). Landed text keeps its historical cites, and a dated line beneath each
  passage pins them to the commit that holds them and gives the line on this tree (code-build Step
  2.3): `issues/audit-command.md` (seven cites, at `141aa86`), `issues/freethreaded-concurrency-stress.md`
  (the handle-field item's seven `overlay.py` cites at `141aa86`; three `ci.yml` cites at `6a046b4`,
  stale on `main` since `50a11bd`), and `issues/deferred-rename-source-orphan.md` (one line pinning
  all of the problem statement's cites to `141aa86`). `issues/source-path-truncate-and-stat.md` held
  already; `issues/model-commit-route-reach.md` cites none.
- [x] Attribution: `issues/model-commit-route-reach.md` said the review gate ruled that the
  remediation leaves `test_model.py` alone. That was the foreman's instruction in the remediation
  brief; the review-gate rulings were to fix F2 and F3 and to sign off after the re-review. Corrected
  there. No other text in the branch diff, `issues/`, `AGENTS.md`, `PLAN.md` or this cycle's
  `PLAN.md` and `TECH.md` attributes it to the gate (`REVIEW.md` quotes the claim as a record).
- [x] Observation folded in: the repo-root `PLAN.md` § Risks entry says the source mutex spans a whole
  copy and that `_record` takes it for a copy served off the source, and what that delays, as
  `AGENTS.md` § 10 does.
- [x] Run A: commit, then `ledger records` and `prepush` on the committed tree; push under the
  standing cycle-branch approval; hand back the pushed SHA with the phase `in_progress`.
  *Built (2026-11-06):* committed `da8fded`; pushed, and `origin/fix/deferred-rename-source-orphan`
  is `da8fded`.
- [x] Run B: the foreman saves the push run's record and job logs; `ci ledger records` on them; close
  the phase and set the cycle `in_review`.
  *Read (2026-11-06):* push run 42313840651 for `da8fded` completed, all four jobs `success`. Both
  Linux jobs: `Assert libfuse loads` `success`, 474 passed, no skip, equal to `AGENTS.md`'s 474.
  macOS: 430 passed + 44 skipped = 474, every skip under `tests/fuse` naming libfuse. `ci.yml` at
  `da8fded` is `main`'s. `ci ledger records` green; `ledger` reached all four seeds.
- **Verify:** `replay_gate.py ci ledger records` (Run B). `records` and `ledger` dry-ran green on the
  planning prototype carrying this phase's records; `ci` dry-ran against fixtures built from the
  previous cycle's saved run record (PLAN § 6).
- **Touches:** `issues/source-path-truncate-and-stat.md`, `issues/source-commit-staged-publish.md`,
  `issues/freethreaded-concurrency-stress.md`, `ROADMAP.md`, `AGENTS.md`, `PLAN.md`; with the foreman's
  ruling, `verify/replay_gate.py` (`MUTANTS`) and this cycle's `PLAN.md` § 2.8.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
