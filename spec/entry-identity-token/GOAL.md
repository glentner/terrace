# GOAL — A call that waits for the source lock never acts on a file that took a purged entry's id

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** entry-identity-token
- **kind:** fix
- **appetite:** small

## Problem

**Three calls can act on a file other than the one they found, and report success.** Each looks its
entry up at its path under the file's path lock, waits for the source lock, then looks the entry up again
under it. The second lookup tells the entry from another only by path, id and kind:

- the handle-less `truncate` (`src/sosfs/engine/overlay.py:781-783` at `c7d2049`);
- the `open` of a copy served off the source (`_lookup_again`, called at `:660`);
- a promotion off the source (`_lookup_again`, called from `_serve` at `:696`). The cut's own promotion
  runs under the lock the cut already holds, so in practice this is the promotion `open` triggers.

Entry ids are reused. `entry.id` is a plain `INTEGER PRIMARY KEY` with no `AUTOINCREMENT`
(`src/sosfs/state/schema.sql:59`). SQLite gives a new row one more than the largest id in the table, so
the id of a purged entry that held the largest id goes to the next entry the catalog adds, whether by a
user `create` or by populating a directory (`tests/engine/test_replay.py:1202`).

The route needs all of these inside one call's wait for the source lock:

1. a directory holding the file is renamed;
2. the file is removed and its entry purged;
3. the catalog adds a file elsewhere, which takes the id;
4. a rename carries that file onto the call's path.

The second lookup then accepts the new file. The cut cuts it and returns success. `open` returns a
handle on its bytes, or promotes them into the stage. No error is raised. A symbolic link or directory
that takes the id is already refused with `ENOENT`, because the kind is decided again under the lock.
Every route needs a rename of an ancestor, because the path lock covers the file's path, not its
ancestors (`issues/freethreaded-concurrency-stress.md`, "Path locks do not cover descendants").

**Evidence**, carried from the seed and not re-run for this GOAL:

- **The cut.** Reproduced by injection in the `source-path-truncate-and-stat` cycle (its `REVIEW.md`,
  `probe_identity_gap.py`, and a staged variant). The stage had `bypass_size=100` and `data/big.bin` held
  1000 bytes. The four steps above ran inside the call's wait, with flushes to purge and settle. The new
  file held 500 bytes. The call returned success and cut the new file to 4 bytes.
- **`open`.** Reproduced by injection in `source-served-open-race`'s planning. The same sequence ran
  inside `open`'s wait, then a flush, and `open` returned the new file's 500 bytes.
- **A promotion off the source.** Argued from the code, not run.

**Pre-existing** (`escaped`: `pre-factory`, Clarifications). The calls acted on whatever file the path
held long before any lookup under the lock existed. `source-path-truncate-and-stat` (`6425a21`) and
`source-served-open-race` (`5b8dcaf`) narrowed that to the reused-id route and recorded the gap. It is
§ Known open violations' row at `AGENTS.md:696`, root `PLAN.md` § Risks (`:595-598`) and `ROADMAP.md:33-42`.

**Why it was deferred, and what has changed.** The seed assumed a fix needs a persistent identity token.
Its examples were a generation column on `entry`, or `AUTOINCREMENT`. Either is a schema change:
`SCHEMA_VERSION` has no migration machinery, so a bump makes every existing state database unopenable
(`AGENTS.md:213`, `:217`, `:245`, `:300-302`; `src/sosfs/state/db.py:34`, `:86`, `:105`). Every route
above runs inside one call's wait, though. The process running it holds the state directory's exclusive
lock for the mount's lifetime (`db.py:157`). So the calls may need to tell entries apart only within one
process, with no persistent token. Q1 asks Geoffrey which family is in scope. The R-IDs below admit
both.

## Outcome / vision

Each of the three calls acts on the file its first lookup found, or raises `ENOENT` having changed
nothing. It never acts on a file that took that file's id after a purge. A call whose file was never
purged behaves as it does today, whatever else is purged or created meanwhile. Every state database
that opens today still opens. The records say exactly what is closed.

The profile's `verify` stays fully green, under `CI=true` and under random seeds, and CI's four jobs
conclude `success`.

## Acceptance criteria (the contract)

These terms hold throughout:

- *The calls* are:
  - a handle-less `truncate(path, length)` of a file;
  - an `open(path)` of a file whose serving copy is the source's (a *source-served open*);
  - an `open(path)` of a file held only on the source that a faster tier admits (an *open that promotes*).
- *The named file* is the entry the call's first lookup at `path` returns (`_ensure`, under the path
  lock).
