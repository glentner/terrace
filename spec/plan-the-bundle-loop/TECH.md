---
slug: plan-the-bundle-loop
title: Redraw the root PLAN.md around the user story
kind: docs
appetite: big
status: in_review
branch: docs/plan-the-bundle-loop
base: main
current_phase: done
last_updated: '2026-11-16'
phases:
- id: P1
  name: The prototype record, on the helper as landed
  status: done
  satisfies:
  - R8
  - R9
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'tools/lint.sh && uv run python spec/plan-the-bundle-loop/prototype/rebuild.py | grep ''^RESULT trees_equal=yes'' >/dev/null && uv run python spec/plan-the-bundle-loop/prototype/shared.py | grep ''^RESULT oracle_bytes=yes'' >/dev/null && grep -q ''^Trees equal in names and bytes: yes'' spec/plan-the-bundle-loop/prototype/RECORD.md && grep -q ''^Each process read the oracle.s bytes: yes'' spec/plan-the-bundle-loop/prototype/RECORD.md && grep -q ''^Helper commit: [0-9a-f]'' spec/plan-the-bundle-loop/prototype/RECORD.md && git diff --cached --quiet --merge-base main -- src tests tools pyproject.toml uv.lock && git diff --cached --numstat --merge-base main -- AGENTS.md | awk ''$2 != 0 {bad=1} END {exit bad}'''
- id: P2
  name: The story, the settled rulings and the tier model
  status: done
  satisfies:
  - R1
  - R2
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && awk '/^-+$/ && length($0)>3 && length($0)==length(prev) {print prev; exit} {prev=$0}' PLAN.md | grep -qx 'The story' && grep -q 'The big user-story I want to tell' PLAN.md && grep -q '^### Settled rulings$' PLAN.md && awk -F'|' '/^[|] [A-J] [|]/ {if (NF<7) bad=1; for (i=2;i<=6;i++) if ($i ~ /^ *$/) bad=1; if ($0 !~ /2026-11-11/) bad=1; n++} END {exit !(n==10 && !bad)}' PLAN.md && grep -q '^The tier model$' PLAN.md && awk '/^### [(][a-h][)] / {n++} END {exit !(n==8)}' PLAN.md && grep -q 'spec/plan-the-bundle-loop/prototype/RECORD.md' PLAN.md && awk '/^-+$/ && length($0)>3 && length($0)==length(prev) {n++; want=NR+2} /^### / {n++; want=NR+2} /^[*]Follows from:[*]/ && NR<=want {t++; want=0} {prev=$0} END {exit !(n>=14 && t==n)}' PLAN.md && git diff --cached --quiet --merge-base main -- src tests tools pyproject.toml uv.lock && git diff --cached --numstat --merge-base main -- AGENTS.md | awk '$2 != 0 {bad=1} END {exit bad}'
- id: P3
  name: The decisions of this cycle, the state store, the lifecycle and the remaining sections
  status: done
  satisfies:
  - R3
  - R4
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && grep -q '^### Decided in this cycle, for the maintainer.s review$' PLAN.md && awk '/^[*][*]Decision [0-9]+ / {d++} /[*]Reason[.][*]/ {r++} /[*]Turned down[.][*]/ {a++} /[*]Evidence[.][*]/ {e++} END {exit !(d>=6 && r>=d && a>=d && e>=d)}' PLAN.md && grep -q '^### The state store on shared storage$' PLAN.md && grep -q 'unverified on a network filesystem' PLAN.md && grep -q '^Holds when the output is:$' PLAN.md && ! grep -q fsck PLAN.md && ! grep -q 'Phase 1[0-2]' PLAN.md && ! grep -q 'wip branch' PLAN.md && awk -F'|' '/^[|] [A-J] [|]/ {if (NF<7) bad=1; for (i=2;i<=6;i++) if ($i ~ /^ *$/) bad=1; if ($0 !~ /2026-11-11/) bad=1; n++} END {exit !(n==10 && !bad)}' PLAN.md && awk '/^-+$/ && length($0)>3 && length($0)==length(prev) {n++; want=NR+2} /^### / {n++; want=NR+2} /^[*]Follows from:[*]/ && NR<=want {t++; want=0} {prev=$0} END {exit !(n>=14 && t==n)}' PLAN.md && git diff --cached --quiet --merge-base main -- src tests tools pyproject.toml uv.lock && git diff --cached --numstat --merge-base main -- AGENTS.md | awk '$2 != 0 {bad=1} END {exit bad}'
- id: P4
  name: What the tree does not yet do, AGENTS.md notes and rows, the roadmap and the seeds
  status: done
  satisfies:
  - R6
  - R7
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: tools/lint.sh && grep -q '^### What the tree does not yet do$' PLAN.md && git diff --cached --numstat --merge-base main -- AGENTS.md | awk '$2 != 0 {bad=1} END {exit bad}' && awk '/Scheduled to change [(]2026-11-1[0-9][)]/ {n++} END {exit !(n>=3)}' AGENTS.md && grep -q 'spec/plan-the-bundle-loop' issues/namespace-sync-redesign.md && grep -q 'spec/plan-the-bundle-loop' issues/plan-md-reconciliation.md && grep -q 'spec/plan-the-bundle-loop' issues/archive-spec-plumbing.md && grep -q 'spec/plan-the-bundle-loop' issues/sharded-archive-sources.md && grep -q 'spec/plan-the-bundle-loop' issues/archive-finalization-integration.md && grep -q 'spec/plan-the-bundle-loop' issues/hpc-deployment.md && test -f issues/durable-tier-record.md && grep -q 'issues/durable-tier-record.md' ROADMAP.md && test -f issues/state-store-journal-mode.md && grep -q 'issues/state-store-journal-mode.md' ROADMAP.md && awk '/^-+$/ && length($0)>3 && length($0)==length(prev) {n++; want=NR+2} /^### / {n++; want=NR+2} /^[*]Follows from:[*]/ && NR<=want {t++; want=0} {prev=$0} END {exit !(n>=14 && t==n)}' PLAN.md && git diff --cached --quiet --merge-base main -- src tests tools pyproject.toml uv.lock
review:
  last_reviewed_commit: 16da8b79a562dd2b22be92605cb8b8575609d1a0
  verdict: changes-requested
  blocked_reason: 'review cycle 1: F1 LOW (wal mode claim), F2 LOW triaged fix (Decision 1 not tied to helper/tests), F3 LOW (P1 gate red on final tree)'
  cycle: 1
