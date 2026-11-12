# GOAL — A write racing a commit is never recorded clean

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** racing-write-marked-clean
- **kind:** fix
- **appetite:** small

## Problem

**A write that lands while a commit is in flight can be lost with no error.** A commit copies a dirty
copy one hop upstream and then decides whether the copy it read from, the *holder*, is clean. A write
through an open handle can land after the copy has read its bytes and still leave the holder recorded
`clean`. The stage then holds the new bytes, its upstream holds the old ones, the residency reads
clean and the queue is empty. Eviction drops clean copies (`AGENTS.md` § 7), so once the handle closes
the next quota crossing deletes the only copy of the write. Until then nothing queues it. No call
raises.

A review of the whole tree found it at `64faf27` on 2026-11-11, and `AGENTS.md` § *Known open
violations* lists it. It is pre-existing: the lines blame to the commits that first built the data
path and the write-back scheduler, months before the first cycle (Q7).

The review reproduced two routes by injection at `64faf27`, on one thread, against a two-tier POSIX
stack on tmpdir tiers with no mount (Python 3.15.2, macOS). Shaping did not run them again. It read
the anchors again at `bf438b4`, and they say what is described here.

**Route 1: the write lands after the commit has last looked at the holder.** The holder tier's `stat`
is wrapped so that, on the commit's stat after the copy, a `pwrite` through the open handle follows
the real stat. The `pwrite` stands for a writer that has entered `write`, found its handle already
dirty, and been preempted before its bytes land.

```
create /f.dat ; open ; write 'AAAA' ; commit_upstream (injected pwrite 'BBBB' after the post-copy stat)
release ; flush
  residency = {project: clean, scratch: clean}   queued = 0
  stage bytes = 'BBBB'   source bytes = 'AAAA'
evict_copy -> True ; read back = 'AAAA'
```

The window is one stat call wide and needs a preempted writer. It is narrow and it is not zero, on
either interpreter build.

**Route 2: the write changes neither the size nor the timestamp.** `TransferPool.copy` is wrapped so
that a real `engine.write` of the same length follows the copy, and the holder's mtime is then set
back to its value before the copy.

```
write 'AAAA' ; commit_upstream (injected engine.write 'BBBB' after the copy, mtime restored)
  residency = {project: clean, scratch: clean}   queued = 0
  stage bytes = 'BBBB'   source bytes = 'AAAA'
```

How often a real filesystem gives two writes one timestamp is unverified, and this cycle does not
measure it (Non-goals). On the development machine 0 of 2,000 back-to-back same-size writes left
`st_mtime_ns` unchanged. The engine compares `TierStat.mtime`, a float
(`src/terrace/tier/protocol.py:36`), which is coarser than that. A filesystem that stamps from a
coarse clock gives every write inside one tick the same timestamp, and a small file copies in less
than a tick. An in-place rewrite during a commit is the workload the project's `GOAL.md` names ("heavy
writes in the middle of a file", `:34`).

**Mechanism**, for orientation only. No criterion depends on it, and the plan step confirms it. After
the copy, `_commit_hop` resets `marked_dirty` on every open handle on the holder, stats the holder,
and records it clean when size and mtime equal what the copy read
(`src/terrace/engine/overlay.py:992-1007`). `write` reads `handle.marked_dirty` and then calls
`os.pwrite` (`:793-796`). The committer holds the path lock. A writer whose handle is already marked
takes no lock.

**Other ways a holder's bytes change**, read at `bf438b4` and not run:

- `truncate` through a handle has the same shape as `write`: it reads the flag and then calls
  `os.ftruncate` with no lock (`overlay.py:834-838`). `open` with `O_TRUNC` reaches it after `open`
  has released the path lock (`:700-701`).
- `truncate` with no handle holds the path lock across its decision, its cut and its recording
  (`:840-866`). Each commit route holds the same lock around its hops (`:955`, `:1020`, `:1057`), so
  at one path the two run one after the other.
- Nothing else was found that changes a holder's bytes without the path lock. The FUSE adapter
  reaches file bytes only through `engine.write` and `engine.truncate`
  (`src/terrace/fuse/adapter.py:202-212`), and the three calls above (`overlay.py:795`, `:837`,
  `:858`) are the only `os.pwrite` and `os.ftruncate` calls under `src/`. The seed says an `mmap`
  write reaches the engine as an ordinary `write` callback; shaping did not check that.

