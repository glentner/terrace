# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for a change that meets a commit: the holder is never recorded clean with bytes its upstream lacks."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, List

# Standard libs
import errno
import os
import threading
import time
from pathlib import Path, PurePosixPath

# External libs
from pytest import mark, raises

# Internal libs
from terrace.engine.overlay import OverlayEngine, Handle
from tests.engine.conftest import Clock, build_spec, seed


pytestmark = mark.unit

PATH = '/data/file.dat'
HOLDER = 'scratch'
UPSTREAM = 'project'
WAIT = 30.0
COPIES = 12


def make_engine(tmp_path: Path, clock: Clock, tiers: int = 2) -> OverlayEngine:
    """A started engine over a seeded source. A commit that does not end fails here instead of hanging."""
    seed(tmp_path / 'project', 'data/file.dat', b'v0')
    engine = OverlayEngine(build_spec(tmp_path, tiers=tiers), clock=clock)
    engine.start()
    real_copy = engine.transfers.copy
    made: List[str] = []

    def copy(path, source, dest, **kwargs):
        made.append(dest.name)
        assert len(made) <= COPIES, 'more copies than any test here needs'
        return real_copy(path, source, dest, **kwargs)

    engine.transfers.copy = copy
    return engine


def dirty(engine: OverlayEngine, data: bytes = b'AAAA') -> Handle:
    """An open handle whose write of `data` has returned, leaving the fastest copy dirty."""
    handle = engine.open(PATH, os.O_RDWR)
    engine.write(handle, data, 0)
    return handle


def commit_row(engine: OverlayEngine) -> None:
    """The scheduler's route: one queued data row."""
    rows = [row for row in engine.journal.queued() if row.op == 'data']
    assert len(rows) == 1
    engine.commit_row(rows[0])


def commit_upstream(engine: OverlayEngine) -> None:
    """The route `fsync`, `flush` and the eviction manager take."""
    engine.commit_upstream(PATH)


ROUTES = [commit_row, commit_upstream]


def stored(tmp_path: Path, tier: str) -> bytes:
    """The bytes a tier's file holds, read off the disk."""
    return (tmp_path / tier / 'data/file.dat').read_bytes()


def data_rows(engine: OverlayEngine, to_tier: str = UPSTREAM) -> list:
    """Queued data rows toward `to_tier`."""
    return [row for row in engine.journal.queued(to_tier) if row.op == 'data']


def assert_truthful(engine: OverlayEngine, tmp_path: Path, holder: str = HOLDER, upstream: str = UPSTREAM) -> None:
    """Clean only when the upstream holds the holder's bytes; otherwise dirty with its data row queued."""
    state = engine.catalog.get_residency(PATH, holder).state
    if stored(tmp_path, holder) != stored(tmp_path, upstream):
        assert state == 'dirty'
    if state == 'dirty':
        assert [row.from_tier for row in data_rows(engine, upstream)] == [holder]
    else:
        assert stored(tmp_path, holder) == stored(tmp_path, upstream)


def assert_reaches_source(engine: OverlayEngine, tmp_path: Path, expected: bytes, holder: str = HOLDER) -> None:
    """With every handle released: one flush leaves the source holding `expected`, nothing queued, and the
    bytes still served once the staged copy is dropped."""
    engine.flush()
    assert stored(tmp_path, holder) == expected
    assert stored(tmp_path, 'project') == expected
    assert [row for row in engine.journal.queued() if row.op == 'data'] == []
    assert engine.evict_copy(PATH, holder) is True
    handle = engine.open(PATH, os.O_RDONLY)
    try:
        assert engine.read(handle, len(expected) + 16, 0) == expected
    finally:
        engine.release(handle)


def after_copy(engine: OverlayEngine, monkeypatch, act: Callable[[], None]) -> List[bool]:
    """Run `act` once, when the first copy has returned and before its hop decides."""
    real_copy = engine.transfers.copy
    fired: List[bool] = []

    def copy(path, source, dest, **kwargs):
        result = real_copy(path, source, dest, **kwargs)
        if not fired:
            fired.append(True)
            act()
        return result

    monkeypatch.setattr(engine.transfers, 'copy', copy)
    return fired


