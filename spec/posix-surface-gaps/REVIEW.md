# REVIEW — Entries created through the mount belong to their creator, and link is refused plainly

> Adversarial QA by `code-review`, run in an isolated/clean context (a subagent; where the runtime
> has none, `code-review`'s sequential fallback). The correctness pass grades the branch diff against
> [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md` § Invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `5565f1b`  ·  **Base:** `main`  ·  **Date:** 2026-11-16
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** fresh, a blind pass over the full spec-excluded diff (`git diff origin/main...HEAD -- .
  ':(exclude)spec/'`; `main` was merged in at `5565f1b`). One fresh reviewer, given `GOAL.md` and the
  rubric by exact path (the only file under `spec/` and the only KB file it could open), `AGENTS.md`
  and the root `PLAN.md` by path. The subagent route ran; the sequential fallback did not. No
  `debate`, no completeness sub-pass. Every R-ID was the reviewer's; none is met by a `spec/` artifact.
- **Contract:** `GOAL.md` carries only its two shaping commits (`b88d270`, `de153ea`), both before the
  plan.

The reviewer's evidence lives in the worktree's gitignored `.local/review1/` (named `R1/` below).

## Verification run

- `tools/lint.sh`: all checks passed (SPDX on 88 files). `uv run pytest -n auto -m unit`: **837
  passed, 9 xfailed** (846), 34.5 s; serial 837 passed, 9 xfailed, 77.6 s (`R1/unit-serial.log`). The
  new and changed test modules, verbose: 18 of 18 passed, none skipped (macFUSE's libfuse loads on this
  host, so `tests/fuse` ran).
- In-process probes of the adapter (a stand-in `fuse_get_context`) and the engine, unprivileged at
  501:20, over 1- and 2-tier stacks, a plain root, a root in a spare group and a setgid root
  (`R1/probe_engine.log`); the same probes against `origin/main` record `0:0`
  (`R1/probe_engine_base.log`).
- Ten planted mutants: nine caught, one survived (F2, `R1/mutants-1.log`).
- `uvx ruff check --select E501 --line-length 120` over `src`, `tests`, `tools` (`R1/e501.log`).
- CI at `5565f1b` (run 43898454126), read by the foreman and the reviewer: all four jobs green.
- **Not run:** a real FUSE mount. Under `tools/temp_site.sh` the daemon logged "Mounted" with libfuse's
  "forking a threaded process is unsafe" warning, but no mount appeared within 30 s and the daemon
  ignored SIGINT; it was killed, nothing left behind. R1, R2 and R4 have no real-mount check, as the
  GOAL expects. The integration suite: no MinIO or Linux FUSE on the host.

## Requirement → evidence matrix

| R | Verdict | Verified how | Who |
|---|---|---|---|
| R1 | ✅ | `tests/fuse/test_surface.py` passes; the adapter probe returns the caller's uid for a created file and directory; mutants dropping the caller's identity from `create` or `mkdir` are caught; `origin/main` records `0:0` | reviewer |
| R2 | ✅ | Under a setgid directory, entries two levels deep take its group whatever the caller's gid; a setgid source root gives every new entry its group, and the source agrees after flush; the mutant without the group rule is caught by 2 tests | reviewer |
| R3 | ✅ (F2: coverage) | Catalog and source uid:gid agree after flush and after `commit_upstream` ancestor placement in every stack and root the probe drove; `origin/main` fails the same probe (catalog `0:0`, source `501:20`) | reviewer |
| R4 | ✅ | `link` returns `EPERM` for six call shapes; it is registered with libfuse; a mutant returning `EROFS` is caught; README names `EPERM` | reviewer |
| R5 | ✅ | Root `PLAN.md` gains one sentence under `### FUSE adapter` and nothing else; `mount.py` mounts without `default_permissions` | reviewer |
| R6 | ✅ | The open-violations row is gone; "two" matches the two remaining rows of `60c44dd`; every figure equals the tree (846 unit, 865 total, 19 integration, 54 test files, 432 engine tests, 88 `.py` files) | reviewer |
| R7 | ✅ | Directories under a setgid parent record the bit at every depth, files do not; the queued mkdir row carries it; committed source directories have it; the mutant without it is caught by 3 tests | reviewer |

## Findings

### [HIGH/CONFIRMED] Two added source lines exceed the 120-character limit
- **Where:** `src/terrace/engine/overlay.py:410` (122 characters, in `create`) and
  `src/terrace/tier/s3.py:214` (121 characters).
- **Failure scenario:** none at run time; the diff breaks `AGENTS.md` § Code style ("120-character
  lines"), which § Invariants grades as a § Conventions violation, HIGH. `tools/lint.sh` does not check
  line length, so no gate caught it.
- **Evidence:** `uvx ruff check --select E501 --line-length 120` at `5565f1b` reports these two new
  lines; the same check on `origin/main`'s `src/` reports only the pre-existing `archive.py:175`
  (`R1/e501.log`). Four other over-length lines in the tree predate this diff.
- **Touches invariant / requirement:** `AGENTS.md` § Code style; a high-blast-radius function
  (`engine/overlay.py` `create`).
- **Property to route:** no line the cycle adds or changes exceeds 120 characters.

### [LOW/CONFIRMED] A surviving mutant: ancestor placement's owner is untested
- **Where:** `src/terrace/engine/overlay.py:1343`, `_place_parents` — the `self.source.mkdir(...,
  uid=entry.uid, gid=entry.gid)` call.
- **Failure scenario:** with `uid=`/`gid=` removed from that call, every test in `tests/engine`,
  `tests/fuse` and `tests/tier` (609) still passes, yet R3 breaks: on a BSD-semantics source whose root
  group differs from the process gid, mkdir `/p`, mkdir `/p/q`, create `/p/q/r.dat`, then
  `commit_upstream` before the mkdir rows replay places `/p` and `/p/q` as `501:12` on the source while
  the catalog records `501:20`, still so after flush. The shipped code passes the same probe.
- **Evidence:** `R1/mutants-1.log`, `R1/probe_engine_place_parents_mutant.log`, `R1/probe_engine.log`.
  The suite's `test_committed_entries_match_catalog` only drives roots whose group is the process gid,
  where the outcome is the same with or without the chown; `test_explicit_group_reaches_the_source`
  covers the queued-mkdir path and `create` onto the source, not ancestor placement.
- **Touches invariant / requirement:** R3; § 5 (the catalog is the authority for uid and gid); a
  high-blast-radius function (`_place_parents`).
- **Property to route:** every site where the engine creates an entry on a tier with an owner (each
  `source.mkdir`, `dest.put` and `materialize_dirs` reach the diff added or changed) is pinned by a test
  run under a source root whose group differs from the process gid, so dropping the owner at any one of
  them fails a test; a mutant sweep over those sites records each mutant and the test that kills it.

## Human-gate triggers

- **Both findings sit in high-blast-radius functions** (`engine/overlay.py` `create`, F1;
  `_place_parents`, F2; `AGENTS.md` § Invariants → High-blast-radius paths). Neither is a correctness
  defect of the shipped behaviour. The gate holds until the maintainer's sign-off is recorded here.
- Also touched, reviewed closely and clean: `_execute_namespace_locked` (the mkdir row's
  `source.mkdir` passes the owner), `tier/posix.py` `mkdir`, `tier/s3.py` and `tier/archive.py`
  `mkdir` signatures. § 4, § 5, § 9 and § 11 hold.

## Out of scope, reproduced and not graded

- The real-mount attempt above: a threaded process forking under macFUSE; not this diff's.
