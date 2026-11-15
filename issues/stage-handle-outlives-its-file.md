---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A write through a handle on a stage is lost once a commit from a faster tier replaces that stage's file

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

One entry can have open handles on two tiers at once: a handle opened while the file sat on a slower
stage keeps its fd there, and a later `open` promotes the file to a faster tier and serves from it. A
commit from the faster tier then writes the slower tier's copy through the adapter's atomic `put`,
which is a temp file renamed over the final path (`src/terrace/tier/posix.py`, `put`). The first
handle's fd still names the file that was there before the rename. A write through it returns success,
marks the slower copy dirty and queues it, and lands in a file no path reaches. The next commit copies
the slower tier's *current* file, which does not hold the write, and records the copy clean. No call
raises and the queue ends empty.

Reproduced on a three-tier POSIX stack on tmpdir tiers with no mount (Python 3.15.2, macOS), at
`bf438b4` and on the `racing-write-marked-clean` prototype, with the same result on both:

```
source holds 10 bytes ; the fastest tier has bypass_size = 5
open            -> handle A on the middle tier (the fastest refuses 10 bytes)
truncate(4, A) ; fsync(A)
open            -> handle B, promoted to the fastest tier (4 bytes fit)
write(B, 'BBBB') ; commit_upstream        (the first hop renames a new file over the middle tier's)
write(A, 'XXXX')                          returns 4
release both ; flush
  every residency = clean   queued = 0   fastest = middle = source = 'BBBB'   'XXXX' is nowhere
```

By reading, not run: a reader is exposed too. After the promotion, handle A reads the middle tier's
bytes while the entry's newest bytes are on the faster tier.

The rule the engine relies on and does not state: a handle's fd names its tier's current file for that
entry. `evict_copy` keeps it for deletion (it refuses a tier with an open handle). Nothing keeps it for
replacement.

## Why it was deferred

Found by the adversarial design review of `racing-write-marked-clean` (2026-11-12). That cycle's
contract is a change that lands *while a commit is in flight*; here no call overlaps another. It is
**pre-existing**: present at `bf438b4`, and the cycle's design neither repairs nor widens it.

## Outcome / vision

A write that returns success through any open handle reaches the source, whichever tier the handle is
on and whatever has been promoted or committed since it was opened. Or the engine never has two
handles of one entry on different tiers.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a `write` or a `truncate` through an open handle returns success, its bytes SHALL
  reach the source after every handle is released and one `flush()` returns, whichever tier the handle
  was opened on and whatever promotion or commit of the entry ran while it was open.
- **R2** — The reproduction above SHALL be a test under `-m unit` that fails on the base commit.
- **R3** — A `read` through a handle opened before a promotion SHALL return the entry's newest bytes,
  or the criterion SHALL say plainly that it does not.

## Notes

- Shapes to weigh at promotion: refuse a promotion while a handle is open on a slower tier; serve a
  later `open` from the tier the open handles are on; write a hop's destination in place when it has
  open handles; or re-point the handles after the rename.
- `open` decides the serving tier from `eligible_tier(entry.size)`, so the pair of handles needs the
  entry's size to cross a `bypass_size` or a quota while the first handle is open.
- Related: `spec/racing-write-marked-clean/` (landed) and its `PLAN.md` § 5;
  `issues/freethreaded-concurrency-stress.md` (a promotion that copies over a stage copy written
  meanwhile, after an ancestor's rename); `AGENTS.md` § 7.
- Found by: `racing-write-marked-clean`, plan step, adversarial design review.