def freeze_stat(engine: OverlayEngine, monkeypatch, tier: str = HOLDER) -> Callable[[], None]:
    """Make `tier` report, for PATH, exactly what it reports now: size and every timestamp. Returns the thaw."""
    real_stat = engine.tiers[tier].stat
    frozen = real_stat(PurePosixPath(PATH))
    state = {'frozen': True}

    def stat(path):
        if state['frozen'] and str(path) == PATH:
            return frozen
        return real_stat(path)

    monkeypatch.setattr(engine.tiers[tier], 'stat', stat)
    return lambda: state.update(frozen=False)


def hold_change(engine: OverlayEngine, monkeypatch, name: str, change: Callable[[], None],
                commit: Callable[[OverlayEngine], None]) -> None:
    """
    A change that has entered the engine and is held before its bytes land (`os.<name>` waits), while
    `commit` runs on this thread; the change is let go after the commit's last stat of the holder and
    has returned before that stat's result is used.
    """
    real_act = getattr(os, name)
    entered, proceed, returned = threading.Event(), threading.Event(), threading.Event()
    errors: List[BaseException] = []

    def act(*args):
        if ident and threading.get_ident() == ident[0] and not entered.is_set():
            entered.set()
            assert proceed.wait(WAIT)
        return real_act(*args)

    def run() -> None:
        ident.append(threading.get_ident())
        try:
            change()
        except BaseException as error:                      # surfaced on the test thread below
            errors.append(error)
        finally:
            returned.set()

    ident: List[int] = []
    monkeypatch.setattr(os, name, act)
    real_stat = engine.tiers[HOLDER].stat
    real_copy = engine.transfers.copy
    copied: List[bool] = []

    def copy(path, source, dest, **kwargs):
        result = real_copy(path, source, dest, **kwargs)
        copied.append(True)
        return result

    def stat(path):
        result = real_stat(path)
        if copied and not proceed.is_set() and str(path) == PATH:
            proceed.set()
            assert returned.wait(WAIT)
        return result

    monkeypatch.setattr(engine.transfers, 'copy', copy)
    monkeypatch.setattr(engine.tiers[HOLDER], 'stat', stat)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(WAIT)
        commit(engine)
        assert proceed.is_set()
    finally:
        proceed.set()
        thread.join(WAIT)
    assert not thread.is_alive()
    assert errors == []


