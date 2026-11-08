# GOAL — `truncate` without a handle finds a source-served file during a queued rename

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** source-path-truncate-and-stat
- **kind:** fix
- **appetite:** small

## Problem

Namespace ops are deferred. `rename` moves the catalog entry when the call returns and queues a row
that moves the source object later. While that row is queued, the source still holds the entry's
bytes at `entry.source_path`, and `AGENTS.md` § Invariants § 5 makes that the place every physical
touch of the source resolves to. A file is *source-served* when its open handle points at the source:
it is over every stage's `bypass_size`, or no stage takes it at all (a one-tier stack, or a `create`
straight onto the source). Its bytes sit off its logical path while its own rename, or an
ancestor's, is queued.

**`truncate` without a handle raises `ENOENT` on such a file, which exists.** POSIX callers read that
as "the file vanished". The FUSE adapter reaches this path whenever the kernel truncates by path with
no file handle (`src/sosfs/fuse/adapter.py:206-211`), as `truncate(1)` and `truncate(2)` do.
Re-confirmed at `0e6d14c` (2026-11-06) with tmpdir tiers, no mount, and `Clock`, `build_spec` and
`seed` from `tests/engine/conftest.py`. Each of these raises `FileNotFoundError` naming the logical
path under the source root:

| Stack | Sequence | Raises for |
|---|---|---|
| two tiers, stage `bypass_size=100`, 1000-byte file | `rename /big2.bin → /moved.bin`, `truncate('/moved.bin', 4)` | `project/moved.bin` |
| same | same, growing: `truncate('/moved.bin', 2000)` | `project/moved.bin` |
| two tiers, stage `bypass_size=100` | `rename /data → /moved`, `truncate('/moved/big.bin', 4)` | `project/moved/big.bin` |
| one tier, `data/a.txt` adopted | `rename /data → /moved`, `truncate('/moved/a.txt', 0)` | `project/moved/a.txt` |
| one tier | `rename /data → /moved`, `create /moved/c.txt`, write 5 bytes, release, `truncate('/moved/c.txt', 0)` | `project/moved/c.txt` |
| one tier | `create /n.txt`, write, release, `flush()`, `rename /n.txt → /m.txt`, `truncate('/m.txt', 1)` | `project/m.txt` |

Controls behave: the same `truncate` with no rename queued, and on a file served from a stage under a
queued directory rename, both succeed, and after `flush()` the source holds the new length. A queued swap
(`rename /a.bin → /t.bin`, `rename /b.bin → /a.bin`) raises `ENOENT` too, but touches no other
object: the second rename's `_vacate` has already moved `t`'s object off `/a.bin`. The failing call
changes nothing: no catalog row, no residency and no object moves before it raises.

`open` resolves this correctly. It serves a source-held copy from `entry.source_path`
(`src/sosfs/engine/overlay.py:640-641`), and `_serve` promotes from it (`overlay.py:661`). The
previous cycle brought `getattr`'s live stat (`overlay.py:290`) and `_record` (`overlay.py:769`)
under the same rule through `_stat_held` (`overlay.py:782-789`). `truncate` without a handle opens
the logical path on the serving tier (`overlay.py:713`).

**Pre-existing, widened since.** The faulty line blames to `4aea975` (2026-07-02), before the factory
was adopted at `6a046b4`. On the pre-cycle base `141aa86`, every row above raises except the
`create` one. There, `create` put the file at its logical path. `deferred-rename-source-orphan`
(landed `fe26962`) places a file created under a queued directory rename at the directory's old
prefix (§ 5, who sets `source_path`), which brought those files into reach.

**What else the seed carried, re-checked at `0e6d14c`:**

- *The live stat and `_record`*: closed by the previous cycle. Through a `bypass_size` stage, with
  the file's own rename queued and with an ancestor's, a handle-based `truncate` to 0 and a 4-byte
  write report `getattr().size == 4` while open, after `release`, and after `flush()`. The source
  tree holds 4 bytes and `assert_converged` passes. One test pins this on one tier with no
  `bypass_size`
  (`tests/engine/test_replay.py::test_size_of_a_file_created_on_the_source_inside_renamed_directory`).
  None pins it through a `bypass_size` stage.
