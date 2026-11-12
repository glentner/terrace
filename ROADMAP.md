# Roadmap

The ordered index of future cycles, in the order they should be taken. One entry per
`issues/{slug}.md`; each **Seed** points at the pre-shaped deferral that holds the evidence.
`code terrace feature` promotes one into a `spec/{slug}/GOAL.md`, and that promotion is where
appetite, non-goals and the R-IDs get negotiated. An entry here is a candidate, not a commitment.
When the cycle lands on `main`, `code terrace roadmap` retires the seed and removes its entry.

The software factory that runs these cycles — its skills, templates and method — lives in the
operator's KB, not in this repository. The repository commits only its own records: `AGENTS.md`
§ *Factory* and § *Invariants*, this file, `issues/`, and `spec/{slug}/` for work in flight.

Entries carry no numbers, and a cross-reference names the slug rather than a position. Retiring an
entry shifts everything below it, and an ordinal reference survives that shift still grammatical and
now pointing at the wrong cycle.

This file replaces the flat phase checklist that drove the project through Phase 8. Phases 1–8 are
complete and their content is in git history; what was left of Phases 9–12, and the output of the
invariant audit run while porting the software factory, is decomposed below into cycles sized to one
appetite each. Defects that audit reproduced are recorded in [`AGENTS.md`](AGENTS.md) § *Invariants*
§ *Known open violations*.

**The order was re-triaged on 2026-11-11**, after a review of the whole tree at `64faf27` and the
maintainer's statement of the same date about what the project is for. Between adoption and that date
every engine cycle worked on one mechanism, deferred namespace replay against a POSIX source, and no
entry outside it moved. Meanwhile the loop that sets the project apart — a dataset held as compressed
bundles at the source, read and written through the overlay, and re-aggregated into those bundles on a
delay — cannot be configured from a spec file, and nothing in the engine calls the re-aggregation. The
first section below redraws `PLAN.md` around that story and then drives the loop end to end on a
developer machine against a local object store. The lifecycle around the loop and the mount a researcher
uses follow. Sharing the staged bundles between read-only mounts may come early; many writers on one
overlay are for a later day. The maintainer agreed this order on 2026-11-11 and ruled the decisions that
gated it; what is still open is in the next section.

Unremediated security findings are indexed separately in `.security/ROADMAP.md`, which is gitignored.
See [`AGENTS.md`](AGENTS.md) for why.

---

## Decisions that gate the queue

Each is the maintainer's. Remove a bullet when it is ruled; the ruling's record is the seed it names.
Ruled on 2026-11-11, and recorded in the seeds: the order of the sections below; that pressure or an
expired delay moves a copy one tier up and no further, a clean copy included
([`issues/eviction-one-hop.md`](issues/eviction-one-hop.md)); what a soft unmount leaves
([`issues/shutdown-rescue-gaps.md`](issues/shutdown-rescue-gaps.md)); one database for everything, on
storage every host sees ([`issues/plan-the-bundle-loop.md`](issues/plan-the-bundle-loop.md)); row ids
that are never reused; and the namespace rebuild, with the two parked cycles folded into it
([`issues/namespace-sync-redesign.md`](issues/namespace-sync-redesign.md)).

- **Feasibility on a real cluster.** Whether compute nodes give ordinary users `/dev/fuse` and an unmount
  helper, how a Python 3.15 runtime arrives, and whether SQLite's locks and journal mode are sound on the
  home filesystem, where the one database is ruled to live. It does not gate the first section. It is
  minutes of checking, and it decides what the third section is
  ([`issues/hpc-deployment.md`](issues/hpc-deployment.md), questions 1, 2 and 6).
- **What follows the loop.** The lifecycle around it or the mount a researcher uses. Decided when the
  first section closes.
- **Retiring two held seeds.** `replay-cost-at-scale` and `replay-defensive-branch-coverage` describe a
  function the rebuild deletes. Retiring them waits for the rebuild to land and for the maintainer's
  confirmation.

## Queued: the bundle loop, driven locally

What sets the project apart, built as one thin slice before any part of it is hardened: a dataset
held as compressed bundles at an object-store source, read and written through the overlay, and
re-aggregated into those bundles on a delay. Everything here runs on a developer machine against a
local object store, engine first.