@mark.parametrize('commit', ROUTES)
def test_write_held_past_the_commit_reaches_the_source(tmp_path: Path, clock: Clock, monkeypatch, commit) -> None:
    """A write that found its handle dirty and lands after the commit last looked at the holder is not lost."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        hold_change(engine, monkeypatch, 'pwrite', lambda: engine.write(handle, b'BBBB', 0), commit)
        assert stored(tmp_path, HOLDER) == b'BBBB'
        assert_truthful(engine, tmp_path)
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, b'BBBB')
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
def test_same_size_write_behind_an_unchanged_stat_reaches_the_source(tmp_path: Path, clock: Clock, monkeypatch,
                                                                     commit) -> None:
    """A same-length write after the copy read its range, with the tier reporting the stat it reported before."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        thaw = freeze_stat(engine, monkeypatch)
        fired = after_copy(engine, monkeypatch, lambda: engine.write(handle, b'BBBB', 0))
        commit(engine)
        thaw()
        assert fired
        assert stored(tmp_path, HOLDER) == b'BBBB'
        assert_truthful(engine, tmp_path)
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, b'BBBB')
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
@mark.parametrize('length, expected', [(2, b'AA'), (6, b'AAAA\x00\x00')], ids=['shorter', 'longer'])
def test_truncate_held_past_the_commit_reaches_the_source(tmp_path: Path, clock: Clock, monkeypatch, commit,
                                                          length, expected) -> None:
    """A truncate through a handle, shorter or longer, held past the commit's last look at the holder."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        hold_change(engine, monkeypatch, 'ftruncate', lambda: engine.truncate(PATH, length, handle=handle), commit)
        assert stored(tmp_path, HOLDER) == expected
        assert_truthful(engine, tmp_path)
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, expected)
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
def test_truncate_without_a_handle_from_another_thread(tmp_path: Path, clock: Clock, monkeypatch, commit) -> None:
    """A truncate by path started while the commit is in flight waits its turn; both return and nothing is lost."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        engine.release(handle)
        started = threading.Event()
        errors: List[BaseException] = []

        def cut() -> None:
            started.set()
            try:
                engine.truncate(PATH, 2)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=cut)

        def launch() -> None:
            thread.start()
            assert started.wait(WAIT)
            deadline = time.monotonic() + WAIT
            while engine.locks._locks[PATH][1] < 2:         # the cut is at the path lock this commit holds
                assert time.monotonic() < deadline
                time.sleep(0.001)

        after_copy(engine, monkeypatch, launch)
        commit(engine)
        thread.join(WAIT)
        assert not thread.is_alive() and errors == []
        assert stored(tmp_path, HOLDER) == b'AA'
        assert_truthful(engine, tmp_path)
        assert_reaches_source(engine, tmp_path, b'AA')
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
def test_truncate_without_a_handle_inside_the_hop(tmp_path: Path, clock: Clock, monkeypatch, commit) -> None:
    """A truncate by path on the committing thread (the path lock is re-entrant), after the hop's last stat."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        engine.release(handle)
        real_stat = engine.tiers[HOLDER].stat
        copied = after_copy(engine, monkeypatch, lambda: None)
        fired: List[bool] = []

        def stat(path):
            result = real_stat(path)
            if copied and not fired and str(path) == PATH:
                fired.append(True)
                engine.truncate(PATH, 2)
            return result

        monkeypatch.setattr(engine.tiers[HOLDER], 'stat', stat)
        commit(engine)
        assert fired
        assert stored(tmp_path, HOLDER) == b'AA'
        assert_truthful(engine, tmp_path)
        assert_reaches_source(engine, tmp_path, b'AA')
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
def test_write_then_release_inside_the_hop(tmp_path: Path, clock: Clock, monkeypatch, commit) -> None:
    """The evidence outlives the handle: a write inside the hop whose handle closes before the hop decides."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        thaw = freeze_stat(engine, monkeypatch)

        def write_and_close() -> None:
            engine.write(handle, b'BBBB', 0)
            engine.release(handle)

        after_copy(engine, monkeypatch, write_and_close)
        commit(engine)
        thaw()
        assert_truthful(engine, tmp_path)
        assert_reaches_source(engine, tmp_path, b'BBBB')
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
def test_commit_meeting_no_change_copies_once_and_settles(tmp_path: Path, clock: Clock, monkeypatch, commit) -> None:
    """With handles open, written and unwritten, and no change in the hop: one copy, clean, settled, one call."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        idle = engine.open(PATH, os.O_RDWR)
        real_copy = engine.transfers.copy
        copies: List[str] = []

        def copy(path, source, dest, **kwargs):
            copies.append(dest.name)
            return real_copy(path, source, dest, **kwargs)

        monkeypatch.setattr(engine.transfers, 'copy', copy)
        commit(engine)
        assert copies == [UPSTREAM]
        assert engine.catalog.get_residency(PATH, HOLDER).state == 'clean'
        assert data_rows(engine) == []
        assert stored(tmp_path, UPSTREAM) == b'AAAA'
        engine.release(handle)
        engine.release(idle)
        assert_reaches_source(engine, tmp_path, b'AAAA')
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
def test_write_returned_before_the_copy_reads_is_carried(tmp_path: Path, clock: Clock, monkeypatch, commit) -> None:
    """A write that returns before the copy starts reading is carried by that copy, which records clean."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        real_copy = engine.transfers.copy
        copies: List[str] = []

        def copy(path, source, dest, **kwargs):
            if not copies:
                engine.write(handle, b'BBBB', 0)
            copies.append(dest.name)
            return real_copy(path, source, dest, **kwargs)

        monkeypatch.setattr(engine.transfers, 'copy', copy)
        commit(engine)
        assert copies == [UPSTREAM]
        assert engine.catalog.get_residency(PATH, HOLDER).state == 'clean'
        assert data_rows(engine) == []
        assert stored(tmp_path, UPSTREAM) == b'BBBB'
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, b'BBBB')
    finally:
        engine.stop()