- *The seed's draft R2 is wrong on one point.* It asks for the residency size to equal the object's
  length "while the handle is open". The residency size is the accounted size. `_record` writes it
  at `fsync` and at `release` only, and it stays at the old figure while a handle is open on any
  stack, with or without a rename (probe: 1000 while open, 4 after `fsync` and after `release`, on
  a `bypass_size` stage with no rename, and on a staged copy). That is the design, not this defect.
- *Sizes already wrong in existing state databases* (Clarifications Q3). The old `_record` and live
  stat looked at the logical path, so they could leave a wrong size only for a copy held on the
  source. A staged tier holds its copy at the logical path. Probe, with `entry.size` and the source
  residency set to 1000 for a 4-byte object:
  - The source residency size heals on the next `release` of any handle on that copy, even a
    read-only one.
  - `entry.size` does not heal. A read-only open, an `fsync` with no write, `flush()`, a remount,
    and a rename plus `flush()` all leave it at 1000. Only a write through a handle, or a `truncate`,
    corrects it.
  - A stale `entry.size` is what `getattr` reports, and placement reads it. The 4-byte file stays
    served off the source, where its true size would stage it.
  - The staged-tier quota counters are untouched: `_record` never adjusts a source counter, and no
    staged copy was ever mis-sized.

  The project is unreleased (`0.1.0.dev0`, no tags), so such databases exist only on development
  machines.

## Outcome / vision

On a file served off the source while its rename or an ancestor's is queued, `truncate` without a
handle behaves as it does on any other file. It succeeds, the overlay reports the new length, and
once the queue drains the source holds exactly the overlay view. The size behaviour the previous
cycle fixed is pinned through a `bypass_size` stage, the configuration that makes a file
source-served in production. The sizes old state databases may still hold wrong have an owner,
stated with the facts the probe established.

The profile's `verify` stays fully green, with `test_model` included, under `CI=true` and under
random seeds. CI's four jobs conclude `success`. `AGENTS.md` says truthfully what is closed.

## Acceptance criteria (the contract)

Here, *source-served* is as defined under Problem. *The overlay view* means what the engine reports
through `readdir`, `getattr` and `read`. *The queue drains* means `journal.queued() == []` and
`journal.pending() == []`. Counts are the numbers pytest prints in its summary line, asserted as
numbers (`AGENTS.md` § Verification).

- **R1** — WHEN `truncate` is called with no handle on a source-served file while a `rename` row for
  that file or for one of its ancestors is queued, the engine SHALL NOT raise. Afterwards:
  - `getattr().size` SHALL equal the requested length;
  - reading the file through a fresh handle SHALL return its original bytes cut to that length, or
    padded with zero bytes when the length is larger.

  Each of these SHALL satisfy it:
  - a file over a stage's `bypass_size` with its own rename queued, both shrinking and growing;
  - a file over a stage's `bypass_size` under a directory whose rename is queued;
  - on a one-tier stack, a file adopted under a directory whose rename is queued;
  - on a one-tier stack, a file created under a directory whose rename is queued.

  (These are the Problem table's first four sequences and its `create` one. Today each raises
  `FileNotFoundError`.)
- **R2** — WHEN a source-served file with its own rename, or an ancestor's, queued is truncated
  through a handle and then written, `getattr().size` SHALL equal the byte length the source holds
  while the handle is open. `getattr().size` and the source residency size SHALL both equal that
  length after `fsync`, after `release`, and once the queue drains. A `-m unit` test SHALL show this
  through a stack whose stage has a `bypass_size` the file exceeds. (The behaviour holds at
  `0e6d14c`; this criterion owes the regression test.)
- **R3** — WHEN the queue drains after any sequence named in R1 or R2, the source tree SHALL equal the
  overlay view. That means the same paths, the same kind for each, and the same bytes for each file,
  with nothing left at a pre-rename path. Every size `getattr` and the residency rows report SHALL be
  the file's byte count, and a fresh `OverlayEngine` over the same tier roots SHALL read that same
  view and those sizes.
- **R4** — The sizes that state databases written before `fe26962` may hold wrong SHALL have one
  named owner when this cycle lands, recorded in that owner's seed, which states:
  - which fields can be wrong (`entry.size` and the source residency's `size`, on source-held
    copies only);
  - which self-heal and which do not, per the probe under Problem;
  - that the staged-tier quota counters are unaffected.

  `AGENTS.md` § Known open violations SHALL name that owner and no longer name this seed.
  (Clarifications Q3: the recommended owner is `issues/audit-command.md`.)
