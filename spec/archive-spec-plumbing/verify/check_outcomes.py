# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The phase gates of the archive-spec-plumbing cycle. Run from the worktree root:

    uv run python -I spec/archive-spec-plumbing/verify/check_outcomes.py MODE

A gate here never trusts an exit code. Each mode names the tests it expects by id and requires each
to have passed; the whole unit lane must show no failure or error, exactly the expected failures the
phase leaves, and at least as many passes as the baseline plus the rows the cycle has added. Phase
modes are sums of part modes, which run alone for a fail-once mutation or a quick re-check:

    p1 = rows-p1 + ids + lane-p1
    p2 = rows-p2 + mutants-p2 + ids + lane-p2
    p3 = rows-p3 + mutants-p3 + ids + lane-p3
    p4 = rows-p4 + mutants-p4 + stages + harness + examples + drive + ids + lane-p4
    p5 = rows-p5 + mutants-p5 + stages + harness + examples + collect + docs + ledger + figures + ids + lane-p5
    r10 = the mount test on a Linux host with /dev/fuse (never part of a phase mode)

The lane also requires every test that passed at the base (verify/base-passed.txt, frozen at plan) to
pass now: no existing test may be deleted, renamed or weakened into a skip. `harness` holds the loop's
support files byte-for-byte and the shared steps of tests/loop/stages.py by AST against the base.
`mutants` plants each of the cycle's mutants (verify/archive_cycle_mutants.py) and requires the rows
that guard it to fail, after confirming the mutant took effect.

Every phase mode re-runs the rows of the phases before it, so a later phase cannot quietly undo an
earlier one. `ids` scans the lines the cycle adds to src/, tests/ and README.md, staged against the
base: `$GATE_BASE` when set, else `git merge-base HEAD origin/main`. It never names a branch commit,
so it survives the squash.

