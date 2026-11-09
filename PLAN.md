---
status: draft
last_updated: 2026-07-01
supersedes: GOAL.md (which remains as the original statement of intent)
---

Plan — Simple Overlay Service (`sosfs`)
=======================================

A clean, reference implementation of a managed data-tiering service for research computing,
delivered as a stand-alone Python program. A FUSE mount presents a single logical namespace;
behind it, an ordered stack of storage tiers (POSIX, S3, TAR/ZIP archives) is managed
automatically: files are promoted toward fast tiers on access, written back toward the
authoritative source on configurable policies, and evicted least-recently-used when per-tier
quotas are reached.

The design goal is *legibility as a reference implementation*: the correct abstractions,
modern Python (3.15+, free-threaded), and no framework semantics leaking into the core model.


Vision and scope
----------------

In HPC environments researchers hand-tier data across `/project` (large, slow, authoritative),
`/scratch` (fast, purged), `/tmp` (node-local), and `/dev/shm` (RAM), plus S3 and archive
formats at the cold end. `sosfs` replaces that manual choreography: the user mounts a
configured tier stack and points their application at the mountpoint; all staging, write-back,
and eviction is policy-driven.

**In scope (v1)**

* FUSE mount presenting one logical namespace over an ordered tier stack.
* Tier types: POSIX directory, S3 prefix/object, TAR and ZIP archives (archives layered over
  either a POSIX path or an S3 object).
* Per-tier quotas (bytes and file count) with LRU eviction between high/low watermarks.
* Write-back policies per tier boundary: quiescence delay, maximum interval, pass-through
  (write-through) mode, and rate-limited archive re-sync (at most N refreshes per period).
* Whole-file staging granularity with a size-bypass rule; the tier protocol carries
  offset/length range operations from day one so chunk-level staging can be added later
  without breaking the contract.
* Persistent overlay state (catalog, residency, LRU clocks, dirty/write-back queue) in SQLite,
  maintained so metadata is always at least as current as the data it describes.
* Graceful shutdown (SIGINT / `sosfs umount`) that drains all changes to the source; orderly
  detach (SIGTERM / `umount --detach`) that stops immediately with metadata intact, rescuing
  node-local dirty data to the nearest shared tier; audit-based recovery from crashes
  (SIGKILL, OOM, node loss) — automatic at mount, standalone via `sosfs audit`.
* Deferred finalization: a standalone `sosfs commit` drains queued dirty state to the
  authoritative source offline, from any host that sees the shared tiers — so expensive
  compute allocations never wait on upstream data movement.
* CLI: `sosfs mount|umount|status|flush|fsck|config` (cmdkit `ApplicationGroup`).

**Explicit non-goals (v1)**

* Multi-client coherence. Named tiers are **managed locations** owned exclusively by their
  overlay and marked as such on disk; concurrent external modification is an audit-repairable
  violation, not a supported mode of use.
* Chunk/block-level partial staging (protocol-ready, not implemented).
* Special files (devices, sockets), hard links across tiers, byte-range lock pass-through
  (advisory locks are honored locally within the mount only).
* Windows. Linux is the deployment target; macOS (macFUSE) is supported for development.


Terminology
-----------

* **Tier stack** — ordered list of tiers. `tiers[0]` is the **source** (upstream terminus,
  authoritative, typically unquoted). The last tier is the fastest (e.g., `/dev/shm/<space>`).
* **Upstream / downstream** — toward the source / toward the fast end.
* **Promotion** — copying a file downstream so reads/writes hit faster media.
* **Commit (write-back)** — pushing new data or namespace changes one hop upstream.
* **Eviction** — dropping a staged copy from a quota'd tier. Only clean copies (or copies
  whose dirty state has first been committed upstream) are evictable.
* **Residency** — the set of tiers currently holding a copy of a file.
* **Catalog** — the SQLite-backed namespace and state database, authoritative while mounted.
* **Managed location** — a named tier root owned exclusively by the overlay and marked as such
  on disk; the state database is authoritative for its contents.
* **Drain** — committing all dirty state hop-by-hop to the source with policy clocks collapsed;
  the graceful-shutdown path.
* **Audit** — reconciling the state database against the managed locations after an unclean
  shutdown; automatic at mount, or standalone via `sosfs audit` with no mount running.
* **Detach** — orderly immediate stop without a full drain: in-flight work resolved or rolled
  back, metadata consistent, dirty state left queued for a later `sosfs commit`.
