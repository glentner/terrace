# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gate for the racing-write-marked-clean cycle.

Usage (from the cycle worktree's root; one mode or several in order, and the first red stops the run):

    uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/verify/racing_gate.py MODE [MODE ...]

Modes:
    base     R2 R6   adds a detached worktree of the base commit beside the cycle worktree and runs this tree's
                     tests/engine/test_racing.py there, against the base's package: every test in BASE_FAILS fails
                     in each of its cases for BASE_CAUSE, every test in BASE_PASSES passes (the MECHANISM tests
                     drive the record itself, which the base lacks, and are not run there); then the base's own
                     unit suite, for its passed count; then `git worktree remove` (never forced). Writes
                     verify/base.txt.
    suite    R1-R7 R9  tools/lint.sh exits 0; `pytest -n auto -m unit` on this tree has 0 failed, 0 errors and
                     more passed than the base; every new test and both R5 tests pass in every case;
                     test_mid_copy_write_rearms_instead_of_settling is the base's source. Writes verify/final.txt.
    records  R8      AGENTS.md: the open-violation row is gone, § 6 and § 10 and the high-blast-radius rows say
                     what the code does, the stated counts equal what `suite` measured; the stale docstring
                     sentence is gone; schema.sql and db.py are the base's; added lines in src/ and tests/ carry
                     no spec id and are at most 120 characters; base.txt and final.txt agree with the lists here.
    ledger           every deferral phrase in TECH.md's phase bodies names an issues/ seed that exists and that
                     ROADMAP.md lists, and each seed in LEDGER carries its line.

"The base" is `git merge-base HEAD origin/main`. The test file is read where it is, by absolute path, with the
base worktree as the working directory: nothing is copied into that worktree, so it stays clean and closes
without force. Each run checks which package it imported. Counts come from pytest's JUnit XML, never from an exit
code. Scratch (XML and logs) goes under .local/racing-gate/, which is gitignored. Nothing is deleted. The gate
writes two tracked files, verify/base.txt and verify/final.txt, and the phase commits them.

Footprint: one subprocess at a time. `base` and `suite` each run pytest once with -n auto (one worker per core,
about 1.5 GB peak on a 10-core machine); every other run is one serial process. `base` builds a second uv
environment for the base worktree (the caller's UV_PROJECT_ENVIRONMENT with `-base` appended, when it is set).
Measured on the planning prototype (2026-11-12, 10 cores): see TECH.md, which names the run.

Exit status: 0 green, 1 red, 3 could not run (a precondition missing; never read as green).
"""

from __future__ import annotations

import os
import re
import ast
import sys
import signal
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


SLUG = 'racing-write-marked-clean'
SPEC = Path('spec') / SLUG
RACING = 'tests/engine/test_racing.py'
WRITEBACK = 'tests/engine/test_writeback.py'
RUN_TIMEOUT = 540

REPRODUCTIONS = ('test_write_held_past_the_commit_reaches_the_source',
                 'test_same_size_write_behind_an_unchanged_stat_reaches_the_source')
BASE_FAILS = REPRODUCTIONS + (
    'test_truncate_held_past_the_commit_reaches_the_source',
    'test_truncate_without_a_handle_inside_the_hop',
    'test_write_then_release_inside_the_hop',
    'test_write_inside_a_mid_stack_hop',
    'test_same_size_write_while_the_copy_is_in_flight',
    'test_second_handles_first_write_inside_the_hop',
    'test_write_admitted_inside_the_hop_and_still_in_flight_at_its_end')
BASE_PASSES = (
    'test_truncate_without_a_handle_from_another_thread',
    'test_commit_meeting_no_change_copies_once_and_settles',
    'test_write_returned_before_the_copy_reads_is_carried',
    'test_write_on_a_dirty_handle_executes_no_sql',
    'test_failed_write_leaves_no_change_in_flight',
    'test_mid_stack_hop_meeting_no_change_settles')
# Tests of the record itself (the mutex, `before_read`, `_hops`). The base has none of the three, so they are
# not run there; each kills a mutant of the design that no behavioural test above can place (PLAN § 4).
MECHANISM = (
    'test_write_held_before_admission_across_a_whole_commit',
    'test_hop_whose_copy_opens_no_record_stays_dirty',
    'test_first_write_is_admitted_under_the_path_lock',
    'test_failed_copy_leaves_no_record')
# Why a fix test fails on the base: the holder is recorded clean with bytes its upstream lacks.
BASE_CAUSE = r"assert 'clean' == 'dirty'"
KEPT = ('test_mid_copy_write_rearms_instead_of_settling', 'test_open_handle_requeues_after_background_commit')
UNCHANGED = 'test_mid_copy_write_rearms_instead_of_settling'

OWN_SEED = f'issues/{SLUG}.md'
# Sentences the fix makes false, wherever they stood.
STALE_AGENTS = ('the residency goes clean only when the stat matches what was copied',
                'reset *before* the stat', 'Moving the reset after the stat')
STALE_SOURCE = ("shows in the holder's stat", )
# A seed that takes a deferral from this cycle, and what its line must name.
LEDGER = {'freethreaded-concurrency-stress': ('racing-write-marked-clean', '_handles_mutex'),
          'pump-liveness-and-loss-windows': ('racing-write-marked-clean', 'commit_upstream'),
          'stage-handle-outlives-its-file': ()}
DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for|deferred|out of scope', re.IGNORECASE)


class Red(Exception):
    """A check failed."""


class Blocked(Exception):
    """A precondition is missing, so nothing was shown."""


def say(line: str) -> None:
    print(line, flush=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Red(message)
    say(f'  ok    {message}')


def scratch(label: str) -> Path:
    path = Path('.local/racing-gate') / label
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def run(argv: list[str], cwd: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run one child in its own process group, bounded; a hang is red and the group is killed."""
    say(f'  run   {" ".join(argv)}' + (f'   (in {cwd})' if cwd else ''))
    child = subprocess.Popen(argv, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    try:
        out, _ = child.communicate(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        raise Red(f'{" ".join(argv[:5])} ... ran past {RUN_TIMEOUT} s (its process group was killed)')
    return subprocess.CompletedProcess(argv, child.returncode, out, '')


def git(*args: str, ok: bool = True) -> str:
    proc = subprocess.run(['git', *args], text=True, capture_output=True)
    if ok and proc.returncode != 0:
        raise Blocked(f'git {" ".join(args)}: {proc.stderr.strip()}')
    return proc.stdout.strip()


def base() -> str:
    return git('merge-base', 'HEAD', 'origin/main')


def package() -> str:
    found = sorted(Path('src').glob('*/engine/overlay.py'))
    if len(found) != 1:
        raise Blocked(f'expected one src/*/engine/overlay.py, found {len(found)}')
    return found[0].parts[1]


def junit(path: Path) -> dict[str, dict[str, tuple[str, str]]]:
    """{test function: {case id: (outcome, text)}} from a JUnit XML file."""
    if not path.exists():
        raise Red(f'pytest wrote no report at {path}')
    cases: dict[str, dict[str, tuple[str, str]]] = {}
    for case in ET.parse(path).getroot().iter('testcase'):
        outcome, text = 'passed', ''
        for child in case:
            if child.tag in ('failure', 'error', 'skipped'):
                outcome = {'failure': 'failed'}.get(child.tag, child.tag)
                text = (child.get('message') or '') + '\n' + (child.text or '')
        name = case.get('name', '')
        cases.setdefault(f'{case.get("classname", "")}::{name.split("[")[0]}', {})[name] = (outcome, text)
    return cases


def pytest(args: list[str], label: str, cwd: str | None = None, env: dict[str, str] | None = None):
    xml = scratch(label) / 'junit.xml'
    if xml.exists():
        xml.rename(xml.with_suffix('.previous.xml'))
    argv = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-rfEs', '-o', 'junit_family=xunit1',
            f'--junit-xml={xml}', '-m', 'unit', *args]
    proc = run(argv, cwd=cwd, env=env)
    (scratch(label) / 'output.log').write_text(proc.stdout)
    return junit(xml), proc


def totals(cases: dict[str, dict[str, tuple[str, str]]]) -> dict[str, int]:
    count = {'passed': 0, 'failed': 0, 'error': 0, 'skipped': 0}
    for group in cases.values():
        for outcome, _ in group.values():
            count[outcome] += 1
    return count


def of(cases: dict[str, dict[str, tuple[str, str]]], module: str, test: str) -> dict[str, tuple[str, str]]:
    """The cases of one test function in one module (the classname is the module's dotted path)."""
    dotted = module[:-3].replace('/', '.')
    found = [group for key, group in cases.items() if key == f'{dotted}::{test}']
    if len(found) != 1:
        raise Red(f'{module}::{test} is not in the report (found {len(found)})')
    return found[0]


def imported(cwd: str, env: dict[str, str] | None, under: Path) -> None:
    name = package()
    proc = run(['uv', 'run', '--frozen', 'python', '-c', f'import {name}; print({name}.__file__)'], cwd=cwd, env=env)
    where = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ''
    check(proc.returncode == 0 and Path(where).resolve().is_relative_to(under.resolve()),
          f'the run imports {name} from {under}')


def mode_base() -> None:
    racing = Path(RACING).resolve()
    if not racing.exists():
        raise Blocked(f'{RACING} does not exist yet')
    rev = base()
    common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir'))
    tree = common.parent / '.worktrees' / f'{SLUG}-base'
    if tree.exists():
        raise Blocked(f'{tree} exists; close it with `git worktree remove` and run again')
    env = dict(os.environ)
    env.pop('VIRTUAL_ENV', None)
    if env.get('UV_PROJECT_ENVIRONMENT'):
        env['UV_PROJECT_ENVIRONMENT'] += '-base'
    git('worktree', 'add', '--detach', str(tree), rev)
    lines = [f'base commit: {rev}', f'test file: {RACING} of the cycle tree, run against the base package', '']
    try:
        imported(str(tree), env, tree / 'src')
        skip = 'not (' + ' or '.join(MECHANISM) + ')'
        cases, _ = pytest(['-k', skip, str(racing)], 'base-racing', cwd=str(tree), env=env)
        for test in BASE_FAILS:
            group = of(cases, RACING, test)
            for name, (outcome, text) in sorted(group.items()):
                check(outcome == 'failed' and re.search(BASE_CAUSE, text) is not None,
                      f'on the base, {name} fails with {BASE_CAUSE}')
                lines.append(f'FAILED {name}')
        for test in BASE_PASSES:
            group = of(cases, RACING, test)
            for name, (outcome, _) in sorted(group.items()):
                check(outcome == 'passed', f'on the base, {name} passes')
                lines.append(f'PASSED {name}')
        named = {f'{RACING[:-3].replace("/", ".")}::{test}' for test in BASE_FAILS + BASE_PASSES}
        check(set(cases) == named, 'on the base, the tests that ran are BASE_FAILS and BASE_PASSES, and no other')
        lines += [f'NOT RUN {test} (a test of the record itself, which the base lacks)' for test in MECHANISM]
        suite, _ = pytest(['-n', 'auto'], 'base-suite', cwd=str(tree), env=env)
        count = totals(suite)
        check(count['failed'] == 0 and count['error'] == 0, f'the base suite has 0 failed, 0 errors ({count})')
        lines += ['', f'base unit suite: {count["passed"]} passed']
    finally:
        closed = subprocess.run(['git', 'worktree', 'remove', str(tree)], text=True, capture_output=True)
        if closed.returncode != 0:
            say(f'  LEFT  {tree}: git worktree remove refused ({closed.stderr.strip()})')
    check(closed.returncode == 0 and not tree.exists(), 'the base worktree closed without force')
    (SPEC / 'verify' / 'base.txt').write_text('\n'.join(lines) + '\n')
    say(f'  wrote {SPEC}/verify/base.txt')


def base_passed() -> int:
    path = SPEC / 'verify' / 'base.txt'
    found = re.search(r'^base unit suite: (\d+) passed$', path.read_text(), re.MULTILINE) if path.exists() else None
    if found is None:
        raise Blocked(f'{path} has no base count; run the `base` mode first')
    return int(found.group(1))


def function_source(text: str, name: str) -> str:
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ''
    raise Red(f'no function {name}')


def mode_suite() -> None:
    floor = base_passed()
    lint = run(['tools/lint.sh'])
    check(lint.returncode == 0, 'tools/lint.sh exits 0')
    imported('.', None, Path('src'))
    cases, _ = pytest(['-n', 'auto'], 'suite')
    count = totals(cases)
    check(count['failed'] == 0 and count['error'] == 0, f'0 failed, 0 errors ({count})')
    check(count['passed'] > floor, f'{count["passed"]} passed, more than the base ({floor})')
    lines = [f'tree: the commit that carries this file (its parent is {git("rev-parse", "HEAD")})',
             'command: tools/lint.sh && uv run pytest -n auto -m unit', '']
    dotted = RACING[:-3].replace('/', '.')
    held = {key.split('::')[1] for key in cases if key.startswith(f'{dotted}::')}
    check(held == set(BASE_FAILS + BASE_PASSES + MECHANISM), 'the new module holds the tests this gate names, and no other')
    for module, tests in ((RACING, BASE_FAILS + BASE_PASSES + MECHANISM), (WRITEBACK, KEPT)):
        for test in tests:
            for name, (outcome, _) in sorted(of(cases, module, test).items()):
                check(outcome == 'passed', f'{name} passes')
                lines.append(f'PASSED {name}')
    then = function_source(git('show', f'{base()}:{WRITEBACK}'), UNCHANGED)
    check(function_source(Path(WRITEBACK).read_text(), UNCHANGED) == then, f'{UNCHANGED} is the base\'s source')
    lines += ['', f'unit suite: {count["passed"]} passed, 0 failed, 0 errors (base: {floor} passed)']
    (SPEC / 'verify' / 'final.txt').write_text('\n'.join(lines) + '\n')
    say(f'  wrote {SPEC}/verify/final.txt')


def section(text: str, heading: str) -> str:
    """The body under a `### ` heading that starts with `heading`, up to the next heading of that level or above."""
    found = re.search(rf'^### {re.escape(heading)}.*?$(.*?)(?=^##+ |\Z)', text, re.MULTILINE | re.DOTALL)
    if found is None:
        raise Red(f'AGENTS.md has no "### {heading}" section')
    return found.group(1)


def mode_records() -> None:
    agents = Path('AGENTS.md').read_text()
    overlay = (Path('src') / package() / 'engine' / 'overlay.py').read_text()
    check(OWN_SEED not in agents, f'AGENTS.md no longer names {OWN_SEED} (the open-violation row is gone)')
    for phrase in STALE_AGENTS:
        check(phrase not in agents, f'AGENTS.md no longer says "{phrase}"')
    for phrase in STALE_SOURCE:
        check(phrase not in overlay, f'overlay.py no longer says "{phrase}"')
    six, ten = section(agents, '6. '), section(agents, '10. ')
    check('`before_read`' in six and '`_handles_mutex`' in six, '§ 6 names `before_read` and `_handles_mutex`')
    check('`changing`' in ten and '`_hops`' in ten, '§ 10 names what `_handles_mutex` now guards')
    rows = [line for line in section(agents, 'High-blast-radius paths').splitlines() if '_mark_handle_dirty' in line]
    check(len(rows) == 2 and all('`_change`' in row and '`_meet`' in row for row in rows),
          'both high-blast-radius rows that name `_mark_handle_dirty` name `_change` and `_meet`')

    path = SPEC / 'verify' / 'final.txt'
    found = re.search(r'^unit suite: (\d+) passed, 0 failed, 0 errors \(base: (\d+) passed\)$',
                      path.read_text(), re.MULTILINE) if path.exists() else None
    if found is None:
        raise Blocked(f'{path} has no count; run the `suite` mode first')
    passed, floor = int(found.group(1)), int(found.group(2))
    check(floor == base_passed() and passed > floor, f'final.txt: {passed} passed, above base.txt\'s {floor}')
    stated = [int(n) for pattern in (r'# (\d+) tests,', r'\((\d+) `unit`', r'(\d+) deselected', r'\*\*(\d+) unit tests')
              for n in re.findall(pattern, agents)]
    check(len(stated) == 4 and set(stated) == {passed}, f'AGENTS.md states the unit count four times, each {passed}')
    whole = re.search(r'(\d+) files, (\d+) tests \(\d+ `unit`, (\d+) `integration`\)', agents)
    files = len([p for p in Path('tests').rglob('*.py') if '__pycache__' not in p.parts])
    check(whole is not None and int(whole.group(1)) == files and int(whole.group(2)) == passed + int(whole.group(3)),
          f'AGENTS.md states {files} test files and the unit count plus the integration count')

    for name in ('base.txt', 'final.txt'):
        text = (SPEC / 'verify' / name).read_text()
        want = 'FAILED' if name == 'base.txt' else 'PASSED'
        for test in REPRODUCTIONS:
            lines = [line for line in text.splitlines() if line.split('[')[0].endswith(f' {test}')]
            check(len(lines) == 2 and all(line.startswith(want) for line in lines), f'{name}: {test} {want} on both routes')

    rev, name = base(), package()
    quiet = subprocess.run(['git', 'diff', '--quiet', rev, '--', f'src/{name}/state/schema.sql', f'src/{name}/state/db.py'])
    check(quiet.returncode == 0, 'schema.sql and db.py are the base\'s')
    added = [line[1:] for line in git('diff', '-U0', rev, '--', 'src', 'tests').splitlines()
             if line.startswith('+') and not line.startswith('+++')]
    new = Path(RACING).read_text().splitlines() if RACING not in git('ls-files', RACING) else []
    bad = [line.strip()[:70] for line in added + new if len(line) > 120 or re.search(r'\b[RP]\d+\b', line)]
    check(bad == [], f'{len(added) + len(new)} added lines in src/ and tests/ carry no spec id and fit 120 '
                     f'characters{"" if not bad else ": " + repr(bad[:3])}')

def mode_ledger() -> None:
    roadmap = Path('ROADMAP.md').read_text()
    for seed, words in LEDGER.items():
        path = Path('issues') / f'{seed}.md'
        check(path.exists() and f'issues/{seed}.md' in roadmap, f'{path} exists and ROADMAP.md lists it')
        notes = [note for note in re.split(r'\n(?=- )', path.read_text()) if all(word in note for word in words)]
        check(len(notes) >= 1, f'{path} carries a dated note naming {" and ".join(words)}')
    tech = (SPEC / 'TECH.md').read_text()
    body = tech[tech.index('\n## Phase '):] if '\n## Phase ' in tech else ''
    check(body != '', 'TECH.md has phase bodies')
    blocks = re.split(r'\n(?=- |\n)', body)
    for block in blocks:
        prose = '\n'.join(line for line in block.splitlines() if not line.lstrip().startswith('#'))
        if DEFERRAL.search(prose) is None or '**Deferral ledger.**' in prose:
            continue                                        # the ledger item quotes the phrases it looks for
        seeds = re.findall(r'issues/([a-z0-9-]+)\.md', prose)
        check(seeds != [] and all((Path('issues') / f'{s}.md').exists() and f'issues/{s}.md' in roadmap for s in seeds),
              f'a deferral names its seed, and ROADMAP.md lists it: {" ".join(prose.split())[:70]!r}')


MODES = {'base': mode_base, 'suite': mode_suite, 'records': mode_records, 'ledger': mode_ledger}


def main(argv: list[str]) -> int:
    if not argv or any(arg not in MODES for arg in argv):
        say(__doc__ or '')
        return 3
    if not (SPEC / 'GOAL.md').exists():
        say(f'could not run: {SPEC}/GOAL.md is not here; run from the cycle worktree\'s root')
        return 3
    for arg in argv:
        say(f'--- {arg}')
        try:
            MODES[arg]()
        except Red as red:
            say(f'  RED   {red}')
            return 1
        except Blocked as blocked:
            say(f'  could not run: {blocked}')
            return 3
        say(f'--- {arg}: green')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
