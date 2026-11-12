# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Mount lifecycle: the engine and its policy pumps under a FUSE session (PLAN.md -> FUSE adapter, Lifecycle)."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Optional

# Standard libs
import os
import sys
import json
import time
import signal
import socket
import threading
import subprocess
from enum import Enum
from pathlib import Path

# Internal libs
from terrace.core.logging import Logger
from terrace.core.exceptions import TerraceError
from terrace.state.db import ExclusiveLock
from terrace.tier.spec import OverlaySpec
from terrace.engine.overlay import OverlayEngine
from terrace.engine.writeback import WritebackScheduler
from terrace.engine.evict import EvictionManager
from terrace.engine.drain import drain
from terrace.engine.rescue import rescue

# Public interface
__all__ = ['Mount', 'Shutdown', 'MountError', 'unmount', 'read_pidfile', 'request_shutdown',
           'PIDFILE_NAME', 'LOCK_NAME']

# Initialize logger
log = Logger.with_name(__name__)


PIDFILE_NAME = 'mount.pid'                         # daemon pidfile, in the overlay state directory
LOCK_NAME = 'lock'                                 # exclusive-lock file held for the mount's lifetime
LOG_NAME = 'mount.log'                             # daemon stdout/stderr, in the state directory


class MountError(TerraceError):
    """A mount could not be established or torn down."""


class Shutdown(Enum):
    """
    How a mount tears down (PLAN.md -> Lifecycle, Shutdown). The engine and the
    drain/rescue moves are identical to the offline paths; only the settled
    `shutdown_state` and whether the rescue runs differ.
    """

    DRAIN = 'drain'          # SIGINT / `umount`: commit everything to the source, settle 'clean'
    DETACH = 'detach'        # SIGTERM / `umount --detach`: rescue node-local dirty data, settle 'detached'
    FORCE = 'force'          # `umount --force` / external unmount: skip the rescue, settle 'detached'


def unmount(mountpoint: str | Path) -> None:
    """
    Unmount a FUSE mount point, breaking any live `FUSE(...)` loop over it. Tries
    the unprivileged `fusermount3`/`fusermount` helpers first, then `umount`
    (macOS and privileged Linux); raises `MountError` only if all are unavailable
    or fail. Shared by the shutdown loop-breaker and `umount --force`.
    """
    target = str(mountpoint)
    last = ''
    for argv in (['fusermount3', '-u', target], ['fusermount', '-u', target], ['umount', target]):
        try:
            result = subprocess.run(argv, capture_output=True, text=True)
        except FileNotFoundError:
            continue                               # helper not installed — try the next
        if result.returncode == 0:
            log.trace(f'Unmounted {target} via {argv[0]}')
            return
        last = result.stderr.strip() or result.stdout.strip()
    raise MountError(f'Failed to unmount {target}: {last or "no unmount helper available"}')


def read_pidfile(state: str | Path) -> Optional[Dict]:
    """The `{pid, host, mountpoint, started}` record of a running mount, or None when absent/unreadable."""
    try:
        text = (Path(state) / PIDFILE_NAME).read_text()
    except FileNotFoundError:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        log.warning(f'Ignoring unparseable pidfile in {state}')
        return None


def _wait_for_unmount(state: str | Path, *, poll: float = 0.2) -> None:
    """Block until the daemon drops its pidfile (its teardown has finished); Ctrl-C stops waiting."""
    try:
        while read_pidfile(state) is not None:
            time.sleep(poll)
    except KeyboardInterrupt:
        log.warning('Stopped waiting for unmount to finish (the daemon is still tearing down)')


