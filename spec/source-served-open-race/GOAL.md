# GOAL — `open` finds a source-served file while a replay moves it

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** source-served-open-race
- **kind:** fix
- **appetite:** small

## Problem

Namespace ops are deferred. `rename` moves the catalog entry when the call returns and queues a row
that moves the source object later. Until that row commits, the source holds the bytes at
`entry.source_path`, and `AGENTS.md` § Invariants § 5 makes that the place every physical touch of the
source resolves to. A file is *source-served* when its fastest resident copy is the source's: it is
over every stage's `bypass_size`, or no stage takes it (a one-tier stack, or a `create` straight onto
the source).

**`open` fails on such a file, which exists, when a replay lands mid-call.** `open` looks the entry up
under the file's path lock only (`src/sosfs/engine/overlay.py:628-629`, `_ensure`). It then reads
`entry.source_path` from that lookup in two places:

- to open the copy the source serves (`overlay.py:640-641`, `serve_path`, then `open_direct`);
- through `_serve` (`overlay.py:636`), to promote off the source when a faster tier admits the file
  (`overlay.py:661-663`, `src_path`, then `transfers.copy`).

Replay takes no path lock. The scheduler's `commit_row`, `flush`, `drain`, and the `_vacate` that
another path's `create`, `mkdir` or `rename` reaches all move source objects under the source lock
alone (§ 10). One that commits the file's queued rename, or an ancestor's, between the lookup and the
tier call moves the object away from the place the call then uses.

Evidence, at `0a88f30`, from the `source-path-truncate-and-stat` plan's research (its brief 01 § 6). The
stack was tmpdir tiers, a stage with `bypass_size=100`, and a 1000-byte `data/f.bin`, with
`rename /data → /moved` queued and a `flush()` injected from a second thread:

- Injected between `_serve` and `open_direct`, `open` raises `FileNotFoundError` on the pre-rename path,
  `project/data/f.bin`, for a file that exists at `project/moved/f.bin`.
- Injected before `_serve`, on a file a faster tier admits, the promotion's `transfer.copy` raises
  `TierError: No file at /data/f.bin`.

Both fail loudly. That probe found no case where `open` acted on another entry's object. These
probes were not re-run for this GOAL.

