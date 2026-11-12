# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The bundle loop scenario. Loads the helper from tools/, where a person runs it as a script."""

# Type annotations
from __future__ import annotations
from types import ModuleType

# Standard libs
import sys
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

# Public interface
__all__ = ['bundle_loop', 'HELPER']

HELPER = Path(__file__).resolve().parents[2] / 'tools' / 'bundle_loop.py'


def _load() -> ModuleType:
    """The helper as a module, so the tests and a person at a terminal run one implementation."""
    spec = spec_from_file_location('bundle_loop', HELPER)
    module = module_from_spec(spec)
    sys.modules['bundle_loop'] = module
    spec.loader.exec_module(module)
    return module


bundle_loop = _load()
