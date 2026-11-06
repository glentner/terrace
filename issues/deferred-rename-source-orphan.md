---
status: unshaped
kind: fix
appetite: big
lane: public
---

# A commit before a queued rename orphans the source object

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

Namespace ops are deferred. `rename` moves the catalog entry the moment the call returns and queues
a `rename` row; the source follows when that row's write-back clock fires. `entry.source_path`
exists to cover the gap — `PLAN.md` → Write-back → Deferred-op replay defines it as the path at
which the source physically holds the bytes, and makes it the resolution target for *every* physical
touch of the source tier.

The data path does not use it. `_commit_hop` copies to the upstream tier at the entry's **logical**
path (`src/sosfs/engine/overlay.py:784`, through `transfer.copy` at
`src/sosfs/engine/transfer.py:93`), and when that upstream is the source it then writes `source_path
= path` (`src/sosfs/engine/overlay.py:808`) without moving or deleting the object still sitting at
the old `source_path`. The queued `rename` row runs later at `src/sosfs/engine/overlay.py:928` — by
then both sides are equal, `source.rename` is a no-op, and the stale object survives with nothing in
the catalog or the journal referring to it.

Reproduced against tmpdir tiers, no mount, using `Clock`/`build_spec`/`seed` from
`tests/engine/conftest.py`:

```python
seed(root / 'project', 'x.dat', b'original')
e = OverlayEngine(build_spec(root, tiers=2), clock=Clock()); e.start()
h = e.open('/x.dat', os.O_RDWR); e.write(h, b'NEWDATA!', 0); e.release(h)
e.rename('/x.dat', '/y.dat')
e.commit_upstream('/y.dat')          # what fsync_policy="commit" and eviction pressure both do
```

```
after commit_upstream: ['.sosfs', 'x.dat', 'y.dat']
after flush:           ['.sosfs', 'x.dat', 'y.dat']
queued: []  pending: []
```

The journal is empty and both queues report drained: a drain that claims success while the source
tree no longer equals the overlay view. Stopping there and starting a second engine over the same
tier roots gives `readdir('/') == ['/x.dat', '/y.dat']`, with `/x.dat` holding the pre-rename bytes
`b'original'` — adoption re-admits the orphan as live user data. That is resurrection of
renamed-away data, and for the `unlink`-shaped variant of the same window it is resurrection of
deleted data.

Three production paths reach it without a test ever asking for it. `fsync_policy = "commit"` calls
`commit_upstream` from `fsync` (`src/sosfs/engine/overlay.py:707`). The eviction manager commits a
dirty copy to the source before dropping it (`src/sosfs/engine/evict.py:151`), on quota pressure,
whenever the rename is not yet due. And the write-back scheduler hits it on any poll where the data
row's deadline precedes the rename row's — which is the *normal* ordering when the write preceded
the rename, since `mark_dirty` stamps `last_write` at the write and the rename row is stamped later.

The suite is green for a specific and important reason: **the two paths that would catch it both run
namespace ops first.** `flush` sorts namespace before data (`src/sosfs/engine/overlay.py:876-878`),
so does `drain` (`src/sosfs/engine/drain.py:94-100`), and `WritebackScheduler.due` applies the same
sort *within one poll* (`src/sosfs/engine/writeback.py:88`). The failing interleaving needs two
polls, or a single-file commit. The hypothesis model's only convergence checkpoint is
`quiesce_and_flush` (`tests/engine/test_model.py:186-191`), and it has no rule that commits one
entry and no rule that advances a scheduler poll — so the model cannot reach the state it exists to
catch. A fix that lands without extending that reach ships with the same blind spot.

Two smaller defects share the root cause and should be weighed as part of the same repair.

**`truncate` without a handle resolves the logical path.** `open` resolves the lag correctly through
`serve_path` (`src/sosfs/engine/overlay.py:612`) and `_serve` passes `src_path` for promotion
(`overlay.py:633`), but `truncate` opens `_pure(path)` directly (`overlay.py:685`). For a file
served off the source — anything over every stage's `bypass_size` — with a rename queued, this
raises `FileNotFoundError` on a file that exists. Reproduced: `truncate('/big2.bin', 4)` on a
1000-byte source-served file under a pending rename raises `[Errno 2] No such file or directory:
.../project/big2.bin`. POSIX callers read ENOENT as "the file vanished".

