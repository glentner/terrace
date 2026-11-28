---
status: draft
last_updated: 2026-11-16
supersedes: GOAL.md (which remains as the original statement of intent)
---

Plan — Terrace (`terrace`)
==========================

A reference implementation of managed data tiering for research computing, delivered as a
stand-alone Python program. A TOML spec names a source, an object store or a tape reached over SFTP
holding one compressed bundle or ten thousand, and a stack of storage tiers beneath it: shared
scratch, then node-local disk and RAM. A FUSE mount presents the bundles' contents as one namespace.
A bundle is pulled to scratch when it is first read and decompressed there without being unpacked;
single files land on the fast tiers as they are read; a changed file moves up the stack one tier at
a time, on a tier's write-back delay or under its limits, to scratch beside the bundles, which is
durable; and a bundle is rebuilt and put back to its source on clocks of its own. A signal stops a
job's mount with every change on scratch, so the node is released, and a commit from another host
later rebuilds the bundles that changed. A POSIX directory or an S3 prefix serves as a source under
the same stack.

The design goal is *legibility as a reference implementation*: the correct abstractions,
modern Python (3.15+, free-threaded), and no framework semantics leaking into the core model.


The story
---------

*Follows from:* nothing; it is the root that every other section traces to.

The maintainer told the story on 2026-11-11, the first time it was told in one place. This document
was first drawn on 2026-07-01, before it, and is redrawn around it: a section that predates the story
and still holds is kept, and every section opens with the sentence of the story, or the ruling or
decision, that it follows from.

**The statement, in the maintainer's words (2026-11-11):**

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

Its five sentences carry the labels a section cites:

- **S1**, "The big user-story I want to tell is that we define a TOML config…": the spec; a source of
  one bundle or many; the cascade down to scratch, where bundles are pulled lazily and decompressed
  but not unpacked; single files landed in the RAM tier and written back up through node-local disk
  to scratch, beside the bundles.
- **S2**, "As changes landed to working copies in /dev/shm…": an altered file moves up the stack by
  eviction or by write-back delay.
- **S3**, "The source bundles and altered versions of files alongside them on Scratch…": the bundles
  and the altered files on scratch are the durable set a halted job recovers from.
- **S4**, "The source bundles are repacked…": a bundle is rebuilt under pressure or on its own delay
  and put back to the source.
- **S5**, "A 'commit' flushes everything back up the stack."

**His clarifications of the same date**, as the seed that recorded them paraphrased them; the labels
are this document's:

- **C1.** every stage tier has a size limit, a count limit and a write-back delay. Lack of room in a
  tier pushes a copy up the stack, which may cause the same in the tier above; on a read-only
  workload that is the only force. A changed copy left alone for its delay is pushed up as well,
  whatever the tier's room, and that may cascade for the upper tier's own reasons;
- **C2.** a push goes one tier up. A write-back in the lowest tier never goes "all the way up" to
  repack the bundle;
- **C3.** a signal, from the user or the batch scheduler, commits what has changed up to the lowest
  durable tier, so the mount can stop and the node be released. Later, from a login node, a full
  commit rebuilds the bundles that changed, and does not delete the staged bundles, so the next mount
  does not pull them again;
- **C4.** a clean copy pushed out of a tier is kept one tier up when that tier has room, so a second
  read comes from node-local disk and not from the bundle. The database tracks every copy and knows
  where the fastest one is;
- **C5.** a soft unmount, asked for directly or by a signal, ends with the database recording every
  modified file on the first durable tier and nothing on any tier below it;
- **C6.** everything lives in the one database, in a state directory on durable storage every host
  sees; on his clusters, the home filesystem. Many writers on one overlay do not work with that and
  are for another day ([`issues/multi-node-mounts.md`](issues/multi-node-mounts.md)). A top durable
  tier shared read-only between mounts may come first
  ([`issues/shared-staged-tier.md`](issues/shared-staged-tier.md)).

The settled rulings below take Ruling A from C1, B from C2, C from C4, D, H and I from C6, E from C5,
and F from C3.


Decisions
---------

*Follows from:* the story, S1–S5 and C1–C6, and what was ruled and decided on it.

It holds three lists. The rulings are the maintainer's and are settled; none is reopened in this document.
The decisions of the redraw were proposed without him and recorded for his review, and he signed all
seven on 2026-11-15. The last list says where the code still does something else, and which seed
changes it. Every other section cites a ruling as Ruling A to J and a decision as Decision 1 to 7. A
sentence elsewhere that a ruling or a decision overturns, where the tree still does what it says, is
kept as the record of what the tree does and marked *Overturned by …*, with the seed that changes it.

### Settled rulings

*Follows from:* C1–C6, and two rulings of the same day made outside the story (G and J).

| Ruling | What is ruled | Date | Reason | Turned down |
|---|---|---|---|---|
| A | Every stage tier has a size limit, a count limit and a write-back delay. | 2026-11-11 | One rule a user can hold in their head: two limits and a delay per tier, and either moves a copy one tier up. On a read-only workload pressure is the only force, and a tier with no limit has none. | Limits and a delay that a spec may leave out, as the tree has them: a stage tier with no limit is never reclaimed and fills its device. Whether a spec must state them or gets defaults is `issues/eviction-one-hop.md`'s. |
| B | Pressure or an expired delay moves a copy one tier up and no further. | 2026-11-11 | The maintainer's cases are codes that edit the middle of files: "There is no benefit of using the product if we trigger a repack on every write-back." A bundle is rebuilt on triggers of its own. | Committing a dirty copy to the source when it is evicted (`AGENTS.md` § 7, "to the *source*, not one hop"), which turns pressure in the RAM tier into a rebuild and a push per evicted file. |
| C | A clean copy pushed out of a tier is kept one tier up when that tier has room. | 2026-11-11 | "there's no reason not to use faster tiers (node-local /tmp) if we have the quota available": a second read comes from node-local disk and not from the bundle. | Dropping the copy, so the next read goes back to the staged bundle. |
| D | One database holds everything. It sits in the overlay's state directory, on durable storage every host of the workflow sees, and outside every tier. | 2026-11-11 | One authority for every tier, which knows where the fastest copy is and which a commit from another host reads. The maintainer accepted that many writers do not work with it (Ruling H). | A database on node-local storage while mounted, written to the durable tier at a soft unmount. It was proposed because SQLite's WAL mode does not work over a network filesystem and every change would be paid at local latency. |
| E | A soft unmount, asked for directly or by a signal, commits every modified file to the first durable tier and halts. The database then records every modified file there and nothing on any tier below it. | 2026-11-11 | A batch job's end releases the node, and the next job mounts on another node whose node-local tiers are empty; nothing recorded on them may be needed. | Residency left recorded on node-local tiers, as the tree leaves it after a rescue, which makes the next mount on another node refuse (reproduced in `issues/shutdown-rescue-gaps.md`). |
| F | A commit run from another host rebuilds the bundles that changed and leaves the staged bundles in place. | 2026-11-11 | C3: the next mount does not pull them again, and a pull from tape is the expensive part. | Deleting the staged bundles after a commit, which makes the next mount pull every bundle again. |
| G | Row ids are never reused, by a schema version bump taken before any state database is deployed. `issues/namespace-sync-redesign.md` owns the bump. | 2026-11-11 | A reused id is what two cycles built machinery to tell apart from the entry that held it before. The bump strands no state database while none is deployed, and stops being free at the first deployment. | Machinery that tells a reused id apart: queue rows recognized by six fields, a four-field mark, and the purge watch. |
| H | Many writers on one overlay are not designed for here, and are scoped when reached. | 2026-11-11 | One database has one writer (Ruling D). In the maintainer's words, "full multi-node writers is a challenge for another day." | The same spec mounted on many nodes now, each with a database of its own, negotiating at the durable tier with directory locks (`issues/multi-node-mounts.md`). |
| I | A top durable tier shared read-only between mounts, each with tiers of its own below, is wanted sooner than many writers. | 2026-11-11 | A multi-node job or a sweep reads one dataset, and without sharing every mount stages its own copy of each bundle and pulls it again. A staged bundle is a function of its source object, so sharing it for reading needs no writer to coordinate. | One staged copy of the dataset per mount (`issues/shared-staged-tier.md`). |
| J | The names: Terrace, `terrace`, `TERRACE_`, `.terrace/`. Configuration and per-user paths follow the XDG convention. | 2026-11-11 | A name was chosen before any release and before any overlay was deployed, so the rename is a hard break with no migration code; XDG is the convention users and sites already configure. | The placeholder name, and paths of the project's own invention (`spec/rename-to-terrace/GOAL.md`, Clarifications). |

### Decided in this cycle, for the maintainer's review

*Follows from:* S1–S5 and the settled rulings.

The cycle `spec/plan-the-bundle-loop/` proposed these seven for the maintainer's review, and he
reviewed and signed them on 2026-11-15: all seven as proposed, with an addition to Decision 1 and a
ruling on Decision 6's check, each recorded under its decision. The heading keeps the name the cycle
gave the list; the decisions are signed, and revisiting one is the maintainer's call on new evidence.
Evidence is tagged *measured*, *documented* (a cited source) or *read* (argued from the code or a
seed). The measurements are in that cycle's directory. The record,
`spec/plan-the-bundle-loop/prototype/RECORD.md`, holds the two prototypes' runs on the local loop's
helper as landed (`a0c88f1`), which agree with the plan step's runs the decisions were signed on;
`research/` beside it holds the briefs behind them: `01` the state store, `02` the bundle rebuild,
`03` the shared read-only tier.

**Decision 1 — Where a staged bundle lives.** Inside the durable tier (Decision 7), the stage tier
directly below the source. It sits in the tier's root beside the altered files, under a name no
entry of the overlay can take, and the tier's limits count it: one file, at its staged, uncompressed
size. The `staging` key is retired; a spec that carries it is refused with a message naming the tier
that now holds the bundle. Because the limits count it, a staged bundle is evictable: under pressure
the tier reclaims a staged bundle as it reclaims any copy. By Rulings B and C, one whose source
already holds its recorded state is dropped, and the next access pulls it again with one read; one
with changes not yet at the source first catches up by Decision 2's rebuild and one write, and is
then dropped with its altered files.
*Reason.* The story puts the bundles on scratch beside the altered files (S1, S3), and a rebuild then
reads one tier and the database and nothing else.
*Turned down.* A path of its own outside every tier, the `staging` key the tree has: the largest
thing on the durable tier, seen by no overlap rule and counted by no limit.
*Evidence.* Measured: in the record's 18 rebuild runs, every one with trees equal in names and
bytes, the rebuild read only the state database and the durable tier's directory. At 300 members
the staged `.tar` was 6,420,480 bytes against 5,000 bytes of altered files, so an uncounted bundle
is nearly all of what the tier holds. Read: `issues/archive-spec-plumbing.md`, note of 2026-11-11,
asks this question.
*Ruled at sign-off (2026-11-15).* The maintainer added that a staged bundle, counted against the
durable tier's limits, is evictable under pressure, as the last three sentences of the decision
state. The mechanism is built with the bundle loop; the pressure rule itself stays with
`issues/eviction-one-hop.md`.

**Decision 2 — How a source catches up.** By a rebuild from recorded state and one object write.
Unchanged members are copied from the staged bundle at their recorded offsets, changed and created
ones come from the altered files, removed ones are left out, renamed ones take their new names; the
result is compressed and replaces the source object in one write. No operation is replayed against a
bundle. After the write the staged bundle is never replaced in place: either it and its altered files
stay as they are, recorded clean and correct only together, or a new staged bundle is written under a
new name and the database switches to it in one transaction.
*Reason.* A bundle has no rename, no unlink and no mkdir; a rebuild has no order to discover.
*Turned down.* Replaying queued operations, which the archive adapter answers with one whole re-pack
each.
*Evidence.* Measured: the record's rebuild runs, at 6 and at 300 members, gave trees equal in names
and bytes in 18 of 18, with 1 read from the source to pull, 0 for five changes and 1 write to rebuild
and write back, identical in all 18. Replay of the same five changes computes to 5 whole re-packs,
30,566,400 bytes at 300 members, against one object of 5,932,471 (`research/02`). A kill injected
after a rename over the staged name left the recorded offsets pointing into the new file and a wrong
view; kills around a switch to a new name all left a view equal to the oracle.

