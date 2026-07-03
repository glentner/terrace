# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""FSM-driven threads: the concurrency idiom for long-running components."""

# Type annotations
from __future__ import annotations
from typing import ClassVar, Optional

# Standard libs
import threading
from enum import Enum

# Internal libs
from sosfs.core.logging import Logger

# Public interface
__all__ = ['State', 'StateThread']

# Initialize logger
log = Logger.with_name(__name__)


class State(Enum):
    """Base for a `StateThread.states` enum; members are declared by each component."""


class StateThread(threading.Thread):
    """
    A finite state machine that runs as its own daemon thread — one class, not a
    machine plus a wrapper thread. Subclasses declare a nested `states` enum with
    at least START and FINAL, and one `on_<state>` method per non-FINAL state,
    each returning the next state:

        class Pump(StateThread):

            class states(State):
                START = auto()
                WORK = auto()
                FINAL = auto()

            def on_start(self) -> State: ...
            def on_work(self) -> State: ...

    The run loop dispatches `on_<state>` until FINAL; the wiring is verified at
    class-definition time. `stop()` requests a halt — state methods observe
    `halting`, or block on `wait()`, which wakes early — and joins the thread.
    An exception raised in any state kills the machine and is re-raised on
    `join`/`stop`, so failures surface to whoever manages the thread.
    """

    states: ClassVar[type[State]]

    def __init_subclass__(cls, **kwargs) -> None:
        """Verify the states enum and its `on_<state>` methods when the class is defined."""
        super().__init_subclass__(**kwargs)
        states = cls.__dict__.get('states', getattr(cls, 'states', None))
        if not (isinstance(states, type) and issubclass(states, State)):
            raise TypeError(f'{cls.__name__} must declare a nested states enum (State subclass)')
        for required in ('START', 'FINAL'):
            if required not in states.__members__:
                raise TypeError(f'{cls.__name__}.states requires a {required} member')
        for member in states:
            if member.name != 'FINAL' and not callable(getattr(cls, f'on_{member.name.lower()}', None)):
                raise TypeError(f'{cls.__name__} must define on_{member.name.lower()} for state {member.name}')

    def __init__(self, name: str) -> None:
        """Initialize in START with no halt requested; `name` labels the thread."""
        super().__init__(name=name, daemon=True)
        self.state = self.states.START
        self.exc: Optional[Exception] = None
        self._halt = threading.Event()

    def run(self) -> None:
        """Dispatch `on_<state>` until FINAL, capturing any exception for `join` to re-raise."""
        try:
            while self.state is not self.states.FINAL:
                state = getattr(self, f'on_{self.state.name.lower()}')()
                log.trace(f'{self.name}: {self.state.name} -> {state.name}')
                self.state = state
        except Exception as exc:
            self.exc = exc
            log.error(f'{self.name} died in {self.state.name}: {exc}')

    @property
    def halting(self) -> bool:
        """Whether a stop has been requested."""
        return self._halt.is_set()

    def wait(self, timeout: float) -> bool:
        """Block up to `timeout` seconds, waking early on `stop()`; returns `halting`."""
        return self._halt.wait(timeout)

    def stop(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Request halt and (by default) join, re-raising anything the machine raised."""
        self._halt.set()
        if wait and self.ident is not None:
            self.join(timeout)

    def join(self, timeout: Optional[float] = None) -> None:
        """Join the thread and re-raise any exception captured from the machine."""
        super().join(timeout)
        if self.exc is not None:
            raise self.exc

    @classmethod
    def new(cls, *args, **kwargs) -> StateThread:
        """Construct and immediately start an instance."""
        thread = cls(*args, **kwargs)
        thread.start()
        return thread
