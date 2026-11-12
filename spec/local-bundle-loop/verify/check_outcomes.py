# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The phase gates of the local-bundle-loop cycle. Run from the worktree root:

    uv run python -I spec/local-bundle-loop/verify/check_outcomes.py MODE

A gate here never trusts an exit code. Each mode names the outcome it expects of each test, by id,
and fails on any other. Phase modes are sums of the part modes, which run alone for a fail-once
mutation or a quick re-check:

    p1 = rows-p1 + figures-p1
    p2 = rows-p2 + probe + figures-p2 + lane
    p3 = rows-p3 + collect + variant + ledger + figures-p3 + lane

Footprint: one pytest run at a time, each `-n auto` (one worker per core, about 150 MB each).
`lane` runs the whole unit lane six times, about four minutes; every other mode is under a minute.
Every child is a foreground `subprocess.run`, so nothing outlives the gate. The helper's tests wait
for each helper process they start and kill it in a `finally`; the gate does not look for leftovers
by name, since a person may have the helper running at a terminal.
"""

# Type annotations
from __future__ import annotations
from typing import Dict, List, Tuple

# Standard libs
import re
import sys
import time
import tempfile
import statistics
import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from collections import Counter

REPO = Path(__file__).resolve().parents[3]
LOOP = 'tests.loop.'
BUDGET = 10.0                        # seconds the cycle may add to the unit lane

HELPER_ROWS = [
    'test_store_answers_then_refuses_after_stop',
    'test_single_bundle_spec_and_layout',
    'test_several_bundles_use_the_sharded_form',
    'test_bundles_are_kilobyte_tarballs_of_their_trees',
    'test_respec_gives_fresh_directories_over_the_same_bucket',
    'test_close_removes_bucket_and_directory',
    'test_failed_setup_leaves_nothing',
    'test_setup_error_in_main_stops_the_store',
    'test_command_exit_tears_down',
    'test_command_failure_passes_status_and_tears_down',
    'test_interrupt_tears_down',
    'test_terminate_tears_down',
    'test_interrupt_tears_down_when_started_with_it_ignored',
    'test_a_second_signal_during_teardown_changes_nothing',
    'test_close_fails_loudly_when_the_directory_survives',
    'test_helper_imports_nothing_of_the_package',
]

SUPPORT_ROWS = [
    'test_blocked_turns_the_exact_failure_into_blocked',
    'test_blocked_passes_another_message_through',
    'test_blocked_passes_a_subclass_through',
    'test_blocked_passes_another_type_through',
    'test_blocked_lets_a_passing_step_pass',
    'test_blocked_refuses_to_pin_a_slip',
    'test_blocked_is_raised_in_one_place',
    'test_every_stage_marker_is_strict_and_names_an_existing_seed',
    'test_the_unit_module_collects_exactly_the_nine_stages',
    'test_oracle_accepts_the_tree_it_was_packed_from',
    'test_oracle_reports_each_kind_of_difference[missing]',
    'test_oracle_reports_each_kind_of_difference[unexpected]',
    'test_oracle_reports_each_kind_of_difference[bytes]',
    'test_oracle_reports_each_kind_of_difference[length]',
    'test_oracle_reports_each_kind_of_difference[kind]',
    'test_oracle_reports_each_kind_of_difference[empty-directory]',
    'test_oracle_refuses_a_member_held_twice',
    'test_oracle_reads_past_an_end_marker',
    'test_oracle_imports_nothing_but_the_standard_library',
    'test_tap_counts_each_kind_once_and_forwards_bytes',
    'test_window_holds_only_its_own_requests',
    'test_window_cannot_be_read_before_it_closes',
    'test_scene_refuses_a_spec_that_bypasses_the_tap',
    'test_tap_refuses_a_chunked_body_and_fails_the_window',
]

VARIANT_ROWS = ['test_the_minio_module_collects_the_same_stages']

# Stage -> the seed its marker must name. GOAL R4's table, as PLAN confirmed it.
STAGE_ROWS = {
    'test_stage_1_reach': 'issues/archive-spec-plumbing.md',
    'test_stage_2_read': 'issues/archive-spec-plumbing.md',
    'test_stage_3_pressure': 'issues/eviction-one-hop.md',
    'test_stage_4_change': 'issues/namespace-sync-redesign.md',
    'test_stage_5_reaggregate': 'issues/archive-finalization-integration.md',
    'test_stage_6_read_back': 'issues/archive-finalization-integration.md',
    'test_stage_7_release': 'issues/shutdown-rescue-gaps.md',
    'test_stage_8_offline_commit': 'issues/offline-commit-command.md',
    'test_stage_9_several_bundles': 'issues/sharded-archive-sources.md',
}

# What the spec parser says today to the spec the helper writes, by bundle count.
REFUSALS = {1: "SpecError: Unknown key(s) in tier 'bundles': location, staging, sync",
            3: "SpecError: Unknown key(s) in tier 'bundles': location, sharded, sync"}

# What `tests/loop/conftest.py` records on each stage's report, and the gate pins: the exception that
# stopped the stage, the type of the exception behind it, and the marker pytest collected. A reason
# string alone can be had by a stage that excuses itself.
BLOCK = 'issues/archive-spec-plumbing.md'
SEVERAL = 'test_stage_9_several_bundles'
MARKER = "xfail ['raises', 'reason', 'strict'] raises=Blocked strict=True args=0"


def expected_stop(name: str) -> str:
    return f'Blocked: {BLOCK}: {REFUSALS[3 if name == SEVERAL else 1].removeprefix("SpecError: ")}'

PROBE = '''
import os, sys
from terrace.tier.spec import OverlaySpec, SpecError
try:
    OverlaySpec.load(os.environ['BUNDLE_LOOP_SPEC'])
except SpecError as error:
    print(f'{type(error).__name__}: {error}')
else:
    print('LOADED')
'''

WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven',
         'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty']

DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left as|left to|not run|never run|deferred|'
                      r'postponed|'
                      r'later cycle|out of scope here', re.I)
SEED = re.compile(r'issues/[a-z0-9-]+\.md')

problems: List[str] = []


def fail(message: str) -> None:
    problems.append(message)
    print(f'gate: FAIL  {message}')


def note(message: str) -> None:
    print(f'gate: {message}')


Outcomes = Dict[Tuple[str, str], Tuple[str, str, float]]
recorded: Dict[Tuple[str, str], Dict[str, List[str]]] = {}      # what the last run's reports carried, by test


def pytest_run(*arguments: str, through: Tuple[str, ...] = ()) -> Tuple[Outcomes, float]:
    """Run pytest once; every test's outcome by (module, name), and the run's wall time."""
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / 'junit.xml'
        started = time.monotonic()
        subprocess.run([*through, sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                        '-o', 'junit_family=xunit1', f'--junitxml={report}', *arguments],
                       cwd=REPO, capture_output=True, text=True)
        elapsed = time.monotonic() - started
        if not report.is_file():
            fail(f'pytest wrote no report for: {" ".join(arguments)}')
            return {}, elapsed
        outcomes: Outcomes = {}
        recorded.clear()
        for case in ElementTree.parse(report).getroot().iter('testcase'):
            outcome, message = 'passed', ''
            properties = recorded.setdefault((case.get('classname', ''), case.get('name', '')), {})
            for item in case.iter('property'):
                properties.setdefault(item.get('name', ''), []).append(item.get('value', ''))
            for child in case:
                if child.tag in ('failure', 'error'):
                    outcome, message = child.tag.replace('failure', 'failed'), child.get('message', '')
                elif child.tag == 'skipped':
                    outcome = 'xfailed' if child.get('type') == 'pytest.xfail' else 'skipped'
                    message = child.get('message', '')
            outcomes[(case.get('classname', ''), case.get('name', ''))] = (outcome, message, float(case.get('time', 0)))
        return outcomes, elapsed


def tally(outcomes: Outcomes, inside: bool) -> Counter:
    """Counts by outcome for the tests inside tests/loop, or for the rest."""
    return Counter(outcome for (module, _), (outcome, _, _) in outcomes.items() if module.startswith(LOOP) == inside)


def expect_passed(outcomes: Outcomes, module: str, names: List[str]) -> None:
    for name in names:
        found = outcomes.get((module, name))
        if found is None:
            fail(f'{module}::{name} did not run')
        elif found[0] != 'passed':
            fail(f'{module}::{name} is {found[0]}, expected passed')


def expect_only(outcomes: Outcomes, allowed: Dict[str, int]) -> None:
    """Every outcome other than passed appears exactly as often as `allowed` says."""
    counts = Counter(outcome for outcome, _, _ in outcomes.values())
    for outcome in ('failed', 'error', 'skipped', 'xfailed'):
        if counts[outcome] != allowed.get(outcome, 0):
            names = [f'{module}::{name}' for (module, name), found in outcomes.items() if found[0] == outcome]
            fail(f'{counts[outcome]} {outcome}, expected {allowed.get(outcome, 0)}: {names[:12]}')


def rows_p1() -> None:
    outcomes, _ = pytest_run('-n', 'auto', '-m', 'unit', 'tests/loop/test_helper.py')
    expect_passed(outcomes, 'tests.loop.test_helper', HELPER_ROWS)
    expect_only(outcomes, {})
    note(f'rows-p1: {tally(outcomes, True)["passed"]} passed in tests/loop/test_helper.py')


def expect_stages(outcomes: Outcomes, module: str) -> None:
    """Each of the nine stages in `module` is an expected failure, stopped by the parser and by nothing else."""
    for name, seed in STAGE_ROWS.items():
        outcome, message, _ = outcomes.get((module, name), ('absent', '', 0.0))
        if outcome != 'xfailed':
            fail(f'stage {name} is {outcome}, expected xfailed')
            continue
        if not message.startswith(f'{seed}: '):
            fail(f'stage {name} names {message!r}, expected its owner {seed}')
        if not (REPO / seed).is_file():
            fail(f'{seed} does not exist')
        seen = recorded.get((module, name), {})
        for key, want in (('stopped', expected_stop(name)), ('cause', 'SpecError'), ('marker', MARKER)):
            if seen.get(key) != [want]:
                fail(f'stage {name}: {key} is {seen.get(key)}, expected [{want!r}]')
    stages = [name for (found, name) in outcomes if found == module]
    if sorted(stages) != sorted(STAGE_ROWS):
        fail(f'{module} ran {sorted(stages)}, expected the nine stages')


def rows_p2(label: str = 'rows-p2', more: List[str] = []) -> None:
    outcomes, _ = pytest_run('-n', 'auto', '-m', 'unit', 'tests/loop')
    expect_passed(outcomes, 'tests.loop.test_helper', HELPER_ROWS)
    expect_passed(outcomes, 'tests.loop.test_support', SUPPORT_ROWS + more)
    expect_stages(outcomes, 'tests.loop.test_scenario')
    expect_only(outcomes, {'xfailed': len(STAGE_ROWS)})
    spent = sum(seconds for (module, _), (_, _, seconds) in outcomes.items() if module.startswith(LOOP))
    # Printed, never judged: the sum counts each worker's store start and stop and is not wall time.
    # `lane` gives the verdict on what the cycle adds.
    note(f'{label}: {dict(tally(outcomes, True))} in tests/loop; test time summed over workers {spent:.1f} s')


def variant() -> None:
    """
    The MinIO module's wiring, with the helper's own store standing in for MinIO: its fixtures, its
    tap and its nine stages run, and stop where the unit module's do. This is not a run against
    MinIO, and nothing here says the variant has run.
    """
    wiring = ('TERRACE_TEST_MINIO_ENDPOINT="$BUNDLE_LOOP_ENDPOINT" TERRACE_TEST_MINIO_ACCESS_KEY=test '
              'TERRACE_TEST_MINIO_SECRET_KEY=test TERRACE_TEST_MINIO_REGION=us-east-1 exec "$@"')
    outcomes, _ = pytest_run('-m', 'integration', 'tests/loop/test_scenario_minio.py',
                             through=(sys.executable, str(REPO / 'tools' / 'bundle_loop.py'), '--',
                                      'sh', '-c', wiring, 'sh'))
    expect_stages(outcomes, 'tests.loop.test_scenario_minio')
    expect_only(outcomes, {'xfailed': len(STAGE_ROWS)})
    note(f'variant: {dict(tally(outcomes, True))} over the helper\'s store; MinIO itself has not run')


def rows_p3() -> None:
    rows_p2('rows-p3', VARIANT_ROWS)


def probe() -> None:
    """Stand the loop up through the helper's command line and hand its spec to the spec parser."""
    for bundles, refusal in REFUSALS.items():
        done = subprocess.run([str(REPO / 'tools' / 'temp_site.sh'), sys.executable,
                               str(REPO / 'tools' / 'bundle_loop.py'), '-n', str(bundles), '--',
                               sys.executable, '-c', PROBE], cwd=REPO, capture_output=True, text=True)
        lines = done.stdout.split('\n')
        if done.returncode != 0 or len(lines) < 2:
            fail(f'probe -n {bundles}: the helper exited {done.returncode}: {done.stderr.strip()[-300:]}')
        elif lines[1] != refusal:
            fail(f'probe -n {bundles}: the parser said {lines[1]!r}, expected {refusal!r}')
        elif Path(lines[0]).parent.exists():
            fail(f'probe -n {bundles}: {Path(lines[0]).parent} outlived the helper')
        else:
            note(f'probe -n {bundles}: {lines[1]}')