- *The reuse route* is the four-step sequence under Problem, run while the call waits for the source lock.
  The named file's entry is purged, a file the catalog adds afterwards takes its id, and a rename carries
  that file onto `path`. *The new file* is that file.
- *The queue drains* means `journal.queued() == []` and `journal.pending() == []`.
- *Converges* means `tests/engine/test_replay.py::assert_converged` passes.
- Counts are the numbers pytest prints in its summary line, asserted as numbers (`AGENTS.md`
  § Verification).

The criteria:

- **R1 — the cut.** WHEN the reuse route runs during a handle-less `truncate`'s wait, the call SHALL
  raise `ENOENT`. The new file's bytes SHALL be unchanged, and `getattr` at `path` SHALL report its size
  unchanged. Each of these SHALL satisfy it:
  - the new file served off the source;
  - the new file served off a stage.

  (Today the call returns success and cuts the new file to `length`.)
- **R2 — a source-served open.** WHEN the reuse route runs during a source-served open's wait, the call
  SHALL raise `ENOENT` and return no handle. No handle SHALL stay registered for the new file's entry.
  The new file's bytes SHALL be unchanged. (Today `open` returns a handle on the new file's bytes.)
- **R3 — an open that promotes.** WHEN the reuse route runs before an open that promotes takes the source
  lock for its promotion, the call SHALL raise `ENOENT`. Afterwards:
  - no `promote` intent SHALL be unresolved;
  - no faster tier SHALL hold bytes or a residency for the new file that the call made;
  - the new file's bytes SHALL be unchanged.

  (Today, argued from the code, the promotion copies the new file's bytes into the stage and `open`
  returns a handle on them.)
- **R4 — no false refusals.** IF the named file's entry is not purged during the call, THEN the call
  SHALL NOT raise `ENOENT` because other entries were purged, added or given reused ids during its wait.
  Its outcome SHALL be the one it has when none of that happens. Each of these SHALL satisfy it, for each
  of the three calls:
  - a flush during the wait that purges another entry's tombstone;
  - a `create` of another file during the wait.

  A design that refuses on any purge during the wait fails this criterion (Q4).
- **R5 — existing state databases.** WHEN the engine starts on a state directory written by the code at
  `c7d2049`, it SHALL open it without `SchemaMismatch`. Its entries, residencies, queued rows and intents
  SHALL read as before. Under Q1 (A) this is met by `SCHEMA_VERSION`, `state/schema.sql` and
  `state/db.py` staying unchanged. Under any other answer, a test SHALL open a database that the code at
  `c7d2049` wrote.
- **R6 — the property,** gradeable by reading the code. The lookup each call makes under the source
  lock SHALL accept the entry it finds at `path` only when it can tell that entry is the named file, by a
  fact that a purge and a later reuse of the id cannot reproduce. Path, id and kind alone SHALL NOT be
  that fact. (Design family per Q1.)
- **R7 — the tests.** Every sequence in R1–R4 SHALL be pinned by a deterministic injected-interleaving
  test in `tests/engine/test_replay.py`, under `-m unit`. Each test SHALL converge once the queue drains.
  Each SHALL have a mutant that turns it red: a fix-removal mutant, or, for a sequence the baseline
  already gets right, a mutant of the fix's own decisions (`spec/truncate-recording-after-ancestor-rename/`
  Q6). The existing tests in `tests/engine/test_replay.py` SHALL pass unchanged. Those include the
  refusals of a symbolic link or directory that took the id, and of a file that took the id and stayed
  off the path.
- **R8 — the records**, in the same commit as the code they describe, saying exactly what the code
  delivers and no more. They SHALL cover every record the change makes untrue, including these:
  - `AGENTS.md` § 5's text on how the three calls tell their entry. § 10 too, if a lock's holders or what
    it serializes change. The high-blast-radius tables, if the change adds a holder or touches coupled
    core.
  - § Known open violations SHALL drop its row (`AGENTS.md:696`), or keep only what stays open, naming a
    seed that outlives this one.
  - The docstrings of `truncate`, `open`, `_serve` and `_lookup_again` SHALL agree with that text, as
    SHALL the module docstring of `tests/engine/test_replay.py` (`:44-52`).
  - Root `PLAN.md` § Risks (`:595-598`) SHALL agree. So SHALL `issues/freethreaded-concurrency-stress.md`'s
    reused-id note (`:122-125`), if the fix changes what it says.
  - The measured test counts SHALL match the final tree in § Commands (`AGENTS.md:70`), § Repository map
    (`:111`), § Conventions (`:672`) and § Verification (`:714`).
  - Q5's home SHALL be written in this cycle.
  - Once the roadmap step has retired the seed and its `ROADMAP.md` entry, no file outside `spec/` SHALL
    name `issues/entry-identity-token.md`.
