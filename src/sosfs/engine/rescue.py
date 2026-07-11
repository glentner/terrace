# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Rescue: hop node-local dirty data to the nearest shared tier under a time budget (PLAN.md -> Lifecycle)."""

# Type annotations
from __future__ import annotations
from typing import Callable, List, NamedTuple, Optional

# Internal libs
from sosfs.core.logging import Logger
from sosfs.engine.overlay import OverlayEngine
from sosfs.engine.drain import dirty_copies, DirtyCopy

# Public interface
__all__ = ['rescue', 'node_dirty', 'RescueResult']

# Initialize logger
log = Logger.with_name(__name__)


class RescueResult(NamedTuple):
    """Outcome of a rescue pass."""

    rescued: int                 # dirty files moved off node-local storage onto a shared durable tier
    stranded: int                # node-local dirty files left behind when the time budget ran out
    complete: bool               # False when the budget cut the rescue short (files still at risk on the node)


def node_dirty(engine: OverlayEngine) -> List[DirtyCopy]:
    """
    Every dirty file whose most-volatile holder sits on a node-scope tier, in
    loss-risk order (most volatile first) — the rescue worklist. A file whose
    newest dirty copy already rests on shared storage survives losing the node
    and is left for `sosfs commit`; only node-local dirty data needs the hop.
    Filters the shared drain/rescue ordering (`dirty_copies`) to the at-risk set.
    """
    scope = {tier.name: tier.scope for tier in engine.spec.tiers}
    return [copy for copy in dirty_copies(engine) if scope[copy.tier] == 'node']


def rescue(engine: OverlayEngine, *, budget: Optional[float] = None,
           clock: Optional[Callable[[], float]] = None) -> RescueResult:
    """
    Rescue node-local dirty data to the nearest shared durable tier so it survives
    losing the node — the minimal move behind `sosfs umount --detach` / SIGTERM
    (PLAN.md -> Lifecycle, Detach). Files are processed in loss-risk order (most
    volatile first), so a `budget` that runs out strands only the least at-risk
    data; `budget=None` (the `--detach` case) rescues everything.

    The budget is polled between files against `clock` (the injectable-clock
    convention; defaults to the engine's) — checked before each file, so a file is
    an indivisible unit and a zero budget rescues nothing (SIGTERM with no grace).
    Each rescued file is left dirty on its new shared holder carrying the
    write-back row a later `sosfs commit` needs; namespace ops and shared-tier
    dirty state stay queued in the durable catalog. The engine must be quiescent
    (pumps stopped, no new writes), as for a drain.
    """
    tick = clock or engine.clock
    worklist = node_dirty(engine)
    start = tick() if budget is not None else 0.0
    rescued = 0
    for copy in worklist:
        if budget is not None and tick() - start >= budget:
            stranded = len(worklist) - rescued
            log.warning(f'Rescue budget exhausted — {stranded} file(s) left on node-local storage')
            return RescueResult(rescued=rescued, stranded=stranded, complete=False)
        engine.commit_to_shared(copy.path)
        rescued += 1
    if rescued:
        log.info(f'Rescued {rescued} file(s) to shared storage')
    return RescueResult(rescued=rescued, stranded=0, complete=True)