Footprint: one pytest run at a time, each `-n auto` (one worker per core, about 150 MB each) except
the serial mutant runs, plus one helper process for `drive`. Every child is a foreground `subprocess.run`, so nothing outlives the
gate. `lane-*` is the whole unit lane, about 40 s; the other modes take under 30 s each. The log of
the last run is kept at .local/archive-spec-plumbing-gate.log under the main checkout, never deleted.
"""

# Type annotations
from __future__ import annotations
from typing import Dict, List, Tuple

# Standard libs
import os
import re
import ast
import sys
import tempfile
import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from collections import Counter

REPO = Path(__file__).resolve().parents[3]
LOG = REPO.parent.parent / '.local' / 'archive-spec-plumbing-gate.log' \
    if REPO.parent.name == '.worktrees' else REPO / '.local' / 'archive-spec-plumbing-gate.log'
BASELINE = 842                       # unit tests passing at 184115f, with the nine stages xfailed
FORMATS = ['zip', 'tar']
STAGED = ['tar.gz']
CALLS = ['mkdir', 'create', 'unlink', 'rmdir', 'rename', 'rename-over', 'chmod', 'chown', 'utimens',
         'truncate', 'open-wronly', 'open-rdwr', 'open-rdonly-trunc', 'open-wronly-append',
         'write-rdonly-handle', 'truncate-handle']
WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven',
         'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty',
         'twenty-one', 'twenty-two']

SOURCE = 'tests.engine.test_archive_source'
READONLY = 'tests.engine.test_archive_readonly'
ADAPTER = 'tests.tier.test_archive'
SPEC = 'tests.tier.test_spec'
R10 = 'test_zip_source_mount_readdir_lists_top_level_members'

SYMLINK_ROWS = [f'test_symlinked_root_overlap_is_refused[{side}]' for side in ('source', 'stage', 'both')] + [
    'test_archive_under_a_symlinked_directory_overlaps_its_stage',
    'test_symlinked_state_dir_inside_a_tier_is_refused',
    'test_state_dir_under_a_symlinked_tier_is_refused']

# The rows each phase adds: module -> test ids. Each must pass in its phase and in every later one.
ROWS: Dict[str, Dict[str, List[str]]] = {
    'p1': {
        SOURCE: [f'{name}[{fmt}]' for fmt in FORMATS for name in (
                     'test_start_builds_an_archive_tier_over_the_location',
                     'test_restart_reads_ok_and_writes_nothing_beside_the_archive',
                     'test_listing_and_reads_match_the_archive')]
                + ['test_missing_archive_is_refused_at_start',
                   'test_moved_location_is_refused_at_restart',
                   'test_rewritten_archive_is_refused_at_restart'],
        'tests.state.test_registry': ['test_enlist_reads_ok_with_no_marker',
                                      'test_enlist_refuses_changed_meta'],
        ADAPTER: [f'{name}[{fmt}]' for fmt in FORMATS for name in (
                      'test_directory_without_mode_is_searchable', 'test_duplicate_member_last_wins')]
                 + ['test_hard_link_reads_its_target', 'test_sparse_member_reads_its_bytes'],
    },
    'p2': {
        SOURCE: [f'{name}[{fmt}]' for fmt in STAGED for name in (
                     'test_start_builds_an_archive_tier_over_the_location',
                     'test_restart_reads_ok_and_writes_nothing_beside_the_archive',
                     'test_listing_and_reads_match_the_archive')]
                + ['test_staged_bundle_is_one_counted_file_in_the_durable_root',
                   'test_staged_bundle_is_not_listed',
                   'test_bundle_pull_leaves_no_intent',
                   'test_second_start_does_not_pull_or_count_again',
                   'test_interrupted_bundle_count_is_recovered_at_restart'],
        'tests.tier.test_posix': ['test_usage_counts_staged_bundles'],
    },
    'p3': {
        READONLY: [f'test_mutating_call_fails_erofs[{fmt}-{call}]' for fmt in FORMATS + STAGED for call in CALLS]
                  + [f'test_archive_unchanged_after_refusals_and_drain[{fmt}]' for fmt in FORMATS + STAGED]
                  + ['test_every_public_engine_method_is_classified'],
    },
    'p4': {
        SPEC: ['test_archive_examples_load[direct-update-zip]', 'test_archive_examples_load[staged-tar-gz]',
               'test_archive_tier_carries_location_and_rate',
               'test_sync_refused_on_direct_update[zip]', 'test_sync_refused_on_direct_update[tar]',
               'test_staging_refused_naming_the_durable_tier', 'test_staging_refused_without_a_durable_tier',
               'test_sharded_refused_naming_its_seed']
              + [f'test_archive_refused_at_stage_position[{fmt}]' for fmt in FORMATS + STAGED]
              + [f'test_archive_stack_rule[{case}]' for case in ('no-stage', 'node-stage', 's3-stage', 'second-shared')]
              + SYMLINK_ROWS + ['test_stage_root_containing_the_archive_overlaps'],
        SOURCE: ['test_parsed_archive_spec_serves_a_read_past_a_full_stage'],
    },
    'p5': {
        'tests.fuse.test_adapter': ['test_zip_source_readdir_through_the_adapter'],
    },
}
# Rows that exist before the cycle and must keep passing once the phase that changes them lands.
KEPT: Dict[str, Dict[str, List[str]]] = {
    'p4': {
        'tests.loop.test_helper': ['test_single_bundle_spec_and_layout', 'test_several_bundles_use_the_sharded_form',
                                   'test_helper_imports_nothing_of_the_package'],
        'tests.loop.test_support': ['test_every_stage_marker_is_strict_and_names_an_existing_seed',
                                    'test_the_unit_module_collects_exactly_the_nine_stages',
                                    'test_the_minio_module_collects_the_same_stages'],
        SPEC: ['test_tier_type_validation', 'test_unknown_keys_rejected'],
    },
}
PHASES = ['p1', 'p2', 'p3', 'p4', 'p5']

MARKER = "xfail ['raises', 'reason', 'strict'] raises=Blocked strict=True args=0"
SHARDED = ("Sharded archive sources are not yet supported (tier 'bundles'); "
           'see issues/sharded-archive-sources.md')
STAGE_OWNERS = {
    'test_stage_3_pressure': 'issues/eviction-one-hop.md',
    'test_stage_4_change': 'issues/namespace-sync-redesign.md',
    'test_stage_5_reaggregate': 'issues/archive-finalization-integration.md',
    'test_stage_6_read_back': 'issues/archive-finalization-integration.md',
    'test_stage_7_release': 'issues/shutdown-rescue-gaps.md',
    'test_stage_8_offline_commit': 'issues/offline-commit-command.md',
    'test_stage_9_several_bundles': 'issues/sharded-archive-sources.md',
}
EROFS = re.compile(r"Blocked: issues/archive-journaling-and-locking\.md: \[Errno 30\] Read-only file system: "
                   r"'/bundle-000/[^']+'")
STOPS: Dict[str, Tuple[str, re.Pattern]] = {
    'test_stage_3_pressure': ('AssertionError', re.compile(
        r'Blocked: issues/eviction-one-hop\.md: nothing was pushed out of the fastest tier(\n.*)?', re.S)),
    **{name: ('OSError', EROFS) for name in ('test_stage_4_change', 'test_stage_5_reaggregate',
                                             'test_stage_6_read_back', 'test_stage_7_release',
                                             'test_stage_8_offline_commit')},
    'test_stage_9_several_bundles': ('SpecError', re.compile(re.escape(f'Blocked: issues/sharded-archive-sources.md: {SHARDED}'))),
}
PASSING_STAGES = {'test_stage_1_reach': 'spec/archive-spec-plumbing/GOAL.md',
                  'test_stage_2_read': 'spec/archive-spec-plumbing/GOAL.md'}

Outcomes = Dict[Tuple[str, str], Tuple[str, str]]
recorded: Dict[Tuple[str, str], Dict[str, List[str]]] = {}
problems: List[str] = []
lines: List[str] = []


def note(text: str) -> None:
    lines.append(text)
    print(text, flush=True)


def fail(text: str) -> None:
    problems.append(text)
    note(f'FAIL {text}')


def pytest_run(*arguments: str, env: Dict[str, str] | None = None) -> Outcomes:
    """Run pytest once; every test's outcome and message by (classname, name)."""
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / 'junit.xml'
        done = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                               '-o', 'junit_family=xunit1', f'--junitxml={report}', *arguments],
                              cwd=REPO, capture_output=True, text=True, env=env)
        if not report.is_file():
            fail(f'pytest wrote no report for {" ".join(arguments)}: {done.stdout[-400:]}{done.stderr[-400:]}')
            return {}
        outcomes: Outcomes = {}
        recorded.clear()
        for case in ElementTree.parse(report).getroot().iter('testcase'):
            key = (case.get('classname', ''), case.get('name', ''))
            outcome, message = 'passed', ''
            properties = recorded.setdefault(key, {})
            for item in case.iter('property'):
                properties.setdefault(item.get('name', ''), []).append(item.get('value', ''))
            for child in case:
                if child.tag in ('failure', 'error'):
                    outcome, message = ('failed' if child.tag == 'failure' else 'error'), child.get('message', '')
                elif child.tag == 'skipped':
                    outcome = 'xfailed' if child.get('type') == 'pytest.xfail' else 'skipped'
                    message = child.get('message', '')
            outcomes[key] = (outcome, message)
        return outcomes


