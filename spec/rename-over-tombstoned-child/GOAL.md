# GOAL — Rename a directory onto a name a removed entry once held

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** rename-over-tombstoned-child
- **kind:** fix
- **appetite:** small

## Problem

`OverlayEngine.rename` of a directory raises `sqlite3.IntegrityError: UNIQUE constraint failed:
entry.path` where a plain POSIX directory renames without error. The trigger is a file that had been
committed to the source under the target name, then moved out and removed before its move reached
the source: rename `/d2/a.dat` to `/a.dat`, unlink `/a.dat`. After that, renaming any directory that
holds an `a.dat` onto `/d2` raises. The removal of `/d2` itself is not needed: the rename fails the
same way when `/d2` is a live empty directory, or no longer exists.

It hurts three ways.

- **Not an `OSError`.** § Invariants 11 promises that every failure out of the VFS API is an `OSError`
  subclass. This one reaches libfuse as an unhandled exception, so the application sees the binding's
  fallback, not an errno.
- **It does not go away.** Draining the writeback queue does not clear the condition, retrying the
  rename raises again, and so does the same rename after the engine stops and starts on the same
  state. Under that name, the directory rename stays broken for the life of the state database.
- **A failed rename destroys the target.** When `/d2` is a live empty directory, the rename removes
  it before it raises, from the namespace and from the source, though the rename never happened.
  POSIX `rename` either succeeds or leaves both names as they were.

Confirmed at `origin/main` = `980f998`, on 2026-11-09. The exception is raised in `Catalog.rename`'s
descendant prefix rewrite (`src/sosfs/state/catalog.py:180`), called from `OverlayEngine.rename`
(`src/sosfs/engine/overlay.py:486`). That rewrite is the only code that rewrites `entry.path` by prefix,
and `entry.path` is the only `UNIQUE` path column (`src/sosfs/state/schema.sql:60`). After the
move-out and unlink, and still after a full drain, the catalog holds a removed row at `/d2/a.dat`
with nothing queued against it. Why that row outlives its removal is **unverified** and is the plan
step's to root-cause: the criteria below state only what a user observes.

The defect is **pre-existing**: it reproduces at `141aa86`, before the first engine cycle landed. The
faulty lines blame to pre-factory work (`d6803f1`, the catalog's prefix rewrite; `437e27f`, the
rename's old-path tombstone and the removal path that cancels a queued move).

## Outcome / vision

A directory rename onto a name that removed entries once held behaves as it would on a plain POSIX
directory: it succeeds, the namespace and bytes match, and the source catches up when the queue
drains. A rename that cannot succeed fails with an errno and changes nothing.

## Acceptance criteria (the contract)

"The trigger" means: a file committed to the source at a path inside the target name, then moved or
renamed away and removed while that move is still queued, whether or not the target directory was
also removed. "Matches the oracle" means the overlay's namespace (`readdir`, `getattr` kind) and file
bytes equal those of a plain directory tree that performed the same sequence of calls.

- **R1** — WHEN a directory is renamed onto a name that is absent or an empty directory, after the
  trigger left a removed entry at a path that the rename moves a same-named entry onto, the engine's
  `rename` SHALL succeed, and the namespace and file contents SHALL then match the oracle.
- **R2** — R1 SHALL hold at any depth of the moved directory: a removed entry's former path coinciding
  with a nested subdirectory's file, not only a direct child's.
- **R3** — R1 SHALL hold whether or not the writeback queue drained between the trigger and the rename,
  and after the engine stops and starts again on the same state in between.
- **R4** — WHEN the writeback queue drains after a rename satisfying R1, the source SHALL hold exactly
  the oracle's tree and bytes: the moved directory and its files under the new name, nothing left
  under the old name, and nothing the trigger removed.
- **R5** — WHILE the sequences of R1–R3 run, no exception other than an `OSError` subclass SHALL escape
  `rename` or any other engine call they make.
- **R6** — A deterministic regression test SHALL drive the reproduction in Related materials, plus the
  live-empty-target and drained-before-rename variants, against the engine and assert R1 and R4. It
  SHALL live outside `tests/engine/test_model.py`, with a `unit` marker.

## Non-goals (no-gos)

- **Mapping other non-`OSError` escapes to errnos.** The `EngineError`, `LookupError`,
  `AttributeError` and `TierError` escapes are `issues/engine-error-mapping.md`'s. This cycle makes
  the rename succeed; it does not add a catch-all translation around the API.
- **Editing `tests/engine/test_model.py`**, including the composite model rule that found this defect.
  That file belongs to the `model-commit-route-reach` cycle in this period, and its model reach is that
  cycle's to land.
- **Concurrency.** The trigger is single-threaded; free-threaded races around rename stay with
  `issues/freethreaded-concurrency-stress.md`.
- **Archive and S3 sources.** The reproduction is on the POSIX test stacks (`build_spec`, three tiers);
  no criterion asks for a non-POSIX source drive.
- The two scope questions below, unless ruled in.

## Clarifications

- **Q1:** After the trigger and a full drain, the catalog still holds a removed row at the
  file's old path with no queued operation against it, and it survives a restart (seen 2026-11-09 at
  `980f998`). Nothing user-visible depends on it except this rename, but `sosfs audit` would have to
  explain it. Is clearing it part of this contract?
  - (a) Yes: add a criterion that after a drain following the trigger, no removed row remains for a
    path with nothing queued against it.
  - (b) No: Non-goal; record it in `issues/audit-command.md` as a state the audit must repair.
  - (c) The plan decides after root-causing: if the fix it chooses clears the row, it says so; if it
    leaves the row, the cycle records it in a seed as in (b).
  - **Recommended: (c).** The row may be the cause (then the fix clears it for free) or a harmless
    leftover (then a contract line pins a mechanism the diagnosis has not confirmed). Step 5 forbids
    a fix criterion that names the suspected mechanism.
  - **A:** (c). The plan root-causes first and states whether its fix clears the stranded row; if it
    does not, the cycle's records phase files the row in a seed (`issues/audit-command.md` or a new one)
    with this cycle's reproduction. (ruled 2026-11-09, foreman as gate judge)
