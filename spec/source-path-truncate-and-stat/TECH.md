---
slug: source-path-truncate-and-stat
title: truncate without a handle finds a source-served file during a queued rename
kind: fix
appetite: small
status: done
branch: fix/source-path-truncate-and-stat
base: main
current_phase: done
last_updated: '2026-11-08'
phases:
- id: P1
  name: A handle-less truncate cuts a source-served file where the source holds it
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  attempts: 1
  verify: uv run --no-project --python 3.15 python -I spec/source-path-truncate-and-stat/verify/truncate_gate.py regressions mutants records suite
- id: P2
  name: Verify is green under the fixed CI seed and twenty random seeds
  status: done
  satisfies:
  - R5
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/source-path-truncate-and-stat/verify/truncate_gate.py suite seeds
- id: P3
  name: CI is read from the run record, and the ledger closes
  status: done
  satisfies:
  - R7
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  attempts: 3
  verify: uv run --no-project --python 3.15 python -I spec/source-path-truncate-and-stat/verify/truncate_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs
review:
  last_reviewed_commit: aafdd44592a6de8350268f59bb009f61c6101164
  verdict: changes-requested
  blocked_reason: 'review c3 (final): F1 records overclaim (symlink last component only; F6 counter write); F3 survivors M11, M27; escalated to Geoffrey'
  cycle: 3
---

# TECH.md — truncate without a handle finds a source-served file during a queued rename

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/source-path-truncate-and-stat/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs.

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
  and every item the previous cycle's last phase carried for a project-level obligation. The previous
  cycle (`deferred-rename-source-orphan`) carried the ledger and the split CI read; P3 carries both.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

**This cycle in particular.**

- **Coupled core and human gate.** P1 edits `src/sosfs/engine/overlay.py` (the profile's coupled
  core) in high-blast-radius functions (`truncate`, `_stat_held`, and the new `_held_path`), and
  `tests/engine/test_replay.py`. Geoffrey signs off on the `truncate` change at review (GOAL Q4). No
  phase edits any other file under `src/`, `tests/engine/test_model.py`, or either `conftest.py`. A
  phase that finds it must is a reason to stop and re-read PLAN, and a `SCHEMA_VERSION` bump is a
  stop-and-ask.
- **§ Invariants text moves with the behaviour.** PLAN § 2.3's `AGENTS.md` and `PLAN.md` edits land in
  P1, the phase whose code makes them true, and each must be true on P1's own tree. They are graded at
  review as a revision of the standard, on their merits.
