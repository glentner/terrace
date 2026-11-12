# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Class checker for review cycle 1, F1: no `tests/loop` test's outcome depends on socket timing.

Run from the worktree root:

    uv run python -I spec/local-bundle-loop/remediation/F1-socket-timing.py

It repeats every `tests/loop` test that makes a socket exchange (the tap's, and the helper's
refused-connection checks after teardown) in one `-n auto` pytest run, while one CPU-bound process
per core runs beside it, and asserts that each named test ran exactly its repeat count and passed
every time. It judges by test id from pytest's JUnit report and never reads an exit code.

The repeats come from a plugin this script writes to a temporary directory and loads with `-p`; the
suite never loads it. The plugin records `f1-repeat` on every repeated test, and the checker asserts
it saw that property, so a run without the plugin cannot pass as a run with it.
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

# Test -> repeats. The chunked refusal raced at about 2 % a run under load on the reviewed commit, so
# it gets enough repeats to catch that with near certainty; the rest of the family gets enough to show
# it is not of the same kind.
REPEATS: Dict[str, Dict[str, int]] = {
    'tests/loop/test_support.py': {
        'test_tap_refuses_a_chunked_body_and_fails_the_window': 300,
        'test_tap_reads_a_long_chunked_body_before_refusing_it': 100,
        'test_tap_counts_each_kind_once_and_forwards_bytes': 30,
        'test_window_holds_only_its_own_requests': 30,
        'test_window_cannot_be_read_before_it_closes': 30,
        'test_scene_refuses_a_spec_that_bypasses_the_tap': 30,
    },
    'tests/loop/test_helper.py': {
        'test_store_answers_then_refuses_after_stop': 10,
        'test_setup_error_in_main_stops_the_store': 10,
        'test_command_exit_tears_down': 5,
        'test_command_failure_passes_status_and_tears_down': 5,
        'test_interrupt_tears_down': 5,
        'test_terminate_tears_down': 5,
        'test_interrupt_tears_down_when_started_with_it_ignored': 5,
        'test_a_second_signal_during_teardown_changes_nothing': 20,
    },
}

PLUGIN = '''
import os, json
COUNTS = json.loads(os.environ['F1_REPEAT'])

def pytest_generate_tests(metafunc):
    count = COUNTS.get(metafunc.function.__name__)
    if count:
        metafunc.fixturenames.append('f1_round')
        metafunc.parametrize('f1_round', range(count), ids=lambda index: f'r{index}')

def pytest_runtest_setup(item):
    if 'f1_round' in getattr(getattr(item, 'callspec', None), 'params', {}):
        item.user_properties.append(('f1-repeat', 'yes'))
'''


def main() -> int:
    counts = {name: count for names in REPEATS.values() for name, count in names.items()}
    # Selected by name, not node id: a named test that is missing then reads as "did not run" here
    # instead of stopping the whole run at collection.
    ids = [*REPEATS, '-k', ' or '.join(counts)]
    burners: List[subprocess.Popen] = []
    problems: List[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / 'f1_repeat.py').write_text(PLUGIN)
        report = Path(scratch) / 'junit.xml'
        env = dict(os.environ, F1_REPEAT=json.dumps(counts),
                   PYTHONPATH=os.pathsep.join(filter(None, [scratch, os.environ.get('PYTHONPATH')])))
        try:
            for _ in range(os.cpu_count() or 4):
                burners.append(subprocess.Popen([sys.executable, '-c', 'while True: pass']))
            started = time.monotonic()
            subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'f1_repeat',
                            '-n', 'auto', '-o', 'junit_family=xunit1', f'--junitxml={report}', *ids],
                           cwd=REPO, env=env, capture_output=True, text=True)
            elapsed = time.monotonic() - started
        finally:
            for burner in burners:
                burner.kill()
            for burner in burners:
                burner.wait()
        if not report.is_file():
            print('F1: FAIL  pytest wrote no report')
            return 1
        seen: Dict[str, Counter] = {name: Counter() for name in counts}
        failures: Dict[str, List[str]] = {}
        marked = 0
        for case in ElementTree.parse(report).getroot().iter('testcase'):
            name = case.get('name', '').split('[', 1)[0]
            if name not in seen:
                continue
            marked += any(item.get('name') == 'f1-repeat' for item in case.iter('property'))
            outcome = 'passed'
            for child in case:
                if child.tag in ('failure', 'error', 'skipped'):
                    outcome = child.tag
                    failures.setdefault(name, []).append(child.get('message', '')[:160])
            seen[name][outcome] += 1
    total = sum(counts.values())
    if marked != total:
        problems.append(f'{marked} of {total} repeats carried the plugin\'s mark')
    for name, count in counts.items():
        tally = seen[name]
        line = f'{name}: {tally["passed"]}/{count} passed'
        bad = {key: value for key, value in tally.items() if key != 'passed'}
        if bad or tally['passed'] != count:
            line += f', {dict(bad)}; first: {failures.get(name, ["(did not run)"])[:2]}'
            problems.append(line)
        print(f'F1: {line}')
    print(f'F1: {total} repeats of {len(counts)} tests in {elapsed:.0f} s beside {len(burners)} CPU burners')
    if problems:
        print(f'F1: FAILED, {len(problems)} problem(s)')
        return 1
    print('F1: ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