def collected(marker: str) -> List[str]:
    done = subprocess.run([sys.executable, '-m', 'pytest', '-m', marker, '--collect-only', '-q',
                           '-p', 'no:cacheprovider'], cwd=REPO, capture_output=True, text=True)
    return [line for line in done.stdout.splitlines() if '::' in line]


def collect() -> None:
    ids = collected('integration')
    variant = sorted(line.split('::', 1)[1] for line in ids if line.startswith('tests/loop/test_scenario_minio.py::'))
    if variant != sorted(STAGE_ROWS):
        fail(f'-m integration collects {variant} from tests/loop/test_scenario_minio.py, expected the nine stages')
    else:
        note(f'collect: -m integration lists the nine stages of the MinIO variant ({len(ids)} integration tests)')


def figures(phase: str) -> None:
    """AGENTS.md states the counts this tree has, at every place it states one."""
    text = (REPO / 'AGENTS.md').read_text()
    unit, integration = len(collected('unit')), len(collected('integration'))
    files = len([path for path in (REPO / 'tests').rglob('*.py') if '__pycache__' not in path.parts])
    stated = [
        (r'uv run pytest -m unit +# (\d+) tests', (unit, )),
        (r'uv run pytest -m integration +# (\d+) tests', (integration, )),
        (r'\| `tests/` \| (\d+) files, (\d+) tests \((\d+) `unit`, (\d+) `integration`\)',
         (files, unit + integration, unit, integration)),
        (r'\((\d+) skipped, (\d+) deselected', (integration, unit)),
        (r'\*\*(\d+) unit tests in', (unit, )),
    ]
    for pattern, want in stated:
        found = re.search(pattern, text)
        if found is None:
            fail(f'figures: AGENTS.md has no line matching {pattern!r}')
        elif tuple(int(group) for group in found.groups()) != want:
            fail(f'figures: AGENTS.md says {found.group(0)!r}, the tree has {want}')
    prose = re.search(r'(\w+) tests — the entire drain', text)
    word = WORDS[integration] if integration < len(WORDS) else ''
    if prose is None or prose.group(1).lower() not in (str(integration), word):
        fail(f'figures: the sentence on tests CI never runs does not say {integration}')
    if 'tools/bundle_loop.py' not in text:
        fail('figures: AGENTS.md does not name tools/bundle_loop.py')
    if phase != 'p1':
        if 'tests/loop/test_scenario.py' not in text:
            fail('figures: AGENTS.md does not name tests/loop/test_scenario.py')
        if not re.search(rf'\b({len(STAGE_ROWS)}|{WORDS[len(STAGE_ROWS)]}) (strict )?expected failures\b', text):
            fail(f'figures: AGENTS.md does not state the {len(STAGE_ROWS)} expected failures')
    if phase == 'p3' and 'tests/loop/test_scenario_minio.py' not in text:
        fail('figures: AGENTS.md does not name tests/loop/test_scenario_minio.py')
    note(f'figures-{phase}: {unit} unit, {integration} integration, {files} files under tests/')