---

# TECH.md — Redraw the root `PLAN.md` around the user story

The **context engine and finite-state machine** for building this cycle. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/plan-the-bundle-loop/TECH.md`); the
per-phase checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (the cycle plan).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs.

Two documents are named `PLAN.md`. In this file **the root `PLAN.md`** is the repository's design
document, which the phases edit, and every path in a `verify:` that reads `PLAN.md` means that one.
**The cycle plan** is `spec/plan-the-bundle-loop/PLAN.md`.

**Where the evidence was made.** The two prototypes and the state-store check belong to the plan
step in this cycle (the foreman's ruling): their measured results are the evidence the redrawn root
`PLAN.md` cites, and they are in `research/01` to `03`. The build phases write documents. P1 is the
one phase that runs code: it re-runs the kept prototype copies on the helper as landed, because the
GOAL asks for that commit, and commits the record.

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

Added for this cycle:

- **Every phase stages its files before its `verify:`.** Two checks read the index
  (`git diff --cached --merge-base main`). The other checks read the files.
- **The gates check form, not content.** Each `verify:` asserts that a section, a row, a trace line
  or a record line exists, and that nothing outside the cycle's scope changed. None of them reads a
  reason and judges it. The review reads the documents. A phase that makes a gate pass by adding a
  heading with nothing true under it has failed the phase.
- **The redraw keeps the document's voice** (`AGENTS.md` § *Prose and comments*) and keeps what is
  still true, section by section, at the disposition the cycle plan § 2.1 gives it. A carried section
  gains its trace line and nothing else.
- **Public-repository conduct.** No cluster name, hostname, account or person's name in any file or
  commit message. The story's own words are quoted as the GOAL quotes them.
- **Every edit to the root `PLAN.md` re-reads its anchors on the build's own commit.** The branch is
  rebased before the build, and `racing-write-marked-clean` and `local-bundle-loop` land in between;
  a `file:line` or a count copied from the cycle plan is a claim to re-check, not a fact.

---

## Phase P1 — The prototype record, on the helper as landed
**Satisfies:** R8, R9 · **Depends on:** —
**Goal:** `spec/plan-the-bundle-loop/prototype/RECORD.md` states what GOAL R8 and R9 ask for, from a
run of both prototypes on `tools/bundle_loop.py` of this branch after the rebase.

R8 and R9 are **artifact deliverables**: their evidence is a committed file under `spec/`, which the
blind reviewer cannot see. The **orchestrator grades them**, by reading `RECORD.md` against the two
criteria and by running the phase's `verify:`.

- [x] Confirm `tools/bundle_loop.py` and `tests/loop/tap.py` exist on this branch (the helper landed
      and the foreman rebased). If they do not, STOP and report: the phase cannot be met on a copy.
- [x] Point `prototype/rebuild.py` and `prototype/shared.py` at the landed helper and tap (each takes
      their directories from two constants at its top). Change nothing else in them; if the landed
      helper's interface differs from `ae420d1`'s, say what was changed and why in the record.
- [x] Run each from the worktree root with `uv run python …`. Each ends with one `RESULT` line.
- [x] Write `prototype/RECORD.md`. It SHALL contain these lines, each at the start of a line, and the
      prose GOAL R8 and R9 ask for under them:
      `Helper commit: <hash>` (the commit of `main` that holds the helper the run used);
      `Trees equal in names and bytes: yes|no`;
      the requests made to the source, counted by kind, per window (pull, rebuild and write-back,
      verifying fetch);
      `Each process read the oracle's bytes: yes|no`;
      the number of times the bundle's object was fetched from the source, per scenario.
      It also states the plan step's run (helper `ae420d1`, from `research/02` and `research/03`) and
      whether the two runs agree.