def rows_through(phase: str) -> Dict[str, List[str]]:
    """Every named row of `phase` and the phases before it, by module."""
    merged: Dict[str, List[str]] = {}
    for each in PHASES[:PHASES.index(phase) + 1]:
        for table in (ROWS, KEPT):
            for module, names in table.get(each, {}).items():
                merged.setdefault(module, []).extend(names)
    return merged


def added_rows(phase: str) -> int:
    return sum(len(names) for each in PHASES[:PHASES.index(phase) + 1] for names in ROWS.get(each, {}).values())


def expect_passed(outcomes: Outcomes, module: str, names: List[str]) -> int:
    good = 0
    for name in names:
        found = outcomes.get((module, name))
        if found is None:
            fail(f'{module}::{name} did not run')
        elif found[0] != 'passed':
            fail(f'{module}::{name} is {found[0]}, expected passed: {found[1][:200]}')
        else:
            good += 1
    return good


def rows(phase: str) -> None:
    merged = rows_through(phase)
    files = sorted({'tests/' + '/'.join(module.split('.')[1:]) + '.py' for module in merged})
    outcomes = pytest_run('-n', 'auto', '-m', 'unit', *files)
    good = sum(expect_passed(outcomes, module, names) for module, names in merged.items())
    note(f'rows-{phase}: {good} of {sum(len(names) for names in merged.values())} named rows passed '
         f'in {len(files)} file(s)')