def ledger() -> None:
    """Every deferral a phase body states names a seed that exists and that ROADMAP.md indexes."""
    lines = (REPO / 'spec' / 'local-bundle-loop' / 'TECH.md').read_text().splitlines()
    body = lines[lines.index('---', 1) + 1:]
    items: List[str] = []
    for line in body:
        if line.startswith('#'):
            continue                                 # a heading is a title, never a deferral
        if line.startswith(('- ', '* ')) or not line.strip() or not items:
            items.append(line)
        else:
            items[-1] += ' ' + line.strip()
    roadmap = (REPO / 'ROADMAP.md').read_text()
    deferrals = [item for item in items if DEFERRAL.search(item)]
    for item in deferrals:
        seeds = SEED.findall(item)
        if not seeds:
            fail(f'ledger: a deferral names no seed: {item.strip()[:120]}')
        for seed in seeds:
            if not (REPO / seed).is_file():
                fail(f'ledger: {seed} does not exist')
            elif seed not in roadmap:
                fail(f'ledger: ROADMAP.md has no entry for {seed}')
    lane = (REPO / 'issues' / 'ci-integration-lane.md').read_text()
    if 'tests/loop/test_scenario_minio.py' not in lane:
        fail('ledger: issues/ci-integration-lane.md does not record the MinIO variant that has never run')
    note(f'ledger: {len(deferrals)} deferral(s) in TECH.md, each with a seed and a roadmap entry')