## Outcome / vision

A copy is recorded clean only when its upstream holds its bytes, whatever lands during the commit and
whatever the tier's filesystem reports about sizes and timestamps. The rule can be stated without
naming a filesystem. A write or a truncate that meets a commit is either carried by it or left queued
for the next one, and its bytes reach the source by the ordinary route. A commit that meets no such
call behaves as it does today, and a write on an already-dirty handle costs what it costs today.
`AGENTS.md` stops listing the violation, and § 6 states the rule the code keeps.

## Acceptance criteria (the contract)

These terms hold throughout:

- *A commit* is one call of `commit_row` (the scheduler's unit of work) or `commit_upstream` (`fsync`
  under `fsync_policy = "commit"`, `flush`, and the eviction manager). *A hop* is one copy inside it,
  from a *holder* tier to that tier's *upstream*. `commit_to_shared` (rescue) makes the same hops, and
  its contract already requires a quiescent engine, as a drain's does.
- *The interval* of a hop runs from the moment its copy starts reading the holder's bytes to the
  moment the hop's outcome is recorded.
- *A change* is a call that alters the holder's bytes: a `write` through an open handle on the holder,
  a `truncate` through such a handle (an `open` with `O_TRUNC` included), or a `truncate` by path with
  no handle.
- The holder's residency is *truthful* when, once the commit and the change have both returned, it
  reads `clean` only if the upstream's copy holds the same bytes as the holder's. Otherwise it reads
  `dirty`, and a `data` row from the holder to its upstream is queued.
- A change *reaches the source* when, after every handle is released and one `flush()` returns, the
  source's bytes equal the holder's, no `data` row for the file is queued, and a read through the
  engine after `evict_copy` has dropped the staged copy returns those bytes.
- *The base commit* is the commit this branch is rebased onto before the plan step (Q3).
- Counts are the numbers pytest prints in its summary line, asserted as numbers (`AGENTS.md`
  § *Verification*).

The criteria:

- **R1 — a racing write.** WHEN a `write` through an open handle lands in a hop's interval, the engine
  SHALL leave the holder's residency truthful, and the write SHALL reach the source. This SHALL hold
  for `commit_row` and for `commit_upstream`, for any offset and length, and whatever size and
  timestamps the holder's filesystem reports afterwards. (Today both routes in Problem end with the
  holder `clean`, an empty queue, the stage holding `BBBB` and the source holding `AAAA`.)
- **R2 — a racing truncate through a handle.** WHEN a `truncate` through an open handle lands in a
  hop's interval, to a shorter or a longer length, the engine SHALL leave the holder's residency
  truthful, and the new length and bytes SHALL reach the source. (Today, by reading: the call has the
  shape of Route 1. The plan step establishes whether the base commit fails it.)
- **R3 — a truncate with no handle.** WHEN `truncate(path, length)` with no handle is called from
  another thread while a commit of `path` is in flight, the engine SHALL leave the holder's residency
  truthful once both have returned, and the new length SHALL reach the source. (Today, by reading: the
  path lock runs the two one after the other, so the base commit already meets this. The fix SHALL
  keep it.)
- **R4 — size and timestamps are not evidence.** The engine SHALL NOT record a holder clean on the
  evidence of its size and timestamps alone. WHEN a `write` of the same length as the bytes it
  replaces lands in a hop's interval after the copy has read that range, and the holder tier
  afterwards reports the size and every timestamp it reported before the copy, the holder's residency
  SHALL be truthful and the write SHALL reach the source. No test of R1 to R4 SHALL depend on the
  timestamp granularity of the filesystem it runs on. (Today: Route 2.)
- **R5 — a commit that meets no change.** WHEN no change lands in a hop's interval, the hop SHALL copy
  the holder's bytes once, record the holder `clean` and settle its `data` row, all within that one
  commit call, as today. This SHALL hold while handles are open on the holder, written or not, and for
  a write that returned before the copy started. Two tests in `tests/engine/test_writeback.py` pin
  what the base commit already gets right, and both SHALL still hold:
  - `test_mid_copy_write_rearms_instead_of_settling` (`:154`) SHALL pass unchanged. It lands its bytes
    by writing the tier's file directly, with a new size, so a change the base commit detects from the
    holder's stat SHALL still leave the holder `dirty`, re-clock its boundary, and be carried by the
    next commit.
  - `test_open_handle_requeues_after_background_commit` (`:184`) SHALL keep every assertion it makes
    on the residency, the queue and the source's bytes: a write through a handle that stayed open
    across a commit makes the holder `dirty` and queues it again. Its one assertion on a handle's
    `marked_dirty` field (`:192`) may be restated if the fix changes that field.
