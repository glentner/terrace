# GOAL — Redraw `PLAN.md` around the user story

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** plan-the-bundle-loop
- **kind:** docs
- **appetite:** big

In this file a bare `PLAN.md` is the repository's design document at the root. This cycle's own
plan is `spec/plan-the-bundle-loop/PLAN.md` and is always written with its path.

## Problem

`PLAN.md` and `AGENTS.md` § *Invariants* describe a different product from the one the maintainer
described on 2026-11-11, and every cycle is graded against them. `PLAN.md` outranks the code on
intent, and a review grades a violation of a numbered invariant CRITICAL, so a cycle that builds
toward the story today builds against its own contract. Nothing in the code is broken by this: the
documents predate the story, which had not been told in one place before that date. The plan-drift
rule already says what follows: a design that moves is reconciled in `PLAN.md` first, with the
maintainer.

**The story, as the maintainer told it on 2026-11-11:**

> The big user-story I want to tell is that we define a TOML config that points to S3/SFTP tape or
> object store which contains either a single bundle or a multi-bundle (think 10k tar.gz from full
> imagenet), and the config defines a cascade down to Lustre Scratch where the bundles would be
> lazily pulled and decompressed (but not unpacked in full yet), the node-local /dev/shm lands single
> files from that .tar bundles as necessary, and writes them back up to node-local /tmp, and then
> back up to Lustre Scratch along-side the .tar sources. As changes landed to working copies in
> /dev/shm, those altered files either due to eviction or write-back delay get committed back up the
> stack. The source bundles and altered versions of files alongside them on Scratch represent durable
> storage (if a job halts we're okay and can recover). The source bundles are repacked either due to
> quota (eviction) or write-back delay themselves and put back to the source over S3/SFTP. A 'commit'
> flushes everything back up the stack.

**His clarifications of the same date**, paraphrased:

- every stage tier has a size limit, a count limit and a write-back delay. Lack of room in a tier
  pushes a copy up the stack, which may cause the same in the tier above; on a read-only workload
  that is the only force. A changed copy left alone for its delay is pushed up as well, whatever the
  tier's room, and that may cascade for the upper tier's own reasons;
- a push goes one tier up. A write-back in the lowest tier never goes "all the way up" to repack the
  bundle;
- a signal, from the user or the batch scheduler, commits what has changed up to the lowest durable
  tier, so the mount can stop and the node be released. Later, from a login node, a full commit
  rebuilds the bundles that changed, and does not delete the staged bundles, so the next mount does
  not pull them again;
- a clean copy pushed out of a tier is kept one tier up when that tier has room, so a second read
  comes from node-local disk and not from the bundle. The database tracks every copy and knows where
  the fastest one is;
- a soft unmount, asked for directly or by a signal, ends with the database recording every modified
  file on the first durable tier and nothing on any tier below it;
- everything lives in the one database, in a state directory on durable storage every host sees; on
  his clusters, the home filesystem. Many writers on one overlay do not work with that and are for
  another day. A top durable tier shared read-only between mounts may come first.

Both are copied from `issues/local-bundle-loop.md`, a seed that is retired when its cycle lands.

Where the documents and the tree differ from the story. Each anchor was re-read at `0967d94`.

| The story | `PLAN.md`, `AGENTS.md` and the tree today |
|---|---|
| Pressure or an expired delay moves a copy one tier up, and no further | A dirty copy under pressure is committed "to the *source*, not one hop" (`AGENTS.md` § 7; `commit_upstream`, `src/terrace/engine/evict.py:151`), and a clean one is dropped (`evict_copy`, `src/terrace/engine/overlay.py:1430`). `PLAN.md` § *Overlay engine* → Eviction has dirty copies "committed upstream first" and clean ones removed |
| The bundles and the altered files on scratch are the durable set; a halted job recovers from them | The state database is authoritative for every managed location (`PLAN.md` § *Terminology*). A mount that finds `mounted` stops (`AGENTS.md` § 8), and the audit that would recover does not exist (§ *Known open violations*) |
| A signal commits what changed to the durable tier and releases the node | After a complete rescue the database still records copies on the node-scope tiers, and a mount on another node is refused (§ *Known open violations*; `issues/shutdown-rescue-gaps.md`) |
| A commit from a login node rebuilds what changed | Agrees, given a state directory both hosts see. The database opens in WAL mode (`src/terrace/state/db.py:94`; `AGENTS.md` § 2), which is unverified on such a filesystem |
| A bundle is repacked on its own delay, under pressure on scratch, or at a commit | The adapter re-packs on every call (`_repack`, `src/terrace/tier/archive.py:529`). `resync`, the one call that pushes a bundle to its source, has no caller under `src/terrace/engine/`. The working copy sits at a path of its own (`staging`, `PLAN.md` § *Configuration*), outside every tier and every quota |
| A change inside a bundle | Queued and replayed against the source one operation at a time (`AGENTS.md` § 5; `PLAN.md` § *Overlay engine* → Deferred-op replay) |
| One bundle or ten thousand, on an object store or on tape over SFTP | Sharded sources and SFTP are "post-v0.1.0" (`PLAN.md` § *Technology decisions*, Archives) |
| The same spec on many nodes later, and a shared read-only tier sooner | Multi-client coherence is the first explicit non-goal (`PLAN.md` § *Vision and scope*), and a second overlay reads a tier's marker as foreign (`src/terrace/state/lifecycle.py:198`) |

The cost is already on record. Between adoption and 2026-11-11 every engine cycle worked on
deferred namespace replay against a POSIX source (`ROADMAP.md`), a mechanism a bundle source cannot
use, because a bundle has no rename, no unlink and no mkdir (`issues/namespace-sync-redesign.md`).
This cycle writes the right contract before the next line of engine code.

## Outcome / vision

`PLAN.md` opens with the user story, and each section under it names the sentence or the decision
it follows from. A builder reads there what each tier holds, what moves a copy and how far, what the
durable tier holds, what a rebuild reads, what a signal does, and what sharing a staged tier between
read-only mounts needs. A reviewer grades a later cycle against that, and reads in `AGENTS.md` which
invariants are scheduled to change and which seed changes each. The maintainer finds in one place
the decisions this cycle made without him, and the one assumption it could not check, with the check
to run.

The cycle commits four things: the redrawn `PLAN.md`; dated notes and rows in `AGENTS.md`; the
roadmap and seed edits that follow; and a record under `spec/plan-the-bundle-loop/` of two
prototypes and what they measured. It writes no engine code.

## Acceptance criteria (the contract)

The deliverables are documents, so R1 to R7 are met by committed text and graded by reading it.
Each says what a document SHALL state, not that it be good. R8 and R9 are measured.

Terms used below. **The story:** the statement and the six clarifications quoted in § *Problem*.
**Durable tier:** the maintainer's "lowest durable tier", which is shared scratch in the story;
R5 (a) asks `PLAN.md` for the rule in any stack. **Base commit:** `0967d94`. **Final commit:** the
last commit of the cycle branch before it lands.

- **R1** — **The story.** `PLAN.md` SHALL open with the story: the maintainer's statement of 2026-11-11
  in his words, and his six clarifications. Every section of `PLAN.md` after it SHALL name the
  sentence of the story, or the recorded decision, it follows from.
- **R2** — **What is already ruled.** `PLAN.md` SHALL record each ruling below as settled, with its
  date, its reason and the alternative turned down, and SHALL present none of them as open. Its
  non-goals SHALL state (h) and (i). The maintainer ruled each on 2026-11-11.
  - **(a)** Every stage tier has a size limit, a count limit and a write-back delay.
  - **(b)** Pressure or an expired delay moves a copy one tier up and no further.
  - **(c)** A clean copy pushed out of a tier is kept one tier up when that tier has room.
  - **(d)** One database holds everything. It sits in the overlay's state directory, on durable storage
    every host of the workflow sees, and outside every tier.
  - **(e)** A soft unmount, asked for directly or by a signal, commits every modified file to the first
    durable tier and halts. The database then records every modified file there and nothing on any
    tier below it.
  - **(f)** A commit run from another host rebuilds the bundles that changed and leaves the staged
    bundles in place.
  - **(g)** Row ids are never reused, by a schema version bump taken before any state database is
    deployed. `PLAN.md` SHALL name the seed that owns the bump.
  - **(h)** Many writers on one overlay are not designed for here, and are scoped when reached.
  - **(i)** A top durable tier shared read-only between mounts, each with tiers of its own below, is
    wanted sooner than many writers.
  - **(j)** The names (Terrace, `terrace`, `TERRACE_`, `.terrace/`), and configuration and per-user
    paths that follow the XDG convention.
- **R3** — **What this cycle decides.** `PLAN.md` SHALL record a decision on each question below, with
  its reason, the alternative turned down, and the evidence it rests on: a prototype's measured
  result (R8, R9), a check run in this cycle (R4), or a cited source. It SHALL list all of them in
  one place, headed "Decided in this cycle, for the maintainer's review". A decision the cycle adds
  to `PLAN.md` beyond these six SHALL be recorded in the same form and appear in the same list.
  - **(a)** Where a staged bundle lives. The starting position, from the story: inside the first shared
    stage tier, beside the altered files, and counted by that tier's limits. The decision SHALL say
    what becomes of the `staging` key.
  - **(b)** How a source catches up. The position to confirm or overturn with evidence: by a rebuild
    from recorded state and one object write, not by replaying operations.
  - **(c)** What a rename inside a bundle is, a directory rename included.
  - **(d)** How a removal is recorded on the durable tier.
  - **(e)** What a mount does after a kill.
  - **(f)** Which SQLite journal mode the design assumes.
- **R4** — **The assumption this cycle cannot check.** No network filesystem is available to the cycle.
  `PLAN.md` SHALL state: the journal mode and the locks the design assumes of the filesystem that
  holds the state directory; what was run on a local filesystem in this cycle and what it showed;
  that nothing was run on a network filesystem and the assumption is unverified there; what in the
  design changes if the assumption fails there; and the exact check for the maintainer to run on
  the filesystem that will hold the state directory, as the commands he would type and the output
  that means the assumption holds.
- **R5** — **The model a builder reads.** `PLAN.md` SHALL state each of these.
  - **(a)** What each tier of the story's stack holds: the source, the durable tier and the node-local
    tiers. Which tier of a stack is the durable tier.
  - **(b)** What the durable tier holds: the staged bundles, the altered files, and whatever R3 (d)
    adds.
  - **(c)** Which steps change the durable tier, and why each is atomic.
  - **(d)** What the durable tier's limits count.
  - **(e)** What a rebuild of a bundle reads, and what triggers one: the bundle's own delay, pressure
    on the durable tier, and a commit.
  - **(f)** That a source holds one bundle or many, and where a source reached over SFTP sits in the
    model. Any statement of when either is built SHALL agree with `ROADMAP.md`.
  - **(g)** What a top durable tier shared read-only between mounts needs from the tier's marker, the
    bundle's member index and the pull.
  - **(h)** What the durable tier records about itself in its own files, and what a mount's database
    holds given that.
- **R6** — **What the tree does not yet do.** `PLAN.md` SHALL list each decision of R2 and R3 that the
  code on the final commit contradicts, with the seed that owns the change. WHERE no seed owns one,
  the cycle SHALL write the seed and its `ROADMAP.md` entry. For each numbered section of
  `AGENTS.md` § *Invariants* that such a decision overturns: the section's rules SHALL NOT be
  reworded to the new rule in this cycle; the section SHALL gain a dated note naming the decision
  that overturns it and the seed that owns the change; and § *Known open violations* SHALL carry a
  row for it naming that seed. § 7 is one such section: R2 (b) overturns its rule that a dirty copy
  is committed "to the *source*, not one hop".
- **R7** — **The roadmap and the seeds.** On the final commit:
  1. WHERE a decision in `PLAN.md` moves work from one seed to another, both seeds and their
     `ROADMAP.md` entries SHALL say so.
  2. `issues/namespace-sync-redesign.md` names this cycle as the writer of the five decisions in
     its draft R1. That seed SHALL say which of the five `PLAN.md` now records and which remain its
     own.
  3. For each passage that items 1 to 5 of `issues/plan-md-reconciliation.md` cite: IF it lies in a
     section of `PLAN.md` this cycle rewrites, THEN the rewritten section SHALL meet that seed's
     draft criterion for the item. That seed SHALL gain a dated note saying, item by item, which
     this cycle resolved and which it leaves.
- **R8** — **Prototype: one bundle rebuilt.** On the local loop's helper, a throwaway prototype SHALL
  take one bundle from the helper's bucket; change one member, create one, rename one, remove one,
  and rename one directory; apply the same changes to a plain directory that holds the bundle's
  unpacked tree, which is the oracle; rebuild the bundle; and write it back to the bucket. The
  record under `spec/plan-the-bundle-loop/` SHALL state what was run and on which commit of the
  helper, whether the unpacked tree of the object written back equalled the oracle's in names and
  bytes, and the requests made to the source, counted by kind. The decisions of R3 (b), (c) and (d)
  SHALL each cite a run in which the trees were equal.
- **R9** — **Prototype: two read-only mounts, one durable tier.** On the same helper, a throwaway
  prototype SHALL run two processes, each standing for a read-only mount with state of its own, over
  one shared durable tier and one bundle, and each SHALL read members of that bundle. The record
  under `spec/plan-the-bundle-loop/` SHALL state what was run and on which commit of the helper,
  whether each process read the oracle's bytes, and how many times the bundle's object was fetched
  from the source. R5 (g) SHALL cite that result.

## Non-goals (no-gos)

- **Engine code.** Nothing under `src/` or `tests/` changes, and no schema changes.
- **The schema bump itself.** R2 (g) records the rule; the seed `PLAN.md` names owns the bump.
- **A design for many writers on one overlay** (`issues/multi-node-mounts.md`).
- **A design for SFTP** beyond its place in the model (`issues/sftp-inner-location.md`).
- **A release.**
- **Reopening a ruling listed in R2.**
- **Rewriting an overturned invariant to the new rule.** The code still implements the old one and
  reviews grade code against the section, so it gains a note and a row (R6) and keeps its text. The
  cycle that changes the code changes the section.
- **Keeping the prototypes as code.** They are thrown away. A copy kept under
  `spec/plan-the-bundle-loop/` as part of the record is allowed and is not a deliverable. Nothing of
  them lands under `tools/`, `src/` or `tests/`.
- **Checking SQLite on a network filesystem.** No remote machine is available to the cycle. R4
  leaves the check written out for the maintainer.
- **Any change to `pyproject.toml` or `uv.lock`.** If the cycle resolves item 3 of
  `issues/plan-md-reconciliation.md`, it does so in `PLAN.md` alone.
- **The items of `issues/plan-md-reconciliation.md` that lie in sections this cycle does not
  rewrite.** They stay with that seed (R7).
- **The designs the owning seeds hold.** This cycle writes the contract. Each seed R6 names still
  shapes and builds its own change.

## Clarifications

Rulings made before shaping. Each is resolved 2026-11-12.

- **Q:** What does the cycle deliver? — **A:** A redrawn `PLAN.md`; the `AGENTS.md` changes the
  seed's draft R8 names; the roadmap and seed edits that follow from a decision that moves work
  between seeds; and a record under `spec/plan-the-bundle-loop/` of the two prototypes and what they
  measured. No engine code, no schema change, nothing under `src/` or `tests/`. The prototypes' code
  is thrown away; a copy kept under `spec/` as a record is allowed and is not a deliverable. (The
  foreman's ruling; resolved 2026-11-12.)
- **Q:** Which decisions are already made? — **A:** Those the maintainer ruled on 2026-11-11, taken
  from the seeds and listed in R2, the names and the XDG convention among them
  (`spec/rename-to-terrace/GOAL.md` → Clarifications). `PLAN.md` records each with its reason, and
  none is reopened. (The rulings are the maintainer's, of 2026-11-11. That they are recorded and not
  reopened is the foreman's ruling; resolved 2026-11-12.)
- **Q:** Which decisions does this cycle make? — **A:** The six of R3, which this GOAL names as open
  for the plan to settle with evidence. `PLAN.md` lists them in one place as decided in this cycle,
  for the maintainer's review. The story puts the bundles on the shared scratch tier beside the
  altered files, so the starting position for a staged bundle is inside the first shared stage tier,
  counted by its limits, and the plan decides what becomes of the `staging` key. (The foreman's
  ruling; resolved 2026-11-12.)
- **Q:** The seed's draft R3 asks for the journal mode and the locks on a network filesystem,
  "verified on one". Can the cycle verify that? — **A:** No. SQLite's locks and journal mode on a
  network filesystem cannot be verified in this cycle, because no remote machine is available to it.
  `PLAN.md` states the assumption, what changes if it fails, and the exact check for the maintainer
  to run on the filesystem that will hold the state directory. "Verified on one" becomes "verified
  on a local filesystem, and recorded as unverified on a network filesystem". (The foreman's ruling;
  resolved 2026-11-12.)
- **Q:** Does the cycle rewrite an invariant that a decision overturns? — **A:** No. A section of
  `AGENTS.md` § *Invariants* that a decision overturns is not rewritten to the new rule while the
  code still implements the old one, because reviews grade code against it. It gains a dated note
  saying which decision overturns it and which seed owns the change, and § *Known open violations*
  gains a row, so a later cycle is graded against the new rule. (The foreman's ruling; resolved
  2026-11-12.)
- **Q:** In what form is a decision recorded, and what justifies a section? — **A:** Every decision
  in `PLAN.md` is recorded with its reason and the alternative turned down, as the seed says. Every
  section of the redrawn `PLAN.md` traces to a sentence of the story or to a recorded decision. (The
  foreman's ruling; resolved 2026-11-12.)
- **Q:** How much of `issues/plan-md-reconciliation.md` does this cycle take? — **A:** The parts
  that fall in sections it rewrites. It says which items it leaves to that seed. (The foreman's
  ruling; resolved 2026-11-12.)
- **Q:** What is out of scope? — **A:** Engine code; the schema bump itself; a design for many
  writers; a design for SFTP beyond its place in the model; a release. (The foreman's ruling;
  resolved 2026-11-12.)
- **Q:** How are the criteria graded? — **A:** The deliverables are documents, so most criteria are
  met by committed documents and graded by reading them. Each is written so a reader can check it:
  what `PLAN.md` SHALL state, not that it be good. The two prototypes are the exception: each has a
  measured result against a plain-directory oracle. (The foreman's ruling; resolved 2026-11-12.)
- **Q:** The prototypes run on the local loop's helper, which is not on the base commit. Where does
  it come from? — **A:** Two cycles are in build as this GOAL is shaped, and both land before this
  cycle is planned: `local-bundle-loop`, a helper under `tools/` that stands the bundle loop up on a
  laptop and a nine-stage scenario under `tests/loop/` whose stages are strict expected failures;
  and `racing-write-marked-clean`, a fix in the engine's commit path. The prototypes run on that
  helper as landed. (The foreman's statement; resolved 2026-11-12.)

## Related materials

- Seed: [`issues/plan-the-bundle-loop.md`](../../issues/plan-the-bundle-loop.md). Its draft R1 to R9
  are regrouped here by what is ruled and what is open. Draft R1 is R1. Draft R2 is R2 (a) to (c).
  Draft R3 is R2 (d), R3 (a) and (f), R4 and R5 (b) to (d). Draft R4 is R3 (b) to (d) and R5 (e).
  Draft R5 is R2 (e) and (f) and R3 (e). Draft R6 is R2 (g) and R5 (h). Draft R7 is R2 (h) and (i)
  and R5 (g). Draft R8 is R6 and R7. Draft R9 is R8 and R9.
- The story and the clarifications: `issues/local-bundle-loop.md`, until its cycle lands and the
  seed is retired. § *Problem* holds the copy this cycle works from.
- The rulings of 2026-11-11, by seed: `issues/eviction-one-hop.md` (R2 a to c, with the maintainer's
  reasons in his words); the seed's own Notes (R2 d, and the alternative turned down);
  `issues/shutdown-rescue-gaps.md`, notes of 2026-11-11 (R2 e, with a reproduction);
  `issues/namespace-sync-redesign.md`, draft R1 (R2 g, and the state-based model behind R3 b);
  `issues/multi-node-mounts.md` (R2 h); `issues/shared-staged-tier.md` (R2 i, R5 g, R9);
  `spec/rename-to-terrace/GOAL.md` → Clarifications (R2 j).
- Open questions the seeds record that bear on R3: `issues/archive-spec-plumbing.md`, note of
  2026-11-11 (the `staging` key, R3 a); `issues/namespace-sync-redesign.md`, note of 2026-11-11
  (stage trees a person can read by name); `issues/eviction-one-hop.md`, Notes (optional limits, a
  full neighbour).
- The check R4 leaves for the maintainer belongs with `ROADMAP.md` § *Decisions that gate the
  queue* → Feasibility on a real cluster, and `issues/hpc-deployment.md`.
- `issues/plan-md-reconciliation.md`: the five items R7 walks.
- `PLAN.md`: every section. `AGENTS.md` § *Invariants* § 2, § 5, § 7, § 8, § *Known open
  violations*, and § *Conventions* → Same-commit rule, which says what a decision that overturns an
  invariant owes.
- Prior art the seed cites, as input to R3 (c) and (d). The kernel's overlay filesystem records a
  removal as a whiteout in its upper directory, and by default fails a rename of a lower-layer
  directory with `EXDEV`, so `mv` falls back to a copy:
  <https://docs.kernel.org/filesystems/overlayfs.html>. `ratarmount` has a write overlay that tracks
  deletions and applies them with a separate command, and does not commit to a compressed archive:
  <https://github.com/mxmlnkn/ratarmount>. SQLite on WAL and network filesystems, for R3 (f) and
  R4: <https://sqlite.org/wal.html>.