- [x] If a result differs from the plan step's in a way that bears on a decision (the trees unequal;
      more than one object write; a process that did not read the oracle's bytes), STOP and report
      before P2: the decisions were signed on the plan step's evidence.
- **Verify:** the frontmatter command. It runs both prototypes and requires each `RESULT` line to
  say yes, then requires the three record lines. It does not check the request counts: the
  orchestrator reads them.
- **Touches:** `spec/plan-the-bundle-loop/prototype/` only.

**Amendment (P1 build, 2026-11-16).**
- *Merged, not rebased.* The branch was pushed before the build, so `main` (`f76bb02`) came in by a
  merge commit, not the rebase this file and the cycle plan § 2.7 assume. Read "the rebased branch"
  in this file as "the branch after that merge". The merge brought the helper (`a0c88f1`) and
  `racing-write-marked-clean` (`7fb6648`); the anchors-on-the-build's-own-commit rule above holds as
  written.
- *The import was adapted.* The landed `tests/loop/tap.py` is a module of the `tests.loop` package,
  and the kept copies' top-level `from tap import Tap` stopped with `ModuleNotFoundError`. Both
  prototypes now import the helper and the tap through `tests.loop`, with the repository root on the
  path; nothing else in them changed. `prototype/RECORD.md` says why and what of the landed helper's
  other changes reaches them (nothing a count reads), and `prototype/README.md` says the same.
- *The run agrees with the plan step's.* No result bears on a signed decision; the three figures that
  moved (object and record sizes by a few bytes, one timing-drawn fetch count, one marker race not
  hit once) are in the record with their cause.
- *Where P2 quotes the story from.* The merge deleted `issues/local-bundle-loop.md`: `f76bb02` retired
  the seed when its cycle landed. The maintainer's statement of 2026-11-11 and his clarifications
  survive in git as `git show a0c88f1:issues/local-bundle-loop.md` (the statement opens at its
  line 25, "The big user-story I want to tell"). P2 quotes the statement from that object, word for
  word, and checks it against the copy in `GOAL.md` § *Problem*; the clarifications there are
  paraphrased, so the object is the source for any that P2 quotes.

## Phase P2 — The story, the settled rulings and the tier model
**Satisfies:** R1, R2, R5 · **Depends on:** P1
**Goal:** the root `PLAN.md` opens with the story, every section names what it follows from, the ten
rulings are recorded as settled, and a builder reads the tier model in one section.

- [x] Frontmatter: `last_updated`; the opening paragraph restated in the story's terms.
- [x] § *The story*, first: the statement of 2026-11-11 as a block quote in the maintainer's words,
      exactly as `GOAL.md` § *Problem* quotes it; the six clarifications; the labels S1–S5 and C1–C6.
- [x] § *Decisions* → `### Settled rulings`: one table row per ruling, first cell the letter A to J,
      then the ruling, the date, the reason, the alternative turned down (cycle plan § 2.2). None is
      worded as open. Ruling G names `issues/namespace-sync-redesign.md` as the owner of the bump.
      Leave the other two subsections of § *Decisions* as headings with their trace lines and one
      sentence each saying which phase fills them.
- [x] § *Vision and scope* and § *Terminology*, rewritten (cycle plan § 2.1 rows 3 and 4). The
      non-goals state Ruling H and Ruling I. The command list names the seven commands
      `uv run terrace --help` prints.