The story it builds toward, from the maintainer's statement and clarification of 2026-11-11 (quoted
in [`issues/local-bundle-loop.md`](issues/local-bundle-loop.md)): a spec points at an object store, or
a tape reached over SFTP, holding one bundle or ten thousand. Bundles are pulled lazily to shared
scratch and decompressed there without being unpacked. Single files land in the RAM tier as they are
read. Every stage tier has a size limit, a count limit and a write-back delay, and either pressure or
an expired delay moves a copy one tier up, never further: to node-local disk, then to scratch beside
the bundles. What is on scratch is durable, so a job that halts can recover. A bundle is repacked and
put back to its source on its own delay or under pressure on scratch. A signal commits what has
changed to the durable tier and releases the node; a commit from a login node then rebuilds the
bundles that changed and leaves the staged ones in place for the next mount.

### Rename `sosfs` to Terrace

**Seed:** [`issues/rename-to-terrace.md`](issues/rename-to-terrace.md) · `refactor` · appetite big

First, because the cost grows with every later cycle and the tree is quiet only while the parked cycles
are held: 498 occurrences across 83 files at `6419966`, before the factory added its own. The mechanical
two-thirds — distribution name, package directory, imports, class names, test modules, CI, prose — is a
rename. The other third is not: the `SOSFS_*` environment prefix, the `user.sosfs.*` xattr namespace, the
`.sosfs/marker` and `.sosfs/partial` directories in every managed tier root, and the default state path
are **on-disk and user-visible contracts**, and changing the marker directory without a decision about
existing overlays turns every managed location into an evaporated tier on the next mount. Nothing is
deployed, which is what makes that decision cheap now. Stable row ids were ruled in on 2026-11-11, and
the schema bump can ride with this format break. `PLAN.md` records `sosfs` as a technology decision
rather than an accident, so under the plan-drift rule this cycle reconciles `PLAN.md` before it touches
code.

### Drive the bundle loop end to end on a local object store

**Seed:** [`issues/local-bundle-loop.md`](issues/local-bundle-loop.md) · `test` · appetite small

The loop that sets the project apart has never run: bundles at an object-store source, staged and
indexed, read and written through the overlay, re-aggregated on a delay, and read back from the
bucket by a fresh engine. Every piece exists alone and none can be joined, so every decision about
the loop is made by reading. This cycle adds a helper that stands the loop up on a laptop with no
container, and one scenario whose stages are strict expected failures, each naming the seed that owns
it, including a stage where pressure moves copies up with no write to the source, and one where a
detach is followed by a commit from a second process that has only the durable tier. The rest of this
section turns those stages green in order.

### Redraw `PLAN.md` around the user story

**Seed:** [`issues/plan-the-bundle-loop.md`](issues/plan-the-bundle-loop.md) · `docs` · appetite big

`PLAN.md` and the invariants describe a different product from the one in the user story, and every cycle
is graded against them: eviction that commits to the source, changes replayed against the source one
operation at a time, a soft unmount that leaves the next node unable to mount, and sharding and SFTP
after the release. This cycle writes the contract the later ones build to: the tier model, what the
durable tier holds, the one database and where it lives, how a bundle is rebuilt, what a soft unmount and
a commit do, stable ids, and what sharing a staged tier between mounts needs. It writes no engine code.
Two throwaway prototypes on the local loop's helper keep its decisions from being made by reading. Before
any engine cycle, because a builder working toward the story today is working against its own contract.

### Reconcile `PLAN.md` against the implementation

**Seed:** [`issues/plan-md-reconciliation.md`](issues/plan-md-reconciliation.md) · `docs` · appetite small

Small and cheap, and it follows the redraw above, which absorbs most of it. It matters because
`code terrace review` grades a cycle against a contract shaped from `PLAN.md`, so every inaccuracy
left standing is a chance to grade correct code as wrong. The audit
found `PLAN.md` internally inconsistent in one place (its summary lists an `fsck` command its own CLI
table calls `audit`) and behind the code in several others: `umount` takes a spec file rather than a
mountpoint, and the implementation is the better design; `rich` and `frozendict` are named as
technology decisions and used nowhere; four of the eight testing-strategy bullets describe coverage
that does not exist. This cycle holds only the residue — the inaccuracies no other queued cycle will
touch. Anything a later cycle changes by doing its own work is reconciled there, in that cycle's own
diff, which is what the same-commit rule requires.