def lane(phase: str) -> None:
    """The whole unit lane: no failure, the phase's exact expected failures, and enough passes."""
    outcomes = pytest_run('-n', 'auto', '-m', 'unit')
    counts = Counter(outcome for outcome, _ in outcomes.values())
    for outcome in ('failed', 'error', 'skipped'):
        names = [f'{module}::{name}' for (module, name), found in outcomes.items() if found[0] == outcome
                 and not (outcome == 'skipped' and found[1].startswith('libfuse cannot be loaded'))]
        if names:
            fail(f'lane-{phase}: {len(names)} {outcome}: {names[:12]}')
    xfailed = sorted(name for (module, name), found in outcomes.items() if found[0] == 'xfailed')
    stages = ['test_stage_1_reach', 'test_stage_2_read'] if phase in ('p1', 'p2', 'p3') else []
    want = sorted(stages + list(STAGE_OWNERS))
    if xfailed != want:
        fail(f'lane-{phase}: expected failures are {xfailed}, expected {want}')
    kept = [line for line in (REPO / 'spec' / 'archive-spec-plumbing' / 'verify' / 'base-passed.txt').read_text()
            .splitlines() if line and not line.startswith('#')]
    lost = [name for name in kept if outcomes.get(tuple(name.split('::', 1)), ('absent', ''))[0] != 'passed']
    if lost:
        fail(f'lane-{phase}: {len(lost)} test(s) that passed at the base do not pass now: {lost[:12]}')
    floor = BASELINE + added_rows(phase) + (0 if stages else 2)
    if counts['passed'] < floor:
        fail(f'lane-{phase}: {counts["passed"]} passed, fewer than the floor {floor} '
             f'(baseline {BASELINE} + {added_rows(phase)} named rows{"" if stages else " + 2 stages"})')
    note(f'lane-{phase}: {dict(counts)}; floor {floor}')


def stages() -> None:
    """Stages 1-2 pass unmarked; 3-9 stop where the plan pins them, under their own owners."""
    before = len(problems)
    outcomes = pytest_run('-n', 'auto', '-m', 'unit', 'tests/loop/test_scenario.py')
    module = 'tests.loop.test_scenario'
    for name, seed in PASSING_STAGES.items():
        outcome, message = outcomes.get((module, name), ('absent', ''))
        if outcome != 'passed':
            fail(f'stage {name} is {outcome}, expected passed: {message[:200]}')
        if recorded.get((module, name), {}).get('marker') != ['']:
            fail(f'stage {name} carries a marker: {recorded.get((module, name), {}).get("marker")}')
    for name, owner in STAGE_OWNERS.items():
        outcome, message = outcomes.get((module, name), ('absent', ''))
        if outcome != 'xfailed':
            fail(f'stage {name} is {outcome}, expected xfailed: {message[:200]}')
            continue
        if not message.startswith(f'{owner}: '):
            fail(f'stage {name} names {message!r}, expected its owner {owner}')
        seen = recorded.get((module, name), {})
        cause, stop = STOPS[name]
        if seen.get('marker') != [MARKER]:
            fail(f'stage {name}: marker is {seen.get("marker")}')
        if seen.get('cause') != [cause]:
            fail(f'stage {name}: cause is {seen.get("cause")}, expected [{cause!r}]')
        stopped = seen.get('stopped', [''])
        if len(stopped) != 1 or not stop.fullmatch(stopped[0]):
            fail(f'stage {name}: stopped is {stopped}, expected {stop.pattern!r}')
    done = subprocess.run([sys.executable, '-c', 'import json; from tests.loop.stages import STAGES; '
                           'print(json.dumps(STAGES))'], cwd=REPO, capture_output=True, text=True)
    try:
        table = __import__('json').loads(done.stdout)
    except ValueError:
        table = {}
        fail(f'stages: could not read STAGES: {done.stderr[-300:]}')
    for name, seed in {**PASSING_STAGES, **STAGE_OWNERS}.items():
        if table.get(name) != seed:
            fail(f'stages: STAGES gives {name} {table.get(name)!r}, expected {seed!r}')
        elif not (REPO / seed).is_file():
            fail(f'stages: {seed} does not exist')
    note(f'stages{" (problems above)" if len(problems) > before else ""}: {sum(1 for (found, _), (o, _) in outcomes.items() if found == module and o == "passed")} passed, '
         f'{sum(1 for (found, _), (o, _) in outcomes.items() if found == module and o == "xfailed")} xfailed, '
         'each checked against its pin')