*Correction (2026-11-08, plan gate):* the baseline is not only loud. The planning research found that a
queued swap of names through two renames, replayed after the lookup (R4's sequence), makes `open` at
`5e0d997` return a handle on the other file's bytes, or promote those bytes into this file's stage copy
recorded clean. It is silent. The R4 tests pin both on the baseline (`PLAN.md` § 4, research brief 01 § 6),
and `AGENTS.md:669` ("loud, never a wrong object") is wrong on the same count. R4 already requires that
neither happens, so the contract stands; the severity is higher than this section first said.

**Pre-existing.** Both reads blame to `8dcc0d0` (2026-07-12), an ancestor of the factory adoption at
`6a046b4`, and they sit unchanged at `141aa86`, where no source lock existed. `deferred-rename-source-orphan`
(landed `fe26962`) introduced the source lock and put replay under it, without touching these reads.
`source-path-truncate-and-stat` (landed `6425a21`) closed the same window for the handle-less `truncate`.
That call now takes the source lock and decides the entry, the serving copy (through `_serve`) and the
held place under it, once (§ 5).

**A residual gap that this seed carries.** The handle-less `truncate` tells its entry from another
only by path, id and kind. Ids are reused, so a file can take a purged entry's id and be carried
onto the path during the cut's wait for the lock. That file is then cut and the call returns
success. It is silent and was reproduced by injection. § Known open violations names this seed as its
owner (`AGENTS.md:667`). Closing it needs an identity token, which is a schema change. The plan-gate
ruling of `source-path-truncate-and-stat` (foreman, 2026-11-06) left it recorded here. This seed is
retired once this cycle lands, so the gap needs an owner that outlives the seed (R6, Q2).

## Outcome / vision

`open` on a source-served file returns a handle on that file's bytes under every interleaving of a
replay. That holds whether the source serves the file or the open promotes it to a faster tier. No
call reports `ENOENT` or `TierError` for a file that exists, and none hands back another entry's bytes.
After the queue drains, the tree converges. The records say exactly what is closed. The identity gap
leaves this seed with an owner of its own.

The profile's `verify` stays fully green, under `CI=true` and under random seeds, and CI's four jobs
conclude `success`.

## Acceptance criteria (the contract)

These terms hold throughout:

- *Source-served* is as defined under Problem.
- *A replay* is any act that moves a source object under the source lock alone (§ 10): `commit_row`,
  `flush`, `drain`, or a `_vacate` reached from another path's `create`, `mkdir` or `rename`.
- *The lookup* is `open`'s `_ensure`.
- *The queue drains* means `journal.queued() == []` and `journal.pending() == []`.
- *Converges* means `tests/engine/test_replay.py::assert_converged` passes.
- Counts are the numbers pytest prints in its summary line, asserted as numbers (`AGENTS.md`
  § Verification).

- **R1** — WHEN a replay commits the queued rename of a source-served file, or of one of its ancestors,
  after the lookup and before `open` opens the copy the source serves, `open` SHALL return a handle,
  and reads through it SHALL return the file's bytes. Each of these SHALL satisfy it:
  - a file over a stage's `bypass_size`, with its own rename queued;
  - the same file, with an ancestor's rename queued;
  - on a one-tier stack, a file under a directory whose rename is queued.

  (Today the call raises `FileNotFoundError` naming the pre-rename path.)
- **R2** — WHEN such a replay lands after the lookup and before a promotion reads the file off the
  source, `open` SHALL return a handle served from the faster tier, and reads through it SHALL return
  the file's bytes. The file is held only on the source, and a faster tier admits it. Its own rename
  and an ancestor's SHALL each satisfy this. Afterwards:
  - no `promote` intent SHALL be left unresolved;
  - the faster tier SHALL hold a residency for the copy.

  (Today the promotion raises `TierError: No file at …`.)
- **R3** — The property, gradeable by reading the code (design family per Q1):
  - **Under the lock.** For a source-served open, and for a promotion off the source, the place on the
    source that the tier call uses SHALL be read from the catalog for the entry's id while the call holds
    the source lock.
  - **Before release.** The source object at that place SHALL be opened before the call releases the
    lock. This is the direct open itself, or the promotion's read of the source object.
  - **No value carried in.** No value read before the lock was taken SHALL decide which source place is
    opened.
  - **`truncate` unchanged.** The handle-less `truncate` already decides its promotion under the lock,
    and it SHALL keep doing so.
- **R4** — IF a replay moves another entry's object onto the place the file was held, after the lookup
  and before `open` opens or promotes the file (as a queued swap of names through two renames does),
  THEN `open` SHALL NOT return a handle on that object's bytes, and SHALL NOT copy them into the
  file's residency. It SHALL serve the file's own bytes, or raise `ENOENT`.
- **R5** — Every sequence in R1, R2 and R4 SHALL be pinned by a deterministic injected-interleaving test
  in `tests/engine/test_replay.py`, under `-m unit`. Each test SHALL converge once the queue drains,
  and each SHALL have a fix-removal mutant that turns it red. `tests/engine/test_replay.py`'s existing
  tests, the handle-less `truncate`'s among them, SHALL pass unchanged.
- **R6** — Records, in the same commit as the code they describe, saying exactly what the code delivers
  and no more:
  - `AGENTS.md` § 5's "Reading the source resolves through it too" paragraph SHALL say how `open` and a
    promotion off the source now resolve the place, and under which lock.
  - § 10's source-mutex bullet SHALL name them among its holders. If a promotion holds the mutex for its
    copy, the bullet SHALL state that delay beside the one it already states for the handle-less
    `truncate`.
  - The docstrings of `open` and `_serve` SHALL agree with that text.
  - § Known open violations SHALL drop the `open`/`_serve` row (`AGENTS.md:669`), or keep only what
    stays open, naming a seed that outlives this one.
  - The identity-gap row (`AGENTS.md:667`) SHALL name an owner that outlives this seed (Q2). So SHALL
    root `PLAN.md` § Risks (`PLAN.md:587-592`) and `issues/truncate-recording-after-ancestor-rename.md:98`.
    Once this cycle lands, no file outside `spec/` SHALL name `issues/source-served-open-race.md`.
  - The measured test counts in § Verification and § Conventions SHALL match the final tree.
- **R7** — The gate. All of these SHALL hold:
  - WHEN the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`) runs with
    `CI=true` on macOS, `tools/lint.sh` SHALL exit 0.
  - In that run, pytest SHALL report 0 failed and 0 errors. Its passed count SHALL exceed the count the
    same command reports at `5e0d997` (§ Verification records 502 unit tests there).
  - WHEN `uv run pytest tests/engine/test_model.py` runs on macOS without `CI` set, from a tree with no
    `.hypothesis/` directory, once for each `--hypothesis-seed` from 1 to 20, every run SHALL pass.
  - WHEN the CI workflow runs on this cycle's final tree, each of `Lint & build`, `Unit (Linux, 3.15)`,
    `Unit (Linux, 3.15t)` and `Unit (macOS, engine-only)` SHALL conclude `success`, as read from the
    run record.

## Non-goals (no-gos)

- **Closing the handle-less `truncate`'s identity gap with an identity token.** It is a schema
  change, and this cycle only re-homes the gap (R6, Q2). Home: the seed Q2 names.
- **A user `rename` of an ancestor moving the file's logical path mid-`open`.** Path locks do not
  cover descendants, so `_serve`'s catalog reads by `entry.path` can miss. That is a different window
  from a replay moving the source object. Home: `issues/freethreaded-concurrency-stress.md` ("Path
  locks do not cover descendants").
- **The handle-less `truncate`'s recording after an ancestor's rename.** Home:
  `issues/truncate-recording-after-ancestor-rename.md`.
- **Narrowing how long a data commit to the source holds the mutex.** Home:
  `issues/source-commit-staged-publish.md`.
- **Exercising the mutex under real threads** (stress, free-threaded). Home:
  `issues/freethreaded-concurrency-stress.md`.
- **`EngineError`, `LookupError` or `TierError` escaping the VFS API** on paths this cycle does not
  close. Home: `issues/engine-error-mapping.md`.
- **A mount-level reproduction and the integration lane.** Coverage is engine-level, under `-m unit`,
  on tmpdir tiers. Home: `issues/ci-integration-lane.md`.

## Constraints

- **`AGENTS.md` § Invariants § 1, § 5 and § 10 hold as written.** That includes the lock order: path
  locks, then the source mutex, then `_handles_mutex`, then the SQLite write lock. Nothing holding the
  source mutex calls `locks.hold`, `_ensure`, `getattr` or `readdir`. Nothing acquires the mutex inside
  `db.transaction()`. A design that bends any sentence of these sections goes in the plan's deviation
  table, before Geoffrey, and into the same diff's § Invariants text.
- **The handle-less `truncate` keeps the property Geoffrey signed off on 2026-11-08.** It is in
  `spec/source-path-truncate-and-stat/REVIEW.md`, "Geoffrey's rulings on the cycle 3 escalation",
  item 5. `truncate` calls `_serve` under the source lock, so any change to `_serve` is graded against
  that property too.
- **No state-database schema change.** There is no `SCHEMA_VERSION` bump. If the plan finds it cannot
  avoid one, that is a stop-and-ask.
- **No edit to `tests/engine/test_model.py`.** Hypothesis's fixed CI draw is a digest of that machine
  class's source, so any edit redraws the CI sample.
- **Same-commit records** (R6, `AGENTS.md` § Conventions). Q2's owner seed and its `ROADMAP.md` entry are
  written in this cycle.
- **Human sign-off at review** on the engine diff, unconditionally (Q3): Geoffrey signs off on the
  final `open`, `_serve` and source-mutex-holder diff whatever the review finds.

## Clarifications

Resolved:

- **Q:** Kind and appetite? — **A:** `fix`, `small`, as the seed's frontmatter gives them. Seven
  criteria, and one code region (`open` and `_serve`) plus records, so the size circuit breaker does
  not trip. That holds if Q2 is answered with a re-home and no code. (resolved 2026-11-08, foreman
  hand-off)
- **Q:** Where did the defect come from (`escaped`)? — **A:** `pre-factory`. Both faulty reads
  (`overlay.py:640` and `:661` at `5e0d997`) blame to `8dcc0d0` (2026-07-12, "WIP: implement
  committed-source-path replay…"). That commit is an ancestor of the factory adoption `6a046b4` and
  carries no `spec/`. The reads are unchanged at `141aa86`. `fe26962` (`deferred-rename-source-orphan`)
  added the source lock, without these reads. Correct it at the gate if that reading is wrong.
  (recorded 2026-11-08, shaper)
- **Q:** Does the seed's draft contract carry over? — **A:** As input only. Its R1 and R2 become R1 and
  R2 here, restated as `open`'s observable result. Its R3 becomes R5, with convergence added. Its R4
  (decide the identity gap) becomes the records duty in R6 plus Q2. R3, R4 and R7 are new: the
  gradeable property, no wrong object, and the gate. (recorded 2026-11-08, shaper)
- **Q1 — Which designs does R3 admit?** — **A:** (A), R3 as written. The place is decided from the
  catalog under the source lock, and the source object is opened before the lock is released. That
  admits holding the lock across the promotion copy, or pinning the opened object and copying from it;
  the plan chooses between them and states § 10's delay for the first. Re-resolve-and-retry is
  excluded: it leaves a failed copy's intent behind and loops while replays keep landing. Same shape as
  the property ruled for `truncate`. (ruled 2026-11-08, foreman as gate judge)
- **Q2 — Who owns the identity gap after this seed retires?** — **A:** (A), re-home it in this cycle to
  a new public seed `issues/entry-identity-token.md` (`status: unshaped`, `kind: fix`) with its own
  `ROADMAP.md` entry, and repoint the rows and text R6 lists to it. No code change. Not recorded as
  accepted behaviour: that would accept a silent, successful cut of another file, which only Geoffrey
  could rule. (ruled 2026-11-08, foreman as gate judge)
- **Q3 — Geoffrey's sign-off at review?** — **A:** (A), yes, unconditionally, as for cycles 2 and 3.
  He signs off on the final engine diff (`open`, `_serve` and the new source-mutex holder) whatever the
  findings. (Geoffrey, 2026-11-08, via Ares's signed board answer `2026/11-08-cycle4-signoff-answer`)
- **Q4 — Does R4 reach the identity gap?** — **A:** No. R4 is graded over interleavings in which no user
  `rename` of one of the file's ancestors runs during the call. The only route on which R4's IF-clause holds
  and its THEN cannot be met without an identity token (a purged id reused, the new file carried onto the
  path by a directory rename, a flush moving its object onto the old place) needs that rename, which the
  Non-goals already exclude. The gap is recorded in `issues/entry-identity-token.md` (Q2), and the code and
  records SHALL say "not another entry's object" only as qualified by the reused id. (ruled 2026-11-08,
  foreman at the plan gate, PLAN deviation row 5)
- **Q5 — When does R6's last clause hold?** — **A:** Once the roadmap step has retired the seed and its
  `ROADMAP.md` entry, which is part of landing (as for cycle 3, `c2b1544`). On the cycle branch the seed's
  own `ROADMAP.md` entry is the one name left; nothing else outside `spec/` names it. (ruled 2026-11-08,
  foreman at the plan gate, PLAN deviation row 6)

## Related materials

- Seed: [`issues/source-served-open-race.md`](../../issues/source-served-open-race.md)
- Previous cycle (context, not this contract): [`spec/source-path-truncate-and-stat/`](../source-path-truncate-and-stat/),
  covering its plan research brief 01 § 6, its plan-gate rulings (`REVIEW.md:24-28`) and its
  review-gate rulings
- Related seeds: `issues/truncate-recording-after-ancestor-rename.md`,
  `issues/freethreaded-concurrency-stress.md`, `issues/source-commit-staged-publish.md`,
  `issues/engine-error-mapping.md`
- Contract text: `AGENTS.md` § Invariants § 5 and § 10, the high-blast-radius tables (the placement
  row), and § Known open violations (`AGENTS.md:667`, `:669`)
- Sources, anchors re-confirmed at `5e0d997`:
  - `src/sosfs/engine/overlay.py`:
    - `_ensure` 214;
    - `open` 619, with its lookup 629, `_serve` call 636, `serve_path` 640 and `open_direct` 641;
    - `_serve` 651, with `src_path` 661 and `transfers.copy` 662-663;
    - `truncate` 693, which calls `_serve` under the source lock at 727;
    - `_held_path` 802;
  - `tests/engine/test_replay.py`, `assert_converged` 181.
