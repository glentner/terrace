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
  1. **Direct-update** (ZIP, uncompressed TAR): the archive is the tier. ZIP commits append
     new entries that supersede old ones (last-entry-wins on read) with periodic compaction;
     uncompressed TAR appends similarly. Deletions are catalog tombstones until compaction.
  2. **Staged re-sync** (tar.gz and any compressed/remote archive): a full archive file is
     periodically re-created in a staging location and pushed to its final location (e.g.,
     `/scratch/some.tar.gz` → `s3://…/some.tar.gz`), rate-limited to at most N refreshes per
     period. Member reads decompress sequentially up to the member (indexed once; acceptable
     for a reference implementation — noted under Future work).

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
  atomic via write-to-temp-name + fsync + rename (temp-key upload for S3). A crash at any
  point leaves either a resolvable intent row or an orphan temp the audit can discard.

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
sosfs umount --detach "$MOUNT"    # rescue node-local dirty data to scratch, stop cleanly

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
| S3 SDK | **obstore** | Rust-backed; API maps 1:1 onto the protocol; strong async/throughput story. Risk: free-threaded (cp315t) wheels — verify in Phase 7, fall back to source builds in CI. Rejected: boto3 (pure Python, but clunky/slow). |
| Staging granularity | **Whole-file + size bypass, range-ready protocol** | Simple and predictable for v1; `bypass_size` handles files larger than a tier; range ops in the protocol keep chunk-level staging a non-breaking future phase. |
| State store | **stdlib `sqlite3`** (WAL, plain SQL schema) | Zero deps, transparent, free-threading-friendly. Rejected: SQLAlchemy (house style, but heavyweight for a small embedded schema). |
| Archives | **stdlib `zipfile`/`tarfile`** | ZIP append-supersede + compaction; uncompressed TAR append; tar.gz always staged re-sync. Optimization path (indexed gzip random access) is future work. |
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
* **obstore free-threaded wheels** — verify at Phase 7 start; fall back to source builds,
  or (worst case) a boto3 adapter behind the same protocol.
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
8. **Archive tiers** — ZIP/TAR direct-update, tar.gz staged re-sync with rate limiting,
   archive indexing.
9. **Control + operations** — control socket, status/flush commands, `sosfs audit`
   (auto-at-mount, standalone), `sosfs commit` (offline finalize, `--direct`, `--forfeit`),
   observability polish.
10. **Hardening & release** — crash-recovery and concurrency stress tests, free-threaded CI,
    benchmark harness, Sphinx docs, packaging, v0.1.0.


Future work (explicitly deferred)
---------------------------------

* Chunk-level partial staging (protocol already range-ready).
* File pinning (`sosfs pin <path> --tier <name>`), per-path policy overrides.
* Indexed random access into compressed archives (ratarmount-style gzip indexes).
* pyfuse3 (or kernel passthrough) adapter for performance-critical deployments.
* Prometheus metrics endpoint; structured event log.
* Multi-client coordination (lease/lock service) — a research project of its own.