**The size in the catalog goes permanently wrong.** `_record` (`overlay.py:732`) and the live-stat
branch of `getattr` (`overlay.py:277`) also stat `_pure(entry.path)`. Reproduced: on a source-served
file with a queued rename, `truncate(handle=…)` to 0 followed by `write(b'tiny')` leaves
`getattr().size == 1000` for a 4-byte file, while the physical object is 4 bytes; `residency.size`
is 1000 too, and it stays 1000 after `release`, after `flush`, and across the rename actually
committing. It never self-heals, because `_ensure` returns the cached entry — only a fresh state
database corrects it. `ls -l`, `mmap` and every archiver reads the lie for the life of the overlay,
and the residency figure feeds the quota counters eviction trusts absolutely.

## Reproduced by CI on Linux and macOS, and on the development Mac (2026-11-05)

`tests/engine/test_model.py::TestOverlayModel::runTest` fails in every unit job of both CI runs read
under the factory:

- 42023846602 at `6a046b4` (`main`): `Unit (Linux, 3.15)` and `Unit (Linux, 3.15t)` failed on it
  alone (448 passed, 1 failed in each). `Unit (macOS, engine-only)` failed on it too (`1 failed,
  404 passed, 3 errors`), with failure 2 below; the 3 errors were `tests/fuse` collection errors
  (`Unable to find libfuse`), so the job's red did not point at this test alone.
- 42059324110 at `0ab60a6` (`fix/toolchain-repair`): all three unit jobs failed on it alone, with
  both failures below (Linux `1 failed, 448 passed`; macOS `1 failed, 404 passed, 44 skipped`).

On the development Mac it fails on every run under `CI=true`, Hypothesis's CI profile with its fixed
seed: the profile's `verify` shows both failures below, and `test_model.py` run alone shows failure
2. With random seeds and no example database it failed 2 of 12 runs of `test_model.py` alone, and
one full-suite run found five distinct failures. A local run that passes is luck: set `CI=true` to
reproduce.

Hypothesis shrank two distinct failures from the same prefix:

```
state.mkdir(path='/d1')
state.mkdir(path='/d2')
state.rename(new='/d1/s1', old='/d2')
state.rename(new='/d2', old='/d1/s1')
state.rmdir(path='/d1')
state.teardown()                     # flush(), then source == snapshot(oracle)
```

1. With a later `create_with_content('/b.dat')` and `rename('/b.dat' → '/d1')`, `flush()` raises
   `PermissionError: [Errno 13]` from `PosixTier.rename` (`os.replace` of `project/d2` →
   `project/d1/s1`) inside `_execute_namespace` (`src/sosfs/engine/overlay.py:928`).
2. Without them, `flush()` completes and the source keeps a `d1` directory the oracle removed.

Neither is specific to Linux. Both are the replay running each queued op at its historical path
after later ops changed what lives there, the class this seed is about, so they are in scope for
this cycle. Until this cycle lands, the landed toolchain repair (`spec/toolchain-repair/`) tolerates
this one test as the only failure:
in a CI unit job (R9) and in the profile's `verify` on a Mac (R2, R8). Its GOAL records the rulings,
and this cycle must leave `verify` fully green. The `@reproduce_failure` blobs are in both runs' job
logs.

## Why it was deferred

Found by the invariant audit run during the software-factory port, which was a harness change and
not the place to rewrite `_commit_hop`. This is a **live defect in the shipped tree** (reproduced at
`6419966`), not unbuilt work — every probe above runs today.

`AGENTS.md` § *Invariants* § *Known open violations* names it in the § 5 row, and § 1/§ 5 both
list `_commit_hop`, `commit_upstream`, `commit_to_shared` and `_execute_namespace` as
high-blast-radius: the sole clean-transition point and the only code that destroys bytes on purpose.
A wrong edit there is silent data loss rather than a crash, so it earns a human sign-off gate and a
cycle of its own.

It is also the divergence class `PLAN.md` § Risks declares closed — "Write-back namespace replay
under path mutation *(resolved, Phase 8)*", `PLAN.md:538-546`. Under the plan-drift rule that claim
is part of what this cycle repairs.

## Outcome / vision

Every physical touch of the source tier resolves through `entry.source_path`, on the data path as
well as the namespace path, so a commit that races a queued rename leaves the source holding exactly
one object per entry. A drain that reports an empty queue means the source tree equals the overlay
view. A remount adopts nothing it did not already know about. The stateful model reaches per-file
commits and scheduler polls, so the interleaving that produced this is covered rather than merely
fixed, and `PLAN.md`'s risk register tells the truth about which divergence classes are closed.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. Prefer EARS phrasing (see
the KB's EARS notes).

