# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Class checker for review cycle 2, A1, and the Linux CI failure of the started-ignored interrupt test
at `14a2b56`: every signal the helper handles ends in teardown and exit on the first one, whatever it
interrupts and whatever the client library's retry mode, and a signal during teardown never cuts it
short.

Run from the worktree root:

    uv run python -I spec/local-bundle-loop/remediation/F2-signal-teardown.py

It runs `tests/loop/test_helper.py` once with `BUNDLE_LOOP_EVERY_SIGNAL=1`, which widens the window
pin from the suite's six representatives to every window, signal and retry mode (48 cases), and in
the same run repeats the four tests that signal the helper while it waits, beside one CPU-bound
process per core. It asserts by test id from pytest's JUnit report that each of the 48 cases ran and
passed, and that each repeated test passed its full count; it never reads an exit code. The repeats
come from a plugin this script writes to a temporary directory; it marks each repeat, and the
checker asserts it saw every mark.
"""

# Type annotations
from __future__ import annotations
from typing import Dict, List

# Standard libs
import os
import sys
import json
import time
import tempfile
import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from collections import Counter

REPO = Path(__file__).resolve().parents[3]
MATRIX = 'test_a_signal_in_any_window_tears_down_on_the_first'
WINDOWS = ('setup-1', 'setup-2', 'teardown-3', 'teardown-4', 'teardown-5', 'second', 'thread', 'command')
CASES = {f'{window}-{name}-{mode}' for window in WINDOWS for name in ('int', 'term', 'hup')
         for mode in ('legacy', 'standard')}
REPEATS = {
    'test_interrupt_tears_down': 25,
    'test_terminate_tears_down': 25,
    'test_interrupt_tears_down_when_started_with_it_ignored': 25,
    'test_a_second_signal_during_teardown_changes_nothing': 25,
}

PLUGIN = '''
import os, json
COUNTS = json.loads(os.environ['F2_REPEAT'])

def pytest_generate_tests(metafunc):
    count = COUNTS.get(metafunc.function.__name__)
    if count:
        metafunc.fixturenames.append('f2_round')
        metafunc.parametrize('f2_round', range(count), ids=lambda index: f'r{index}')

def pytest_runtest_setup(item):
    if 'f2_round' in getattr(getattr(item, 'callspec', None), 'params', {}):
        item.user_properties.append(('f2-repeat', 'yes'))
'''


def main() -> int:
    problems: List[str] = []
    burners: List[subprocess.Popen] = []
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / 'f2_repeat.py').write_text(PLUGIN)
        report = Path(scratch) / 'junit.xml'
        env = dict(os.environ, F2_REPEAT=json.dumps(REPEATS), BUNDLE_LOOP_EVERY_SIGNAL='1',
                   PYTHONPATH=os.pathsep.join(filter(None, [scratch, os.environ.get('PYTHONPATH')])))
        try:
            for _ in range(os.cpu_count() or 4):
                burners.append(subprocess.Popen([sys.executable, '-c', 'while True: pass']))
            started = time.monotonic()
            subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'f2_repeat',
                            '-n', 'auto', '-o', 'junit_family=xunit1', f'--junitxml={report}',
                            'tests/loop/test_helper.py', '-k', ' or '.join([MATRIX, *REPEATS])],
                           cwd=REPO, env=env, capture_output=True, text=True)
            elapsed = time.monotonic() - started
        finally:
            for burner in burners:
                burner.kill()
            for burner in burners:
                burner.wait()
        if not report.is_file():
            print('F2: FAIL  pytest wrote no report')
            return 1
        matrix: Dict[str, str] = {}
        seen: Dict[str, Counter] = {name: Counter() for name in REPEATS}
        marked = 0
        for case in ElementTree.parse(report).getroot().iter('testcase'):
            name, _, param = case.get('name', '').partition('[')
            outcome = next((child.tag for child in case if child.tag in ('failure', 'error', 'skipped')), 'passed')
            if outcome != 'passed':
                problems.append(f'{case.get("name")}: {outcome}')
            if name == MATRIX:
                matrix[param.rstrip(']')] = outcome
            elif name in seen:
                marked += any(item.get('name') == 'f2-repeat' for item in case.iter('property'))
                seen[name][outcome] += 1
    if set(matrix) != CASES:
        problems.append(f'window pin ran {len(matrix)} cases, expected {len(CASES)}; '
                        f'missing {sorted(CASES - set(matrix))[:6]}')
    passed = sum(outcome == 'passed' for outcome in matrix.values())
    print(f'F2: {MATRIX}: {passed}/{len(CASES)} cases passed')
    total = sum(REPEATS.values())
    if marked != total:
        problems.append(f'{marked} of {total} repeats carried the plugin\'s mark')
    for name, count in REPEATS.items():
        print(f'F2: {name}: {seen[name]["passed"]}/{count} passed')
        if seen[name]['passed'] != count:
            problems.append(f'{name}: {dict(seen[name])} of {count}')
    print(f'F2: {len(matrix) + total} cases in {elapsed:.0f} s beside {len(burners)} CPU burners')
    if problems:
        for problem in problems[:20]:
            print(f'F2: {problem}')
        print(f'F2: FAILED, {len(problems)} problem(s)')
        return 1
    print('F2: ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
