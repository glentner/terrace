---
status: adopted:source-path-truncate-and-stat
kind: fix
appetite: small
lane: public
---

# `truncate` and the live stat resolve a source-served file at its logical path

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

Namespace ops are deferred: `rename` moves the catalog entry when the call returns and queues a row
that moves the source object later. While the row is queued, `entry.source_path` is where the source
physically holds the entry's bytes, and `AGENTS.md` § *Invariants* § 5 makes it the resolution target
for every physical touch of the source. The `deferred-rename-source-orphan` cycle brought the data
commit, the namespace replay and, in its first review's remediation, the stat behind `getattr` and
`_record` under that rule. One reader of a *source-served* file (its open handle points at the
source) still resolves the entry's logical path, which does not exist on the source until the rename
row runs: `truncate` without a handle.

A file is source-served when it is over every stage's `bypass_size`, or when no stage takes it at all
(a stack with no stage, or a `create` straight onto the source). Its bytes sit off its logical path
while its own rename **or an ancestor's** is queued. That cycle widened the second case: a file
created straight onto the source inside a directory whose rename is queued is placed under the
directory's old prefix (`AGENTS.md` § 5, who sets `source_path`), so every such file is in this
seed's reach, not only a `bypass_size` file with a rename of its own.

`open` gets it right. It resolves `serve_path = entry.source_path` when the serving tier is the source
(`src/sosfs/engine/overlay.py:640-641`), and `_serve` passes `src_path` for promotion
(`overlay.py:661`). `getattr`'s live stat (`overlay.py:290`) and `_record` (`overlay.py:769`) stat
through `_stat_held` (`overlay.py:782`), which reads the source at `source_path`. The one that does
not, re-confirmed on the cycle branch after that remediation (2026-11-06):

**`truncate` without a handle raises `ENOENT` on a file that exists.** It opens `_pure(path)` on the
serving tier directly (`overlay.py:713`). Reproduced: `truncate('/big2.bin', 4)` on a 1000-byte
source-served file under a pending rename raises `FileNotFoundError: [Errno 2] No such file or
directory: .../project/big2.bin` (the seed's probe; on the cycle branch the same probe renamed to
`/moved.bin` raises it for `.../project/moved.bin`). On a one-tier stack with `rename /data → /moved`
queued, `truncate('/moved/c.txt', 0)` on a file just created there, and `truncate('/moved/a.txt', 0)`
on one adopted there, both raise it too. POSIX callers read `ENOENT` as "the file vanished".

**The size in the catalog went permanently wrong (closed by that remediation, 2026-11-06).** `_record`
and the live-stat branch of `getattr` statted `_pure(entry.path)`, found nothing, and kept the old
size. Reproduced at `40817e1`: on a source-served file with a queued rename, `truncate(handle=…)` to 0
followed by `write(b'tiny')` left `getattr().size == 1000` for a 4-byte file, while the physical object
was 4 bytes; `residency.size` was 1000 too, and stayed 1000 after `release`, after `flush`, and after
the rename had committed. The same probe now reports 4 while open, after `release` (the residency
too) and after `flush`. The code path is pinned by
`tests/engine/test_replay.py::test_size_of_a_file_created_on_the_source_inside_renamed_directory`
(one tier, no `bypass_size`); no test drives it through a `bypass_size` stage.

Probe, against tmpdir tiers with no mount, using `Clock`, `build_spec` and `seed` from
`tests/engine/conftest.py`:

```python
seed(root / 'project', 'big.bin', b'x' * 1000)
seed(root / 'project', 'big2.bin', b'x' * 1000)
e = OverlayEngine(build_spec(root, tiers=2, scratch_quota=QuotaSpec(bypass_size=100)), clock=Clock())
e.start(); e.readdir('/')
e.rename('/big2.bin', '/moved.bin')
e.truncate('/moved.bin', 4)                     # FileNotFoundError

# a fresh engine over a fresh root, same seeds: the size
e.rename('/big.bin', '/m.bin')
h = e.open('/m.bin', os.O_RDWR)                 # serving tier: project
e.truncate('/m.bin', 0, handle=h); e.write(h, b'tiny', 0)
e.getattr('/m.bin').size                        # was 1000 open, after release, after flush; now 4
```

