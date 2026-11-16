---
status: unshaped
kind: test
appetite: small
lane: public
---

# The S3 tier tests' moto server looks its own name up as it starts

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs are negotiated with a human.

## Problem

`tests/tier/conftest.py:39` starts the session's S3 server as `ThreadedMotoServer(ip_address='127.0.0.1',
port=0)`. moto builds werkzeug's server in a thread of its own, and `http.server.HTTPServer.server_bind`
names the server by `socket.getfqdn('127.0.0.1')`, a reverse lookup of the loopback address. Two
consequences:

- The start waits as long as the host's resolver takes to answer that lookup. On the macOS CI runner,
  in a helper process started with a changed environment, the lookup did not return within 25 s
  (`local-bundle-loop`, CI run 43765053446 at `94fafe9`, 2026-11-15: both stack dumps show the serving
  thread in `getfqdn` under `server_bind`). The pytest worker's own lookup on that runner returns, which
  is why the tier tests pass there today; nothing makes that hold.
- If the server's construction raises, the serving thread ends before it sets moto's ready event, and
  `ThreadedMotoServer.start()` waits on it forever instead of raising.

`local-bundle-loop` removed both from the servers it starts (the helper's store in `tools/bundle_loop.py`
and the tap in `tests/loop/tap.py`) with `bundle_loop.NamedByAddress`, a mixin whose `server_bind`
names the server by its address, and pins it with `test_no_server_looks_up_its_own_name` in
`tests/loop/test_helper.py`. This fixture is the one loopback server in the suite still built the old
way. Pre-existing on `main`.

## Why it was deferred

`tests/tier/` is outside the bundle loop's contract (its GOAL covers the helper, the scenario and the
documents that describe them), and the fixture passes on every CI leg today.

## Outcome / vision

No server the suite starts waits on a name lookup or can leave its start waiting on a thread that
ended.

## Sketch of the acceptance criteria

- **R1** — WHEN the S3 tier tests start their object store with `socket.getfqdn` failing, the store
  SHALL start and answer a request within a few seconds.

## Notes

- Related: `spec/local-bundle-loop/TECH.md` (P1, the amendment of 2026-11-15); the helper's `Store`,
  which the fixture could use as it is.
- Found by: `local-bundle-loop` P1 remediation, the sweep of loopback servers (2026-11-15).
