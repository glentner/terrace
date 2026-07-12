AGENTS.md — Simple Overlay Service (`sosfs`)
============================================

This file provides architectural and operational context for agents (and humans) working in
this repository. It is the canonical reference for conventions, structure, and workflow.
Read this first. `CLAUDE.md` is a symlink to this file.

Document map:

* **PLAN.md** — the canonical design: architecture, semantics, technology decisions with
  rationale, testing strategy, risks. When a design question comes up, the answer is there
  or belongs there.
* **ROADMAP.md** — the tactical checklist: phased items, frontmatter progress tracking.
  Implementation resumes from its `current_phase`.
* **GOAL.md** — the original statement of intent; historical, do not edit.


What this project is
--------------------

`sosfs` is a clean reference implementation of managed data tiering for research computing.
A FUSE mount presents one logical namespace over an ordered stack of storage tiers — e.g.,
`/project` (authoritative source) → `/scratch` → `/tmp` → `/dev/shm` — with S3 and TAR/ZIP
archives available as tiers. Files promote toward fast tiers on access, write back toward
the source on policy clocks (quiescence delay, max interval, pass-through), and evict LRU
when per-tier quotas hit. Whole-file granularity in v1; the tier protocol is range-ready.

The differentiating lifecycle: SIGINT/`umount` fully drains to the source; SIGTERM or
`umount --detach` stops immediately after *rescuing* node-local dirty data one hop to the
nearest shared tier; a standalone `sosfs commit` later finalizes the source from any host —
so expensive compute allocations never wait on data movement. SQLite state is authoritative
after any crash (metadata-before-ack + intent journaling); `sosfs audit` reconciles.


Repository structure
--------------------

```
src/sosfs/
├── __init__.py        # SOSFSApp (cmdkit ApplicationGroup), main(), subcommand registry
├── core/              # config (layered TOML), logging (TRACE + rich), platform, units,
│                      #   exceptions — no domain logic here
├── state/             # schema.sql, db.py (WAL, per-thread conns), sql.py (t-string
│                      #   rendering), catalog.py, lifecycle.py (dirty bit + markers)
├── tier/              # protocol.py (the Tier contract), posix.py, s3.py (obstore),
│                      #   archive.py (zipfile/tarfile over an inner location)
├── engine/            # overlay.py (VFS-ops API — the product), writeback.py, evict.py,
│                      #   transfer.py, locks.py  — all FSM/Thread based, mount-free
├── fuse/              # adapter.py (mfusepy Operations), mount.py — thin; lazy imports
└── control/           # unix-socket JSON control channel (server + client)
tests/                 # pytest; strict markers: unit, integration
docs/                  # sphinx + furo
.agents/skills/        # /continue and /release skills (.claude is a symlink to .agents)
```

Layering rule: `fuse/` and CLI commands call `engine/`; `engine/` calls `tier/` and
`state/`; nothing below `engine/` imports above itself. The engine must remain fully
testable without a mount — never let FUSE concerns leak downward.


Key technology decisions
------------------------

Full rationale in PLAN.md → Technology decisions. Load-bearing summary:

* **Python 3.15+** (`requires-python = ">=3.15"`); free-threaded build is a first-class
  target — CI runs 3.15 and 3.15t. Use modern features deliberately: lazy imports (PEP 810)
  for the FUSE/S3 adapters, `frozendict` where immutability clarifies.
* **mfusepy** (threaded, high-level path API) — not pyfuse3, not fusepy.
* **Purpose-built `Tier` protocol** — not fsspec. Keep the surface small; adapters own
  their atomicity (temp+rename for POSIX, atomic PUT for S3).
* **obstore** for S3; **stdlib `zipfile`/`tarfile`** for archives; **stdlib `sqlite3`**
  (WAL, plain SQL in `schema.sql`) for state — no ORM.
* **cmdkit** for CLI/config; **hypothesis** stateful tests against a plain-directory
  oracle; **moto-server** (unit) + **MinIO** (integration) for S3.