**Sizes already wrong in existing state databases.** A code fix stops new wrong sizes; it does not
correct the ones a state database already holds. Whether to correct them, and how, interacts with the
residency sizes the quota counters are built from. The natural home is the audit
([`issues/audit-command.md`](audit-command.md)), which reconciles the catalog against the tiers.

## Why it was deferred

Split out of `deferred-rename-source-orphan` at its GOAL gate (Q1, Q5; 2026-11-05). That cycle
already carried twelve criteria; these siblings would add at least two more, pull in a third
high-blast-radius region (`truncate`, metadata-before-ack) and a data-correction question touching the
quota counters. They do not block the profile's `verify`: the stateful model serves nothing off the
source (no `bypass_size`), so it never reaches them. **Pre-existing**: the lines blame to pre-factory
commits and reproduce on `main` at `141aa86` as well as on the cycle branch.

The cycle's first review (2026-11-06, F2) found that its own `create`-onto-the-source placement routed
new files into the live-stat defect. Geoffrey ruled that one fixed in that cycle and the rest kept
here; the fix (`_stat_held`) closed the live-stat half for every source-served file, so what stays is
`truncate` without a handle and the already-wrong sizes.

## Outcome / vision

Every physical touch of a source-served file, `truncate` included, resolves through
`entry.source_path` while a rename is queued, so a truncate succeeds on a file that exists, as the
size the catalog reports already equals the bytes the serving tier holds. Sizes already wrong
in existing state databases have a stated owner and a way to be corrected.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHEN `truncate` is called with no open handle on a source-served file whose entry carries
  a queued `rename`, the engine SHALL truncate the object the overlay serves to the requested length
  and SHALL NOT raise `FileNotFoundError`.
- **R2** — A test SHALL pin, through a `bypass_size` stage, that after a handle-based `truncate` and a
  write on a source-served file with a queued rename, `getattr().size` and the residency size equal
  the byte length the serving tier holds, both while the handle is open and after `release`. (The
  behaviour holds since `deferred-rename-source-orphan`'s first review; only the `bypass_size` pin is
  owed.)
- **R3** — WHEN the queue drains after R1's sequence or R2's, the source tree SHALL equal the overlay
  view, sizes included.
- **R4** — Sizes that existing state databases already hold wrong SHALL have an owner: a correction
  this cycle performs with its own evidence, or a criterion handed to `issues/audit-command.md`.

## Notes

- Open question — **`truncate`'s contract.** The `deferred-rename-source-orphan` GOAL (Q4)
  recommended resolving through the same serve path `open` uses, rather than forcing the queued
  namespace op first. Forcing it would make an unrelated `truncate` do unbounded namespace work, and
  would add a second caller to the one place a namespace op jumps its policy clock
  (`_replay_through`, `AGENTS.md` § 5).
- Open question — **the model.** A source-served configuration (a stage `bypass_size`) in
  `tests/engine/test_model.py` would reach these paths; the cycle that fixes them decides whether the
  model gains one. Weakening the model is forbidden (`AGENTS.md`'s high-blast-radius table).
- Open question — **the correction.** A one-time rewrite of existing residency sizes changes the quota
  counters eviction trusts, and needs its own contract and evidence.
- Related: [`issues/audit-command.md`](audit-command.md) (catalog↔tier reconciliation);
  `AGENTS.md` § *Invariants* § 5 and its row under *Known open violations*;
  `spec/deferred-rename-source-orphan/` (GOAL Q1, Q4, Q5).
- Found by: the invariant audit run during the software-factory port (2026-08-22), carried in
  `issues/deferred-rename-source-orphan.md`; split out by `deferred-rename-source-orphan` P5.
