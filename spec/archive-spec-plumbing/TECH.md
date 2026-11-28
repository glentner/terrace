---
slug: archive-spec-plumbing
title: Make archive sources reachable from a spec file
kind: feature
appetite: big
status: in_progress
branch: feature/archive-spec-plumbing
base: main
current_phase: P1
last_updated: '2026-11-28'
phases:
- id: P1
  name: The engine serves a direct-update archive source, registered with no marker
  status: pending
  satisfies:
  - R6
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p1
- id: P2
  name: A tar.gz bundle is pulled into the durable tier, journaled and counted
  status: pending
  satisfies:
  - R6
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p2
- id: P3
  name: Every mutating engine call over an archive source fails EROFS
  status: pending
  satisfies:
  - R9
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p3
- id: P4
  name: The parser opens the archive types and refuses what the engine cannot serve; the loop moves
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R8
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p4
- id: P5
  name: The mount test, the documents and the figures
  status: pending
  satisfies:
  - R10
  - R11
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p5
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — Make archive sources reachable from a spec file

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --no-project --with pyyaml python "$KB/bin/factory/next_phase.py" spec/archive-spec-plumbing/TECH.md`); the per-phase
checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --no-project --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

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
  and every item the previous cycle's last phase carried for a project-level obligation (mpsearch's
  `## Times` quote, taken as `code-build` Step 4 says). A plan that drops one says why in `PLAN.md`.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

**This cycle's gates.** Every `verify:` runs `spec/archive-spec-plumbing/verify/check_outcomes.py`. That
gate is frozen from the plan commit. It names test ids, so the ids below are part of each phase's
work: a row named here and not written fails the gate as "did not run". Each mode also re-runs every
earlier phase's rows and the whole unit lane. Stage before running `verify:` (code-build Step 6's
staging rule, by path), because `ids` and `harness` read the staged diff against the base. The gate assumes a host
where libfuse loads, as this development host does. Every mode's lane check also enforces the following:

- **Every test that passed at the base passes now** (`verify/base-passed.txt`, 842 ids). No existing test
  may be deleted, renamed or turned into a skip. Change an assertion inside a kept test; never its name.
- **No spec id in an added line** of `src/`, `tests/` or `README.md`: `R1`, `P3`, and also `D2`, `O1`
  and `Q4` (pattern `\b[RPDOQ]\d{1,2}\b`).