- **R9 — the gate.** All of these SHALL hold:
  - WHEN the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`) runs with
    `CI=true` on macOS, `tools/lint.sh` SHALL exit 0.
  - In that run, pytest SHALL report 0 failed and 0 errors. Its passed count SHALL exceed the count the
    same command reports at `c7d2049`, where § Verification records 530 unit tests.
  - WHEN `uv run pytest tests/engine/test_model.py` runs on macOS without `CI` set, from a tree with no
    `.hypothesis/` directory, once for each `--hypothesis-seed` from 1 to 20, every run SHALL pass.
  - WHEN the CI workflow runs on this cycle's final tree, each of `Lint & build`, `Unit (Linux, 3.15)`,
    `Unit (Linux, 3.15t)` and `Unit (macOS, engine-only)` SHALL conclude `success`, as read from the
    run record.

## Non-goals (no-gos)

- **A persistent identity token, and schema migration machinery.** No call spans a remount, so R1–R4 do
  not need either (Q1, Q3). Home: none needed under Q1 (A). If Geoffrey answers Q1 (B), the migration
  machinery becomes a seed of its own and this cycle waits on it (the size circuit breaker).
- **Entry-id identity outside the three lookups.** That covers handles keyed by entry id
  (`overlay.py:92`, and `catalog.by_id(handle.entry_id)` at `:737`, `:836`, `:843`), intent rows, and
  queue rows, whose identity is by value. Home: `issues/freethreaded-concurrency-stress.md` (Q5).
- **A rename or removal landing before the lookup under the lock, by any route that needs no reused id.**
  That covers `_serve`'s `EngineError` before `fastest_copy`, and a stage-served open handed another
  entry's stage bytes by an ancestor's round trip. Home: `issues/freethreaded-concurrency-stress.md`
  ("Path locks do not cover descendants", "An ancestor's round trip").
- **Making path locks cover ancestors or descendants.** Home: `issues/freethreaded-concurrency-stress.md`.
- **`EngineError`, `LookupError` or `TierError` escaping the VFS API** on paths this cycle does not
  close. Home: `issues/engine-error-mapping.md`.
- **Narrowing how long anything holds the source mutex.** Home: `issues/source-commit-staged-publish.md`.
- **Exercising the route under real threads** (stress, free-threaded). Home:
  `issues/freethreaded-concurrency-stress.md`.
- **A mount-level reproduction and the integration lane.** Coverage is engine-level, under `-m unit`,
  on tmpdir tiers. Home: `issues/ci-integration-lane.md`.

## Constraints

- **`AGENTS.md` § Invariants § 1, § 5 and § 10 hold as written.**
  - That includes the lock order: path locks, then the source mutex, then `_handles_mutex`, then the
    SQLite write lock.
  - Nothing holding the source mutex calls `locks.hold`, `_ensure`, `getattr` or `readdir`.
  - Nothing acquires the mutex inside `db.transaction()`.
  - A design that bends any sentence of these sections goes in the plan's deviation table, before
    Geoffrey, and into the same diff's § Invariants text.
- **The handle-less `truncate` keeps the property Geoffrey signed off on 2026-11-08.** It is item 5 of
  "Geoffrey's rulings on the cycle 3 escalation" in `spec/source-path-truncate-and-stat/REVIEW.md`. The
  refusal of a file that took the id joins that property's refusals of a missing entry, a changed id and
  a non-file. Nothing else in it changes.
- **Earlier cycles' properties hold.** `open` and `_serve` keep cycle 4's
  (`spec/source-served-open-race/GOAL.md` R3, R4). The handle-less `truncate` keeps cycle 5's
  (`spec/truncate-recording-after-ancestor-rename/GOAL.md` R1–R4). Their tests stay green unchanged (R7).
- **No state-database schema change** (Q1, Geoffrey). No `SCHEMA_VERSION` bump, and no edit
  to `src/sosfs/state/schema.sql` or `src/sosfs/state/db.py`. If the plan finds it cannot avoid one, it
  stops and asks.
- **No edit to `tests/engine/test_model.py`.** Hypothesis's fixed CI draw is a digest of that machine
  class's source, so any edit redraws the CI sample.
- **No `src/` or `tests/` text cites an `issues/` seed** (Geoffrey's directive, 2026-11-09).
- **Same-commit records** (R8, `AGENTS.md` § Conventions).
- **Human sign-off at review** on the engine diff, unconditionally (Q6, Geoffrey).

## Clarifications

Resolved:

- **Q:** Kind and appetite? — **A:** `fix`, as the seed gives it. Appetite `small`, not the seed's `big`.
  The seed is `status: unshaped`, so its appetite was never agreed. It priced in a schema change and a
  migration or rebuild. Under Q1 (A) neither is in scope. What is left is one deliverable: the lookup
  under the lock, at two code sites (the cut's inline check and `_lookup_again`), with tests and
  records. That is the size of cycles 4 and 5, which ran `small` with seven and eight criteria. There
  are nine criteria here, and three of them are records, tests and the gate, so the size circuit breaker
  does not trip. **This holds only under Q1 (A).** Under Q1 (B) or (C) the appetite rounds up to `big`,
  and (B) splits first (Non-goals). (recorded 2026-11-09, shaper)
- **Q:** Where did the defect come from (`escaped`)? — **A:** `pre-factory`. Correct it at the gate if
  that reading is wrong. (recorded 2026-11-09, shaper)
  - At `c7d2049` the id checks blame to the two cycles that added them. The cut's lookup under the lock
    (`overlay.py:781-783`) blames to `6425a21` (`source-path-truncate-and-stat`), and `_lookup_again`
    (`:702-714`) to `5b8dcaf` (`source-served-open-race`). Each recorded the gap rather than shipping
    it unknowingly.
  - Past those rewrites, the defect they kept was older. At `6425a21^` the cut opened its copy at the
    logical path with no lookup under any lock: `open_direct(_pure(path), os.O_WRONLY)` blames to
    `4aea975` (2026-07-02). So whatever file a rename put on the path was cut, with or without a reused
    id. `open`'s reads at `5b8dcaf^` blame to `8dcc0d0` (2026-07-12, per cycle 4's GOAL). The reusable id
    (`schema.sql:59`) blames to `cc33dd8` (2026-07-01).
  - All three commits are ancestors of the factory adoption `6a046b4` and carry no `spec/`.
  - Recorded with `metrics.py escaped spec/entry-identity-token --from pre-factory`.
- **Q:** Does the seed's draft contract carry over? — **A:** As input only. It maps as follows:
  - Its R1 becomes R1, with the new file served off a stage added (the staged probe).
  - Its R2 ("every other lookup") is bounded to the three named calls and split into R2 and R3. Other
    id-keyed identities go to Q5.
  - Its R3 splits. "A database written before it SHALL still open" becomes R5, stated so that both
    design families can meet it. "The token SHALL survive a remount" is dropped, pending Q3.
  - Its R4 becomes R7, with convergence and the mutant rule added.
  - R4, R6, R8 and R9 are new: no false refusals, the gradeable property, the records, and the gate.

  (recorded 2026-11-09, shaper)

Ruled at the GOAL gate:

- **Q1 — Is a state-database schema change in scope?** — **A:** (A), no. The calls tell entries apart
  within the process, for example by an in-process record of purges or additions kept under the source
  lock, or by identity by value. The plan picks the mechanism. There is no `SCHEMA_VERSION` bump, and no edit
  to `schema.sql` or `db.py`. Existing state databases open unchanged (R5). Appetite stays `small`.
  (Geoffrey, 2026-11-09, in the foreman's session)
- **Q2 — Is acting on the new file a defect at all?** — **A:** (A), yes. The call acts on the named file
  or raises `ENOENT`. This matches the code's existing refusal of a different id, what `AGENTS.md` § 5
  says, and the earlier rulings that declined to accept a silent cut of another file. (ruled 2026-11-09,
  foreman as gate judge)
- **Q3 — Must the guarantee survive a remount?** — **A:** (A), no. No call spans a remount, so the seed's
  R3 first half is dropped. It would also have forced a persistent token, which Q1 rules out. (ruled
  2026-11-09, foreman as gate judge)
- **Q4 — Are false refusals forbidden?** — **A:** (A), yes, as R4 says. Flushes purge under the source
  lock exactly when calls wait for it, so refusing on any purge would turn ordinary write-back into
  spurious `ENOENT`. (ruled 2026-11-09, foreman as gate judge)
- **Q5 — Where do other entry-id identities go?** — **A:** (A), out. A dated line is written in this cycle
  under the stress seed's reused-id note (R8). It names handles keyed by entry id, and any other
  lookup-by-id after a wait that the plan finds. (ruled 2026-11-09, foreman as gate judge)
- **Q6 — Does Geoffrey sign off at review unconditionally?** — **A:** (A), yes. He signs off on the final
  engine diff (`truncate`, `open`, `_serve`, `_lookup_again`, and any catalog or purge site the plan
  touches) whatever the review finds, as for cycles 2–5. (Geoffrey, 2026-11-09, in the foreman's session)

Ruled at the plan gate (foreman as gate judge, 2026-11-09). The plan had two adversarial passes. The second
attacked the atomic-naming redesign and found no interleaving that breaks it. Its gate and records findings
are folded (PLAN § 4.1).

- **Reading of R1–R3's "during the wait".** The Outcome binds: the reuse route counts whenever it lands
  after the call's first lookup returns, through the lookup under the source lock, not only while the call
  waits for that lock. The tests that pin the window between the first lookup and the lock (T12, T14, and
  the second pass's two `open` analogs) grade R1–R3 on that reading.
- **Reading of R7's "existing tests unchanged".** It keeps the signatures of `_ensure` and `_adopt`. Cycle 4's
  `AfterLookup` replaces `_ensure` with a one-argument callable.
- **Reading of R8's counts.** They include `AGENTS.md`'s engine-test count, next to the suite counts.
- **Q-a** (deviation row 2): **accepted**. A catalog read held under the leaf mutex, with the connection opened
  before the hold, and a thread-local that carries the watch into `_ensure`.
- **Q-b** (row 3): **accepted**. R4's "other entries" doesn't cover the named entry's own rolled-back purge.
  The records say so.
- **Q-c**: **confirmed**. These two stage windows are the Non-goals' "ancestor's round trip", not R2 or R3:
  a route landing before `_serve` reads `fastest_copy`, and one landing after a promotion and before `open`
  opens the stage copy. Both are homed in the stress seed.
- **Q-d** (row 5): **accepted**. `_purge`, `_ensure`, `_named`, `_adopt`, `_watch` and `Catalog.purge` join
  the high-blast-radius tables, as cycle 5's row 6 did.
- **Q-e**: **accepted**. Pins on cites that drifted in seeds this cycle doesn't otherwise touch are R8
  records, in P1's commit.

## Related materials

- Seed: [`issues/entry-identity-token.md`](../../issues/entry-identity-token.md)
- Origin cycles (context, not this contract):
  - `spec/source-path-truncate-and-stat/`: its `PLAN.md:301-306` (the residual gap), its `REVIEW.md:24-28`
    (the plan-gate ruling) and "Geoffrey's rulings on the cycle 3 escalation" (item 5);
  - `spec/source-served-open-race/`: GOAL Q2 and Q4;
  - `spec/truncate-recording-after-ancestor-rename/`, the shape precedent, including Q6 (the mutant rule).
- Related seeds: `issues/freethreaded-concurrency-stress.md`, `issues/engine-error-mapping.md`,
  `issues/audit-command.md`, `issues/rename-to-terrace.md`, `issues/source-commit-staged-publish.md`,
  `issues/ci-integration-lane.md`.
- Contract text: `AGENTS.md` § Invariants § 5 and § 10; the high-blast-radius rows for `state/db.py`
  and `state/schema.sql` (`:213`, `:217`, `:245`) and `SCHEMA_VERSION` (`:300-302`); § Known open
  violations (`:696`).
- Sources, anchors re-confirmed at `c7d2049`:
  - `src/sosfs/engine/overlay.py`:
    - `_handles` 92, and the source lock's comment 94-96;
    - `catalog.purge` calls 534, 1121, 1142, 1267;
    - `open` 626, with its first lookup 649, `_serve` 656 and `_lookup_again` 660;
    - `_serve` 671, with `_lookup_again` 696 and `transfers.copy` 697;
    - `_lookup_again` 702, with its check 712;
    - `truncate` 742, with its first lookup 775 and the lookup under the lock 781-783;
    - `catalog.by_id(handle.entry_id)` 737, 836, 843;
  - `src/sosfs/state/schema.sql`: `entry` 58, `id` 59;
  - `src/sosfs/state/db.py`: `SCHEMA_VERSION` 34, mismatch refusals 86 and 105, `ExclusiveLock` 157;
  - `src/sosfs/state/catalog.py`: `purge` 166;
  - `tests/engine/test_replay.py`:
    - module docstring 44-52, `assert_converged` 194, `Interleaved` 675, `concurrent_removal` 707;
    - the reused-id tests 902, 1184, 1218, 1569 and 1611.
