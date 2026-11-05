---
status: unshaped
kind: feature
appetite: small
lane: public
---

# Benchmark harness and honest overhead numbers

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

Nothing measures this filesystem. `PLAN.md` § *Risks and mitigations* names the obligation: "Python
FUSE throughput — this is a reference implementation, not a kernel bypass; mitigate with passthrough
file handles for resident-copy I/O, free-threading, and a benchmark harness … to keep honest numbers
in the docs." The mitigation exists in code and the number does not. `PosixTier.open_direct`
(`src/sosfs/tier/posix.py:70`) hands the engine a real OS file descriptor, and `OverlayEngine.read`
and `write` (`src/sosfs/engine/overlay.py:639-654`) are `os.pread` and `os.pwrite` on it — the design
that makes a defensible overhead ratio possible. Until someone measures it, the claim that the
overlay adds a tolerable fraction to direct tier access is an assertion in a risks table.

The measurement is well defined: overlay throughput and latency against direct access to the same
tier root, so engine plus FUSE overhead is what is left over. Four hazards make it easy to publish a
number that is wrong in the flattering direction.

- **The suite's fixtures are not a benchmark substrate.** `build_spec` (`tests/engine/conftest.py:45`)
  and the `engine` fixture build tmpdir tiers sized for correctness, and the whole 449-test unit suite
  runs in 4.84 s serial. A harness that reuses them measures a few kilobytes on whatever tmpfs the
  developer's laptop provides.
- **Promotion is synchronous and holds the path lock.** `AGENTS.md` § *Invariants* § 11 states
  it: a promote-on-open of a large file holds the lock for the whole copy, which is intended. So cold
  `open` latency *is* a whole-file copy, and a latency table that does not separate cold from warm
  reports the copy as per-call overhead.
- **Commits and rescues are serial.** § 8 of the same file: the rescue budget is polled between
  files and transfers are serial. A "commit throughput" figure is a single-stream figure and has to be
  labelled as one, or a reader will scale it by core count.
- **Archive reads are not ranged.** `ArchiveTier.read` (`src/sosfs/tier/archive.py:486-501`) extracts
  the whole member and then slices, and `S3InnerLocation.open_read` (`archive.py:151`) buffers the
  entire object into a `BytesIO`, re-opened per member read. The `Tier` protocol is range-ready; for
  archives that is a promise about the signature, not about the cost. Do not write a criterion that
  assumes a ranged archive read is cheap.

`PLAN.md` also attributes this work to the wrong phase twice — § *Risks and mitigations* says
"benchmark harness (Phase 10)" in one bullet and "the Phase 10 stress suite" in another, where the
checklist that drove the project put both in Phase 11. That is a documentation inaccuracy owned by
[`issues/plan-md-reconciliation.md`](plan-md-reconciliation.md); this cycle should not propagate it
into the docs it writes numbers for.

This is unbuilt work. Nothing regressed and nothing is broken.

## Why it was deferred

Measuring a moving target wastes the measurement. Every cycle above this one in
[`ROADMAP.md`](../ROADMAP.md) changes the engine, the tier stack or the commit path, and a number
published against a system that then changes has to be taken down or re-measured. The harness also
wants a system believed correct: the honest reading of a fast overlay that loses a queued rename is
that it is fast at the wrong thing.

## Outcome / vision

The docs carry a table a skeptical reader can argue with: overlay versus direct, per operation class,
with the host, the interpreter build, the tier types and the date. The ratio for warm sequential I/O
through a passthrough handle is small and stated; the cold-open cost is stated separately and
explained as a whole-file promotion rather than hidden inside an average. Re-running the harness after
a change is one command, and run-to-run variance is on the page so nobody reads noise as a regression.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes.

- **R1** — WHEN the harness runs against a two-tier POSIX stack, it SHALL emit a table of sequential
  read, sequential write and metadata-operation rates for the overlay and for direct access to the
  same tier root, with the ratio between them.
- **R2** — THE harness SHALL record alongside every number: the interpreter build
  (`sys._is_gil_enabled()`), the tier types and roles, the file sizes, whether the measurement went
  through a real FUSE mount or the engine API directly, and whether the file was already resident.
- **R3** — THE harness SHALL report cold-open latency and warm-open latency as separate figures, so a
  whole-file promotion is never reported as per-call overhead.
- **R4** — WHEN the same configuration runs twice on one host, THE harness SHALL report run-to-run
  variance so a reader can distinguish signal from noise.
- **R5** — THE harness SHALL NOT be collected by `-m unit`: `uv run pytest -m unit -q --collect-only`
  SHALL report the same test total after this cycle as before it.
- **R6** — THE published numbers SHALL name the host, the interpreter build and the measurement date,
  and SHALL state which figures came from a real mount. Mount figures need Linux with `/dev/fuse`;
  CI runs no integration job, so on any other machine they are taken on trust.

## Notes

- Related: [`issues/documentation-and-v0.1.0.md`](documentation-and-v0.1.0.md) publishes the numbers
  and is the only consumer; [`issues/ci-integration-lane.md`](ci-integration-lane.md) provides the
  only automated machine that could ever run a mounted benchmark;
  [`issues/freethreaded-concurrency-stress.md`](freethreaded-concurrency-stress.md) needs the same
  mixed-workload driver and should share it; [`issues/plan-md-reconciliation.md`](plan-md-reconciliation.md)
  owns the Phase-10-versus-11 mislabel; [`issues/archive-spec-plumbing.md`](archive-spec-plumbing.md)
  and [`issues/archive-finalization-integration.md`](archive-finalization-integration.md) decide
  whether an archive stack is benchmarkable at all. Root `PLAN.md` → § *Risks and mitigations*,
  § *Technology decisions* → staging granularity (`bypass_size`, the other path worth measuring).
- Open question — **real mount or engine API.** A mounted benchmark is the honest one and is
  Linux-only, which excludes the maintainer's macOS development loop and every CI job that exists
  today. An engine-API benchmark is portable and omits the FUSE round trip, which is most of the
  overhead being claimed. Publishing engine-only figures under the word "overhead" is the dishonest
  option; publishing both, labelled, is more work and more explaining.
- Open question — **where it lives and when it runs.** A script under a `benchmarks/` directory
  outside `testpaths` keeps it clear of `-m unit`; a pytest module behind a third marker inherits the
  correctness-sized fixtures named above. Either way, does it run automatically anywhere, or by hand
  at release time?
- Open question — **the workload.** Which file sizes, and does the harness deliberately cross
  `bypass_size` so the pass-through path is measured as well as the staged one?
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the remaining-work
  review found nothing built, and the tier and verification-substrate reviews supplied the measurement
  hazards.
