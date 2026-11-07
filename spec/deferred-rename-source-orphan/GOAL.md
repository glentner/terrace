# GOAL — A commit before a queued rename leaves no orphan, and the replay converges

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** deferred-rename-source-orphan
- **kind:** fix
- **appetite:** big

## Problem

Namespace ops are deferred: `rename`, `unlink`, `rmdir` and `mkdir` change the catalog when the call
returns and queue a row that changes the source later. While a row is queued, where the source
physically holds an entry's bytes can differ from the entry's catalog path. `AGENTS.md` § Invariants
§ 5 and `PLAN.md` → Write-back → Deferred-op replay both promise that this lag is invisible. Every
physical touch of the source resolves through `entry.source_path`, so nothing is orphaned and the
source converges on the overlay view. The tree at `141aa86` breaks that promise in two ways, both
**pre-existing** (every faulty line landed before the factory was adopted).

**1. A per-entry data commit during the lag orphans the pre-rename object.** Write a file, rename it
and commit the entry to the source before the rename row runs. The source then holds the new bytes at
the new path *and* the old bytes at the old path. The queued rename finds nothing to do, both queues
report empty, and nothing in the catalog or journal refers to the old object. A second engine over the
same tier roots adopts it as live data. Reproduced at `141aa86` on macOS (2026-11-05) with the seed's
probe, two tiers and no mount:

```
after commit_upstream: ['.sosfs', 'x.dat', 'y.dat']
after flush:           ['.sosfs', 'x.dat', 'y.dat']
queued: [] pending: []
remount readdir:       ['/x.dat', '/y.dat']      /x.dat bytes: b'original'
```

That is resurrection of renamed-away data behind a drain that reports success, and of deleted data for
the `unlink`-shaped variant. Three production paths commit one entry during the lag, none of them
test-only:
- `fsync` under `fsync_policy = "commit"` (`src/sosfs/engine/overlay.py:707`);
- the eviction manager committing a dirty copy under quota pressure (`src/sosfs/engine/evict.py:151`);
- a write-back scheduler poll that reaches the data row before the rename row. That is the normal
  order when the write preceded the rename.

`flush` and `drain` hide it because both run namespace rows first (`overlay.py:876-878`,
`drain.py:94-100`). `WritebackScheduler.due` sorts the same way only *within* one poll
(`writeback.py:88`).

