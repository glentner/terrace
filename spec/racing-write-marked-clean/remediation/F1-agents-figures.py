# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Class checker for review cycle 1, F1: every measured figure AGENTS.md carries equals what the tree measures.

Usage (from the cycle worktree's root, after the frozen gate):

    uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/remediation/F1-agents-figures.py

The figures are every count this cycle's diff can move: the unit, integration and total test counts, the engine
tests, the test files, and the `.py` files the SPDX check covers. Each is read the way the text writes it (a
numeral, or a number word), each pattern must match exactly once, and each is compared with a measurement of the
tree: pytest's collection (node ids counted, never an exit code) and the same file walk `tools/lint.sh` and the
frozen gate use. A figure left stale, or a sentence reworded so its figure is no longer found, is red. The
timings (`~60 s`, `~30 s`, `~25 s`) are approximate and are not checked here.

Exit status: 0 green, 1 red, 3 could not run.
"""

from __future__ import annotations

import re
import sys
import subprocess
from pathlib import Path


WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
         'ten': 10, 'eleven': 11, 'twelve': 12}

# (where, pattern, the measured figure each group must equal)
FIGURES = (
    ('§ Commands, the unit line', r'# (\d+) tests, ~\d+ s — must pass', ('unit', )),
    ('§ Commands, the integration line', r'pytest -m integration\s+# (\d+) tests', ('integration', )),
    ('§ Repository map, `tests/`', r'(\d+) files, (\d+) tests \((\d+) `unit`, (\d+) `integration`\)',
     ('test_files', 'total', 'unit', 'integration')),
    ('§ Architecture, the engine tests', r'directly, by (\d+) engine tests with no mount', ('engine', )),
    ('§ CI, the integration surface', r'so\s+(\w+) tests — the entire drain', ('integration', )),
    ('§ Conventions, the integration gate', r'\((\d+) skipped, (\d+) deselected', ('integration', 'unit')),
    ('§ Verification', r'\*\*(\d+) unit tests in', ('unit', )),
    ('§ Code style, SPDX', r'all (\d+) `\.py` files carry them', ('py_files', )),
)


def say(line: str) -> None:
    print(line, flush=True)


def collected(*args: str) -> int:
    """Test node ids pytest collects for `args`."""
    proc = subprocess.run(['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '--collect-only', '-q',
                           *args], text=True, capture_output=True)
    ids = [line for line in proc.stdout.splitlines() if line.startswith('tests/') and '::' in line]
    if proc.returncode != 0 or not ids:
        raise RuntimeError(f'pytest --collect-only {" ".join(args)} exited {proc.returncode} with {len(ids)} ids')
    return len(ids)


def python_files(*roots: str) -> int:
    return len([p for root in roots for p in Path(root).rglob('*.py') if '__pycache__' not in p.parts])


def number(text: str) -> int:
    return int(text) if text.isdigit() else WORDS.get(text.lower(), -1)


def main() -> int:
    if not Path('AGENTS.md').exists() or not Path('tests').is_dir():
        say('could not run: no AGENTS.md or tests/ here; run from the cycle worktree\'s root')
        return 3
    try:
        measured = {'unit': collected('-m', 'unit'), 'integration': collected('-m', 'integration'),
                    'total': collected(), 'engine': collected('-m', 'unit', 'tests/engine'),
                    'test_files': python_files('tests'), 'py_files': python_files('src', 'tests')}
    except RuntimeError as error:
        say(f'could not run: {error}')
        return 3
    say(f'  measured {measured}')
    red = []
    if measured['unit'] + measured['integration'] != measured['total']:
        red.append(f'the unit and integration counts do not add up to the total ({measured})')
    agents = Path('AGENTS.md').read_text()
    for where, pattern, keys in FIGURES:
        found = list(re.finditer(pattern, agents))
        if len(found) != 1:
            red.append(f'{where}: the pattern {pattern!r} matches {len(found)} times, not once')
            continue
        line = agents.count('\n', 0, found[0].start()) + 1
        for group, key in enumerate(keys, start=1):
            stated = number(found[0].group(group))
            if stated != measured[key]:
                red.append(f'AGENTS.md:{line} ({where}) states {found[0].group(group)} for {key}; '
                           f'the tree measures {measured[key]}')
            else:
                say(f'  ok    AGENTS.md:{line} ({where}): {key} = {measured[key]}')
    for message in red:
        say(f'  RED   {message}')
    say(f'--- F1-agents-figures: {"red" if red else "green"} ({len(FIGURES)} sentences, '
        f'{sum(len(keys) for _, _, keys in FIGURES)} figures)')
    return 1 if red else 0


if __name__ == '__main__':
    sys.exit(main())