- [x] § *The tier model*, new, with eight subsections headed `### (a) …` to `### (h) …` in the order
      of GOAL R5 (cycle plan § 2.4). (g) cites `spec/plan-the-bundle-loop/prototype/RECORD.md` for
      the fetch count. (f)'s statement of when many bundles and SFTP are built agrees with
      `ROADMAP.md`: read the two entries on this commit.
- [x] A `*Follows from:*` line as the first line under every heading of the root `PLAN.md`, the
      carried sections included (cycle plan § 2.1, last column).
- [x] Sections not yet reached keep their bodies in this phase.
- **Verify:** the frontmatter command: the first section is "The story" and holds the statement's
  opening words; ten ruling rows with a date and no empty cell; the tier model with eight lettered
  subsections; the record cited; a trace line under every heading; nothing outside scope changed.
- **Touches:** `PLAN.md`.

**Amendment (P2 build, 2026-11-16).**
- *Where the story was quoted from.* The statement and the six clarifications in the root `PLAN.md`
  § *The story* are taken from the git object `a0c88f1:issues/local-bundle-loop.md` (lines 25 to 35
  and 39 to 57), since `f76bb02` retired the file. The statement is that object's block quote line
  for line, and equals the copy in `GOAL.md` § *Problem*. The clarifications are the object's text,
  which the seed itself calls a paraphrase, word for word, with two changes of form and none of
  wording: each bullet opens with its label (`**C1.**` to `**C6.**`), and C6's two links point at
  `issues/…` from the root instead of at the seed's siblings. Their link text is unchanged.
- *The two Decisions subsections P3 and P4 fill* carry their exact headings now, so P3's and P4's
  greps find them, each with its trace line and one sentence naming the build phase that fills it.
- *(f) on this commit.* `ROADMAP.md` puts *Sharded archive sources* and *A bundle source over SFTP*
  in § *Queued: the bundle loop, driven locally*, after the entries that make one bundle work end to
  end and before § *Queued: release*; (f) and § *Vision and scope* say so. The carried sentences that
  still say "post-v0.1.0" (§ *Tier protocol*, § *Configuration*, § *Technology decisions*) are P3's,
  by the cycle plan § 2.1.
- *A note for P4.* P3's `verify:` counts table rows that open `| A |` to `| J |` and requires
  exactly ten. The cycle plan § 2.6 draws § *What the tree does not yet do* with rows that open the
  same way (`| A |`, `| B, C |`, …); P3's gate does not run after P4, but a row of that list written
  as `| A |` would make a rerun of P3's or P2's ruling check count eleven or more.

## Phase P3 — The decisions of this cycle, the state store, the lifecycle and the remaining sections
**Satisfies:** R3, R4 · **Depends on:** P2
**Goal:** the root `PLAN.md` lists what this cycle decided in one place, states the one assumption
it could not check with the check to run, and every remaining section is at its disposition.

- [x] § *Decisions* → `### Decided in this cycle, for the maintainer's review`: Decisions 1 to 7 as
      signed at the PLAN/TECH gate (cycle plan § 2.3). Each is one paragraph opening
      `**Decision N — <question>.**` and carrying `*Reason.*`, `*Turned down.*` and `*Evidence.*`.
      Decisions 2, 3 and 4 each cite the run in `prototype/RECORD.md` in which the trees were equal.
      Any decision the build finds it must add goes in the same list in the same form, and is named
      in the build report.
- [x] § *Architecture* → *State store* → `### The state store on shared storage` (cycle plan § 2.5):
      the journal mode and the locks assumed; what was run on a local filesystem in this cycle and
      what it showed; the sentence that nothing was run on a network filesystem and the assumption is
      "unverified on a network filesystem"; what changes in the design if it fails there; the check,
      as a fenced block of the commands to type, followed by a line `Holds when the output is:` and a
      fenced block of that output. Copy both blocks from `research/01`, and run the commands once on
      this host before committing them.
- [x] § *Architecture* (the rest), § *Lifecycle and recovery*, § *Configuration*, § *CLI surface*,
      § *Technology decisions*, § *Testing strategy*, § *Risks and mitigations*, § *Phase outline*,
      § *Future work*: each to its disposition (cycle plan § 2.1). A sentence a decision overturns is
      marked with the decision's label and the owning seed, not deleted, where the tree still does
      what the sentence says.