### Make archive tiers reachable from a spec file

**Seed:** [`issues/archive-spec-plumbing.md`](issues/archive-spec-plumbing.md) · `feature` · appetite big

`ArchiveTier` is adapter-complete and covered by 36 unit tests, and no user can configure it: the
spec parser admits only `posix` and `s3`, and the archive keys `location`, `staging` and `sync` are
rejected as unknown, so the three worked examples in `PLAN.md` all fail at parse time. `LocationSpec`
and `inner_location()` are implemented and reachable only from tests, and the engine's tier
construction has no archive branch. A shipped-but-unreachable adapter is worse than an unwritten one
because it reads as done. The loop's first stage: the read side alone — admit the types and keys,
construct the tier, index, read — turns the scenario's read stage green, and shaping may split the
cycle there.

### A write racing a commit can be recorded clean

**Seed:** [`issues/racing-write-marked-clean.md`](issues/racing-write-marked-clean.md) · `fix` · appetite small

A commit decides a copy is clean from a flag it resets and a stat it compares, and a write can pass
both: one that read the flag before the reset and lands after the stat, or one of the same size inside
one timestamp tick. Both reproduced by injection: the stage holds the new bytes, the source the old,
the residency reads clean and the queue is empty, so the next eviction deletes the write. Placed
ahead of the bundle write-back because a re-aggregation reads staged copies it believes are clean, so
a copy wrongly recorded clean becomes a bundle with stale bytes in it. Small, independent of the
namespace work, and how wide the second route is on a production kernel is one measurement away.

### Pressure or delay moves a copy one tier up, and no further

**Seed:** [`issues/eviction-one-hop.md`](issues/eviction-one-hop.md) · `fix` · appetite small

The maintainer's tier model: every stage tier has a size limit, a count limit and a delay, and either one
moves a copy exactly one tier up, which may cascade. The tree differs twice. Eviction commits a dirty
copy all the way to the source, which `AGENTS.md` § 7 states as a rule on a reason the code does not bear
out; for a bundle source that turns pressure in the RAM tier into a re-aggregation and a push per evicted
file. And eviction drops a clean copy, where the maintainer's ruling of 2026-11-11 keeps it one tier up,
so node-local disk catches what falls out of RAM. Small, and built before the re-aggregation that depends
on it.

### Rebuild how the namespace reaches the source

**Seed:** [`issues/namespace-sync-redesign.md`](issues/namespace-sync-redesign.md) · `refactor` · appetite big

Six landed cycles and a parked seventh worked on deferred namespace replay against a POSIX source,
and each found the next defect in it: 727 lines of source added, 3,281 lines of tests and some 19,000
lines of cycle records, and a directory rename still raises `IntegrityError` over a tombstone that
nothing can remove. The seed traces the family to three properties — ids that are reused, tombstones
that occupy names, and path locks that cannot see a directory rename — and proposes to remove them.
It sits here, ahead of the bundle write-back, because a bundle cannot take a replayed operation at
all: the model that fits it is to mark a bundle dirty and rebuild it from state, which in the bundle
case has no order to discover. So the model, the schema and the bundle source come first, and POSIX
and object-prefix sources move to it afterwards, when the replay is deleted. Shaping decides whether
that is one cycle or two. Its decisions are the plan cycle's; this is the build.
High-blast-radius as a whole: it changes the schema and the gated regions of `engine/overlay.py`.

### Bring archive mutations under the journal and a lock

**Seed:** [`issues/archive-journaling-and-locking.md`](issues/archive-journaling-and-locking.md) · `fix` · appetite big

Re-pack and re-sync mutate a managed location — the source archive, which for an archive source is
the whole dataset — outside any intent bracket. `'compact'` and `'resync'` exist in the journal's
action vocabulary with no producer. Alongside it, `ArchiveTier` has no tier-wide lock while
`engine/locks.py` is path-keyed, so two concurrent puts to different paths in the same archive both
snapshot the member set, both re-pack, and the second rename wins — a member vanishes with both calls
returning success. Two more in the same region: a stale staging copy silently wins over a newer
source, and the archive index cache is trusted unconditionally. Sequenced before the
finalization work, which adds callers to exactly these paths.