- **R5** — WHEN the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`) runs with
  `CI=true` on macOS, `tools/lint.sh` SHALL exit 0. pytest SHALL report 0 failed and 0 errors, with a
  passed count greater than 474: the 474 that pass at `0e6d14c`, plus this cycle's new tests.
- **R6** — WHEN `uv run pytest tests/engine/test_model.py` runs on macOS without `CI` set, from a tree
  with no `.hypothesis/` directory, once for each `--hypothesis-seed` from 1 to 20, every run SHALL
  pass.
- **R7** — WHEN the CI workflow runs on this cycle's final tree, each of `Lint & build`,
  `Unit (Linux, 3.15)`, `Unit (Linux, 3.15t)` and `Unit (macOS, engine-only)` SHALL conclude
  `success`, as read from the run record rather than assumed.

## Non-goals (no-gos)

- **The live stat and `_record`.** The previous cycle closed them. R2 only pins that behaviour
  through a `bypass_size` stage and does not reopen it. The slug keeps "and-stat" because the branch
  froze it.
- **Correcting sizes already wrong in existing state databases** in this cycle: no start-up scan, no
  one-time rewrite, no migration (Clarifications Q3). R4 hands them on.
- **A residency size that tracks an open handle live.** The residency is the accounted size, written
  at `fsync` and `release` by design.
- **A source-served configuration in the stateful model.** `tests/engine/test_model.py` stays
  unchanged (Clarifications Q2).
- **Forcing the queued namespace op before `truncate`** (Clarifications Q1).
- **Non-POSIX sources.** Only `PosixTier` serves a file directly (`open_direct`). An S3 or archive
  source never reaches this path.
- **Other open violations in the same regions.** The `rmdir`-versus-replayed-`mkdir` race, engine
  error mapping, and the shutdown-rescue gaps keep their own seeds.
- **A state-database schema change.** There is no `SCHEMA_VERSION` bump. If the plan finds it cannot
  avoid one, that is a stop-and-ask.
- **The mount-level reproduction and the integration lane.** Coverage is engine-level, under
  `-m unit`.

## Constraints

- **`AGENTS.md` § Invariants § 1, § 5 and § 10 hold as written.** `truncate` sits in the
  high-blast-radius table for metadata before acknowledgment: a dirty transition commits before
  bytes vanish. Under § 5, `_replay_through` is the one place a namespace op jumps its policy clock.
  `_record` resolves and stats a source-held copy under the source lock, so a concurrent replay
  cannot move the object in between (its docstring); a no-handle `truncate` resolves the same place.
  A design that bends any sentence of these sections goes in the plan's deviation table, before
  Geoffrey, and into the same diff's § Invariants text.
- **Same-commit records.** The diff that fixes the defect also updates `AGENTS.md`:
  - § 5's "Reading the source resolves through it too" paragraph, so it covers `truncate`;
  - § Known open violations, where the § 5 row goes, or keeps only what remains open, naming the seed
    that owns it (R4);
  - the measured test counts in § Verification and § Conventions.

  The R4 owner's seed is updated in the same cycle.
- **No edit to `tests/engine/test_model.py`.** Hypothesis's fixed CI draw is a digest of that machine
  class's source, so any edit redraws the CI sample (Clarifications Q2).
- **Human sign-off at review** on the `truncate` change (Clarifications Q4).

## Clarifications

Resolved:

- **Q:** What must the outcome include? — **A:** The profile's `verify` stays fully green: 474+ unit
  tests, `test_model` included, under `CI=true` and under random seeds. CI's four jobs conclude
  `success`, read from the run record (R5–R7). (resolved 2026-11-06, foreman ruling)
- **Q:** Kind and appetite? — **A:** `fix`, `small`, as the seed's frontmatter gives them. Seven
  criteria and one code region, so the size circuit breaker does not trip. (resolved 2026-11-06,
  foreman hand-off)
- **Q:** Where did the defect come from (`escaped`)? — **A:** Recorded as `pre-factory`. The faulty
  line (`overlay.py:713`) blames to `4aea975` (2026-07-02), an ancestor of the factory adoption at
  `6a046b4`. It carries no `spec/`, and the defect reproduces on `141aa86`. `deferred-rename-source-orphan`
  widened its reach to files created under a queued directory rename, but did not ship the faulty
  line. Correct it at the gate if that reading is wrong. (recorded 2026-11-06, shaper)

Resolved at the GOAL gate (2026-11-06, foreman ruling). Every recommendation below is accepted as
written: Q1 is the planner's choice inside R1–R3, with the shaper's lean noted; Q2 keeps
`tests/engine/test_model.py` unchanged; Q3 hands the already-wrong sizes to `issues/audit-command.md`;
Q4 keeps Geoffrey's unconditional sign-off at review, as in the previous cycle (he may drop it):

- **Q1 — `truncate`'s contract: resolve where the source holds the bytes, or force the queued rename
  first?** — accepted. *Recommended: the criteria are written so that either design passes, and the plan chooses.
  The shaper leans toward resolving the place `open` and `_stat_held` already use and keeping the
  rename queued.* Forcing the rename would make an unrelated `truncate` do unbounded namespace work.
  It would also add a caller to `_replay_through`, which § 5 enumerates, and so needs a deviation
  and § 5 text. The previous cycle's GOAL (Q4) recommended the same.
- **Q2 — Does the stateful model gain a source-served configuration (a stage `bypass_size`)?** — accepted.
  *Recommended: no. `tests/engine/test_model.py` stays unchanged.* R1–R3 are deterministic sequences
  that engine-level tests pin directly. Any edit redraws Hypothesis's fixed CI sample. A
  source-served model would also open placement, eviction and promotion interactions well past a
  small appetite. If Geoffrey wants model coverage of source-served files, that should be its own
  seed.
- **Q3 — Sizes already wrong in existing state databases (R4): repair here, hand to the audit, or
  declare unnecessary?** — accepted. *Recommended: hand to `issues/audit-command.md`. It already carries a dated
  note for these sizes, which this cycle corrects with the probe's facts.* It is not unnecessary:
  `entry.size` does not heal on read, remount or flush, and a stale size is what `getattr` reports
  and what placement reads. A repair here is poor value for the cost:
  - there is no migration machinery;
  - a start-up scan would stat every source-held copy;
  - the project is unreleased, so only development databases can hold such sizes.

  The audit already compares each residency against its tier object, which is the same comparison
  this repair needs. The audit note also over-states the risk to the quota counters, and the
  correction says so: only source-held copies were ever mis-sized, and source counters are not
  adjusted from residency sizes.
- **Q4 — Geoffrey's sign-off at review?** — accepted. *Recommended: yes, unconditionally, as last cycle.*
  `truncate` is a high-blast-radius path (metadata before acknowledgment). Its neighbours
  `_stat_held`, `_record` and `getattr` are listed too. Reordering there is, in the table's words,
  undetectable in review. The diff should be small, so the sign-off is cheap. The table's own rule
  only forces it on a confirmed finding. If the gate prefers that default, drop the Constraint's
  last bullet.

## Related materials

- Seed: [`issues/source-path-truncate-and-stat.md`](../../issues/source-path-truncate-and-stat.md)
- Previous cycle (context, not this contract):
  [`spec/deferred-rename-source-orphan/`](../deferred-rename-source-orphan/), its GOAL Q1, Q4, Q5 and
  Q7, and its REVIEW.md F2
- Related seed: [`issues/audit-command.md`](../../issues/audit-command.md), the 2026-11-06 note on
  sizes already wrong
- Contract text: `AGENTS.md` § Invariants § 1, § 5, § 10, both high-blast-radius tables, and
  § Known open violations (the § 5 row)
- Sources, with anchors re-confirmed at `0e6d14c`:
  - `src/sosfs/engine/overlay.py`: `getattr` 277 (live stat 290); `open` 619 (`serve_path` 640-641);
    `_serve` 651 (`src_path` 661); `truncate` 693 (no-handle open 713); `_record` 750 (stat 769);
    `_stat_held` 782-789;
  - `src/sosfs/tier/posix.py:70` (`open_direct`);
  - `tests/engine/test_replay.py` (`assert_converged` 173,
    `test_size_of_a_file_created_on_the_source_inside_renamed_directory` 346);
  - `tests/engine/test_model.py:91` (three tiers, no quota).
- Shaping probes (scratch, not committed): `probe_r1.py`, `probe_r2.py`, `probe_r2c.py`,
  `probe_r4.py`
