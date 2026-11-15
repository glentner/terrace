# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Stand the bundle loop up on one machine.

Starts a local S3-compatible object store (moto, in this process, no container), fills a bucket
with generated tar.gz bundles, writes an overlay spec over it whose stage tiers and state directory
are POSIX directories under one temporary directory, and prints the spec's path as the first line of
standard output. With a command, runs it with BUNDLE_LOOP_SPEC, BUNDLE_LOOP_ENDPOINT and
BUNDLE_LOOP_BUCKET set and exits with its status; without one, waits for an interrupt. Every exit
path (the command ending, an error, SIGINT, SIGTERM, SIGHUP) removes the bucket and the directory and
stops the store. SIGKILL cannot be caught: it ends the store with the process and leaves the
directory behind.

The helper imports nothing of the project, so it works whatever the spec parser accepts.
"""

# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, List

# Standard libs
import io
import os
import sys
import random
import select
import shutil
import signal
import tarfile
import argparse
import tempfile
import threading
import subprocess
import socketserver
from uuid import uuid4
from pathlib import Path

# Public interface
__all__ = ['NamedByAddress', 'Store', 'Loop', 'pack', 'member_bytes', 'main']

PREFIX = 'bundles'
STAGES = (('scratch', 'shared'), ('tmp', 'node'), ('shm', 'node'))      # stack order below the source, fastest last
RESYNC = ('24/day', 3600.0)                                             # sync.max_frequency and its interval in seconds
DELAY = ('60s', 60.0)                                                   # every stage tier's write-back delay
SIZE = 64 * 1024 * 1024                                                 # a stage tier's size limit unless overridden
FILES = 10000                                                           # every stage tier's count limit
HANDLED = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)                # each one tears the loop down


class NamedByAddress:
    """
    Mixed in ahead of an `http.server` server class: bind without looking the server's own name up.
    `HTTPServer.server_bind` names the server by `socket.getfqdn` of its address, a reverse lookup
    that waits as long as the host's resolver takes to answer, which can be longer than any test
    allows. Nothing here reads the name, so the address stands in for it.
    """

    server_address: Any

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), port


class Store:
    """A local S3-compatible object store with no container: a moto server on a free loopback port."""

    endpoint: str | None
    _server: Any
    _thread: threading.Thread | None

    def __init__(self) -> None:
        self.endpoint = None
        self._server = None
        self._thread = None

    def start(self) -> Store:
        # moto's application under werkzeug's threaded server, as moto's ThreadedMotoServer serves it,
        # but bound here: a failure to bind raises from this call instead of leaving it waiting on a
        # serving thread that has ended, and no banner reaches standard output, which carries the
        # spec's path.
        from moto.server import DomainDispatcherApplication, create_backend_app
        from werkzeug.serving import ThreadedWSGIServer

        class Server(NamedByAddress, ThreadedWSGIServer):
            pass

        self._server = Server('127.0.0.1', 0, DomainDispatcherApplication(create_backend_app))
        self._thread = threading.Thread(target=self._server.serve_forever, name='store', daemon=True)
        self._thread.start()
        self.endpoint = f'http://127.0.0.1:{self._server.port}'
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()                  # serve_forever closes the listening socket as it returns
            self._thread.join()
            self._server = self._thread = None

    def __enter__(self) -> Store:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def member_bytes(bundle: int, member: int, size: int) -> bytes:
    """Seeded random bytes: the same on every run, different for every member, and not compressible."""
    return random.Random(f'{bundle}/{member}').randbytes(size)


def pack(content: Path, stem: str) -> bytes:
    """One tar.gz of `content/stem`, members named from `stem/` down."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        archive.add(content / stem, arcname=stem)
    return buffer.getvalue()


