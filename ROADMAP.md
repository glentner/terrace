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
complete and their content is in git history; what was left of Phases 9–12 is decomposed below into
cycles sized to one appetite each. Roughly the first third of this list is not from that checklist
at all: it is the output of the invariant audit run while porting the software factory, which
reproduced several defects the suite does not cover. Those are recorded in
[`AGENTS.md`](AGENTS.md) § *Invariants* § *Known open violations* and carried here as their own
cycles. The order is the triage of 2026-11-04, which also added the toolchain, sharding,
priority-lane and deployment entries.

Unremediated security findings are indexed separately in `.security/ROADMAP.md`, which is gitignored.
See [`AGENTS.md`](AGENTS.md) for why.

---

## Queued

### A commit before a queued rename orphans the source object

**Seed:** [`issues/deferred-rename-source-orphan.md`](issues/deferred-rename-source-orphan.md) · `fix` · appetite big

Reproduced. `_commit_hop` writes the destination at the entry's *logical* path and then advances
`source_path` without moving or removing the object still sitting at the old one; the queued rename
later runs with both sides equal and does nothing. The stale object survives, and the next mount
re-adopts it as live data — the exact divergence class `PLAN.md` declares closed in Phase 8. Reachable
from three production paths: `fsync_policy = "commit"`, the eviction manager committing a dirty copy
before the rename is due, and any write-back poll where the data row's deadline precedes the rename
row's, which is the normal case when the write preceded the rename. `flush` and `drain` mask it
because both run namespace ops first, and the hypothesis model test never sees it because its only
convergence checkpoint is `flush()`. Two smaller siblings share the cause: `truncate` without a
handle and the live-stat path both resolve against the logical path, giving `ENOENT` on a file that
exists and a permanently wrong size in the catalog. Highest-value fix on this list — it is silent
resurrection of deleted data behind a drain that reports success.

Moved up to follow the toolchain repair on 2026-11-05 (Geoffrey, at the toolchain-repair plan gate).
The first CI run read under the factory, 42023846602 at `6a046b4`, failed both Linux unit jobs on
`test_model.py` alone: a directory renamed into a sibling and back, then the sibling removed, makes
`flush()` raise `EACCES` from `_execute_namespace`'s rename replay and leaves a stray directory on the
source. It shows on macOS too: that run's macOS job failed the same test beside its `tests/fuse`
collection errors, every unit job of the cycle-branch run 42059324110 failed on it alone, and the
development Mac fails it on every run under `CI=true` and on some random seeds. Same replay family,
now reproduced by the model itself, so this cycle takes it too. Until it lands, that one test is the
only failure the landed toolchain repair accepts, in CI (R9) and in the Mac's `verify` (R2, R8); see
`spec/toolchain-repair/GOAL.md`.

### Rename `sosfs` to Terrace

**Seed:** [`issues/rename-to-terrace.md`](issues/rename-to-terrace.md) · `refactor` · appetite big

Immediately after the namespace-replay fix, because the cost grows with every later cycle: 498
occurrences across 83 files at `6419966`, before this harness added its own, and every entry below
writes more of them. The mechanical two-thirds — distribution name, package directory,
imports, class names, test modules, CI, prose — is a rename. The other third is not: the `SOSFS_*`
environment prefix, the `user.sosfs.*` xattr namespace, the `.sosfs/marker` and `.sosfs/partial`
directories in every managed tier root, and the default state path are **on-disk and user-visible
contracts**, and changing the marker directory without a decision about existing overlays turns every
managed location into an evaporated tier on the next mount. `PLAN.md` also records `sosfs` as a
technology decision rather than an accident, so under the plan-drift rule this cycle reconciles
`PLAN.md` before it touches code. The shipped-artifact question — whether anything is out there to
migrate — is what makes it cheap now and expensive after v0.1.0.

### Reconcile `PLAN.md` against the implementation

**Seed:** [`issues/plan-md-reconciliation.md`](issues/plan-md-reconciliation.md) · `docs` · appetite small

