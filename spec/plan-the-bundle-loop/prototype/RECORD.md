# Prototype record — plan-the-bundle-loop

The two prototypes of the plan step, run again on the local loop's helper as it landed on `main`
(GOAL R8 and R9). The plan step ran them on a copy of the helper taken from its in-review branch;
this record states that run and this one, and where they differ. The decisions of the cycle plan
§ 2.3 were signed on the plan step's run.

Helper commit: a0c88f1

`a0c88f1` is the commit of `main` that landed the helper (pull request 3). It holds
`tools/bundle_loop.py` at blob `f0c20ee`, `tests/loop/tap.py` at blob `a615c88` and
`tests/loop/__init__.py` at blob `000c531`; none of the three changed on `main` up to `f76bb02`,
which the cycle branch merged before this run. The plan step's copy was commit `ae420d1` of branch
`test/local-bundle-loop` (`tools/bundle_loop.py` blob `e6d18b4`, `tests/loop/tap.py` blob `e1c91b3`).

## What was run

On 2026-11-16, from the cycle worktree, on a laptop (macOS, local filesystem, Python 3.15.2), with
the helper's `Store` (moto, in process) as the source and the helper's `Tap` in front of it counting
requests on the wire:

```
uv run python spec/plan-the-bundle-loop/prototype/rebuild.py     # 6 s
uv run python spec/plan-the-bundle-loop/prototype/shared.py      # 2 min 25 s
```

`rebuild.py` printed `RESULT trees_equal=yes runs=18` and `shared.py` printed
`RESULT oracle_bytes=yes processes=504`; both exited 0. The figures below are from that run, before
this file existed. The phase's gate ran both again on the same code and read the same two lines.
The runs' full output stayed in the build's scratch directory, as the plan step's did.

## What was changed for the landed helper

One thing. As landed, `tests/loop/tap.py` is a module of the `tests.loop` package and imports the
helper through it (`from tests.loop import bundle_loop`), for the tap's server to bind as the
store's does. The plan step's copies put `tools/` and `tests/loop/` on the import path and imported
`bundle_loop` and `tap` as top-level modules; against the landed tap that stops at the first import
with `ModuleNotFoundError: No module named 'tests'`. Both prototypes now put the repository root on
the path and take the helper from `tests.loop` and the tap from `tests.loop.tap`, as the landed
tests do. The constants that name the helper's directory and the scratch directory, and every line
past the imports, are as the plan step ran them.

The landed helper's other changes are outside what the prototypes call. They use `Store`, `Loop`,
`pack`, `member_bytes` and `Tap`, and never `main`, `run` or the signal handling, so restoring the
caller's handlers and exiting 128+N for a command ended by a signal do not reach them. `Loop` gained
a `cancelled` argument with a default, which they do not pass. Binding without a reverse name lookup
(`NamedByAddress`) and serving moto's application under werkzeug's server built by the helper,
instead of moto's `ThreadedMotoServer`, change how the store and the tap start and nothing a request
count reads. One visible effect: werkzeug's access log now reaches standard error, one line per
request, during `shared.py`, which did not quiet that logger as `rebuild.py` does. Standard output,
which carries the results and the `RESULT` line, is unchanged, and the counts come from the tap, not
the log. The prototypes were not changed for it.

## R8 — one bundle rebuilt

Trees equal in names and bytes: yes

In 18 of 18 runs, at 6 and at 300 members, the unpacked tree of the object written back equalled
the oracle's in names and bytes. The runs are the core flow (rename as recorded state, removal in the
database, altered files by name), rename by copy and remove, an altered file under the renamed
directory kept by name and by id, the per-bundle record with the state database deleted before the
rebuild, the staged `.tar` rebuilt under a new name, and five kills after the object write. Modes and
file mtimes: 0 differing in every run. Directory mtimes: 1 differing in every run, the bundle's top
directory, whose mtime the prototype does not model. Ownership was not compared.

Requests made to the source, by kind, per window, identical in all 18 runs:

| Window | read | stat | list | write |
|---|---|---|---|---|
| Pull | 1 | 0 | 0 | 0 |
| The five changes | 0 | 0 | 0 | 0 |
| Rebuild and write-back | 0 | 0 | 0 | 1 |
| Verifying fetch (the check, not the design) | 1 | 0 | 0 | 0 |

The figures the decisions cite, from this run:

| | This run | Plan step |
|---|---|---|
| Staged `.tar` at 300 members, against the altered files | 6,420,480 B, 5,000 B | the same |
| Replay of the five changes, computed, at 6 and 300 members | 5 re-packs, 102,400 B and 30,566,400 B | the same |
| Directory rename, 150 members, as recorded state | 0 B to the tier, 151 rows | the same |
| The same as copy and remove | 2,956,800 B, 150 files, 452 rows; tier 154 files, 9,383,048 B | the same |
| One altered file under it, by name / by id | 152 rows and 1 move / 151 rows | the same |
| Kill between the POSIX rename and the commit, by name | a row naming a missing file; the read fails `ENOENT` | the same |
| The tier's files alone, no record, at 6 members | names unequal: 5 missing, 6 extra | the same |
| The record file, by name, at 6 and 300 members | 913 B and 33,616 B | 914 B and 33,604 B |
| Kills after the object write, around a new-name switch | the view equals the oracle in all four; a second rebuild sends 1 write | the same |
| Kill after a rename over the staged name | the view is wrong | the same |

## R9 — two read-only mounts, one durable tier

Each process read the oracle's bytes: yes

