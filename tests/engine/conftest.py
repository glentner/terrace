# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for engine tests: tier-stack builders and the ticking clock."""

# Type annotations
from __future__ import annotations

# Standard libs
import io
from pathlib import Path, PurePosixPath

# External libs
from pytest import fixture

# Internal libs
from sosfs.tier.spec import OverlaySpec, TierSpec, QuotaSpec, WritebackSpec
from sosfs.tier.posix import PosixTier
from sosfs.tier.protocol import FileMeta
from sosfs.engine.overlay import OverlayEngine


class Clock:
    """A deterministic ticking clock (injectable-clock convention)."""

    def __init__(self, start: float = 100.0, step: float = 1.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def seed(root: Path, path: str, data: bytes, mode: int = 0o644, mtime: float = 50.0) -> None:
    """Place a file directly at a tier root (out-of-band content, as a user would)."""
    root.mkdir(parents=True, exist_ok=True)
    tier = PosixTier('seed', root)
    for parent in reversed(PurePosixPath('/' + path).parents):
        if parent != PurePosixPath('/') and tier.stat(parent) is None:
            tier.mkdir(parent)
    tier.put(PurePosixPath('/' + path), io.BytesIO(data), meta=FileMeta(size=len(data), mtime=mtime, mode=mode))


def build_spec(tmp_path: Path, tiers: int = 2, **tier_kwargs) -> OverlaySpec:
    """
    An OverlaySpec over tmp directories: project (source) plus up to three stage
    tiers (scratch, tmp, shm — fastest last). Stage roots are auto-created by the
    engine; the source root is created here and pre-seeded by tests. Per-tier
    quota and writeback policies override via `<name>_quota` / `<name>_writeback`.
    """
    (tmp_path / 'project').mkdir(exist_ok=True)
    names = ['scratch', 'tmp', 'shm'][:tiers - 1]
    stages = tuple(
        TierSpec(name=name, type='posix', path=str(tmp_path / name), role='stage',
                 scope='node' if name in ('tmp', 'shm') else 'shared',
                 quota=tier_kwargs.get(f'{name}_quota', QuotaSpec()),
                 writeback=tier_kwargs.get(f'{name}_writeback', WritebackSpec()))
        for name in names)
    source = TierSpec(name='project', type='posix', path=str(tmp_path / 'project'), role='source')
    return OverlaySpec(name='testspace', state=tmp_path / 'state', tiers=(source, ) + stages)


@fixture
def clock() -> Clock:
    """A fresh ticking clock."""
    return Clock()


@fixture
def engine(tmp_path: Path, clock: Clock) -> OverlayEngine:
    """A started two-tier engine (project -> scratch) with seeded source content."""
    seed(tmp_path / 'project', 'top.txt', b'top-level')
    seed(tmp_path / 'project', 'data/a.txt', b'alpha', mode=0o640)
    seed(tmp_path / 'project', 'data/b.txt', b'beta-content')
    instance = OverlayEngine(build_spec(tmp_path, tiers=2), clock=clock)
    instance.start()
    yield instance
    if instance.started:
        instance.stop()
