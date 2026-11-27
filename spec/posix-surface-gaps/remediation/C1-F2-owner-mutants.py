# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Class checker for review cycle 1, F2: dropping the owner where the engine creates an entry on a tier fails a test.

Usage (from the cycle worktree's root):

    uv run --no-project --python 3.15 python -I spec/posix-surface-gaps/remediation/C1-F2-owner-mutants.py [--before]

The sites are every place this cycle's diff hands an owner to a tier as it creates an entry there: `create`'s
`FileMeta` for `dest.put`, the replayed mkdir's and `_place_parents`'s `self.source.mkdir`, and `PosixTier.mkdir`'s
ownership step. Each mutant drops the owner at one site, located inside its own function by the AST (never the first
matching text in the file), planted in the working tree, and restored byte for byte afterwards (copy, touch, `cmp`).

For each mutant the checker runs the `foreign_root` tests of `tests/engine/test_ownership.py` and requires at least
one to fail on an assertion, naming each that fails. The unmutated tree runs them first and must pass all five, so a
kill is the mutant's. With `--before`, each mutant also runs every other unit test under `tests/engine`, `tests/fuse`
and `tests/tier` (the suite without these pins) and records whether it survived there.

Exit status: 0 green (every mutant killed, tree restored), 1 red, 3 could not run.
"""

from __future__ import annotations

import os
import re
import ast
import sys
import signal
import shutil
import filecmp
import subprocess
from pathlib import Path


PINS = 'tests/engine/test_ownership.py'
PIN_KEY = 'foreign_root'
PIN_COUNT = 5
BEFORE = ('tests/engine', 'tests/fuse', 'tests/tier')
BACKUP = Path('.local/posix-surface-gaps/mutants')
LIMIT = 300                                                   # seconds per pytest run

# (id, file, class, function, text in that function, its replacement)
MUTANTS = (
    ('M1', 'src/terrace/engine/overlay.py', 'OverlayEngine', 'create',
     'meta = FileMeta(size=0, mtime=now, mode=mode, uid=uid, gid=gid)',
     'meta = FileMeta(size=0, mtime=now, mode=mode)'),
    ('M2', 'src/terrace/engine/overlay.py', 'OverlayEngine', '_execute_namespace_locked',
     'self.source.mkdir(_pure(placed), entry.mode, uid=entry.uid, gid=entry.gid)',
     'self.source.mkdir(_pure(placed), entry.mode)'),
    ('M3', 'src/terrace/engine/overlay.py', 'OverlayEngine', '_place_parents',
     'self.source.mkdir(_pure(target), entry.mode, uid=entry.uid, gid=entry.gid)',
     'self.source.mkdir(_pure(target), entry.mode)'),
    ('M4', 'src/terrace/tier/posix.py', 'PosixTier', 'mkdir',
     'self._apply_meta(real, FileMeta(size=0, mtime=None, uid=uid, gid=gid))',
     'self._apply_meta(real, FileMeta(size=0, mtime=None))'),
)


def say(line: str) -> None:
    print(line, flush=True)


def plant(source: str, cls: str, func: str, old: str, new: str) -> str:
    """`source` with `old` replaced by `new` inside `cls.func` only; `old` must occur there exactly once."""
    tree = ast.parse(source)
    spans = [node for top in tree.body if isinstance(top, ast.ClassDef) and top.name == cls
             for node in top.body if isinstance(node, ast.FunctionDef) and node.name == func]
    if len(spans) != 1:
        raise LookupError(f'{cls}.{func}: found {len(spans)} definitions')
    lines = source.splitlines(keepends=True)
    start, end = spans[0].lineno - 1, spans[0].end_lineno
    body = ''.join(lines[start:end])
    if body.count(old) != 1:
        raise LookupError(f'{cls}.{func}: the planted text occurs {body.count(old)} times, not once')
    return ''.join(lines[:start]) + body.replace(old, new) + ''.join(lines[end:])


def pytest(*args: str) -> tuple[int, str]:
    """Run pytest in its own process group, killed whole after `LIMIT` seconds."""
    command = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-m', 'unit', '-q', '-rfE', *args]
    proc = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=LIMIT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        return -1, output
    return proc.returncode, output


def failed(output: str) -> list[str]:
    return sorted(set(re.findall(r'^FAILED (\S+?)(?: - .*)?$', output, re.MULTILINE)))


def summary(output: str) -> str:
    lines = [line for line in output.splitlines() if re.search(r'\d+ (passed|failed|error)', line)]
    return lines[-1].strip('= ') if lines else '(no summary)'


def pins() -> tuple[int, str]:
    return pytest('-vv', PINS, '-k', PIN_KEY)                # -vv over the base -q: one line per test


def main() -> int:
    before = '--before' in sys.argv[1:]
    if not Path(PINS).exists():
        say('could not run: run from the cycle worktree\'s root')
        return 3
    code, output = pins()
    passed = len(re.findall(rf'::test_{PIN_KEY}\w* PASSED', output))
    if code != 0 or passed != PIN_COUNT:
        say(f'could not run: the unmutated tree passes {passed} of {PIN_COUNT} pins (exit {code}); '
            f'{summary(output)}')
        return 3
    say(f'  baseline: {PIN_COUNT} of {PIN_COUNT} pins pass on the unmutated tree')
    BACKUP.mkdir(parents=True, exist_ok=True)
    red, rows = [], []
    for ident, name, cls, func, old, new in MUTANTS:
        target = Path(name)
        backup = BACKUP / f'{ident}-{target.name}'
        original = target.read_text()
        try:
            mutated = plant(original, cls, func, old, new)
        except LookupError as error:
            red.append(f'{ident}: not planted: {error}')
            continue
        shutil.copyfile(target, backup)
        try:
            target.write_text(mutated)
            if target.read_text() == original:
                red.append(f'{ident}: the mutation did not land')
                continue
            code, output = pins()
            killers = [test.split('::')[-1] for test in failed(output)]
            survived_before = None
            if before:
                b_code, b_output = pytest('-n', 'auto', *BEFORE, '-k', f'not {PIN_KEY}')
                survived_before = f'survived ({summary(b_output)})' if b_code == 0 else \
                    f'killed by {", ".join(t.split("::")[-1] for t in failed(b_output)) or summary(b_output)}'
        finally:
            shutil.copyfile(backup, target)
            os.utime(target)
        if not filecmp.cmp(backup, target, shallow=False) or target.read_text() != original:
            say(f'  RED   {ident}: {name} was not restored; the backup is {backup}')
            return 1
        if code <= 0 or not killers:
            red.append(f'{ident} ({cls}.{func}) survived the pins: exit {code}, {summary(output)}')
        rows.append((ident, f'`{cls}.{func}`', ', '.join(f'`{k}`' for k in killers) or 'none', survived_before))
    say('')
    say('| Mutant | Site (owner dropped) | Killed by | Without these pins |' if before else
        '| Mutant | Site (owner dropped) | Killed by |')
    say('|---|---|---|---|' if before else '|---|---|---|')
    for ident, site, killers, survived_before in rows:
        say(f'| {ident} | {site} | {killers} | {survived_before} |' if before else f'| {ident} | {site} | {killers} |')
    say('')
    for message in red:
        say(f'  RED   {message}')
    say(f'--- C1-F2-owner-mutants: {"red" if red else "green"} ({len(rows)} of {len(MUTANTS)} mutants run, '
        f'{sum(1 for row in rows if row[2] != "none")} killed; every file restored)')
    return 1 if red else 0


if __name__ == '__main__':
    sys.exit(main())
