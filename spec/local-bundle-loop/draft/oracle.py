# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
DRAFT for tests/loop/oracle.py (plan step; the build phase owns the final file).

What a bundle should hold, decided with a directory tree and `tarfile` alone. Nothing here imports
the project, the helper, or an S3 client: the caller hands over a directory and an object's bytes.
"""

# Type annotations
from __future__ import annotations
from typing import Dict, Optional

# Standard libs
import io
import tarfile
import tempfile
from pathlib import Path

# Public interface
__all__ = ['Tree', 'tree', 'unpacked', 'difference']

# A tree is every path below a root, '/'-rooted: a file maps to its bytes, a directory to None.
Tree = Dict[str, Optional[bytes]]


def tree(root: Path) -> Tree:
    """Every file and directory below `root`. A symlink or a special file is an error, never skipped."""
    found: Tree = {}
    for path in sorted(root.rglob('*')):
        name = '/' + path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError(f'The oracle holds files and directories only: {name}')
        found[name] = None if path.is_dir() else path.read_bytes()
    return found


def unpacked(blob: bytes) -> Tree:
    """
    The tree a tar.gz object unpacks to, through `tarfile` into a scratch directory.

    Extraction keeps the last of two members with one name, so an archive rebuilt by appending
    would unpack to the right tree while holding every superseded member. A name that appears
    twice is an error here, and `ignore_zeros` reads on past an end-of-archive marker so that a
    second archive behind the first is seen as well.
    """
    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz', ignore_zeros=True) as archive:
            names = [member.name.strip('/') for member in archive.getmembers()]
            if repeated := sorted({name for name in names if names.count(name) > 1}):
                raise ValueError(f'The archive holds a member more than once: {", ".join(repeated)}')
            archive.extractall(scratch, filter='data')
        return tree(Path(scratch))


def difference(expected: Tree, found: Tree) -> str:
    """An empty string when the trees are equal, else one line per path that differs."""
    lines = []
    for name in sorted(set(expected) | set(found)):
        if name not in found:
            lines.append(f'missing: {name}')
        elif name not in expected:
            lines.append(f'unexpected: {name}')
        elif (expected[name] is None) != (found[name] is None):
            lines.append(f'kind differs: {name}')
        elif expected[name] != found[name]:
            lines.append(f'bytes differ: {name}')
    return '\n'.join(lines)