**2. `flush()` replays queued namespace ops that later ops have invalidated.** The stateful model test
`tests/engine/test_model.py::TestOverlayModel::runTest` fails on this in every CI unit job read under
the factory (Linux 3.15, Linux 3.15t, macOS; runs 42023846602 and 42059324110). It fails on every run
under `CI=true` (Hypothesis's fixed seed) on the development Mac, and on some random seeds. There are
three failure classes, all in the same replay family:

- **EACCES from the rename replay.** `mkdir /d1, mkdir /d2, rename /d2 → /d1/s1, rename /d1/s1 → /d2,
  rmdir /d1, create /b.dat, rename /b.dat → /d1, flush()`. `flush()` raises `PermissionError: [Errno
  13]` from `PosixTier.rename` (`project/d2` → `project/d1/s1`) inside `_execute_namespace`
  (`overlay.py:928`).
- **A stray source directory.** The same prefix without the last two ops, or `mkdir /d1, mkdir /d1/s1,
  rename /d1/s1 → /d2, rmdir /d1, flush()`. `flush()` completes, and the source keeps a `d1` that the
  overlay removed.
- **`ENOTEMPTY` on re-creating a removed directory.** `mkdir /d1, mkdir /d1/s1, flush(), rmdir /d1/s1,
  rmdir /d1, mkdir /d1`. The last `mkdir` raises `OSError(66, 'Directory not empty')` from `_vacate`'s
  synchronous source removal (`overlay.py:972-997`), where POSIX and the oracle succeed.

All three reproduced at `141aa86` on macOS on 2026-11-05. The first two came from the profile's
`verify` under `CI=true`: `1 failed, 448 passed`, "Hypothesis found 2 distinct failures". The third
came from a direct engine probe. The landed toolchain repair (`spec/toolchain-repair/`, R2, R8, R9)
tolerates this one test as the only failure until this cycle lands, and its Clarifications require
this cycle to leave `verify` fully green.

**Why the suite never caught the first defect.** The model's only convergence checkpoints are
`quiesce_and_flush` and `teardown`, and both run `flush()`. No rule commits one entry, and no rule
advances a scheduler poll, so the model cannot reach the interleaving behind defect 1.
`PLAN.md` § Risks (lines 538-546) records this divergence class as *resolved, Phase 8* on the strength
of that model. Under the plan-drift rule, that claim is part of what this cycle repairs.

Two smaller defects share the logical-path root cause:
- `truncate` without a handle raises `ENOENT` on a source-served file whose rename is queued;
- the live-stat and `_record` paths leave a permanently wrong size in the catalog.

Both reproduced at `141aa86`: `FileNotFoundError` on `truncate`, and `getattr().size == 1000` for a
4-byte object after `release` and after `flush`. Whether they ride in this cycle is an open question
below.

## Outcome / vision

When an entry's data reaches the source while a namespace row for it is still queued, the source ends
up holding exactly one object for that entry, at the place the overlay says it lives. A drain that
reports an empty queue means the source tree equals the overlay view, and a remount adopts nothing the
catalog had abandoned. `flush()` converges on the overlay view for any sequence of namespace ops,
including ops whose paths later ops reused. The stateful model reaches per-entry commits and scheduler
polls, so this interleaving is covered, not just fixed.

The profile's `verify` is fully green on macOS and Linux, `test_model` included, under the fixed CI
seed and under random seeds. CI's four jobs conclude `success`, which retires the toolchain repair's
tolerance. `PLAN.md`'s risk register and `AGENTS.md`'s open-violations table say truthfully which
divergence classes are closed.

## Acceptance criteria (the contract)

Here, "the overlay view" means what the engine reports through `readdir`, `getattr` and `read`. "The
queue drains" means `journal.queued() == []` and `journal.pending() == []`. Counts are the numbers
pytest prints in its summary line, asserted as numbers (`AGENTS.md` § Verification).

- **R1** — WHEN an entry's data is committed to the source while a `rename` row for that entry is still
  queued, the source tree SHALL, once the queue drains, hold exactly one object for that entry. It SHALL
  sit at the entry's catalog path and carry the committed bytes, with nothing left at the pre-rename
  path. Each of these routes SHALL satisfy it:
  - `fsync` under `fsync_policy = "commit"`;
  - the eviction manager committing the dirty copy;
  - a write-back scheduler poll that settles the data row before the rename row.

  (The seed probe's source root lists `['.sosfs', 'y.dat']`, not `['.sosfs', 'x.dat', 'y.dat']`.)
- **R2** — WHEN a file inside a directory whose `rename` row is still queued is committed to the source,
  the source SHALL, once the queue drains, hold the directory and every child at the new prefix, with
  the committed file's new bytes, and nothing at the old prefix. Every child SHALL be readable through
  the overlay at its catalog path in the meantime. (In scope: Clarifications Q3.)
- **R3** — WHILE the queue is drained, the source tree SHALL equal the overlay view: the same set of
  paths, the same kind for each, and the same bytes for each file.
- **R4** — WHEN the queue has drained and the engine stops, a fresh `OverlayEngine` started over the
  same tier roots SHALL list exactly the entries the previous engine's catalog held. It SHALL adopt no
  path that had been renamed away or removed.
- **R5** — WHEN `flush()` settles a queue in which later namespace ops reused or removed a path an
  earlier queued op names, `flush()` SHALL complete without raising, and the source tree SHALL equal
  the overlay view. Both shrunk sequences under Problem → 2 (the EACCES one and the stray-directory
  one) SHALL be among the cases that show it.
- **R6** — WHEN a directory is removed while its source removal is still queued and is then created
  again at the same path, the `mkdir` SHALL succeed as it does on a plain directory. Once the queue
  drains, the source SHALL hold an empty directory there. (Sequence: `mkdir /d1, mkdir /d1/s1, flush(),
  rmdir /d1/s1, rmdir /d1, mkdir /d1`.)
- **R7** — The stateful model test SHALL include, as ops in its rule mix:
  - committing a single entry's data to the source;
  - advancing a single write-back scheduler poll.

  It SHALL also check source↔overlay convergence after the queue drains from states those ops
  produce, not only at the `flush()` checkpoint.
- **R8** — The stateful model test SHALL keep every rule it has at `141aa86`. Its path, data and offset
  strategies SHALL be no narrower, and its `max_examples` and `stateful_step_count` no lower (100 and
  50). It SHALL add no skip guard, `assume`, precondition or filter that excludes a window the
  model reaches today.
- **R9** — WHEN the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`) runs with
  `CI=true` on macOS, `tools/lint.sh` SHALL exit 0. pytest SHALL report 0 failed and 0 errors, with a
  passed count of at least 449 (the 448 that pass at `141aa86` plus `test_model`).
- **R10** — WHEN `uv run pytest tests/engine/test_model.py` runs on macOS without `CI` set, from a
  tree with no `.hypothesis/` directory, once for each `--hypothesis-seed` from 1 to 20, every run
  SHALL pass. (Clarifications Q6: Linux is graded through R11's fixed CI seed.)
- **R11** — WHEN the CI workflow runs on this cycle's final tree, as read from the run record rather
  than assumed, each of `Lint & build`, `Unit (Linux, 3.15)`, `Unit (Linux, 3.15t)` and
  `Unit (macOS, engine-only)` SHALL conclude `success`.
- **R12** — `PLAN.md` § Risks SHALL NOT record the write-back namespace-replay divergence class as
  *resolved, Phase 8* unless it is resolved in the shipped tree. The entry SHALL be reconciled in the
  same diff as the fix, and SHALL name any part of the class this cycle leaves open.

## Non-goals (no-gos)

- **The `truncate` and live-stat siblings.** These are `truncate` without a handle raising `ENOENT`
  under a queued rename, and the wrong catalog and residency size after a handle-based truncate. They
  are deferred to a follow-up seed, which this cycle writes (see Constraints). (Clarifications Q1.)
- **Correcting catalog or residency sizes that existing state databases already hold wrong.** This
  goes to the siblings' follow-up seed and to [`issues/audit-command.md`](../../issues/audit-command.md),
  not to this cycle. (Clarifications Q5.)
- **Catalog↔source reconciliation, or recovery of orphans already on a source.** That is the audit's
  job (`PLAN.md:213-215`). This cycle prevents new orphans and does not find old ones.
- **Weakening the model or the replay tests.** That means removing a rule, narrowing a strategy,
  lowering a setting, or adding a skip guard (R8, and `AGENTS.md`'s high-blast-radius table).
- **The mount-level reproduction and the integration lane.** Coverage added here is engine-level and
  runs under `-m unit`. The mount-level version is [`issues/ci-integration-lane.md`](../../issues/ci-integration-lane.md).
- **Other open violations in the same regions.** The shutdown-rescue gaps, engine error mapping and
  archive journaling have their own seeds.
- **A state-database schema change.** No `SCHEMA_VERSION` bump: there is no migration path. If the plan
  finds it cannot avoid one, that is a stop-and-ask, not a deviation.

## Constraints

- **Human sign-off gate on the fix.** This cycle edits high-blast-radius paths: `_commit_hop`,
  `commit_upstream`, `_execute_namespace`, `_vacate` and `tests/engine/test_model.py`, and likely
  `engine/writeback.py` and `engine/evict.py`. Geoffrey signs off on the fix at review before the
  cycle lands, whether or not review confirms a finding in those regions. (Decided before shaping,
  2026-11-05.)
- **Same-commit records.** The diff that fixes the defect also updates `AGENTS.md` (§ Known open
  violations, the § 5 row, and the measured test counts) and `PLAN.md` (R12) to match what ships.
  If Q1 splits the siblings, the § 5 row stays and names the follow-up seed for exactly what remains
  open.
- **Deferral is recorded before the seed retires.** When this cycle lands, `code terrace roadmap`
  deletes `issues/deferred-rename-source-orphan.md`. Before then, anything this GOAL defers must move
  to its own `issues/` seed with a `ROADMAP.md` entry, keeping the seed's reproductions and
  `file:line` evidence. That covers the siblings and the already-wrong sizes if Q1 splits them.
- **`AGENTS.md` § Invariants § 1 and § 5 hold as written** (intent → act → confirm, confirm before
  purge, `_populate_tree` before a directory rename, `_vacate` as the one clock jump). A design that
  overturns any sentence of them goes in the plan's deviation table, before Geoffrey, and into the
  same diff's § Invariants text.

## Clarifications

Resolved:

- **Q:** What must the outcome include? — **A:** The profile's `verify` must be fully green on Linux
  and macOS, `test_model` included, under `CI=true` and under random seeds. CI's four jobs must
  conclude `success`, read from the run record. Together these retire the toolchain repair's
  tolerance (its R2, R8, R9). (resolved 2026-11-05, foreman ruling)
- **Q:** May the model test change? — **A:** Extending its op mix is in scope. Weakening it is
  forbidden by `AGENTS.md`'s high-blast-radius table. (resolved 2026-11-05, foreman ruling)
- **Q:** Is the `ENOTEMPTY` failure through `_vacate`, found in toolchain-repair's review cycle 2, in
  scope? — **A:** Yes, as the third class of the same replay family (R6). (resolved 2026-11-05,
  foreman ruling)
- **Q:** Human sign-off on the fix? — **A:** Yes, at review. It is recorded under Constraints.
  (resolved 2026-11-05, foreman ruling)
- **Q:** Kind and appetite? — **A:** `fix`, `big`, as the seed's frontmatter gives them. (resolved
  2026-11-05, foreman hand-off)

Resolved at the GOAL gate (2026-11-05, foreman ruling). Every recommendation below is accepted as
written; Q1's follow-up seed and its `ROADMAP.md` entry are written in this cycle's diff, per
Constraints, and the plan's last phase carries them in its deferral ledger:

- **Q1 — Do the `truncate` and live-stat siblings ride along?** — accepted.
  *Recommended: split them into a follow-up seed.* R1–R12 already reach the circuit breaker's ~8–10
  (twelve criteria). The siblings would add at least two more R-IDs, plus Q4 and Q5. They would also
  pull in a third high-blast-radius region, `truncate` (metadata-before-ack), and a data-correction
  question that touches the quota counters. They do not block `verify`: the model serves nothing off
  the source (no `bypass_size`), so it never reaches them. Keeping them costs one shared diff over
  regions this cycle already opens. Splitting them costs a second, small cycle in a region that is
  fresh in mind. If the gate keeps them, add:
  - **R13**: WHEN `truncate` is called with no open handle on a source-served file whose entry
    carries a queued `rename`, the engine SHALL truncate the object the overlay serves to the
    requested length and SHALL NOT raise `FileNotFoundError`.
  - **R14**: After a handle-based `truncate` and a write on a source-served file with a queued
    rename, `getattr().size` and the residency size SHALL equal the byte length the serving tier
    holds, both while the handle is open and after `release`.

  Then drop the first Non-goal and amend the Constraints accordingly.
- **Q2 — Where does the commit write: at `source_path`, or at the logical path with the stale object
  removed?** — accepted. *Recommended: the plan chooses, inside R1–R6.* Either is a design
  choice, and the criteria are written so that either can pass. The shaper leans toward writing at
  `source_path` and keeping the queued rename as the only thing that moves a source object. That
  keeps one mover, matches § 5 as written, and needs no deviation. Collapsing the rename into the data
  commit would overturn § 5's "committing a rename moves the object", leave a queued row with nothing
  to do, and need a journal ruling on whether that row was settled or executed. It is also an egress
  decision on S3, where a rename is a copy plus a delete. Either way, the deviation table records
  the choice.
- **Q3 — Are directory renames in scope?** — accepted. *Recommended: yes, as R2.* One
  source rename relocates a whole subtree (`catalog.advance_source_path`,
  `src/sosfs/state/catalog.py:186-191`). A per-file commit that advances only its own `source_path`
  inside a renamed directory leaves its siblings pointing at the old prefix, so a fix checked only
  on the single-file case can leave the subtree case orphaning. The model's directory and file paths
  (`/d1/a.dat`, `/d1/s1/a.dat`) will reach it once R7 lands, so leaving it out risks R9/R10 anyway.
- **Q4 — `truncate`'s contract** (only if Q1 keeps the siblings). — moot, since Q1 splits them; the recommendation goes to the follow-up seed.
  *Recommended: resolve through the same serve path `open` uses, rather than forcing the queued
  namespace op first.* Forcing it would make an unrelated `truncate` do unbounded namespace work.
  It would also add a second place a namespace op jumps its policy clock, overturning § 5's "`_vacate`
  is the one place".
- **Q5 — Correcting already-wrong catalog sizes?** — accepted. *Recommended: out of this
  cycle.* Hand it to the siblings' follow-up seed, with the audit as its natural home. A one-time
  correction rewrites every existing state database's residency sizes, and the quota counters eviction
  trusts are built from them. It would need its own contract and its own evidence. The siblings'
  code fix only stops new wrong sizes.
- **Q6 — What counts as "green under random seeds"?** — accepted. *Recommended: R10 as
  written.* That is 20 fixed `--hypothesis-seed` values (1–20) on the development Mac, from a tree
  with no example database, so the evidence is reproducible. Linux is graded through CI's fixed seed
  (R11). Today's random-seed failure rate on the Mac was 2 in 12, so 20 clean seeds is meaningful
  evidence, not proof. Random-seed runs on Linux would need a container or a CI change, and this GOAL
  does not ask for either.
- **Q7 — How far does the model's extension reach?** — accepted. *Recommended: R7's two
  ops are required. An `fsync`-under-commit-policy rule and an eviction rule are the plan's choice.
  A source-served configuration (a stage `bypass_size`) stays out unless Q1 keeps the siblings.* A
  model that serves files off the source would reach the deferred siblings and turn `verify` red,
  which R9 forbids.
- **Q8 — Where did the defect come from (`escaped`)?** *Recorded as `pre-factory`.* Every faulty line
  blames to a pre-factory Phase 4, 5 or 8 commit (`4aea975`, `437e27f`, `8d2265f`, `8dcc0d0`,
  `b070407`; July 2026), all ancestors of the factory adoption at `6a046b4`. None of them carries a
  `spec/` directory. Correct it at the gate if that reading is wrong.

## Related materials

- Seed: [`issues/deferred-rename-source-orphan.md`](../../issues/deferred-rename-source-orphan.md)
  (its probes, the CI run IDs, and the `@reproduce_failure` pointers)
- Landed predecessor: [`spec/toolchain-repair/GOAL.md`](../toolchain-repair/GOAL.md), whose R2, R8 and
  R9 tolerance this cycle retires
- Related seeds: [`issues/audit-command.md`](../../issues/audit-command.md),
  [`issues/ci-integration-lane.md`](../../issues/ci-integration-lane.md),
  [`issues/shutdown-rescue-gaps.md`](../../issues/shutdown-rescue-gaps.md),
  [`issues/plan-md-reconciliation.md`](../../issues/plan-md-reconciliation.md)
- Contract text: `AGENTS.md` § Invariants § 1, § 5, the high-blast-radius table, and § Known open
  violations (the § 5 row). `PLAN.md` → Write-back → Deferred-op replay (lines 194-215) and § Risks
  (lines 538-546).
- Sources (anchors re-confirmed at `141aa86`):
  - `src/sosfs/engine/overlay.py`: `_commit_hop` 784, 808; `fsync` 707; `flush` 876-878;
    `_execute_namespace` around 928; `_vacate` 972-997; `truncate` 685; `_record` 732; live stat 277.
  - `src/sosfs/engine/transfer.py:93`, `evict.py:151`, `drain.py:94-100`, `writeback.py:88`.
  - `src/sosfs/state/catalog.py:186-191`.
  - `tests/engine/test_model.py`.
