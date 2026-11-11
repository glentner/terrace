# 01 — Root cause: the replay keeps no record of its stack

Planner, 2026-11-10. All anchors are at `980f998`.

## The mechanism

Two pre-replays call `_replay_through` with `skip={row.id}`:

- an `rmdir` row's holders (`src/sosfs/engine/overlay.py:1110-1118`);
- a rename row's places between, through `_clear_inner_target` (`:1295`).

`_replay_through` walks the queue in order and skips only `skip` (`:1312`). The rows it runs are called
with no skip of their own (`:1314`). So a row in the prefix whose own pre-replay reaches a row already
running runs that row again from the top. Python's recursion limit ends the cycle as `RecursionError`.
No row acts and no intent opens first.

## Where it reaches

Every case below raises `RecursionError` at `980f998`:

- **The GOAL's shape: nested directories.** The queue is `[rmdir /d1/d2, rmdir /d1, unlink /m.dat]`.
  The inner `rmdir` replays toward the unlink, and the outer `rmdir` in its prefix replays back into the
  inner one.
- **Sibling directories, depth 1, interleaved.** `rmdir /a`, `rmdir /c`, then `unlink /y.dat`,
  `unlink /x.dat`. Each directory's replay reaches the other's. This holds from N = 2 sibling chains
  upward. The shaper's map held one chain, so it saw depth 1 pass.
- **Directory renames crossing each other's subtrees.** A rename's inner-target replay reaches
  another's, and that one reaches back (`_clear_inner_target`). It turned up in 38 of 8,000 sequences
  in a directory-heavy random walk.

## A separate cause in the same function (R10)

`_clear_inner_target` reads the rows between once. When one of those moves leaves the target still
inside the object, the newly-between rows never run, and the row raises `EngineError: Cannot move …
into its own subtree`.

At `980f998` the R10 example (`/c/a/a`, `/c/a/d/b`, four renames) does this on the first flush, and a
second flush drains. A user rename does the same in `case38`. The recursion fix does not cause this; it
is a one-shot read.

## The stack-passing design (v2), and why it was not enough

v2 passed the stack down the recursion, skipped stack rows, and made a nested row wait when its needs
met the stack. It drained every shape in the map and the fuzz. It fails in two ways:

- The recursion depth grows with the number of interleaved chains. 400 sibling chains raise
  `RecursionError` again; the review saw it from about 330.
- It keeps the one-shot inner-target read, so the R10 class still raises `EngineError`.

GOAL Q5 and Q6 ruled the response: an iterative replay with no depth bound, and a fixed point over a
rename's places between.
