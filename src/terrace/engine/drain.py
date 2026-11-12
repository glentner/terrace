# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Drain: commit all dirty state to the source in loss-risk order (PLAN.md -> Lifecycle, Shutdown)."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

# Internal libs
from terrace.core.logging import Logger
from terrace.tier.spec import OverlaySpec
from terrace.engine.overlay import OverlayEngine

# Public interface
__all__ = ['drain', 'dirty_copies', 'tier_volatility', 'DrainResult', 'DirtyCopy']

# Initialize logger
log = Logger.with_name(__name__)


def tier_volatility(spec: OverlaySpec) -> Dict[str, Tuple[int, int]]:
    """
    Loss-risk rank per tier — the smaller tuple sorts first (higher risk): a
    node-scope tier (evaporates with the node) before a shared durable one, and
    within a scope the more-downstream (faster, more volatile) tier before the
    slower one upstream. Ranking by scope first keeps volatile-first correct even
    in an unusual stack where a shared tier sits downstream of a node-local one.
    """
    return {tier.name: (0 if tier.scope == 'node' else 1, -position)
            for position, tier in enumerate(spec.tiers)}


class DirtyCopy(NamedTuple):
    """A path's most-volatile dirty holder and its loss-risk rank."""

    path: str
    tier: str
    rank: Tuple[int, int]


class DrainResult(NamedTuple):
    """Outcome of a drain pass."""

    namespace: int               # namespace ops applied to the source
    data: int                    # dirty files committed to the source
    complete: bool               # False when a second signal cut the drain short (downgrade to detach)


def dirty_copies(engine: OverlayEngine) -> List[DirtyCopy]:
    """
    Every dirty file's most-volatile holder, in loss-risk order (most volatile
    first) — the shared worklist for both drain and detach-time rescue. The
    queued data rows are the authoritative dirty set (`mark_dirty` creates one per
    boundary); each row's `from_tier` is the copy that owes the commit. A file
    mid-cascade may show more than one such row, so its most-volatile holder wins
    as the representative that fixes the file's place in the order.
    """
    rank = tier_volatility(engine.spec)
    best: Dict[str, DirtyCopy] = {}
    for row in engine.journal.queued():
        if row.op != 'data':
            continue
        entry = engine.catalog.by_id(row.entry)
        if entry is None or entry.deleted:
            continue                                        # unlink already dropped the copy and its rows
        holder_rank = rank[row.from_tier]
        current = best.get(entry.path)
        if current is None or holder_rank < current.rank:
            best[entry.path] = DirtyCopy(entry.path, row.from_tier, holder_rank)
    return sorted(best.values(), key=lambda copy: copy.rank)


def drain(engine: OverlayEngine, *, cancel: Optional[Callable[[], bool]] = None) -> DrainResult:
    """
    Commit all dirty state to the source and finalize it — the graceful-shutdown
    path behind SIGINT / `terrace umount` (PLAN.md -> Lifecycle, Shutdown). Policy
    clocks are collapsed and re-sync rate limits waived by construction: the
    immediate `commit_upstream` / `commit_row` path honors neither (archive
    re-sync limits arrive with Phase 8 and take the same path).

    Namespace ops go first — they shape the tree that data lands in — then dirty
    files commit, most-volatile holder first and hop by hop toward the source, so
    an interrupted drain has already secured whatever was most at risk. `cancel`,
    polled between units, downgrades an in-progress drain to a detach on a second
    SIGINT: it returns with `complete=False` and leaves the remaining queue for a
    later `terrace commit`.

    The engine must be quiescent (pumps stopped, no new writes; shutdown stops the
    pumps first) so one pass converges: each namespace row it reads starts its own
    replay, where it never waits, so a `complete` pass leaves none of them queued.
    """
    stopped = cancel or (lambda: False)
    namespace = 0
    for row in engine.journal.queued(engine.source.name):
        if row.op == 'data':
            continue                                        # data commits below, in loss-risk order
        if stopped():
            return DrainResult(namespace=namespace, data=0, complete=False)
        engine.commit_row(row)
        namespace += 1
    data = 0
    for copy in dirty_copies(engine):
        if stopped():
            return DrainResult(namespace=namespace, data=data, complete=False)
        engine.commit_upstream(copy.path)
        data += 1
    log.info(f'Drained to source ({namespace} namespace op(s), {data} file(s))')
    return DrainResult(namespace=namespace, data=data, complete=True)
