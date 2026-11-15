# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Class checker for review cycle 1, F2: the reset of a handle's flag after a hop is pinned on both sides.

Usage (from the cycle worktree's root, after the frozen gate):

    uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/remediation/F2-liveness-pin.py

Liveness: `test_writer_that_never_pauses_lets_commit_upstream_return` passes on this tree. Completeness: the named
tests that place a write on each side of the reset pass on this tree. Teeth: the review's surviving mutant (the
flag reset to `bool(met)` instead of `False`) is planted into a copy of `src/` under `.local/remediation-F2/`,
never into the tree, and the liveness test fails there on its outcome assertion while the completeness tests
still pass, so the kill is the liveness pin's own. Counts come from pytest's JUnit XML. Nothing is deleted.

Exit status: 0 green, 1 red, 3 could not run.
"""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


RACING = 'tests/engine/test_racing.py'
LIVENESS = 'test_writer_that_never_pauses_lets_commit_upstream_return'
# A write that finds its flag reset marks on the path lock; a write admitted inside a hop is on its record.
COMPLETENESS = ('test_write_held_before_admission_across_a_whole_commit',
                'test_first_write_is_admitted_under_the_path_lock',
                'test_same_size_write_behind_an_unchanged_stat_reaches_the_source',
                'test_second_handles_first_write_inside_the_hop',
                'test_write_admitted_inside_the_hop_and_still_in_flight_at_its_end')
RESET = 'open_handle.marked_dirty = False        # re-marking queues on the path lock we hold'
MUTANT = 'open_handle.marked_dirty = bool(met)'
KILL = "assert outcomes == ['admitted', 'waits on the path lock']"


class Red(Exception):
    """A check failed."""


def say(line: str) -> None:
    print(line, flush=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Red(message)
    say(f'  ok    {message}')


def run(label: str, env: dict) -> dict:
    """{test function: [(case, outcome, text)]} for the named tests, from a JUnit XML report."""
    out = Path('.local/remediation-F2') / label
    out.mkdir(parents=True, exist_ok=True)
    xml = (out / 'junit.xml').resolve()
    if xml.exists():
        xml.rename(xml.with_suffix('.previous.xml'))
    ids = [f'{RACING}::{name}' for name in (LIVENESS, *COMPLETENESS)]
    proc = subprocess.run(['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-m', 'unit',
                           '-o', 'junit_family=xunit1', f'--junit-xml={xml}', *ids],
                          text=True, capture_output=True, env=env)
    (out / 'output.log').write_text(proc.stdout + proc.stderr)
    check(xml.exists(), f'{label}: pytest wrote a report')
    found: dict = {}
    for case in ET.parse(xml).getroot().iter('testcase'):
        outcome, text = 'passed', ''
        for child in case:
            if child.tag in ('failure', 'error', 'skipped'):
                outcome, text = child.tag, (child.get('message') or '') + '\n' + (child.text or '')
        found.setdefault(case.get('name', '').split('[')[0], []).append((case.get('name'), outcome, text))
    check(set(found) == {LIVENESS, *COMPLETENESS}, f'{label}: the report holds the {1 + len(COMPLETENESS)} named tests')
    return found


def imports_from(env: dict) -> Path:
    proc = subprocess.run(['uv', 'run', '--frozen', 'python', '-c', 'import terrace; print(terrace.__file__)'],
                          text=True, capture_output=True, env=env)
    return Path(proc.stdout.strip().splitlines()[-1]).resolve() if proc.stdout.strip() else Path()


def main() -> int:
    overlay = Path('src/terrace/engine/overlay.py')
    if not overlay.exists() or not Path(RACING).exists():
        say('could not run: no src/terrace/engine/overlay.py or tests/engine/test_racing.py here')
        return 3
    try:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH', None)
        check(imports_from(env).is_relative_to(Path('src').resolve()), 'the tree run imports terrace from src/')
        head = run('head', env)
        for name, cases in sorted(head.items()):
            check(cases != [] and all(outcome == 'passed' for _, outcome, _ in cases),
                  f'on this tree, {name} passes ({len(cases)} case{"s" if len(cases) > 1 else ""})')

        copy = Path('.local/remediation-F2/mutant/src').resolve()
        shutil.copytree('src', copy, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        planted = copy / 'terrace/engine/overlay.py'
        source = overlay.read_text()
        check(source.count(RESET) == 1, 'the reset in `_commit_hop` is found once')
        planted.write_text(source.replace(RESET, MUTANT))
        check(planted.read_text().count(MUTANT) == 1 and RESET not in planted.read_text(),
              'the mutant landed in the copy')
        mutant_env = dict(env, PYTHONPATH=str(copy))
        check(imports_from(mutant_env).is_relative_to(copy), 'the mutant run imports terrace from the copy')
        mutant = run('mutant', mutant_env)
        (_, outcome, message), = mutant[LIVENESS]
        check(outcome == 'failure' and KILL in message, f'on the mutant, {LIVENESS} fails on `{KILL}`')
        for name in COMPLETENESS:
            check(all(outcome == 'passed' for _, outcome, _ in mutant[name]), f'on the mutant, {name} still passes')
        check(overlay.read_text() == source, 'the tree\'s overlay.py is untouched')
    except Red as red:
        say(f'  RED   {red}')
        say('--- F2-liveness-pin: red')
        return 1
    say('--- F2-liveness-pin: green')
    return 0


if __name__ == '__main__':
    sys.exit(main())