- [x] Reconciliation items 1 to 5 (cycle plan § 2.1, last paragraph): no `fsck`; `umount` takes a
      spec and the row says the pidfile in the state directory is what makes the cross-host guard
      possible; named features with no code behind them marked as intent; no forward `Phase N`
      pointer, dated records kept; each of the four overstated testing bullets names its test path or
      its seed.
- **Verify:** the frontmatter command: the heading; at least six decisions each with the three
  parts; the state-store section with its "unverified" sentence and its "Holds when" line; the three
  reconciliation greps; the P2 checks again.
- **Touches:** `PLAN.md`.

**Amendment (P3 build, 2026-11-16).**
- *The heading stays; the body says signed.* P3's gate greps the heading "Decided in this cycle, for
  the maintainer's review" exactly, so the heading is kept, and the list's opening paragraph says
  the maintainer reviewed and signed all seven on 2026-11-15. P2's paragraph under § *Decisions*,
  which said the decisions "are recorded for his review", now says he signed them, and states the
  marker form below. The two sign-off rulings sit under Decisions 1 and 6 as
  `*Ruled at sign-off (2026-11-15).*`; Decision 1's addition is also in the decision's own text.
- *One marker form for an overturned sentence.* Every sentence a ruling or decision overturns, where
  the tree still does what it says, is kept and followed by `*Overturned by <label>, which <seed>
  builds; the tree still does this.*` (`grep -n 'Overturned by' PLAN.md` lists them). Seeds cited
  that P4 writes: `issues/state-store-journal-mode.md` and `issues/durable-tier-record.md`.
- *The decisions cite the landed run.* Decisions 2, 3 and 4 cite `prototype/RECORD.md`'s runs with
  equal trees; Decision 4 quotes the record file at 913 and 33,616 bytes (this branch's run), not
  the plan step's 914 and 33,604. The record says why they differ.
- *The check was run.* The block of `research/01` § *The check for the maintainer* was extracted
  byte for byte into the root `PLAN.md` and run once on 2026-11-16 on the build host, all four
  commands on one host (the single-host variant), with the project's interpreter first on the path
  and `D` pointed at a scratch directory instead of `$HOME`; it printed the five lines of the output
  block with the host's name in place of `<host>`, and removed its directory.
- *Added beyond the cycle plan's § 2.1 wording*, inside sections it assigns to P3: a subsection
  `### After a kill` in § *Lifecycle and recovery* (Decision 5), with its trace line; and in the
  Python-baseline row, "beta now" became "beta when decided", since the tree runs a final 3.15.
- *Left as carried:* the Risks bullet "3.15 is beta until October" and the spec comment
  `drain_timeout … 0 disables` (owned by `issues/shutdown-rescue-gaps.md`) are not P3's by § 2.1.
- *Notes for P4.* (1) No table row of the root `PLAN.md` may open `| A |` … `| J |` or `| B, C |`:
  P2's and P3's ruling checks count exactly ten such rows. Lead § *What the tree does not yet do*'s
  rows with `Ruling A`, `Decision 1` or the like. (2) The cycle plan § 2.6 says
  `issues/state-store-journal-mode.md` is declined if the maintainer keeps WAL; Decision 6's
  sign-off ruling supersedes that: the seed builds without waiting on the check. (3) The root
  `PLAN.md` now says `issues/hpc-deployment.md` carries the check (§ *The state store on shared
  storage*); P4's item 4 makes that true. (4) For the deferral ledger, P3's text defers: the
  pressure rule for staged bundles (`issues/eviction-one-hop.md`); the recovery walk
  (`issues/audit-command.md`); the check on a network filesystem (`issues/hpc-deployment.md`);
  shortening the replay account (`issues/namespace-sync-redesign.md`); measuring throughput
  across SQLite's options until the project is more mature (Decision 6's ruling, which names no
  seed: P4 gives it one, likely `issues/state-store-journal-mode.md`); how a cut-short rescue is
  recorded (`issues/shutdown-rescue-gaps.md`); a clean copy at a full neighbour and default limits
  (`issues/eviction-one-hop.md`).

## Phase P4 — What the tree does not yet do, `AGENTS.md` notes and rows, the roadmap and the seeds
**Satisfies:** R6, R7 · **Depends on:** P3
**Goal:** a reviewer of a later cycle reads which invariants are scheduled to change and which seed
changes each, and the roadmap and seeds agree with the root `PLAN.md`.

- [x] Root `PLAN.md` § *Decisions* → `### What the tree does not yet do`: one row per ruling or
      decision the code on this commit contradicts, with the owning seed (cycle plan § 2.6). Re-read
      every anchor on this commit: `racing-write-marked-clean` has landed and line numbers moved.