* **Rescue** — the minimal move at detach: dirty data on node-local (`scope = "node"`) tiers
  hops to the nearest shared durable tier so it survives losing the node.


Architecture
------------

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

### Tier protocol

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
  2. **Staged re-sync** (tar.gz and any compressed/remote archive): on first access the whole
     archive is pulled to the durable tier just below the source and decompressed once to an
     uncompressed `.tar` there; every member is indexed into SQLite (data offsets in
     `archive_index`, full metadata in the catalog) so `find`/`stat`/`readdir` are served from
     the state store alone, and a member read seeks to its data offset in that `.tar` (no
     repeated decompression). Write-back reconstructs the full archive into a staging file on
     the same durable tier, recompresses it, and atomically pushes it to the source (a single
     atomic PUT for S3 — a key is never partially visible; temp+rename for POSIX), rate-limited
     by `max_frequency` and forced at drain/commit. The pull/decompress/reconstruct is O(whole
     archive); sharded sources (below) bound that cost to the touched shard.

  **Sharded sources** (`prefix/*.tar.gz`, planned post-v0.1.0) split a dataset into independent
  per-subtree archives so a write reconstructs only its shard. Each shard maps to one top-level
  directory whose name matches the shard's stem — strictly enforced: the mount is rejected if a
  shard lacks that single top-level directory, and for a sharded source the mount root holds
  only shard directories (root-level files are refused). The shard set is dynamic (glob at
  mount; a top-level create/delete adds/removes a shard, new shards in the source's default
  format). Shards are pulled and indexed lazily, one at a time, only when their subtree is first
  entered, and the index persists across remounts — so an unread shard among thousands costs
  nothing but the initial listing. (A lone, non-sharded archive presents its contents at the
  mount root and carries no single-top-level-dir requirement.)

### Overlay engine

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
60-second quiescence clock toward `/scratch`, and so on. Policies per boundary:

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

**Deferred-op replay.** Namespace ops are entry-keyed and deferred: the catalog moves the
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
changes nothing, and a removal landing during the act keeps its own queued op. Reading the source
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
deliberately *not* adopted on the flush path — it remains the audit's job (Phase 10), which
reuses the committed source path to move rather than re-copy a relocated clean source object.

**Eviction.** Per-tier quota on bytes and file count, with high/low watermarks (default
95%/85%) to avoid thrash. When the high watermark is crossed, the eviction manager removes
least-recently-used *clean* copies until below the low watermark; dirty copies are committed
upstream first (eviction can force early write-back). A copy that is the most-upstream holder
of new data is never dropped. Files exceeding a tier's `bypass_size` are never staged into it.

**Concurrency.** libfuse runs a multithreaded callback loop; the engine uses per-file locks
(an in-memory lock registry) plus background components in the hypershell idiom — FSM-driven
`Thread` subclasses: `WritebackScheduler`, `EvictionManager`, and a `TransferPool` executor
for copies. On free-threaded 3.15 builds, FUSE callbacks and transfers run genuinely in
parallel; on GIL builds the workload is I/O-bound and degrades gracefully.

### State store

Stdlib `sqlite3`, WAL mode (`synchronous=NORMAL`), plain SQL schema (`schema.sql`), per-thread
connections. Tables (sketch): `entry` (namespace + metadata), `residency` (entry × tier, atime
for LRU, clean/dirty, recorded size/mtime and optional blake2b checksum), `writeback` (queued
commits and namespace ops, timestamps for delay/max-interval math), `intent` (in-flight
mutations of managed locations), `tier_usage` (running byte/file counters), `archive_index`
(member offsets for TAR/ZIP), and a single `overlay` row (UUID, generation, `shutdown_state`,
mounting host).

Two write-ordering invariants make the database authoritative after any process death:

* **Metadata before acknowledgment.** A clean→dirty transition commits to SQLite before the
  triggering FUSE write is acknowledged; subsequent writes to an already-dirty file incur no
  metadata cost. Residency changes likewise commit before anything depends on them. WAL makes
  these transactions survive SIGKILL; `synchronous=NORMAL` suffices because the harsher cases
  (kernel panic, power loss) also evaporate the volatile tiers, which the audit handles anyway.
* **Intent journaling.** Every mutation of a managed location — promotion, commit, eviction,
  compaction, archive re-sync — is journaled as *intent → act → confirm*, with the act made
  atomic via write-to-temp-name + fsync + rename (a single atomic PUT for S3, which is atomic
  per key). A crash at any point leaves either a resolvable intent row or an orphan temp the
  audit can discard.