PROBE = '''
import os, sys, tomllib
from terrace.tier.spec import OverlaySpec, SpecError
text = open(os.environ['BUNDLE_LOOP_SPEC']).read()
try:
    spec = OverlaySpec.parse(text)
except SpecError as error:
    print('SpecError: ' + str(error))
else:
    print(spec.source.type, spec.tiers[1].name, spec.tiers[1].scope, spec.tiers[1].type, sep=' ')
print(any('staging' in tier for tier in tomllib.loads(text)['tier']))
'''


def drive() -> None:
    """The helper's spec, loaded through the parser in a throwaway site, in both forms."""
    want = {1: 'tar.gz scratch shared posix', 2: f'SpecError: {SHARDED}'}
    for bundles, expected in want.items():
        done = subprocess.run([str(REPO / 'tools' / 'temp_site.sh'), sys.executable,
                               str(REPO / 'tools' / 'bundle_loop.py'), '-n', str(bundles), '--',
                               sys.executable, '-c', PROBE], cwd=REPO, capture_output=True, text=True)
        out = done.stdout.split('\n')
        if done.returncode != 0 or len(out) < 3:
            fail(f'drive -n {bundles}: the helper exited {done.returncode}: {done.stderr.strip()[-300:]}')
        elif out[1] != expected:
            fail(f'drive -n {bundles}: the parser said {out[1]!r}, expected {expected!r}')
        elif out[2] != 'False':
            fail(f'drive -n {bundles}: the spec the helper wrote mentions staging')
        elif Path(out[0]).parent.exists():
            fail(f'drive -n {bundles}: {Path(out[0]).parent} outlived the helper')
        else:
            note(f'drive -n {bundles}: {out[1]}')


def collected(marker: str, *paths: str) -> List[str]:
    done = subprocess.run([sys.executable, '-m', 'pytest', '-m', marker, '--collect-only', '-q',
                           '-p', 'no:cacheprovider', *paths], cwd=REPO, capture_output=True, text=True)
    return [line for line in done.stdout.splitlines() if '::' in line]


def collect() -> None:
    """The mount test is collected under the integration marker, and on this host skips by the platform gate."""
    wanted = f'tests/fuse/test_integration.py::{R10}'
    if wanted not in collected('integration'):
        fail(f'{wanted} is not collected under -m integration')
    if wanted in collected('unit'):
        fail(f'{wanted} is collected under -m unit')
    outcomes = pytest_run('-m', 'integration', 'tests/fuse/test_integration.py', '-k', R10)
    outcome, message = outcomes.get(('tests.fuse.test_integration', R10), ('absent', ''))
    linux = sys.platform.startswith('linux') and os.path.exists('/dev/fuse')
    if linux and outcome != 'passed':
        fail(f'{R10} is {outcome} on a Linux host with /dev/fuse: {message[:200]}')
    elif not linux and (outcome != 'skipped' or 'requires Linux' not in message):
        fail(f'{R10} is {outcome} ({message!r}), expected a skip naming Linux on this host')
    note(f'collect: {R10} is {outcome} here ({"graded" if linux else "ungraded: needs Linux with /dev/fuse"})')


def r10() -> None:
    """R10's grade: run only on a Linux host with /dev/fuse; anything but a pass fails."""
    outcomes = pytest_run('-m', 'integration', 'tests/fuse/test_integration.py', '-k', R10)
    outcome, message = outcomes.get(('tests.fuse.test_integration', R10), ('absent', ''))
    if outcome != 'passed':
        fail(f'r10: {R10} is {outcome}: {message[:300]}')
    else:
        note(f'r10: PASSED tests/fuse/test_integration.py::{R10}')


def section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ''
    end = text.find('\n### ', start + len(heading))
    return text[start:end if end > 0 else len(text)]