def test_write_on_a_dirty_handle_executes_no_sql(tmp_path: Path, clock: Clock) -> None:
    """Writes on a handle already marked dirty, with no commit since, run no statement on the state database."""
    engine = make_engine(tmp_path, clock)
    try:
        statements: List[str] = []
        engine.db.connection.set_trace_callback(statements.append)
        handle = engine.open(PATH, os.O_RDWR)
        statements.clear()
        engine.write(handle, b'AAAA', 0)
        assert statements                                   # the counter sees the first write's dirty mark
        statements.clear()
        for offset in range(64):
            engine.write(handle, b'BBBB', offset)
        engine.truncate(PATH, 32, handle=handle)
        assert statements == []
        engine.release(handle)
    finally:
        engine.stop()


def test_failed_write_leaves_no_change_in_flight(tmp_path: Path, clock: Clock, monkeypatch) -> None:
    """A write whose bytes never land is not counted in flight, so the next commit still settles."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        real_pwrite = os.pwrite

        def pwrite(*args):
            raise OSError(errno.ENOSPC, 'No space left on device')

        monkeypatch.setattr(os, 'pwrite', pwrite)
        with raises(OSError):
            engine.write(handle, b'BBBB', 0)
        monkeypatch.setattr(os, 'pwrite', real_pwrite)
        commit_row(engine)
        assert engine.catalog.get_residency(PATH, HOLDER).state == 'clean'
        assert data_rows(engine) == []
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, b'AAAA')
    finally:
        engine.stop()


def test_write_inside_a_mid_stack_hop(tmp_path: Path, clock: Clock, monkeypatch) -> None:
    """Three tiers: a write inside the first hop keeps the fastest copy dirty while the hop's bytes move on."""
    engine = make_engine(tmp_path, clock, tiers=3)
    try:
        handle = dirty(engine, b'AAAA')
        assert handle.tier == 'tmp'
        thaw = freeze_stat(engine, monkeypatch, tier='tmp')
        after_copy(engine, monkeypatch, lambda: engine.write(handle, b'BBBB', 0))
        rows = [row for row in engine.journal.queued() if row.op == 'data']
        assert [(row.from_tier, row.to_tier) for row in rows] == [('tmp', 'scratch')]
        engine.commit_row(rows[0])
        thaw()
        assert_truthful(engine, tmp_path, holder='tmp', upstream='scratch')
        assert engine.catalog.get_residency(PATH, 'tmp').state == 'dirty'
        engine.release(handle)
        engine.flush()
        assert stored(tmp_path, 'project') == b'BBBB'
        assert [row for row in engine.journal.queued() if row.op == 'data'] == []
    finally:
        engine.stop()


def test_mid_stack_hop_meeting_no_change_settles(tmp_path: Path, clock: Clock) -> None:
    """Three tiers: a stage-to-stage hop that meets no change records clean in its one call."""
    engine = make_engine(tmp_path, clock, tiers=3)
    try:
        handle = dirty(engine, b'AAAA')
        rows = [row for row in engine.journal.queued() if row.op == 'data']
        assert [(row.from_tier, row.to_tier) for row in rows] == [('tmp', 'scratch')]
        engine.commit_row(rows[0])
        assert engine.catalog.get_residency(PATH, 'tmp').state == 'clean'
        assert data_rows(engine, 'scratch') == []
        assert stored(tmp_path, 'scratch') == b'AAAA'
        engine.release(handle)
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
def test_same_size_write_while_the_copy_is_in_flight(tmp_path: Path, clock: Clock, monkeypatch, commit) -> None:
    """A same-length write after the copy read its range and before the copy returns, behind an unchanged stat."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        thaw = freeze_stat(engine, monkeypatch)
        real_put = engine.tiers[UPSTREAM].put
        fired: List[bool] = []

        def put(path, source, *, meta):
            real_put(path, source, meta=meta)
            if not fired:
                fired.append(True)
                engine.write(handle, b'BBBB', 0)

        monkeypatch.setattr(engine.tiers[UPSTREAM], 'put', put)
        commit(engine)
        thaw()
        assert fired
        assert stored(tmp_path, HOLDER) == b'BBBB'
        assert_truthful(engine, tmp_path)
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, b'BBBB')
    finally:
        engine.stop()


@mark.parametrize('commit', ROUTES)
def test_second_handles_first_write_inside_the_hop(tmp_path: Path, clock: Clock, monkeypatch, commit) -> None:
    """A handle that has never written makes its first write, which marks, inside the hop."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        second = engine.open(PATH, os.O_RDWR)
        thaw = freeze_stat(engine, monkeypatch)
        after_copy(engine, monkeypatch, lambda: engine.write(second, b'BBBB', 0))
        commit(engine)
        thaw()
        assert stored(tmp_path, HOLDER) == b'BBBB'
        assert_truthful(engine, tmp_path)
        engine.release(handle)
        engine.release(second)
        assert_reaches_source(engine, tmp_path, b'BBBB')
    finally:
        engine.stop()