- [x] Write `issues/durable-tier-record.md` from the KB's ISSUE template, `status: unshaped`, and its
      `ROADMAP.md` entry in § *Queued: the bundle loop, driven locally*, before the finalization
      entry. Write any other seed the walk above shows is needed, the same way, and name it in the
      build report (cycle plan § 2.6, "Seeds the cycle writes").
- [x] `AGENTS.md`, additions only: one `**Scheduled to change (2026-11-12).**` note at the end of
      each numbered section the cycle plan § 2.6 lists, and one row per section in § *Known open
      violations*. No existing line of the file changes. The existing § 8 row for
      `issues/shutdown-rescue-gaps.md` is left as it is; the new row carries the ruling.
- [x] Seed and roadmap edits (cycle plan § 2.6, items 1 to 4): `issues/archive-spec-plumbing.md`,
      `issues/sharded-archive-sources.md`, `issues/archive-finalization-integration.md`,
      `issues/namespace-sync-redesign.md` (its draft R1, which of the five), 
      `issues/plan-md-reconciliation.md` (item by item), `issues/hpc-deployment.md` (the check), and
      the `ROADMAP.md` entries of each, with § *Decisions that gate the queue*. Each note is dated
      and cites `spec/plan-the-bundle-loop/` and the root `PLAN.md` section.
- [x] Do not edit `issues/plan-the-bundle-loop.md` or its roadmap entry: `code terrace roadmap`
      retires both when the cycle lands.
- [x] **Deferral ledger.** Walk P1 to P4 and the build's own commits for "left to", "stays with",
      "not here", "follow-up", "unverified" and "known limitation", in prose and list items, not
      headings. Each names an `issues/` file that exists and a `ROADMAP.md` entry. The known ones:
      whether `rich` leaves `pyproject.toml` → `issues/plan-md-reconciliation.md`; the check on a
      network filesystem → `issues/hpc-deployment.md`; shortening the replay account in § *Risks* →
      `issues/namespace-sync-redesign.md`. An unrecorded deferral fails the phase.
- **Verify:** the frontmatter command: the heading; no deleted line in `AGENTS.md`; at least three
  dated notes there; the six seeds each cite the cycle's record; the new seed and its roadmap entry
  exist; the trace check; nothing under `src`, `tests`, `tools`, `pyproject.toml` or `uv.lock`
  changed.
- **Touches:** `PLAN.md`, `AGENTS.md`, `ROADMAP.md`, `issues/`.

**Amendment (P4 build, 2026-11-16).**
- *The date in the notes.* Every note, row and seed note this phase adds is dated 2026-11-16, the
  day it was written, not the cycle plan's 2026-11-12: the decisions were signed on 2026-11-15, so a
  note dated before the sign-off would misstate when the change was scheduled. The `AGENTS.md` form
  is otherwise the plan's: `**Scheduled to change (2026-11-16).**`, the label, the rule quoted, the
  seed. P4's gate reads any date of 2026-11-1x.
- *Five sections, five notes, five rows*, as § 2.6 of the cycle plan lists (§§ 2, 5, 7, 8, 10). Two
  additions beyond the notes and rows, both additions only: a paragraph *Scheduled to change* at the
  end of § *Read before editing*, since that summary restates the overturned rules of §§ 7 and 8; and
  a paragraph before the § *Known open violations* table saying the new rows are not defects. No
  measured figure in `AGENTS.md` counts what this phase added, and none changed.
- *Append-only, not cut.* The cycle plan asks that `issues/namespace-sync-redesign.md`'s draft R1 be
  edited and that `issues/plan-md-reconciliation.md`'s roadmap entry be cut to its residue. Both are
  landed text, which `code-build` corrects only by dated addition: R1 gains a dated sub-note saying
  which of the five decisions the root `PLAN.md` records and which remain the seed's, and every
  roadmap entry touched gains a dated paragraph beneath it. Nothing in `issues/` or `ROADMAP.md` was
  deleted.