- **Q2:** A rename that fails after replacing its target removes an existing empty directory
  from the namespace and the source, as the live-empty-target variant shows. R1 removes this
  trigger. Should the contract also require failure atomicity: any `rename` that raises leaves both
  names as they were?
  - (a) Yes, for every failure path of `rename`.
  - (b) Only for the trigger's sequences. Once R1 holds, that is moot, so this is (c) in effect.
  - (c) No: Non-goal; the cycle records the replace-then-fail ordering in a seed
    (`issues/invariant-audit-gaps.md` or a new one) with this reproduction as its evidence.
  - **Recommended: (c).** With R1 met there is no known sequence that reaches the loss. Making
    `rename` atomic against every later failure means reordering a replaced target's removal around
    the catalog move, a design change to high-blast-radius paths (`rename`, `_remove`, `_purge_source`)
    that a `small` appetite should not take on unbidden.
  - **A:** (c). Failure atomicity of `rename` is a Non-goal. The cycle's records phase files the
    replace-then-fail ordering in a seed, with the live-empty-target reproduction as evidence, and R6's
    live-empty-target variant shows the empty directory is renamed over, not lost. (ruled 2026-11-09,
    foreman as gate judge)
- **Q3:** Planning stopped before the plan commit. A second base-engine defect lies inside R1 × R2 × R5:
  with two nested removed directories and a grandchild moved out and then removed, replaying the nested
  `rmdir` rows recurses until `RecursionError`. `flush`, drain and the scheduler all fail, and because
  the queue persists, it never drains. The foreman reproduced it on `980f998`. How does this cycle
  proceed?
  - **A:** A separate fix cycle first, for the replay recursion, shaped on its own; it lands on `main`.
    This cycle is parked unchanged and resumes on the new `main`: the rename fix, its tests and the
    root cause (Q1's answer: the fix does not clear stranded rows outside a rename's path, from either
    stranding route) carry over from the parked prototype. After this cycle lands,
    `model-commit-route-reach` resumes. (Geoffrey, 2026-11-09, in the foreman's session)

## Related materials

- Found by the `model-commit-route-reach` cycle's model reach on 2026-11-09, at its plan step. Its
  write-up (catalog dump, Hypothesis seed 5 falsifying example, which draws reach it) is on the
  parked, unpushed commit `refs/park/model-commit-route-reach-plan` (`3572ed9`), at
  `spec/model-commit-route-reach/research/03-engine-defect.md`. Read it with `git show`, and never
  modify it.
- Deterministic reproduction at `980f998` (from the worktree root, `PYTHONPATH=.:src`):

  ```python
  import dataclasses, tempfile
  from pathlib import Path
  from sosfs.engine.overlay import OverlayEngine
  from tests.engine.conftest import Clock, build_spec, seed
  root = Path(tempfile.mkdtemp(prefix='repro-'))
  seed(root / 'project', 'seeded.txt', b'initial-content')
  engine = OverlayEngine(dataclasses.replace(build_spec(root, tiers=3), fsync_policy='commit'), clock=Clock())
  engine.start()
  engine.mkdir('/d2'); engine.create('/d2/a.dat'); engine.commit_upstream('/d2/a.dat')
  engine.rename('/d2/a.dat', '/a.dat')   # queued
  engine.rmdir('/d2')                    # optional: the rename fails without it too
  engine.unlink('/a.dat')                # removed while its move is still queued
  engine.mkdir('/d1'); engine.create('/d1/a.dat')
  engine.rename('/d1', '/d2')            # raises sqlite3.IntegrityError
  ```

  Variants confirmed the same day: drop the `rmdir` (then `/d2` is a live empty directory, and the
  failed rename also removes it); `engine.flush()` before `mkdir('/d1')`; stop and start the engine
  before it; `/d1/s/b.dat` beside `/d1/a.dat`; the trigger at `/d2/s/a.dat` with the moved directory
  holding only `/d1/s/a.dat`; the file renamed twice (`/a.dat`, then `/b.dat`) before
  the unlink. All raise. Renaming a directory with no colliding child onto `/d2`, a file onto `/d2`,
  and `mkdir('/d2')` then `create('/d2/a.dat')` all succeed.
- Where the defect came from: pre-factory (blame above; recorded with `metrics.py escaped --from
  pre-factory`).
- Source: `src/sosfs/engine/overlay.py` (`rename`, `_remove`, `_vacate`), `src/sosfs/state/catalog.py`
  (`rename`), `src/sosfs/state/schema.sql`. Oracle: `tests/engine/test_model.py` (`OverlayMachine`,
  read only).
- `AGENTS.md` § Invariants 1, 5, 11; § Known open violations (the § 11 row). `rename`, `_remove`,
  `_vacate` and `_purge_source` are on the high-blast-radius list, so a fix there takes the human gate.
- Related seeds: `issues/engine-error-mapping.md`, `issues/audit-command.md`,
  `issues/invariant-audit-gaps.md`.
