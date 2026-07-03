# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Write-back scheduler: the policy clocks that push dirty data upstream (PLAN.md -> Write-back)."""

# Type annotations
from __future__ import annotations
from typing import Dict, List, Optional

# Standard libs
from enum import auto

# Internal libs
from sosfs.core.logging import Logger
from sosfs.core.fsm import State, StateThread
from sosfs.state.journal import Writeback
from sosfs.tier.spec import OverlaySpec, WritebackSpec
from sosfs.engine.overlay import OverlayEngine

# Public interface
__all__ = ['WritebackScheduler', 'boundary_policies']

# Initialize logger
log = Logger.with_name(__name__)


def boundary_policies(spec: OverlaySpec) -> Dict[str, WritebackSpec]:
    """
    The write-back policy governing each boundary, keyed by the tier receiving
    the commit: the boundary into a tier belongs to its downstream neighbor, so
    a queued row ages on the clocks of the tier it commits *from* — and pure
    namespace ops (queued straight to the source) age on the clock of the
    source's neighbor, crossing the same stage-to-source boundary as data.
    """
    return {spec.tiers[position - 1].name: spec.tiers[position].writeback or WritebackSpec()
            for position in range(1, len(spec.tiers))}


class WritebackScheduler(StateThread):
    """
    Background commit pump: watches the write-back queue and commits each row
    one hop across its boundary when that boundary's policy fires (PLAN.md ->
    Write-back). The cascade emerges from the hop mechanics — a commit into a
    mid-stack tier queues a fresh row against the next boundary, which then
    ages on its own clocks.

    Policy math uses the engine's injectable clock; `poll` is the synchronous
    core (tests, and later drain, call it directly with an explicit `now`), and
    the machine wraps it in a scan/commit/wait loop where only the wait between
    scans (`tick`) is real time.
    """

    class states(State):
        START = auto()
        SCAN = auto()
        COMMIT = auto()
        WAIT = auto()
        FINAL = auto()

    def __init__(self, engine: OverlayEngine, *, tick: float = 1.0) -> None:
        """Initialize over a started `engine`; `tick` is the real-time scan cadence."""
        super().__init__(name='sosfs-writeback')
        self.engine = engine
        self.tick = tick
        self.policies = boundary_policies(engine.spec)
        self._due: List[Writeback] = []

    # -- policy -------------------------------------------------------------------------------------------------

    def deadline(self, row: Writeback) -> float:
        """
        When `row` becomes committable: quiescence expiry (`last_write + delay`)
        bounded by `max_interval` anchored on the last commit (or first dirty
        when the boundary has never committed); passthrough rows are due at once.
        """
        policy = self.policies[row.to_tier]
        if policy.mode == 'passthrough':
            return row.last_write
        due = row.last_write + policy.delay
        if policy.max_interval is not None:
            anchor = row.last_commit if row.last_commit is not None else row.first_dirty
            due = min(due, anchor + policy.max_interval)
        return due

    def due(self, now: float) -> List[Writeback]:
        """Queued rows whose deadline has arrived — namespace ops first (they shape the tree), then data."""
        rows = [row for row in self.engine.journal.queued() if self.deadline(row) <= now]
        return sorted(rows, key=lambda row: (row.op == 'data', row.last_write))

    def poll(self, now: Optional[float] = None) -> int:
        """Commit everything due at `now` (the engine clock by default) one hop; returns rows processed."""
        now = self.engine.clock() if now is None else now
        count = 0
        for row in self.due(now):
            if self.halting:
                break
            self.engine.commit_row(row)
            count += 1
        return count

    # -- machine ------------------------------------------------------------------------------------------------

    def on_start(self) -> State:
        log.debug(f'Write-back scheduler started (boundaries={len(self.policies)}, tick={self.tick}s)')
        return self.states.SCAN

    def on_scan(self) -> State:
        self._due = self.due(self.engine.clock())
        return self.states.COMMIT if self._due else self.states.WAIT

    def on_commit(self) -> State:
        committed = 0
        for row in self._due:
            if self.halting:
                break
            self.engine.commit_row(row)
            committed += 1
        self._due = []
        log.debug(f'Committed {committed} write-back row(s)')
        return self.states.FINAL if self.halting else self.states.SCAN   # rescan so cascades drain

    def on_wait(self) -> State:
        return self.states.FINAL if self.wait(self.tick) else self.states.SCAN
