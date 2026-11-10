# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A pytest plugin that counts the model's queued-rename commits per route, from outside the test module.

Loaded by `route_gate.py` as `-p route_census`, with this directory on PYTHONPATH and ROUTE_CENSUS_OUT naming the
JSON file it writes at the end of the session. It wraps `OverlayEngine._commit_hop` on the class and nothing else;
`tests/engine/test_model.py` is never read, imported or patched by it, so the `OverlayMachine` source, and with it
Hypothesis's fixed CI draw, is exactly the committed one.

A *queued-rename commit* is a call to `_commit_hop` whose destination is the source tier, made while
`journal.queued()` holds a `rename` row for the entry being committed (*own*) or, failing that, for a directory
whose current catalog path is a proper ancestor of the entry's current path (*ancestor*). The state is read when the
call begins, before the hop moves anything. *Lagging* counts the subset whose commit lands off the entry's logical
path: its committed source path, or (with none yet) its target under a renamed parent (`_target`), differs from its
logical path. That is the case a commit at the logical path gets wrong; a rename whose target already equals the
source path, or a directory with no source copy yet, queues a rename without a lag. *Polled* counts the subset made
inside `WritebackScheduler.poll` (the scheduler route's polls, as against the model's direct `commit_row` calls in
`commit_scanned` and `rename_scan_unlink_commit`); *polled_lagging* and *ancestor_lagging* are the intersections the
gate's floors read. Each count is also kept per model rule (`rules`).

The route is the innermost of these frames on the call stack, walking outward from the hop:

- `OverlayEngine.commit_row` (a data row; namespace rows never reach `_commit_hop`) is *scheduler*;
- `OverlayEngine.fsync` is *fsync*;
- any frame in `sosfs/engine/evict.py` (the `EvictionManager`) is *eviction*;
- a frame of the model's `OverlayMachine.evict` rule (its own body, or the rule called from another rule) is
  *eviction*;
- a frame of `OverlayMachine.rewrite_rename_commit` (its `engine_side` closure) is *direct*;
- any other `OverlayMachine` rule or method is *other*, keyed by its name (`commit_entry`, `quiesce_and_flush`,
  `teardown`, ...).

The wrapper reads the catalog and the queue and calls nothing that ticks the engine's clock; it asserts that the
clock reads the same after its reads as before, so the run it measures is the run it would have been.
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import PurePosixPath


ROUTES = ('scheduler', 'eviction', 'fsync', 'direct')
MODEL_SUFFIX = os.path.join('tests', 'engine', 'test_model.py')
OVERLAY_SUFFIX = os.path.join('sosfs', 'engine', 'overlay.py')
EVICT_SUFFIX = os.path.join('sosfs', 'engine', 'evict.py')
WRITEBACK_SUFFIX = os.path.join('sosfs', 'engine', 'writeback.py')
CLASS = 'OverlayMachine.'


def route_of(frame) -> tuple[str, str, bool]:
    """
    The route of a hop whose caller's frame is `frame` (see the module docstring), the outermost model rule on the
    stack (the rule Hypothesis ran), and whether a scheduler poll is on the stack.
    """
    route, rule, polled = None, 'outside-the-model', False
    while frame is not None:
        code = frame.f_code
        filename = code.co_filename
        if filename.endswith(WRITEBACK_SUFFIX) and code.co_name == 'poll':
            polled = True
        if filename.endswith(OVERLAY_SUFFIX) and route is None:
            if code.co_name == 'commit_row':
                route = 'scheduler'
            elif code.co_name == 'fsync':
                route = 'fsync'
        elif filename.endswith(EVICT_SUFFIX) and route is None:
            route = 'eviction'
        elif filename.endswith(MODEL_SUFFIX) and code.co_qualname.startswith(CLASS):
            rule = code.co_qualname[len(CLASS):].split('.', 1)[0]
            if route is None and rule == 'evict':
                route = 'eviction'
            elif route is None and rule == 'rewrite_rename_commit':
                route = 'direct'
        frame = frame.f_back
    return route or f'other:{rule}', rule, polled


class Census:
    """The counts one session accumulates."""

    def __init__(self) -> None:
        self.routes = {route: self.bucket() for route in ROUTES}
        self.other: dict[str, dict[str, int]] = {}
        self.to_source: dict[str, int] = {}          # every hop to the source, queued rename or not, per route
        self.hops = 0
        self.engines = 0

    @staticmethod
    def bucket() -> dict:
        return {'own': 0, 'ancestor': 0, 'lagging': 0, 'polled': 0, 'polled_lagging': 0, 'ancestor_lagging': 0,
                'rules': {}}

    def record(self, engine, path: str, frame) -> None:
        clock = getattr(engine, 'clock', None)
        before = getattr(clock, 'now', None)
        route, rule, polled = route_of(frame)
        key = route if route in ROUTES else 'other'
        self.to_source[key] = self.to_source.get(key, 0) + 1
        entry = engine.catalog.get(path)
        if entry is not None:
            own = ancestor = False
            for row in engine.journal.queued():
                if row.op != 'rename':
                    continue
                if row.entry == entry.id:
                    own = True
                    break
                holder = engine.catalog.by_id(row.entry)
                if holder is not None and holder.kind == 'dir' and holder.path != entry.path \
                        and PurePosixPath(entry.path).is_relative_to(holder.path):
                    ancestor = True
            if own or ancestor:
                bucket = self.routes[route] if route in ROUTES else self.other.setdefault(route, self.bucket())
                bucket['own' if own else 'ancestor'] += 1
                bucket['rules'][rule] = bucket['rules'].get(rule, 0) + 1
                lagging = (entry.source_path or engine._target(entry.path)) != entry.path
                bucket['polled'] += polled
                bucket['lagging'] += lagging
                bucket['polled_lagging'] += polled and lagging
                bucket['ancestor_lagging'] += lagging and not own
        after = getattr(clock, 'now', None)
        if before != after:
            raise AssertionError(f'route census: the engine clock moved during a read ({before} -> {after})')

    def as_json(self) -> dict:
        return {'routes': self.routes, 'other': self.other, 'to_source': self.to_source, 'hops': self.hops,
                'engines': self.engines}


def pytest_configure(config) -> None:
    out = os.environ.get('ROUTE_CENSUS_OUT')
    if not out:
        raise RuntimeError('route census: ROUTE_CENSUS_OUT is not set')
    from sosfs.engine import overlay
    census = Census()
    engine_class = overlay.OverlayEngine
    original_hop = engine_class._commit_hop
    original_start = engine_class.start

    def counted_hop(self, path: str, holder: str, upstream: str, *args, **kwargs) -> None:
        census.hops += 1
        if self.tiers[upstream] is self.source:
            census.record(self, path, sys._getframe(1))
        return original_hop(self, path, holder, upstream, *args, **kwargs)

    def counted_start(self) -> None:
        census.engines += 1
        return original_start(self)

    engine_class._commit_hop = counted_hop
    engine_class.start = counted_start
    config._route_census = (census, out, overlay.__file__)


def pytest_unconfigure(config) -> None:
    state = getattr(config, '_route_census', None)
    if state is None:
        return
    census, out, imported = state
    payload = census.as_json()
    payload['overlay'] = imported
    with open(out, 'w') as stream:
        json.dump(payload, stream, indent=1, sort_keys=True)