class Loop:
    """One bucket of generated bundles, a spec over it, and the directories the spec names."""

    root: Path
    content: Path
    stems: List[str]
    keys: Dict[str, str]
    spec: Path
    state: Path
    tiers: Dict[str, Path]
    staging: Path | None
    form: str

    def __init__(self, endpoint: str, *, bundles: int = 1, members: int = 6, fastest_size: int | None = None,
                 access_key: str = 'test', secret_key: str = 'test', region: str = 'us-east-1',
                 parent: str | Path | None = None, cancelled: Callable[[], object] = lambda: False) -> None:
        if bundles < 1:
            raise ValueError('a loop needs at least one bundle')
        import boto3
        self.endpoint, self.region = endpoint, region
        self.access_key, self.secret_key = access_key, secret_key
        self.form = 'single' if bundles == 1 else 'sharded'
        self.bucket = f'loop-{uuid4().hex[:12]}'
        self.client = boto3.client('s3', endpoint_url=endpoint, region_name=region,
                                   aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        self.fastest_size = fastest_size
        self.resync_interval, self.delay = RESYNC[1], DELAY[1]
        self.staging = None
        self.root = Path(tempfile.mkdtemp(prefix='bundle-loop.', dir=parent))
        self._made_bucket = False
        try:
            # `cancelled` is asked between requests, never inside one: a stop raised inside the client
            # library can be wrapped and retried away.
            def check() -> None:
                if cancelled():
                    raise Stop()
            self.content = self.root / 'content'
            self.stems = [f'bundle-{index:03d}' for index in range(bundles)]
            self.keys = {stem: f'{PREFIX}/{stem}.tar.gz' for stem in self.stems}
            check()
            self.client.create_bucket(Bucket=self.bucket)
            self._made_bucket = True
            for index, stem in enumerate(self.stems):
                check()
                self._generate(index, stem, members)
                self.client.put_object(Bucket=self.bucket, Key=self.keys[stem], Body=pack(self.content, stem))
            check()
            self.spec = self.respec('.')
        except BaseException:
            self.close()
            raise

    def _generate(self, index: int, stem: str, members: int) -> None:
        """One bundle's tree: a single top-level directory named for the bundle, and no empty directory."""
        top = self.content / stem
        for member in range(members):
            folder = top if member % 2 == 0 else top / 'sub'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f'member-{member:03d}.txt').write_bytes(member_bytes(index, member, 512 + 128 * member))

    def respec(self, label: str) -> Path:
        """Write a spec over the same bucket whose state and stage directories are under `root/label`."""
        base = (self.root / label).resolve()
        base.mkdir(parents=True, exist_ok=True)
        self.state = base / 'state'
        self.tiers = {name: base / name / 'space' for name, _ in STAGES}
        connection = (f'{{ endpoint = "{self.endpoint}", region = "{self.region}", '
                      f'access_key_id = "{self.access_key}", secret_access_key = "{self.secret_key}", '
                      f'allow_http = {"true" if self.endpoint.startswith("http://") else "false"} }}')
        lines = ['[overlay]', 'name = "loop"', f'state = "{self.state}"', '',
                 '[[tier]]', 'name = "bundles"', 'type = "tar.gz"', 'role = "source"']
        if self.form == 'single':
            stem = self.stems[0]
            # Beside the durable tier's root, as PLAN.md's example puts /scratch/space.tar.gz beside /scratch/space.
            self.staging = base / 'scratch' / f'{stem}.tar'
            lines += [f'location = {{ type = "s3", url = "s3://{self.bucket}/{self.keys[stem]}", s3 = {connection} }}',
                      f'staging = "{self.staging}"']
        else:
            lines += ['sharded = true',
                      f'location = {{ type = "s3", url = "s3://{self.bucket}/{PREFIX}/*.tar.gz", s3 = {connection} }}']
        lines += [f'sync = {{ max_frequency = "{RESYNC[0]}" }}']
        for name, scope in STAGES:
            size = self.fastest_size if (name == STAGES[-1][0] and self.fastest_size) else SIZE
            lines += ['', '[[tier]]', f'name = "{name}"', 'type = "posix"', f'path = "{self.tiers[name]}"',
                      f'scope = "{scope}"', f'quota = {{ size = {size}, files = {FILES} }}',
                      f'writeback = {{ delay = "{DELAY[0]}" }}']
        spec = base / 'spec.toml'
        spec.write_text('\n'.join(lines) + '\n')
        return spec

    def close(self) -> None:
        """
        Remove the bucket this loop made and its directory. Safe to call twice. A directory that
        cannot be removed raises: a loop left behind is never reported as a clean exit.
        """
        if self._made_bucket:
            try:
                listing = self.client.list_objects_v2(Bucket=self.bucket)
                for item in listing.get('Contents', []):
                    self.client.delete_object(Bucket=self.bucket, Key=item['Key'])
                self.client.delete_bucket(Bucket=self.bucket)
            except Exception:
                pass                                    # the store may already be gone; the directory still goes
            self._made_bucket = False
        if self.root.exists():
            for found in [self.root, *self.root.rglob('*')]:
                if found.is_dir() and not found.is_symlink():
                    found.chmod(0o700)
            shutil.rmtree(self.root)

    def __enter__(self) -> Loop:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Stop(Exception):
    """A termination signal arrived; raised by the helper's own code, between library calls."""