### Batch archive finalization at drain and commit

**Seed:** [`issues/archive-finalization-integration.md`](issues/archive-finalization-integration.md) · `feature` · appetite big

The flagship, in the maintainer's words of 2026-11-11: write-back with delayed re-aggregation into
the compressed bundles at the source. `PLAN.md` requires one re-pack per commit or flush and never
per file; the adapter re-packs on every `put`, `set_meta`, `delete`, `mkdir`, `rmdir` and `rename`,
and nothing batches. Staged re-sync's rate limit is waived at drain in the design and unreachable in
the code, because no engine module calls `resync` at all. With the synchronization model above in
place this is its first use: a change marks its bundle dirty, and the bundle's clock or a drain
rebuilds it from state and replaces it with one atomic write. It is not a small addition.

### Archive coverage at the engine and mount level

**Seed:** [`issues/archive-tier-test-coverage.md`](issues/archive-tier-test-coverage.md) · `test` · appetite small

The adapter is well covered in isolation, and the loop's scenario drives one bundle through the
engine. This closes the archive work immediately after the code that makes the tests possible: both
modes over POSIX, tar.gz over S3 against moto, index persistence across remounts, and reconstruction
correctness after a batched finalization. Re-scope at shaping: the scenario already holds the
end-to-end case, so what is left is breadth.

### Sharded archive sources

**Seed:** [`issues/sharded-archive-sources.md`](issues/sharded-archive-sources.md) · `feature` · appetite big

The loop at scale, in the owner's words of 2026-11-04: a center refuses a 100-million-inode quota
request and the researcher's dataset lives instead as ten thousand `.tar.gz` shards of ten thousand
images each, cascading down the tier stack just in time. Placed after one bundle works end to end,
because a shard is then both the unit that is staged and the unit that is re-aggregated, and each is
built once. That placement contradicts `PLAN.md`, which schedules sharding post-v0.1.0, so under the
plan-drift rule the cycle reconciles `PLAN.md` first. A streaming or ranged read in `InnerLocation`
is a prerequisite rather than an optimization: archive I/O is whole-blob in memory today, and a shard
of that size cannot pass through RAM. Two questions were added on 2026-11-11: whether a member is
served by offset from the staged shard instead of being unpacked to a file of its own, and how many
mounts may read one staged dataset.

### A bundle source over SFTP

**Seed:** [`issues/sftp-inner-location.md`](issues/sftp-inner-location.md) · `feature` · appetite small

The user story names a tape reached over SFTP beside an object store. Bundles are fetched and replaced
whole, which is how tape wants to be used, so the adapter is three methods; the open questions are
around it: the client library, atomic replacement over a rename, a recall that takes minutes while
other calls must not wait, and a local server for tests. Split out of the extended archive sources on
2026-11-11. Last in this section because it reuses the streaming read the sharding cycle builds.

## Queued: the lifecycle around the loop

Drain, detach with rescue, recovery after a kill, and a commit from another host: what lets a job stop at
once and finalize its source later. The engine half exists and the recovery half does not. This section
can run beside the next one; which leads is a decision for when the loop closes.

### The write-back scheduler dies on a source-only stack

**Seed:** [`issues/source-only-writeback-scheduler.md`](issues/source-only-writeback-scheduler.md) · `fix` · appetite small

On a stack with the source alone, `boundary_policies` has no entry for the source, so the scheduler's
`deadline` raises `KeyError` at its first scan after any namespace op and the pump dies. Found while
mapping the nested-rmdir replay shapes, and recorded there without a fix. Small, and it sits in the
scheduler the liveness work below changes.

### Nothing notices when a policy pump dies

**Seed:** [`issues/pump-liveness-and-loss-windows.md`](issues/pump-liveness-and-loss-windows.md) · `fix` · appetite big

A `StateThread` that raises stores the exception and exits its loop; the exception surfaces only on
`join`, and nothing monitors liveness — the mount watches the FUSE thread alone. A write-back
scheduler that dies on a transient tier error leaves the filesystem fully functional and accepting
writes while nothing ever commits upstream, which converts every bounded loss window into an
unbounded one, and the only evidence is one log line. Two configuration gaps belong with it because
they are the same failure seen from the operator's side: `max_interval` defaults to `None`, so writes
closer together than `delay` defer the commit forever, and the eviction watermarks `PLAN.md`
documents as defaults are hardcoded with no configuration key at all.