Managed locations are marked: each tier root carries a `.sosfs/marker` file recording the
overlay UUID, tier name, and a generation counter. A missing or mismatched marker tells the
audit the tier evaporated wholesale (node reboot wiping `/dev/shm`) rather than files going
missing one by one. The state directory itself must live on durable storage — never inside a
volatile tier; `sosfs mount` refuses a spec that puts it in one.

The namespace is populated lazily: directory listings hit the catalog first and fall through
to the source tier on first touch (archives index once — ZIP central directory, TAR single
scan — cached in `archive_index`). No eager full scan at mount.

### FUSE adapter

mfusepy's high-level path-based API. Notable mappings: `statfs` reports the source tier's
capacity (or configured quota for unbounded sources like S3) so `df` is meaningful;
per-file tiering state is exposed read-only via xattrs (`user.sosfs.tiers`,
`user.sosfs.dirty`) for observability and testing.

### Control channel

A Unix domain socket in the overlay's state directory, serving a small line-oriented JSON
protocol. `sosfs status` and `sosfs flush [path]` talk to the live mount through it; `status`
can also read the SQLite state directly when no mount is running.


Lifecycle and recovery
----------------------

Named tiers are managed locations: the overlay owns their contents, and the state database is
the authority on what is where. The lifecycle design follows from that principle.

### Shutdown

| Trigger | Behavior |
|---|---|
| `sosfs umount`, SIGINT | **Drain**: policy clocks collapse (all dirty state becomes immediately committable), re-sync rate limits are waived, and the write-back pipeline pushes everything to the source in loss-risk order. Unmount, set `shutdown_state = clean`. Zero loss, source finalized. |
| `sosfs umount --detach`, SIGTERM | **Detach**: stop now, finalize later. Dirty data on `scope = "node"` tiers is first *rescued* one hop to the nearest shared durable tier — `--detach` completes the rescue; SIGTERM bounds it by `drain_timeout` (schedulers send SIGKILL shortly after; `0` disables). In-flight transfers resolve or roll back, the WAL is checkpointed, `shutdown_state = detached`. Metadata is consistent — no audit needed; finalize later with `sosfs commit`. |
| Second SIGINT mid-drain | Downgrade the drain to a detach. |
| SIGKILL / OOM / node loss | Nothing runs. `shutdown_state` still reads `mounted` — the crash flag, exactly a filesystem dirty bit. The audit runs automatically before the next mount or commit. |

Both the drain and the rescue move data in **loss-risk order**: dirty data on the most volatile
tiers first, then upstream hop by hop — so whatever time budget exists is spent where data
would otherwise be unrecoverable.

After a drain the source is final; after a detach every newest version lives in a shared
durable tier awaiting `sosfs commit`. Only a crash leaves real exposure, and it is neither
silent nor unbounded: the loss window at each boundary is capped by its write-back policy
(`delay` / `max_interval` / `passthrough`) plus whatever rescue SIGTERM completed, making the
policy knobs an explicit trade between upstream write amplification and exposure at the fast end.

### Deferred commit (`sosfs commit`)

The motivating pattern: a GPU job stages and writes against fast tiers, computation finishes,
and the write-back policies haven't pushed recent outputs upstream. A full drain would finalize
the source but burn expensive allocation time on data movement — worse when the source involves
an archive re-pack or S3 upload. Instead:

```bash
# last line (or trap) of the GPU job script — seconds to minutes:
sosfs umount --detach spec.toml   # rescue node-local dirty data to scratch, stop cleanly

# later, from a single-CPU job or a login node — off the GPU bill:
sosfs commit spec.toml            # finalize scratch → project / archive / S3
```

`sosfs commit` is the offline finalizer: with no mount running (exclusive lock on the state
database), it drains the queued write-back state all the way to the source, from any host that
can see the shared tiers and the state directory.

* After a detach it proceeds directly — metadata is trusted, no audit walk. After a crash it
  runs the audit first, automatically.
* **Efficient by construction**: policy clocks and rate limits don't apply; transfers run in
  parallel; a checksum-recorded file whose content already matches upstream is skipped; and
  archive sources are finalized with one batched re-pack and one re-sync/upload at the end,
  not per-file operations.
* `--direct` collapses hops: the newest version of each file copies straight to the source and
  stale intermediate copies are invalidated. The default cascades hop-by-hop, leaving shared
  intermediate tiers warm and consistent for the next mount.