def test_write_admitted_inside_the_hop_and_still_in_flight_at_its_end(tmp_path: Path, clock: Clock,
                                                                      monkeypatch) -> None:
    """A write that enters after the copy and is still inside `os.pwrite` when the commit returns."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        real_pwrite = os.pwrite
        entered, proceed = threading.Event(), threading.Event()
        errors: List[BaseException] = []
        ident: List[int] = []

        def pwrite(*args):
            if ident and threading.get_ident() == ident[0]:
                entered.set()
                assert proceed.wait(WAIT)
            return real_pwrite(*args)

        def run() -> None:
            ident.append(threading.get_ident())
            try:
                engine.write(handle, b'BBBB', 0)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run)

        def launch() -> None:
            thread.start()
            assert entered.wait(WAIT)

        monkeypatch.setattr(os, 'pwrite', pwrite)
        after_copy(engine, monkeypatch, launch)
        try:
            commit_row(engine)
        finally:
            proceed.set()
            thread.join(WAIT)
        assert not thread.is_alive() and errors == []
        assert stored(tmp_path, HOLDER) == b'BBBB'
        assert_truthful(engine, tmp_path)
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, b'BBBB')
    finally:
        engine.stop()


class HeldMutex:
    """Stands in for the handle mutex: the named thread's first acquire waits for `proceed`."""

    def __init__(self, real, entered: threading.Event, proceed: threading.Event) -> None:
        self.real, self.entered, self.proceed = real, entered, proceed
        self.thread = None
        self.done = False

    def __enter__(self):
        if threading.get_ident() == self.thread and not self.done:
            self.done = True
            self.entered.set()
            assert self.proceed.wait(WAIT)
        return self.real.__enter__()

    def __exit__(self, *exc):
        return self.real.__exit__(*exc)


def test_write_held_before_admission_across_a_whole_commit(tmp_path: Path, clock: Clock) -> None:
    """A writer stopped at its first take of the handle mutex while one commit runs start to finish:
    the flag it then reads is the one the commit reset, so it marks before its bytes land."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        entered, proceed = threading.Event(), threading.Event()
        held = HeldMutex(engine._handles_mutex, entered, proceed)
        engine._handles_mutex = held
        errors: List[BaseException] = []

        def run() -> None:
            held.thread = threading.get_ident()
            try:
                engine.write(handle, b'BBBB', 0)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            assert entered.wait(WAIT)
            commit_row(engine)
            assert engine.catalog.get_residency(PATH, HOLDER).state == 'clean'
        finally:
            proceed.set()
            thread.join(WAIT)
        assert not thread.is_alive() and errors == []
        assert stored(tmp_path, HOLDER) == b'BBBB'
        assert_truthful(engine, tmp_path)
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, b'BBBB')
    finally:
        engine.stop()


def test_hop_whose_copy_opens_no_record_stays_dirty(tmp_path: Path, clock: Clock, monkeypatch) -> None:
    """A copy that never reports its first look leaves the hop without a record, which is not evidence of clean."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        real_copy = engine.transfers.copy

        def copy(path, source, dest, **kwargs):
            kwargs.pop('before_read', None)
            return real_copy(path, source, dest, **kwargs)

        monkeypatch.setattr(engine.transfers, 'copy', copy)
        commit_row(engine)
        assert engine.catalog.get_residency(PATH, HOLDER).state == 'dirty'
        assert [row.from_tier for row in data_rows(engine)] == [HOLDER]
        engine.release(handle)
    finally:
        engine.stop()