def docs() -> None:
    """The retired sentences are gone and the passages R11 names say what the tree now does."""
    before = len(problems)
    plan, agents = (REPO / 'PLAN.md').read_text(), (REPO / 'AGENTS.md').read_text()
    flat = lambda text: re.sub(r'\s+', ' ', text)
    gone = [
        (plan, 'No archive example here is accepted by the tree\'s spec parser yet'),
        (plan, 'The `.tar` is pulled to a working path of its own, the `staging` key'),
        (plan, 'which the staged example once gave as `staging ='),
        (agents, '`ArchiveTier` is **unreachable from a spec file**'),
        (agents, 'each stopped at the same step: the spec parser\'s refusal of the archive tier'),
        (agents, 'stale staging copy'),
        (agents, 'adapter-complete but not yet reachable from a spec file'),
        (plan, 'no spec reaches the adapter'),
        (plan, 'no spec rule limits a bundle source\'s stack to one shared stage tier'),
        (plan, 'on first access the whole archive is pulled'),
    ]
    for text, sentence in gone:
        if flat(sentence) in flat(text):
            fail(f'docs: still says {sentence!r}')
    managed = flat(section(plan, 'Managed locations are marked'))
    if not re.search(r'archive source carries no marker', managed, re.I):
        fail('docs: PLAN.md § Managed locations does not say an archive source carries no marker')
    if '.terrace/bundles/' not in plan:
        fail('docs: PLAN.md does not name .terrace/bundles/ for the staged bundle')
    note('docs: ' + ('problems above' if len(problems) > before else
                     'retired sentences absent; Managed locations and the bundle place stated'))


def ledger() -> None:
    """The new seed exists and is indexed; the residual notes landed in the seeds the plan names."""
    before = len(problems)
    roadmap = (REPO / 'ROADMAP.md').read_text()
    seed = REPO / 'issues' / 'source-directory-mode.md'
    if not seed.is_file():
        fail('ledger: issues/source-directory-mode.md does not exist')
    elif 'status: unshaped' not in seed.read_text():
        fail('ledger: issues/source-directory-mode.md is not status: unshaped')
    if 'issues/source-directory-mode.md' not in roadmap:
        fail('ledger: ROADMAP.md has no entry for issues/source-directory-mode.md')
    for name in ('eviction-one-hop', 'audit-command', 'engine-error-mapping', 'invariant-audit-gaps',
                 'archive-tier-test-coverage'):
        if 'spec/archive-spec-plumbing' not in (REPO / 'issues' / f'{name}.md').read_text():
            fail(f'ledger: issues/{name}.md carries no note citing spec/archive-spec-plumbing')
    note('ledger: ' + ('problems above' if len(problems) > before else 'new seed indexed; residual notes present'))


def figures() -> None:
    """AGENTS.md states the counts this tree has, at every place it states one."""
    text = (REPO / 'AGENTS.md').read_text()
    unit, integration = len(collected('unit')), len(collected('integration'))
    engine = len(collected('unit', 'tests/engine'))
    files = len([path for path in (REPO / 'tests').rglob('*.py') if '__pycache__' not in path.parts])
    stated = [
        (r'uv run pytest -m unit +# (\d+) tests', (unit, )),
        (r'uv run pytest -m integration +# (\d+) tests', (integration, )),
        (r'\| `tests/` \| (\d+) files, (\d+) tests \((\d+) `unit`, (\d+) `integration`\)',
         (files, unit + integration, unit, integration)),
        (r'\((\d+) skipped, (\d+) deselected', (integration, unit)),
        (r'\*\*(\d+) unit tests in', (unit, )),
        (r'directly, by (\d+) engine tests', (engine, )),
    ]
    for pattern, want in stated:
        found = re.search(pattern, text)
        if found is None:
            fail(f'figures: AGENTS.md has no line matching {pattern!r}')
        elif tuple(int(group) for group in found.groups()) != want:
            fail(f'figures: AGENTS.md says {found.group(0)!r}, the tree has {want}')
    prose = re.search(r'([\w-]+) tests — the entire drain', text)
    word = WORDS[integration] if integration < len(WORDS) else ''
    if prose is None or prose.group(1).lower() not in (str(integration), word):
        fail(f'figures: the sentence on tests CI never runs does not say {integration}')
    note(f'figures: {unit} unit, {integration} integration, {engine} engine, {files} files under tests/')


LOOP_FROZEN = ['tests/loop/conftest.py', 'tests/loop/oracle.py', 'tests/loop/tap.py', 'tests/loop/test_scenario.py',
               'tests/loop/test_scenario_minio.py', 'tests/loop/test_support.py', 'tests/loop/__init__.py']
