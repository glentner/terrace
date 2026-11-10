---
status: unshaped
kind: test
appetite: small
lane: public
---

# The model never serves a file from the source

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The stateful model (`tests/engine/test_model.py`) builds a three-tier stack whose stages have no
`bypass_size`, so every `open` promotes a file onto a stage and no file is ever served from the source.
The source-served paths the cycles since 2026-11-08 changed are outside its reach: `open` of a copy held
on the source at its committed source path, the handle-less `truncate` that cuts it there, and the live
stat `getattr` and `_record` read there (`_stat_held`). `tests/engine/test_replay.py` pins each of them
deterministically (`spec/source-path-truncate-and-stat/`, `spec/source-served-open-race/`,
`spec/truncate-recording-after-ancestor-rename/`, `spec/entry-identity-token/`), but the model's random
interleavings, its convergence check after a drain by polls and its remount at teardown never cast over
them.

## Why it was deferred

- 2026-11-06: the `source-path-truncate-and-stat` cycle decided that the model gains no source-served
  configuration and that `tests/engine/test_model.py` stays unchanged (that cycle's GOAL Q2): its
  requirements were deterministic sequences the engine tests pin directly, any edit redraws
  Hypothesis's fixed CI sample, and a source-served model opens placement, eviction and promotion
  interactions well past a small appetite. Model coverage of source-served files, if wanted, stayed
  with the seed for the model's commit-route reach, whose cycle would redraw the sample anyway.
- 2026-11-09: that cycle (`spec/model-commit-route-reach/`, GOAL Q1) ruled it out of its own scope: a
  `bypass_size` stage reaches the source-served `open`, `truncate` and stat paths, a second deliverable
  with its own oracle questions, which a small appetite does not hold. This seed is its home.

## Outcome / vision

The model serves some files from the source, so its parity checks, its invariant and its convergence
checks run over source-served `open`, `truncate` and `getattr`, interleaved with queued renames and
removals.

## Notes

- Open question — **the configuration.** A stage `bypass_size` goes through `dataclasses.replace` on the
  spec `__init__` builds, as `fsync_policy` does; `tests/engine/conftest.py` is an interface
  (`AGENTS.md` § Conventions) and stays unchanged. Whether every stage bypasses, or one, decides which
  promotions the model still reaches.
- Open question — **the oracle.** A plain directory has no notion of where bytes are served from;
  which observable differences (sizes while open, after release, after a remount) the model asserts is
  the shaping conversation's.
- Open question — **the draw.** Any edit to `OverlayMachine` redraws the fixed CI sample, so the cycle
  re-grades on the new draw: the profile's `verify` under `CI=true`, twenty random seeds, and every
  fix-removal mutant the model must fail.