- **R1** — When the engine commits an entry's data to the source while a `rename` row for that entry
  is still queued, the source tree shall afterwards hold exactly one object for that entry and
  nothing at the pre-rename path: the source root lists `['.sosfs', 'y.dat']`, not `['.sosfs',
  'x.dat', 'y.dat']`.
- **R2** — When the write-back scheduler polls a queue in which the data row's deadline precedes the
  rename row's, the source tree after both rows settle shall equal the tree produced by running the
  same queue through `flush()`, which orders namespace ops first.
- **R3** — While the journal reports `queued() == []` and `pending() == []`, a fresh `OverlayEngine`
  started against the same tier roots shall list exactly the entries the previous engine's catalog
  held, adopting no path the catalog abandoned.
- **R4** — The stateful model shall reach a single-entry commit and a single scheduler poll as ops
  in its mix, and catalog↔source convergence shall hold after the queue drains from either of those
  states — not only at the `flush()` checkpoint.
- **R5** — When `truncate` is called with no open handle on a file served from the source whose
  entry carries a queued rename, the engine shall truncate the source object to the requested length
  and shall not raise `FileNotFoundError`.
- **R6** — After a handle-based `truncate` and a write on a source-served file carrying a queued
  rename, `getattr` shall report the byte length the serving tier actually holds — 4, not 1000 —
  both while the handle is open and after `release`, and the residency size shall match.
- **R7** — `PLAN.md` § Risks shall not record the namespace-replay divergence class as *resolved,
  Phase 8* while any code path resolves a source-tier touch through the entry's logical path; the
  entry is reconciled in the same commit as the fix.

## Notes

- Related: [`shutdown-rescue-gaps.md`](shutdown-rescue-gaps.md) — the other reproduced
  silent-data-loss defect, queued immediately after this one. [`audit-command.md`](audit-command.md)
  — catalog↔source reconciliation is deliberately the audit's job (`PLAN.md:213-215`), and the audit
  is the only component that could ever find an orphan this cycle fails to prevent; it does not
  exist yet, which is why prevention is the whole defence today.
  [`ci-integration-lane.md`](ci-integration-lane.md) — the regression coverage this cycle adds is
  engine-level and runs under `-m unit`, but the mount-level version of the same sequence is
  integration-only. [`plan-md-reconciliation.md`](plan-md-reconciliation.md) holds only the residue;
  the § Risks entry above belongs in this cycle's diff, per the same-commit rule.
- `PLAN.md` → Write-back → Deferred-op replay (lines 194-215) and § Risks (lines 538-546).
  `AGENTS.md` § *Invariants* § 5 and its § 5 row under *Known open violations*.
- Open question — **where the commit writes.** Either write the destination at `source_path` and
  leave the queued rename as the only thing that moves a source object, or write at the logical path
  and remove the stale object inside the same intent bracket, collapsing the rename into the data
  commit. The first keeps one mover and one invariant; the second saves a source-side move but
  leaves a queued `rename` row with nothing to do, and the journal needs to say whether that row is
  settled or executed. On S3 a rename is copy-then-delete, so the choice is also an egress decision.
- Open question — **directory renames.** `catalog.advance_source_path`
  (`src/sosfs/state/catalog.py:186-191`) rewrites the prefix for every descendant, because one
  source rename relocates a whole subtree. A per-file data commit that advances only its own
  `source_path` inside a renamed subtree leaves its siblings pointing at the old prefix. The fix has
  to be checked against the subtree case, not only the single-file one, and `_populate_tree`'s eager
  walk (`src/sosfs/engine/overlay.py:406-407`) is what currently keeps directory listings honest
  through it.
- Open question — **`truncate`'s contract.** Resolve through `serve_path` the way `open` does, or
  refuse a source-served truncation while a rename is queued and force the namespace op first, the
  way `_vacate` jumps its policy clock (`overlay.py:972-997`). The second is simpler and makes an
  unrelated `truncate` do unbounded namespace work.
- Open question — **already-wrong catalog sizes.** The bad size never self-heals, so existing state
  databases stay wrong after the code is fixed. Whether the repair is a one-time correction this
  cycle performs or work handed to [`audit-command.md`](audit-command.md) is a scope decision, and
  it interacts with the residency sizes the quota counters are built from.
- Open question — **whether the two siblings ride along.** They share the root cause and the same
  regions, which argues for one diff; they also widen a high-blast-radius change that already needs
  human sign-off.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — engine-core
  invariants brief, divergences A, B and C.