LOOP_STEPS = ['blocked', 'owned', 'Blocked', 'Scene', 'listing', 'assert_tree', 'test_stage_1_reach', 'test_stage_2_read']


def base() -> str:
    found = os.environ.get('GATE_BASE') or subprocess.run(
        ['git', 'merge-base', 'HEAD', 'origin/main'], cwd=REPO, capture_output=True, text=True).stdout.strip()
    if not found:
        fail('no base (set GATE_BASE or fetch origin/main)')
    return found


def steps(source: str) -> Dict[str, str]:
    """The AST of each shared step and stage 1-2 body, decorators stripped."""
    found: Dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in LOOP_STEPS:
            node.decorator_list = []
            found[node.name] = ast.dump(node, include_attributes=False)
    return found


def harness() -> None:
    """The loop's support files are the base's, byte for byte; its shared steps and stages 1-2 keep their bodies."""
    before = len(problems)
    start = base()
    if not start:
        return
    done = subprocess.run(['git', 'diff', '--cached', '--name-only', start, '--', *LOOP_FROZEN],
                          cwd=REPO, capture_output=True, text=True)
    changed = [line for line in done.stdout.splitlines() if line]
    if done.returncode != 0 or changed:
        fail(f'harness: frozen loop files changed: {changed or done.stderr[-200:]}')
    old = subprocess.run(['git', 'show', f'{start}:tests/loop/stages.py'], cwd=REPO, capture_output=True, text=True)
    now = subprocess.run(['git', 'show', ':tests/loop/stages.py'], cwd=REPO, capture_output=True, text=True)
    was, are = steps(old.stdout), steps(now.stdout)
    for name in LOOP_STEPS:
        if name not in are:
            fail(f'harness: stages.py lost {name}')
        elif was.get(name) != are[name]:
            fail(f'harness: stages.py changed the body of {name}')
    note('harness: ' + ('problems above' if len(problems) > before else
                        f'{len(LOOP_FROZEN)} loop files unchanged; {len(LOOP_STEPS)} shared steps keep their bodies'))


EXAMPLE = """
import sys, re
from terrace.tier.spec import OverlaySpec
plan = open('PLAN.md').read()
blocks = re.findall(r'```toml\\n(.*?)```', plan, re.S)
for label in ('# direct-update ZIP as source', '# staged re-sync tar.gz on S3 as source'):
    found = [block for block in blocks if label in block]
    if len(found) != 1:
        print('MISSING ' + label); continue
    text = '[overlay]\\nname = "example"\\nstate = "/var/example-state"\\n\\n' + found[0]
    if '[[tier]]' not in found[0].split(label, 1)[1]:
        text += '\\n[[tier]]\\nname = "stage"\\ntype = "posix"\\npath = "/var/example-stage"\\n'
    spec = OverlaySpec.parse(text)
    print(label, spec.source.type, spec.source.location.type,
          None if spec.source.max_frequency is None else spec.source.max_frequency.count, sep='|')
"""


def examples() -> None:
    """The two single-bundle archive examples, taken from PLAN.md as written, parse with their tables unchanged."""
    done = subprocess.run([sys.executable, '-c', EXAMPLE], cwd=REPO, capture_output=True, text=True)
    want = ['# direct-update ZIP as source|zip|posix|None', '# staged re-sync tar.gz on S3 as source|tar.gz|s3|4']
    got = done.stdout.splitlines()
    if done.returncode != 0 or got != want:
        fail(f'examples: {got or done.stderr[-400:]}, expected {want}')
    else:
        note('examples: both single-bundle examples of PLAN.md parse as written')


MUTANTS: Dict[str, Tuple[str, Dict[str, List[str]]]] = {
    'read-only': ('p3', {READONLY: ROWS['p3'][READONLY][:-1]}),
    'oracle': ('p2', {SOURCE: ['test_staged_bundle_is_one_counted_file_in_the_durable_root'],
                      'tests.tier.test_posix': ['test_usage_counts_staged_bundles']}),
    'counter': ('p2', {SOURCE: ['test_staged_bundle_is_one_counted_file_in_the_durable_root']}),
    'realpath': ('p4', {SPEC: SYMLINK_ROWS}),
}


