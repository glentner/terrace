# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for FUSE-adapter tests: a started engine wrapped in the mfusepy adapter."""

# Type annotations
from __future__ import annotations
from typing import TYPE_CHECKING, Iterator

# Standard libs
import zipfile
from pathlib import Path

# External libs
from pytest import Config, Item, fixture, mark

# Internal libs
from terrace.tier.spec import OverlaySpec
from terrace.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed

if TYPE_CHECKING:
    from terrace.fuse.adapter import OverlayOperations

# mfusepy loads libfuse at import and raises OSError both when it finds no library and when dlopen fails.
try:
    import mfusepy                                         # noqa: F401 — availability probe only
    LIBFUSE_UNAVAILABLE = None
except OSError as error:
    LIBFUSE_UNAVAILABLE = f'libfuse cannot be loaded: {error}'

HERE = Path(__file__).parent

ZIP_MEMBERS = {'top.txt': (0o100644, b'top-level'), 'sub/': (0o040755, b''), 'sub/nested.txt': (0o100644, b'nested')}


def zip_source_spec(tmp_path: Path) -> OverlaySpec:
    """
    A ZIP of `top.txt`, `sub/` and `sub/nested.txt`, each with an explicit unix mode, and a spec parsed from
    TOML that names it as a direct-update source over a POSIX location, above one shared POSIX stage.
    """
    archive = tmp_path / 'data.zip'
    with zipfile.ZipFile(archive, mode='w') as bundle:
        for name, (mode, data) in ZIP_MEMBERS.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 2, 3, 4, 5))
            info.external_attr = mode << 16 | (0x10 if name.endswith('/') else 0)
            bundle.writestr(info, data)
    return OverlaySpec.parse(f'''
[overlay]
name = "zipspace"
state = "{tmp_path / 'state'}"

[[tier]]
name = "archive"
type = "zip"
role = "source"
location = {{ type = "posix", path = "{archive}" }}

[[tier]]
name = "scratch"
type = "posix"
path = "{tmp_path / 'scratch'}"
''')


def pytest_collection_modifyitems(config: Config, items: list[Item]) -> None:
    """Without libfuse, skip every test in this package by name; the hook sees the whole session's items."""
    if not LIBFUSE_UNAVAILABLE:
        return
    # Prepended so pytest meets it before a test's own true skipif and reports libfuse as the reason.
    marker = mark.skipif(True, reason=LIBFUSE_UNAVAILABLE)
    for item in items:
        if item.path.is_relative_to(HERE):
            item.add_marker(marker, append=False)


@fixture
def clock() -> Clock:
    """A fresh ticking clock (injectable-clock convention)."""
    return Clock()


@fixture
def operations(tmp_path: Path, clock: Clock) -> Iterator[OverlayOperations]:
    """A started two-tier engine (project -> scratch) behind the FUSE adapter."""
    from terrace.fuse.adapter import OverlayOperations
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
