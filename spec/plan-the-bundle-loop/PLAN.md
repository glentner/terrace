# PLAN — Redraw the root `PLAN.md` around the user story

> **Status:** Draft for review · **Last updated:** 2026-11-12
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

Two documents are named `PLAN.md`. In this file **the root `PLAN.md`** is the repository's design
document, the deliverable. **The cycle plan** is this file.

## 1. Summary

The cycle redraws the root `PLAN.md` in place. It gains three new sections at the top (the story, the
decisions, the tier model), keeps every section that is still true, and marks each passage a decision
overturns with the decision and the seed that owns the change. The six decisions the GOAL leaves open
are proposed in § 2.3 below for sign-off at this gate, each on evidence the plan step measured: two
prototypes run on the local loop's helper against a plain-directory oracle, and a check of SQLite's
journal modes on a local filesystem. The build then writes documents in four phases; the first re-runs
both prototypes on the helper as landed and commits the record the later phases cite.

## 2. Design

### 2.1 The shape of the redrawn root `PLAN.md`

It is a redraw, not a new document. Each section of today's root `PLAN.md` (660 lines at `7e36368`)
gets one of three dispositions:

- **rewritten**: the body is replaced, because a ruling or a decision changes what it says;
- **amended**: the body is kept, sentences a decision overturns are marked with that decision and the
  owning seed, and what the story adds is added;
- **carried**: the body is unchanged.

Every section, whatever its disposition, opens with one line of the form `*Follows from:* …` naming
the sentences of the story or the decisions it rests on (GOAL R1; the shaper's point that a carried
section needs a trace too). The story's sentences and the decisions get stable labels in the root
`PLAN.md` so a trace can name them: **S1–S5** for the five sentences of the maintainer's statement,
**C1–C6** for his six clarifications, **Ruling A–J** for the ten settled rulings (GOAL R2 a–j, in
that order), and **Decision 1–7** for what this cycle decides (GOAL R3 a–f in that order, then one
the redraw adds). GOAL R-IDs do not appear in the root `PLAN.md`: they restart per cycle.