Small, cheap, and it comes early because `code terrace review` grades a cycle against a contract shaped
from `PLAN.md`, so every inaccuracy left standing is a chance to grade correct code as wrong. The
audit found `PLAN.md` internally inconsistent in one place (its summary lists an `fsck` command its
own CLI table calls `audit`) and behind the code in several others: `umount` takes a spec file rather
than a mountpoint, and the implementation is the better design; `rich` and `frozendict` are named as
technology decisions and used nowhere; four of the eight testing-strategy bullets describe coverage
that does not exist. This cycle holds only the residue — the inaccuracies no other queued cycle will
touch. Anything a later cycle changes by doing its own work is reconciled there, in that cycle's own
diff, which is what the same-commit rule requires.

### A second SIGINT settles `detached` without rescuing

**Seed:** [`issues/shutdown-rescue-gaps.md`](issues/shutdown-rescue-gaps.md) · `fix` · appetite big

The second-Ctrl-C downgrade path settles `shutdown_state = detached` and never calls `rescue()`.
`detached` is a promise — it tells a later `sosfs commit` that metadata is trusted and no audit walk
is needed — so every dirty file still queued on `/tmp` or `/dev/shm` is stranded behind a state that
says it is safe, and when the node is reclaimed the data is gone with no record that anything was
lost. Two related gaps travel with it: `umount --detach` is bounded by `drain_timeout` where
`PLAN.md` says it completes, because the daemon cannot distinguish it from a scheduler's SIGTERM; and
an incomplete rescue's stranded count is discarded rather than recorded. Reaching the documented
semantics needs a distinguishable "complete the rescue" signal, which is a design change and belongs
in `PLAN.md` first. Sequenced immediately after the orphan fix: both are silent data loss on the
differentiating lifecycle, and this one has no test that would catch a regression.

### Run the integration suite somewhere

**Seed:** [`issues/ci-integration-lane.md`](issues/ci-integration-lane.md) · `ci` · appetite big

Ten tests — the entire drain, detach, force-umount, xattr and metadata-propagation surface, plus the
only real-S3 round trip — execute on no automated machine. CI has no integration job and nothing
starts MinIO. Worse, the obvious gate is a trap: `uv run pytest -m integration` exits 0 when every
test skipped, so a criterion verified that way proves nothing, and the two data-loss defects above
sat in exactly the region this lane covers. Placed after them rather than before because the lane is
most valuable once it has regressions to protect, and both fixes should land with integration
coverage that this cycle then makes run. Needs a Linux runner with `/dev/fuse` and a MinIO service
container, and a decision about whether the lane blocks a PR or reports.

### The default mount fails on a first-ever mount

**Seed:** [`issues/daemonized-mount-first-run.md`](issues/daemonized-mount-first-run.md) · `fix` · appetite small

`_daemonize_and_serve` redirects its streams before `start()` runs, and the redirection opens
`mount.log` inside the state directory — which nothing has created yet, because the exclusive lock
and the engine create it. Measured: against a fresh `state =` path the documented happy-path command
exits 1 with `FileNotFoundError`; `mkdir -p` the directory first and the identical command mounts.
`-f` is unaffected, and every unit and integration test uses foreground mode or calls
`start()`/`shutdown()` directly, so nothing in the suite covers the daemonized branch at all. Small
fix, and the first thing a new user hits.

### Non-`OSError` exceptions escape the VFS API

**Seed:** [`issues/engine-error-mapping.md`](issues/engine-error-mapping.md) · `fix` · appetite small

The engine promises `OSError` subclasses so the adapter can map errno directly. Two paths break it:
`_serve` raises `EngineError` when no resident copy exists, and writing through a handle to a file
that has been unlinked raises `LookupError` out of `mark_dirty`. The second is the ordinary
`tempfile` pattern — create, open, unlink, write — which POSIX requires to succeed, and the adapter
maps neither, so it reaches libfuse as an unhandled exception. The existing test covers only the read
case.

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

### Small gaps behind inaccurate invariants

**Seed:** [`issues/invariant-audit-gaps.md`](issues/invariant-audit-gaps.md) · `fix` · appetite small

