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
from typing import Any, Dict, List

# Standard libs
import io
import os
import sys
import random
import shutil
import signal
import tarfile
import argparse
import tempfile
import subprocess
from uuid import uuid4
from pathlib import Path

# Public interface
__all__ = ['Store', 'Loop', 'pack', 'member_bytes', 'main']

PREFIX = 'bundles'
STAGES = (('scratch', 'shared'), ('tmp', 'node'), ('shm', 'node'))      # stack order below the source, fastest last
RESYNC = ('24/day', 3600.0)                                             # sync.max_frequency and its interval in seconds
DELAY = ('60s', 60.0)                                                   # every stage tier's write-back delay
SIZE = 64 * 1024 * 1024                                                 # a stage tier's size limit unless overridden
FILES = 10000                                                           # every stage tier's count limit


class Store:
    """A local S3-compatible object store with no container: a moto server on a free loopback port."""

    endpoint: str | None
    _server: Any

    def __init__(self) -> None:
        self.endpoint = None
        self._server = None

    def start(self) -> Store:
        from moto.server import ThreadedMotoServer
        # verbose=False keeps the server's banner off standard output, which carries the spec's path.
        self._server = ThreadedMotoServer(ip_address='127.0.0.1', port=0, verbose=False)
        self._server.start()
        host, port = self._server.get_host_and_port()
        self.endpoint = f'http://{host}:{port}'
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None

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
                 parent: str | Path | None = None) -> None:
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
            self.content = self.root / 'content'
            self.stems = [f'bundle-{index:03d}' for index in range(bundles)]
            self.keys = {stem: f'{PREFIX}/{stem}.tar.gz' for stem in self.stems}
            self.client.create_bucket(Bucket=self.bucket)
            self._made_bucket = True
            for index, stem in enumerate(self.stems):
                self._generate(index, stem, members)
                self.client.put_object(Bucket=self.bucket, Key=self.keys[stem], Body=pack(self.content, stem))
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
    """A termination signal arrived."""


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

    # A handler of our own for SIGINT too, so a helper started with it ignored (a shell's background
    # job) still stops on an interrupt.
    def on_signal(signum: int, frame: object) -> None:
        raise Stop(signum)
    handled = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    for signum in handled:
        signal.signal(signum, on_signal)

    def quiet() -> None:
        """Once teardown begins, a second signal must not cut it short."""
        for signum in handled:
            signal.signal(signum, signal.SIG_IGN)

    store = None
    try:
        endpoint = options.endpoint
        if endpoint is None:
            store = Store().start()
            endpoint = store.endpoint
        loop = Loop(endpoint, bundles=options.bundles, access_key=options.access_key,
                    secret_key=options.secret_key, region=options.region, fastest_size=options.fastest_size)
        status = 0
        try:
            print(loop.spec, flush=True)
            if command:
                env = dict(os.environ, BUNDLE_LOOP_SPEC=str(loop.spec), BUNDLE_LOOP_ENDPOINT=endpoint,
                           BUNDLE_LOOP_BUCKET=loop.bucket)
                status = subprocess.run(command, env=env).returncode
            else:
                print('bundle_loop: up; interrupt to tear down', file=sys.stderr, flush=True)
                signal.pause()
        except (KeyboardInterrupt, Stop):
            status = 130 if command else 0
        finally:
            quiet()
            loop.close()
        return status
    except (KeyboardInterrupt, Stop):
        return 130 if command else 0
    finally:
        quiet()
        if store is not None:
            store.stop()


if __name__ == '__main__':
    sys.exit(main())
