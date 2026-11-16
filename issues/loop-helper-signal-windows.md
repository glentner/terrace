---
status: unshaped
kind: test
appetite: small
lane: public
---

# A signal in the helper's setup or restore window can leave its handling installed

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs are negotiated with a human.

## Problem

The bundle loop's helper (`tools/bundle_loop.py`, class `Requests`) records the caller's SIGINT,
SIGTERM and SIGHUP handlers, wakeup fd and signal mask, installs its own, and restores all of them in
`Requests.close()` on every exit path of `main()`. Two windows of a few bytecodes remain where a signal
leaves an in-process caller (today, only the test suite) with the helper's handling:

- **Setup.** A SIGINT that arrives during or just after `signal.set_wakeup_fd(...)` in
  `Requests.__init__`, while the caller's `KeyboardInterrupt` handler is still in force, raises there.
  The `except BaseException` path closes the pipe but leaves the interpreter's wakeup fd registered on
  the closed descriptor number. The next `open()` can reuse that number, and each later signal writes
  its number into the unrelated file: in the review's probe a file holding `abc` became `abc\x02`.
  Before the 2026-11-16 remediation the same injection left an open, unread pipe registered instead.
- **Restore.** A SIGINT that arrives between the SIGINT restore and the SIGHUP restore in
  `Requests.close()` raises out of it, leaving SIGTERM and SIGHUP on the helper's recorder: a SIGTERM
  sent afterwards is recorded and ignored.

Both were reached only by injecting a signal at the exact point (`local-bundle-loop` review cycle 4,
2026-11-16, PLAUSIBLE, LOW); no natural signal hit either. Teardown is complete in both windows: the
store stops and the directory goes.

## Why it was deferred

The windows are a few bytecodes wide and affect only a caller that runs `main()` in its own process;
the helper's contract (its R2: teardown on every exit path) holds. The maintainer chose to land the
loop and seed this (2026-11-16).

## Outcome / vision

However a signal is timed against the helper's setup and restore, an in-process caller gets its own
signal handling back and no descriptor it later opens receives signal bytes.

## Sketch of the acceptance criteria

- **R1** — WHEN a signal is delivered at any point of the helper's signal setup or restore, THEN
  after `main()` returns the caller's handlers, wakeup fd and signal mask SHALL equal what they were
  before it was called, and no descriptor the caller opens afterwards SHALL receive a byte from a
  signal.

## Notes

- Shapes to weigh: block the three signals around setup and restore (`pthread_sigmask`) so neither
  sequence can be interrupted; on the setup error path, restore the previous wakeup fd before closing
  the pipe.
- Related: `spec/local-bundle-loop/REVIEW.md` (cycle 4, N1) and its probes; `tests/loop/test_helper.py`
  `test_main_in_process_gives_the_caller_its_signal_handling_back`.
- Found by: `local-bundle-loop` review cycle 4 (2026-11-16).