**Decision 3 — What a rename inside a bundle is.** A change of recorded state. A member's recorded
name changes; a directory rename changes the recorded prefix of every member under it. No bytes move
for an unchanged member, and nothing is sent to the source until the bundle is rebuilt. Whether an
altered file under a renamed directory is kept on the durable tier by name or by entry id is not
decided here: it is `issues/namespace-sync-redesign.md`'s decision 4, and this one gives it a
measured input.
*Reason.* The rebuild writes every member under its current name whatever its history, so the
history need not exist.
*Turned down.* The kernel overlay filesystem's default, which refuses the rename of a lower-layer
directory, so the tool copies every member and removes the old ones.
*Evidence.* Measured, in the record's runs with trees equal: a directory of 150 members renamed as
recorded state wrote 0 bytes to the durable tier and 151 rows; as copy and remove, 2,956,800 bytes,
150 files and 452 rows. An altered file kept by name under the directory adds one POSIX rename, a
second step beside the database commit, and a kill injected between them left a row naming a missing
file (`ENOENT`); kept by id there is no such step. Documented: the kernel's overlay filesystem
(<https://docs.kernel.org/filesystems/overlayfs.html>), which fails such a rename with `EXDEV`.

**Decision 4 — How a removal is recorded on the durable tier.** In the database at once, as every
change is, and in a bundle record: one small file per staged bundle on the durable tier, written by
rename into place, that with the staged bundle and the altered files is enough to rebuild the bundle
without the database. It names what the staged bundle's members are now called, which are removed,
and which altered file holds a member's bytes. It is written when the durable tier is brought up to
date for that bundle (a push into the tier, a soft unmount, before a rebuild), not on every change.
*Reason.* The story calls the bundles and the altered files on scratch the durable set a halted job
recovers from (S3). Without a record that set is wrong by exactly the removals and the renames.
*Turned down.* The database alone, for which the tier cannot stand in. Whiteout files: a renamed
unchanged member has no bytes on the tier for a whiteout to sit beside, so whiteouts need a second
kind of marker, moved with every directory rename.
*Evidence.* Measured, in the record's runs with trees equal: with the state database deleted, a
rebuild from the tier's files and the record equalled the oracle; from the tier's files alone it did
not (at 6 members, 5 names missing and 6 extra: the removed member back and every renamed member
under its old name). The record file was 913 bytes at 6 members and 33,616 at 300, about 110 bytes
a member each time it is written, which is why it is not written on every change. Whiteouts were
argued and not run.

**Decision 5 — What a mount does after a kill.** It recovers and mounts; it does not stop. A mount
that finds the last one never settled trusts the database, since a commit acknowledged before the
kill is in it, and checks the tiers against it: a node tier that is gone, or that is another host's,
loses its recorded copies; a modified file whose newest copy was there goes back to the version the
durable tier holds and is named in a loss report; an unresolved intent is rolled back or finished;
the durable tier is checked against the bundle records. Then it serves. A read-only mount has
nothing to lose and recovers the same way with an empty report.
*Reason.* A batch job that reaches its time limit is killed, and the next job on another node must
start. A stop that waits for a person turns the ordinary end of a job into an outage.
*Turned down.* The unconditional stop the tree makes (`AGENTS.md` § 8), which exists because the
audit that would recover does not.
*Evidence.* Measured (`research/01`): on a local filesystem, every commit a process acknowledged
before `SIGKILL` was read by the next process, 301 of 301 in the tree's journal mode and 300 of 300
in the one Decision 6 assumes. Read: `issues/shutdown-rescue-gaps.md`, where the next mount is
refused (reproduced there), and `issues/read-only-mount.md`. Unverified: the same on a network
filesystem (§ *The state store on shared storage*). No prototype of a recovery was run; the walk is
the audit's, and its design stays with `issues/audit-command.md`.

**Decision 6 — Which SQLite journal mode the design assumes.** A rollback journal,
`journal_mode = DELETE`, with `synchronous = FULL`; and of the filesystem that holds the state
directory, locks on the database file alone (§ *The state store on shared storage*). This departs
from the tree, which configures WAL and `synchronous = NORMAL`.
*Reason.* It is the one mode SQLite's documentation names as mitigating a network filesystem, and it
needs no shared-memory file. `FULL`, because a released node is nearer a system crash than an
application crash, and with `NORMAL` a commit is not synced.
*Turned down.* Keeping WAL: SQLite states it "does not work over a network filesystem", for a reason
(memory shared between hosts) this workflow avoids, but no page says a `-shm` file on a network
filesystem works from one host. WAL with `locking_mode = EXCLUSIVE`: a second connection in the same
process is refused, and the engine keeps one connection per thread.
*Evidence.* Documented: `sqlite.org/wal.html` § 1 and § 8, `useovernet.html` and `pragma.html`,
quoted in `research/01`. Measured there, on a local filesystem: 300 of 300 acknowledged commits
survive a kill; a second process is refused the write lock; a connection per thread still works and
a reader does not wait on an open write transaction; a commit waits on open reads (1.010 s against a
1.0 s reader), which WAL never does; the median write transaction is 0.285 ms against 0.022 ms.
Unverified: everything on a network filesystem, the cost of a commit there most of all.
*Ruled at sign-off (2026-11-15).* For this workload the safer mode is the right default. The design
enforces `journal_mode = DELETE` with `synchronous = FULL` now, and
`issues/state-store-journal-mode.md` builds without waiting on the network-filesystem check.
Measuring throughput across SQLite's options is deferred until the project is more mature. The check
stays with `issues/hpc-deployment.md`; it also runs the tree's mode, so the maintainer can read WAL
on his own filesystem, and by his ruling that reading no longer holds the change back.

**Decision 7 — Which tier of a stack is the durable tier.** The lowest durable tier in the story's
sense: the first tier whose `scope` is `shared`, counting up from the fastest, and the tier a soft
unmount commits to. Over a bundle source a stack has exactly one shared stage tier, directly below
the source, so the tier that holds the staged bundles and the tier a soft unmount commits to are one
tier; a spec that breaks this is refused. Over a POSIX or object-prefix source with no shared stage
tier, the durable tier is the source. The redraw added this decision: the story names one such tier,
and § *The tier model* (a) needs the rule for any stack.
*Reason.* Rulings E and F and Decisions 1, 2 and 4 each say "the durable tier" as if a stack had one.
Over a bundle source the rule makes that true.
*Turned down.* Several shared stage tiers over a bundle source: a rebuild would read altered files
from two tiers, and a soft unmount would land beside no bundle. No user has asked for it.
`issues/shared-staged-tier.md` leaves open a private durable tier beneath a tier shared for reading;
that seed reopens this rule if it wants one.
*Evidence.* Read: the story (S1, S3, C3, C5); `issues/multi-node-mounts.md`, which proposes the same
shape as the first rule to test; `AGENTS.md` § 8, where the rescue already "stops at the first shared
tier". Not measured: both prototypes used the story's stack.

### What the tree does not yet do

*Follows from:* the two lists above.

One row per ruling or decision the code contradicts, read from the tree on 2026-11-16, with the seed
that owns the change and the numbered section of `AGENTS.md` § *Invariants* it overturns. Each such
section keeps its text until the owning cycle lands, and ends with a dated note *Scheduled to
change*, matched by a row in § *Known open violations*; until then a review grades code against the
section as written. Rulings D and H agree with the tree: the state directory is outside every tier,
and one mount at a time writes an overlay.

| Ruled or decided | What the tree does | Owning seed | `AGENTS.md` |
|---|---|---|---|
| Ruling A | The limits and the delay are optional: a stage tier with neither `quota.size` nor `quota.files` is never reclaimed and fills its device, and `max_interval` defaults to none (`tier/spec.py`); `tests/tier/test_spec.py:101` asserts those defaults and `tests/engine/test_evict.py:169` that an unbounded tier is never reclaimed. | `issues/eviction-one-hop.md` (its Notes); `issues/pump-liveness-and-loss-windows.md` for `max_interval` | — (§ 6 lists these windows as the tree's) |
| Rulings B and C | Eviction commits a dirty copy all the way to the source (`commit_upstream`, called at `engine/evict.py:151`) and drops a clean copy (`evict_copy`, `engine/overlay.py:1521`); the engine model's `evict` rule does the same (`tests/engine/test_model.py:331`). | `issues/eviction-one-hop.md` | § 7 |
| Ruling E | After a complete rescue the database still records a copy on every node-scope tier the file passed through, so a mount where those tiers are empty is refused; a second SIGINT, a rescue cut short by its budget and `--force` settle `detached` without a finished rescue (`fuse/mount.py:254-259`; `tests/fuse/test_mount.py:182` and `tests/engine/test_rescue.py:160` pin the second SIGINT and the zero budget). | `issues/shutdown-rescue-gaps.md` | § 8 |
| Ruling F | `terrace commit` is a stub, and nothing under `engine/` calls `resync` (`tier/archive.py:427`), so nothing rebuilds a bundle; `tests/test_cli.py:43` asserts `commit` is a stub. | `issues/offline-commit-command.md`; `issues/archive-finalization-integration.md` for the rebuild | — |
| Ruling G | Row ids are reused: `entry`, `writeback` and `intent` take `INTEGER PRIMARY KEY` with no `AUTOINCREMENT` (`state/schema.sql:59`, `:106`, `:124`). | `issues/namespace-sync-redesign.md` | § 5, § 10 |
| Ruling I | A tier's marker names one overlay, so a second overlay reads it as foreign; the member index is in each overlay's database (`archive_index`); `tests/state/test_registry.py:136` and `tests/state/test_lifecycle.py:133` assert the `FOREIGN` reading. | `issues/shared-staged-tier.md`, after `issues/read-only-mount.md` | — |
| Ruling J | The names agree. An empty or relative `XDG_CONFIG_HOME` or `XDG_STATE_HOME` is used as written (`core/platform.py:35-36`). | `issues/xdg-empty-and-relative-values.md` | — |
| Decision 1 | The staged bundle sits in the durable tier's `.terrace/bundles/` and its limits count it (`engine/overlay.py:188`, `tier/posix.py:211`), but nothing reclaims it under pressure: the evictor walks residency rows and the bundle has none, so it stays until the tier is wiped. | the pressure rule that reclaims a staged bundle, `issues/eviction-one-hop.md` | — |
| Decisions 2 and 3 | Each queued operation is replayed into the adapter, which re-packs the whole working archive on every call (`_repack`, `tier/archive.py:575`); a rename is an operation like any other. Over an archive source the replay is unreachable while every mutating engine call is refused `EROFS`. | `issues/namespace-sync-redesign.md` (the model); `issues/archive-finalization-integration.md` (the rebuild and its triggers) | § 5 |
| Decision 4 | Nothing on the durable tier records a bundle's index, a removal or a rename; the member index is in each overlay's database. | `issues/durable-tier-record.md` | — |
| Decision 5 | `begin_mount` raises `CrashDetected` on `mounted` (`state/lifecycle.py:101`) and nothing catches it (`tests/state/test_lifecycle.py:67`, `tests/state/test_survival.py:101` and `tests/engine/test_namespace.py:46` assert the raise); `terrace audit` is a stub (`tests/test_cli.py:43`). | `issues/audit-command.md`; its read-only slice `issues/read-only-mount.md` | § 8 |
| Decision 6 | `journal_mode = WAL` and `synchronous = NORMAL` on every connection (`state/db.py:94-95`), and `tests/state/test_db.py:35-39` reads those two pragmas back. | `issues/state-store-journal-mode.md`; the check on the real filesystem, `issues/hpc-deployment.md` | § 2, § 10 |
| Decision 7 | No rule names the tier a soft unmount commits to: the rescue moves a copy to the nearest shared tier (`engine/rescue.py`). Over an archive source the parser already holds the stack to one shared POSIX stage tier directly below it (`tier/spec.py:398`), so there the two are one tier. | `issues/shutdown-rescue-gaps.md` (the tier a soft unmount commits to) | — |

The evidence behind the decisions has limits, and each is carried by the seed that builds what it
bears on. Not run on a network filesystem or through FUSE: the state store's locks, `fsync` and the
cost of a commit there (`issues/hpc-deployment.md` runs the check; measuring throughput across
SQLite's options is a later concern recorded in `issues/state-store-journal-mode.md`), and atomic
rename, exclusive create and reading a replaced open file on shared scratch, which the prototypes
assumed and for which no check is written yet (`issues/hpc-deployment.md`). Argued and not injected:
a kill between the rename of a staged bundle or a bundle record and its database commit
(`issues/durable-tier-record.md`). Not tested by the rebuild prototype: a streamed or multipart
upload, a source object changed by another writer between the pull and the write-back, writes during
a rebuild, and symbolic links, hard links, empty directories and ownership inside a bundle
(`issues/archive-finalization-integration.md`). Not tested by the sharing prototype: more than two
mounts, and a source with no ETag such as SFTP (`issues/shared-staged-tier.md`,
`issues/sftp-inner-location.md`).


Vision and scope
----------------

*Follows from:* S1, which says what the product is for; Rulings H and I, which bound it.

In HPC environments researchers hand-tier data across `/project` (large, slow, authoritative),
`/scratch` (fast, purged), `/tmp` (node-local) and `/dev/shm` (RAM), and a large dataset often lives
colder still, as compressed bundles on an object store or on tape, because a center will not grant
the inode quota its files would take unpacked. Terrace replaces that choreography: the user writes
one spec, mounts it, and points their application at the mountpoint. Pulling, staging, write-back,
rebuilding the bundles and putting them back follow from the spec's limits and delays. What sets it
apart from a read-only archive mount is the write path: changes move up the stack one tier at a time
and are folded back into the compressed bundles at the source on a delay, and the batch lifecycle
lets a job release its node without waiting on the source.

**In scope (v1)**

* A FUSE mount presenting one logical namespace over an ordered tier stack.
* Sources: an object holding one compressed bundle, or a prefix of many, on an object store or
  reached over SFTP (S1); ZIP and uncompressed TAR archives updated in place; a POSIX directory; an
  S3 prefix. Many bundles and SFTP are queued after one bundle works end to end (§ *The tier model*
  (f)). Stage tiers are POSIX directories.
* Every stage tier with a size limit, a count limit and a write-back delay (Ruling A); pressure or an
  expired delay moves a copy one tier up and no further (Ruling B), and a clean copy is kept one tier
  up when that tier has room (Ruling C). Reclaim is least-recently-used between high and low
  watermarks.
* Write-back policies per tier boundary: quiescence delay, maximum interval, pass-through. A bundle
  is rebuilt and put back on its own delay, under pressure on the durable tier, or at a commit,
  bounded by `sync.max_frequency` (S4).
* Staged bundles on the durable tier, decompressed and not unpacked, with the altered files beside
  them, and a bundle rebuilt from recorded state and put back in one object write (§ *The tier
  model*).
* Whole-file staging with a size-bypass rule; the tier protocol carries offset/length range
  operations so chunk-level staging can be added later without breaking the contract.
* One state database in SQLite, in a state directory on storage every host sees (Ruling D), kept so
  metadata is always at least as current as the data it describes.
* A soft unmount, on a signal or asked for directly, that commits every modified file to the durable
  tier and halts with nothing recorded below it, so the node is released (Ruling E); a drain
  (`terrace umount`, SIGINT) that pushes everything to the source.
* A commit from any host that sees the state directory and the durable tier, which rebuilds the
  bundles that changed and leaves the staged bundles in place (Ruling F), so a compute allocation
  never waits on the source.
* Recovery after a kill (SIGKILL, OOM, a job's time limit, node loss): the next mount checks the
  tiers against the database, reports what was lost, and serves (Decision 5); `terrace audit` runs
  the same walk with no mount.
* A top durable tier shared read-only between mounts, each with tiers of its own below (Ruling I;
  § *The tier model* (g)).
* CLI: `terrace mount|umount|status|flush|commit|audit|config` (cmdkit `ApplicationGroup`).

**Explicit non-goals (v1)**

* Many writers on one overlay (Ruling H). One database has one writer, so the same spec mounted for
  writing on many nodes at once is not designed for here; it is scoped when reached
  (`issues/multi-node-mounts.md`). Named tiers are **managed locations** owned by their overlay and
  marked as such on disk, and concurrent external modification is an audit-repairable violation,
  not a supported mode of use.
* Sharing beyond reading. What comes sooner is the reading half (Ruling I): a top durable tier that
  mounts share read-only, which no mount writes to, evicts from or counts its limits against. A
  shared tier that mounts write to is part of many writers.
* Chunk/block-level partial staging (protocol-ready, not implemented).
* Special files (devices, sockets), hard links across tiers, byte-range lock pass-through
  (advisory locks are honored locally within the mount only).
* Windows. Linux is the deployment target; macOS (macFUSE) is supported for development.


Terminology
-----------

*Follows from:* S1–S5 for the story's terms; Rulings B to F for what moves a copy and what a commit
and an unmount are.

* **Tier stack** — ordered list of tiers. `tiers[0]` is the **source** (upstream terminus,
  authoritative, typically unquoted). The last tier is the fastest (e.g., `/dev/shm/<space>`).
* **Upstream / downstream** — toward the source / toward the fast end.
* **Bundle** — a compressed archive at the source (`tar.gz` in the story) whose members are files of
  the namespace. A source holds one, whose contents are the mount's root, or a prefix of many.
* **Stage tier** — every tier below the source. Each has a size limit, a count limit and a write-back
  delay (Ruling A), and a `scope` of `shared` or `node`.
* **Durable tier** — the maintainer's "lowest durable tier", shared scratch in the story: the first
  tier whose `scope` is `shared`, counting up from the fastest, and the tier a soft unmount commits
  to (Decision 7). What is on it outlives the node.
* **Staged bundle** — a bundle pulled from the source and decompressed on the durable tier but not
  unpacked: one uncompressed `.tar`, whose members are read at their offsets.
* **Member index** — a staged bundle's member names, data offsets, sizes, modes and mtimes, written
  once beside it.
* **Altered file** — a member changed or created through the mount, kept whole as a file of its own
  on the durable tier beside its staged bundle.
* **Bundle record** — one small file per staged bundle on the durable tier, naming what its members
  are now called, which are removed, and which altered file holds a member's bytes (Decision 4).
* **Promotion** — copying a file downstream so reads and writes hit faster media.
* **Push** — moving a copy one tier up, under pressure or on an expired delay, and never further
  (Ruling B). A changed copy is committed to the tier above; a clean one is kept there when the tier
  has room (Ruling C).
* **Write-back** — the push of a changed copy on its tier's policy: after its delay, by its maximum
  interval, or at once for a pass-through tier.
* **Eviction** — reclaiming room in a stage tier under pressure, least-recently-used between
  watermarks. An evicted copy is pushed one tier up (Rulings B, C), not dropped while the tier above
  has room for it. At the durable tier the tier above is the source: a clean staged bundle is
  dropped, and one with changes is rebuilt first (Decision 1).
* **Rebuild** — writing a bundle anew from recorded state: unchanged members copied from the staged
  bundle at their offsets, changed and created ones from the altered files, removed ones left out,
  renamed ones under their new names; compressed and put back to the source in one object write
  (Decision 2).
* **Commit** — what flushes everything back up the stack (S5). `terrace commit`, run with no mount
  from any host that sees the state directory and the durable tier, rebuilds the bundles that changed
  and leaves the staged bundles in place (Ruling F); over a POSIX or S3 source it drains queued
  write-back to the source. It is not one hop of write-back.
* **Soft unmount** — a stop, asked for directly or by a signal, that commits every modified file to
  the durable tier and halts, with nothing recorded on any tier below it (Ruling E). The tree's
  detach (`terrace umount --detach`, SIGTERM) is its first half.
* **Rescue** — the soft unmount's move: changed data on node-scope (`scope = "node"`) tiers pushed
  one tier at a time to the durable tier, so it survives losing the node.
* **Drain** — committing all dirty state hop by hop to the source with policy clocks collapsed;
  `terrace umount` and SIGINT.
* **Residency** — the set of tiers currently holding a copy of a file. The database knows which copy
  is the fastest.
* **Catalog** — the namespace and state in the one database (Ruling D), authoritative while it
  exists.
* **Managed location** — a named tier root owned exclusively by its overlay and marked as such on
  disk; the database is authoritative for its contents. A tier shared read-only between mounts
  (Ruling I) carries a marker of another kind and is owned by none of them.
* **Audit** — reconciling the database against the managed locations after a kill; at the next mount
  (Decision 5), or standalone via `terrace audit` with no mount running.


The tier model
--------------

*Follows from:* S1–S5 and C1–C6; Decisions 1 to 4 and 7.

What each tier of the story's stack holds, what changes the durable tier and how, what its limits
count, what a rebuild reads, and what a second mount needs to share it. The stack is the story's:
a source of bundles; shared scratch beneath it, the durable tier; node-local `/tmp`; the RAM tier
`/dev/shm`. Two prototypes on the local loop's helper checked the model against a plain-directory
oracle: `spec/plan-the-bundle-loop/prototype/RECORD.md` holds their runs, and
`spec/plan-the-bundle-loop/research/` the measurements behind them.

### (a) What each tier holds, and which is the durable tier

*Follows from:* S1, S3; Decision 7.

The source holds the bundles, one object or a prefix of many, compressed; only a rebuild writes a
bundle source. The durable tier is the first tier whose `scope` is `shared`, counting up from the
fastest (Decision 7). Over a bundle source a stack has exactly one shared stage tier, directly below
the source, so the tier that holds the staged bundles and the tier a soft unmount commits to are one
tier, and a spec that breaks this is refused. Over a POSIX or S3 source with no shared stage tier,
the durable tier is the source. It holds what (b) lists. The node-local tiers hold single files:
members landed as they are read, and working copies. Nothing on them is the only copy of a change
for longer than the tier's delay.

### (b) What the durable tier holds

*Follows from:* S1, S3; Decisions 1 and 4.

Staged bundles, decompressed and not unpacked, under a name no entry of the overlay can take.
Altered files: each changed or created member, whole. Per staged bundle, a member index, which is a
function of the bundle and is written once beside it, and a bundle record (Decision 4). The tier's
marker. In passing, the temporary names of renames in flight and the spool of a rebuild.

### (c) Which steps change the durable tier, and why each is atomic

*Follows from:* S3; Decisions 2 and 4.

Each step is bracketed intent, act, confirm, and each act is a rename into place or a single object
write, so a kill between an act and its record leaves an unnamed file to discard, never a name that
resolves to the wrong bytes.

* **Staging a pulled bundle.** The index is renamed into place first, then the bundle, so a bundle
  under its staged name always has its index. A kill before the database commit leaves a staged
  file no row names (argued; the prototype did not inject it).
* **Landing an altered file.** A temporary name, a sync, a rename. A kill before the database
  commit left the view equal to the tree before the change, and a file no row names.
* **Writing a bundle record.** The same. A kill between the rename and the database commit leaves
  the record one change ahead of the database and no further (argued; not injected).
* **A rebuild.** A spool, one object write (atomic per key), one transaction that records the bundle
  clean. A kill after the write and before the transaction leaves the bundle recorded as changed,
  and a second rebuild sent one write and gave the same tree. When a new staged bundle is written, it
  takes a new name and one transaction switches to it before the old one is removed; kills before
  and after that switch all left a view equal to the oracle.
* **Dropping** a clean staged bundle or a retired altered file, under the same bracket.

The one step that left wrong bytes behind a name was a rename of the new staged bundle over the old
one's name: a kill before the commit left the recorded offsets pointing into the new file. Decision 2
forbids it. A directory rename moves no bytes of an unchanged member (Decision 3); an altered file
kept by name under that directory adds one POSIX rename beside the database commit, and a kill
between them left a row naming a missing file. Kept by entry id there is no such step. Which of the
two the durable tier uses is `issues/namespace-sync-redesign.md`'s to decide.

### (d) What the durable tier's limits count

*Follows from:* C1; Rulings A and C; Decision 1.

By size: every staged bundle at its staged, uncompressed size, every altered file, the records and
indexes, and a spool while it exists. By count: one per staged bundle and one per altered file.
What cannot leave under pressure: an altered file not yet in a bundle written back, which is the only
copy of its bytes. A staged bundle with changes leaves only after its rebuild. After a rebuild that
wrote no new staged bundle, the old one and its altered files are correct only together and leave as
one unit. A clean staged bundle is dropped, since the source, one tier up, holds it, so Ruling C needs
no special case at this tier. Whether a spec must state the limits or gets defaults stays with
`issues/eviction-one-hop.md`.

### (e) What a rebuild reads, and what triggers one

*Follows from:* S4, S5, C2, C3; Rulings B and F; Decision 2.

A rebuild reads the database for what the bundle should hold and the durable tier for the bytes
(Decision 2), and nothing else: in the prototype it read the state database and the durable tier's
directory, with one read from the source to pull and one write to put the bundle back. Three things
trigger one:

* the bundle's own delay, which is the durable tier's write-back delay counted from the last change
  that reached the tier for that bundle, bounded by `sync.max_frequency`;
* pressure on the durable tier, when a bundle with changes is chosen for reclaim;
* a commit, which rebuilds every changed bundle with the clocks collapsed.

A push into the durable tier is never itself a trigger (Ruling B).

### (f) One bundle or many, and SFTP

*Follows from:* S1.

A source is one object, whose contents are the mount's root, or a prefix of many, each one top-level
directory named for its stem. The bundle is the unit that is pulled, staged, counted, dropped and
rebuilt, so ten thousand bundles change nothing in the model but the count. A source reached over
SFTP is an inner location: where a bundle's object is fetched whole and replaced whole. Nothing below
the source differs. When each is built is `ROADMAP.md`'s to say: both are queued in its section
*Queued: the bundle loop, driven locally*, after one bundle works end to end, and neither waits for
the first release.

### (g) What a top durable tier shared read-only between mounts needs

*Follows from:* C6; Ruling I.

The prototype ran two processes, each standing for a read-only mount with a state of its own, over
one shared durable tier and one bundle (`spec/plan-the-bundle-loop/prototype/RECORD.md`, on the
helper at `a0c88f1`): 504 of 504 processes read the oracle's bytes. What sharing needs:

* **The marker.** A kind that says staged bundles shared for reading, a format number, and the
  identity of the source as the spec gives it. No overlay id, tier name, generation, writer or time,
  so its text is a function of the source and two mounts that arrive together write the same bytes;
  with a writer id and a time in it, one mount read a marker that was then replaced. A mount refuses
  a marker of another kind or naming another source.
* **The member index.** One flat file beside each staged bundle, written by whoever pulled it and
  renamed into place before the bundle. Each mount's database keeps a row for the bundle it reads and
  for the copies it made below, and no member rows.
* **The pull.** A staged name that carries the source object's signature, so a bundle changed at the
  source is staged beside the old one and a mount reading the old one is not disturbed. A fetch to a
  name unique to the process and a rename into place. A lock file that only saves a second fetch,
  lapses at a stated timeout, and is removed only by its maker. The record's fetch counts: one after
  the other, 1; two mounts started together with a rename only, 2 in every run; with a lock file, 1
  in every run; with a lock timeout shorter than a pull, 2. A mount killed during the pull left only
  temporary files, which no later mount took for a bundle.

No mount evicts from the tier, counts its limits against it, or alters a file in it; reclaiming it
is an explicit command. What it costs a mount to learn a bundle's signature (a request per bundle,
one list per mount, or none if it trusts what is staged) is an open choice of
`issues/shared-staged-tier.md`.

### (h) What the durable tier records about itself, and what a mount's database holds given that

*Follows from:* S3, C6; Ruling D; Decision 4.

In its own files: the marker (whose tier this is, or that it is shared), each staged bundle under a
name that says which source object it is, each bundle's index, each bundle's record, and the
altered files. The database (Ruling D) holds everything that changes: the namespace, every copy on
every tier and which is fastest, what is modified and waiting, the intents, the lifecycle row. It is
the authority while it exists. The tier's files are what a rebuild takes its bytes from, what a
mount after a kill checks the database against (Decision 5), what a second mount reads instead of
scanning, and what is left to recover from if the database is lost: with the database deleted, the
tier's files and the record rebuilt the oracle, and the files alone did not. A record is written
when the tier is brought up to date for its bundle, so it may be behind the database and is never
ahead of it by more than the change being recorded.


Architecture
------------

*Follows from:* S1, a mount over a stack of tiers, and Ruling D, one database; each subsection names its own.

Four layers, strictly separated so the core is testable without a mount:

```
┌────────────────────────────────────────────────────────┐
│ FUSE adapter (mfusepy Operations)          src/…/fuse/ │  thin: translates FUSE
├────────────────────────────────────────────────────────┤  callbacks to engine calls
│ Overlay engine                           src/…/engine/ │  namespace ops, read/write
│   promotion · write-back · eviction · locks · transfer │  paths, policy machinery
├────────────────────────────────────────────────────────┤
│ Tier protocol + adapters                   src/…/tier/ │  posix · s3 · tar · zip
├────────────────────────────────────────────────────────┤
│ State store (SQLite, WAL)                 src/…/state/ │  catalog · residency · LRU
└────────────────────────────────────────────────────────┘  dirty queue · quota usage
```

The state store opens in WAL, as drawn. *Overturned by Decision 6, which
`issues/state-store-journal-mode.md` builds; the tree still does this.*

### Tier protocol

*Follows from:* S1 and S4, the source and how a bundle reaches it; Decisions 1 and 2.

A small, purpose-built protocol we own (no fsspec). Sketch:

```python
class Tier(Protocol):
    name: str
    def stat(self, path: PurePosixPath) -> TierStat | None: ...
    def list(self, path: PurePosixPath) -> Iterator[TierEntry]: ...
    def read(self, path, offset: int = 0, size: int = -1) -> bytes: ...   # range-ready
    def open_read(self, path) -> BinaryIO: ...                            # streaming
    def put(self, path, source: BinaryIO | Path, *, meta: FileMeta) -> None: ...
    def set_meta(self, path, meta: FileMeta) -> None: ...  # apply mode/owner/mtime to an existing object
    def delete(self, path) -> None: ...
    def mkdir(self, path) / rmdir(self, path) / rename(self, old, new): ...
    def usage(self) -> TierUsage: ...          # bytes + file count for quota accounting
```

Adapters:

* **PosixTier** — `os`/`pathlib`; the staging tiers additionally expose direct passthrough
  file handles so hot read/write I/O on a resident copy does not funnel through `put`/`read`.
* **S3Tier** — obstore (`get`, `get_range`, `put`, `list`, `head` map 1:1 onto the protocol).
* **ArchiveTier** — stdlib `zipfile`/`tarfile` over an inner *location* (POSIX path or S3
  object). Two operating modes, matching the two patterns in GOAL.md:
  1. **Direct-update** (ZIP, uncompressed TAR): the archive is the tier. A commit re-packs
     the archive in place — the live member set (the changed member applied, deleted members
     dropped) is written to a temp beside it, fsynced, and renamed over the original — so the
     temp+rename atomicity invariant holds and the archive is always compact (no superseded
     entries or tombstones accumulate; compaction is implicit in the re-pack). This suits the
     low, batched write frequency of an archive source (one re-pack per commit/flush, never
     per-file — PLAN.md → Deferred commit); the member index is rebuilt from the new archive.
  2. **Staged re-sync** (tar.gz and any compressed/remote archive): at mount the whole
     archive is pulled to the durable tier just below the source and decompressed once to an
     uncompressed `.tar` there, the staged bundle, which sits in the tier's root and is counted by
     its limits (Decision 1); a member read seeks to its data offset in that `.tar` (no repeated
     decompression). The source catches up by a rebuild from recorded state, compressed and
     pushed in one write (Decision 2; § *The tier model* (c) and (e)): a single atomic PUT for
     S3, where a key is never partially visible, and temp+rename for POSIX, bounded by
     `max_frequency` and forced at a commit. The pull and the rebuild are O(whole bundle); a
     source of many bundles (below) bounds that cost to the touched bundle.

     The bundle lives at `.terrace/bundles/<source tier>.tar` in the durable tier's root, a
     name no overlay entry can take, and is never listed. A mount that finds it absent pulls it
     under a journal intent and counts it as one file at its uncompressed size; a mount that finds
     it in place pulls and counts nothing.

     The tree differs in two places, kept here as the record of what it does. Every member is
     indexed into SQLite (data offsets in `archive_index`, full metadata in the catalog), so
     `find`/`stat`/`readdir` are served from the state store alone, and nothing on the durable tier
     records the index, a removal or a rename. *Overturned by Decision 4 and the tier model's (b),
     which `issues/durable-tier-record.md` builds; the tree still does this.* Every mutating engine
     call over an archive source is refused `EROFS`, so the adapter's per-call re-pack of the
     staged `.tar` is unreachable, and `resync`, which recompresses it and pushes it to the source,
     has no caller in the engine. *Overturned by Decision 2, which
     `issues/namespace-sync-redesign.md` (the model) and
     `issues/archive-finalization-integration.md` (the triggers) build; the tree still does this.*

  **Sharded sources** (`prefix/*.tar.gz`) split a dataset into independent per-subtree archives
  so a write reconstructs only its shard. `ROADMAP.md` queues them in its section *Queued: the
  bundle loop, driven locally*, after one bundle works end to end, not after the first release
  (§ *The tier model* (f); `issues/sharded-archive-sources.md`). Each shard maps to one top-level
  directory whose name matches the shard's stem — strictly enforced: the mount is rejected if a
  shard lacks that single top-level directory, and for a sharded source the mount root holds
  only shard directories (root-level files are refused). The shard set is dynamic (glob at
  mount; a top-level create/delete adds/removes a shard, new shards in the source's default
  format). Shards are pulled and indexed lazily, one at a time, only when their subtree is first
  entered, and the index persists across remounts — so an unread shard among thousands costs
  nothing but the initial listing. (A lone, non-sharded archive presents its contents at the
  mount root and carries no single-top-level-dir requirement.)

### Overlay engine

*Follows from:* S2, C1, C2 and C4, what moves a copy; Rulings A to C; Decisions 2 and 3.

The engine is the product. It exposes a synchronous VFS-operations API (`getattr`, `readdir`,
`open`, `read`, `write`, `create`, `unlink`, `rename`, `truncate`, `fsync`, `chmod`, `chown`,
`utimens`, …) consumed by the FUSE adapter and, directly, by the test suite.

**Read path.** Locate the fastest resident copy. Default policy promotes on open
(synchronously, whole-file) into the fastest tier whose size-bypass threshold and quota admit
the file; reads are then served from the resident copy via a passthrough handle.

**Write path.** Writes land on the fastest eligible tier (promoting or creating the file
there first). The file is marked dirty against its upstream boundary; the write-back scheduler
owns everything after that. `fsync` guarantees durability in the resident tier and journals
state; it does not force an upstream commit by default (`fsync_policy = "resident" | "commit"`).

**Write-back.** Dirty state is tracked per tier boundary and cascades: a write in `/dev/shm`
with a pass-through policy commits immediately to `/tmp`, which then ages against its own
60-second quiescence clock toward `/scratch`, and so on. Each commit is one hop: a copy moves to
the tier directly above and no further (Ruling B), and a bundle at the source is rebuilt on
triggers of its own (§ *The tier model* (e)). Policies per boundary:

* `delay` — commit after this much write inactivity (the grace period, e.g., `"60s"`).
* `max_interval` — an upper bound between commits even under constant writes (e.g., `"15m"`).
* `mode = "delayed" | "passthrough"` — pass-through tiers never delay (write-through).
* Archive re-sync tiers: `max_frequency` (e.g., `"4/day"`) bounds full re-creations.

Namespace operations (create, unlink, rename, mkdir, rmdir) and metadata mutations
(chmod/chown/utimens) propagate upstream through the same journaled pipeline, so a rename or a
mode change is durable in the catalog immediately and reaches the source on the same policy
clock as data. A metadata mutation records to the catalog first (metadata before
acknowledgment), applies to the fastest resident copy, then enqueues a `meta` write-back op
carrying only the changed attributes; the source receives it through the tier's `set_meta`.
Because no bytes change, a `meta` op never marks content dirty — it rides the namespace-op
queue, not the residency dirty bit. Ownership is best-effort (an unprivileged mount cannot
`chown`): the catalog stays the authority and each tier applies what its privileges allow,
mirroring `put`'s metadata handling.

**Deferred-op replay.** This is what the tree does for every source, and the record of it. For a
bundle source a change is recorded state and the bundle is rebuilt from it, and nothing is replayed.
*Overturned for a bundle source by Decisions 2 and 3, which `issues/namespace-sync-redesign.md`
builds; the tree still does this.* Namespace ops are entry-keyed and deferred: the catalog moves the
moment the call returns, the source follows when the queued op commits. Because the source
lags, the physical location of an entry's source copy can differ from its current catalog path
during the window a rename is queued but not yet committed upstream. The engine records that
physical location explicitly — each entry carries a *committed source path* (durable catalog
state: the path at which the source tier holds its bytes, `NULL` until the source holds a copy
at all). Adoption sets it to the entry's path; a deferred rename leaves it untouched (the
source has not moved); committing a rename upstream moves the source object from the committed
source path to the entry's *current target* — its parent's committed source path (or the
parent's own target) plus its name, not the path the op recorded — and advances it, rewriting the
prefix for every descendant of a renamed directory since one source rename relocates the whole
subtree; a chain of queued renames collapses into one move; a removal targets it. A data commit
to the source writes where the source already holds the entry, else at its target, and records
that place in the transaction confirming the copy, so a queued rename stays the only thing that
moves a source object. Replay resolves each entry's current place because the queue is a lossy
history: removing an entry cancels its rows, so replaying recorded paths would re-create
ancestors nobody owns and read the kind and mode of paths since reused; a queued `mkdir` creates
the directory at its target, placing any ancestor the source lacks with that ancestor's own
catalog mode. No two entries share a physical place: before a new or moved name takes a place,
the queued ops that still hold it (a removed entry's removal, another entry's pending move) are
replayed in queue order, as a prefix of the queue, so a parent's `mkdir` or a child's move-out
always runs first; a directory's removal likewise first replays the queued move or removal of
anything its source object still holds. A replay acts on, and settles, only the queued row it read:
row ids are reused, so a row a user op has since run or cancelled, or whose id a newer row took,
changes nothing, and a removal landing during the act keeps its own queued op. *Overturned by
Ruling G, row ids never reused, which `issues/namespace-sync-redesign.md` builds; the tree still
does this.* Reading the source
resolves the same way: the size reported for a file served off the source, live while it is open
and recorded when it is released, is read at its committed source path. Deferred removals and renames therefore resolve against
where the source physically holds the data, not where the catalog now says the entry lives — so
removing or superseding an entry that still carries an uncommitted rename deletes or moves the
real source object instead of orphaning it at the pre-rename path (the divergence classes
characterized in `tests/engine/test_replay.py`). The committed source path is authoritative over
the op log, superseding the earlier historical-path-in-`detail` shim; the recorded path stays
for the audit only. Both write-ordering invariants hold unchanged: the source-path update
commits in the same transaction as the op it records (metadata before acknowledgment), and every
source mutation stays bracketed intent → act → confirm. Full catalog↔source reconciliation is
deliberately *not* adopted on the flush path — it remains the audit's job
(`issues/audit-command.md`), which reuses the committed source path to move rather than re-copy a
relocated clean source object.

**Eviction.** Every stage tier has a limit on bytes and on file count (Ruling A), with high/low
watermarks (default 95%/85%) to avoid thrash. When the high watermark is crossed, the eviction
manager reclaims least-recently-used copies until below the low watermark, and each reclaimed copy
is pushed one tier up and no further (Ruling B). A dirty copy is committed to the tier directly
above and dropped once it is clean there; the tier above starts its own delay for it, and makes no
request further up. A clean copy is kept in the tier above when that tier has room (Ruling C), and
dropped when the tier above already holds it, so the catalog's fastest copy is then the one a tier
up. A push into a tier that is itself full moves the pressure up, which the model accepts. At the
durable tier the tier above is the source: a clean staged bundle is dropped, and one with changes is
rebuilt first (Decision 1). A copy that is the most-upstream holder of new data is never dropped.
Files exceeding a tier's `bypass_size` are never staged into it. Whether a spec must state the
limits or gets defaults, and what a clean copy does at a full neighbour, are
`issues/eviction-one-hop.md`'s to decide.

The tree commits a dirty copy all the way to the source when it is evicted (`commit_upstream`).
*Overturned by Ruling B, which `issues/eviction-one-hop.md` builds; the tree still does this.* It
drops a clean copy (`evict_copy`), and a later read goes to the next copy up. *Overturned by
Ruling C, which `issues/eviction-one-hop.md` builds; the tree still does this.* Both limits are
optional, and a stage tier with neither is never reclaimed. *Overturned by Ruling A, which
`issues/eviction-one-hop.md` builds; the tree still does this.*

**Concurrency.** libfuse runs a multithreaded callback loop; the engine uses per-file locks
(an in-memory lock registry) plus background components in the hypershell idiom — FSM-driven
`Thread` subclasses: `WritebackScheduler`, `EvictionManager`, and a `TransferPool` executor
for copies. On free-threaded 3.15 builds, FUSE callbacks and transfers run genuinely in
parallel; on GIL builds the workload is I/O-bound and degrades gracefully.

### State store

*Follows from:* C6; Rulings D and G; Decision 6.

Stdlib `sqlite3`, WAL mode (`synchronous=NORMAL`), plain SQL schema (`schema.sql`), per-thread
connections. Tables (sketch): `entry` (namespace + metadata), `residency` (entry × tier, atime
for LRU, clean/dirty, recorded size/mtime and optional blake2b checksum), `writeback` (queued
commits and namespace ops, timestamps for delay/max-interval math), `intent` (in-flight
mutations of managed locations), `tier_usage` (running byte/file counters), `archive_index`
(member offsets for TAR/ZIP), and a single `overlay` row (UUID, generation, `shutdown_state`,
mounting host). *Overturned by Decision 6, a rollback journal with `synchronous = FULL`, which
`issues/state-store-journal-mode.md` builds; the tree still opens WAL with `NORMAL`.*

It is the one database of Ruling D, which holds everything: the namespace, every copy on every tier
and which is fastest, what is modified and waiting, the intents and the lifecycle row. Row ids are
never reused (Ruling G); `issues/namespace-sync-redesign.md` owns the schema version bump that makes
them so, taken before any state database is deployed. The tables' integer keys are reused today.
*Overturned by Ruling G, which `issues/namespace-sync-redesign.md` builds; the tree still does
this.*

Two write-ordering invariants make the database authoritative after any process death:

* **Metadata before acknowledgment.** A clean→dirty transition commits to SQLite before the
  triggering FUSE write is acknowledged; subsequent writes to an already-dirty file incur no
  metadata cost. Residency changes likewise commit before anything depends on them. WAL makes
  these transactions survive SIGKILL; `synchronous=NORMAL` suffices because the harsher cases
  (kernel panic, power loss) also evaporate the volatile tiers, which the audit handles anyway.
  *Overturned by Decision 6, which `issues/state-store-journal-mode.md` builds; the tree still
  does this.* SQLite documents a committed transaction as durable across a process crash in every
  journal mode; `FULL` is chosen because a released node is nearer a system crash.
* **Intent journaling.** Every mutation of a managed location — promotion, commit, eviction,
  compaction, archive re-sync — is journaled as *intent → act → confirm*, with the act made
  atomic via write-to-temp-name + fsync + rename (a single atomic PUT for S3, which is atomic
  per key). A crash at any point leaves either a resolvable intent row or an orphan temp the
  audit can discard.

Managed locations are marked: each tier root carries a `.terrace/marker` file recording the
overlay UUID, tier name, and a generation counter. A missing or mismatched marker tells the
audit the tier evaporated wholesale (node reboot wiping `/dev/shm`) rather than files going
missing one by one. The state directory itself must live on durable storage — never inside a
volatile tier; `terrace mount` refuses a spec that puts it in one. By Ruling D that storage is seen
by every host of the workflow, and the directory is outside every tier, which the spec parser
already enforces for every tier root.

An archive source carries no marker of its own while writes over it are refused: it has no root to
hold one, and its registration records it by type, with the location's identity and signature, which
a later mount must match or be refused. What the overlay owns over a tar.gz source, the staged
bundle, sits in the durable tier under that tier's marker. The cycle that first lets a write reach an
archive source settles where a direct-update archive's marker lives.

The namespace is populated lazily: directory listings hit the catalog first and fall through
to the source tier on first touch (archives index once — ZIP central directory, TAR single
scan — cached in `archive_index`). No eager full scan at mount.

### The state store on shared storage

*Follows from:* C6 and Ruling D, which put the one database on storage every host sees; Decision 6.

The one assumption the redraw could not check. No network filesystem was available to the cycle
that wrote it, so this section states what is assumed, what was run and where, what changes if the
assumption fails, and the check that settles it on the filesystem that will hold the state
directory. The evidence is `spec/plan-the-bundle-loop/research/01-state-store-on-shared-storage.md`.

**What is assumed.** Of SQLite, Decision 6: a rollback journal, `journal_mode = DELETE`, with
`synchronous = FULL`. Of the filesystem that holds the state directory: POSIX advisory locks on the
database file that every host of the workflow sees and that die with the process that held them,
and an `fsync` that reaches the server. The state-directory lock (`flock` on `<state>/lock`) is
assumed on one host only, as its own docstring says; exclusion between hosts rests on the lifecycle
row, read and set in one transaction, and on the workflow having one host at a time (Ruling D). The
check below reports whether `flock` is seen across hosts as well; the design does not lean on it.

**What was run, on a local filesystem.** In the cycle's plan step, on a laptop's local filesystem
with SQLite 3.53.1, connections configured as the tree configures them: a process killed with `SIGKILL` left
every commit it had acknowledged for the next process to read, 301 of 301 in the tree's mode and 300
of 300 in the assumed one; a second process was refused the write lock while the first held it; a
reader on its own connection did not wait on an open write transaction in either mode; a commit
waited 1.010 s on a reader's 1.0 s open transaction in the assumed mode and 0.000 s in the tree's;
one write transaction took a median of 0.285 ms in the assumed mode against 0.022 ms in the tree's.
On 2026-11-16 the check below was run once more as written, all four commands on one laptop in a
directory on its local filesystem, and printed the five lines that follow it.

**What was not run.** Nothing was run on a network filesystem, and the assumption is
unverified on a network filesystem: locks seen across hosts, a dead process's locks released,
`fsync` reaching the server, and the cost of a commit there. Measuring throughput across SQLite's
options is deferred until the project is more mature (Decision 6, ruled at sign-off).

**What changes if it fails there.** By the failing line of the check. Locks not seen across hosts:
the lifecycle row and the one-host-at-a-time workflow are the only guard, and a commit from another
host refuses to start while the row reads `mounted` on a live host. Acknowledged rows missing,
integrity failed, or a dead process's locks still held: the database cannot stay on that filesystem
while mounted. It is then kept node-local while mounted and written to shared storage at a soft
unmount, the alternative Ruling D turned down, which returns as the fallback; a kill then loses the
node's database, and recovery reads the durable tier's own files, which Decision 4 makes possible.
Ruling D is the maintainer's to revisit on that evidence; this document does not reopen it.

**The check.** Run on the filesystem that will hold the state directory: host A is a compute node,
host B a login node. Python 3.6 or later, standard library only; each line prints the SQLite
version, so prefer the interpreter the product runs on. `wal` as a last argument to every step runs
the tree's mode instead, WAL with `synchronous = NORMAL` (the default mode runs a rollback journal with
`FULL`); its step B1 opens a WAL database from a second host while the first is
live, which SQLite documents as unsupported, so only ever in this throwaway directory. The issue
`issues/hpc-deployment.md` carries the check with its question on the state store.

```
D=$HOME/terrace-sqlite-check        # both hosts; must not exist
mkdir "$D" && cat > "$D/check.py" <<'EOF'
import fcntl, os, sqlite3, sys, time
d, role, mode, N = os.path.dirname(os.path.abspath(__file__)), sys.argv[1], (sys.argv[2:] or ['delete'])[0], 200
def db():
    c = sqlite3.connect(os.path.join(d, 'state.db'), timeout=1, isolation_level=None)
    c.execute('PRAGMA journal_mode=' + mode)
    c.execute('PRAGMA synchronous=' + ('NORMAL' if mode == 'wal' else 'FULL'))
    return c
def flock():
    f = open(os.path.join(d, 'lock'), 'a+')
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return f
    except OSError: return None
def put(c, i): c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO t VALUES (?)', (i,))
who = '%s sqlite %s %s' % (os.uname().nodename.split('.')[0], sqlite3.sqlite_version, mode)
if role == 'a':        # host A: commit N rows, then hold an open write transaction and the flock
    held, c = flock(), db()
    assert held, 'flock not granted on host A'
    c.execute('CREATE TABLE t (i INTEGER PRIMARY KEY)')
    for i in range(1, N + 1): put(c, i); c.execute('COMMIT')
    put(c, N + 1)
    print('A  %s: acknowledged %d, holding write lock and flock' % (who, N), flush=True)
    time.sleep(3600)
elif role == 'b1':     # host B, while A holds
    free = flock() is not None
    try: put(db(), N + 2); wrote = 'ACCEPTED'
    except sqlite3.OperationalError as e: wrote = 'refused (%s)' % e
    print('B1 %s: flock %s, write %s' % (who, 'FREE' if free else 'held elsewhere', wrote))
    print('B1', 'HOLDS' if not free and wrote == 'refused (database is locked)' else 'FAILS')
elif role == 'b2':     # host B, after kill -9 of A
    free, c = flock() is not None, db()
    n, top = c.execute('SELECT count(*), max(i) FROM t').fetchone()
    put(c, N + 3); c.execute('COMMIT')
    ok = c.execute('PRAGMA integrity_check').fetchone()[0]; c.close()
    print('B2 %s: flock %s, rows %d of %d acknowledged (max %d), write ok, integrity %s'
          % (who, 'free' if free else 'STILL HELD', n, N, top, ok))
    good = free and n == N and top == N and ok == 'ok'
    if good:
        for f in os.listdir(d): os.remove(os.path.join(d, f))
        os.rmdir(d)
    print('B2', 'HOLDS, nothing left behind' if good else 'FAILS, files kept in ' + d)
EOF
python3 "$D/check.py" a &           # host A; wait for its line
python3 "$D/check.py" b1            # host B
kill -9 %1                          # host A
python3 "$D/check.py" b2            # host B
```

Holds when the output is:

```
A  <host> sqlite 3.53.1 delete: acknowledged 200, holding write lock and flock
B1 <host> sqlite 3.53.1 delete: flock held elsewhere, write refused (database is locked)
B1 HOLDS
B2 <host> sqlite 3.53.1 delete: flock free, rows 200 of 200 acknowledged (max 200), write ok, integrity ok
B2 HOLDS, nothing left behind
```

The `<host>` field is the host's short name. The `wal` run printed the same five lines with `wal`
for `delete` on the same laptop. What a different line means:

| Line | Meaning |
|---|---|
| A stops on `flock not granted`, or on an `OperationalError` | The filesystem refuses the lock or the journal outright. Nothing is assumed safely there |
| B1 `flock FREE` | `flock` is not seen across hosts. The state-directory lock guards one host only, as its docstring says; cross-host exclusion rests on the lifecycle row alone |
| B1 `write ACCEPTED` | SQLite's write lock is not seen across hosts. Two hosts at once can corrupt the database; nothing but the workflow keeps them apart |
| B1 `write refused (…)` with another message | The lock call itself fails on this filesystem. Record the message |
| B2 `flock STILL HELD`, or it stops on `database is locked` | A killed process's locks outlive it. A mount after a kill cannot open the state |
| B2 `rows` below 200, `max` not 200, or `integrity` not `ok` | A commit acknowledged on host A is not what host B reads. The database cannot stay on this filesystem while mounted |

Single-host variant: all four commands in one shell on one node, in a directory on the same
filesystem. It shows the locks are granted, a killed process's locks are released, and acknowledged
rows survive. It does not show that another host sees any of it. The check uses 200 rows and one
kill: it can show the assumption fails, and passing once does not show the locks are right under
every timing. A node that loses power or is fenced is not simulated.

### FUSE adapter

*Follows from:* S1, the mount.

mfusepy's high-level path-based API. Notable mappings: `statfs` reports the source tier's
capacity (or configured quota for unbounded sources like S3) so `df` is meaningful;
per-file tiering state is exposed read-only via xattrs (`user.terrace.tiers`,
`user.terrace.dirty`) for observability and testing.

The mount records permission bits and does not enforce them: it passes no `default_permissions` and defines no `access` callback.

### Control channel

*Follows from:* Ruling D, the state directory that holds the socket.

A Unix domain socket in the overlay's state directory, serving a small line-oriented JSON
protocol. `terrace status` and `terrace flush [path]` talk to the live mount through it; `status`
can also read the SQLite state directly when no mount is running.


Lifecycle and recovery
----------------------

*Follows from:* S3, S5, C3 and C5; Rulings E and F; Decisions 4 and 5.

Named tiers are managed locations: the overlay owns their contents, and the state database is
the authority on what is where. The lifecycle design follows from that principle, and from the
batch job the story is written for: it ends on a signal or at its time limit, its node is released,
and the next job, on another node, or a commit from a login node starts where it stopped.

### Shutdown

*Follows from:* C3 and C5; Ruling E.

| Trigger | Behavior |
|---|---|
| `terrace umount`, SIGINT | **Drain**: policy clocks collapse (all dirty state becomes immediately committable), re-sync rate limits are waived, and the write-back pipeline pushes everything to the source in loss-risk order; over a bundle source that ends in a rebuild of every changed bundle (§ *The tier model* (e)). Unmount, set `shutdown_state = clean`. Zero loss, source finalized. |
| `terrace umount --detach`, SIGTERM | **Soft unmount** (Ruling E): stop now, finalize later. Every modified file on a `scope = "node"` tier is committed one tier at a time to the durable tier (the *rescue*), and in-flight transfers resolve or roll back. The database then records every modified file on the durable tier and no copy on any tier below it, so the next mount, on any node, needs nothing from a node tier; each staged bundle with changes has its bundle record written (Decision 4). `shutdown_state = detached`. `--detach` completes the rescue; SIGTERM bounds it by `drain_timeout` (schedulers send SIGKILL shortly after). Metadata is consistent — no audit needed; finalize later with `terrace commit`. |
| Second SIGINT mid-drain | Downgrade the drain to a soft unmount. |
| SIGKILL / OOM / node loss / a job's time limit | Nothing runs. `shutdown_state` still reads `mounted` — the crash flag, exactly a filesystem dirty bit. The next mount or commit recovers before it does anything else (§ *After a kill*). |

Both the drain and the rescue move data in **loss-risk order**: dirty data on the most volatile
tiers first, then upstream hop by hop — so whatever time budget exists is spent where data
would otherwise be unrecoverable.

After a drain the source is final; after a soft unmount every newest version lives on the durable
tier awaiting `terrace commit`. Only a crash leaves real exposure, and it is neither silent nor
unbounded: the loss window at each boundary is capped by its write-back policy (`delay` /
`max_interval` / `passthrough`) plus whatever rescue SIGTERM completed, making the policy knobs an
explicit trade between upstream write amplification and exposure at the fast end. A rescue that its
budget cuts short has not done what a soft unmount promises; how that is recorded, and how the
daemon tells `--detach` from a scheduler's SIGTERM, are `issues/shutdown-rescue-gaps.md`'s to
decide.

The tree does the first half of a soft unmount and not the second: after a complete rescue it still
records a copy on every node-scope tier the file passed through, and a mount where those tiers are
empty is refused. *Overturned by Ruling E, which `issues/shutdown-rescue-gaps.md` builds; the tree
still does this.* The same seed owns three defects in these paths, recorded in `AGENTS.md`
§ *Known open violations*: a second SIGINT, a rescue cut short by its budget and `--force` each
settle `detached` without a finished rescue, and `--detach` is bounded by `drain_timeout` as SIGTERM
is.

### Deferred commit (`terrace commit`)

*Follows from:* S5 and C3; Ruling F.

The motivating pattern: a GPU job stages and writes against fast tiers, computation finishes,
and the write-back policies haven't pushed recent outputs upstream. A full drain would finalize
the source but burn expensive allocation time on data movement — worse when the source is a
bundle to rebuild and put back over S3 or SFTP. Instead:

```bash
# last line (or trap) of the GPU job script — seconds to minutes:
terrace umount --detach spec.toml  # soft unmount: node-local changes to scratch, stop cleanly

# later, from a single-CPU job or a login node — off the GPU bill:
terrace commit spec.toml           # finalize scratch → the source
```

`terrace commit` is the offline finalizer and what the story calls a commit (S5): with no mount
running (exclusive lock on the state database), from any host that sees the durable tier and the
state directory, it flushes everything back up the stack. Over a bundle source it rebuilds every
bundle that changed, puts each back in one write, and leaves the staged bundles where they are, so
the next mount does not pull them again (Ruling F). Over a POSIX or S3 source it drains the queued
write-back state to the source.

* After a soft unmount it proceeds directly — metadata is trusted, no audit walk. After a crash
  it recovers first, as a mount does (§ *After a kill*).
* **Efficient by construction**: policy clocks and rate limits don't apply; transfers run in
  parallel; a checksum-recorded file whose content already matches upstream is skipped; and each
  changed bundle is rebuilt once from recorded state (Decision 2), not re-packed per file.
* `--direct` collapses hops: the newest version of each file copies straight to the source and
  stale intermediate copies are invalidated. The default cascades hop-by-hop, leaving shared
  intermediate tiers warm and consistent for the next mount.

Cross-host caveats: the state directory is on storage every host sees (Ruling D), so commit can
run elsewhere. The `overlay` row records the mounting host, so a `scope = "node"` tier seen from
another machine reads as *unreachable*, not evaporated — data stranded there (a crash without
rescue) is reported by hostname, and rolling it back to the last committed version requires an
explicit `--forfeit`, since a recovery or a mount back on that host may still find it.

In the tree `terrace commit` is a stub: `issues/offline-commit-command.md` builds the command, and
`issues/archive-finalization-integration.md` the rebuild it calls.

### After a kill

*Follows from:* S3, a halted job recovers; Decision 5.

A mount that finds `shutdown_state = mounted` recovers and mounts; it does not stop (Decision 5).
It trusts the database, since a commit acknowledged before the kill is in it, and checks the tiers
against it: a node tier that is gone, or that is another host's, loses its recorded copies; a
modified file whose newest copy was there goes back to the version the durable tier holds and is
named in a loss report; an unresolved intent is rolled back or finished; the durable tier is checked
against the bundle records (Decision 4). Then it serves. A read-only mount has nothing to lose and
recovers the same way with an empty report. The walk is the audit's, below, run in the mount.

If the state database itself is lost, which is the fallback § *The state store on shared storage*
names, recovery reads the durable tier's own files: the staged bundles, the altered files and the
bundle records are enough to rebuild what each bundle holds (Decision 4; § *The tier model* (h)).

In the tree `begin_mount` raises `CrashDetected` on `mounted` and nothing catches it, so the next
mount fails: `AGENTS.md` § 8 calls finding `mounted` an unconditional stop. *Overturned by Decision
5, which `issues/audit-command.md` builds, with its read-only slice `issues/read-only-mount.md`; the
tree still does this.*

### Audit

*Follows from:* S3, a halted job recovers; Decisions 4 and 5.

`terrace audit <spec.toml>` reconciles the state database against the managed locations. It runs
standalone with no mount (taking an exclusive lock on the state database), and every mount and
commit after a kill runs the same code first (Decision 5; `--no-audit` skips, `--audit=full`
deepens). The walk goes from the fastest tier upstream — freshest data first, since the fast end
determines what still needs committing:

1. **Marker check.** Verify each tier root's `.terrace` marker (overlay UUID, generation). A
   missing marker invalidates every residency row for that tier at once and attributes losses
   to tier evaporation rather than per-file mystery. After a soft unmount no row is recorded on a
   node-scope tier (Ruling E), so a node tier that is missing or empty there loses nothing.
2. **Reconcile.** Compare tier contents against residency rows. Recorded-and-present copies
   are kept — clean copies must match recorded size/mtime (checksum under `--full`); dirty
   copies are trusted as-is, since their bytes *are* the newest version. Recorded-but-missing
   copies are dropped; if such a copy was the most-upstream holder of dirty data, the file
   rolls back to the version the durable tier holds and lands in the loss report.
   Present-but-unrecorded files are either recognizable transfer temps (discarded) or true
   orphans (quarantined and reported — a managed-location violation). On the durable tier the
   staged bundles, their indexes, their records and the altered files are checked against what
   the database says each bundle holds (Decision 4).
3. **Resolve intents.** In-flight transfer/eviction/compaction intents roll back or complete
   idempotently. An interrupted rebuild is sent again from the staged bundle and the altered
   files: the source object is replaced in one write, so a second rebuild gives the same tree
   (§ *The tier model* (c)).
4. **Rebuild counters.** Recompute `tier_usage` counters; requeue surviving dirty state and
   pending namespace operations.
5. **Report.** Per-tier summary (verified / recovered / lost / orphaned) plus an explicit loss
   report naming each file rolled back and the age of the restored version. Exit codes: 0
   consistent, 1 repaired, 2 losses or attention required.

Modes: `--fast` (default: markers, existence, size/mtime) and `--full` (verify clean copies
against blake2b checksums, when `checksum = true` recorded them at commit time). The audit
repairs as it goes; `--dry-run` reports only. The audit never moves data upstream —
finalizing the source is `terrace commit`'s job, and commit invokes the audit itself after a
crash.

The audit is idempotent and is the same code standalone and at mount: after a crash at *any*
point, audit + remount must converge to a consistent state with an accurate loss report. The
fault-injection suite that is to assert exactly that is unbuilt
(`issues/crash-fault-injection-suite.md`; § *Testing strategy*). In the tree `terrace audit` is a
stub and `journal.pending()` has no consumer; `issues/audit-command.md` builds the walk, and the
durable tier's checks need the bundle record `issues/durable-tier-record.md` defines.


Configuration
-------------

*Follows from:* S1 and C1; Rulings A and J; Decision 1.

Two TOML surfaces, both cmdkit-backed:

1. **Application config** (logging, defaults) with hypershell-style precedence:
   CLI > environment (`TERRACE_*`) > `.terrace.toml` > `~/.config/terrace/config.toml` >
   `/etc/terrace/config.toml` > defaults.
2. **Overlay spec** — the tier stack definition, passed to `terrace mount`. Order in the file
   is stack order, source first:

```toml
[overlay]
name  = "myspace"
state = "~/.local/state/terrace/myspace"  # durable storage; shared storage enables cross-host commit
drain_timeout = "5m"                      # SIGTERM rescue budget (SIGINT always drains fully; 0 disables)
checksum = false                          # record blake2b at commit; enables `terrace audit --full`

[[tier]]                      # upstream terminus — authoritative
name = "project"
type = "posix"                # posix | s3 | zip | tar | tar.gz
path = "/project/space"
role = "source"

[[tier]]
name  = "scratch"
type  = "posix"
path  = "/scratch/space"
quota = { size = "10TB", files = 1_000_000 }
writeback = { delay = "60s", max_interval = "15m" }

[[tier]]
name  = "tmp"
type  = "posix"
path  = "/tmp/space"
scope = "node"                # node-local: rescued at detach, unreachable from other hosts
quota = { size = "512GB", files = 1_000_000 }
writeback = { delay = "60s" }

[[tier]]
name  = "shm"
type  = "posix"
path  = "/dev/shm/space"
scope = "node"                # (default scope is "shared")
quota = { size = "64GB", files = 1_000_000, bypass_size = "8GB" }
writeback = { mode = "passthrough" }
```

Archive sources compose a type with an inner location:

```toml
[[tier]]                                  # direct-update ZIP as source
name = "archive"
type = "zip"
role = "source"
location = { type = "posix", path = "/project/some.zip" }
```

```toml
[[tier]]                                  # staged re-sync tar.gz on S3 as source
name = "cold"
type = "tar.gz"
role = "source"
location = { type = "s3", url = "s3://bucket/space.tar.gz" }
sync     = { max_frequency = "4/day" }

[[tier]]                                  # the durable tier: the one shared stage tier, below the source
name  = "scratch"
type  = "posix"
path  = "/scratch/space"
quota = { size = "10TB", files = 1_000_000 }    # counts the staged bundle at its uncompressed size
writeback = { delay = "60s", max_interval = "15m" }
```

```toml
[[tier]]                                  # sharded source: one archive per top-level subtree
name    = "imagenet"
type    = "tar.gz"
role    = "source"
sharded = true
location = { type = "s3", url = "s3://bucket/imagenet/*.tar.gz" }
sync     = { max_frequency = "4/day" }
```

A staged bundle lives in the durable tier directly below the source, in the tier's root, and the
tier's limits count it (Decision 1). The spec names no path for it: the `staging` key is retired,
and a spec that carries it is refused with a message naming the tier that now holds the bundle.
Every stage tier states a size limit, a count limit and a write-back delay (Ruling A), as each
example does; whether a spec must state them or gets defaults is `issues/eviction-one-hop.md`'s to
decide. A source of many bundles is queued in `ROADMAP.md` after one bundle works end to end, not
after the first release (§ *The tier model* (f); `issues/sharded-archive-sources.md`).

The two single-bundle examples load as written: the ZIP above one shared POSIX stage tier, the
tar.gz as shown. The sharded example is refused at load, naming the tier and its seed,
`issues/sharded-archive-sources.md`. Over any archive source the parser requires exactly one shared
POSIX stage tier directly below the source, refuses an archive type at a stage position, and refuses
`sync` on a `zip` or `tar` source, which has no re-sync to rate-limit. Over an archive source every
mutating engine call fails `EROFS`; through the mount a hard link stays `EPERM` and an xattr write
`ENOTSUP`, the FUSE layer's defaults (`issues/archive-journaling-and-locking.md`).


CLI surface
-----------

*Follows from:* C3 and C5; Rulings E and F.

`terrace` (cmdkit `ApplicationGroup`, hypershell pattern):

| Command | Purpose |
|---|---|
| `terrace mount <spec.toml> <mountpoint>` | Mount the overlay (`-f` foreground, `-d` debug; daemonizes by default; after a kill, recovers and then serves, Decision 5) |
| `terrace umount <spec.toml>` | Drain to the source, then unmount; `--detach` is a soft unmount: every modified file committed to the durable tier and nothing recorded below it, then stop (Ruling E; finalize later with `terrace commit`); `--force` skips even the rescue. Takes the spec, not the mountpoint: the daemon's pidfile in the spec's state directory records the mounting host, and that is what makes the cross-host guard possible (below) |
| `terrace status [<spec|mountpoint>]` | Per-tier usage vs. quota, dirty counts, transfer activity |
| `terrace flush [<path>]` | Force upstream commit now, against a live mount (whole overlay or subtree) |
| `terrace commit <spec.toml>` | Finalize the source offline, from any host that sees the durable tier and the state directory: rebuild every changed bundle and leave the staged bundles in place (Ruling F), or drain queued write-back state to a POSIX or S3 source (`--direct` collapses hops); recovers first after a crash |
| `terrace audit <spec.toml>` | Reconcile state DB against managed locations, no mount required; reports and repairs, never moves data upstream (see Lifecycle and recovery) |
| `terrace config get|set|which|edit` | Application config management (hypershell pattern) |

`umount` takes the spec, not the mountpoint, because the spec names the state directory, and the
daemon's pidfile there records the host that mounted it. That indirection is what makes the
cross-host guard possible: run on another host, `umount` refuses, naming the host to unmount it on
or `terrace commit` to finalize instead. A mountpoint is a path on one host and carries no such
record.

In the tree `mount` and `umount` are built; `status`, `flush`, `commit`, `audit` and `config` are
stubs, and each is intent owned by a seed: `issues/control-channel-and-status.md` (`status`,
`flush`), `issues/offline-commit-command.md`, `issues/audit-command.md` and
`issues/config-command.md`. A mount after a kill stops in the tree. *Overturned by Decision 5,
which `issues/audit-command.md` builds; the tree still does this.*


Technology decisions
--------------------

*Follows from:* the decision each row records; the archives and state-store rows also Decisions 2 and 6.

Decided 2026-07-01 with rationale; alternatives noted.

| Decision | Choice | Rationale / alternatives |
|---|---|---|
| Python baseline | **3.15** (beta when decided; final 2026-10-01), free-threaded build a first-class target | Research brief calls for modern innovations: lazy imports (PEP 810), `frozendict` (PEP 814), sampling profiler (PEP 799), stable free-threaded ABI (PEP 803). These four are intent, with no code behind them: none appears in `src/` or `pyproject.toml`, and the tree keeps its FUSE and S3 imports lazy by importing inside the functions that need them, not through PEP 810. CI on both builds. Rejected: 3.14 floor. |
| FUSE binding | **mfusepy**, threaded | Maintained fusepy fork (ratarmount's binding); high-level path API; libfuse 2/3 + macFUSE (dev on macOS); pure ctypes → runs unmodified free-threaded. Rejected: pyfuse3 (async/Trio, Linux-only, forces async through the engine); fusepy (unmaintained). |
| Tier abstraction | **Purpose-built `Tier` protocol** | Small owned surface; no third-party caching semantics fighting our tiering. Rejected: fsspec (~56-method API, tar read-only anyway); hybrid. |
| S3 SDK | **obstore** | Rust-backed; API maps 1:1 onto the protocol; strong async/throughput story. Free-threaded (cp315t) wheels absent (verified Phase 7); CI compiles obstore from source on 3.15t (see Risks). Rejected: boto3 (pure Python, but clunky/slow). |
| Staging granularity | **Whole-file + size bypass, range-ready protocol** | Simple and predictable for v1; `bypass_size` handles files larger than a tier; range ops in the protocol keep chunk-level staging a non-breaking future phase. |
| State store | **stdlib `sqlite3`** (WAL, plain SQL schema) | Zero deps, transparent, free-threading-friendly. Rejected: SQLAlchemy (house style, but heavyweight for a small embedded schema). *Overturned by Decision 6, a rollback journal with `synchronous = FULL`, which `issues/state-store-journal-mode.md` builds; the tree still opens WAL.* |
| Archives | **stdlib `zipfile`/`tarfile`** | ZIP/uncompressed-TAR direct-update via atomic in-place re-pack (temp+fsync+rename; compaction implicit, no tombstones). tar.gz/remote: staged re-sync — decompress once to a staged `.tar` on the durable tier, counted by its limits (Decision 1), offset-read, rebuild from recorded state + recompress + one atomic push (Decision 2; the tree re-packs per call, § *Tier protocol*), rate-limited. Sharded sources (`*.tar.gz`) and an SFTP inner location are queued in `ROADMAP.md` after one bundle works end to end, not after the first release; a Globus inner location is after it (`issues/extended-archive-sources.md`). Optimization path (true append-supersede, indexed gzip random access) is future work. |
| S3 testing | **moto-server** (unit, pure Python, real S3 HTTP → works with obstore) + **MinIO** container behind `@mark.integration` | Matches the unit/integration marker split. |
| CLI / config / logging / style | cmdkit `ApplicationGroup`; layered TOML + `TERRACE_*`; hypershell `Logger` w/ `TRACE`; `src/` layout, SPDX Apache-2.0 headers, 120-char lines, `from __future__ import annotations`, FSM + `Thread` idioms | House style per hypershell. hatchling + uv; Sphinx/furo docs; Keep-a-Changelog + SemVer. |
| Package / entry point | **`terrace`** | The project is Terrace, and its distribution, import package and console script are `terrace`. This overturns the earlier row, which recorded the placeholder name as the package and called the name unimportant: GOAL.md kept that name only "until a better name is chosen", and one was chosen on 2026-11-11, before any release and before any overlay was deployed. Renamed in one cycle with no compatibility code; CHANGELOG.md lists what moved. |


Testing strategy
----------------

*Follows from:* S3, recovery after a halt, and the decision each bullet tests.

* **Engine-first**: the bulk of the suite drives the VFS-operations API directly against
  tmpdir POSIX tiers — no mount, fast, portable (this is the payoff of the layered design).
* **Model-based**: hypothesis stateful tests run random operation sequences against the
  overlay engine and a plain-directory oracle, asserting namespace/content equivalence after
  quiesce+flush. This is the highest-leverage test for an overlay filesystem.
* **Policy tests**: virtual clock injected into the write-back scheduler and eviction manager
  so grace periods, max-interval and watermark hysteresis are tested deterministically
  (`tests/engine/test_policy.py`). Rate-limit behaviour is unbuilt: `parse_rate` is unit-tested
  (`tests/core/test_units.py`), and no write-back rate limit is implemented to exercise; the
  `max_frequency` bound on a bundle's rebuilds is `issues/archive-finalization-integration.md`'s.
* **FUSE integration** (`@mark.integration`, `tests/fuse/test_integration.py`): real mounts
  exercising POSIX tools against the mountpoint; xattr-based assertions on residency/dirtiness.
  Linux CI does not run them yet: no job runs `-m integration` and nothing starts MinIO, so they
  run on no automated machine (`issues/ci-integration-lane.md`).
* **S3**: moto-server in unit tests; MinIO container in integration tests.
* **The bundle loop**: `tests/loop/` stands the loop up against a local object store and runs it
  as nine ordered stages, from reaching the source through pressure, a change, a rebuild, a
  release and an offline commit to several bundles, each checked against a plain-directory oracle.
  A stage the tree cannot pass is a strict expected failure naming the seed that owns it, so the
  file is the loop's progress record. The unit lane runs it over the helper's own store; its MinIO
  half is an integration test (`tests/loop/test_scenario_minio.py`).
* **The design's prototypes**: two throwaway prototypes on the same helper, each against a
  plain-directory oracle, are the measured evidence for Decisions 1 to 4 and § *The tier model*
  (g): one bundle rebuilt from recorded state, with trees equal in names and bytes, and two
  read-only mounts over one shared durable tier, each reading the oracle's bytes. They are a
  record under `spec/plan-the-bundle-loop/prototype/`, not part of the suite.
* **Crash recovery**: fault-injection kill points (SIGKILL between intent, act, and confirm of
  every mutation type; mid-drain; mid-rescue; mid-re-sync) followed by audit + remount,
  asserting convergence and loss-report accuracy; tier-evaporation tests (wipe a staging tier
  wholesale, assert the audit attributes it to the marker and rolls back correctly). Unbuilt
  (`issues/crash-fault-injection-suite.md`): what exists is `tests/state/test_survival.py`,
  committed state surviving SIGKILL at two kill points, with no audit, no remount and no tier
  evaporation.
* **Detach/commit**: detach leaves an audit-free state with node-scope dirty data rescued;
  `terrace commit` from a simulated second host (node-scope tier paths absent) finalizes the
  source; `--direct` and the default cascade converge to byte-identical sources; drain and
  rescue honor loss-risk ordering under a constrained time budget (`tests/engine/test_drain.py`,
  `tests/engine/test_rescue.py`). The commit cases are unbuilt with the command
  (`issues/offline-commit-command.md`).
* **CI matrix**: Linux {3.15, 3.15t} and macOS each run `uv run pytest -n auto -m unit`; macOS
  is engine-only because no mount test carries the `unit` marker (no kext in CI). The
  free-threaded job runs the same suite, `tests/tier/test_concurrency.py` included, as every job
  does; its targeted concurrency stress tests are unbuilt
  (`issues/freethreaded-concurrency-stress.md`).


Risks and mitigations
---------------------

*Follows from:* the decision each risk names.

* **Python FUSE throughput** — this is a reference implementation, not a kernel bypass;
  mitigate with passthrough file handles for resident-copy I/O, free-threading, and a
  benchmark harness (`issues/benchmark-harness.md`) to keep honest numbers in the docs.
* **obstore free-threaded wheels** — *verified 2026-07-11 (Phase 7 start): absent.* obstore
  0.11.0 publishes `cp310`, `cp311-abi3`, `cp314-cp314t`, and PyPy wheels — no `cp315t`, and
  the abi3 wheel is not usable on free-threaded builds, so `uv sync --only-binary` on 3.15t is
  unsatisfiable. Mitigation is live: the CI 3.15t job installs a Rust toolchain and `uv sync`
  compiles obstore from source via maturin/pyo3 — confirmed to build and import with the GIL
  disabled (`_obstore.cpython-315t*.so`, `sys._is_gil_enabled() is False`), with uv's wheel
  cache reusing the build across runs. A boto3 adapter behind the same protocol remains the
  worst-case fallback should pyo3 free-threaded support regress.
* **3.15 is beta until October** — pin beta in `.python-version` via uv; the final lands
  mid-roadmap; nothing in the design depends on beta-only behavior.
* **macFUSE dev friction** (kext approval) — engine-first testing means mounts are rarely
  needed locally; FUSE-T is a fallback.
* **SQLite contention under heavy metadata load** — WAL + per-thread connections + batched
  dirty-flag updates; to be measured by the free-threaded stress suite
  (`issues/freethreaded-concurrency-stress.md`). *Overturned by Decision 6, which
  `issues/state-store-journal-mode.md` builds; the tree still opens WAL.* In the rollback journal a
  commit waits on every open read on another connection, which WAL never does, so that seed audits
  which engine reads hold a cursor open long enough to delay one. Measuring throughput across
  SQLite's options waits until the project is more mature (Decision 6, ruled at sign-off).
* **The state store is unverified on a network filesystem** — the one database sits on storage
  every host sees (Ruling D), and its locks, the release of a dead process's locks, an `fsync`
  that reaches the server and the cost of a commit there were checked on a local filesystem only.
  The check that settles it, and what changes if it fails, are in § *The state store on shared
  storage*; running it on the filesystem that will hold the state directory is
  `issues/hpc-deployment.md`'s.
* **A staged bundle is counted at its uncompressed size** — the durable tier's limits count the
  staged `.tar`, not the object at the source (Decision 1), and at 300 members of the prototype it
  was 6,420,480 bytes against 5,000 bytes of altered files. A size limit set from the source's
  compressed size admits fewer bundles than it appears to; pressure then drops clean staged
  bundles and the next access pulls them again, one read each.
* **tar.gz member reads are sequential-decompress** — acceptable for v1 (staged re-sync mode
  keeps hot data extracted anyway); indexed random access noted as future work.
* **Volatile tiers evaporate** (node reboot, `/tmp` purge policies) — recently written data can
  be lost with them; exposure is bounded by each boundary's write-back policy and reported
  precisely by the audit. Tighten `delay`/`max_interval` (or use `passthrough`) where the loss
  window matters more than write amplification.
* **Write-back namespace replay under path mutation** *(open in part: the data-commit orphan, the
  replay family, stale scheduler rows, the live stat and the handle-less `truncate`'s recording are closed;
  a removal racing a replayed `mkdir`, and the thread-level test stay open)* — kept as the record of
  a mechanism Decisions 2 and 3 retire for a bundle source and Ruling G's stable ids simplify;
  shortening this account stays with `issues/namespace-sync-redesign.md` (its draft R8). *Overturned
  by Decisions 2 and 3, which `issues/namespace-sync-redesign.md` builds; the tree still does this.*
  The account:
  the write-back queue is entry-keyed, so a deferred namespace op's target can drift from where
  the source physically holds the bytes while a rename is queued but uncommitted. Phase 8 added
  the committed source path and resolved deferred removals and renames against it, and recorded
  the class as resolved on the strength of a model whose only convergence checkpoint was
  `flush()`. It was not resolved: a data commit during the lag (an `fsync` under the commit
  policy, the eviction manager, a scheduler poll that reached the data row before the rename row)
  wrote at the logical path and orphaned the pre-rename object, which the next mount adopted as
  live data; `flush()` replayed queued ops at recorded paths that later ops had reused or removed
  (`EACCES`, a stray directory, `ENOTEMPTY` on re-creating a removed directory or removing one
  whose moved-out child still sat in it); and a scheduler snapshot made stale by a user op still
  ran. Those are closed (see Write-back → Deferred-op replay): replay resolves each entry's
  current target, a data commit to the source writes where the source holds the entry, the queued
  ops that hold a place replay before a new name takes it, one source-namespace mutex serializes
  every change to the source's namespace, and a stale row changes nothing. The mutex is held for a
  whole copy to the source, `_record` takes it to stat a copy served off the source, `open` takes it
  to open a copy served off the source, a promotion off the source takes it through its whole copy,
  and a handle-less `truncate` takes it for every cut, so a large source commit delays every `create`,
  `mkdir` and `rename` that reaches `_vacate`, the `open`, `release` or `fsync` of a source-served
  copy, every promotion off the source, and every handle-less `truncate`, until it finishes, and a
  promotion off the source, on `open` or a handle-less `truncate`, holds it for that copy in turn
  (`AGENTS.md` § 10; `issues/source-commit-staged-publish.md` proposes narrowing
  a commit's hold). A replay settles only the row it read, so a user removal landing during its act
  (removals take no source mutex) keeps the op it queued under the cancelled row's id, whichever of
  the scheduler, `flush`, a drain or a vacate ran the replay; a file whose bytes sit off its logical
  path reports the size its object holds (the live stat behind `getattr` and `_record` reads the
  committed source path); and `truncate` without a handle cuts it there, holding the mutex from its
  decision (the entry at its path, by path, id, kind and a purge watch, and the copy that serves it) through its
  recording, and never through a symbolic link at the file itself (the last path component); and
  `open` of a source-served copy and a promotion off the source look the entry up again under the
  mutex and open the object before releasing it, so a replay landing after `open`'s lookup leaves the
  call at the object's new place. A user `rename` holds the mutex from its `_vacate` through its catalog
  move, so no ancestor's catalog move lands between the cut's lookup and its recording. Each of the
  cut, that `open` and that promotion opens a purge watch before it names its entry, which records every
  purge from then on, and its lookup under the mutex refuses with `ENOENT` a file that took the entry's id
  after its purge and was carried onto the path during the call; a purge of another entry refuses none.
  `tests/engine/test_replay.py` guards each class deterministically, the concurrent ones by injecting
  the interleaving on one thread, through one helper thread, or (an ancestor's rename against the
  handle-less `truncate`) on two threads ordered by events at the mutex, and the hypothesis model
  (`tests/engine/test_model.py`) commits single entries, polls the scheduler, and checks convergence
  after draining by polls as well as at `flush()`.
  Still open: sizes that state databases written before the live-stat fix may hold wrong for a
  source-held copy (`issues/audit-command.md`). A rename moves its stage copies after releasing
  the mutex. A cut at the file's new path before they move, whose fastest copy is a stage copy not yet
  moved, raises `ENOENT` after its dirty mark, having cut nothing, when it serves that copy, and
  `TierError`, not an `OSError`, out of `_serve`'s promotion before any dirty mark when a faster tier
  admits the file (`issues/engine-error-mapping.md`); one whose fastest copy is on the source, or on a
  stage copy already moved, is cut and recorded (`issues/freethreaded-concurrency-stress.md`). A user
  `rmdir` landing while a replayed `mkdir` has
  placed its directory but not yet recorded it purges the entry under the replay, whose confirm then
  raises and leaves the directory on the source (pre-existing, reproduced by injection); queue-row
  identity is by value, not a unique key, so a reused entry id with an identical row at an
  identical clock reading would pass as the same row. Both, and the thread-level test of the mutex,
  are `issues/freethreaded-concurrency-stress.md` (R7, R9): the mutex is argued, and the races are
  exercised single-threaded only. Full catalog↔source
  reconciliation, including recovery of orphans already on a source, remains the audit's job
  (`issues/audit-command.md`), reusing the committed source path.


Phase outline
-------------

*Follows from:* Ruling J, the names; `ROADMAP.md` orders what is left.

A record of how the tree was first built, kept because commits and seeds still cite its phase
numbers. From 2026-07-01 to 2026-07-15 the project was built from a flat checklist of phases, under
the placeholder name Ruling J replaced. Phases 1 to 8 were completed:

1. **Scaffolding** (2026-07-01) — pyproject (hatchling, uv, 3.15 pin), src layout, core
   config/logging/platform modules, CLI skeleton, CI matrix, repo hygiene (AGENTS.md, skills).
2. **State store** (2026-07-01 to 2026-07-02) — SQLite schema, DB layer, catalog with lazy
   population; managed-location markers, `shutdown_state` flag, metadata-before-ack and
   intent-journal primitives.
3. **Tier protocol + POSIX tier** (2026-07-02) — protocol types, PosixTier, quota accounting.
4. **Overlay engine core** (2026-07-02) — namespace ops, read/write paths, promotion, multi-tier
   POSIX stacks; engine test suite + hypothesis model tests.
5. **Policies** (2026-07-03 to 2026-07-11) — write-back scheduler (delay, max-interval,
   passthrough), LRU eviction with watermarks, intent-journaled transfers, drain and rescue with
   loss-risk ordering, virtual-clock policy tests.
6. **FUSE adapter** (2026-07-11) — mfusepy Operations, mount/umount commands (`--detach`), signal
   handling (SIGINT drain, SIGTERM detach with rescue budget), statfs, xattrs, Linux integration
   tests.
7. **S3 tier** (2026-07-11 to 2026-07-12) — obstore adapter, moto-server unit tests, MinIO
   integration tests.
8. **Write-back namespace-replay hardening** (2026-07-12) — committed-source-path tracking for
   deferred renames and removals; dropped the model-test guard added in Phase 7.

Phase 9, the archive tiers, was begun and not finished (2026-07-12 to 2026-07-15): ZIP/TAR
direct-update by atomic re-pack, the tar.gz staged re-sync and the S3 inner location landed, but
no spec could reach the adapter and nothing in the engine called its re-sync. The three phases
planned after it (control and operations, hardening and release, extended archive sources) were
never started.

On 2026-11-04 the project adopted spec-driven cycles, and `ROADMAP.md` replaced the checklist. It is
the ordered index of seeded cycles, one entry per `issues/` seed, with no phase numbers. What was left
of the archive phase and the three after it is decomposed there into cycles, re-triaged on
2026-11-11 around the story; the cycles since are recorded under `spec/` and in git history. This
document names no phase ahead: what comes next is `ROADMAP.md`'s to say.


Future work (explicitly deferred)
---------------------------------

*Follows from:* Rulings H and I.

* Chunk-level partial staging (protocol already range-ready).
* File pinning (`terrace pin <path> --tier <name>`), per-path policy overrides.
* Indexed random access into compressed archives (ratarmount-style gzip indexes).
* Wrapping a non-conforming archive/shard (no single top-level directory matching the stem)
  into a synthetic subtree instead of rejecting the mount (`issues/extended-archive-sources.md`).
* pyfuse3 (or kernel passthrough) adapter for performance-critical deployments.
* Prometheus metrics endpoint; structured event log.
* Many writers on one overlay (Ruling H), and whatever coordination they need, a lease or lock
  service among them: scoped when reached (`issues/multi-node-mounts.md`). Sharing a top durable
  tier read-only between mounts is not deferred with it; it comes sooner (Ruling I,
  `issues/shared-staged-tier.md`).