- **No spec ids in code.** Comments in `src/` and `tests/` name constraints, never R-IDs or phase ids.
- **Where the gates run.** The development Mac, inside the agent sandbox, with these settings in every
  Bash call that runs `uv`, as separate statements:
  - `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and `UV_TOOL_DIR` set to per-session directories under
    the session scratchpad (the profile denies the shared cache). The Python install dir must already
    hold a 3.15 interpreter, because the sandbox blocks Python downloads.
  - `pypi.org` and `files.pythonhosted.org` in `allowed_domains`. `tools/lint.sh` runs an unpinned
    `uvx ruff`, `suite` runs `uv sync --locked`, and `seeds` builds two scratch environments.
  - Gate scratch, JUnit files and a copy of each run's output go under the worktree's gitignored
    `.local/truncate-gate/`. A session scratchpad does not survive a reboot. Nothing is deleted.
    `seeds` sets `UV_PROJECT_ENVIRONMENT` per scratch tree, so no stray `.venv` lands elsewhere.
  - The shell is zsh. `rm` is blocked (move scratch aside instead). Quote globs and separator strings,
    and never name a variable `path`.
  - Running a gate outside the sandbox is approved for this effort if the sandbox blocks it. If that
    is refused, stop and report rather than work around it.
  - Measured on the planning prototype (development Mac, 2026-11-06): `regressions`, `mutants` and
    `records` together 10 s; P1's whole verify 41 s (the suite's pytest 29 s); `seeds` 4 min 46 s on
    two workers. Run in the foreground with long timeouts (`seeds` near 5 min needs the 10-minute
    cap). Never hand back while a gate is still running.
- **Hypothesis state.** `.hypothesis/` in the worktree is gitignored and replays found failures. `seeds`
  never reads it: each seed runs in a scratch tree from `git archive HEAD`, with any `.hypothesis/`
  moved aside first, never deleted. `CI=true` gives Hypothesis's fixed seed.

---

## Phase P1 — A handle-less truncate cuts a source-served file where the source holds it
**Satisfies:** R1, R2, R3, R4 · **Depends on:** —
**Goal:** `truncate` without a handle succeeds on a source-served file while its rename or an
ancestor's is queued. The overlay reports the new length, and the queue drains to a source equal to
the overlay view. Last cycle's size behaviour is pinned through a `bypass_size` stage, and the
records say what is closed and who owns what is not. R4's evidence (the audit seed note and the
open-violation rows) is committed text outside `spec/`, so the blind reviewer can grade it.

- [x] `src/sosfs/engine/overlay.py`: PLAN § 2.1, as written there. `_held_path(entry, tier)` holds the
  expression `_stat_held` holds today, and `_stat_held` calls it. In `truncate`'s no-handle branch,
  every statement up to and including the `mark_dirty` stays byte-identical (§ 1 ordering). Then come
  `on_source`, the source lock when `on_source`, the entry looked up again with `catalog.get(path)`,
  which raises `FileNotFoundError` when it is missing or its id differs, and the open at `_held_path`.
  The catalog writes keep the baseline's text, keyed by `path`, and the counter guard becomes
  `not on_source`. Never look the entry up by id alone; review finding 1 is in PLAN § 4.1. The
  docstring says why: the replay window, and an entry taken off the path. No comment names a spec id.
- [x] `tests/engine/test_replay.py`: the sixteen tests of PLAN § 2.2, with exactly those names and
  assertions, **appended after the last existing test** so every `test_replay.py:N` cite in `issues/`
  keeps holding. Add the helpers `source_served`, `assert_cut`, `Interleaved` and `concurrent_removal`.
  `Interleaved` sets `ran` only after its action has succeeded, and re-raises that action's exception.
  Import `threading` and `raises`, and add a module-docstring bullet for the new class. Each test walks
  first (through `source_served`) and ends in `assert_converged(engine)`. No sleeps, no timeouts.
- [x] `AGENTS.md`, per PLAN § 2.3: the § 5 bullet, the § 10 bullet (taker and delay), `_held_path` in
  both high-blast-radius tables (the long table's "why" covers cutting), and the § Known open
  violations rows. The § 5 row becomes the old-sizes row owned by `issues/audit-command.md`. A new
  `| 5, 10 |` row names the `open`/`_serve` window and `issues/source-served-open-race.md` (the
  deferral this plan filed, with its `ROADMAP.md` entry). Counts move from 474 to 490 at all four
  places, the repository map's total to 500, and the ~28 s figure is re-measured.
- [x] `PLAN.md` (repo root) § Risks, the namespace-replay entry: the handle-less `truncate` is a
  mutex taker and closed. "Still open" names `issues/audit-command.md` (the old sizes) and
  `issues/source-served-open-race.md` (the window), and no longer this cycle's seed. The injection
  sentence admits helper-thread injection.
- [x] `issues/audit-command.md`: append the dated note of PLAN § 2.3 before the "orphans already on a
  source" item. It carries `entry.size`, the source residency's `size`, source-held copies only, and
  `fe26962`. It says the residency heals on `release` and that `entry.size` does not heal on a read,
  an `fsync` with no write, `flush()`, a remount, or a rename plus `flush()`. It says the staged-tier
  quota counters are unaffected, and that the earlier note overstated that. It holds no `file:line`
  cite and edits no existing line. Leave this cycle's own seed and `ROADMAP.md` entry in place: `code
  terrace roadmap` retires both after landing.
- [x] If a guarded line's text differs from the gate's `MUTANTS` anchor, re-anchor it in
  `verify/truncate_gate.py` in this commit, keeping the decision it attacks and the tests it names.
- [x] `issues/model-commit-route-reach.md`: append a dated line beneath the bullet that says this
  cycle's seed decides the model question, stating the decision (plan-gate rulings, 2026-11-06).
- **Verify:** `truncate_gate.py regressions mutants records suite`.
- **Touches:** `src/sosfs/engine/overlay.py`, `tests/engine/test_replay.py`, `AGENTS.md`, `PLAN.md`,
  `issues/audit-command.md`, `issues/model-commit-route-reach.md`.
- **Amendment (build, 2026-11-06):** the two lock-probe tests share a fifth helper, `lock_probe`,
  rather than each defining its own helper-thread probe; the tests' names and assertions are as
  planned. The plan-gate ruling's seed line is added as a checklist item above.

**Remediation, review cycle 1 (amendment, 2026-11-07).** `REVIEW.md` F1–F3 and Geoffrey's rulings at
the review gate reopen P1, P2 and P3 (remediation mode). The three findings sit in the code and records
P1 built, so P1 carries them; P2 and P3 re-run because `src/` changes. The run is split as P3 was:
this run builds, gates, pushes and stops with P3 `in_progress`.

- [x] **F2** (`src/sosfs/engine/overlay.py`, `truncate`'s no-handle branch). Everything the call
  decided before the source-lock wait is decided again inside it: after the entry is looked up again
  and its id checked, the serving tier is the entry's fastest copy as it stands under the lock, and the
  upstream, the clock reading, the dirty mark and the counter guard follow from that tier. A copy a
  faster tier took during the wait (through a renamed ancestor, which the path lock does not cover) is
  marked dirty and cut, and the source is left to the write-back. The class, scoped by evaluating
  "decided before the lock, used after it" over the branch: the serving tier, the upstream, the
  dirty-mark target, `now` and the counter guard (`on_source`); the entry itself was already re-read.
  The staged branch takes no lock, so its decisions are made and used under the path lock with no wait.
  This moves the dirty mark below the identity check; on a staged copy it still commits before the cut
  (§ 1), which the staged control and mutant M11 pin. No new function, so neither high-blast-radius
  table changes (`truncate` is in both).
- [x] **F2 regression** `test_truncate_without_a_handle_cuts_a_faster_copy_made_during_the_wait`,
  the review's probe as an `Interleaved` test: the stage copy is dirty at 4 bytes, the source a clean,
  true 10, a fresh read and a read after evicting the stage copy both give 4 bytes, and the tree
  converges. It fails on `aec3676`'s engine at the residency assertion (clean 4 on the source, clean
  10 on the stage) and on the GOAL baseline at `ran`. Mutant M12 removes the re-decision.
- [x] **F1** records. `AGENTS.md` § 5 no longer says a file that took the entry's id is never cut; it
  says what the id check rejects, that such a file carried onto the path is cut, and points at a new
  `| 5, 10 |` § Known open violations row naming `issues/source-served-open-race.md`. That seed (filed
  by this cycle, so corrected in place) says the gap reproduces by injection and is silent, records the
  F2 window as reproduced and fixed, and gains a sketch R4 for the gap. The `truncate` docstring, the
  test module's bullet, the reused-id test's docstring and comment (it proves the path check, not the
  id check), and the repo `PLAN.md` § Risks (the re-decided copy; the gap under "Still open") follow.
  The gap stays accepted (plan-gate ruling 2). Sweep: `never cut`, `argued and not reproduced`,
  `reused`, `identity` over `AGENTS.md`, `PLAN.md`, `issues/`, `src/`, `tests/`; `spec/` records are
  frozen and left alone.
- [x] **F3** mutant sweep over every decision in the no-handle branch and `_held_path`. New pins:
  `test_truncate_without_a_handle_leaves_another_file_carried_onto_the_path_alone` (a different file
  a directory rename carries onto the path; only the id comparison rejects it, mutant M13) and
  `test_truncate_without_a_handle_of_a_missing_path_or_a_directory` (M15, M16). Both pass on
  `aec3676`'s engine, as a pin of correct code must; their teeth are the mutants, and M13 and M16
  survive the whole `-m unit` suite on `aec3676`. Gate mutants M12–M20 join M1–M11 (M10 and M11
  re-anchored). Two mutants are equivalent and recorded in the gate's `EQUIVALENT`: E1 drops the
  counter guard's `previous is not None` (the serving tier's row was just read as the fastest copy
  under the path lock, so it exists); E2 drops `_held_path`'s `source_path is not None` (a source copy
  records `source_path` in the transaction that records it). Both pass the whole `-m unit` suite under
  `CI=true`.
- [x] The gate (`verify/truncate_gate.py`): `regressions` also runs the new tests on `aec3676`'s
  engine (the F2 regression fails for its cause, every other named test passes); `records` asserts
  § 5 claims no "never cut", one `| 5, 10 |` row names the gap and its seed, and the seed's gap
  paragraph says it reproduces by injection.
- [x] Counts measured: 493 unit (four places), 503 total, 155 engine tests, ~30 s serial (30.6 s).
- **Touches (remediation):** `src/sosfs/engine/overlay.py`, `tests/engine/test_replay.py`,
  `AGENTS.md`, `PLAN.md`, `issues/source-served-open-race.md`,
  `spec/source-path-truncate-and-stat/verify/truncate_gate.py`, this file.

**Remediation, review cycle 2 (amendment, 2026-11-07).** `REVIEW.md` cycle 2 (F4, F5, F6, F1 reopened, F3
reopened) and Geoffrey's escalation ruling reopen P1, P2 and P3 again. The ruling: a handle-less `truncate`
always takes the source lock, then decides once under it, with its path lock, every value the cut and its
recording use (the lock held, the kind, the serving copy, the held place, the write keys); it refuses
(`ENOENT`) when any of them changed during the wait and never follows a symlink. F4 and F5 are HIGH; F6 goes
out of this cycle to a seed; F1 and F3 as written. P1 carries the code, tests and records; P2 re-runs
`suite` and `seeds`; P3 runs Run A only (this run pushes and stops, P3 `in_progress`).

- [x] **F4, F5, F2's open domain** (`src/sosfs/engine/overlay.py`, `truncate`'s no-handle branch). One
  decision point: path lock, `_ensure` and the kind check as before (the caller-observable state; a
  directory or symlink still raises `EISDIR`, unchanged), then the source lock unconditionally, then, once:
  the entry at `path` (missing, another id, or not a file: `ENOENT`), the serving copy (one `_serve` call,
  which may promote), the held place (`_held_path`), the upstream and the dirty mark, the cut, and the
  recording keyed by `path`, all before the lock is released. The open passes `O_NOFOLLOW`. Nothing is
  decided before the wait except the entry the comparison uses. Lock order stays path → source → SQLite
  (§ 10): `_serve`'s `transfer.copy` and `mark_dirty` open SQLite transactions inside the source lock, and
  nothing inside takes a path lock or calls `_ensure`. Class scoping: the predicate "a value the cut or its
  recording uses that was decided before the source-lock wait, or a mover the source lock does not exclude
  between the decision and the use", evaluated over the branch. Before the wait: the lock choice, the
  serving copy, the kind (all now decided under the lock). Between decision and use: replays and commits to
  the source take the source lock (excluded); promotion, eviction and a rename's catalog and stage moves
  take only their own path locks. A rename that starts during the cut waits at its `_vacate` (it takes the
  source lock first); one already past `_vacate` is not excluded: F6's residue, below.
- [x] **Blocking-call judgment (§ 10).** A stage-served cut now holds the source lock across a stage
  `open_direct` and `ftruncate`, and a cut that promotes holds it across the whole-file promotion copy (as
  large as the stage admits). § 10's finite-timeout rule binds `StateThread` states; the source lock is
  taken without a timeout everywhere, and a state that replays (the scheduler's `commit_row`, a drain)
  already waits behind a commit to the source, which holds it for a whole copy. The cut adds a hold of the
  same kind, shorter than a source commit unless it promotes. Judged within § 10's existing contract, and
  recorded in § 10's text (every handle-less `truncate` waits behind a source commit; one that promotes
  delays the same calls for that copy) and in the spec PLAN § Risks amendment.
- [x] **F6** (deferred out of this cycle, Geoffrey's ruling). Checked first whether the restructure closes it: the
  review's interleaving (a rename started as the stage `open_direct` returns) now waits at `_vacate` until
  the cut is recorded, and converges (probe, `.local/rem2/probe_f6.log`). A rename already past `_vacate`
  when the cut takes the source lock (simulated in the cut's own thread; the lock is re-entrant) still
  leaves `getattr` 5 and fails `assert_converged`. Narrowed, not closed, so the seed is written:
  `issues/truncate-recording-after-ancestor-rename.md`, its `ROADMAP.md` entry, and a `| 5, 10 |` § Known
  open violations row. `test_truncate_without_a_handle_of_a_staged_copy_holds_the_source_lock` pins the
  narrowing (both locks held at the stage open).
- [x] **Tests** (appended): staged-holds-lock (F4, F6's narrowing; also the path lock), flip-to-source (F4's
  round trip: the cut promotes again and cuts the stage copy with the lock held, never the source around
  it), wrong-object (F4's `wrongobj`, moved into the wait: refused, the other file whole, converged),
  symlink-takes-id and directory-takes-id (F5: `ENOENT`, never `AttributeError`, the link's target whole),
  of-a-symlink (the review's M03: `EISDIR`, target whole), never-follows-a-link (`O_NOFOLLOW`: `ELOOP`).
  The faster-copy test asserts the stage counters (the review's M20). Every interleaving is injected while
  the cut waits for the source lock (`Interleaved` runs its helper before the real acquire), never inside
  it. `lock_probe` captures the engine's lock when made, so a test can stand `Interleaved` in afterwards. The
  R1 sequences are untouched and pass uncontended.
- [x] **F1 records.** The `truncate` docstring; `AGENTS.md` § 5's reading bullet and § 10's mutex bullet;
  the identity-gap row (file only; a symlink or directory taking the id is refused) and the new F6 row;
  `issues/source-served-open-race.md` (this cycle's own seed, corrected in place); root `PLAN.md` § Risks;
  the spec PLAN § Risks (a dated amendment, the plan being a record); the test module docstring. Root
  `PLAN.md`: the brief asked for a dated append-only correction, but the cited text (`PLAN.md:580` at
  `9d5b6a7`) is this branch's own (`git diff origin/main -- PLAN.md` shows `6e42330` and `bd70c89` added it),
  not landed, so it is corrected in place (code-build Step 2.3). Sweep: `decides again`, `decided again`,
  `found again`, `everything it decided`, `closes it there`, `cut one`, `never cut` over `AGENTS.md`,
  `PLAN.md`, `ROADMAP.md`, `issues/`, `src/`, `tests/`; `spec/` records left alone. Both high-blast-radius
  tables unchanged (no function added or moved; `truncate`, `_serve`, `_held_path` already listed). No
  `file:line` cite added.
- [x] **F3** sweep: `remediation/cycle2-mutant-sweep.md`. Gate mutants re-anchored on the restructured
  cut, M21–M29 added (M1–M29 all killed); S1 killed on the full pass; E1 re-judged (differs only inside
  F6's recorded window) and E2 unchanged, both passing the full `-m unit` suite under `CI=true`. No
  survivor.
- [x] **Gate retune** (`verify/truncate_gate.py`, not frozen, as in cycle 1; the `verify:` strings are
  unchanged). `regressions` adds the cycle-2 tests: each fails on the baseline engine and on review cycle 2's
  engine (`ea38083`) for its named cause, the two pins pass on both; `records` asserts the F6 row and seed
  exist and that no live record repeats a claim review cycle 2 found false (the pattern, not the lines);
  `mutants` as above.
- [x] Counts measured: 500 unit (four places), 510 total, 162 engine tests (`--collect-only`).
- Attempt recorded (2026-11-07): the first throwaway gate run was red on the run's own checker, mutant M4
  (anchored on the copy found before the wait, which promotes nowhere, so the staged-holds test passed under
  it). Re-anchored on the reviewed shape (the lock chosen from a `_serve` before the wait), every step re-run.
- **Touches (remediation, cycle 2):** `src/sosfs/engine/overlay.py`, `tests/engine/test_replay.py`,
  `AGENTS.md`, `PLAN.md`, `ROADMAP.md`, `issues/source-served-open-race.md`,
  `issues/truncate-recording-after-ancestor-rename.md`, `spec/source-path-truncate-and-stat/PLAN.md`,
  `spec/source-path-truncate-and-stat/verify/truncate_gate.py`,
  `spec/source-path-truncate-and-stat/remediation/cycle2-mutant-sweep.md`, this file.

**Remediation, review cycle 3 (amendment, 2026-11-08).** `REVIEW.md` cycle 3 (the final review) ended
changes-requested on F1 (records) and F3 (two surviving mutants) and stopped for Geoffrey. His ruling closes
the cycle with a records-and-tests fix and no further review: F1 is MEDIUM; "never follows a symlink" in his
property means the last path component, so the records say so and the code does not change; the final engine
diff in `src/sosfs/engine/overlay.py` is signed off, so `src/` changes here only in the `truncate` docstring,
which is one of F1's records. P1 carries the records and the tests, P2 re-runs `suite` and `seeds`, and P3
(CI) re-runs in Run B; this run builds, gates, pushes and stops with P3 as it stands.

- [x] **F1(a)**, the symlink claim: every record states that the cut's open does not follow a symbolic link
  at the file itself, the last path component (`O_NOFOLLOW`). `AGENTS.md` § 5's reading bullet, the
  `truncate` docstring, the test module's docstring bullet, root `PLAN.md` § Risks and
  `issues/source-served-open-race.md`'s identity-gap paragraph. Sweep: `follows a link`, `follows a symbolic
  link`, `through a symbolic link`, `never follow` over `AGENTS.md`, `PLAN.md`, `ROADMAP.md`, `issues/`,
  `src/`, `tests/`; the one other hit (`tests/tier/test_posix.py`, a `stat` of a link reads as a link) is a
  different claim, true as written. The test name `…never_follows_a_link_at_the_held_place` already scopes
  itself to the held place and is named by the frozen gate, so it stays.
- [x] **F1(b)**, the F6 window's record: every copy names the three writes the late recording makes and
  their effect. The two catalog writes, keyed by path, match no row; the stage-counter write
  (`adjust_usage`), keyed by tier, lands, so the counter moves while the residency does not, and eviction
  leaves it off by the cut's size change, negative after a shrinking cut. That is a § 7 effect, so the
  § Known open violations row is tagged `5, 7, 10`. The row and the seed name the earlier landings
  (`_serve` raising `EngineError`; a promotion raising `LookupError` with its `promote` intent unresolved
  and the stage bytes under no residency) and correct the healing claim: a read-only open and release heals
  the residency `size` (and moves the counter by the change a second time); `entry.size` heals only on
  another write or `truncate`. Measured by probes in the ignored `.local/rem3/`: the counter `(2, 1)` then
  `(-3, 0)` after a flush and eviction; with a read-only open and release first, the residency
  `('dirty', 2)`, the counter `(-1, 1)`, then `(-3, 0)`, `getattr` 5; `_serve`'s `EngineError` and the
  promotion's `LookupError` with one `promote` intent pending and no stage residency. Sites: `AGENTS.md`
  § 5's reading bullet and the row, the `truncate` docstring, root `PLAN.md` § Risks, `ROADMAP.md`'s entry,
  `issues/truncate-recording-after-ancestor-rename.md` (problem, evidence, and the sketch's R1 and R2) and
  `issues/source-served-open-race.md`. Sweep: `matches nothing`, `matching nothing`, `match no row`, `old
  figure`, `old size`, `short of another`, `already passed its`, `already past its` over the same paths.
  Every site is this branch's own text (none is on `origin/main`), so each is corrected in place.
- [x] **F3**: `test_truncate_without_a_handle_promotes_from_where_a_replay_during_the_wait_moved_the_file`
  (the file's own queued rename replays during the wait, then the cut promotes; kills the review's M11) and
  `test_truncate_without_a_handle_counts_the_stage_copy_it_cuts` (an uncontended cut of a dirty 8-byte stage
  copy over a 5-byte source copy, the stage counter `(2, 1)` then `(0, 0)` after eviction; kills the
  review's M27), appended to `tests/engine/test_replay.py`, each ending in `assert_converged`. Each mutant
  survives the 500-test suite and fails its pin; the commands and outputs are in
  `remediation/cycle3-pinning-tests.md`. The gate (`verify/`) is frozen and unchanged; the pins are not in
  its `MUTANTS`, so that note is their record. `tests/engine/test_model.py` is unchanged.
- [x] Counts measured (`--collect-only`): 502 unit (four places in `AGENTS.md`), 512 total, 10 integration,
  164 engine tests.
- Gate: P1's `verify:` (`regressions mutants records suite`) GREEN (exit 0, 414 checks ok, none failed) in a
  throwaway worktree holding the work as one commit, whose diff from `8517e7f` is byte-identical to this
  run's working-tree diff before this note and the remediation note were written; `suite` there: lint OK,
  502 passed under `CI=true`, 0 skipped, equal to `AGENTS.md`'s 502. The profile's full `verify` in the
  cycle worktree: lint exit 0, `502 passed in 26.60s`, 0 failed, 0 skipped. The two pins passed 40 of 40
  runs, eight processes at once. `records` and `ledger` re-run on the commit (P2 records them).
- **Touches (remediation, cycle 3):** `src/sosfs/engine/overlay.py` (docstring only),
  `tests/engine/test_replay.py`, `AGENTS.md`, `PLAN.md`, `ROADMAP.md`, `issues/source-served-open-race.md`,
  `issues/truncate-recording-after-ancestor-rename.md`,
  `spec/source-path-truncate-and-stat/remediation/cycle3-pinning-tests.md`, this file.

## Phase P2 — Verify is green under the fixed CI seed and twenty random seeds
**Satisfies:** R5, R6 · **Depends on:** P1
**Goal:** the profile's `verify` is green under `CI=true` with more than 474 passed, equal to
`AGENTS.md`'s count, and `test_model` passes for seeds 1 to 20 from committed state.

- [x] Run the verify. No code changes are expected. A red seed is a defect in P1's code, never a
  reason to touch `test_model.py`. It reopens P1 (`set_phase.py`), and the falsifying example becomes
  a deterministic regression in `test_replay.py`.
- [x] Record in the commit body: the passed count, the suite time, and the seeds' pass list.
- [x] `AGENTS.md` § Architecture: correct the stale engine-test figure to the measured count.
- **Verify:** `truncate_gate.py suite seeds`.
- **Touches:** `spec/source-path-truncate-and-stat/TECH.md`, `AGENTS.md`.
- [x] **Remediation, review cycle 1:** `suite` and `seeds` on the remediation commit. P1's `suite` run
  stands in for this phase's; `seeds` reads HEAD, so it runs after the commit.
  On `bd70c89`: `suite` green before the commit (lint OK, 493 passed under `CI=true`, 0 skipped);
  after it, `regressions mutants records seeds` green in 318 s, test_model passing for seeds 1–20.
  This commit changes only this file, so that run stands for it.
- [x] **Remediation, review cycle 2:** `suite` and `seeds` on the cycle-2 P1 commit. P1's `suite` run stands
  in for this phase's; `seeds` reads HEAD, so it runs after that commit.
  P1's gate (`regressions mutants records suite`) GREEN before the commit, in a throwaway worktree holding
  the work as one commit whose tree differs from `eab81f7` only in this file's state lines (lint OK,
  500 passed under `CI=true`, 0 skipped, equal to `AGENTS.md`'s 500). On `eab81f7`: `seeds` GREEN in about
  5 minutes, test_model passing for seeds 1–20. Serial `-m unit` measured at 29.7 s (500 passed). This
  commit adds the F6 seed (`issues/truncate-recording-after-ancestor-rename.md`) to `ledger`'s required
  reach and words P1's F6 item as deferred work; `ledger` GREEN, and red once with that word removed.
- [x] **Remediation, review cycle 3:** `suite` and `seeds` on the cycle-3 P1 commit, `4d5f198` (both read
  committed state, so they ran after it). GREEN (exit 0): `uv sync --locked` exit 0, lint OK, 502 passed
  under `CI=true` in 26.5 s, 0 skipped, equal to `AGENTS.md`'s 502; test_model passing for seeds 1–20, each
  from a tree with no `.hypothesis/`. Serial `-m unit` measured at 28.7 s (502 passed), so `AGENTS.md`'s
  ~30 s holds. `records` and `ledger` GREEN on `4d5f198` too. This commit changes only this file.
- **Amendment (build, 2026-11-06, foreman ruling):** `AGENTS.md` § Architecture said "111 engine
  tests", stale before this cycle: `tests/engine` collects 136 at the base and 152 after P1. This
  phase sets it to the measured 152 and adds `AGENTS.md` to Touches. No other test count in
  `AGENTS.md` or the repo `PLAN.md` is stale: the unit count stays 490 at all four places the gate
  reads, and the 500 total, 10 integration and 42 files hold as measured.

## Phase P3 — CI is read from the run record, and the ledger closes
**Satisfies:** R7 · **Depends on:** P2
**Goal:** the four CI jobs conclude `success` on the pushed tree, read from the run record. The
evidence is a saved run record the foreman hands over, so the orchestrator grades R7, not the blind
reviewer.

This phase waits on CI, so it is **two runs** (code-foreman → "External waits split the run").

**Run A — check, push, stop.**
- [x] Deferral ledger: walk P1 and P2 for deferral wording and confirm each names an `issues/` seed
  with a `ROADMAP.md` entry (`ledger` does the mechanical half; prose and list items, never headings).
  This plan's deferrals are the old sizes (`issues/audit-command.md`) and the `open`/`_serve` window
  (`issues/source-served-open-race.md`).
  Run A: `ledger` green, reaching both seeds. The prose walk found no other deferral: P1's "Still
  open" sentence names both seeds, and its own-seed item hands retirement to the roadmap step after
  landing. Every seed P1 and P2 name has its file and its `ROADMAP.md` entry.
- [x] Commit (the phase stays `in_progress`; the body says "Run A"). Run `truncate_gate.py ledger
  prepush` on the committed tree. On green, push `fix/source-path-truncate-and-stat` to `origin` under
  the standing cycle-branch approval. Stop and hand back the pushed SHA.
  Run A: committed `b9b0027`; `ledger prepush` GREEN on it (clean tree, `uv sync --locked` exit 0,
  lint OK, `490 passed in 29.02s` under `CI=true`, records all ok, no home paths); pushed, and
  `origin/fix/source-path-truncate-and-stat` is at `b9b0027`.

**Run B — read, grade, close.**
- [x] The foreman saves `gh run view <id> --json databaseId,headSha,headBranch,event,status,conclusion,jobs`
  for the push run to `.local/ci/run.json`, and the four job logs to `.local/ci/logs/<job id>.log`
  (the builder has no `gh`). Run the verify. `ci` takes the commit from
  `origin/fix/source-path-truncate-and-stat` unless `--sha=` names another; commits after it may touch
  only `spec/source-path-truncate-and-stat/`. Every job must conclude `success`, and Linux passed must
  equal `AGENTS.md`'s count with no skip. On macOS, passed plus skipped must equal Linux, with every
  skip under `tests/fuse` naming libfuse.
  Run B: `ledger records ci` GREEN (exit 0) on the saved record.
- [x] Record the run id, the pushed SHA and each job's counts in this phase, then close it.
  Run B: CI run `42329860978`, event `push`, head `b9b0027df217ae9e14ad390a64678231cbcaadb9` on
  `fix/source-path-truncate-and-stat`, conclusion `success`; `ci.yml` at the pushed commit is
  `main`'s.

  | Job | Id | Conclusion | Counts |
  |---|---|---|---|
  | Unit (Linux, 3.15) | 128273654570 | success | 490 passed, 0 skipped; `Assert libfuse loads` success |
  | Unit (Linux, 3.15t) | 128273654398 | success | 490 passed, 0 skipped; `Assert libfuse loads` success |
  | Unit (macOS, engine-only) | 128273654166 | success | 446 passed + 44 skipped = 490; all 44 skips under `tests/fuse`, naming libfuse |
  | Lint & build | 128273654560 | success | no test count |
- **Verify:** `truncate_gate.py ledger records ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs`.
- **Touches:** `spec/source-path-truncate-and-stat/TECH.md` only.
- [x] **Remediation, review cycle 1, Run A:** `ledger prepush` on the committed tree, then push.
  Run A (2026-11-07): `ledger prepush` GREEN (exit 0) on `7eba2ad` (clean tree, lint OK, `493 passed`
  under `CI=true`, equal to `AGENTS.md`'s 493, records all ok, no home paths); pushed, and
  `origin/fix/source-path-truncate-and-stat` is at `7eba2ad`.
- [x] **Remediation, review cycle 1, Run B:** `ledger records ci` on the new push run's saved record.
  Run B (2026-11-07): `ledger records ci` GREEN (exit 0) on CI run `42342479853`, event `push`, head
  `7eba2ade55c10202087ad7236aa922593b5e42f2` on `fix/source-path-truncate-and-stat`, conclusion
  `success`; `ci.yml` at the pushed commit is `main`'s.

  | Job | Id | Conclusion | Counts |
  |---|---|---|---|
  | Unit (Linux, 3.15) | 128312985069 | success | 493 passed, 0 skipped; `Assert libfuse loads` success |
  | Unit (Linux, 3.15t) | 128312984999 | success | 493 passed, 0 skipped; `Assert libfuse loads` success |
  | Unit (macOS, engine-only) | 128312984952 | success | 449 passed + 44 skipped = 493; all 44 skips under `tests/fuse`, naming libfuse |
  | Lint & build | 128312984792 | success | no test count |
- [x] **Remediation, review cycle 2, Run A:** `ledger prepush` on the committed tree, then push.
  Attempts recorded (2026-11-07): `ledger prepush` was red on `baea598` and on `2e5fdc7`, each time on this
  file's own new text, a line whose wording the ledger flags with no seed named (P2's results line, then
  this note); both lines are reworded, and both modes re-run.
  Run A (2026-11-07): `ledger prepush` GREEN (exit 0) on `1a8be2d` (clean tree, lint OK, `500 passed`
  under `CI=true`, equal to `AGENTS.md`'s 500, records all ok, no home paths); pushed, and
  `origin/fix/source-path-truncate-and-stat` is at `1a8be2d`.
- [x] **Remediation, review cycle 2, Run B:** `ledger records ci` on the new push run's saved record.
  Attempt recorded (2026-11-07): `ledger records ci` red on CI run `42409264319` (head `1a8be2d`): the
  saved record carries `conclusion,createdAt,headSha,jobs,status,updatedAt,url`, not the gate's field list,
  so `event` and `headBranch` read as missing. `ledger` and `records` were green; the record needs
  re-saving with `databaseId,headSha,headBranch,event,status,conclusion,jobs`, then the verify re-runs.
  Geoffrey's ruling (2026-11-07, at the breaker gate): "Exempt and re-run Run B". Attempt 3 was a foreman
  record-keeping slip (the saved run record lacked the gate's fields), not a gate finding, and doesn't count
  toward the breaker: P3 stands at two counted attempts. `set_phase.py` has no reset or annotate option, so
  the YAML counter reads 3 and this note carries the exemption.
  Run B (2026-11-07), on the re-saved record: `ledger records ci` GREEN (exit 0) on CI run `42409264319`,
  event `push`, head `1a8be2d8b741032929558e9770829f9316523c39` on `fix/source-path-truncate-and-stat`,
  conclusion `success`; `ci.yml` at the pushed commit is `main`'s.

  | Job | Id | Conclusion | Counts |
  |---|---|---|---|
  | Unit (Linux, 3.15) | 128513633927 | success | 500 passed, 0 skipped; `Assert libfuse loads` success |
  | Unit (Linux, 3.15t) | 128513633869 | success | 500 passed, 0 skipped; `Assert libfuse loads` success |
  | Unit (macOS, engine-only) | 128513633921 | success | 456 passed + 44 skipped = 500; all 44 skips under `tests/fuse`, naming libfuse |
  | Lint & build | 128513633733 | success | no test count |
- [x] **Spot fix after review cycle 3 (Geoffrey's ruling, closing route (a)), Run A:** `ledger prepush` on
  the committed tree, then push.
  Run A (2026-11-08): built under the P1 and P2 commits (`4d5f198` P1: F1 records, F3 pins; `cb50f3b` P2:
  `suite` and `seeds`). `ledger prepush` GREEN on `cb50f3b` (clean tree, lint OK, `502 passed` under
  `CI=true`, equal to `AGENTS.md`'s 502, records all ok, no home paths); pushed, and
  `origin/fix/source-path-truncate-and-stat` is at `cb50f3b`.
- [x] **Spot fix after review cycle 3 (Geoffrey's ruling, closing route (a)), Run B:** `ledger records ci`
  on the new push run's saved record.
  Run B (2026-11-08): `ledger records ci` GREEN (exit 0) on CI run `42451000482`, event `push`, head
  `cb50f3b2c8d510c79b02517bfc57177c241da280` on `fix/source-path-truncate-and-stat`, conclusion `success`;
  `ci.yml` at the pushed commit is `main`'s, and the one commit after it (`a7b2df2`, the cycle 3 rulings)
  touches only `spec/source-path-truncate-and-stat/`.

  | Job | Id | Conclusion | Counts |
  |---|---|---|---|
  | Unit (Linux, 3.15) | 128640023136 | success | 502 passed, 0 skipped; `Assert libfuse loads` success |
  | Unit (Linux, 3.15t) | 128640023056 | success | 502 passed, 0 skipped; `Assert libfuse loads` success |
  | Unit (macOS, engine-only) | 128640022781 | success | 458 passed + 44 skipped = 502; all 44 skips under `tests/fuse`, naming libfuse |
  | Lint & build | 128640023021 | success | no test count |

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[{category}]` commit; stop and report.