def mutants(phase: str) -> None:
    """Each mutant due by this phase takes effect, and every row that guards it then fails."""
    verify = REPO / 'spec' / 'archive-spec-plumbing' / 'verify'
    for name, (due, guarded) in MUTANTS.items():
        if PHASES.index(due) > PHASES.index(phase):
            continue
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / 'mutant.log'
            files = sorted({'tests/' + '/'.join(module.split('.')[1:]) + '.py' for module in guarded})
            environment = dict(os.environ, ARCHIVE_CYCLE_MUTANT=name, ARCHIVE_CYCLE_MUTANT_LOG=str(log),
                               PYTHONPATH=os.pathsep.join(filter(None, [str(verify), os.environ.get('PYTHONPATH')])))
            outcomes = pytest_run('-p', 'archive_cycle_mutants', '-m', 'unit', *files, env=environment)
            seen = log.read_text().splitlines() if log.is_file() else []
            if f'{name} planted' not in seen or f'{name} fired' not in seen:
                fail(f'mutant {name} did not take effect: {seen[:4]}')
                continue
            survivors = [f'{module}::{row}' for module, rows in guarded.items() for row in rows
                         if outcomes.get((module, row), ('absent', ''))[0] == 'passed']
            absent = [f'{module}::{row}' for module, rows in guarded.items() for row in rows
                      if (module, row) not in outcomes]
            if survivors:
                fail(f'mutant {name} survived: {len(survivors)} guarding row(s) passed: {survivors[:6]}')
            if absent:
                fail(f'mutant {name}: guarding rows did not run: {absent[:6]}')
            if not survivors and not absent:
                note(f'mutant {name}: killed by all {sum(len(rows) for rows in guarded.values())} guarding rows')


SPEC_ID = re.compile(r'\b[RPDOQ]\d{1,2}\b')


def ids() -> None:
    """No spec ids in the lines the cycle adds to src/, tests/ or README.md (staged against the base)."""
    start = base()
    if not start:
        return
    diff = subprocess.run(['git', 'diff', '--cached', '-U0', start, '--', 'src', 'tests', 'README.md'],
                          cwd=REPO, capture_output=True, text=True).stdout
    found = [line for line in diff.splitlines()
             if line.startswith('+') and not line.startswith('+++') and SPEC_ID.search(line)]
    for line in found[:10]:
        fail(f'ids: a spec id in an added line: {line[:160]!r}')
    note(f'ids: {len(found)} added line(s) with a spec id, against {start[:9]}')


MODES = {
    **{f'rows-{phase}': [lambda phase=phase: rows(phase)] for phase in PHASES},
    **{f'lane-{phase}': [lambda phase=phase: lane(phase)] for phase in PHASES},
    'stages': [stages], 'drive': [drive], 'collect': [collect], 'docs': [docs], 'ledger': [ledger],
    'figures': [figures], 'ids': [ids], 'r10': [r10], 'harness': [harness], 'examples': [examples],
    **{f'mutants-{phase}': [lambda phase=phase: mutants(phase)] for phase in PHASES},
    'p1': [lambda: rows('p1'), ids, lambda: lane('p1')],
    'p2': [lambda: rows('p2'), lambda: mutants('p2'), ids, lambda: lane('p2')],
    'p3': [lambda: rows('p3'), lambda: mutants('p3'), ids, lambda: lane('p3')],
    'p4': [lambda: rows('p4'), lambda: mutants('p4'), stages, harness, examples, drive, ids, lambda: lane('p4')],
    'p5': [lambda: rows('p5'), lambda: mutants('p5'), stages, harness, examples, collect, docs, ledger, figures, ids,
           lambda: lane('p5')],
}


def main(argv: List[str]) -> int:
    if len(argv) != 1 or argv[0] not in MODES:
        print(f'usage: check_outcomes.py {{{"|".join(MODES)}}}', file=sys.stderr)
        return 2
    for check in MODES[argv[0]]:
        check()
    verdict = f'gate: {argv[0]} FAILED, {len(problems)} problem(s)' if problems else f'gate: {argv[0]} ok'
    print(verdict)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text('\n'.join(lines + [verdict]) + '\n')
    except OSError as error:
        print(f'gate: could not keep the log at {LOG}: {error}', file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
