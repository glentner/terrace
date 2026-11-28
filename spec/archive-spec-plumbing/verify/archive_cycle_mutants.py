# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
A pytest plugin that plants one of the cycle's mutants before any test runs, for the gate's
`mutants` mode. Loaded as `-p archive_cycle_mutants` with this directory on PYTHONPATH; the mutant is
named by ARCHIVE_CYCLE_MUTANT, and each plant appends a line to ARCHIVE_CYCLE_MUTANT_LOG so the gate
can tell a mutant that took effect from one that did not. Never collected as a test module.

    read-only   the engine never reads as read-only, whatever the source
    realpath    os.path.realpath does no symlink resolution (abspath)
    oracle      PosixTier.usage leaves out the staged bundles, as the base did
    counter     ArchiveTier.stage reports that it created nothing, so nothing counts the pull
"""

# Standard libs
import os
from pathlib import Path


def _log(text: str) -> None:
    target = os.environ.get('ARCHIVE_CYCLE_MUTANT_LOG')
    if target:
        with open(target, 'a') as stream:
            stream.write(text + '\n')


def _read_only() -> None:
    from terrace.engine.overlay import OverlayEngine
    original = OverlayEngine.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.read_only = False
        _log('read-only fired')

    OverlayEngine.__init__ = init


def _realpath() -> None:
    import posixpath
    plain = posixpath.abspath

    def realpath(path, *args, **kwargs):
        _log('realpath fired')
        return plain(path)

    posixpath.realpath = realpath
    os.path.realpath = realpath


def _oracle() -> None:
    from terrace.tier.posix import PosixTier
    from terrace.tier.protocol import TierUsage
    original = PosixTier.usage

    def usage(self):
        found = original(self)
        bundles = Path(self.root) / '.terrace' / 'bundles'
        size, count = 0, 0
        if bundles.is_dir():
            for item in bundles.iterdir():
                if item.is_file() and item.name.endswith('.tar') and not item.name.startswith('.'):
                    size, count = size + item.stat().st_size, count + 1
        if count:
            _log('oracle fired')
        return TierUsage(bytes=found.bytes - size, files=found.files - count)

    PosixTier.usage = usage


def _counter() -> None:
    from terrace.tier.archive import ArchiveTier
    original = ArchiveTier.stage

    def stage(self, *args, **kwargs):
        original(self, *args, **kwargs)
        _log('counter fired')
        return False

    ArchiveTier.stage = stage


PLANTS = {'read-only': _read_only, 'realpath': _realpath, 'oracle': _oracle, 'counter': _counter}


def pytest_configure(config) -> None:
    name = os.environ.get('ARCHIVE_CYCLE_MUTANT')
    if name:
        PLANTS[name]()
        _log(f'{name} planted')
