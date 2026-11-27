# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Class checker for review cycle 1, F1: no line this cycle adds or changes under src/, tests/ or tools/ exceeds 120.

Usage (from the cycle worktree's root):

    uv run --no-project --python 3.15 python -I spec/posix-surface-gaps/remediation/C1-F1-line-length.py

The cycle's lines are the added side of `git diff --merge-base origin/main` over src/, tests/ and tools/ (the
working tree against the merge base, so it grades uncommitted work too). Ruff's E501 at 120 runs over every file
that diff touches; a hit on one of the cycle's lines is red, and a hit on any other line predates the cycle and is
listed, not graded. A file the diff touches that ruff reports nothing for must still have been checked, so the
checker asserts ruff saw every one of them.

Exit status: 0 green, 1 red, 3 could not run.
"""

from __future__ import annotations

import re
import sys
import json
import subprocess
from pathlib import Path


BASE = 'origin/main'
ROOTS = ('src', 'tests', 'tools')
LIMIT = 120
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def say(line: str) -> None:
    print(line, flush=True)


def git(*args: str) -> str:
    return subprocess.run(['git', *args], text=True, capture_output=True, check=True).stdout


def added_lines() -> dict[str, set[int]]:
    """Line numbers (in the working tree) the cycle adds or changes, per Python file under the roots."""
    added: dict[str, set[int]] = {}
    current = None
    for line in git('diff', '--merge-base', BASE, '-U0', '--no-color', '--', *ROOTS).splitlines():
        if line.startswith('+++ '):
            name = line[4:]
            current = name[2:] if name.startswith('b/') and name.endswith('.py') else None
            if current is not None:
                added.setdefault(current, set())
        elif current is not None and (match := HUNK.match(line)):
            start, count = int(match.group(1)), int(match.group(2) if match.group(2) is not None else 1)
            added[current].update(range(start, start + count))
    return added


def main() -> int:
    if not Path('AGENTS.md').exists() or not Path('src').is_dir():
        say('could not run: no AGENTS.md or src/ here; run from the cycle worktree\'s root')
        return 3
    try:
        added = added_lines()
    except subprocess.CalledProcessError as error:
        say(f'could not run: git diff against {BASE} failed: {error.stderr.strip()}')
        return 3
    files = sorted(name for name in added if Path(name).exists())
    if not files:
        say(f'could not run: the diff against {BASE} touches no Python file under {", ".join(ROOTS)}')
        return 3
    proc = subprocess.run(['uvx', 'ruff', 'check', '--no-cache', '--select', 'E501', '--line-length', str(LIMIT),
                           '--output-format', 'json', *files], text=True, capture_output=True)
    if proc.returncode not in (0, 1):
        say(f'could not run: ruff exited {proc.returncode}: {proc.stderr.strip()}')
        return 3
    hits = json.loads(proc.stdout or '[]')
    red, carried = [], []
    for hit in hits:
        name = str(Path(hit['filename']).resolve().relative_to(Path.cwd().resolve()))
        row = hit['location']['row']
        (red if row in added.get(name, set()) else carried).append(f'{name}:{row} {hit["message"]}')
    say(f'  graded {sum(len(added[name]) for name in files)} added or changed lines in {len(files)} files: '
        f'{", ".join(files)}')
    for message in carried:
        say(f'  base  {message} (not a line this cycle adds or changes)')
    for message in red:
        say(f'  RED   {message}')
    say(f'--- C1-F1-line-length: {"red" if red else "green"} ({len(red)} cycle lines over {LIMIT}, '
        f'{len(carried)} carried from {BASE})')
    return 1 if red else 0


if __name__ == '__main__':
    sys.exit(main())