- *Seeds and entries beyond § 2.6 items 1 to 4.* The walk found work that moves to five more seeds,
  which GOAL R7.1 requires both sides of to say: `issues/eviction-one-hop.md` (the pressure rule for a
  staged bundle, Decision 1's sign-off ruling), `issues/shutdown-rescue-gaps.md` (Decision 7, the
  tier a soft unmount commits to), `issues/audit-command.md` (Decision 5's check of the durable tier
  against the bundle records), `issues/shared-staged-tier.md` (the signature-cost choice the tier
  model (g) leaves open, and what the sharing prototype did not test) and
  `issues/sftp-inner-location.md` (a source with no ETag). Each gains a dated note and its roadmap
  entry a dated paragraph. Decision 7 has two owners: the rescue's target is
  `issues/shutdown-rescue-gaps.md`'s, and refusing a bundle source's stack with more than one shared
  stage tier is `issues/archive-spec-plumbing.md`'s, whose parser holds the stack rules.
- *Placement of `issues/state-store-journal-mode.md`.* Beside the deployment entry, as § 2.6 places
  it, with the entry saying that by the sign-off ruling it does not wait for the check and can be
  taken earlier. § 2.6's "declined if the maintainer keeps WAL" is superseded by that ruling (P3's
  note 2); the throughput measurement the ruling defers is in that seed's Notes as a later, separate
  concern, not in its draft R-IDs.
- *The prototypes' limits have homes.* The cycle plan § 5 lists what the prototypes did not test and
  says each is named in the root `PLAN.md` where its decision is recorded; P3 did not write them. P4
  names them once, in a paragraph under § *What the tree does not yet do*, each with the seed that
  carries it, and each of those seeds says so.
- *P2's and P3's gates retuned (code-build Step 1.3, the cumulative-gate case; the foreman's ruling).*
  Both ended `git diff --cached --quiet --merge-base main -- src tests tools pyproject.toml uv.lock
  AGENTS.md`, an exact-state clause that requires `AGENTS.md` unchanged and cannot pass once this
  phase adds its notes and rows. Run on P4's staged tree, both were red on that clause alone (every
  other check of each exited 0). Each now ends `git diff --cached --quiet --merge-base main -- src
  tests tools pyproject.toml uv.lock && git diff --cached --numstat --merge-base main -- AGENTS.md |
  awk '$2 != 0 {bad=1} END {exit bad}'`: nothing else changed, and `AGENTS.md` additions only, P4's
  own check. Set with `set_phase.py --verify-file`, read back and byte-compared. All three gates were
  then run once on the final staged tree.