### A second SIGINT settles `detached` without rescuing

**Seed:** [`issues/shutdown-rescue-gaps.md`](issues/shutdown-rescue-gaps.md) · `fix` · appetite big

The second-Ctrl-C downgrade path settles `shutdown_state = detached` and never calls `rescue()`, so
every dirty file still queued on node-local storage is stranded behind a state that says it is safe.
Two related gaps travel with it: `umount --detach` is bounded by `drain_timeout` where `PLAN.md` says
it completes, because the daemon cannot distinguish it from a scheduler's SIGTERM; and an incomplete
rescue's stranded count is discarded rather than recorded. A note of 2026-11-11 adds that a forced
unmount settles the same state by design, so the state cannot carry the promise as written, and that
the shutdown is a state machine held in three flags. A second note of that date records a defect on
the main path: after a complete rescue the database still records copies on the node-local tiers, so
the next mount on another node is refused with "audit required". Reaching the documented semantics is
a design change and belongs in `PLAN.md` first.

### Small gaps behind inaccurate invariants

**Seed:** [`issues/invariant-audit-gaps.md`](issues/invariant-audit-gaps.md) · `fix` · appetite small

Six independent findings from the August audit, each too small for its own cycle and each a place
where an asserted property is not the one the code has. `residency.checksum` is never written though
the spec parses `checksum = true`, so `audit --full` would have nothing to verify against.
`tier.generation` never advances in production. `intent.temp_name` is dead and the archive's sibling
`.tmp` falls outside the orphan-recognition convention. `statfs` reports an entirely full filesystem
for an unquota'd non-POSIX source. There is no directory `fsync` anywhere, so a rename's *name* is not
durable even though its data is — a design decision rather than a drive-by fix. And `S3Tier.rename`'s
docstring claims an atomicity S3 does not have. Placed before the audit, which reads several of these
columns.

### `sosfs audit` — reconcile the state database against the tiers

**Seed:** [`issues/audit-command.md`](issues/audit-command.md) · `feature` · appetite big

The recovery half of a sacred invariant is unimplemented. `journal.pending()` has no consumer anywhere in
`src/`, `begin_mount` raises `CrashDetected` and nothing catches it — so after a crash the next mount
fails instead of recovering — and intents accumulate resolvable only by hand. The five-step walk is fully
specified in `PLAN.md`: marker check, residency reconcile, intent resolution, counter rebuild, report
with exit codes 0/1/2. `issues/read-only-mount.md` describes its smallest slice (markers, intents,
counters); whichever of the two is taken first builds it. It also defines what "converged" means for the
fault-injection suite and unblocks the commit cycle. High-blast-radius as a whole, being the only
component licensed to delete residency rows and quarantine files on its own reading of the world.

### `sosfs commit` — finalize the source offline

**Seed:** [`issues/offline-commit-command.md`](issues/offline-commit-command.md) · `feature` · appetite big

The project's differentiating feature and the thing the detach lifecycle was built for: drain the
queued write-back state to the source from any host that sees the shared tiers, off the expensive
allocation. Exclusive lock, direct after a detach, audit first after a crash, loss-risk ordering,
checksum-match skip, batched archive finalization, `--direct` hop collapse with stale-copy
invalidation, and `--forfeit` for data stranded on an unreachable node-scope tier. Directly
downstream of the audit. `PLAN.md` promises parallel transfers here and every copy in the tree runs on
its calling thread; the seed carries that contradiction.

### Control channel, `status` and `flush`

**Seed:** [`issues/control-channel-and-status.md`](issues/control-channel-and-status.md) · `feature` · appetite small

`src/sosfs/control/` does not exist. A unix-domain socket in the state directory serving
line-oriented JSON, wired into the mount daemon, with a client library; `status` reading it live and
falling back to a direct database read when no mount is running; `flush` forcing an upstream commit
for the whole overlay or a subtree. The first cheap entry after two big ones, and it reuses the
offline read path the audit cycle introduces rather than building it twice.

### Fault injection around every crash point

**Seed:** [`issues/crash-fault-injection-suite.md`](issues/crash-fault-injection-suite.md) · `test` · appetite big