| # | Section of the redrawn root `PLAN.md` | Disposition | Carries | Trace |
|---|---|---|---|---|
| 0 | Frontmatter and the one-paragraph description | rewritten | `last_updated`, what the product is in the story's terms | — |
| 1 | **The story** (new, first) | new | R1: the statement of 2026-11-11 in the maintainer's words as a block quote, the six clarifications, and the labels S1–S5, C1–C6 | is the root |
| 2 | **Decisions** (new) → *Settled rulings* | new | R2: a ten-row table, Ruling A–J, each with date, reason, alternative turned down. Ruling G names `issues/namespace-sync-redesign.md` as the owner of the schema bump | C1–C6 |
| 2 | → *Decided in this cycle, for the maintainer's review* | new | R3: Decision 1–7 in one list, each with reason, alternative turned down, evidence | S1–S5, the rulings |
| 2 | → *What the tree does not yet do* | new | R6: one row per ruling or decision the code contradicts, with the owning seed and the `AGENTS.md` section it overturns, if any | the two lists above |
| 3 | Vision and scope | rewritten | In scope by the story; non-goals state Ruling H and Ruling I; the command list names the seven commands `terrace --help` prints (reconciliation item 1) | S1, Rulings H, I |
| 4 | Terminology | rewritten | Adds durable tier, staged bundle, altered file, bundle record, push, rebuild, soft unmount; keeps the terms still true; "eviction" and "commit" restated to Rulings B, C, F | S1–S5, Rulings B–F |
| 5 | **The tier model** (new) | new | R5 (a)–(h), one subsection each (§ 2.4 below) | S1–S5, C1–C6, Decisions 1–4, 7 |
| 6 | Architecture → Tier protocol | amended | The protocol sketch and the POSIX and S3 adapters are carried. The archive adapter's "staged re-sync" mode is restated to Decision 1 and 2; "sharded sources (planned post-v0.1.0)" is corrected to agree with `ROADMAP.md` | S1, S4, Decisions 1, 2 |
| 6 | → Overlay engine | amended | Read path, write path, concurrency carried. Write-back: carried, with the one-hop rule stated. Deferred-op replay: kept as the record of what the tree does for a POSIX source, marked as overturned for a bundle source by Decisions 2 and 3, owner `issues/namespace-sync-redesign.md`. Eviction: rewritten to Rulings A–C | S2, C1, C2, C4, Rulings A–C, Decisions 2, 3 |
| 6 | → State store | amended | Tables and the two write-ordering invariants carried. Adds **The state store on shared storage** (R4, § 2.5 below) and Ruling D, G | C6, Rulings D, G, Decision 6 |
| 6 | → FUSE adapter, Control channel | carried | — | S1 (the mount), Ruling D (the state directory) |
| 7 | Lifecycle and recovery | rewritten | Shutdown table to Ruling E (a soft unmount is today's detach, finished); commit to Ruling F; after a kill to Decision 5; the audit kept, re-scoped to what Decision 4 and 5 give it | S3, S5, C3, C5, Rulings E, F, Decisions 4, 5 |
| 8 | Configuration | amended | The POSIX example carried. Archive examples to Decision 1 (the `staging` key) and Ruling A (limits and a delay on every stage tier) | S1, C1, Ruling A, J, Decision 1 |
| 9 | CLI surface | amended | `umount` takes a spec file and says why (reconciliation item 2); `umount`, `commit` rows to Rulings E, F | C3, C5, Rulings E, F |
| 10 | Technology decisions | amended | Archives row and State store row to Decisions 2, 6 and to `ROADMAP.md`; the Python-baseline row marks named features with no code behind them as intent (reconciliation item 3, in the root `PLAN.md` alone) | the decisions each row names |
| 11 | Testing strategy | amended | Adds the bundle-loop scenario and the two prototypes' oracle. Each of the four overstated bullets names its test path or is marked unbuilt with its seed (reconciliation item 5) | S3 (recovery), the decisions each bullet tests |
| 12 | Risks and mitigations | amended | Adds: the state-store assumption is unverified on a network filesystem; a staged bundle is counted at its uncompressed size. Forward `Phase N` pointers become seed names (reconciliation item 4). The replay bullet is kept and marked as the record of a mechanism Decisions 2 and 3 retire; shortening it stays with `issues/namespace-sync-redesign.md` (its draft R8) | the decisions each risk names |
| 13 | Phase outline | rewritten | Becomes a dated record of the phases that were built, and a pointer to `ROADMAP.md` as the ordered index of seeded cycles; no `wip` branch, no forward phase number (reconciliation item 4) | Ruling J (names), `ROADMAP.md` |
| 14 | Future work | amended | The multi-client line is restated to Rulings H and I; the forward phase pointer goes | Rulings H, I |

For GOAL R7.3 a section counts as rewritten when its body changes at all, so *rewritten* and
*amended* both bind. On that reading every passage that items 1 to 5 of
`issues/plan-md-reconciliation.md` cite lies in a section this cycle changes, and the redraw meets
that seed's draft criterion for each. What it leaves to that seed: whether `rich` leaves
`pyproject.toml` (the GOAL forbids the change here), that seed's draft R7, and the residue of its
2026-11-11 note if the namespace rebuild is declined.

### 2.2 The settled rulings (GOAL R2)

The root `PLAN.md` records Rulings A–J as settled, dated 2026-11-11, none presented as open. The
reason and the alternative turned down come from the seed that holds each ruling, in the maintainer's
words where the seed has them:

| Ruling | From | Alternative turned down, as the root `PLAN.md` will record it |
|---|---|---|
| A limits and a delay on every stage tier | `issues/eviction-one-hop.md` | tiers with optional limits, which fill their device |
| B one tier up, no further | same; "There is no benefit of using the product if we trigger a repack on every write-back" | commit to the source on eviction (`AGENTS.md` § 7) |
| C a clean copy is kept one tier up when there is room | same; "there's no reason not to use faster tiers … if we have the quota available" | drop it and read again from the bundle |
| D one database, in the state directory, on storage every host sees, outside every tier | `issues/plan-the-bundle-loop.md` Notes | a node-local database while mounted, written to the durable tier at a soft unmount |
| E a soft unmount commits to the first durable tier and halts; nothing recorded below it | `issues/shutdown-rescue-gaps.md`, notes of 2026-11-11 | residency left on node tiers, which refuses the next node's mount (reproduced there) |
| F a commit from another host rebuilds what changed and leaves the staged bundles | the story, C3 | deleting the staged bundles after a commit, which makes the next mount pull again |
| G row ids never reused, by a schema bump before any deployment | `issues/namespace-sync-redesign.md` | machinery that tells a reused id apart (marks, the purge watch) |
| H many writers on one overlay: not designed here | `issues/multi-node-mounts.md` | a database per node negotiated at the durable tier, now |
| I a top durable tier shared read-only between mounts, sooner | `issues/shared-staged-tier.md` | one staged copy of the dataset per mount |
| J the names and the XDG convention | `spec/rename-to-terrace/GOAL.md` → Clarifications | the placeholder name; paths of the project's own invention |

### 2.3 Decided in this cycle, for sign-off at this gate (GOAL R3)

Each is a proposal until the foreman signs it. Evidence is tagged **measured** (a run in this plan
step, in the brief named), **documented** (a cited source) or **read** (argued from the code or a
seed). Runs are on the helper at `ae420d1`; P1 repeats them on the helper as landed (§ 2.7).

**Signed at the PLAN/TECH gate (Geoffrey, 2026-11-15):** all seven, Decision 1 with an addition
and Decision 6 with a ruling on its check, each recorded under its decision.

**Decision 1 — Where a staged bundle lives (R3 a).** Inside the stage tier directly below the source,
which must be a shared tier: the durable tier (Decision 7). It sits in the tier's root beside the
altered files, under a name no entry of the overlay can take, and the tier's limits count it: one
file, at its staged (uncompressed) size. The `staging` key is retired; a spec that carries it is
refused with a message naming the tier that now holds the bundle. This confirms the starting
position.
*Reason.* The story puts the bundles on scratch beside the altered files (S1, S3), and a rebuild
then reads one tier and the database and nothing else.
*Turned down.* A path of its own outside every tier (today's `staging`): the largest thing on the
durable tier, seen by no overlap rule and counted by no limit.
*Evidence.* Measured, `research/02`: the rebuild read only the state database and the durable-tier
directory, in 18 runs with equal trees. At 300 members the staged `.tar` was 6,420,480 bytes against
5,000 bytes of altered files, so an uncounted bundle is nearly all of what the tier holds. Read:
`issues/archive-spec-plumbing.md`, note of 2026-11-11, asks this question.
*Ruled at sign-off (2026-11-15).* Because the staged bundle counts against the tier's limits, it is
evictable: under pressure the tier reclaims staged bundles as it reclaims any copy. By Rulings B and
C, a staged bundle whose source already holds its recorded state is dropped, and the next access
pulls it again with one read; one with changes not yet at the source first catches up by
Decision 2's rebuild and one write, then is dropped with its altered files. The mechanism is built
with the bundle loop; the pressure rule itself stays with `issues/eviction-one-hop.md`.

**Decision 2 — How a source catches up (R3 b).** By a rebuild from recorded state and one object
write. Unchanged members are copied from the staged bundle at their recorded offsets, changed and
created ones come from the altered files, removed ones are left out, renamed ones take their new
names; the result is compressed and replaces the source object in one write. No operation is
replayed against a bundle. After the write the staged bundle is never replaced in place: either it
and its altered files stay as they are, recorded clean and correct only together, or a new staged
bundle is written under a new name and the database switches to it in one transaction. This confirms
the starting position and adds the last sentence.
*Reason.* A bundle has no rename, no unlink and no mkdir; a rebuild has no order to discover.
*Turned down.* Replaying queued operations, which the adapter answers with one whole re-pack each.
*Evidence.* Measured, `research/02`: trees equal in names and bytes at 6 and at 300 members; source
requests 1 read for the pull, 0 for five changes, 1 write for the rebuild, identical in all 18 runs.
Replay of the same five changes computes to 5 whole rewrites, 30,566,400 bytes at 300 members,
against one object of 5,932,471. A kill injected after a rename over the staged name left recorded
offsets pointing into the new file and a wrong view; kills around a new-name switch all left a view
equal to the oracle.

**Decision 3 — What a rename inside a bundle is (R3 c).** A change of recorded state. A member's
recorded name changes; a directory rename changes the recorded prefix of every member under it. No
bytes move for an unchanged member, and nothing is sent to the source until the bundle is rebuilt.
This confirms the starting position. Whether an altered file under a renamed directory is kept on
the durable tier by name or by id is not decided here: it is the namespace seed's decision 4, and
this cycle gives it one measured input.
*Reason.* The rebuild writes every member under its current name whatever its history, so the
history need not exist.
*Turned down.* The overlay filesystem's default, which refuses the rename of a lower-layer directory
so the tool copies every member and removes the old ones.
*Evidence.* Measured, `research/02`, a directory of 150 members: as recorded state, 0 bytes to the
durable tier and 151 rows; as copy and remove, 2,956,800 bytes, 150 files and 452 rows; the rebuilt
trees equal the oracle either way. With an altered file kept by name under the directory, the rename
adds one POSIX rename that is a second step beside the database commit; a kill injected between them
left a row naming a missing file (`ENOENT`). Kept by id there is no such step. Documented: the
kernel's overlay filesystem, cited in the GOAL.

**Decision 4 — How a removal is recorded on the durable tier (R3 d).** In the database at once, as
every change is, and in a **bundle record**: one small file per staged bundle on the durable tier,
written by rename into place, that with the staged bundle and the altered files is enough to rebuild
the bundle without the database. It names what the staged bundle's members are now called, which are
removed, and which altered file holds a member's bytes. It is written when the durable tier is
brought up to date for that bundle (a push into the tier, a soft unmount, before a rebuild), not on
every change. This goes beyond the starting positions, which did not cover it.
*Reason.* The story calls the bundles and the altered files on scratch the durable set a halted job
recovers from (S3). Without a record that set is wrong by exactly the removals and the renames.
*Turned down.* The database alone: the tier cannot stand in for it. Whiteout files: a renamed
unchanged member has no bytes on the tier for a whiteout to sit beside, so whiteouts need a second
kind of marker, moved with every directory rename.
*Evidence.* Measured, `research/02`: with the state database deleted, a rebuild from the tier's
files and the record equalled the oracle; from the tier's files alone it did not (at 6 members, 5
names missing and 6 extra: the removed member back and every renamed member under its old name). The
record cost 914 bytes at 6 members and 33,604 at 300, about 110 bytes a member each time it is
written, which is why it is not written on every change. Whiteouts were argued and not run.

**Decision 5 — What a mount does after a kill (R3 e).** It recovers and mounts; it does not stop. A
mount that finds the last one never settled trusts the database, since a commit acknowledged before
the kill is in it, and checks the tiers against it: a node tier that is gone, or that is another
host's, loses its recorded copies; a modified file whose newest copy was there goes back to the
version the durable tier holds and is named in a loss report; an unresolved intent is rolled back or
finished; the durable tier is checked against the bundle records. Then it serves. A read-only mount
has nothing to lose and recovers the same way with an empty report.
*Reason.* A batch job that reaches its time limit is killed, and the next job on another node must
start. A stop that waits for a person turns the ordinary end of a job into an outage.
*Turned down.* The unconditional stop of `AGENTS.md` § 8, which is what the tree does, and which
exists because the audit that would recover does not.
*Evidence.* Measured, `research/01`: on a local filesystem, every commit a process acknowledged
before `SIGKILL` was read by the next process (301 of 301 in today's journal mode, 300 of 300 in
the assumed one).
Read: `issues/shutdown-rescue-gaps.md` (the next mount refused, reproduced there) and
`issues/read-only-mount.md`. Unverified: the same on a network filesystem (§ 2.5). No prototype of a
recovery was run; the walk is the audit's, and its design stays with `issues/audit-command.md`.

**Decision 6 — Which SQLite journal mode the design assumes (R3 f).** A rollback journal,
`journal_mode = DELETE`, with `synchronous = FULL`; and of the filesystem, locks on the database
file alone (§ 2.5). This departs from the tree, which configures WAL and `NORMAL`.
*Reason.* It is the one mode SQLite's documentation names as mitigating a network filesystem, and it
needs no shared-memory file. `FULL` because a released node is nearer a system crash than an
application crash, and with `NORMAL` a commit is not synced.
*Turned down.* Keeping WAL: SQLite states it "does not work over a network filesystem", for a reason
(shared memory between hosts) this workflow avoids, but no page says a `-shm` file on a network
filesystem works from one host. WAL with `locking_mode = EXCLUSIVE`: a second connection in the same
process is refused, and the engine keeps one connection per thread.
*Evidence.* Documented: `sqlite.org/wal.html` § 1 and § 8, `useovernet.html`, `pragma.html`, quoted in
`research/01`. Measured there on a local filesystem: 300 of 300 acknowledged commits survive a kill;
a second process is refused the write lock; a connection per thread still works and a reader does
not wait on an open write transaction; a commit waits on open reads (1.010 s against a 1.0 s
reader), which WAL never does; the median write transaction is 0.285 ms against 0.022 ms.
Unverified: everything on a network filesystem, the cost of a commit there most of all. The
maintainer's check runs both modes, so keeping WAL stays open to him on evidence from his
filesystem.
*Ruled at sign-off (2026-11-15).* For this workload the safer mode is the right default: the design
enforces `journal_mode = DELETE` with `synchronous = FULL`, and `issues/state-store-journal-mode.md`
builds without waiting on the network-filesystem check. Measuring throughput across SQLite options
is deferred until the project is more mature; the check stays with `issues/hpc-deployment.md` as
§ 2.6 states.

**Decision 7 — Which tier of a stack is the durable tier (added by the redraw; R5 a asks for the
rule).** The lowest tier of the stack whose `scope` is `shared`: the tier a soft unmount commits to.
Over a bundle source a stack has exactly one shared stage tier, directly below the source, so the
tier that holds the staged bundles and the tier a soft unmount commits to are one tier; a spec that
breaks this is refused. Over a POSIX or object-prefix source with no shared stage tier, the durable
tier is the source.
*Reason.* The story has one such tier, and Rulings E and F and Decisions 1, 2 and 4 each say "the
durable tier" as if a stack had one. Over a bundle source the rule makes that true.
*Turned down.* Several shared stage tiers over a bundle source: a rebuild would read altered files
from two tiers and a soft unmount would land beside no bundle. No user has asked for it.
`issues/shared-staged-tier.md` leaves open a private durable tier beneath a tier shared for reading;
that seed reopens this rule if it wants one.
*Evidence.* Read: the story (S1, S3, C3, C5); `issues/multi-node-mounts.md`, which proposes the same
shape as the first rule to test; `AGENTS.md` § 8, where the rescue already "stops at the first
shared tier". Not measured: both prototypes used the story's stack.

### 2.4 The tier model the root `PLAN.md` states (GOAL R5)

The root `PLAN.md` gains § *The tier model*, with one subsection per item of R5, headed
`### (a) …` to `### (h) …`. What each states:

- **(a) What each tier holds, and which is the durable tier.** The source holds the bundles, one
  object or many, compressed; only a rebuild writes it. The durable tier is named by Decision 7; it
  holds what (b) lists. The node-local tiers hold single files: members landed as they are read, and
  working copies. Nothing on them is the only copy of a change for longer than the tier's delay.
- **(b) What the durable tier holds.** Staged bundles, decompressed and not unpacked. Altered files:
  each changed or created file, whole. Per staged bundle, a member index (a function of the bundle,
  written once beside it) and a bundle record (Decision 4). The tier's marker. In passing: the
  temporary names of renames in flight and the spool of a rebuild.
- **(c) Which steps change the durable tier, and why each is atomic.** Staging a pulled bundle
  (index renamed into place first, then the bundle, so a bundle under its staged name always has its
  index). Landing an altered file (temporary name, sync, rename). Writing a bundle record (the
  same). A rebuild: spool, one object write (atomic per key), one transaction that records the
  bundle clean; then, when a new staged bundle is written, a new name and one transaction that
  switches to it before the old one is removed. Dropping a clean staged bundle or a retired altered
  file. Each is bracketed intent, act, confirm, and for each the section says what a kill between
  the act and its record leaves, from the kill points `research/02` injected: an unnamed file to
  discard, never a name that resolves to the wrong bytes. The one step that did leave wrong bytes,
  a rename over the staged name, is forbidden by Decision 2.
- **(d) What the durable tier's limits count.** By size: every staged bundle at its staged size,
  every altered file, the records and indexes, and a spool while it exists. By count: one per staged
  bundle, one per altered file. What cannot leave under pressure: an altered file not yet in a
  written-back bundle, which is the only copy of its bytes. A staged bundle with changes leaves
  only after its rebuild. After a rebuild that wrote no new staged bundle, the old one and its
  altered files are correct only together and leave as one unit. A clean staged bundle is dropped:
  the source, one tier up, holds it (Ruling C needs no special case here). Whether a spec must state
  the limits or gets defaults stays with `issues/eviction-one-hop.md`.
- **(e) What a rebuild reads, and what triggers one.** It reads the database for what the bundle
  should hold and the durable tier for the bytes (Decision 2). Three triggers: the bundle's own
  delay, which is the durable tier's write-back delay counted from the last change that reached the
  tier for that bundle, bounded by `sync.max_frequency`; pressure on the durable tier, when a bundle
  with changes is chosen for reclaim; and a commit, which rebuilds every changed bundle with the
  clocks collapsed. A push into the durable tier is never itself a trigger (Ruling B).
- **(f) One bundle or many, and SFTP.** A source is one object, whose contents are the mount's root,
  or a prefix of many, each one top-level directory named for its stem. The bundle is the unit that
  is pulled, staged, counted, dropped and rebuilt, so ten thousand bundles change nothing in the
  model but the count. A source reached over SFTP is an inner location: where a bundle's object is
  fetched whole and replaced whole. Nothing below the source differs. When each is built is read
  from `ROADMAP.md` on the build's commit and stated to agree with it: today both are in its first
  section, after one bundle works end to end, and neither is "post-v0.1.0".
- **(g) What a top durable tier shared read-only between mounts needs** (Ruling I), from
  `research/03` and citing the P1 record. *The marker:* a kind that says staged bundles shared for
  reading, a format number, and the identity of the source as the spec gives it; no overlay id, tier
  name, generation, writer or time, so that its text is a function of the source and two mounts that
  arrive together write the same bytes. A mount refuses another kind and another source. *The member
  index:* one flat file beside each staged bundle, written by whoever pulled it and renamed into
  place before the bundle; each mount's database keeps a row for the bundle it reads and the copies
  it made below, and no member rows. *The pull:* a staged name that carries the source object's
  signature, so a bundle changed at the source is staged beside the old one and a mount reading the
  old one is not disturbed; fetch to a name unique to the process and rename into place; a lock file
  that only saves a second fetch, lapses at a stated timeout and is removed only by its maker. No
  mount evicts from the tier, counts its limits against it or alters a file in it; reclaiming it is
  an explicit command. What it costs a mount to learn the signature (a request per bundle, one list
  per mount, or none if it trusts what is staged) is stated as an open choice of
  `issues/shared-staged-tier.md`.
- **(h) What the durable tier records about itself, and what a mount's database holds given that.**
  In its own files: the marker (whose tier this is, or that it is shared), each staged bundle under
  a name that says which source object it is, each bundle's index, each bundle's record, and the
  altered files. The database (Ruling D) holds everything that changes: the namespace, every copy on
  every tier and which is fastest, what is modified and waiting, the intents, the lifecycle row. It
  is the authority while it exists. The tier's files are what a rebuild takes its bytes from, what a
  mount after a kill checks the database against (Decision 5), what a second mount reads instead of
  scanning, and what is left to recover from if the database is lost. A record is written when the
  tier is brought up to date, so it may be behind the database and is never ahead of it by more than
  the change being recorded.

### 2.5 The state store on shared storage (GOAL R4)

The root `PLAN.md` gains `### The state store on shared storage` under § *Architecture* → *State
store*. It states five things, in this order, from [`research/01`](research/01-state-store-on-shared-storage.md):

1. **What is assumed** (Decision 6). A rollback journal, `journal_mode = DELETE`, with
   `synchronous = FULL`. Of the filesystem that holds the state directory: POSIX advisory locks on
   the database file that every host of the workflow sees and that die with the process that held
   them, and an `fsync` that reaches the server. The state-directory lock (`flock` on
   `<state>/lock`) is assumed on one host only, as its own docstring says (`state/db.py:162-164`);
   exclusion between hosts rests on the lifecycle row, read and set in one transaction, and on the
   workflow having one host at a time (Ruling D). The check below reports whether `flock` is seen
   across hosts as well; the design does not lean on it.
2. **What was run on a local filesystem in this cycle, and what it showed.** The table of
   `research/01`: on a laptop's local filesystem, SQLite 3.53.1, a process killed with `SIGKILL`
   left every commit it had acknowledged for the next process to read (301 of 301 in today's mode,
   300 of 300 in the assumed one); a second process was refused the write lock while the first held
   it; a reader on its own connection did not wait on an open write transaction in either mode; a
   commit waited 1.010 s on a reader's 1.0 s open transaction in the assumed mode and 0.000 s in
   today's; one write transaction took a median of 0.285 ms in the assumed mode against 0.022 ms in
   today's.
3. **What was not run.** Nothing was run on a network filesystem, and the assumption is unverified
   on a network filesystem: locks seen across hosts, a dead process's locks released, `fsync`
   reaching the server, and the cost of a commit there.
4. **What changes if it fails there.** By the failing line of the check. Locks not seen across
   hosts: the design names the lifecycle row and the one-host-at-a-time workflow as the only guard,
   and a commit from another host refuses to start while the row reads `mounted` on a live host.
   Acknowledged rows missing, integrity failed, or a dead process's locks still held: the database
   cannot stay on that filesystem while mounted. It is then kept node-local while mounted and written
   to shared storage at a soft unmount, the alternative Ruling D turned down, which returns as the
   fallback; a kill then loses the node's database, and recovery reads the durable tier's own files
   (Decision 4 is what makes that possible). The ruling is the maintainer's to revisit on that
   evidence; this cycle does not reopen it.
5. **The check**, as the commands to type on two hosts that see one directory and the output that
   means the assumption holds: the block of `research/01` § *The check for the maintainer* (a
   38-line script written by a here-document, four commands, the five output lines ending
   `B1 HOLDS` and `B2 HOLDS, nothing left behind`), its table of what each differing line means, and
   the single-host variant with what it does not show. The same commands with `wal` appended run
   today's mode, so the maintainer can see whether keeping it is open to him.

`db.py` configures WAL and `synchronous = NORMAL` today (`state/db.py:94-95`), and `AGENTS.md` § 2
and § 10 state properties of WAL. Decision 6 therefore joins the list of what the tree does not yet
do, with a seed of its own (§ 2.6).

### 2.6 `AGENTS.md`, the roadmap and the seeds (GOAL R6, R7)

**`AGENTS.md` changes are additions only.** No line of § *Invariants* is reworded or removed. Each
numbered section a ruling or decision overturns gains one dated note at its end, in a fixed form a
reader can find: `**Scheduled to change (2026-11-12).**`, then the ruling or decision by its label in
the root `PLAN.md`, the rule of the section it overturns quoted in a few words, and the owning seed.
§ *Known open violations* gains one row per such section, naming the seed. Until the owning cycle
lands, a review grades code against the section as written; the row is what tells a reviewer that a
cycle building toward the new rule is not in violation of the plan.

| § | Rule overturned | By | Owning seed |
|---|---|---|---|
| 7 | a dirty copy is committed "to the *source*, not one hop" | Ruling B | `issues/eviction-one-hop.md` |
| 7 | a clean copy under pressure is dropped (first bullet, as `evict_copy` implements it) | Ruling C | `issues/eviction-one-hop.md` |
| 5 | changes reach the source by replaying queued operations at the committed source path | Decisions 2, 3 (for a bundle source); Ruling G retires the reused-id machinery the section narrates | `issues/namespace-sync-redesign.md` |
| 8 | "finding `mounted` at mount time is an unconditional stop" | Decision 5 | `issues/audit-command.md`; its read-only slice `issues/read-only-mount.md` |
| 8 | a soft unmount may leave residency recorded on node tiers (the section is silent; the code does) | Ruling E | `issues/shutdown-rescue-gaps.md` (a row exists and is left as it is; a new row carries the ruling's label) |
| 2 | "WAL, `synchronous = NORMAL` … applied per connection"; "WAL is what makes committed transactions survive `SIGKILL`" | Decision 6 | `issues/state-store-journal-mode.md` (new) |
| 10 | "a WAL reader does not wait on a writer's open transaction", on which the purge-watch read is argued | Decision 6 (in the assumed mode a commit waits on open reads); Ruling G retires the purge watch itself | `issues/state-store-journal-mode.md` (new); `issues/namespace-sync-redesign.md` |

**What the tree does not yet do** (the root `PLAN.md`'s list, GOAL R6). The build re-reads each
anchor on its own commit; the plan's reading at `7e36368`:

| Ruling or decision | The tree | Owning seed |
|---|---|---|
| A | limits and the delay are optional; an unquota'd stage tier is never reclaimed | `issues/eviction-one-hop.md` (Notes); `issues/pump-liveness-and-loss-windows.md` for `max_interval` |
| B, C | `commit_upstream` to the source; `evict_copy` drops a clean copy | `issues/eviction-one-hop.md` |
| D | agrees, but for the journal mode (Decision 6) | — |
| E | residency left on node tiers; three paths settle `detached` without the rescue | `issues/shutdown-rescue-gaps.md` |
| F | `commit` is a stub; nothing in `engine/` calls a rebuild | `issues/offline-commit-command.md`, `issues/archive-finalization-integration.md` |
| G | ids are reused | `issues/namespace-sync-redesign.md` |
| I | a second overlay reads a tier's marker as foreign; the member index is per overlay | `issues/shared-staged-tier.md`, `issues/read-only-mount.md` |
| J | agrees; an empty or relative XDG value is used as written | `issues/xdg-empty-and-relative-values.md` |
| 1 | `staging` is a path of its own, outside every tier and every quota | `issues/archive-spec-plumbing.md` |
| 2, 3 | one replayed operation at a time; the adapter re-packs on every call | `issues/namespace-sync-redesign.md` (the model), `issues/archive-finalization-integration.md` (the triggers) |
| 4 | nothing on the durable tier records a removal or a rename; the member index is in each overlay's database | `issues/durable-tier-record.md` (new) |
| 5 | a mount that finds `mounted` stops; the audit does not exist | `issues/audit-command.md`, `issues/read-only-mount.md` |
| 6 | `journal_mode = WAL`, `synchronous = NORMAL` (`state/db.py:94-95`) | `issues/state-store-journal-mode.md` (new); the check on the real filesystem stays with `issues/hpc-deployment.md` |
| 7 | no rule names the durable tier; rescue stops at "the first shared tier" | `issues/shutdown-rescue-gaps.md` |

**Seeds the cycle writes** (GOAL R6, "where no seed owns one"): two, each from the KB's ISSUE
template with `status: unshaped` and a `ROADMAP.md` entry.

- `issues/durable-tier-record.md` — Decision 4 and the tier model (h): the bundle record and the
  index beside the staged bundle, when each is written, and what a rebuild and a recovery read from
  them. No seed owns it: `issues/archive-finalization-integration.md` owns the rebuild and its
  triggers, `issues/audit-command.md` the recovery walk, `issues/shared-staged-tier.md` the shared
  index. Placed in the roadmap's first section, before the finalization entry, which reads it.
- `issues/state-store-journal-mode.md` — Decision 6: `state/db.py:94-95`, `AGENTS.md` § 2 and § 10,
  and an audit of which engine reads hold a cursor open long enough to delay a commit
  (`state/journal.py:309`, `:318` were named and not audited). High blast radius: `db.py`'s pragmas
  are on the list. Placed beside `issues/hpc-deployment.md`'s check, which decides whether it is
  built as written: if the maintainer's `wal` run holds on his filesystem and he keeps WAL, the seed
  is declined and Decision 6 is revised in the root `PLAN.md`.

If the walk in P4 finds another contradicted decision with no owner, P4 writes that seed too and
names it in its report.

**Seed and roadmap edits** (GOAL R7), each a dated note of 2026-11-12 that cites the root `PLAN.md`
by section and `spec/plan-the-bundle-loop/`, never the seed `issues/plan-the-bundle-loop.md`, which
is retired when this cycle lands:

1. *Work that moves.* `issues/archive-spec-plumbing.md`: its open question on `staging` is answered
   by Decision 1, and its draft R2, R6 and R7 change with it. `issues/sharded-archive-sources.md`:
   the reconciliation of the root `PLAN.md`'s "post-v0.1.0" that its roadmap entry assigns to its own
   cycle is done here. `issues/archive-finalization-integration.md`: the rebuild's inputs and its
   three triggers are now the root `PLAN.md`'s (§ *The tier model*); the seed builds them. Each seed
   and its `ROADMAP.md` entry says so.
2. *The namespace seed's five decisions.* The root `PLAN.md` now records: 1 (stable ids, Ruling G);
   3 for a bundle source (Decisions 2 and 3); 5 (the database and the durable tier, Ruling D and
   Decision 4). They remain that seed's own: 2 (where tombstones live in the catalog); 3 for a POSIX
   or object-prefix source (the planner, and whether deferral is wanted there at all); 4 (what
   excludes a directory rename), to which Decision 3 adds one input and no ruling. The seed's draft
   R1 is edited to say which is which.
3. *The reconciliation seed.* `issues/plan-md-reconciliation.md` gains a note, item by item: 1 to 5
   resolved here, with what § 2.1 leaves to it. Its `ROADMAP.md` entry is cut to that residue.
4. *The check for the maintainer* (GOAL R4) is added to `issues/hpc-deployment.md` beside its
   question 6, and to `ROADMAP.md` § *Decisions that gate the queue* → Feasibility on a real cluster,
   as a pointer to the root `PLAN.md` section that prints it.

### 2.7 The prototype record (GOAL R8, R9)

The plan step ran both prototypes on a copy of the helper taken from the in-review branch
(`tools/bundle_loop.py` at `ae420d1`, blob `e6d18b4`). The GOAL asks for runs "on that helper as
landed". So the build's first phase runs the kept copies again, on `tools/bundle_loop.py` and
`tests/loop/tap.py` of the rebased branch, and commits
`spec/plan-the-bundle-loop/prototype/RECORD.md`: what was run, the commit of the helper, whether the
trees were equal in names and bytes, the requests to the source by kind, whether each of the two
processes read the oracle's bytes, and the fetch count of the bundle's object. The record states both
runs, the plan step's and the build's, and says if they differ. The copies under
`spec/plan-the-bundle-loop/prototype/` are a record and not a deliverable; nothing of them lands
under `tools/`, `src/` or `tests/`.

What the plan step measured, on the helper at `ae420d1` (laptop, local filesystem, moto store,
requests counted on the wire by the helper's tap):

| | R8: one bundle rebuilt (`research/02`) | R9: two read-only processes (`research/03`) |
|---|---|---|
| Run | 18 full runs at 6 and 300 members, 3 change-kill cases | 252 two-process runs at 6 and 300 members, 6 kill runs, 1 stale run |
| Oracle | trees equal in names and bytes: yes, 18 of 18; modes and file mtimes 0 differing; 1 directory mtime differing (not modelled); ownership not compared | each process read the oracle's bytes: yes, 504 of 504 processes |
| Requests to the source | pull: 1 read. Five changes: 0. Rebuild and write-back: 1 write. Verifying fetch: 1 read. No stat, no list | fetches of the bundle's object: one after the other, 1 (every run); started together with rename only, 2 (40 of 40 per size); started up to two pull-times apart, 1 or 2; together with a lock file, 1 (40 of 40 per size); lock timeout shorter than a pull, 2 (5 of 5). One HEAD per mount that asks for the signature. No write |
| The contrast | the tier's files alone, with no record: names unequal | a mount killed mid-pull left two temporary files nothing took for a bundle; the next mount staged in 33–39 ms with no lock, and after a 2.00 s timeout with one |

The kept copies are `prototype/rebuild.py` (from `research/02`) and `prototype/shared.py` (from
`research/03`), with `prototype/README.md` saying how each was run. Each ends with one `RESULT` line
that P1's gate reads.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | Root `PLAN.md` § *The story* first; a `*Follows from:*` line opening every section (§ 2.1) |
| R2 | § *Decisions* → *Settled rulings*, ten rows (§ 2.2); non-goals in § *Vision and scope* state Rulings H and I |
| R3 | § *Decisions* → *Decided in this cycle, for the maintainer's review*, Decisions 1–7 (§ 2.3) |
| R4 | § *Architecture* → *State store* → *The state store on shared storage* (§ 2.5) |
| R5 | § *The tier model*, subsections (a)–(h) (§ 2.4); (g) cites the R9 record |
| R6 | § *Decisions* → *What the tree does not yet do*; `AGENTS.md` notes and rows; new seeds and their `ROADMAP.md` entries (§ 2.6) |
| R7 | The seed and roadmap edits of § 2.6; the dispositions of § 2.1 for item 3 |
| R8 | `prototype/RECORD.md` and `prototype/rebuild.py`; Decisions 2, 3, 4 cite it (§ 2.7) |
| R9 | `prototype/RECORD.md` and `prototype/shared.py`; the tier model (g) cites it (§ 2.7) |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research and again after this design was drafted. The cycle changes no code, so no
invariant's mechanism is touched; what it touches is the text that states them, and the rule about
changing that text.

- **§ *Conventions* → Same-commit rule** ("a change that overturns an invariant adds that section
  too … belongs in the plan's deviation table and in front of a human") — the cycle overturns rules
  of § 2, § 5, § 7, § 8 and § 10 in the design document, not in behaviour. The GOAL rules that
  the sections keep their text and gain a note and a row; that is the first deviation row below, and
  this gate is where it comes before a human.
- **§ 2 (state-store durability)** — Decision 6 assumes another journal mode and `synchronous`
  level than the section asserts. `autocommit=True`, `BEGIN IMMEDIATE`, per-connection pragmas and
  the `SCHEMA_VERSION` rule are untouched. Ruling G is the product decision the section says a bump
  is. Noted, not reworded.
- **§ 10 (concurrency)** — one sentence rests on WAL (a reader does not wait on a writer). Measured
  in the assumed mode: the reader still does not wait on an open write transaction, but a commit
  waits on open reads. Noted, not reworded.
- **§ 4 (tier atomicity)** — the tier model's atomic steps (§ 2.4 c) are all rename-into-place or a
  single object write, which is the section's rule. The staged bundle sits in the tier's root under a
  name no entry of the overlay can take (the reserved `.terrace/` tree is the one such place today)
  and is counted by the tier's limits, where the section says `usage()` "must exclude managed
  internals": Decision 1 makes the staged bundle a counted copy, not an internal. Second deviation
  row.
- **§ 5 (committed source path)** — untouched for a POSIX source. For a bundle source Decisions 2 and
  3 replace the replay; the section is noted, not reworded.
- **§ 6 (write-back policy)** — honoured: a boundary's policy still belongs to the tier the data
  commits from. The bundle's own delay is the durable tier's boundary toward the source.
- **§ 7 (eviction)** — Rulings B and C overturn two of its rules; noted, not reworded.
- **§ 8 (lifecycle)** — Ruling E and Decision 5; noted, not reworded. Marker rules are honoured: a
  marker is still never rewritten for a registered tier. The marker a shared read-only tier needs
  (§ 2.4 g) is a second kind, not a relaxation of the first.
- **High-blast-radius list** — no listed path is edited. Research ran in full anyway (appetite big).
- **§ *Prose and comments*** — the root `PLAN.md` keeps its voice: declarative, the why, no filler.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| The root `PLAN.md` states rules that contradict `AGENTS.md` § 2, § 5, § 7, § 8 and § 10 while those sections keep their text | The GOAL (R6 and its non-goal) rules it: reviews grade code against the sections, and the code still implements them | Rewording the sections now makes correct code an auto-CRITICAL finding until each owning cycle lands; leaving the root `PLAN.md` as it is keeps every engine cycle building against its own contract |
| A staged bundle kept under a reserved name in its tier's root is counted by the tier's limits, where § 4 has `usage()` exclude managed internals | Decision 1: the story puts the bundles on scratch and the starting position counts them | Leaving the staged bundle uncounted is today's `staging` path: the largest thing on the durable tier outside every quota |
| The build's first phase re-runs prototypes whose design evidence the plan step already measured | The GOAL asks for the runs "on that helper as landed"; the plan step could only use the in-review copy | Citing the plan step's run alone would record a helper commit that never reached `main` |
| The gates are inline `grep` and `awk` checks of form, not gate scripts | A document's content is judged by the review; a gate can check that a section, a row or a trace line exists | A script that scores a document's content would be a gate that passes text it cannot read, and would have fired the adversarial-review trigger for no gain |

Readings of the GOAL the design carries, each for the sign-off:

| R-ID | Wording | Reading taken |
|---|---|---|
| R4 | "the locks the design assumes of the filesystem" | SQLite's locks on the database file. The state-directory `flock` is assumed on one host only; the check reports it across hosts and the design does not lean on it |
| R5 (a) | "Which tier of a stack is the durable tier" | Decision 7, which also restricts a stack over a bundle source to one shared stage tier |
| R5 (g) | what sharing "needs from … the pull" | The needs are stated; how a mount learns a bundle's signature (three measured options) is left open to `issues/shared-staged-tier.md` |
| R7.3 | "a section of `PLAN.md` this cycle rewrites" | Any section whose body changes, amended ones included, so all five items are met |
| R8, R9 | "on which commit of the helper" | The record states two: the plan step's (`ae420d1`, in review) and the build's (as landed) |
| R9 | "how many times the bundle's object was fetched" | Reported per scenario: it is 1 or 2 by whether the pulls overlap and whether a lock file is used |

Read against the R-IDs: the first row is R6's own instruction. The second touches R3 (a) ("counted
by that tier's limits") and agrees with it. The third touches R8 and R9 ("on which commit of the
helper") and is how they are met. The fourth touches no R-ID's wording: every R1–R7 criterion is
"SHALL state", graded by reading.

## 4. Rabbit holes (resolved)

- **Does SQLite's objection to WAL reach one host at a time?** → Half. The stated reason is shared
  memory between hosts, and SQLite lists "one at a time, exclusive access" as an acceptable use over
  a network; but no page says a `-shm` file works on a network filesystem from one host, and
  rollback mode is the one it names as mitigating. Decision 6 assumes rollback mode and leaves WAL
  open to the maintainer's own run ([`research/01`](research/01-state-store-on-shared-storage.md)).
- **Would another journal mode force a change to the engine's connection model?** → WAL with
  exclusive locking would (a second connection in one process is refused, measured). A rollback
  journal would not: a connection per thread still works. Its cost is that a commit waits on open
  reads ([`research/01`](research/01-state-store-on-shared-storage.md)).
- **Can a bundle be rebuilt from recorded state alone, a directory rename included?** → Yes: equal
  trees in 18 runs, one read and one write to the source
  ([`research/02`](research/02-bundle-rebuild-prototype.md)).
- **Can the durable tier stand in for the database?** → Not by its files alone (names unequal), and
  yes with one record file per bundle. That is Decision 4, and it is what gives Decision 5 and the
  fallback of § 2.5 something to read ([`research/02`](research/02-bundle-rebuild-prototype.md)).
- **What happens to the staged bundle after a write-back?** → Three options run. A rename over the
  staged name is unsafe at a kill; leaving it, or a new name switched in one transaction, is safe
  ([`research/02`](research/02-bundle-rebuild-prototype.md)).
- **Is an unlocked concurrent pull really harmless?** → Yes: 2 fetches, both processes on the
  oracle's bytes, in 40 of 40 runs; a lock file brings it to 1 and a stranded one costs one timeout
  ([`research/03`](research/03-shared-read-only-tier-prototype.md)).
- **Can a marker for a shared tier be written by two mounts at once without a lock?** → Only if its
  text is a function of the source: with a writer id or a time in it, one mount read a marker that
  was then replaced, 20 of 20 ([`research/03`](research/03-shared-read-only-tier-prototype.md)).
- **Gate scripts.** None was written; the gates are inline checks of form (§ 6). The adversarial
  review of trigger 3 therefore did not run, by the foreman's ruling 4.

## 5. Risks & open questions

- **Decision 6 is the widest decision here, and the least tested where it matters.** It asks for an
  engine change (`db.py`'s pragmas, on the high-blast-radius list) on documentation and a local
  run. On a network filesystem `synchronous = FULL` makes every commit a round trip to the server,
  and the engine commits on every clean-to-dirty transition and three times per open of a resident
  file (`issues/read-path-metadata-writes.md`). That cost is unmeasured. If it is ruinous, the
  pressure falls on Ruling D itself, which this cycle does not reopen. The foreman may prefer to
  sign Decision 6 as "assumed pending the maintainer's check" and hold the new seed until the check
  is run; the plan is written so that either reading builds.
- **Decision 4 adds a file format the story does not name.** Its timing (when the tier is brought up
  to date, not on every change) is argued from the measured per-member cost and was not itself
  measured. Its format is left to the new seed.
- **Decision 5 has no prototype.** It rests on the kill check of `research/01` and on seeds. The
  recovery walk is `issues/audit-command.md`'s to design; the root `PLAN.md` states the outcome, not
  the walk.
- **Decision 7 narrows what a spec may say** over a bundle source (one shared stage tier). It is
  read from the story, not measured, and no seed asked for it.
- **Nothing ran on a network filesystem or through FUSE.** Atomic rename, exclusive create, and
  reading a replaced open file are assumed on shared scratch; `research/03` says so. The check of
  § 2.5 covers the state directory's filesystem only. A like check for the durable tier's
  filesystem is not written; P4 records it in `issues/hpc-deployment.md`.
- **Not tested by the prototypes:** whiteouts; a streamed or multipart upload (the rebuild spools
  the whole compressed object, and a store's ETag after a multipart upload differs); a source object
  changed by another writer between pull and write-back (no precondition is set); writes during a
  rebuild; symlinks, hard links, empty directories, ownership; more than two mounts; a source with
  no ETag (SFTP). Each is named in the root `PLAN.md` where the decision it bears on is recorded.
- **The helper is not on this branch.** P1 stops if it has not landed. A Linux-only teardown defect
  in it is known and does not matter on the build host.
- **`AGENTS.md` and the seeds move under this branch** before it is built: two cycles land first.
  P4 re-reads every anchor; the additions-only check is taken against the merge base, so it stays
  true after the rebase.
- **Size.** The root `PLAN.md` is 660 lines and gains three sections. P2 and P3 each rewrite or
  amend several hundred lines of a document every later cycle is graded against. The review is the
  control; the gates check form only.
- **Open question for the sign-off:** by-name or by-id for altered files on the durable tier is
  left to `issues/namespace-sync-redesign.md` (its decision 4). The measurements favour by-id with
  the bundle record; the seed's note of 2026-11-11 favours by-name for a durable set a person can
  read. If the foreman wants it decided here, it is one more decision in the list, on evidence
  already in `research/02`.

## 6. Verification strategy

There is no CLI behaviour to drive: the deliverables are documents and one measured record. Each
phase closes with `tools/lint.sh` (the cycle must leave the static gate green and `CLAUDE.md` a
symlink) and inline checks of committed text that a reader could run by hand:

- **Presence.** The sections that must exist, by heading; the story's first words in the
  maintainer's phrasing; the exact heading "Decided in this cycle, for the maintainer's review".
- **Form.** Ten ruling rows, each with a date and no empty cell; at least six decisions, each with a
  reason, an alternative turned down and evidence; a `*Follows from:*` line within two lines of every
  heading of the root `PLAN.md`.
- **What must not change.** `git diff --cached --merge-base main` is empty for `src`, `tests`,
  `tools`, `pyproject.toml` and `uv.lock`, and shows no deleted line in `AGENTS.md`.
- **The record.** P1 runs both prototypes and greps their result lines; the later phases grep the
  root `PLAN.md` for the record's path.

What the gates cannot judge, and the review must: that a reason is a reason, that a trace names the
right sentence, that a carried section is still true, that the voice is the document's own. No gate
script decides pass or fail for a document, so the adversarial-review trigger did not fire (the
foreman's ruling 4). The trace check was dry-run in the plan step on three trees: today's root
`PLAN.md` (red, no trace lines), a fixture with a trace under every heading (green), and the same
fixture with one trace removed (red).

A phase stages its files before its `verify:`, since two checks read the index.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