Cross-host caveats: the state directory must be on shared storage for commit to run elsewhere
(it is already required to be durable). The `overlay` row records the mounting host, so a
`scope = "node"` tier seen from another machine reads as *unreachable*, not evaporated — data
stranded there (a crash without rescue) is reported by hostname, and rolling it back to the
last committed version requires an explicit `--forfeit`, since an audit or mount back on that
host may still recover it.

### Audit

`sosfs audit <spec.toml>` reconciles the state database against the managed locations. It runs
standalone with no mount (taking an exclusive lock on the state database), and `sosfs mount`
runs the same code automatically whenever `shutdown_state != clean` (`--no-audit` skips,
`--audit=full` deepens). The walk goes from the fastest tier upstream — freshest data first,
since the fast end determines what still needs committing:

1. **Marker check.** Verify each tier root's `.sosfs` marker (overlay UUID, generation). A
   missing marker invalidates every residency row for that tier at once and attributes losses
   to tier evaporation rather than per-file mystery.
2. **Reconcile.** Compare tier contents against residency rows. Recorded-and-present copies
   are kept — clean copies must match recorded size/mtime (checksum under `--full`); dirty
   copies are trusted as-is, since their bytes *are* the newest version. Recorded-but-missing
   copies are dropped; if such a copy was the most-upstream holder of dirty data, the file
   rolls back to its last committed upstream version and lands in the loss report.
   Present-but-unrecorded files are either recognizable transfer temps (discarded) or true
   orphans (quarantined and reported — a managed-location violation).
3. **Resolve intents.** In-flight transfer/eviction/compaction intents roll back or complete
   idempotently; interrupted archive re-syncs restart from the extracted staging copy.
4. **Rebuild.** Recompute `tier_usage` counters; requeue surviving dirty state and pending
   namespace operations.
5. **Report.** Per-tier summary (verified / recovered / lost / orphaned) plus an explicit loss
   report naming each file rolled back and the age of the restored version. Exit codes: 0
   consistent, 1 repaired, 2 losses or attention required.

Modes: `--fast` (default: markers, existence, size/mtime) and `--full` (verify clean copies
against blake2b checksums, when `checksum = true` recorded them at commit time). The audit
repairs as it goes; `--dry-run` reports only. The audit never moves data upstream —
finalizing the source is `sosfs commit`'s job, and commit invokes the audit itself after a
crash.

The audit is idempotent and is the same code standalone and at mount: after a crash at *any*
point, audit + remount must converge to a consistent state with an accurate loss report. The
fault-injection suite (Testing) asserts exactly that.


Configuration
-------------

Two TOML surfaces, both cmdkit-backed:

1. **Application config** (logging, defaults) with hypershell-style precedence:
   CLI > environment (`SOSFS_*`) > `.sosfs.toml` > `~/.config/sosfs/config.toml` >
   `/etc/sosfs/config.toml` > defaults.
2. **Overlay spec** — the tier stack definition, passed to `sosfs mount`. Order in the file
   is stack order, source first:

```toml
[overlay]
name  = "myspace"
state = "~/.local/state/sosfs/myspace"   # durable storage; shared storage enables cross-host commit
drain_timeout = "5m"                     # SIGTERM rescue budget (SIGINT always drains fully; 0 disables)
checksum = false                         # record blake2b at commit; enables `sosfs audit --full`

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
staging  = "/scratch/space.tar.gz"
sync     = { max_frequency = "4/day" }
```

```toml
[[tier]]                                  # sharded source: one archive per top-level subtree (post-v0.1.0)
name    = "imagenet"
type    = "tar.gz"
role    = "source"
sharded = true
location = { type = "s3", url = "s3://bucket/imagenet/*.tar.gz" }
sync     = { max_frequency = "4/day" }
```


CLI surface
-----------

`sosfs` (cmdkit `ApplicationGroup`, hypershell pattern):

| Command | Purpose |
|---|---|
| `sosfs mount <spec.toml> <mountpoint>` | Mount the overlay (`-f` foreground, `-d` debug; daemonizes by default; auto-audits after an unclean shutdown) |
| `sosfs umount <mountpoint>` | Drain to the source, then unmount; `--detach` stops immediately after rescuing node-local dirty data (finalize later with `sosfs commit`); `--force` skips even the rescue |
| `sosfs status [<spec|mountpoint>]` | Per-tier usage vs. quota, dirty counts, transfer activity |
| `sosfs flush [<path>]` | Force upstream commit now, against a live mount (whole overlay or subtree) |
| `sosfs commit <spec.toml>` | Finalize the source offline: drain queued write-back state up the stack from any host seeing the shared tiers (`--direct` collapses hops); auto-audits after a crash |
| `sosfs audit <spec.toml>` | Reconcile state DB against managed locations, no mount required; reports and repairs, never moves data upstream (see Lifecycle and recovery) |
| `sosfs config get|set|which|edit` | Application config management (hypershell pattern) |