Six independent findings from the same audit, each too small for its own cycle and each a place where
an asserted property is not the one the code has. `residency.checksum` is never written though the
spec parses `checksum = true`, so `audit --full` would have nothing to verify against.
`tier.generation` never advances in production, so a generation mismatch can only come from external
tampering. `intent.temp_name` is dead — no call site passes it — and the archive's sibling `.tmp`
falls outside the orphan-recognition convention entirely. `statfs` reports an entirely full
filesystem for an unquota'd non-POSIX source, so `df` shows 100% and tools that pre-check free space
refuse to write to a working mount. There is no directory `fsync` anywhere, so a rename's *name* is
not durable even though its data is — a repo-wide omission, and changing it is a design decision
rather than a drive-by fix. And `S3Tier.rename`'s docstring claims an atomicity S3 does not have.

### Make archive tiers reachable from a spec file

**Seed:** [`issues/archive-spec-plumbing.md`](issues/archive-spec-plumbing.md) · `feature` · appetite big

`ArchiveTier` is adapter-complete and covered by 36 unit tests, and no user can configure it: the
spec parser admits only `posix` and `s3`, and the archive keys `location`, `staging` and `sync` are
rejected as unknown, so the three worked examples in `PLAN.md` all fail at parse time. `LocationSpec`
and `inner_location()` are implemented and reachable only from tests, and the engine's tier
construction has no archive branch. A shipped-but-unreachable adapter is worse than an unwritten one
because it reads as done — three Phase 9 checkboxes were true at the unit level and misleading at the
product level. This is the prerequisite for everything else archive-shaped.

### Bring archive mutations under the journal and a lock

**Seed:** [`issues/archive-journaling-and-locking.md`](issues/archive-journaling-and-locking.md) · `fix` · appetite big

Re-pack and re-sync mutate a managed location — the source archive, which for an archive source is
the whole dataset — outside any intent bracket. `'compact'` and `'resync'` exist in the journal's
action vocabulary with no producer, and the ROADMAP recorded the journaling as done. Alongside it,
`ArchiveTier` has no tier-wide lock while `engine/locks.py` is path-keyed, so two concurrent puts to
different paths in the same archive both snapshot the member set, both re-pack, and the second rename
wins — a member vanishes with both calls returning success. Two more in the same region: a stale
staging copy silently wins over a newer source because `_populate` returns early on the staging file
merely existing, and the archive index cache is trusted unconditionally, so a stale offset returns
arbitrary in-range bytes with no error. Sequenced before the finalization work because that work adds
callers to exactly these paths.

### Batch archive finalization at drain and commit

**Seed:** [`issues/archive-finalization-integration.md`](issues/archive-finalization-integration.md) · `feature` · appetite big

The last unchecked code item of Phase 9. `PLAN.md` requires one re-pack per commit or flush and never
per file; the adapter re-packs on every `put`, `set_meta`, `delete`, `mkdir`, `rmdir` and `rename`,
and nothing batches. Staged re-sync's rate limit is waived at drain in the design and unreachable in
the code, because no engine module calls `resync` at all. This is where the drain and commit paths
learn that an archive source is finalized once, at the end, rather than continuously — and it is not
a small addition.

### Archive coverage at the engine and mount level

**Seed:** [`issues/archive-tier-test-coverage.md`](issues/archive-tier-test-coverage.md) · `test` · appetite small

The adapter is well covered in isolation; nothing exercises an archive source *through the engine*,
because until the two cycles above land nothing can. This closes Phase 9 immediately after the code
that makes the tests possible, while the design is still in the author's head: both modes over POSIX,
tar.gz over S3 against moto, index persistence across remounts, and reconstruction correctness after
a batched finalization. Re-scope rather than transcribe the old checklist item — the unit-level half
it describes is substantially already done.

### Sharded archive sources

**Seed:** [`issues/sharded-archive-sources.md`](issues/sharded-archive-sources.md) · `feature` · appetite big