The proof obligation for the audit and commit cycles, and the largest gap between the testing strategy
`PLAN.md` describes and the one that exists. Today there are three survival tests with two SIGKILL
points, no audit, no remount and no tier evaporation. What is needed: kill points between intent, act
and confirm of every mutation type, mid-drain, mid-rescue and mid-re-sync, each followed by audit and
remount, asserting convergence and loss-report accuracy; plus tier-evaporation cases that wipe a
staging tier wholesale and assert the marker attributes it correctly. Placed right after the
operations surface closes, so a defect it finds still has a natural home.

### Concurrency stress on the free-threaded build

**Seed:** [`issues/freethreaded-concurrency-stress.md`](issues/freethreaded-concurrency-stress.md) · `test` · appetite small

The 3.15t job runs the same suite as every other job; there are no targeted stress tests despite
free-threading being a first-class target. Parallel FUSE callbacks against transfers against eviction,
and race hunting with the sampling profiler. Until 2026-11-11 this seed also held the engine's known
races, several reproduced by injection; those are defects and now belong to `racing-write-marked-clean`
and `namespace-sync-redesign`. What stays here is the workload, which needs a surface that has stopped
moving.

### A priority lane in the transfer pool

**Seed:** [`issues/transfer-priority-lane.md`](issues/transfer-priority-lane.md) · `feature` · appetite small

`TransferPool` is one flat FIFO executor that nothing submits to: every copy in the tree runs on its
calling thread, measured during a drain on 2026-11-11. So there is no queue for a lane to reorder yet,
and shaping starts by deciding whether background copies use the pool at all, which the commit cycle
decides first. Last in this section for that reason.

## Queued: a mount a researcher can use

The surface between the engine and a person on a cluster: a mount that starts, files that are theirs,
a read-only mode for datasets that are never written, and a module. Its behaviour shows only on a real
mount, which is why the lane leads it.

### Run the integration suite somewhere

**Seed:** [`issues/ci-integration-lane.md`](issues/ci-integration-lane.md) · `ci` · appetite big

Ten tests — the entire drain, detach, force-umount, xattr and metadata-propagation surface, plus the
only real-S3 round trip — execute on no automated machine. CI has no integration job and nothing
starts MinIO. Worse, the obvious gate is a trap: `uv run pytest -m integration` exits 0 when every
test skipped, so a criterion verified that way proves nothing. Moved ahead of the mount-level fixes
below because each of them is a behaviour only a real mount shows: the daemonized branch has no test
at all, the owner of a created file is decided in the adapter, and a read-only mount is defined by
what the kernel is told. The orphan fix (landed 2026-11-06) and the handle-less truncate fix owe
mount-level cases this lane makes run. The bundle loop's scenario runs its MinIO and mounted halves
here. Needs a Linux runner with `/dev/fuse` and a MinIO service container, and a decision about
whether the lane blocks a landing or reports.

### The default mount fails on a first-ever mount

**Seed:** [`issues/daemonized-mount-first-run.md`](issues/daemonized-mount-first-run.md) · `fix` · appetite small

`_daemonize_and_serve` redirects its streams before `start()` runs, and the redirection opens
`mount.log` inside the state directory — which nothing has created yet, because the exclusive lock
and the engine create it. Measured: against a fresh `state =` path the documented happy-path command
exits 1 with `FileNotFoundError`; `mkdir -p` the directory first and the identical command mounts.
`-f` is unaffected, and every unit and integration test uses foreground mode or calls
`start()`/`shutdown()` directly, so nothing in the suite covers the daemonized branch at all. Small
fix, and the first thing a new user hits.

### Everything created through the mount is owned by root

**Seed:** [`issues/posix-surface-gaps.md`](issues/posix-surface-gaps.md) · `fix` · appetite small

`create` and `mkdir` default the owner to `0:0` and the FUSE adapter never passes the caller's
identity, so `ls -l` shows root on a user's own new files and the catalog disagrees with the source
it commits to. Measured through the engine API; the adapter half is read from the code. The same seed
carries symlink creation, which the engine can read and not perform, and the unsupported operations
that fail without a chosen errno. A conformance suite in the integration lane would find the rest of
this class by measurement.

### Non-`OSError` exceptions escape the VFS API

