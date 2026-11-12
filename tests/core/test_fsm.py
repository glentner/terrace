# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for terrace.core.fsm: the merged FSM/Thread idiom."""

# Type annotations
from __future__ import annotations
from typing import List

# Standard libs
import time
from enum import auto

# External libs
from pytest import mark, raises

# Internal libs
from terrace.core.fsm import State, StateThread


pytestmark = mark.unit


class Counter(StateThread):
    """Counts up to a limit and finishes: the minimal well-formed machine."""

    class states(State):
        START = auto()
        COUNT = auto()
        FINAL = auto()

    def __init__(self, limit: int) -> None:
        super().__init__(name='test-counter')
        self.limit = limit
        self.trail: List[str] = []

    def on_start(self) -> State:
        self.trail.append('start')
        return self.states.COUNT

    def on_count(self) -> State:
        self.trail.append('count')
        return self.states.COUNT if len(self.trail) < self.limit + 1 else self.states.FINAL


class Sleeper(StateThread):
    """Waits forever unless stopped; on_wait exercises the interruptible wait."""

    class states(State):
        START = auto()
        WAIT = auto()
        FINAL = auto()

    def __init__(self) -> None:
        super().__init__(name='test-sleeper')

    def on_start(self) -> State:
        return self.states.WAIT

    def on_wait(self) -> State:
        return self.states.FINAL if self.wait(60.0) else self.states.WAIT


class Faulty(StateThread):
    """Raises from a state; the exception must surface on join/stop."""

    class states(State):
        START = auto()
        FINAL = auto()

    def __init__(self) -> None:
        super().__init__(name='test-faulty')

    def on_start(self) -> State:
        raise RuntimeError('machine exploded')


def test_runs_to_final() -> None:
    """The loop dispatches on_<state> methods until FINAL."""
    machine = Counter(limit=3)
    machine.start()
    machine.join(timeout=5.0)
    assert machine.state is Counter.states.FINAL
    assert machine.trail == ['start', 'count', 'count', 'count']


def test_new_constructs_and_starts() -> None:
    """The `new` convenience starts the thread immediately."""
    machine = Counter.new(limit=1)
    machine.join(timeout=5.0)
    assert machine.state is Counter.states.FINAL


def test_stop_wakes_wait() -> None:
    """stop() interrupts a blocking wait() and joins promptly."""
    machine = Sleeper.new()
    started = time.monotonic()
    machine.stop(timeout=5.0)
    assert time.monotonic() - started < 5.0
    assert not machine.is_alive()
    assert machine.state is Sleeper.states.FINAL


def test_stop_before_start_is_harmless() -> None:
    """A machine never started can still be stopped (no join on an unstarted thread)."""
    machine = Sleeper()
    machine.stop()
    assert machine.halting


def test_exception_reraises_on_join() -> None:
    """An exception inside a state kills the machine and re-raises on join."""
    machine = Faulty.new()
    with raises(RuntimeError, match='machine exploded'):
        machine.join(timeout=5.0)


def test_exception_reraises_on_stop() -> None:
    """stop() joins and therefore re-raises too."""
    machine = Faulty.new()
    time.sleep(0.05)                                        # let it die before stopping
    with raises(RuntimeError, match='machine exploded'):
        machine.stop(timeout=5.0)


def test_missing_states_enum_rejected() -> None:
    """A subclass without a states enum fails at class definition."""
    with raises(TypeError, match='states enum'):
        class NoStates(StateThread):
            pass


def test_missing_required_members_rejected() -> None:
    """The states enum requires START and FINAL."""
    with raises(TypeError, match='FINAL'):
        class NoFinal(StateThread):
            class states(State):
                START = auto()

            def on_start(self) -> State: ...


def test_missing_state_method_rejected() -> None:
    """Every non-FINAL state needs its on_<state> method."""
    with raises(TypeError, match='on_work'):
        class NoMethod(StateThread):
            class states(State):
                START = auto()
                WORK = auto()
                FINAL = auto()

            def on_start(self) -> State: ...
