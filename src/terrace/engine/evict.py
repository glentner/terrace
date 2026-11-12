# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Eviction manager: LRU reclaim between per-tier watermarks (PLAN.md -> Eviction)."""

# Type annotations
from __future__ import annotations
from typing import List, NamedTuple, Optional

# Standard libs
from enum import auto

# Internal libs
from terrace.core.logging import Logger
from terrace.core.fsm import State, StateThread
from terrace.tier.spec import TierSpec
from terrace.engine.overlay import OverlayEngine

# Public interface
__all__ = ['EvictionManager', 'Thresholds', 'DEFAULT_HIGH', 'DEFAULT_LOW']

# Initialize logger
log = Logger.with_name(__name__)


DEFAULT_HIGH = 0.95
DEFAULT_LOW = 0.85

# A batch large enough that the scan rarely re-queries, small enough to bound each pass.
SCAN_BATCH = 128


class Thresholds(NamedTuple):
    """Reclaim bounds for one tier's quota; None where that dimension is unbounded."""

    high_bytes: Optional[float]
    low_bytes: Optional[float]
    high_files: Optional[float]
    low_files: Optional[float]


class EvictionManager(StateThread):
    """
    Background reclaim pump: watches each staging tier's usage against its quota
    and, once the high watermark is crossed, drops least-recently-used copies
    until usage falls back to the low watermark (PLAN.md -> Eviction). The
    hysteresis band between the watermarks is what keeps a busy tier from
    thrashing on every put.

    Clean copies drop first — their data already lives upstream, so losing them
    loses nothing. Only when clean copies run out does reclaim force early
    write-back: a dirty copy is committed to the source (so the newest bytes are
    safe) before it is dropped, and it is never dropped while still dirty. Files
    over a tier's `bypass_size` never stage into it (`OverlayEngine.eligible_tier`
    enforces that at admission), so they never appear here to reclaim.

    Eviction is triggered by usage, not by a clock, so there is no policy time to
    inject; `poll` is the synchronous core (tests call it directly) and the
    machine wraps it in a scan/evict/wait loop where only the wait is real time.
    """

    class states(State):
        START = auto()
        SCAN = auto()
        EVICT = auto()
        WAIT = auto()
        FINAL = auto()

    def __init__(self, engine: OverlayEngine, *, tick: float = 1.0,
                 high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW) -> None:
        """Initialize over a started `engine`; `tick` is the real-time scan cadence."""
        if not 0.0 < low < high <= 1.0:
            raise ValueError(f'Watermarks must satisfy 0 < low < high <= 1 (got low={low}, high={high})')
        super().__init__(name='terrace-evict')
        self.engine = engine
        self.tick = tick
        self.high = high
        self.low = low
        self._pressured: List[TierSpec] = []

    # -- policy -------------------------------------------------------------------------------------------------

    def targets(self) -> List[TierSpec]:
        """Staging tiers with a quota to enforce, fastest first (relieve hot tiers soonest)."""
        return [spec for spec in reversed(self.engine.spec.tiers[1:])
                if spec.quota.size is not None or spec.quota.files is not None]

    def thresholds(self, spec: TierSpec) -> Thresholds:
        """The high/low byte and file bounds derived from `spec`'s quota."""
        quota = spec.quota
        return Thresholds(
            high_bytes=None if quota.size is None else quota.size * self.high,
            low_bytes=None if quota.size is None else quota.size * self.low,
            high_files=None if quota.files is None else quota.files * self.high,
            low_files=None if quota.files is None else quota.files * self.low)

    def above_high(self, spec: TierSpec) -> bool:
        """Whether `spec` has crossed a high watermark and needs a reclaim pass."""
        bound, usage = self.thresholds(spec), self.engine.registry.usage(spec.name)
        return ((bound.high_bytes is not None and usage.bytes > bound.high_bytes) or
                (bound.high_files is not None and usage.files > bound.high_files))

    def above_low(self, spec: TierSpec) -> bool:
        """Whether `spec` is still above a low watermark — reclaim continues while true."""
        bound, usage = self.thresholds(spec), self.engine.registry.usage(spec.name)
        return ((bound.low_bytes is not None and usage.bytes > bound.low_bytes) or
                (bound.low_files is not None and usage.files > bound.low_files))

    # -- reclaim ------------------------------------------------------------------------------------------------

    def reclaim(self, spec: TierSpec) -> int:
        """
        Drop LRU copies from `spec` down to the low watermark; returns the count.
        Clean copies go first (free bytes at no cost); only if the tier is still
        pressured does it commit dirty copies upstream and then drop them.
        """
        dropped = self._drop_clean(spec)
        dropped += self._drop_dirty(spec)
        return dropped

    def _drop_clean(self, spec: TierSpec) -> int:
        """Drop least-recently-used clean copies until at the low watermark or none remain."""
        dropped = 0
        while self.above_low(spec) and not self.halting:
            progress = False
            for path, _ in self.engine.catalog.least_recent(spec.name, state='clean', limit=SCAN_BATCH):
                if self.halting or not self.above_low(spec):
                    break
                if self.engine.evict_copy(path, spec.name):
                    dropped += 1
                    progress = True
            if not progress:
                break                                       # remaining clean copies are pinned (open handles)
        return dropped

    def _drop_dirty(self, spec: TierSpec) -> int:
        """
        Force early write-back on LRU dirty copies, then drop them — the last
        resort when clean copies cannot free enough. Committing to the source
        first makes the newest bytes durable, so the drop never loses data.
        """
        dropped = 0
        while self.above_low(spec) and not self.halting:
            candidates = self.engine.catalog.least_recent(spec.name, state='dirty', limit=SCAN_BATCH)
            if not candidates:
                break
            progress = False
            for path, _ in candidates:
                if self.halting or not self.above_low(spec):
                    break
                self.engine.commit_upstream(path)           # dirty -> clean, bytes safe on the source
                if self.engine.evict_copy(path, spec.name):
                    dropped += 1
                    progress = True
            if not progress:
                break                                       # remaining dirty copies are pinned open
        return dropped

    def poll(self) -> int:
        """Run one reclaim pass over every pressured tier; returns copies evicted."""
        count = 0
        for spec in self.targets():
            if self.halting:
                break
            if self.above_high(spec):
                count += self.reclaim(spec)
        return count

    # -- machine ------------------------------------------------------------------------------------------------

    def on_start(self) -> State:
        log.debug(f'Eviction manager started (watermarks={self.high:.0%}/{self.low:.0%}, tick={self.tick}s)')
        return self.states.SCAN

    def on_scan(self) -> State:
        self._pressured = [spec for spec in self.targets() if self.above_high(spec)]
        return self.states.EVICT if self._pressured else self.states.WAIT

    def on_evict(self) -> State:
        evicted = 0
        for spec in self._pressured:
            if self.halting:
                break
            evicted += self.reclaim(spec)
        self._pressured = []
        if evicted:
            log.debug(f'Evicted {evicted} cop{"y" if evicted == 1 else "ies"}')
        if self.halting:
            return self.states.FINAL
        return self.states.SCAN if evicted else self.states.WAIT   # rescan cascades; back off when stuck

    def on_wait(self) -> State:
        return self.states.FINAL if self.wait(self.tick) else self.states.SCAN