**Seed:** [`issues/engine-error-mapping.md`](issues/engine-error-mapping.md) · `fix` · appetite small

The engine promises `OSError` subclasses so the adapter can map errno directly. Two paths break it:
`_serve` raises `EngineError` when no resident copy exists, and writing through a handle to a file
that has been unlinked raises `LookupError` out of `mark_dirty`. The second is the ordinary
`tempfile` pattern — create, open, unlink, write — which POSIX requires to succeed, and the adapter
maps neither, so it reaches libfuse as an unhandled exception. The existing test covers only the read
case.

### A read-only mount

**Seed:** [`issues/read-only-mount.md`](issues/read-only-mount.md) · `feature` · appetite small

A mount that refuses every mutation has a contract small enough to ship: no copy is ever dirty, every
shutdown settles `clean`, and recovery after a kill resolves promotion intents and verifies or drops
stage copies, with nothing to report lost. That recovery is the read-only slice of the audit, and it
is on the deployment path regardless: a batch job that reaches its time limit is killed, and today
the next mount of the same overlay raises `CrashDetected`. The seed also states the question the
one-mount-per-overlay rule leaves open for this workload, where a multi-node job mounts on every node
and a sweep runs many jobs over one dataset; the next entry takes that up.

### Share the staged bundles between read-only mounts

**Seed:** [`issues/shared-staged-tier.md`](issues/shared-staged-tier.md) · `feature` · appetite big

The maintainer's suggestion of 2026-11-11 for the time being: the top durable tier shared read-only
between mounts, each with tiers of its own below. A multi-node job and a sweep then pull and decompress
each bundle once, where today every mount stages a copy of its own. It is tractable because a staged
bundle is a function of its source alone, so overlapping pulls are harmless and a lock only saves work;
one left by a killed mount can lapse. What forbids it today is the marker that ties a tier to one
overlay, a member index kept in each overlay's own database, and limits and eviction that assume one
owner. Requires a read-only mount, which is why it follows one.

### Opening a resident file costs three database writes

**Seed:** [`issues/read-path-metadata-writes.md`](issues/read-path-metadata-writes.md) · `perf` · appetite small

`open` advances the LRU clock and `release` rewrites the residency row and a usage counter, so every
read of a staged file passes through SQLite's single writer. Measured once through the engine API on
a laptop: about 3,100 to 4,300 opens a second against 35,900 on the stage directly, flat from four
threads up. For a dataset opened once per file per epoch this is the hot path, and the LRU clock is
the one figure in the catalog that is safe to batch. Taking the baseline is cheap at any time; the fix
belongs beside the read-only mount, whose workload it bounds.

### Deploy on an HPC cluster

**Seed:** [`issues/hpc-deployment.md`](issues/hpc-deployment.md) · `feature` · appetite big

The goal is a researcher who runs `module load terrace`, mounts at the top of a batch script, and
finds that it works — free, and maintained by the center. Its first two questions are in the
decisions above and are worth answering early, since they decide whether this section is engineering
or a policy conversation. What remains here is the modulefile, the job-script patterns, a mount per
node, and the batch scheduler's time-limit sequence. For a read-only mount that sequence needs no
rescue: the criterion is that a killed mount leaves an overlay the next job can mount. The
detach-then-commit pattern depends on the lifecycle section and joins when that closes.

## Queued: release

What turns the sections above into a tagged version.

### `sosfs config`

**Seed:** [`issues/config-command.md`](issues/config-command.md) · `feature` · appetite small

`get`/`set`/`which`/`edit` over the layered application config, on the existing `core.config`
primitives, following the hypershell pattern. The smallest remaining cycle and pure assembly. It
completes the CLI surface, so the documentation cycle has nothing stubbed left to describe.

### Benchmark harness and honest overhead numbers

**Seed:** [`issues/benchmark-harness.md`](issues/benchmark-harness.md) · `feature` · appetite small

Throughput and latency against direct tier access, published in the docs. `PLAN.md` commits to honest
overhead accounting for a Python FUSE implementation. The published table belongs here, measured on a
system believed correct. A baseline does not wait for that: the read-path entry above takes one, and a
note in the seed proposes splitting the two.

### Documentation and v0.1.0

**Seed:** [`issues/documentation-and-v0.1.0.md`](issues/documentation-and-v0.1.0.md) · `feature` · appetite big