- *Rows lead with their label.* No row of the new table opens `| A |` … `| J |` (P3's note 1); each
  opens `| Ruling A |`, `| Rulings B and C |`, `| Decision 1 |` and so on, so P2's and P3's ruling
  checks still count ten.
- *Deferral ledger.* Walked the build's added lines (P1 to P4) and the four build commits for "left
  to", "stays with", "not here", "follow-up", "unverified", "known limitation", "deferred", "not run",
  "not measured" and "not injected". Every deferral names an `issues/` file that exists and has a
  `ROADMAP.md` entry:

  | Deferral | Home |
  |---|---|
  | whether `rich` leaves `pyproject.toml`; the seed's R7 | `issues/plan-md-reconciliation.md` |
  | the check on a network filesystem (Decisions 5 and 6, § *The state store on shared storage*, § *Risks*) | `issues/hpc-deployment.md` |
  | a like check for the durable tier's filesystem (cycle plan § 5) | `issues/hpc-deployment.md` |
  | measuring throughput across SQLite's options, the cost of a commit there (Decision 6's ruling) | `issues/state-store-journal-mode.md` (Notes) |
  | shortening the replay account in § *Risks* (R8) | `issues/namespace-sync-redesign.md` |
  | by name or by id under a renamed directory (Decision 3, tier model (c)) | `issues/namespace-sync-redesign.md` (decision 4) |
  | the pressure rule for a staged bundle (Decision 1's ruling) | `issues/eviction-one-hop.md` |
  | defaults for the limits; a clean copy at a full neighbour (§ *Eviction*, tier model (d)) | `issues/eviction-one-hop.md` |
  | the recovery walk (Decision 5) | `issues/audit-command.md` |
  | how a cut-short rescue is recorded; `--detach` against a scheduler's SIGTERM | `issues/shutdown-rescue-gaps.md` |
  | the signature's cost to a mount (tier model (g)); more than two mounts | `issues/shared-staged-tier.md` |
  | a source with no ETag | `issues/sftp-inner-location.md`, `issues/shared-staged-tier.md` |
  | the bundle record's format; the two kills argued and not injected (tier model (c)) | `issues/durable-tier-record.md` (its R4) |
  | multipart upload, a source changed between pull and write-back, writes during a rebuild, links, empty directories, ownership | `issues/archive-finalization-integration.md` |
  | the integration lane (§ *Testing strategy*) | `issues/ci-integration-lane.md` |

**Amendment (P4 remediation of review cycle 1, 2026-11-16).** Three LOW findings; P1, P3 and P4 were
reopened (`in_progress`) and are built as one run, since the finding on P3's check and the one on P4's
row share the root `PLAN.md` and the seeds. `review.*` is untouched.
- *F1 (R4), the `wal` mode.* The check's `db()` set `synchronous=FULL` in every mode, so `wal` ran WAL
  with `FULL`, not the tree's WAL with `NORMAL` (`src/terrace/state/db.py:94-95`). Fixed in the check:
  `wal` now sets `synchronous=NORMAL`, the default mode keeps `FULL`. **The block now differs from
  `research/01`'s**, whose script sets `FULL` in both modes; R4's content is intact (assumption, local
  run, what was not run, what changes on failure, the commands, the holding output, the failing-line
  table, the single-host variant), and the default run, which R4 requires, is byte-for-byte what it was.
  The sentence offering `wal` now says what it runs (WAL with `synchronous = NORMAL`; the default is a
  rollback journal with `FULL`), as does `issues/hpc-deployment.md`'s. Re-run on one laptop in scratch
  directories under `.local/f1/`, the block extracted from the root `PLAN.md`: the five lines hold in
  default mode and in `wal` mode (`B1 HOLDS`, `B2 HOLDS, nothing left behind`, nothing left behind), and a
  read-back of `db()` gave `wal 1` (NORMAL) in `wal` mode and `delete 2` (FULL) in the default. Grepped
  the tree outside `spec/` for any other site claiming the mode: the root `PLAN.md` lines 223 and 856
  ("301 of 301 in the tree's journal mode") are measured results of the research run, not statements of
  what the check runs, and stand.
- *F2 (R6), the sites a decision overturns.* Decision 1's row now names the helper's `staging` line
  (`tools/bundle_loop.py:201-204`, the spec it writes and its comment citing the removed example),
  `tests/loop/test_helper.py:76` and `:92`, stage 8's assertion (`tests/loop/stages.py:402`), the
  stages' pinned refusal naming the key (`:82`) and the adapter's staged tests
  (`tests/tier/test_archive.py:365`, `:371`, `:384`); `issues/archive-spec-plumbing.md` gains a dated
  line saying the Decision 1 cycle changes them. Sweep of Decisions 2 to 7 and Rulings A to J over
  `tools/` and `tests/`, each cited line opened at this tree: Ruling A: `tests/tier/test_spec.py:101`,
  `tests/engine/test_evict.py:169`. Rulings B and C: `tests/engine/test_model.py:331`. Ruling E:
  `tests/fuse/test_mount.py:182`, `tests/engine/test_rescue.py:160` (already in the seed's body). Ruling F:
  `tests/test_cli.py:43` (already in the seed). Ruling G: `tests/engine/test_replay.py:1085`, `:1100`.
  Ruling I: `tests/state/test_registry.py:136`, `tests/state/test_lifecycle.py:133`. Ruling J: none (the
  tests set absolute values; none asserts an empty or relative one). Decisions 2 and 3:
  `tests/tier/test_archive.py:260`, `:312`. Decision 4: none (the `archive_index` tests assert the
  overlay's own cache, which the record does not replace). Decision 5: `tests/state/test_lifecycle.py:67`,
  `tests/state/test_survival.py:101`, `tests/engine/test_namespace.py:46`, `tests/test_cli.py:43` (the
  seed already names them). Decision 6: `tests/state/test_db.py:35-39`, `tests/state/test_survival.py:86`.
  Decision 7: none (the helper writes one shared stage tier; no test asserts several). Each found site is
  in its row and in the owning seed, with a dated line where the seed did not already name it. Nothing
  under `src/`, `tests/` or `tools/` changed.
- *F3, P1's gate retuned (code-build Step 1.3, the cumulative-gate case).* Only its last clause changed:
  `git diff --cached --quiet --merge-base main -- src tests tools pyproject.toml uv.lock AGENTS.md`
  became `git diff --cached --quiet --merge-base main -- src tests tools pyproject.toml uv.lock && git diff
  --cached --numstat --merge-base main -- AGENTS.md | awk '$2 != 0 {bad=1} END {exit bad}'`, as P2's and
  P3's were. Set with `--verify-file`, read back and byte-compared (701 bytes, equal). Run once, on the
  final staged tree; P2, P3 and P4's gates each ran once on it as well, and each distinct step ran once
  per gate that carries it.

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
