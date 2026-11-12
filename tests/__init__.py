# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test helpers for Terrace."""

# Type annotations
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

# Standard libs
import os
import re
import sys
import subprocess

# Public interface
__all__ = ['main', 'main_lines', 'assert_output']


def main(argv: List[str], env: Optional[Dict[str, str]] = None, timeout: float = 30) -> Tuple[int, str, str]:
    """Run the Terrace CLI in a subprocess; returns (returncode, stdout, stderr)."""
    base_env = {name: value for name, value in os.environ.items() if not name.startswith('TERRACE_')}
    result = subprocess.run([sys.executable, '-m', 'terrace', *argv],
                            env={**base_env, **(env or {})},
                            capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def main_lines(argv: List[str], **kwargs) -> Tuple[int, List[str], List[str]]:
    """Same as `main` with output split into lines."""
    returncode, stdout, stderr = main(argv, **kwargs)
    return returncode, stdout.splitlines(), stderr.splitlines()


def assert_output(pattern: str, output: str, count: Optional[int] = None) -> None:
    """Assert `pattern` matches within `output` (optionally exactly `count` times)."""
    matches = re.findall(pattern, output, flags=re.MULTILINE)
    if count is None:
        assert matches, f'Pattern {pattern!r} not found in output:\n{output}'
    else:
        assert len(matches) == count, f'Pattern {pattern!r} found {len(matches)}x (expected {count}):\n{output}'