The flagship use case, in the owner's words of 2026-11-04: a center refuses a 100-million-inode quota
request and the researcher's dataset lives instead as ten thousand `.tar.gz` shards of ten thousand
images each, cascading down the tier stack just in time — a staged copy of a shard on durable storage
below the archive tier, a metadata parse on first crawl with the index persisted, the shard unpacked
only when its subtree is used, and one file pulled to the fastest tier on its first read. Placed
directly after the archive chain because it extends all three of those cycles and their test
fixtures, and ahead of the operations surface because it is what the project is for. That placement
contradicts `PLAN.md`, which schedules sharding post-v0.1.0, so under the plan-drift rule the cycle
reconciles `PLAN.md` first. A streaming or ranged read in `InnerLocation` is a prerequisite rather
than an optimization: archive I/O is whole-blob in memory today, and a shard of that size cannot
pass through RAM.

### A priority lane in the transfer pool

**Seed:** [`issues/transfer-priority-lane.md`](issues/transfer-priority-lane.md) · `feature` · appetite small

`TransferPool` is one flat FIFO executor, so an on-demand promote can wait behind every queued
background copy. Split out of the extended-archive-sources seed on 2026-11-04, as that seed's own
notes recommended: the lane benefits a plain POSIX stack exactly as much as an archive one, and
filed in an archive bucket it kept losing to work that was visible. Placed directly after sharding,
whose per-shard pulls are the heaviest on-demand transfers the product will make. Shaping starts by
establishing which on-demand path actually queues, because the synchronous promote-on-open runs on
the calling thread and never enters the pool.

### `sosfs audit` — reconcile the state database against the tiers

**Seed:** [`issues/audit-command.md`](issues/audit-command.md) · `feature` · appetite big

The only *live operational* gap on this list rather than unbuilt feature work: the recovery half of a
sacred invariant is unimplemented. `journal.pending()` has no consumer anywhere in `src/`,
`begin_mount` raises `CrashDetected` and nothing catches it — so after a crash the next mount fails
instead of recovering — and intents accumulate resolvable only by hand. The five-step walk is fully
specified in `PLAN.md`: marker check, residency reconcile, intent resolution, counter rebuild, report
with exit codes 0/1/2. It also defines what "converged" means for the fault-injection suite and
unblocks the commit cycle. It is high-blast-radius as a whole, being the only component licensed to
delete residency rows and quarantine files on its own reading of the world.

### `sosfs commit` — finalize the source offline

**Seed:** [`issues/offline-commit-command.md`](issues/offline-commit-command.md) · `feature` · appetite big

The project's differentiating feature and the thing the detach lifecycle was built for: drain the
queued write-back state to the source from any host that sees the shared tiers, off the expensive
allocation. Exclusive lock, direct after a detach, audit first after a crash, loss-risk ordering,
checksum-match skip, batched archive finalization, `--direct` hop collapse with stale-copy
invalidation, and `--forfeit` for data stranded on an unreachable node-scope tier. Directly
downstream of the audit.

### Control channel, `status` and `flush`

**Seed:** [`issues/control-channel-and-status.md`](issues/control-channel-and-status.md) · `feature` · appetite small

`src/sosfs/control/` does not exist. A unix-domain socket in the state directory serving
line-oriented JSON, wired into the mount daemon, with a client library; `status` reading it live and
falling back to a direct database read when no mount is running; `flush` forcing an upstream commit
for the whole overlay or a subtree. The first cheap entry after two big ones, and it reuses the
offline read path the audit cycle introduces rather than building it twice.

### `sosfs config`

**Seed:** [`issues/config-command.md`](issues/config-command.md) · `feature` · appetite small

`get`/`set`/`which`/`edit` over the layered application config, on the existing `core.config`
primitives, following the hypershell pattern. The smallest remaining cycle and pure assembly. Placed
here because it completes the CLI surface, so the documentation cycle has nothing stubbed left to
describe.

### Deploy on an HPC cluster

**Seed:** [`issues/hpc-deployment.md`](issues/hpc-deployment.md) · `feature` · appetite big

