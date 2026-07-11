# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for FUSE-adapter tests: a started engine wrapped in the mfusepy adapter."""

# Type annotations
from __future__ import annotations
from typing import Iterator

# Standard libs
from pathlib import Path

# External libs
from pytest import fixture

# Internal libs
from sosfs.engine.overlay import OverlayEngine
from sosfs.fuse.adapter import OverlayOperations
from tests.engine.conftest import Clock, build_spec, seed


@fixture
def clock() -> Clock:
    """A fresh ticking clock (injectable-clock convention)."""
    return Clock()


@fixture
def operations(tmp_path: Path, clock: Clock) -> Iterator[OverlayOperations]:
    """A started two-tier engine (project -> scratch) behind the FUSE adapter."""
    seed(tmp_path / 'project', 'top.txt', b'top-level')
    seed(tmp_path / 'project', 'data/a.txt', b'alpha', mode=0o640)
    seed(tmp_path / 'project', 'data/b.txt', b'beta-content')
    engine = OverlayEngine(build_spec(tmp_path, tiers=2), clock=clock)
    engine.start()
    try:
        yield OverlayOperations(engine)
    finally:
        if engine.started:
            engine.stop()