class Requests:
    """
    Termination requests. The handler only records the signal: it raises nothing, so no library code
    on the stack can swallow or retry the request, and a signal during teardown cannot cut it short.
    The interpreter also writes each signal's number to a pipe, from whichever thread the signal
    landed on, so a wait on that pipe wakes even when the signal came before the wait began.
    """

    signum: int | None

    def __init__(self) -> None:
        self.signum = None
        self._read, self._write = os.pipe()
        for end in (self._read, self._write):
            os.set_blocking(end, False)
        # A mask inherited from the parent would hold these pending forever; a handler of our own for
        # SIGINT too, so a helper started with it ignored (a shell's background job) still stops.
        signal.pthread_sigmask(signal.SIG_UNBLOCK, HANDLED)
        self._previous = signal.set_wakeup_fd(self._write, warn_on_full_buffer=False)
        for signum in HANDLED:
            signal.signal(signum, self._record)

    def _record(self, signum: int, frame: object) -> None:
        if self.signum is None:
            self.signum = signum

    def __bool__(self) -> bool:
        """Whether a signal has arrived, by the handler or by the pipe, whichever saw it first."""
        try:
            seen = os.read(self._read, 512)
        except BlockingIOError:
            seen = b''
        if seen and self.signum is None:
            self.signum = seen[0]
        return self.signum is not None

    def wait(self, timeout: float | None = None) -> bool:
        """Block until a signal arrives or `timeout` passes; whether one has arrived."""
        if not self:
            select.select([self._read], [], [], timeout)
        return bool(self)

    def close(self) -> None:
        """Stop the pipe. The handler stays: a signal once the loop is gone changes nothing."""
        signal.set_wakeup_fd(self._previous)
        os.close(self._read)
        os.close(self._write)


def run(command: List[str], env: Dict[str, str], requests: Requests) -> int:
    """Run the command to its end and return its status; on a request, end it and raise `Stop`."""
    process = subprocess.Popen(command, env=env)
    try:
        while True:
            try:
                return process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                if requests:
                    raise Stop() from None
    finally:
        if process.poll() is None:
            try:
                process.wait(timeout=0.25)          # an interrupt to the process group reached it too
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main(argv: List[str] | None = None) -> int:
    """The command line: stand the loop up, print the spec's path, and tear it down on every exit path."""
    parser = argparse.ArgumentParser(prog='bundle_loop.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-n', '--bundles', type=int, default=1, help='bundles in the bucket (default 1)')
    parser.add_argument('--fastest-size', type=int, help='size limit of the fastest tier, in bytes')
    parser.add_argument('--endpoint', help='use this S3 endpoint instead of starting a store')
    parser.add_argument('--access-key', default='test')
    parser.add_argument('--secret-key', default='test')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='run this, then tear down')
    options = parser.parse_args(argv)
    command = options.command[1:] if options.command[:1] == ['--'] else options.command
    stopped = 130 if command else 0

    requests = Requests()
    store = None
    try:
        endpoint = options.endpoint
        if endpoint is None:
            store = Store().start()
            endpoint = store.endpoint
        if requests:
            return stopped
        try:
            loop = Loop(endpoint, bundles=options.bundles, access_key=options.access_key,
                        secret_key=options.secret_key, region=options.region, fastest_size=options.fastest_size,
                        cancelled=requests.__bool__)
        except Stop:
            return stopped
        try:
            print(loop.spec, flush=True)
            if command:
                env = dict(os.environ, BUNDLE_LOOP_SPEC=str(loop.spec), BUNDLE_LOOP_ENDPOINT=endpoint,
                           BUNDLE_LOOP_BUCKET=loop.bucket)
                return run(command, env, requests)
            print('bundle_loop: up; interrupt to tear down', file=sys.stderr, flush=True)
            while not requests.wait():
                pass
            return stopped
        except Stop:
            return stopped
        finally:
            loop.close()
    finally:
        try:
            if store is not None:
                store.stop()
        finally:
            requests.close()


if __name__ == '__main__':
    sys.exit(main())
