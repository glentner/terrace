# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for the Terrace test suite."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
from pathlib import Path

# External libs
from pytest import fixture


@fixture(scope='function')
def temp_site(tmp_path: Path, monkeypatch) -> Path:
    """Isolated site: scrub TERRACE_* variables and redirect XDG paths into a tmp dir."""
    for name in list(os.environ):
        if name.startswith('TERRACE_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    (tmp_path / 'config').mkdir()
    (tmp_path / 'state').mkdir()
    return tmp_path