Two invariants are sacred (PLAN.md → State store): *metadata before acknowledgment* and
*intent → act → confirm* journaling for every managed-location mutation. Any change that
weakens either is a design change and belongs in PLAN.md first.


Code style
----------

Follows hypershell conventions (the author's house style):

* `src/` layout; 120-character lines; SPDX headers on every module.
* Module template:

```python
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Concise module docstring."""

# Type annotations
from __future__ import annotations
from typing import Protocol

# Standard libs
import os

# External libs
from cmdkit.app import Application

# Internal libs
from sosfs.core.logging import Logger

# Public interface
__all__ = ['Thing']

# Initialize logger
log = Logger.with_name(__name__)
```

* Import groups in that order (annotations, standard, external, internal), blank line
  between groups; explicit `__all__` per module.
* Full type annotations; PEP 604 unions (`str | None`); concise docstrings.
* Classes for state machines, threads, tiers, and cmdkit Applications; functions for
  utilities. Long-running concurrent components are FSM-driven `Thread` subclasses with
  `.stop()` and exception re-raising — no ad-hoc daemon loops.
* Logging through `Logger.with_name(__name__)`; use TRACE for per-operation detail.
* SQL is written as PEP 750 t-strings rendered by `sosfs.state.sql.render` (interpolations
  bind as parameters; `{name:id}` splices whitelisted identifiers) — never hand-assemble
  SQL strings or placeholders. Static DDL lives in `schema.sql`.
* Comments only for constraints the code cannot express.


Development commands
--------------------

```bash
uv sync                                  # install (dev group included by default)
uv run pytest -m unit                    # fast suite — must pass before every commit
docker compose up -d --wait              # MinIO for the S3 integration tests (docker-compose.yml)
uv run pytest -m integration             # mounts, MinIO; Linux + services required
uv run pytest -n auto -m unit            # parallel via pytest-xdist
uv run sosfs --help                      # CLI surface
uv run sosfs mount spec.toml /mnt/space -f   # foreground mount (debug: -d)
uv run sphinx-build docs docs/_build     # docs
uv build                                 # sdist + wheel
```

Testing conventions: engine-first (drive the VFS-ops API against tmpdir tiers — no mount);
all policy logic takes an injectable clock (never `sleep` in policy tests); FUSE and MinIO
tests live behind `@mark.integration`; markers are strict.

Environment note: `rm` is aliased away on this machine — use `del` for untracked removals
and `git rm` for tracked files.


Workflow conventions
--------------------

* All work happens on the **`wip`** branch (create from `main` if missing). Never commit
  to `main` directly — `main` moves only by fast-forward from `wip` via `/release`.
* One ROADMAP.md checklist item ≈ one commit, message prefixed `WIP: `, co-authored with
  the agent's standard attribution line (e.g.,
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`).
* After completing an item: check it off in ROADMAP.md, advance frontmatter
  (`current_phase`, `phases_completed`) only when the phase is fully checked, update
  `last_updated`, include the ROADMAP edit in the same commit.
* **`/continue`** executes the next item (arguments: `status`, `dry run`, `phase X`,
  `next N`, `through N`, `phases X..Y`, `skip review`, `bundle`). **`/release`** ships
  `wip` to `main` (strips `WIP: ` prefixes via scripted rebase, fast-forward merge;
  optional squash/bump/tag/GitHub release).
* **Plan drift**: PLAN.md wins over code and over this file. If implementation surfaces a
  contradiction or a better design, STOP — reconcile PLAN.md (with the user when
  substantive) before writing the code, and update ROADMAP.md items to match.
* Version source of truth: `version` in `pyproject.toml`. SemVer; CHANGELOG.md in
  Keep-a-Changelog format, updated as part of release preparation, not per commit.


Out of scope for v1
-------------------

Do not implement these without an explicit plan change (PLAN.md → non-goals / future work):
multi-client coherence, chunk-level staging, special files and cross-tier hard links,
byte-range lock pass-through, indexed random access into compressed archives, pinning,
Prometheus metrics, pyfuse3 adapter, Windows.