- **R6 — the reproductions are tests.** Both reproductions in Problem SHALL be tests under `-m unit`.
  Each SHALL place its racing call by injection or by explicit synchronization, never by sleeping or
  by chance. Each SHALL fail on the base commit and pass on the cycle's final tree, and the cycle's
  record under `spec/racing-write-marked-clean/` SHALL show both results. Each SHALL assert that the
  write reaches the source, and not only what the residency and queue rows read. How a test holds its
  writer inside the interval is the plan's to design: Route 1's `pwrite` models a writer the fixed
  engine may shape differently, and the test follows the engine's own `write`.
- **R7 — a write on a dirty handle costs no SQL.** WHILE a handle's copy is recorded `dirty` and no
  commit of it has started since that handle last wrote, a `write` through the handle SHALL execute no
  SQL statement, as today. A test SHALL count the statements the state database executes across such
  writes and assert zero.
- **R8 — the records**, in the same commit as the behaviour they describe, saying what the code
  delivers and no more:
  - the row for this defect in `AGENTS.md` § *Known open violations* (`:752`) SHALL be removed;
  - every sentence in § *Invariants* that the fix makes false SHALL be corrected. § 6's bullet "After a
    copy, `changed` detection re-arms rather than settles" (`:505-509`) is one: it gives a matching
    stat as the condition for going clean. § 10 is another if a lock's holders or its order change;
  - the docstrings of `_commit_hop` (`overlay.py:967-972` says a racing write "shows in the holder's
    stat"), `write`, `truncate` and any other the change makes untrue;
  - the measured test counts `AGENTS.md` carries (§ *Verification*, `:763`, and wherever else the
    unit count appears).
- **R9 — the gate.** WHEN the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`)
  runs on the cycle's final tree, `tools/lint.sh` SHALL exit 0, pytest SHALL report 0 failed and 0
  errors, and its passed count SHALL exceed the count the same command reports on the base commit.
  WHEN CI runs on the cycle's pull request, every job SHALL conclude `success`.

## Non-goals (no-gos)

- **Writing the copy's lifecycle down as an explicit state machine in code**, or using the reserved
  `'active'` write-back state (`src/terrace/state/schema.sql:112`). Both belong to
  `issues/namespace-sync-redesign.md`.
- **The free-threaded stress suite.** It belongs to `issues/freethreaded-concurrency-stress.md`. This
  cycle's tests are the deterministic ones R6 describes.
- **Measuring timestamp granularity** on any other machine or filesystem. The fix must not depend on
  the answer (R4).
- **Any change to eviction policy.** Eviction keeps trusting the residency `state` column; this cycle
  makes the column truthful.
- **A call that acts on the file under another path while a commit is in flight.** The path lock does
  not cover an ancestor, so after an ancestor's rename a `truncate` with no handle at the file's new
  path does not wait for a commit that holds the old one, and a promotion can copy over a stage copy
  written meanwhile and record it clean (the `truncate` and `_serve` docstrings, `overlay.py:815-817`
  and `:715-717`). `issues/freethreaded-concurrency-stress.md` (`:79`, `:147`) records these, and
  `issues/namespace-sync-redesign.md` (its R4) proposes the repair. R3 is about one path.
- **A performance budget.** R5 and R7 keep two costs where they are today. This cycle sets no timing
  or statement-count ceiling for a commit.

## Clarifications

- **Q1:** The seed's draft R4 names a mechanism (a write count read under a lock). Does the contract
  keep it? — **A:** No. This is a `kind: fix`, and each criterion states the observable
  broken-to-fixed behaviour. The draft becomes R4 here: a copy is not recorded clean on the evidence
  of size and timestamps alone, shown by the racing same-size write whose timestamp is restored. How
  the engine knows is the plan's to design (the foreman's ruling, 2026-11-11).
- **Q2:** Which routes are covered? — **A:** Every route by which a holder's bytes change during a
  commit's interval: a write through an open handle, and a truncate with or without a handle. Any
  other route the confirmation reads show changing a holder's bytes outside the path lock is in scope
  too (the foreman's ruling, 2026-11-11). The reads showed none, and Problem lists what was read. The
  neighbouring case, a call that acts under another path after an ancestor's rename, holds a path lock
  of its own; it has an owner already and is in Non-goals.
- **Q3:** The seed's draft says the reproductions fail on `64faf27`. Which commit do the tests have to
  fail on? — **A:** The cycle's base commit, the one this branch is rebased onto before planning, and
  not `64faf27` (the foreman's ruling, 2026-11-11).
- **Q4:** Does the seed's draft R5 hold, and does it gain a companion on the commit side? — **A:** It
  holds, as R7: a write on an already-dirty handle costs no SQL statement. A companion is added only
  if it is observable, and no performance budget is invented (the foreman's ruling, 2026-11-11). The
  observable companion is R5's clause that a hop meeting no change copies the holder's bytes once.
- **Q5:** What happens to the records of the defect? — **A:** The row in `AGENTS.md` § *Known open
  violations* is removed in this cycle, and any § *Invariants* sentence the fix makes false is
  corrected in the same commit, under the repository's same-commit rule (the foreman's ruling,
  2026-11-11). That rule also puts an overturned invariant in the plan's deviation table.
- **Q6:** What is out of scope? — **A:** The explicit state machine and the reserved `'active'` state,
  the free-threaded stress suite, measuring timestamp granularity elsewhere, and any change to
  eviction policy (the foreman's ruling, 2026-11-11). Non-goals carries them.
- **Q7:** Which cycle shipped the defect? — **A:** None. `git blame` on `origin/main` gives the
  write-side lines (`overlay.py:793-796`, `:834-838`) to `4aea975` (2026-07-02) and the commit-side
  lines (`:992-1007`) to `8d2265f` (2026-07-03). Neither commit carries a `spec/` directory, and the
  first cycle landed on 2026-11-05. The cycle's metrics record it as `pre-factory` (the foreman's
  ruling on the method, 2026-11-11). The seed names `4aea975` alone.

## Related materials

- Seed: [`issues/racing-write-marked-clean.md`](../../issues/racing-write-marked-clean.md), with both
  reproductions as run at `64faf27`. Its `ROADMAP.md` entry is "A write racing a commit can be
  recorded clean".
- **Names and anchors.** Another cycle renames the project before this one is planned, and this branch
  is rebased onto it. Every path, command and identifier here uses the new names. The anchors were
  read at `bf438b4`, where the package path is `src/sosfs/`; the line numbers are from that commit.
- Source: `src/terrace/engine/overlay.py` — `write` (`:786-797`), `_mark_handle_dirty` (`:799-806`),
  `truncate` (`:808-866`), `release` and `_record` (`:881-923`), `commit_row` (`:939-960`),
  `_commit_hop` (`:966-1011`), `commit_upstream` (`:1013-1036`), `commit_to_shared` (`:1038-1070`),
  `evict_copy` (`:1430-1465`). `src/terrace/engine/transfer.py:64-120` (`TransferPool.copy` stats the
  holder before it reads, and that stat is what the commit compares against).
  `src/terrace/engine/locks.py:18-27` (path locks are re-entrant per thread, so a `truncate` with no
  handle injected on the committing thread enters a lock another thread would wait for).
  `src/terrace/engine/evict.py:151-152` (the eviction manager commits, then drops).
- Tests: `tests/engine/test_writeback.py:154` and `:184` (R5).
- `AGENTS.md`: § 6 (`:505-509`), § 7 (`:521-530`), § 10 (`:611-613`, `_commit_hop` runs under its
  caller's lock), § *Known open violations* (`:752`), § *Conventions* → same-commit rule
  (`:700-709`). The functions this cycle edits are listed in § *High-blast-radius paths* (`:221`,
  `:246`).
- Related seeds: `issues/freethreaded-concurrency-stress.md` (the handle fields; its 2026-11-11 note
  hands this defect here), `issues/namespace-sync-redesign.md`,
  `issues/pump-liveness-and-loss-windows.md`.