def request_shutdown(spec: OverlaySpec, *, detach: bool = False, force: bool = False, wait: bool = True) -> None:
    """
    Ask a running mount daemon to tear down (`terrace umount`, PLAN.md -> Shutdown).
    Default is a drain (SIGINT); `detach` is a detach (SIGTERM, rescue then stop);
    `force` skips even the rescue by unmounting the mount point directly, which
    breaks the daemon's loop into a force teardown. Resolution is via the pidfile
    in the overlay state directory; `wait` blocks until the daemon has finished.
    """
    if detach and force:
        raise MountError('Choose at most one of --detach and --force')
    record = read_pidfile(spec.state)
    if force:
        mountpoint = (record or {}).get('mountpoint')
        if mountpoint is None:
            raise MountError(f'No pidfile for overlay {spec.name!r} in {spec.state}; cannot resolve mount point')
        unmount(mountpoint)
        log.info(f'Force-unmounted {mountpoint}')
    else:
        if record is None:
            raise MountError(f'No running mount for overlay {spec.name!r} (no pidfile in {spec.state})')
        if record.get('host') and record['host'] != socket.gethostname():
            raise MountError(f'Overlay {spec.name!r} is mounted on {record["host"]}, not this host — '
                             f'unmount it there, or finalize with `terrace commit`')
        pid = int(record['pid'])
        sig = signal.SIGTERM if detach else signal.SIGINT
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            raise MountError(f'Mount daemon (pid {pid}) is not running — stale pidfile in {spec.state}') from None
        log.info(f'Requested {"detach" if detach else "drain"} of {spec.name!r} (pid {pid})')
    if wait:
        _wait_for_unmount(spec.state)


