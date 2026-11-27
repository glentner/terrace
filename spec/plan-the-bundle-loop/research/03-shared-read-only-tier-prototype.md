# Research 03 — Two read-only mounts over one shared durable tier (prototype, R9)

Serves R9 and is the evidence for R5 (g). Statements are **measured** (the run below) unless marked
**argued** or **recommended**.

## What was run

One script, `proto.py`, kept in the plan step's scratch with its logs (`run-final.log`, 299 JSON
lines). It imports the helper and nothing of the project. Helper: `tools/bundle_loop.py` (blob
`e6d18b4`) and `tests/loop/tap.py` (blob `e1c91b3`) as at commit `ae420d1` of branch
`test/local-bundle-loop`. To run it again, from the cycle worktree, with the kept copy of `proto.py`
(see `prototype/README.md` for where it finds the helper):

```
uv run python spec/plan-the-bundle-loop/prototype/shared.py drive --runs 20
```

The driver starts the helper's store (moto), puts the tap in front of it and gives the tap's endpoint
to every mount. A mount is an operating-system process (`proto.py mount`, Python 3.15, macOS, local
filesystem) with a state directory and SQLite database of its own, a lower directory of its own, and
the one shared directory. It reads the marker, takes the object's signature, stages the bundle if no
file has the staged name (GET, gunzip to `<name>.<pid>.<id>.partial`, write the index sidecar the same
way, rename the index, rename the bundle), loads the index, copies every member by offset into its
lower directory and records each copy in its database. The driver compares each lower directory with
`loop.content`, member by member. One bundle, at two sizes: 6 members (the helper's default; object
5.6 kB) and 300 members (object 5.93 MB, tar 6.42 MB).

## Result

Each process read the oracle's bytes: **yes, in all 252 two-mount runs** (504 processes; every member
of the bundle each time). Fetches are GETs of the bundle's key on the tap. Distributions were the
same at both sizes except where shown.

| Scenario | Runs per size | Fetches of the bundle | HEAD | list |
|---|---|---|---|---|
| (a) one after the other; both take the signature by HEAD | 10 | 1 ×10 | 2 | 0 |
| (a) second mount trusts what is staged | 3 | 1 ×3 | 1 | 0 |
| (a) second mount takes the signature from a list entry | 3 | 1 ×3 | 1 | 1 |
| (a) no sidecar; each mount scans the staged tar | 5 | 1 ×5 | 2 | 0 |
| (b) together; temporary name and rename only | 20 | 2 ×20 | 2 | 0 |
| (b) the same, 0.3 s held between fetch and rename | 20 | 2 ×20 | 2 | 0 |
| (b) second starts 0 to about 2 pull-times after the first | 20 | 6 members: 1 ×18, 2 ×2; 300: 1 ×15, 2 ×5 | 2 | 0 |
| (c) together; `O_CREAT\|O_EXCL` lock file, 5 s timeout | 20 | 1 ×20 | 2 | 0 |
| (c) the same, 0.3 s held between fetch and rename | 20 | 1 ×20 | 2 | 0 |
| (c) timeout 0.1 s, shorter than a 0.5 s pull | 5 | 2 ×5 | 2 | 0 |