def lane() -> None:
    """The unit lane with and without tests/loop, three times each, alternating."""
    before: List[float] = []
    after: List[float] = []
    for _ in range(3):
        without, seconds = pytest_run('-n', 'auto', '-m', 'unit', '--ignore=tests/loop')
        before.append(seconds)
        whole, seconds = pytest_run('-n', 'auto', '-m', 'unit')
        after.append(seconds)
        base, rest, loop = tally(without, False), tally(whole, False), tally(whole, True)
        if base['failed'] or base['error'] or not base['passed']:
            fail(f'lane: the suite without tests/loop is not green: {dict(base)}')
        if rest != base:
            fail(f'lane: tests outside tests/loop changed outcome: {dict(base)} without, {dict(rest)} with')
        if loop['xfailed'] != len(STAGE_ROWS) or loop['failed'] or loop['error'] or loop['skipped']:
            fail(f'lane: tests/loop reports {dict(loop)}, expected {len(STAGE_ROWS)} xfailed and the rest passed')
    low, high = statistics.median(before), statistics.median(after)
    note(f'lane before: {low:.1f} s median of {[round(value, 1) for value in before]}; {dict(base)}')
    note(f'lane after:  {high:.1f} s median of {[round(value, 1) for value in after]}; '
         f'{dict(rest + loop)}')
    note(f'lane added:  {high - low:.1f} s, budget {BUDGET:.0f} s')
    if high - low > BUDGET:
        fail(f'lane: tests/loop adds {high - low:.1f} s to the unit lane, over {BUDGET:.0f} s')


MODES = {
    'rows-p1': [rows_p1], 'rows-p2': [rows_p2], 'rows-p3': [rows_p3], 'variant': [variant], 'probe': [probe],
    'collect': [collect], 'ledger': [ledger],
    'lane': [lane],
    'figures-p1': [lambda: figures('p1')], 'figures-p2': [lambda: figures('p2')], 'figures-p3': [lambda: figures('p3')],
    'p1': [rows_p1, lambda: figures('p1')],
    'p2': [rows_p2, probe, lambda: figures('p2'), lane],
    'p3': [rows_p3, collect, variant, ledger, lambda: figures('p3'), lane],
}


def main(argv: List[str]) -> int:
    if len(argv) != 1 or argv[0] not in MODES:
        print(f'usage: check_outcomes.py {{{"|".join(MODES)}}}', file=sys.stderr)
        return 2
    for check in MODES[argv[0]]:
        check()
    if problems:
        print(f'gate: {argv[0]} FAILED, {len(problems)} problem(s)')
        return 1
    print(f'gate: {argv[0]} ok')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