class Mount:
    """
    A live overlay: the `OverlayEngine` and its write-back and eviction pumps
    under a FUSE session, owning the exclusive lock, the pidfile, and the
    shutdown orchestration (PLAN.md -> FUSE adapter, Lifecycle).

    The engine and the drain/rescue moves are mount-free and unit-tested
    directly; the FUSE loop and its loop-breaker are injectable (`runner` /
    `breaker`) so the serve orchestration is exercised without libfuse, and the
    real libfuse session is covered by the integration suite. `start`/`shutdown`
    are the testable core; `serve` runs the loop between them.
    """

    def __init__(self, spec: OverlaySpec, mountpoint: str | Path, *, foreground: bool = False,
                 debug: bool = False, clock: Callable[[], float] = time.time,
                 writeback_tick: float = 1.0, evict_tick: float = 5.0,
                 runner: Optional[Callable[[], None]] = None,
                 breaker: Optional[Callable[[], None]] = None) -> None:
        """Wire an unstarted mount over `spec` at `mountpoint`; no I/O until `serve`/`run`."""
        self.spec = spec
        self.mountpoint = Path(mountpoint)
        self.foreground = foreground
        self.debug = debug
        self.engine = OverlayEngine(spec, clock=clock)
        self.lock = ExclusiveLock(Path(spec.state) / LOCK_NAME)
        self.writeback_tick = writeback_tick
        self.evict_tick = evict_tick
        self._runner = runner
        self._breaker = breaker
        self.writeback: Optional[WritebackScheduler] = None
        self.eviction: Optional[EvictionManager] = None
        self._mode: Optional[Shutdown] = None
        self._sigint = 0
        self._stop = threading.Event()
        self._loop_error: Optional[Exception] = None
        self._prev_handlers: Dict = {}

    @property
    def pidfile(self) -> Path:
        """The daemon pidfile path, in the overlay state directory."""
        return Path(self.spec.state) / PIDFILE_NAME

    # -- pidfile ------------------------------------------------------------------------------------------------

    def _write_pidfile(self) -> None:
        """Record this process as the overlay's mount daemon (pid + mount point for `umount`)."""
        record = {'pid': os.getpid(), 'host': socket.gethostname(),
                  'mountpoint': str(self.mountpoint), 'started': time.time()}
        self.pidfile.write_text(json.dumps(record) + '\n')

    def _remove_pidfile(self) -> None:
        """Drop the pidfile; tolerant of an already-removed file."""
        try:
            self.pidfile.unlink()
        except FileNotFoundError:
            pass

    # -- validation ---------------------------------------------------------------------------------------------

    def _validate(self) -> None:
        """The mount point must be an existing directory before libfuse can bind it."""
        if not self.mountpoint.is_dir():
            raise MountError(f'Mount point does not exist or is not a directory: {self.mountpoint}')

    # -- startup ------------------------------------------------------------------------------------------------

    def start(self) -> None:
        """
        Bring the overlay up: take the exclusive lock, start the engine (markers
        verified, `shutdown_state = mounted`), write the pidfile, and start the
        write-back and eviction pumps. Releases the lock if the engine fails to
        start so a failed mount leaves nothing held.
        """
        self._validate()
        self.lock.acquire()
        try:
            self.engine.start()
        except Exception:
            self.lock.release()
            raise
        self._write_pidfile()
        self.writeback = WritebackScheduler(self.engine, tick=self.writeback_tick)
        self.eviction = EvictionManager(self.engine, tick=self.evict_tick)
        self.writeback.start()
        self.eviction.start()
        log.info(f'Mounted {self.spec.name!r} at {self.mountpoint}')

    def _stop_pumps(self) -> None:
        """Stop both policy pumps so the engine is quiescent before a drain (errors logged, not raised)."""
        for pump in (self.writeback, self.eviction):
            if pump is None:
                continue
            try:
                pump.stop()
            except Exception as exc:                # a pump that died mid-run re-raises here; log and press on
                log.error(f'{pump.name} raised during stop: {exc}')
        self.writeback = self.eviction = None

    # -- shutdown orchestration (mount-free, unit-tested) -------------------------------------------------------

    def shutdown(self, mode: Shutdown) -> None:
        """
        Tear the overlay down under `mode`: stop the pumps (making the engine
        quiescent), move dirty data per the mode, settle `shutdown_state`, then
        release the lock and drop the pidfile. A second SIGINT (`_sigint >= 2`)
        cancels an in-progress drain, which downgrades a `clean` settle to
        `detached` — the remaining queue is finalized later by `terrace commit`.
        """
        if not self.engine.started:
            return
        log.info(f'Shutting down {self.spec.name!r} ({mode.value})')
        self._stop_pumps()
        try:
            if mode is Shutdown.DRAIN:
                result = drain(self.engine, cancel=lambda: self._sigint >= 2)
                state = 'clean' if result.complete else 'detached'
            elif mode is Shutdown.DETACH:
                rescue(self.engine, budget=self.spec.drain_timeout, clock=self.engine.clock)
                state = 'detached'
            else:
                state = 'detached'                  # FORCE: skip the rescue; metadata stays consistent
            self.engine.stop(state)
        finally:
            self.lock.release()
            self._remove_pidfile()
        log.info(f'Unmounted {self.spec.name!r} ({mode.value} -> {state})')

    # -- signals ------------------------------------------------------------------------------------------------

    def _on_sigint(self, *_) -> None:
        """SIGINT: drain to the source; a repeat mid-drain downgrades to a detach (PLAN.md -> Shutdown)."""
        self._sigint += 1
        if self._mode is None:
            self._mode = Shutdown.DRAIN
            log.warning('SIGINT — draining to source (send SIGINT again to downgrade to detach)')
        elif self._mode is Shutdown.DRAIN:
            log.warning('SIGINT — downgrading drain to detach')
        self._stop.set()

    def _on_sigterm(self, *_) -> None:
        """SIGTERM: detach now, rescuing node-local dirty data within the `drain_timeout` budget."""
        if self._mode is None:
            self._mode = Shutdown.DETACH
            log.warning('SIGTERM — detaching (rescuing node-local dirty data)')
        self._stop.set()

    def _install_signals(self) -> None:
        """Take over SIGINT/SIGTERM for the serve loop; a no-op off the main thread (as in tests)."""
        try:
            self._prev_handlers = {signal.SIGINT: signal.signal(signal.SIGINT, self._on_sigint),
                                   signal.SIGTERM: signal.signal(signal.SIGTERM, self._on_sigterm)}
            log.trace('Installed SIGINT/SIGTERM shutdown handlers')
        except ValueError:
            self._prev_handlers = {}
            log.debug('Signal handlers not installed (serving off the main thread)')

    def _restore_signals(self) -> None:
        """Restore the signal handlers that were in place before the serve loop."""
        for sig, handler in self._prev_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, TypeError):
                pass
        self._prev_handlers = {}

    # -- serve loop ---------------------------------------------------------------------------------------------

    def _default_runner(self) -> None:
        """Run the real libfuse session over the adapter (lazy import keeps the engine libfuse-free)."""
        from mfusepy import FUSE                    # lazy: pulls libfuse only when a mount actually runs
        from terrace.fuse.adapter import OverlayOperations
        FUSE(OverlayOperations(self.engine), str(self.mountpoint), foreground=True, debug=self.debug)

    def _run_loop(self) -> None:
        """The FUSE loop body run on a worker thread; a failure wakes `serve` to shut down."""
        try:
            (self._runner or self._default_runner)()
        except Exception as exc:
            self._loop_error = exc
            log.error(f'FUSE session error: {exc}')
            self._stop.set()

    def _break_loop(self) -> None:
        """Break a live FUSE loop by unmounting the mount point (or the injected breaker in tests)."""
        breaker = self._breaker or (lambda: unmount(self.mountpoint))
        try:
            breaker()
        except MountError as exc:
            log.error(f'Could not break FUSE loop: {exc}')

    def _serve_after_start(self) -> None:
        """
        Run the FUSE loop on a worker thread, wait for a shutdown signal, then
        tear down. The signal handlers stay installed across `shutdown` so a
        second SIGINT is still caught mid-drain and downgrades it to a detach.
        """
        self._install_signals()
        try:
            fuse_thread = threading.Thread(target=self._run_loop, name='terrace-fuse', daemon=True)
            fuse_thread.start()
            while fuse_thread.is_alive():
                if self._stop.wait(0.5):
                    if fuse_thread.is_alive():
                        self._break_loop()
                    break
            fuse_thread.join()
            if self._loop_error is not None:
                self.shutdown(Shutdown.FORCE)
                raise MountError(f'FUSE session failed: {self._loop_error}') from self._loop_error
            self.shutdown(self._mode or Shutdown.FORCE)
        finally:
            self._restore_signals()

    def serve(self) -> None:
        """Start the overlay and run the FUSE session until shutdown (the foreground path)."""
        self.start()
        self._serve_after_start()

    # -- daemonization ------------------------------------------------------------------------------------------

    def run(self) -> None:
        """Serve in the foreground, or daemonize and serve in the background (the CLI entry)."""
        if self.foreground:
            self.serve()
        else:
            self._daemonize_and_serve()

    def _daemonize_and_serve(self) -> None:
        """
        Double-fork into a background daemon, then serve. A readiness pipe reports
        mount success back to the launching process: the parent exits 0 once the
        engine and pumps are up, or non-zero if `start` fails — so `terrace mount`
        blocks only until the overlay is live, not for the daemon's lifetime.
        """
        read_fd, write_fd = os.pipe()
        if os.fork() > 0:                           # launching process: await readiness, then exit
            os.close(write_fd)
            ready = os.read(read_fd, 1)
            os.close(read_fd)
            os._exit(0 if ready == b'\x01' else 1)
        os.close(read_fd)
        os.setsid()
        if os.fork() > 0:
            os._exit(0)                             # session leader exits so the daemon cannot reacquire a tty
        self._detach_streams()
        try:
            self.start()
        except Exception:
            os.write(write_fd, b'\x00')
            os.close(write_fd)
            raise
        os.write(write_fd, b'\x01')
        os.close(write_fd)
        self._serve_after_start()

    def _detach_streams(self) -> None:
        """Redirect the daemon's std streams: stdin to /dev/null, stdout/stderr to the state-dir log."""
        os.chdir('/')
        sys.stdout.flush()
        sys.stderr.flush()
        null = os.open(os.devnull, os.O_RDONLY)
        logfile = os.open(str(Path(self.spec.state) / LOG_NAME), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(null, 0)
        os.dup2(logfile, 1)
        os.dup2(logfile, 2)
        os.close(null)
        os.close(logfile)