No run made a write to the source or a GET of any other key. After every run the shared tier held
one staged tar, byte-equal to the gunzip of the object, one index, one marker, and no temporary or
lock file. So without coordination two overlapping pulls are correct and cost one wasted fetch; the
lock removes that fetch and is not needed for correctness, since a waiter that gave up and pulled
anyway still read the oracle's bytes. Time to a staged bundle at 300 members was 39 ms (b) and 36 ms
(c), median. The lock needs an atomic exclusive create; the pull needs an atomic rename over an
existing name. No `flock` or `fcntl` lock is taken on the shared tier (SQLite takes its own in each
mount's private state directory).

## The kill case

300 members, 3 runs per variant. Mount A was sent SIGKILL after its fetch and before its renames;
then B started, then C.

| Variant | A left on the shared tier | B | C | Fetches |
|---|---|---|---|---|
| rename only | marker, 2 `.partial` files | pulled; staged in 33–39 ms, no delay | no fetch; 6–7 ms | 2 |
| lock, 2 s timeout | the same and 1 `.lock` | waited 2.00 s, pulled; staged in 2.04–2.08 s | no fetch; 6–7 ms, lock still there | 2 |

B and C read the oracle's bytes in all 6 runs. Nothing A left has a staged name, so nothing was taken
for a bundle. No mount removed A's files: they were still there at the end. A stranded lock delays
only mounts that arrive before some pull completes, each by one timeout.

## The member index

Medians of 7, local disk, warm cache. The 5000-member tar is synthetic, not the helper's.

| Tar | Members | Scan the `.tar` | Flat sidecar (JSON lines): size, load all | SQLite sidecar: size, open + one lookup, open + load all |
|---|---|---|---|---|
| helper default | 6 | 0.18 ms | 353 B, 0.02 ms | 8 kB, 0.06 ms, 0.06 ms |
| helper | 300 | 6.2 ms | 18.7 kB, 0.27 ms | 20 kB, 0.06 ms, 0.19 ms |
| synthetic, 1 KiB members | 5000 | 50 ms | 319 kB, 4.1 ms | 242 kB, 0.07 ms, 2.6 ms |

Inside a mount, scanning and storing rows in its own database took 0.59 ms (6) and 7.3 ms (300);
loading the shared sidecar took 0.06 ms and 0.32 ms. The sidecar is renamed before the bundle, so a
bundle under its staged name always has its index: in all 80 lock runs the waiter read the holder's
sidecar with no lock. A killed puller can leave an index without a bundle, which is harmless, since
the next puller writes the same bytes. The SQLite form was read with `mode=ro&immutable=1`, which
made no file beside it. **Argued:** without `immutable` a reader takes `fcntl` locks on the shared
filesystem, and a WAL-mode file would need a `-shm` written there; the flat file needs neither.

## Naming and the stale case

Staged name: `<stem>.<first 16 of ETag>-<size>.tar`. The signature cost each mount one HEAD per bundle,
or one list request for the whole source (a list entry carries ETag and size), or nothing when a
mount trusts the newest staged name, which then cannot see a change at the source. The pull compares
the GET's ETag with the name's.

Stale run (300 members, once): A staged the old object and read 150 members; the object was replaced
at the source (by the driver, off the tap); B made 1 HEAD and 1 GET, staged under another name and
read the new oracle's bytes (300 of 300); A then read 149 members from its open descriptor and 1 by
reopening the name its database records: 300 of 300 were the old oracle's bytes. A later mount that
trusts made no request and read the new bundle; one that asks made 1 HEAD and no GET. Tap total:
2 GET, 3 HEAD, 0 list, 0 write. Both staged tars and both indexes remained.

## The marker

Text used: `kind = "shared-staged"`, `format = 1`, `source = "s3://<bucket>/<key>"`. Two mounts
arriving together at an empty directory, 20 runs each:

| Marker text | Written by | Both mounts read the marker that remained |
|---|---|---|
| a function of the source alone | temp + rename | yes ×20 (both wrote; bytes identical) |
| also carries writer id and time | temp + rename | no ×20 (one had read a marker that was replaced) |
| also carries writer id and time | temp + exclusive hard link | yes ×20 (one writer) |

A mount given today's overlay marker (UUID, tier, generation) refused it as another kind; a mount
given a marker naming another source refused it. Neither made a request or wrote to the shared tier.

## What each mount wrote

To the shared tier: the marker, staged tars, index sidecars, `.partial` names and lock files; no file
of any other kind in any run. Outside its own state directory, its own lower directory and the
shared tier: nothing (0 of all recorded writes). No mount removed anything on the shared tier but its
own lock. Neither was given the other's paths.

## What the result says is needed (recommended, for R5 g)

**The marker**
- A kind meaning "staged bundles, shared, read-only to every mount", a format number, and the
  identity of the source as the spec gives it.
- No overlay UUID, tier name, generation, time or writer: its text is a function of the source, so
  the first-writer race needs no lock.
- A mount refuses a corrupt marker, another kind, or another source.
- Turned down: a marker listing the overlays that share the tier (a read-modify-write on a shared
  file, and a killed mount never signs out).

**The member index**
- One flat file beside each staged bundle, named for it, written by the puller and renamed first.
- Each mount's database holds one row per bundle it reads (key, source, signature, staged name) and
  the copies it made in its own tiers; no member rows.
- Turned down: each mount scanning into its own `archive_index` (about 20 times the time at 300 members,
  once per mount); a SQLite sidecar (faster for one lookup, but safe only when opened immutable).

**The pull**
- The staged name carries the object's signature; a changed object is staged beside the old one.
- Fetch to a name unique to the process, rename into place; the rename is the only commit.
- The lock file is optional, lapses at a stated timeout, and is removed only by its maker.
- No mount evicts from the tier, counts its limits against it, or alters a staged file. Temporary
  files, stranded locks and superseded bundles go only by an explicit reclaim command.
- Turned down: one fixed staged name replaced on change (offsets move under a reader); `flock` or
  `fcntl` locks.

## Not tested

No network filesystem: atomic rename, atomic exclusive create and reading a replaced open file are
assumed there, not measured. No FUSE. Two mounts only: more than two, and several waiters timing out
on one stranded lock together, were not run. One bundle, at most 6 MB, fetched whole into memory. A
store's ETag after a multipart upload, and a source with no ETag (size and time over SFTP), were not
run. The reclaim command was not built. The SQLite sidecar was timed in one process, not used by the
mounts. The seed's draft R3 ("no later mount makes a request to the source for that bundle") and
draft R7 (staleness is recognized) meet only if the signature costs no per-bundle request: the
choice between one list per mount, a HEAD per bundle, and trusting what is staged is left open.