In 252 two-process runs at 6 and at 300 members, 504 of 504 processes read every member of the
bundle with the oracle's bytes. In the kill case the two mounts after the killed one read the
oracle's bytes in 6 of 6 runs, and in the stale case each mount read the bytes of the object it
staged (300 of 300 members each).

Times the bundle's object was fetched from the source (GETs of its key on the tap), per scenario.
The counts are the same at 6 and 300 members unless the row says otherwise.

| Scenario | Runs per size | This run | Plan step |
|---|---|---|---|
| (a) one after the other, both take the signature by HEAD | 10 | 1 in 10 | 1 in 10 |
| (a) the second mount trusts what is staged | 3 | 1 in 3 | 1 in 3 |
| (a) the second mount takes the signature from a list | 3 | 1 in 3 | 1 in 3 |
| (a) no index file; each mount scans the staged `.tar` | 5 | 1 in 5 | 1 in 5 |
| (b) together, temporary name and rename only | 20 | 2 in 20 | 2 in 20 |
| (b) the same, 0.3 s held between fetch and rename | 20 | 2 in 20 | 2 in 20 |
| (b) the second starts 0 to about 2 pull-times after the first | 20 | 6 members: 1 in 17, 2 in 3; 300: 1 in 15, 2 in 5 | 6 members: 1 in 18, 2 in 2; 300: 1 in 15, 2 in 5 |
| (c) together, exclusive-create lock file, 5 s timeout | 20 | 1 in 20 | 1 in 20 |
| (c) the same, 0.3 s held between fetch and rename | 20 | 1 in 20 | 1 in 20 |
| (c) lock timeout 0.1 s, shorter than a 0.5 s pull | 5 | 2 in 5 | 2 in 5 |
| Kill after the fetch, before the renames, rename only (300) | 3 | 2 in 3 | 2 in 3 |
| Kill after the fetch, before the renames, 2 s lock (300) | 3 | 2 in 3 | 2 in 3 |
| Stale: the object replaced while a mount holds the old one (300) | 1 | 2 | 2 |

No run wrote to the source or fetched any other key. A mount that takes the signature by HEAD makes
one; one that trusts what is staged makes none, and one that reads it from a list makes one list
request instead. After every two-mount run the shared tier held one staged `.tar`, byte-equal to the
gunzip of the object, one marker, no temporary or lock file, and no file of another kind.

In the kill case the killed mount left the marker and two `.partial` files, and a lock file where it
took one; nothing of it had a staged name. With no lock the next mount staged in 33–34 ms; with the
2 s lock it waited 2.00–2.01 s and staged in 2.04–2.08 s. The third mount made no fetch and staged in
6–7 ms. The plan step measured 33–39 ms, 2.00 s, 2.04–2.08 s and 6–7 ms.

Marker writes, two mounts at an empty directory, 20 runs each: with text that is a function of the
source and a rename into place, both read the marker that remained in 20 of 20 (both wrote in 19,
one in 1); with a writer id and time in the text and a rename, one mount had read a marker that was
then replaced in 19 of 20, and in the twentieth only one mount wrote; with a writer id and an
exclusive hard link, both read the marker that remained in 20 of 20, one writer each time. A mount
given today's overlay marker, or a marker naming another source, refused it with no request.

The member index, medians of 7: scanning the staged `.tar` took 0.19 ms (6 members), 6.3 ms (300)
and 50 ms (a synthetic 5,000); the flat index file loaded in 0.024, 0.27 and 4.1 ms; the SQLite form
took 0.056, 0.062 and 0.067 ms to open and look one member up. Medians of the time to a staged bundle
at 300 members: 39 ms together with rename only, 36 ms with the lock.

## Against the plan step's run

The two runs agree. Every result a decision rests on is the same: the trees equal in 18 of 18 runs,
one read to pull, none for the five changes, one write to rebuild and write back; 504 of 504
processes read the oracle's bytes; and the fetch count of every scenario whose outcome does not
depend on timing.

Where the figures differ, none bears on a decision:

- **Object, spool and record sizes, by a few bytes.** The helper writes each bundle's members at the
  time it runs, and their mtimes are in the tar headers, the gzip stream and the record. The source
  object at 6 members was 5,581 to 5,609 bytes across this run's own cases, and the record file 913
  and 33,616 bytes against 914 and 33,604. The helper's change did not cause this: the cases of this
  one run, on one helper, differ the same way. Decision 4's "about 110 bytes a member" stands.
- **The staggered start at 6 members**, 1 fetch in 17 runs and 2 in 3, against 18 and 2. The second
  mount starts after a random delay, so the count is a draw, and the plan step reported it as "1 or
  2", which this run confirms.
- **The marker with a writer id, written by rename**, unsafe in 19 of 20 runs against 20 of 20. In the
  twentieth only one mount wrote, so the race did not happen. The recommendation it supports, a
  marker whose text is a function of the source alone, is unchanged.

Of the decisions signed at the PLAN/TECH gate (cycle plan § 2.3), this record is the measured
evidence of Decisions 1 to 4, through R8: where a staged bundle lives (the rebuild read only the
state database and the durable tier), how a source catches up (one write), what a rename is, and how
a removal is recorded. R9 is the evidence for the tier model's shared read-only tier, (g) in the
cycle plan § 2.4. Decisions 5 and 6 rest on `research/01`, and Decision 7 on reading; neither
prototype bears on them. Nothing here differs from the run they were signed on in a way that bears
on any of them.