Technology decisions
--------------------

Decided 2026-07-01 with rationale; alternatives noted.

| Decision | Choice | Rationale / alternatives |
|---|---|---|
| Python baseline | **3.15** (beta now; final 2026-10-01), free-threaded build a first-class target | Research brief calls for modern innovations: lazy imports (PEP 810), `frozendict` (PEP 814), sampling profiler (PEP 799), stable free-threaded ABI (PEP 803). CI on both builds. Rejected: 3.14 floor. |
| FUSE binding | **mfusepy**, threaded | Maintained fusepy fork (ratarmount's binding); high-level path API; libfuse 2/3 + macFUSE (dev on macOS); pure ctypes → runs unmodified free-threaded. Rejected: pyfuse3 (async/Trio, Linux-only, forces async through the engine); fusepy (unmaintained). |
| Tier abstraction | **Purpose-built `Tier` protocol** | Small owned surface; no third-party caching semantics fighting our tiering. Rejected: fsspec (~56-method API, tar read-only anyway); hybrid. |
| S3 SDK | **obstore** | Rust-backed; API maps 1:1 onto the protocol; strong async/throughput story. Free-threaded (cp315t) wheels absent (verified Phase 7); CI compiles obstore from source on 3.15t (see Risks). Rejected: boto3 (pure Python, but clunky/slow). |
| Staging granularity | **Whole-file + size bypass, range-ready protocol** | Simple and predictable for v1; `bypass_size` handles files larger than a tier; range ops in the protocol keep chunk-level staging a non-breaking future phase. |
| State store | **stdlib `sqlite3`** (WAL, plain SQL schema) | Zero deps, transparent, free-threading-friendly. Rejected: SQLAlchemy (house style, but heavyweight for a small embedded schema). |
| Archives | **stdlib `zipfile`/`tarfile`** | ZIP/uncompressed-TAR direct-update via atomic in-place re-pack (temp+fsync+rename; compaction implicit, no tombstones). tar.gz/remote: staged re-sync — decompress once to a durable `.tar`, offset-read, reconstruct+recompress+atomic-push, rate-limited. Sharded sources (`*.tar.gz`) and SFTP/Globus inner backings are post-v0.1.0 (Phase 12). Optimization path (true append-supersede, indexed gzip random access) is future work. |
| S3 testing | **moto-server** (unit, pure Python, real S3 HTTP → works with obstore) + **MinIO** container behind `@mark.integration` | Matches the unit/integration marker split. |
| CLI / config / logging / style | cmdkit `ApplicationGroup`; layered TOML + `SOSFS_*`; hypershell `Logger` w/ `TRACE`; `src/` layout, SPDX Apache-2.0 headers, 120-char lines, `from __future__ import annotations`, FSM + `Thread` idioms | House style per hypershell. hatchling + uv; Sphinx/furo docs; Keep-a-Changelog + SemVer. |
| Package / entry point | **`sosfs`** | Per GOAL.md; repo name is a placeholder and unimportant. |


Testing strategy
----------------

* **Engine-first**: the bulk of the suite drives the VFS-operations API directly against
  tmpdir POSIX tiers — no mount, fast, portable (this is the payoff of the layered design).
* **Model-based**: hypothesis stateful tests run random operation sequences against the
  overlay engine and a plain-directory oracle, asserting namespace/content equivalence after
  quiesce+flush. This is the highest-leverage test for an overlay filesystem.
* **Policy tests**: virtual clock injected into the write-back scheduler and eviction manager
  so grace periods, max-interval, watermark, and rate-limit behavior are tested deterministically.
* **FUSE integration** (`@mark.integration`, Linux CI): real mounts exercising POSIX tools
  against the mountpoint; xattr-based assertions on residency/dirtiness.
* **S3**: moto-server in unit tests; MinIO container in integration tests.
* **Crash recovery**: fault-injection kill points (SIGKILL between intent, act, and confirm of
  every mutation type; mid-drain; mid-rescue; mid-re-sync) followed by audit + remount,
  asserting convergence and loss-report accuracy; tier-evaporation tests (wipe a staging tier
  wholesale, assert the audit attributes it to the marker and rolls back correctly).
* **Detach/commit**: detach leaves an audit-free state with node-scope dirty data rescued;
  `sosfs commit` from a simulated second host (node-scope tier paths absent) finalizes the
  source; `--direct` and the default cascade converge to byte-identical sources; drain and
  rescue honor loss-risk ordering under a constrained time budget.
* **CI matrix**: Linux {3.15, 3.15t}; macOS engine-only (no kext in CI). Free-threaded jobs
  run the same suite plus targeted concurrency stress tests.


Risks and mitigations
---------------------

* **Python FUSE throughput** — this is a reference implementation, not a kernel bypass;
  mitigate with passthrough file handles for resident-copy I/O, free-threading, and a
  benchmark harness (Phase 10) to keep honest numbers in the docs.
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
  dirty-flag updates; measured in the Phase 10 stress suite.
* **tar.gz member reads are sequential-decompress** — acceptable for v1 (staged re-sync mode
  keeps hot data extracted anyway); indexed random access noted as future work.
* **Volatile tiers evaporate** (node reboot, `/tmp` purge policies) — recently written data can
  be lost with them; exposure is bounded by each boundary's write-back policy and reported
  precisely by the audit. Tighten `delay`/`max_interval` (or use `passthrough`) where the loss
  window matters more than write amplification.
* **Write-back namespace replay under path mutation** *(open in part: the data-commit orphan, the
  replay family, stale scheduler rows, the live stat and the handle-less `truncate`'s recording are closed;
  a removal racing a replayed `mkdir`, and the thread-level test stay open)* —
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
  (Phase 10), reusing the committed source path.


Phase outline
-------------

Expanded with discrete checklist steps in ROADMAP.md; summarized here:

1. **Scaffolding** — pyproject (hatchling, uv, 3.15 pin), src layout, core config/logging/
   platform modules, CLI skeleton, CI matrix, repo hygiene (AGENTS.md, skills, wip branch).
2. **State store** — SQLite schema, DB layer, catalog with lazy population; managed-location
   markers, `shutdown_state` flag, metadata-before-ack and intent-journal primitives.
3. **Tier protocol + POSIX tier** — protocol types, PosixTier, quota accounting.
4. **Overlay engine core** — namespace ops, read/write paths, promotion, multi-tier POSIX
   stacks; engine test suite + hypothesis model tests.
5. **Policies** — write-back scheduler (delay, max-interval, passthrough), LRU eviction with
   watermarks, intent-journaled transfers, drain and rescue with loss-risk ordering,
   virtual-clock policy tests.
6. **FUSE adapter** — mfusepy Operations, mount/umount commands (`--detach`), signal handling
   (SIGINT drain, SIGTERM detach with rescue budget), statfs, xattrs, Linux integration tests.
7. **S3 tier** — obstore adapter, moto-server unit tests, MinIO integration tests.
8. **Write-back namespace-replay hardening** — correct deferred rename/remove replay under
   path mutation (source-path tracking or flush-time reconciliation); drop the model-test
   guard added in Phase 7.
9. **Archive tiers** — ZIP/TAR direct-update (atomic re-pack), tar.gz staged re-sync
   (decompress-once + offset reads + reconstruct/recompress/push, rate-limited), S3 inner-
   location backing, drain/commit batching, archive indexing.
10. **Control + operations** — control socket, status/flush commands, `sosfs audit`
    (auto-at-mount, standalone), `sosfs commit` (offline finalize, `--direct`, `--forfeit`),
    observability polish.
11. **Hardening & release** — crash-recovery and concurrency stress tests, free-threaded CI,
    benchmark harness, Sphinx docs, packaging, v0.1.0.
12. **Extended archive sources** (post-v0.1.0) — SFTP and Globus inner-location backings
    (get/put; Globus submit-wait shim on the top source), sharded archive sources
    (`prefix/*.tar.gz`, per-shard lazy pull/index and reconstruction), TransferPool priority
    lane (on-demand pulls preempt background warming).


Future work (explicitly deferred)
---------------------------------

* Chunk-level partial staging (protocol already range-ready).
* File pinning (`sosfs pin <path> --tier <name>`), per-path policy overrides.
* Indexed random access into compressed archives (ratarmount-style gzip indexes).
* Wrapping a non-conforming archive/shard (no single top-level directory matching the stem)
  into a synthetic subtree instead of rejecting the mount (Phase 12 refinement).
* pyfuse3 (or kernel passthrough) adapter for performance-critical deployments.
* Prometheus metrics endpoint; structured event log.
* Multi-client coordination (lease/lock service) — a research project of its own.