The goal is a researcher who runs `module load terrace`, mounts at the top of a batch script, and
finds that it works — free, and maintained by the center. Every question that decides how is
unverified: whether compute nodes expose `/dev/fuse` and a setuid `fusermount3` to ordinary users,
how a Python 3.15 runtime is delivered, how a modulefile ships, and what the job-script pattern is.
The one with an R-ID of its own is the batch scheduler's time-limit sequence — SIGTERM, then SIGKILL
after a site grace period — mapped onto the existing SIGTERM rescue-and-detach lifecycle, because
that mapping is the product's differentiator and today it is inferred from the signal handlers
rather than demonstrated. Placed after the operations surface closes, since the pattern ends in
`sosfs commit` and leans on the rescue and first-mount fixes above.

### Fault injection around every crash point

**Seed:** [`issues/crash-fault-injection-suite.md`](issues/crash-fault-injection-suite.md) · `test` · appetite big

The proof obligation for the audit and commit cycles, and the largest gap between the testing strategy
`PLAN.md` describes and the one that exists. Today there are three survival tests with two SIGKILL
points, no audit, no remount and no tier evaporation — and one of them performs the audit's job by
hand in a comment. What is needed: kill points between intent, act and confirm of every mutation
type, mid-drain, mid-rescue and mid-re-sync, each followed by audit and remount, asserting
convergence and loss-report accuracy; plus tier-evaporation cases that wipe a staging tier wholesale
and assert the marker attributes it correctly. Placed right after the operations surface closes,
so a defect it finds still has a natural home.

### Concurrency stress on the free-threaded build

**Seed:** [`issues/freethreaded-concurrency-stress.md`](issues/freethreaded-concurrency-stress.md) · `test` · appetite small

The 3.15t job runs the same suite as every other job; there are no targeted stress tests despite
free-threading being a first-class target. Parallel FUSE callbacks against transfers against eviction,
race hunting with the sampling profiler, and the specific unguarded state the audit named: the handle
fields written from callback threads, and the non-atomic check-then-set in logging initialization.
Needs a surface that has stopped moving, which is why it sits here — everything above it moves the
engine.

### Benchmark harness and honest overhead numbers

**Seed:** [`issues/benchmark-harness.md`](issues/benchmark-harness.md) · `feature` · appetite small

Throughput and latency against direct tier access, published in the docs. `PLAN.md` commits to honest
overhead accounting for a Python FUSE implementation, and the passthrough-handle design exists
precisely to make the number defensible. Measures a system believed correct; measuring earlier means
measuring twice.

### Documentation and v0.1.0

**Seed:** [`issues/documentation-and-v0.1.0.md`](issues/documentation-and-v0.1.0.md) · `feature` · appetite big

Sphinx and furo: quickstart, overlay-spec reference, architecture from `PLAN.md`, an operations guide
built around the job-script detach-then-commit pattern, and a CLI reference. Then release readiness —
`CHANGELOG.md` for 0.1.0, `README.md`, verified build artifacts, and the version. Consumes the
benchmark numbers and documents a surface with nothing stubbed in it. Last before the tag. Whether the
docs build joins CI as a fourth job is a question for shaping.

### Extended archive sources

**Seed:** [`issues/extended-archive-sources.md`](issues/extended-archive-sources.md) · `feature` · appetite big

Post-v0.1.0 by design in both `PLAN.md` and the original phase outline. Two capabilities remain behind
one entry: SFTP and Globus inner locations over the existing three-method protocol, and
synthetic-subtree wrapping for a non-conforming archive. Sharded sources and the `TransferPool`
priority lane were split out on 2026-11-04 and sit above, sharding because the owner named it the
flagship use case and the lane because it was never an archive feature. What is left has no user
asking for it yet, and an honest SFTP backing wants the ranged read the sharding cycle has to build
first.

## Terminal records

Deferrals considered and closed **without** shipping — `declined` and `accepted-behaviour`. Listed
apart from the ordered cycles so the index above stays an index of *work*. Read one before re-filing
the thing it describes. Work that shipped leaves no entry here: the code refutes a re-filing on its
own, and `spec/{slug}/` holds the account.

*(none yet)*