- **From P4, the loop's harness is frozen.** `tests/loop/{conftest,oracle,tap,test_scenario,
  test_scenario_minio,test_support,__init__}.py` are unchanged. In `stages.py`, `blocked`, `owned`,
  `Blocked`, `Scene`, `listing`, `assert_tree` and the bodies of stages 1 and 2 are unchanged by AST
  (stage 1 and 2 lose their decorators only).
- **Mutants**, planted by `verify/archive_cycle_mutants.py`, must each be killed by their guarding rows:
  - `read-only` (P3 on): every `test_mutating_call_fails_erofs` id fails once the engine never reads as
    read-only.
  - `oracle` and `counter` (P2 on): R7's counted-bundle row fails once `PosixTier.usage` leaves the
    bundle out, or `ArchiveTier.stage` reports nothing created.
  - `realpath` (P4 on): every symlink row fails once `os.path.realpath` does no resolution.

  So `ArchiveTier.stage` is the method name, and P2's and P4's rows must exercise what their names
  say.

**Sign-off note (O1).** `test_rewritten_archive_is_refused_at_restart` and the signature half of
`enlist` exist only if the sign-off accepts PLAN's open question O1. If O1 is declined, the planner
removes that row from `ROWS['p1']` in the gate and from P1 below before build starts, and P5's ledger
adds the risk to `issues/archive-finalization-integration.md`.

---

## Phase P1 — The engine serves a direct-update archive source, registered with no marker
**Satisfies:** R6, R8 (zip and tar; tar.gz in P2) · **Depends on:** — · coupled core: yes
**Goal:** An engine started over a hand-built `OverlaySpec` whose source is a `zip` or `tar` tier builds
an `ArchiveTier` over the inner location. It registers the tier with no marker (status `OK`), restarts
cleanly, and lists and reads every member through the engine API. Nothing parses such a spec yet.

- [ ] `tier/spec.py`: add `ARCHIVE_TYPES`, and `TierSpec.location: Optional[LocationSpec] = None` and
  `TierSpec.max_frequency: Optional[Rate] = None`. Fields only; the parser is unchanged in this phase.
- [ ] `state/registry.py`: add `TierRegistry.enlist(name, *, position, type, role, scope, meta) ->
  (TierRecord, MarkerStatus)` beside `_attach`, with `_attach`, `attach` and `attach_tier` untouched.
  - It calls `register`. On a first registration it stores `meta` as JSON in `tier.meta`; on a later one
    it compares and raises `SpecDrift` naming the tier and both values.
  - It returns `MarkerStatus.OK` and does no marker I/O.
  - `meta = {'location': inner.identity, 'signature': list(inner.signature())}`.
- [ ] `engine/overlay.py` `start`: add an archive branch ahead of the POSIX `else` (PLAN § 2.2).
  - Function-local imports.
  - A missing archive raises `EngineError('Source archive does not exist: <identity>')`.
  - Build `ArchiveTier(name, inner, db, type, bundle=None, max_frequency=…)` and `enlist` it before
    any index.
  - A tar.gz source still needs P2's bundle; in this phase the adapter's own constructor refusal
    stands.
- [ ] `tier/archive.py` (none of these are on the high-blast-radius list):
  - `_stat_of` and synthesized ancestors report `0o755` for a directory whose mode is `None`.
  - `_scan_zip`/`_scan_tar` keep the last member of a duplicated name.
  - A tar hard link (`islnk()`) is indexed as a regular file with its target's data offset and size.
  - A sparse member (`issparse()`) is marked in `extra` and read through `tarfile`'s extraction in
    `_extract`, not by offset.
  - Device, FIFO and character members stay skipped (PLAN D4).
- [ ] New module `tests/engine/test_archive_source.py` (`pytestmark = mark.unit`).
  - The fixture archive holds: a top-level file, an empty file, depth-2 and depth-3 files, a member of
    at least 1 MiB, a directory with a header and one without, and a tar symlink. The ZIP has no
    directory entries and no unix mode on one directory.
  - Parametrize with ids `zip`, `tar` (P2 adds `tar.gz`):
    - `test_start_builds_an_archive_tier_over_the_location[*]`: `engine.source` is an `ArchiveTier`
      over the spec's location; the registry row exists; start returned.
    - `test_restart_reads_ok_and_writes_nothing_beside_the_archive[*]`: start, stop, start again.
      Both starts succeed. The archive's sha256, size, `st_mtime_ns` and inode, and its parent
      directory's listing, are unchanged.
    - `test_listing_and_reads_match_the_archive[*]`: a recursive `readdir` equals the member set;
      every file read through `open`/`read`/`release` equals its bytes from `zipfile`/`tarfile`; a
      promoted directory on the stage has mode `0o755`. It asserts the mode, so it holds when run as
      root.
  - `test_missing_archive_is_refused_at_start`
  - `test_moved_location_is_refused_at_restart`
  - `test_rewritten_archive_is_refused_at_restart` (O1)
- [ ] `tests/state/test_registry.py`: `test_enlist_reads_ok_with_no_marker` and
  `test_enlist_refuses_changed_meta`.
- [ ] `tests/tier/test_archive.py`: `test_directory_without_mode_is_searchable[zip|tar]`,
  `test_duplicate_member_last_wins[zip|tar]`, `test_hard_link_reads_its_target` and
  `test_sparse_member_reads_its_bytes`.
- **Verify:** `tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p1`.
  - Every row above passes.
  - The unit lane has no failure, exactly the nine stage xfails, and at least 842 plus 17 passes.
  - No spec id appears in an added line.
- **Touches:** `src/terrace/tier/spec.py` (fields), `src/terrace/state/registry.py`,
  `src/terrace/engine/overlay.py` (`start`), `src/terrace/tier/archive.py`, and the three test files.

## Phase P2 — A tar.gz bundle is pulled into the durable tier, journaled and counted
**Satisfies:** R7, R6 and R8 for tar.gz · **Depends on:** P1 · coupled core: yes
**Goal:** Over a tar.gz source, start pulls the bundle once into
`<durable root>/.terrace/bundles/<source>.tar`, under an intent. The durable tier's counters count it as
one file at its uncompressed size, and `PosixTier.usage()` agrees. The bundle is never listed, and
members read at their offsets.

- [ ] `state/journal.py`: `BUNDLE_DIR = 'bundles'` beside `PARTIAL_DIR`. `tier/archive.py`:
  `bundle_path(root, source) -> Path`, and the keyword `staging` renamed to `bundle` (tar.gz requires
  it).
- [ ] `ArchiveTier.stage() -> bool`: runs `_populate` and returns whether it created the file.
  `_populate`, `index` and `atomic_write` are unchanged.
- [ ] `engine/overlay.py`:
  - In `start`, build the tar.gz tier with `bundle=bundle_path(Path(spec.tiers[position + 1].path),
    name)`, with no disk access.
  - After the attach loop and before the root `stat`, `_stage_bundle()` runs when the source is
    tar.gz: `journal.begin('promote', durable, now=…, detail=…)` with no entry, then `source.stage()`.
    On an exception it rolls back and re-raises.
  - Then, in one `db.transaction()`: `confirm`, plus `registry.adjust_usage(durable,
    bytes=st_size, files=1)` only when the bundle was created.
  - No residency row. The action vocabulary and `SCHEMA_VERSION` are unchanged.
  - **Recovery of an interrupted count.** Before staging, `_stage_bundle` looks for a pending intent
    (`journal.pending(durable)`) whose `detail` names this bundle (`detail = {'kind': 'bundle',
    'bundle': <path>, 'source': <tier>}`). If it finds one, the previous pull may have landed without
    its count. It counts the bundle if the file is present, and resolves that intent in the same
    transaction. A crash between `_populate`'s rename and the confirm then cannot leave the bundle
    uncounted forever.
- [ ] `tier/posix.py` `usage()` also counts regular files named `*.tar` (not dot-named) directly in
  `.terrace/bundles/`. In the same commit, narrow `AGENTS.md` § 4's `usage()` bullet, and § *Read before
  editing* if it restates it, to say the staged bundle is the one managed file the oracle counts,
  because Decision 1 counts it (PLAN D2).
- [ ] `PLAN.md`'s ArchiveTier `staging` sentence (`:653-655`) may wait for P5, which owns R11's text.
  Nothing in this phase may leave `AGENTS.md` asserting a `usage()` rule the code breaks.
- [ ] `tests/tier/test_archive.py`: rename `staging=` to `bundle=` at `:362`, `:365-368` (message) and
  `:512`.
- [ ] `tests/engine/test_archive_source.py`:
  - Add `tar.gz` to the three parametrized tests. The tar.gz is built from the same tar.
  - `test_staged_bundle_is_one_counted_file_in_the_durable_root`. Before any member read:
    - exactly one regular file is under the durable root's `.terrace/bundles/`;
    - its size equals the decompressed archive's length;
    - no file is outside the tier roots, the state directory and the archive;
    - `registry.usage(durable) == (size, 1)`.

    After reads, `registry.usage(t) == tiers[t].usage()` for every stage tier.
  - `test_staged_bundle_is_not_listed`: `readdir('/')` equals the archive's top-level members.
  - `test_bundle_pull_leaves_no_intent`: `journal.pending()` is empty after start.
  - `test_second_start_does_not_pull_or_count_again`: the bundle's inode and the counters are unchanged
    across a stop and a start.
  - `test_interrupted_bundle_count_is_recovered_at_restart`:
    1. Make the first start fail once, at the confirm transaction, after the bundle has landed.
    2. Restart.
    3. `registry.usage(durable) == tiers[durable].usage()` and equals `(size, 1)`, and no intent is
       pending.
- [ ] `tests/tier/test_posix.py`: `test_usage_counts_staged_bundles`. A bundle counts; a temp beside it,
  `partial/` and `marker` do not.
- **Verify:** `tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p2`.
  - P1's and P2's rows all pass.
  - Nine stage xfails, and at least 842 plus 26 passes.
  - No spec ids.
- **Touches:** `src/terrace/state/journal.py` (constant), `src/terrace/tier/archive.py`,
  `src/terrace/engine/overlay.py` (`start`, `_stage_bundle`), `src/terrace/tier/posix.py` (`usage`),
  `AGENTS.md` § 4, and tests.

## Phase P3 — Every mutating engine call over an archive source fails EROFS
**Satisfies:** R9, and R6's "nothing written beside the location" under writes · **Depends on:** P2 · coupled core: yes
**Goal:** Over a zip, tar or tar.gz source, every mutating engine call fails `OSError` with errno
`EROFS`. A drain, a flush, the scheduler, a stop and a restart leave the archive's bytes, inode and
siblings unchanged.

- [ ] `engine/overlay.py`:
  - `self.read_only = spec.source.type in ARCHIVE_TYPES`, set in `__init__`.
  - `_refuse(path)` raises `OSError(errno.EROFS, os.strerror(errno.EROFS), path)`.
  - Add `if self.read_only: self._refuse(path)` as the first statement of `mkdir`, `create`, `unlink`,
    `rmdir`, `rename` (before `getattr(old)`), `write`, `truncate` and `_set_meta`. For `write`, which
    takes a handle, use the handle's path. No existing statement moves (PLAN D1).
  - `open` refuses `flags & os.O_ACCMODE != os.O_RDONLY` or `flags & os.O_TRUNC` before `_ensure`.
- [ ] New module `tests/engine/test_archive_readonly.py` (`unit`):
  - **`test_mutating_call_fails_erofs[{fmt}-{call}]`**, with ids exactly `fmt` in `zip`, `tar`, `tar.gz`
    and `call` in `mkdir`, `create`, `unlink`, `rmdir`, `rename`, `rename-over`, `chmod`, `chown`,
    `utimens`, `truncate`, `open-wronly`, `open-rdwr`, `open-rdonly-trunc`, `open-wronly-append`,
    `write-rdonly-handle`, `truncate-handle`: 48 ids.
    - Each asserts `errno == EROFS`.
    - The fixture includes an empty `empty/` directory for `rmdir`.
    - `rename-over` renames onto an existing file.
  - **`test_archive_unchanged_after_refusals_and_drain[{fmt}]`**:
    1. Snapshot the archive (sha256, size, `st_mtime_ns`, inode) and its parent's listing.
    2. Make every refused call.
    3. Assert `journal.queued()` is empty and no residency is dirty.
    4. Run `flush()`, a `WritebackScheduler` poll, `drain`, `stop()` and a restart, then read a member.
    5. Compare the snapshot.
  - **`test_every_public_engine_method_is_classified`**: a `MUTATING` set and a `NON_MUTATING` set
    whose union is exactly `{n for n in vars(OverlayEngine) if not n.startswith('_')}`.
- **Verify:** `tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p3`.
  - All earlier rows and these 52 pass.
  - Nine stage xfails, and at least 842 plus 78 passes.
  - No spec ids.
- **Touches:** `src/terrace/engine/overlay.py` (the guards, `open`, `__init__`), and the new test
  module.

## Phase P4 — The parser opens the archive types and refuses what the engine cannot serve; the loop moves
**Satisfies:** R1, R2, R3, R4, R5, R8 (stages 1–2 and the helper) · **Depends on:** P3 · coupled core: no (`tier/spec.py` is high-blast-radius)
**Goal:** The two single-bundle archive examples of `PLAN.md` § *Configuration* load. Every stack the
engine cannot serve is refused at load, naming the tier at fault. Overlap is judged on resolved paths.
Loop stages 1 and 2 pass unmarked over the helper's spec, which carries no `staging`, and stages 3–9 are
pinned at their new stops in the same commit.

- [ ] `tier/spec.py`, PLAN § 2.1:
  - Add the keys `location`, `sync` and `sharded` to `TIER_KEYS`. `staging` stays out of `TIER_KEYS`,
    so `TierSpec.from_data` called directly still refuses it as unknown. `OverlaySpec.from_data` scans
    the raw `[[tier]]` tables for `staging` before any tier is built.
  - The five supported types, with the dead branch removed.
  - Refusals in `from_data`, in order: `sharded`, R3, location, `sync`.
  - The `_rate` wrapper.
  - `staging` checked first at stack level, then R4 in `_validate_stack`.
  - realpath comparison keys for POSIX roots, POSIX archive files and the state directory; S3 never
    resolved.
- [ ] **The sharded refusal's exact text**, which the gate and stage 9 pin:
  `Sharded archive sources are not yet supported (tier '<name>'); see issues/sharded-archive-sources.md`.
- [ ] `tests/tier/test_spec.py`: change the `tar`-at-stage assertion in `test_tier_type_validation` to the
  R3 refusal, and add these rows:
  - `test_archive_examples_load[direct-update-zip|staged-tar-gz]`: each example's `[[tier]]` tables
    are kept verbatim inside an `[overlay]` table, the ZIP above one POSIX shared stage tier.
  - `test_archive_tier_carries_location_and_rate`
  - `test_sync_refused_on_direct_update[zip|tar]`
  - `test_staging_refused_naming_the_durable_tier`: the message names `staging`, the carrying tier,
    and the durable tier.
  - `test_staging_refused_without_a_durable_tier` (PLAN D3)
  - `test_sharded_refused_naming_its_seed`
  - `test_archive_refused_at_stage_position[zip|tar|tar.gz]`
  - `test_archive_stack_rule[no-stage|node-stage|s3-stage|second-shared]`: each message names the tier
    at fault, or says the stage tier is missing.
  - `test_symlinked_root_overlap_is_refused[source|stage|both]`: the symlink is on the source side (a
    source root reached through a symlink, a stage under its real path), on the stage side (a stage
    root that is a symlink into the source), or on both. Each is refused naming both tiers.
  - `test_archive_under_a_symlinked_directory_overlaps_its_stage`: the archive's parent directory is a
    symlink, and the stage root is that directory's real path.
  - `test_symlinked_state_dir_inside_a_tier_is_refused`: the state directory is a symlink into a tier
    root.
  - `test_state_dir_under_a_symlinked_tier_is_refused`: a tier root is a symlink, and the state
    directory is a real path inside its target.
  - `test_stage_root_containing_the_archive_overlaps`
- [ ] `tests/engine/test_archive_source.py`: `test_parsed_archive_spec_serves_a_read_past_a_full_stage`.
  A ZIP spec loaded through `OverlaySpec.parse`, whose one stage has `quota.size` smaller than the
  member, still serves the read, with no `EngineError` (R4's second sentence).
- [ ] `tools/bundle_loop.py`: drop the `staging` attribute and line. `tests/loop/test_helper.py`: replace
  the `staging` assertions (`:76`, `:86`, `:92-93`, `:98`) with `'staging' not in source` and no
  `staging` attribute, in both forms.
- [ ] `tests/loop/stages.py` (PLAN § 2.6):
  - Stages 1 and 2 lose `@owned`, and their `STAGES` entries become `spec/archive-spec-plumbing/GOAL.md`.
  - `reach` pins only the sharded form: `blocked('issues/sharded-archive-sources.md', SpecError, <text>)`.
  - `change()` pins its first mutating call: `blocked('issues/archive-journaling-and-locking.md',
    OSError, "[Errno 30] Read-only file system: '/bundle-000/member-000.txt'")`. Use the path the
    stage actually opens.
  - Stage 3 pins its `pushed` assertion: `blocked('issues/eviction-one-hop.md', AssertionError, <text
    measured on this tree>)`. The gate checks the prefix `nothing was pushed out of the fastest tier`.
  - Stages 3, 7 and 8 call `engine.stop()` in a `finally`.
  - Stage 8's bundle check uses `bundle_path`.
  - Replace `PARSER`/`REFUSAL` and update `__all__`.
- [ ] `AGENTS.md`, in this commit:
  - § Verification's loop paragraph ("Today they are 9 expected failures … each stopped at the same
    step") states the new record: 2 passing and 7 expected failures, with their stops.
  - The `tests/` row's `(blocked, REFUSAL)` wording follows the code.
  - § Commands' helper paragraph no longer implies a `staging` path.
- **Verify:** `tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p4`.
  - All rows of P1–P4 pass, including the kept helper and support tests.
  - `examples`: the gate takes the two single-bundle examples from the root `PLAN.md` as written,
    found by their comments `# direct-update ZIP as source` and `# staged re-sync tar.gz on S3 as
    source`. It wraps each in an `[overlay]` table, gives the ZIP one POSIX stage, and parses them.
    P5 must keep those two blocks and their comments.
  - `harness` and `mutants` (`realpath`, plus the earlier ones), as in § This cycle's gates.
  - `stages`: stages 1 and 2 pass unmarked. Stages 3–9 are xfailed under their owners, each with the
    strict marker and the pinned `cause`/`stopped`. Stages 1 and 2 name the surviving `GOAL.md`.
  - `drive`, through `tools/temp_site.sh`: the helper's one-bundle spec loads as `tar.gz` above
    `scratch`, a shared POSIX tier; the two-bundle spec is refused with the exact sharded text; neither
    mentions `staging`.
  - The lane has seven xfails, and at least 842 plus 2 plus 101 passes.
  - No spec ids.
- **Touches:** `src/terrace/tier/spec.py`, `tools/bundle_loop.py`, `tests/tier/test_spec.py`,
  `tests/engine/test_archive_source.py`, `tests/loop/{stages.py,test_helper.py}`, and `AGENTS.md`
  (§ Verification, § Commands, the `tests/` row).

## Phase P5 — The mount test, the documents and the figures
**Satisfies:** R10, R11 · **Depends on:** P4 · coupled core: no
**Goal:** A FUSE mount over a ZIP source lists the archive's top-level members. That is proven on a
Linux host with `/dev/fuse`, and here the test skips by the platform gate. The root `PLAN.md` and
`AGENTS.md` say what the tree now does, and the residuals have homes.

- [ ] `tests/fuse/test_integration.py`:
  - `start_mount` takes an optional `spec`. Its thread target records any exception `Mount.serve`
    raises, such as the engine's start failing. The wait calls `fail()` with it rather than skipping
    on the timeout. The existing `_loop_error` skip, for a host that refuses the mount itself, stays a
    skip.
  - Add `test_zip_source_mount_readdir_lists_top_level_members`: a ZIP with explicit modes (`top.txt`,
    `sub/`, `sub/nested.txt`) and a spec parsed from TOML with `OverlaySpec.parse` (a zip source over
    a posix location, one shared POSIX stage). It asserts `sorted(os.listdir(point)) == ['sub',
    'top.txt']`.
- [ ] `tests/fuse/test_adapter.py` (`unit`): `test_zip_source_readdir_through_the_adapter`. It uses the
  same archive and spec through `OverlayOperations` with no mount, and asserts `readdir('/')`,
  `getattr('/')` is a directory, and `statfs('/')` returns.
- [ ] **R11, the root `PLAN.md`** (PLAN § 2.8):
  - § *Configuration* (`:1225-1233`): the two single-bundle examples load; the sharded one is refused
    naming its seed. The working-copy paragraph is removed.
  - ArchiveTier (`:653-655`): the bundle lives in `.terrace/bundles/` of the durable tier and is
    pulled at mount.
  - The Decision 1 row (`:290`) narrows to the reclaim rule; the Decision 7 row (`:295`) to the tier a
    soft unmount commits to.
  - § *Managed locations* (`:825-831`): an archive source carries no marker while writes over it are
    refused.
  - ArchiveTier (`:643`): "on first access the whole archive is pulled" becomes "at mount". The
    sentence on a member write re-packing the staged `.tar` (`:658`) says writes over an archive
    source are refused, so no re-pack is reachable.
  - The Decision 1 row's "no spec reaches the adapter" and the Decision 7 row's "no spec rule limits a
    bundle source's stack to one shared stage tier" are gone.
- [ ] **R11, `AGENTS.md` § Project's honest status** (`:31-32`): drop "adapter-complete but not yet
  reachable from a spec file"; say archive sources are readable and refuse writes.
- [ ] **R11, `AGENTS.md` § Known open violations:**
  - Remove the `issues/archive-spec-plumbing.md` row.
  - In the two `issues/archive-journaling-and-locking.md` rows, say the defects are unreachable while
    every write over an archive source is refused, and "stale staging copy" becomes the staged bundle.
- [ ] **`AGENTS.md` figures:**
  - The unit and integration counts, both 19 to 20 integration (lines 66 and 105, the word "twenty").
  - The `tests/` row's files and tests, the `-m integration` skip line, the unit count and timings in
    § Verification, and the engine test count in § Architecture.
  - Measure the timings on this tree.
  - Optionally, name the archive-source readdir in the list of tests CI never runs.
- [ ] **Deferral ledger.** Walk every phase above for deferral phrases ("not in this cycle", "residual",
  "stays with", "out of scope", "left as") and confirm each has its seed and a `ROADMAP.md` entry. The
  plan's are:
  - The new seed `issues/source-directory-mode.md` (`status: unshaped`, from the KB's ISSUE template):
    an S3 source's mode-less directories are cataloged `0o644` by `_adopt`, so a promotion below one
    fails `EACCES`, as brief 06 reads it; also a directory whose own mode lacks owner `wx`. Add its
    `ROADMAP.md` entry.
  - A dated note in `issues/eviction-one-hop.md`: a re-pull during a mount is unjournaled and
    recounted, and a bundle over the durable tier's low watermark causes eviction churn there.
  - A dated note in `issues/audit-command.md`: a bundle deleted between mounts is recounted, and the
    audit's rebuild heals it through `usage()`.
  - A dated note in `issues/engine-error-mapping.md`: a corrupt archive raises `BadZipFile`/`ReadError`.
  - A dated note in `issues/invariant-audit-gaps.md`: the pull's temp now sits in
    `.terrace/bundles/`, still outside `partial/`.
  - A dated note in `issues/archive-tier-test-coverage.md`: a zip or tar over an S3 location
    downloads the whole object for each first read of a member, and no test covers that form.
  - The note in `issues/audit-command.md` also says how the audit reads a pending bundle pull: a
    `promote` intent with no entry and `detail.kind == 'bundle'`.
  - If O1 was declined, a note in `issues/archive-finalization-integration.md`.

  Each note cites `spec/archive-spec-plumbing/`.
- **Verify:** `tools/lint.sh && uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py p5`.
  - Every row of P1–P5 passes.
  - `collect`: R10's test is collected under `integration` only, and here it skips naming Linux.
  - `docs`: the retired sentences are gone; § Managed locations says an archive source carries no
    marker; `.terrace/bundles/` is named.
  - `ledger`: the new seed exists, is unshaped and indexed, and the five notes cite the cycle.
  - `figures`: every `AGENTS.md` count equals the collected count.
  - Seven xfails, and at least 842 plus 2 plus 102 passes.
  - No spec ids.
- **R10's grade is not this `verify:`.** It is graded on a Linux host with `/dev/fuse` that the foreman
  arranges, by `uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py r10`, or by the
  equivalent `uv run pytest -m integration tests/fuse/test_integration.py -k
  test_zip_source_mount_readdir_lists_top_level_members -rA` and the line `PASSED
  tests/fuse/test_integration.py::test_zip_source_mount_readdir_lists_top_level_members`. It is never
  graded by exit status. Without that run, the review records R10 as ungraded. No cluster command is
  planned or run by build.
- **R11's prose** is read by the reviewer against the tree. The `docs` mode checks only that the
  retired sentences are gone and that two statements exist.
- **Touches:** `tests/fuse/{test_integration.py,test_adapter.py}`, `PLAN.md`, `AGENTS.md`,
  `issues/source-directory-mode.md`, `ROADMAP.md`, and four existing `issues/` seeds (notes only).

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