def test_first_write_is_admitted_under_the_path_lock(tmp_path: Path, clock: Clock) -> None:
    """A handle's first write goes in flight in a hold of the mutex taken under the path lock, so the next
    commit of the path finds it."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = engine.open(PATH, os.O_RDWR)
        real = engine._handles_mutex
        seen: List[tuple] = []

        class Spy:
            def __enter__(self):
                return real.__enter__()

            def __exit__(self, *exc):
                seen.append((handle.changing, PATH in engine.locks._locks))
                return real.__exit__(*exc)

        engine._handles_mutex = Spy()
        engine.write(handle, b'AAAA', 0)
        engine._handles_mutex = real
        assert [locked for changing, locked in seen if changing == 1] == [True]
        engine.release(handle)
    finally:
        engine.stop()


def test_failed_copy_leaves_no_record(tmp_path: Path, clock: Clock, monkeypatch) -> None:
    """A hop whose copy raises removes its record and leaves the holder dirty and queued."""
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        real_put = engine.tiers[UPSTREAM].put

        def put(path, source, *, meta):
            raise OSError(errno.EIO, 'Input/output error')

        monkeypatch.setattr(engine.tiers[UPSTREAM], 'put', put)
        with raises(OSError):
            commit_row(engine)
        monkeypatch.setattr(engine.tiers[UPSTREAM], 'put', real_put)
        assert engine._hops == {}
        assert engine.catalog.get_residency(PATH, HOLDER).state == 'dirty'
        commit_row(engine)
        assert engine.catalog.get_residency(PATH, HOLDER).state == 'clean'
        engine.release(handle)
    finally:
        engine.stop()


def test_writer_that_never_pauses_lets_commit_upstream_return(tmp_path: Path, clock: Clock, monkeypatch) -> None:
    """
    A handle that writes again inside every hop, from a fresh thread, after the hop's last look at the holder and
    before the hop takes its record. The first hop meets the write and resets the handle's flag, so the second
    write marks and waits on the path lock the commit holds: the second hop meets nothing, and the commit returns
    after exactly two copies. The write the commit did not carry stays queued and reaches the source.
    """
    rounds = 6                                              # a commit that admits every offered write copies 7 times
    engine = make_engine(tmp_path, clock)
    try:
        handle = dirty(engine, b'AAAA')
        real_copy, real_stat, real_hold = engine.transfers.copy, engine.tiers[HOLDER].stat, engine.locks.hold
        copies: List[str] = []
        outcomes: List[str] = []
        waiting: Dict[int, threading.Event] = {}            # writer thread -> set when it asks for the path lock
        writers: List[threading.Thread] = []
        errors: List[BaseException] = []
        armed = [True]

        def copy(path, source, dest, **kwargs):
            result = real_copy(path, source, dest, **kwargs)
            copies.append(dest.name)
            return result

        def hold(*paths):
            if threading.get_ident() in waiting:
                waiting[threading.get_ident()].set()
            return real_hold(*paths)

        def place(number: int) -> str:
            """One whole write from a fresh thread: it returns, or it asks for the path lock this thread holds."""
            settled, returned = threading.Event(), threading.Event()

            def run() -> None:
                waiting[threading.get_ident()] = settled
                try:
                    engine.write(handle, bytes([65 + number]) * 4, 0)
                except BaseException as error:              # surfaced on the test thread below
                    errors.append(error)
                finally:
                    returned.set()
                    settled.set()

            thread = threading.Thread(target=run)
            writers.append(thread)
            thread.start()
            assert settled.wait(WAIT)
            return 'admitted' if returned.is_set() else 'waits on the path lock'

        def stat(path):
            result = real_stat(path)
            if armed[0] and str(path) == PATH and len(copies) > len(outcomes) and len(outcomes) < rounds:
                outcomes.append(place(len(outcomes) + 1))   # the hop's last look, before its record
            return result

        monkeypatch.setattr(engine.transfers, 'copy', copy)
        monkeypatch.setattr(engine.tiers[HOLDER], 'stat', stat)
        monkeypatch.setattr(engine.locks, 'hold', hold)
        try:
            commit_upstream(engine)
        finally:
            armed[0] = False
            for thread in writers:
                thread.join(WAIT)
        assert not any(thread.is_alive() for thread in writers) and errors == []
        assert outcomes == ['admitted', 'waits on the path lock']
        assert copies == [UPSTREAM, UPSTREAM]
        assert stored(tmp_path, UPSTREAM) == b'BBBB'
        assert stored(tmp_path, HOLDER) == b'CCCC'
        assert_truthful(engine, tmp_path)
        engine.release(handle)
        assert_reaches_source(engine, tmp_path, b'CCCC')
    finally:
        engine.stop()