Sphinx and furo: quickstart, overlay-spec reference, architecture from `PLAN.md`, an operations guide
built around the job-script patterns, and a CLI reference. Then release readiness — `CHANGELOG.md` for
0.1.0, `README.md`, verified build artifacts, and the version. Consumes the benchmark numbers and
documents a surface with nothing stubbed in it. Last before the tag. Whether a first tag follows the
bundle loop alone is a question for the maintainer once that section closes.

## Queued: after the first release

Wanted, and not in the first tag.

### Many writers on one overlay

**Seed:** [`issues/multi-node-mounts.md`](issues/multi-node-mounts.md) · `feature` · appetite big

Asked for in user consultations more than once, and named by the maintainer as the change that would
matter most for high-throughput work: the same spec mounted on many nodes, each writing, the nodes
negotiating at the durable tier with directory locks. Set aside by his ruling of 2026-11-11. Everything
lives in one database, one database has one writer, and "full multi-node writers is a challenge for
another day". The seed keeps the statement, the reason it is deferred, and what a first scoping pass
should decide. The reading half is not deferred; it is the shared staged tier in the section above.

### Extended archive sources

**Seed:** [`issues/extended-archive-sources.md`](issues/extended-archive-sources.md) · `feature` · appetite big

Post-v0.1.0 by design in both `PLAN.md` and the original phase outline. Two capabilities remain behind
one entry: a Globus inner location, which is a submit-and-wait transfer rather than a stream, and
synthetic-subtree wrapping for a non-conforming archive. Sharding, the priority lane and the SFTP
inner location were split out as each found a user. What is left has no one asking for it yet.

## Held for the namespace rebuild

Four seeds that repair or measure the mechanism
[`issues/namespace-sync-redesign.md`](issues/namespace-sync-redesign.md) replaces. Ruled on 2026-11-11:
the rebuild goes ahead, and the two parked cycles fold into it. The first seed below is therefore its
acceptance material and has no cycle of its own. The next two wait for the rebuild to land. The last is
shaped after it. Held, not retired: each keeps its file and its evidence.

### The model rarely reaches the scheduler and eviction commit routes

**Seed:** [`issues/model-commit-route-reach.md`](issues/model-commit-route-reach.md) · `test` · appetite small

Over the fixed CI draw the stateful model committed to the source through a scheduler poll's data row
zero times, and through the eviction manager zero times. Its cycle, parked at its plan stop on branch
`test/model-commit-route-reach`, was folded into the rebuild on 2026-11-11: widening the model's reach
found two engine defects in two dry runs, each of which opened a fix cycle. The widened model is the
instrument a rebuild should be judged by.

### A single replay's cost grows faster than its queue

**Seed:** [`issues/replay-cost-at-scale.md`](issues/replay-cost-at-scale.md) · `fix` · appetite big

Queues the nested-rmdir fix made drainable can take super-linear time to drain: hundreds of chains
removed level by level, or one deep chain removed outermost first, run for minutes in one replay,
holding the source lock throughout.

### Replay decisions no test distinguishes

**Seed:** [`issues/replay-defensive-branch-coverage.md`](issues/replay-defensive-branch-coverage.md) · `test` · appetite small

Mutants of `_replay_through`'s performance-only skips, its bottom-of-stack branch and some of its
bookkeeping survive every replay test. A coverage observation from the nested-rmdir review, not a
defect.

### Stage a source commit outside the source mutex

**Seed:** [`issues/source-commit-staged-publish.md`](issues/source-commit-staged-publish.md) · `feature` · appetite big

A data commit to the source holds the source mutex for the whole copy, so while a large file commits,
every `create`, `mkdir` and `rename` that reaches `_vacate` waits for it. Accepted as shipped on
2026-11-06 and seeded: stage the bytes outside the mutex and publish them inside it. How long that
mutex is held, and whether it survives, is one of the rebuild's four decisions.

## Terminal records

Deferrals considered and closed **without** shipping — `declined` and `accepted-behaviour`. Listed
apart from the ordered cycles so the index above stays an index of *work*. Read one before re-filing
the thing it describes. Work that shipped leaves no entry here: the code refutes a re-filing on its
own, and `spec/{slug}/` holds the account.

*(none yet)*
