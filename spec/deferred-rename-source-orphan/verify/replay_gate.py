# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the deferred-rename-source-orphan cycle.

Usage (from the cycle worktree root, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I spec/deferred-rename-source-orphan/verify/replay_gate.py MODE [MODE ...]

Modes:
    commit    R1-R4    the data-commit regressions pass on this tree and each fails on the baseline engine; the
                       review-cycle-1 size regression passes here and fails on the reviewed engine
    replay    R3-R6    the namespace-replay regressions pass on this tree and each fails on the baseline engine;
                       the review-cycle-1 settle regressions pass here and each fails on the reviewed engine
    model     R7, R8   test_model keeps every baseline rule, strategy and setting, adds the commit, poll and
                       drain rules, passes under CI=true, and fails under CI=true on the baseline engine with a
                       falsifying example (shrinking off for that run only)
    mutants   R1-R8    each fix-removal mutant of this tree's engine (planted into a scratch copy of HEAD's src/)
                       fails the regressions it names, and the ones marked for the model fail test_model under
                       CI=true with shrinking off: the gates and the model reach what the fix closed
    seeds     R10      test_model passes for each --hypothesis-seed in --seeds=A-B (default 1-20), without CI,
                       two scratch trees built from HEAD, one worker each, no .hypothesis/ before any run
    suite     R9       CI=true: tools/lint.sh exits 0; pytest -n auto -m unit has 0 failed, 0 errors,
                       passed >= 449 and == AGENTS.md's stated unit count, with test_model and every
                       regression passing
    records   R12      PLAN.md's risk register and AGENTS.md's open-violations row match what ships; the
                       follow-up seed exists with its evidence and a ROADMAP.md entry; no live text keeps a
                       tolerated test failure
    ledger             every deferral phrase in TECH.md's phase bodies names an issues/ seed that ROADMAP.md lists
    prepush            clean tree, suite, records, then no home-directory path in the branch diff or messages
    ci        R11      the push run for the pushed commit (--sha, default origin/<branch>; later commits may touch
                       only spec/): all four jobs concluded success, Linux passed >= the unit count with no skip,
                       macOS skipped only tests/fuse naming libfuse. --run-json/--log-dir read a saved
                       `gh run view --json` record and job logs (<job id>.log or <job name>.log) instead

"Fails on the baseline engine" runs this tree's tests against `src/` from BASELINE (the GOAL's 141aa86),
extracted with `git archive` into scratch and put first on PYTHONPATH; the run checks which sosfs it imported.
"Fails on the reviewed engine" does the same with `src/` from REVIEWED, the engine review cycle 1 graded.
Counts come from pytest's JUnit XML, never from the exit code.

Footprint: one subprocess at a time, except `seeds`, which runs two pytest processes at once (two workers,
each its own scratch tree and environment, each child in its own process group, killed as a group on the
first red or on a per-seed timeout). `suite` and `prepush` run pytest with `-n auto` (one worker per core,
about 1.5 GB peak on a 10-core machine); every other pytest run is serial (under 500 MB each). `model` runs
test_model twice (about 30 s on this tree; the baseline run has shrinking turned off by a scratch plugin, so
it stops at the first falsifying example). `seeds` takes about 30 s per seed at 1000 examples, so 20 seeds on
two workers take about 5 min. Scratch goes under $TMPDIR and is left in place; nothing is deleted. The gate
never pushes and never writes to the tree.

Exit status: 0 green, 1 red, 3 could not run (a precondition missing; never read as green).
"""

from __future__ import annotations

import os
import re
import ast
import sys
import json
import time
import uuid
import shutil
import signal
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


SLUG = 'deferred-rename-source-orphan'
BASELINE = '141aa86'
REVIEWED = '67dfaee'          # review cycle 1's commit; its src/ is the reviewed 535263c's (and CI's c737308's)
MODEL = 'tests/engine/test_model.py'
MODEL_ID = ('tests.engine.test_model.TestOverlayModel', 'runTest')       # JUnit (classname, name)
REPLAY = 'tests/engine/test_replay.py'
UNIT_FLOOR = 449              # the GOAL's floor: 448 passing at the baseline plus test_model

# Regression tests build writes into tests/engine/test_replay.py (names fixed here, PLAN § 6).
REPLAY_TESTS = (                                   # P1: namespace replay, vacate, stale scheduler rows
    'test_flush_after_directory_round_trip_and_reuse',
    'test_flush_after_directory_round_trip',
    'test_flush_after_child_moved_out_of_removed_parent',
    'test_mkdir_over_removed_directory_with_queued_child_removal',
    'test_rename_over_empty_directory_with_queued_child_removal',
    'test_scheduler_rows_made_moot_before_they_run',
    'test_scheduler_row_id_reused_after_cancel',
    'test_scheduler_data_row_id_reused_by_next_hop',
    'test_new_file_at_a_renamed_away_path',
    'test_new_file_where_a_removed_file_still_sits',
    'test_flush_purges_only_the_tombstone_a_rename_left',
    'test_flush_removes_a_moved_out_child_before_its_old_parent',
)
DESIGN_TESTS = (                                   # P1: guards on this design's own hazards; the baseline passes them,
    'test_flush_when_a_directory_target_lies_inside_its_own_source_object',   # so `mutants` is their teeth
)
COMMIT_TESTS = (                                   # P2: data commits during the lag
    'test_commit_before_queued_rename_leaves_one_object',
    'test_fsync_commit_policy_before_queued_rename',
    'test_eviction_commit_before_queued_rename',
    'test_scheduler_poll_settles_data_before_rename',
    'test_commit_inside_renamed_directory',
    'test_create_on_the_source_inside_renamed_directory',
)
REVIEW_REPLAY_TESTS = (                            # review cycle 1, F3: a replay settles only the row it read
    'test_scheduler_replay_settles_only_its_own_row',
    'test_flush_replay_settles_only_its_own_row',
    'test_drain_replay_settles_only_its_own_row',
    'test_vacate_replay_settles_only_its_own_row',
    'test_flush_settles_only_the_data_row_it_read',
)
REVIEW_COMMIT_TESTS = (                            # review cycle 1, F2: a placed file reports its object's size
    'test_size_of_a_file_created_on_the_source_inside_renamed_directory',
)
BASELINE_REPLAY = (            # the four classes test_replay.py guarded at the baseline; they must still pass
    'test_unlink_of_pending_rename_removes_source_copy',
    'test_rmdir_of_pending_rename_removes_source_dir',
    'test_rename_over_pending_target_removes_stale_source_copy',
    'test_double_rename_then_unlink_removes_source_copy',
)

# R8: the model's names a weakening would touch. Strategy and settings floors are read from the baseline file.
STRATEGIES = ('DIRS', 'FILES', 'PATHS', 'FILE_PATHS', 'DIR_PATHS', 'DATA', 'OFFSETS')
EXCLUDERS = {'assume', 'precondition', 'reject', 'skip', 'skipif', 'xfail', 'filter', 'register_profile',
             'load_profile', 'phases', 'derandomize', 'database'}
SETTING_FLOORS = {'max_examples': 100, 'stateful_step_count': 50}
STATE = {'engine', 'oracle', 'root'}           # reassigning one outside __init__ resets the model's depth

DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|deferr(?:al|als|ed)\b|'
                      r'split (?:out|from)|stays? open', re.IGNORECASE)
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')    # written so this line cannot match itself
TOLERANCE = re.compile(r'only failure .{0,80}accepts|tolerat\w* .{0,60}test_model|test_model.{0,80}tolerat\w*|'
                       r'allowed to fail|expected (?:to )?fail\w*|xfail',
                       re.IGNORECASE | re.DOTALL)
FOLLOW_UP = 'issues/source-path-truncate-and-stat.md'
ANSI = re.compile(r'\x1b\[[0-9;]*m')


class Red(Exception):
    """A gate assertion failed."""


class CannotRun(Exception):
    """A precondition is missing; the gate proves nothing either way."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Red(message)
    print(f'  ok    {message}')


def scratch(label: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f'{SLUG}-{label}-', dir=os.environ.get('TMPDIR')))


def run(argv: list[str], env: dict[str, str] | None = None, quiet: bool = False, cwd: str | None = None,
        drop: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    print(f'  run   {" ".join(argv)}', flush=True)
    saved = dict(os.environ)                 # a child's environment starts from a saved copy, never a cleared one
    inherited = {k: v for k, v in saved.items() if k != 'PYTHONPATH' and k not in drop}
    proc = subprocess.run(argv, env={**inherited, **(env or {})}, text=True, capture_output=True, cwd=cwd)
    if not quiet:
        for line in (proc.stdout + proc.stderr).strip().splitlines()[-6:]:
            print(f'        {line}')
    return proc


def git(*args: str) -> str:
    return run(['git', *args], quiet=True).stdout


# -- JUnit ---------------------------------------------------------------------------------------------------

@dataclass
class Case:
    classname: str
    name: str
    outcome: str                  # passed | skipped | failed | error
    message: str = ''


@dataclass
class Report:
    cases: list[Case] = field(default_factory=list)

    def outcome(self, classname: str, name: str) -> list[str]:
        return [c.outcome for c in self.cases if (c.classname, c.name) == (classname, name)]

    def count(self, outcome: str) -> int:
        return sum(c.outcome == outcome for c in self.cases)


def junit(path: Path) -> Report:
    if not path.exists():
        raise Red(f'pytest wrote no JUnit report at {path}')
    report = Report()
    for case in ET.parse(path).getroot().iter('testcase'):
        outcome, message = 'passed', ''
        for tag in ('error', 'failure', 'skipped'):
            node = case.find(tag)
            if node is not None:
                outcome = {'failure': 'failed', 'error': 'error'}.get(tag, tag)
                message = node.get('message') or ''
                break
        report.cases.append(Case(case.get('classname') or '', case.get('name') or '', outcome, message))
    return report


def pytest(args: list[str], label: str, env: dict[str, str] | None = None, cwd: str | None = None,
           drop: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess, Report]:
    xml = scratch(label) / 'junit.xml'
    argv = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-rfEs', '-o', 'junit_family=xunit1',
            f'--junitxml={xml}'] + args
    proc = run(argv, env=env, cwd=cwd, drop=drop)
    return proc, junit(xml)


def no_red(report: Report, scope: str) -> None:
    reds = [f'{c.classname}::{c.name} ({c.outcome})' for c in report.cases if c.outcome in ('failed', 'error')]
    check(not reds, f'{scope}: 0 failed and 0 errors (got {reds})')


# -- the baseline engine -------------------------------------------------------------------------------------

def baseline_src(rev: str = BASELINE) -> Path:
    """`src/` at `rev` (BASELINE by default), extracted from git (committed state, never the working copy)."""
    where = scratch('baseline')
    archive = subprocess.run(['git', 'archive', rev, 'src'], capture_output=True)
    if archive.returncode != 0:
        raise CannotRun(f'git archive {rev} failed: {archive.stderr.decode()[-200:]}')
    subprocess.run(['tar', '-x', '-C', str(where)], input=archive.stdout, check=True)
    return where / 'src'


NO_SHRINK = '''
from hypothesis import Phase, settings
settings.register_profile('replay-gate-baseline', parent=settings.get_profile('ci'),
                          phases=[Phase.explicit, Phase.reuse, Phase.generate])
settings.load_profile('replay-gate-baseline')
'''
API_DRIFT = ('ImportError', 'AttributeError', 'NameError', 'TypeError', 'ModuleNotFoundError')


def on_baseline(args: list[str], label: str, env: dict[str, str] | None = None, shrink: bool = True,
                rev: str = BASELINE) -> Report:
    """Run pytest with `rev`'s engine (BASELINE by default) first on the import path, asserting that is what loaded."""
    src = baseline_src(rev)
    paths = [str(src)]
    if not shrink:                              # a scratch plugin; the test file is never touched
        plugin = scratch('plugin')
        (plugin / 'replay_gate_no_shrink.py').write_text(NO_SHRINK)
        paths.append(str(plugin))
        args = ['-p', 'replay_gate_no_shrink'] + args
    pythonpath = os.pathsep.join(paths)
    probe = run(['uv', 'run', '--frozen', 'python', '-c', 'import sosfs; print(sosfs.__file__)'],
                env={'PYTHONPATH': pythonpath}, quiet=True)
    check(probe.stdout.strip().startswith(str(src)), f'{label}: the baseline engine is the one imported '
                                                      f'({probe.stdout.strip() or probe.stderr.strip()[-120:]})')
    _, report = pytest(args, label, env={'PYTHONPATH': pythonpath, **(env or {})})
    return report


def failed_for_cause(report: Report, classname: str, name: str) -> bool:
    """Failed (not errored) on an assertion or an engine error, never on the test using an API the baseline lacks."""
    cases = [c for c in report.cases if (c.classname, c.name) == (classname, name)]
    return len(cases) == 1 and cases[0].outcome == 'failed' and not any(w in cases[0].message for w in API_DRIFT)


CONVERGED_NAMES = {'queued', 'pending', 'listed', 'deleted', 'tree', 'view', 'sizes', 'stop', 'start'}


def regression_shape(names: tuple[str, ...]) -> None:
    """Each named test walks the tree before acting and ends in assert_converged, which checks what it must."""
    module = ast.parse(Path(REPLAY).read_text())
    defs = {n.name: n for n in module.body if isinstance(n, ast.FunctionDef)}
    helper = defs.get('assert_converged')
    text = ast.unparse(helper) if helper is not None else ''
    missing = sorted(w for w in CONVERGED_NAMES if not re.search(rf'\b{w}\b', text))
    check(helper is not None and not missing,
          f'assert_converged checks the queues, unlisted directories, the catalog, the source, the view, the '
          f'sizes and a remount (missing {missing if helper else "the helper"})')
    for name in names:
        node = defs.get(name)
        check(node is not None, f'{name} is defined once in {REPLAY}')
        last = node.body[-1]
        ends = isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) \
            and getattr(last.value.func, 'id', None) == 'assert_converged'
        check(ends and 'walk' in names_in(node), f'{name} walks the tree and ends in assert_converged(...)')


def regressions(names: tuple[str, ...], label: str, design: tuple[str, ...] = (),
                review: tuple[str, ...] = ()) -> None:
    """
    Each named test passes here, the baseline classes still pass, each named test fails on the baseline, and
    each `review` test fails on the reviewed engine.
    """
    regression_shape(names + design + review)
    classname = REPLAY.removesuffix('.py').replace('/', '.')
    _, report = pytest(['-m', 'unit', REPLAY], f'{label}-here')
    no_red(report, f'{REPLAY} on this tree')
    for name in BASELINE_REPLAY + names + design + review:
        check(report.outcome(classname, name) == ['passed'], f'{name} ran once and passed')
    report = on_baseline(['-m', 'unit', REPLAY], f'{label}-baseline')
    for name in names:
        got = [(c.outcome, c.message[:100]) for c in report.cases if (c.classname, c.name) == (classname, name)]
        check(failed_for_cause(report, classname, name), f'{name} fails on the baseline engine, not on API drift '
                                                         f'(got {got})')
    if review:
        report = on_baseline(['-m', 'unit', REPLAY], f'{label}-reviewed', rev=REVIEWED)
        for name in review:
            got = [(c.outcome, c.message[:100]) for c in report.cases if (c.classname, c.name) == (classname, name)]
            check(failed_for_cause(report, classname, name), f'{name} fails on the reviewed engine ({REVIEWED}), '
                                                             f'not on API drift (got {got})')


def mode_commit() -> None:
    regressions(COMMIT_TESTS, 'commit', review=REVIEW_COMMIT_TESTS)


def mode_replay() -> None:
    regressions(REPLAY_TESTS, 'replay', DESIGN_TESTS, review=REVIEW_REPLAY_TESTS)


# -- the model -----------------------------------------------------------------------------------------------

def machine(tree: ast.Module) -> ast.ClassDef:
    found = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'OverlayMachine']
    if not found:
        raise Red('OverlayMachine is defined')
    return found[0]


def rules(cls: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    out = {}
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                if isinstance(target, ast.Name) and target.id in ('rule', 'invariant'):
                    out[node.name] = node
    return out


def assignments(tree: ast.Module) -> dict[str, str]:
    return {t.id: ast.dump(n.value) for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}


def settings_of(tree: ast.Module) -> dict[str, object]:
    """The keywords of the settings(...) assigned to TestOverlayModel.settings, the only one Hypothesis reads."""
    found = [n.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
             and any(ast.unparse(t) == 'TestOverlayModel.settings' for t in n.targets)]
    if len(found) != 1 or getattr(found[0].func, 'id', None) != 'settings':
        return {}
    return {k.arg: ast.literal_eval(k.value) for k in found[0].keywords
            if k.arg in SETTING_FLOORS and isinstance(k.value, ast.Constant)}


def names_in(node: ast.AST) -> set[str]:
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def embeds(old: list[ast.stmt], new: list[ast.stmt], tail_only: bool = False) -> bool:
    """
    `old` is `new` with statements removed: each old statement appears in order, compound ones recursively.
    With `tail_only`, statements may be added only after the old ones in each block.
    """
    position = 0
    for statement in old:
        if tail_only and position < len(new) and not same_statement(statement, new[position], tail_only):
            return False
        while position < len(new) and not same_statement(statement, new[position], tail_only):
            if isinstance(new[position], (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                return False                    # an added early exit skips what follows
            position += 1
        if position == len(new):
            return False
        position += 1
    return True


def same_statement(old: ast.stmt, new: ast.stmt, tail_only: bool = False) -> bool:
    if type(old) is not type(new):
        return False
    blocks = ('body', 'orelse', 'finalbody', 'handlers')
    if not any(getattr(old, b, None) for b in blocks):
        return ast.dump(old) == ast.dump(new)
    head = lambda n: ast.dump(type(n)(**{f: getattr(n, f) for f in n._fields if f not in blocks}))
    if head(old) != head(new):
        return False
    for block in blocks:
        a, b = getattr(old, block, None) or [], getattr(new, block, None) or []
        if block == 'handlers':
            if [ast.dump(h) for h in a] != [ast.dump(h) for h in b]:
                return False
        elif not embeds(a, b, tail_only) or (block != 'body' and len(a) != len(b)):
            return False
    return True


def engine_built(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Assign) and any(
        isinstance(t, ast.Attribute) and t.attr == 'engine' for t in statement.targets)


def static_model() -> None:
    """R8 by construction: the baseline model survives verbatim, and what is added excludes nothing."""
    old = ast.parse(git('show', f'{BASELINE}:{MODEL}'))
    new = ast.parse(Path(MODEL).read_text())
    before, after = assignments(old), assignments(new)
    for name in STRATEGIES:
        check(after.get(name) == before.get(name), f'strategy {name} is unchanged from {BASELINE}')
    old_rules, new_rules = rules(machine(old)), rules(machine(new))
    for name, node in old_rules.items():
        check(name in new_rules and ast.dump(new_rules[name]) == ast.dump(node),
              f'rule {name} is unchanged from {BASELINE} (decorator, strategies and body)')
    old_methods = {n.name: n for n in machine(old).body if isinstance(n, ast.FunctionDef)}
    new_methods = {n.name: n for n in machine(new).body if isinstance(n, ast.FunctionDef)}
    for name in ('parity', 'real'):
        check(ast.dump(new_methods.get(name, ast.Pass())) == ast.dump(old_methods[name]), f'{name} is unchanged')
    check(ast.dump(next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == 'snapshot'))
          == ast.dump(next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == 'snapshot')),
          'snapshot is unchanged')
    check(embeds(old_methods['teardown'].body, new_methods.get('teardown', ast.Pass()).body, tail_only=True),
          'teardown keeps every baseline statement, in order, under the same enclosing statements, and adds '
          'statements only after them')
    init_old = [s for s in old_methods['__init__'].body if not engine_built(s)]
    check(embeds(init_old, new_methods['__init__'].body),
          '__init__ keeps every baseline statement but the engine construction, in order')
    calls = [c for c in ast.walk(new_methods['__init__']) if isinstance(c, ast.Call)]
    named = lambda name: [c for c in calls if (getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)) == name]
    specs = [ast.unparse(c) for c in named('build_spec')]
    check(specs == ['build_spec(self.root, tiers=3)'], f'__init__ builds one spec, build_spec(self.root, tiers=3) '
                                                       f'(got {specs})')
    changed = {k.arg for c in named('replace') for k in c.keywords}
    check(changed <= {'fsync_policy'}, f'the spec changes nothing but fsync_policy (got {sorted(changed)})')
    check(len(named('OverlayEngine')) == 1 and sum(map(engine_built, new_methods['__init__'].body)) == 1,
          '__init__ builds exactly one engine')
    floors = settings_of(new)
    for key, floor in SETTING_FLOORS.items():
        check(isinstance(floors.get(key), int) and floors[key] >= floor, f'{key} = {floors.get(key)} >= {floor}')
    added = names_in(new) - names_in(old)
    check(not (added & EXCLUDERS), f'no excluding construct added (got {sorted(added & EXCLUDERS)})')
    bodies = {name: names_in(node) for name, node in new_rules.items() if name not in old_rules}
    check(any('commit_upstream' in b for b in bodies.values()), 'a new rule commits one entry (commit_upstream)')
    check(any('poll' in b and 'queued' not in b for b in bodies.values()), 'a new rule advances one scheduler poll')
    check(any({'poll', 'queued', 'pending', 'snapshot'} <= b and not any('flush' in n for n in b)
              for b in bodies.values()),
          'a new rule drains by polls and asserts convergence without flush()')
    check('TestOverlayModel' in assignments(new), 'TestOverlayModel is still the collected TestCase')
    check(after.get('pytestmark') == before.get('pytestmark'), 'pytestmark is unchanged (the model stays in -m unit)')
    names = [n.name for n in machine(new).body if isinstance(n, ast.FunctionDef)]
    check(len(names) == len(set(names)), 'no method is defined twice (a later def would shadow a rule)')
    tail = new.body[new.body.index(machine(new)) + 1:]
    check(len(tail) == 2 and ast.dump(tail[0]) == ast.dump(old.body[old.body.index(machine(old)) + 1]),
          'after the class, only the TestCase and its settings')
    method_names = set(new_methods)
    baseline_stores = {ast.dump(t) for n in old_methods.values() for t in ast.walk(n)
                       if isinstance(t, ast.Attribute) and isinstance(t.ctx, ast.Store)}
    stores = [t for n in new_methods.values() for t in ast.walk(n)
              if isinstance(t, ast.Attribute) and isinstance(t.ctx, ast.Store) and t.attr in STATE]
    check(len(stores) == sum(1 for n in old_methods.values() for t in ast.walk(n)
                             if isinstance(t, ast.Attribute) and isinstance(t.ctx, ast.Store) and t.attr in STATE),
          f'no added assignment to self.{"/".join(sorted(STATE))}')
    for name, node in new_methods.items():
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and getattr(sub.func, 'id', None) in ('setattr', 'delattr', 'vars'):
                raise Red(f'{name} calls {sub.func.id}()')
            if isinstance(sub, ast.Attribute) and sub.attr == '__dict__':
                raise Red(f'{name} reaches __dict__')
            if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store) and sub.attr in method_names \
                    and ast.dump(sub) not in baseline_stores:
                raise Red(f'{name} rebinds the method self.{sub.attr}')
            body = getattr(sub, 'body', None)
            if isinstance(body, list):
                exits = [i for i, st in enumerate(body) if isinstance(st, (ast.Return, ast.Raise))]
                if exits and exits[0] != len(body) - 1:
                    raise Red(f'{name} has statements after a return or raise')
            if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                if ast.unparse(sub) != 'self.clock.now' and not (isinstance(sub.value, ast.Name)
                                                                 and sub.value.id == 'self'):
                    raise Red(f'{name} assigns {ast.unparse(sub)} (patches an object the model drives)')
                if sub.attr in STATE and name not in ('__init__', 'teardown'):
                    raise Red(f'{name} reassigns self.{sub.attr}')
            if isinstance(sub, ast.ExceptHandler) and (name not in old_methods or name == 'teardown') \
                    and ast.dump(sub) not in {ast.dump(h) for h in ast.walk(old_methods.get(name, ast.Pass()))}:
                raise Red(f'{name} adds an exception handler')
            if isinstance(sub, (ast.Global, ast.Nonlocal)):
                raise Red(f'{name} rebinds a global')
    print('  ok    no method patches what the model drives, resets its state, or adds an exception handler')
    for conftest in ('tests/engine/conftest.py', 'tests/conftest.py'):
        before = git('show', f'{BASELINE}:{conftest}')
        after = Path(conftest).read_text() if Path(conftest).exists() else ''
        check(ast.dump(ast.parse(before)) == ast.dump(ast.parse(after)), f'{conftest} is unchanged from {BASELINE}')


def mode_model() -> None:
    static_model()
    proc, report = pytest([MODEL], 'model-ci', env={'CI': 'true'})
    check(report.outcome(*MODEL_ID) == ['passed'], f'test_model passes under CI=true ({report.outcome(*MODEL_ID)})')
    report = on_baseline([MODEL], 'model-baseline', env={'CI': 'true'}, shrink=False)
    got = [(c.outcome, c.message[:120]) for c in report.cases if (c.classname, c.name) == MODEL_ID]
    check(failed_for_cause(report, *MODEL_ID), f'test_model fails under CI=true on the baseline engine, not on API '
                                               f'drift (got {got})')


# Hypothesis loads its ci profile (derandomize, no database) when any of these is set (hypothesis._settings).
CI_VARS = ('CI', 'TF_BUILD', 'BUILDKITE', 'CIRCLECI', 'CIRRUS_CI', 'CODEBUILD_BUILD_ID', 'GITHUB_ACTIONS',
           'GITLAB_CI', 'HEROKU_TEST_RUN_ID', 'TEAMCITY_VERSION', 'bamboo.buildKey', 'TRAVIS', 'JENKINS_URL',
           'BITBUCKET_BUILD_NUMBER', '__TOX_ENVIRONMENT_VARIABLE_ORIGINAL_CI')
SEED_TIMEOUT = 300                # a passing seed takes about 30 s; a failing one shrinks for minutes


def seed_tree(label: str) -> tuple[Path, dict[str, str]]:
    """A scratch tree from HEAD (committed state) with its own environment, importing its own sosfs."""
    tree = scratch(label) / 'tree'
    tree.mkdir()
    archive = subprocess.run(['git', 'archive', 'HEAD'], capture_output=True, check=True)
    subprocess.run(['tar', '-x', '-C', str(tree)], input=archive.stdout, check=True)
    venv = {'UV_PROJECT_ENVIRONMENT': str(tree.parent / 'venv')}     # its own environment, never the caller's
    probe = run(['uv', 'run', '--frozen', 'python', '-c', 'import sosfs; print(sosfs.__file__)'], env=venv,
                cwd=str(tree), drop=CI_VARS, quiet=True)
    found = probe.stdout.strip()
    ci = run(['uv', 'run', '--frozen', 'python', '-c', 'from hypothesis._settings import is_in_ci; print(is_in_ci())'],
             env=venv, cwd=str(tree), drop=CI_VARS, quiet=True)
    check(ci.stdout.strip() == 'False', f'{label}: Hypothesis does not see a CI environment ({ci.stdout.strip()})')
    check(found.startswith(str(tree.resolve())) or found.startswith(str(tree)),
          f'{label}: the scratch tree imports its own sosfs ({found or probe.stderr.strip()[-160:]})')
    return tree, venv


def mode_seeds(seeds: str = '1-20') -> None:
    """R10 from committed state: no CI, no .hypothesis/ before any run, two workers at most."""
    low, high = (int(x) for x in seeds.split('-'))
    check(git('status', '--porcelain').strip() == '', 'the tree is committed (the runs read HEAD)')
    lanes = [list(range(low + i, high + 1, 2)) for i in (0, 1)]
    workers = [(seed_tree(f'seeds-{i}'), lane) for i, lane in enumerate(lanes) if lane]
    aside = scratch('seeds-hypothesis')
    token = f'replay-gate-{uuid.uuid4().hex}'
    saved = dict(os.environ)
    base = {k: v for k, v in saved.items() if k not in ('PYTHONPATH', *CI_VARS)}

    def start(tree: Path, venv: dict[str, str], seed: int) -> tuple[subprocess.Popen, Path]:
        db = tree / '.hypothesis'
        if db.exists():
            shutil.move(str(db), str(aside / f'before-seed-{seed}'))   # moved aside, never deleted
        check(not db.exists(), f'seed {seed}: the tree holds no .hypothesis/')
        xml = scratch(f'seed-{seed}') / 'junit.xml'
        argv = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'junit_family=xunit1',
                '-o', f'junit_suite_name={token}', f'--junitxml={xml}', MODEL, f'--hypothesis-seed={seed}']
        print(f'  run   seed {seed}: {" ".join(argv)}', flush=True)
        child = subprocess.Popen(argv, cwd=str(tree), env={**base, **venv},
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return child, xml

    running: dict[int, tuple] = {}
    queues = [list(lane) for _, lane in workers]
    try:
        while any(queues) or running:
            for index, ((tree, venv), _) in enumerate(workers):
                if index not in running and queues[index]:
                    seed = queues[index].pop(0)
                    running[index] = (seed, *start(tree, venv, seed), time.monotonic())
            time.sleep(0.5)
            for index, (seed, child, xml, began) in list(running.items()):
                if child.poll() is None:
                    if time.monotonic() - began > SEED_TIMEOUT:
                        raise Red(f'seed {seed}: test_model ran past {SEED_TIMEOUT} s (a failing seed shrinks)')
                    continue
                del running[index]
                outcome = junit(xml).outcome(*MODEL_ID)
                check(outcome == ['passed'], f'seed {seed}: test_model passes ({outcome})')
    finally:
        for seed, child, _, _ in running.values():
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            child.wait()
    left = subprocess.run(['pgrep', '-f', token], capture_output=True, text=True)
    if left.returncode in (0, 1):               # pgrep may be unavailable in a sandbox; then the group kill stands
        check(left.stdout.strip() == '', f'no seed run outlives the gate (got {left.stdout.split()})')


# -- fix-removal mutants ------------------------------------------------------------------------------------

# (id, file, exact text in this tree, replacement, regressions that must fail, must the model fail?)
# Anchors are the built engine's own text; the mode is red when one is not found exactly once, so a build that
# reshapes a guarded line re-anchors it here (PLAN § 6).
MUTANTS = (
    ('M1 commit at the logical path', 'src/sosfs/engine/overlay.py',
     'where = entry.source_path or self._place_parents(path, self.clock())', 'where = path',
     ('test_commit_before_queued_rename_leaves_one_object', 'test_commit_inside_renamed_directory'), True),
    ('M2 stale rows looked up by id alone', 'src/sosfs/state/journal.py',
     'same = key(found) == key(row)', 'same = True',
     ('test_scheduler_row_id_reused_after_cancel', 'test_scheduler_data_row_id_reused_by_next_hop'), True),
    ('M3 vacate ignores other holders', 'src/sosfs/engine/overlay.py',
     'rows = self._holder_rows(self._target(path), exclude=mover)', 'rows = set()',
     ('test_new_file_at_a_renamed_away_path', 'test_new_file_where_a_removed_file_still_sits'), True),
    ('M4 any tombstone purged after a rename', 'src/sosfs/engine/overlay.py',
     'if tomb is not None and tomb.deleted and tomb.source_path is None \\\n'
     '                        and not self.journal.queued_for(old) and self.catalog.holder(old) is None:',
     'if tomb is not None and tomb.deleted:',
     ('test_flush_purges_only_the_tombstone_a_rename_left',), False),
    ('M5 a target inside its own source object is skipped', 'src/sosfs/engine/overlay.py',
     "if row.op == 'rename' and entry.source_path is not None:\n            entry = self._clear_inner_target(row, entry)",
     "if False:\n            entry = self._clear_inner_target(row, entry)",
     ('test_flush_when_a_directory_target_lies_inside_its_own_source_object',), False),
    ('M6 a create onto the source lands at the logical path', 'src/sosfs/engine/overlay.py',
     'where = path if upstream is not None else self._place_parents(path, now)', 'where = path',
     ('test_create_on_the_source_inside_renamed_directory',), False),
    ('M7 an rmdir row runs before its moved-out children', 'src/sosfs/engine/overlay.py',
     "if entry is not None and row.op == 'rmdir' and entry.source_path is not None:",
     'if False:',
     ('test_flush_removes_a_moved_out_child_before_its_old_parent',), True),
    ('M8 a served copy is stat\'ed at its logical path', 'src/sosfs/engine/overlay.py',
     'where = entry.source_path if tier == self.source.name and entry.source_path is not None else entry.path',
     'where = entry.path',
     ('test_size_of_a_file_created_on_the_source_inside_renamed_directory',
      'test_create_on_the_source_inside_renamed_directory'), False),
    ('M9 a replayed row is settled by id alone', 'src/sosfs/state/journal.py',
     '            if self.current(row) is None:\n                return False',
     "            if self.db.execute(t'SELECT id FROM writeback WHERE id = {row.id}').fetchone() is None:\n"
     '                return False',
     REVIEW_REPLAY_TESTS, False),
)


def mode_mutants() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    classname = REPLAY.removesuffix('.py').replace('/', '.')
    for label, file, old, new, tests, model in MUTANTS:
        where = scratch('mutant')
        archive = subprocess.run(['git', 'archive', 'HEAD', 'src'], capture_output=True, check=True)
        subprocess.run(['tar', '-x', '-C', str(where)], input=archive.stdout, check=True)
        target = where / file
        text = target.read_text()
        check(text.count(old) == 1, f'{label}: its anchor occurs once in {file}')
        target.write_text(text.replace(old, new))
        src = where / 'src'
        probe = run(['uv', 'run', '--frozen', 'python', '-c', 'import sosfs; print(sosfs.__file__)'],
                    env={'PYTHONPATH': str(src)}, quiet=True)
        check(probe.stdout.strip().startswith(str(src)), f'{label}: the mutant engine is the one imported')
        _, report = pytest(['-m', 'unit', REPLAY], 'mutant-replay', env={'PYTHONPATH': str(src)})
        for name in tests:
            check(report.outcome(classname, name) == ['failed'], f'{label}: {name} fails')
        if model:
            plugin = scratch('plugin')
            (plugin / 'replay_gate_no_shrink.py').write_text(NO_SHRINK)
            pythonpath = os.pathsep.join([str(src), str(plugin)])
            _, report = pytest(['-p', 'replay_gate_no_shrink', MODEL], 'mutant-model',
                               env={'PYTHONPATH': pythonpath, 'CI': 'true'})
            check(failed_for_cause(report, *MODEL_ID), f'{label}: test_model fails under CI=true')


# -- the full gate -------------------------------------------------------------------------------------------

def stated_unit_count() -> int:
    text = Path('AGENTS.md').read_text()
    found = set(int(n) for n in re.findall(r'(\d+) unit tests', text))
    found |= set(int(n) for n in re.findall(r'\((\d+) `unit`', text))
    found |= set(int(n) for n in re.findall(r'pytest -m unit\s+# (\d+) tests', text))
    check(len(found) == 1, f'AGENTS.md states one unit-test count (got {sorted(found)})')
    return found.pop()


def mode_suite() -> None:
    sync = run(['uv', 'sync', '--locked', '-q'])
    check(sync.returncode == 0, f'uv sync --locked exits 0 (got {sync.returncode})')
    lint = run(['tools/lint.sh'], env={'CI': 'true'})
    check(lint.returncode == 0, f'tools/lint.sh exits 0 (got {lint.returncode})')
    proc, report = pytest(['-n', 'auto', '-m', 'unit'], 'suite', env={'CI': 'true'})
    no_red(report, '-m unit under CI=true')
    passed = report.count('passed')
    check(proc.returncode == 0, f'pytest exits 0 (got {proc.returncode})')
    check(passed >= UNIT_FLOOR, f'-m unit: {passed} passed >= {UNIT_FLOOR}')
    check(report.outcome(*MODEL_ID) == ['passed'], 'test_model passed')
    classname = REPLAY.removesuffix('.py').replace('/', '.')
    for name in (BASELINE_REPLAY + COMMIT_TESTS + REPLAY_TESTS + DESIGN_TESTS + REVIEW_COMMIT_TESTS
                 + REVIEW_REPLAY_TESTS):
        check(report.outcome(classname, name) == ['passed'], f'{name} passed')
    stated = stated_unit_count()
    check(passed + report.count('skipped') == stated, f'AGENTS.md states {stated} unit tests; the run collected '
                                                      f'{passed} passed + {report.count("skipped")} skipped')


def mode_records() -> None:
    plan = Path('PLAN.md').read_text()
    risk = re.search(r'\* \*\*Write-back namespace replay[^\n]*(?:\n(?!\* |\n).*)*', plan)
    check(risk is not None, 'PLAN.md § Risks keeps its namespace-replay entry')
    entry = ' '.join(risk.group(0).split())
    status = re.search(r'\*\((.*?)\)\*', entry)
    check(status is not None and 'open' in status.group(1) and 'Phase 8' not in status.group(1),
          f'the entry\'s status names what stays open and no longer claims Phase 8 '
          f'({status.group(1) if status else None!r})')
    check(FOLLOW_UP in entry, f'the entry names what stays open ({FOLLOW_UP})')
    agents = Path('AGENTS.md').read_text()
    rows = [line for line in agents.splitlines() if line.startswith('| 5 |')]
    check(len(rows) == 1 and FOLLOW_UP in rows[0] and 'deferred-rename-source-orphan' not in rows[0],
          f'AGENTS.md has one § 5 open-violation row and it names {FOLLOW_UP}')
    seed = Path(FOLLOW_UP)
    check(seed.exists(), f'{FOLLOW_UP} exists')
    text = seed.read_text()
    check(re.search(r'^status: unshaped$', text, re.MULTILINE) is not None, f'{FOLLOW_UP} is status: unshaped')
    for token in ('FileNotFoundError', '1000', 'overlay.py:', 'audit-command.md', 'truncate', '_record'):
        check(token in text, f'{FOLLOW_UP} carries {token!r} from the seed evidence')
    check(FOLLOW_UP in Path('ROADMAP.md').read_text(), f'ROADMAP.md lists {FOLLOW_UP}')
    touched = set(git('diff', '--name-only', 'origin/main...HEAD').split())
    needed = {'src/sosfs/engine/overlay.py', 'AGENTS.md', 'PLAN.md', FOLLOW_UP, 'ROADMAP.md'}
    check(needed <= touched, f'the branch diff carries the fix and its records together (missing '
                             f'{sorted(needed - touched)})')
    live = [Path('AGENTS.md'), Path('PLAN.md'), Path('.github/workflows/ci.yml'), Path(FOLLOW_UP)]
    live += [p for p in sorted(Path('issues').glob('*.md')) if p.name != f'{SLUG}.md']
    hits = [f'{p}: {m.group(0)[:80]!r}' for p in live for m in TOLERANCE.finditer(p.read_text())]
    roadmap = Path('ROADMAP.md').read_text()
    own = re.search(rf'### [^\n]*\n\n\*\*Seed:\*\* \[`issues/{SLUG}\.md`\].*?(?=\n### |\Z)', roadmap, re.DOTALL)
    rest = roadmap.replace(own.group(0), '') if own else roadmap
    hits += [f'ROADMAP.md: {m.group(0)[:80]!r}' for m in TOLERANCE.finditer(rest)]
    check(not hits, f'no live text keeps a tolerated test failure, outside this cycle\'s own retiring seed '
                    f'and entry (got {hits})')


def blocks(text: str) -> list[str]:
    """Prose paragraphs and list items (with their continuation lines); headings are skipped."""
    out, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        starts_item = re.match(r'(- |\* |\d+\. )', stripped) is not None
        if not stripped or stripped.startswith('#') or starts_item or stripped.startswith('**Verify') :
            if current:
                out.append(' '.join(current))
            current = [] if (not stripped or stripped.startswith('#')) else [stripped]
            continue
        current.append(stripped)
    if current:
        out.append(' '.join(current))
    return out


def mode_ledger() -> None:
    """Every phase-body paragraph or list item with deferral wording names an issues/ seed ROADMAP.md lists."""
    text = Path(f'spec/{SLUG}/TECH.md').read_text().split('\n---\n', 2)[-1]
    phases = text[text.index('## Phase P1'):text.index('## How `code-build`')]
    roadmap = Path('ROADMAP.md').read_text()
    named = set()
    for block in blocks(phases):
        if not DEFERRAL.search(block.replace(SLUG, '')):       # the slug itself reads as deferral wording
            continue
        seeds = re.findall(r'issues/([a-z0-9-]+)\.md', block)
        check(bool(seeds), f'deferral names a seed: {block[:100]!r}')
        for name in seeds:
            check(Path(f'issues/{name}.md').exists(), f'issues/{name}.md exists')
            check(f'issues/{name}.md' in roadmap, f'ROADMAP.md lists issues/{name}.md')
            named.add(name)
    for name in ('source-path-truncate-and-stat', 'source-commit-staged-publish', 'freethreaded-concurrency-stress',
                 'model-commit-route-reach'):
        check(name in named, f'the ledger reached the deferral to issues/{name}.md')


def mode_prepush() -> None:
    check(git('status', '--porcelain').strip() == '', 'working tree is clean')
    mode_suite()
    mode_records()
    text = git('log', '--format=%B', 'origin/main..HEAD') + git('diff', 'origin/main..HEAD')
    hits = sorted(set(HOME_PATH.findall(text)))
    check(not hits, f'no home-directory paths in the branch diff or commit messages (got {hits})')


# -- CI ------------------------------------------------------------------------------------------------------

CI_JOBS = ('Unit (Linux, 3.15)', 'Unit (Linux, 3.15t)', 'Unit (macOS, engine-only)', 'Lint & build')
CI_LINUX = ('Unit (Linux, 3.15)', 'Unit (Linux, 3.15t)')
CI_MACOS = 'Unit (macOS, engine-only)'
CI_PROBE_STEP = 'Assert libfuse loads'
FUSE_UNIT_FLOOR = 44
SUMMARY = re.compile(r'=+ (.*\b(?:passed|failed|error|errors|skipped)\b.*) in [0-9.]+s')
RUN_FIELDS = 'databaseId,headSha,headBranch,event,status,conclusion,jobs'


def summary(log: str, job: str) -> tuple[str, dict[str, int]]:
    lines = SUMMARY.findall(log)
    check(len(lines) == 1, f'{job}: exactly one pytest summary line in the log (got {len(lines)})')
    counts = {w: int(m.group(1)) if (m := re.search(rf'\b(\d+) {w}', lines[0])) else 0
              for w in ('passed', 'skipped', 'failed', 'error')}
    return lines[0], counts


def pushed_sha(sha: str) -> str:
    head = git('rev-parse', 'HEAD').strip()
    branch = git('branch', '--show-current').strip()
    pushed = git('rev-parse', '--verify', '--quiet', f'refs/remotes/origin/{branch}').strip()
    target = git('rev-parse', sha).strip() if sha else (pushed or head)
    if not target:
        raise CannotRun(f'unknown commit {sha!r}')
    moved = [f for f in git('diff', '--name-only', target, head).split() if not f.startswith(f'spec/{SLUG}/')]
    check(not moved, f'commits after {target[:12]} touch only spec/{SLUG}/ (got {moved})')
    return target


def gh_json(args: list[str]) -> object:
    if shutil.which('gh') is None:
        raise CannotRun('gh is not available; pass --run-json and --log-dir')
    proc = run(['gh', *args], quiet=True)
    if proc.returncode != 0:
        raise CannotRun(f'gh {" ".join(args)} failed: {proc.stderr.strip()[-300:]}')
    return json.loads(proc.stdout)


def mode_ci(run_json: str = '', log_dir: str = '', sha: str = '') -> None:
    """R11 from the run record: every job concluded success; nothing is tolerated."""
    target = pushed_sha(sha)
    branch = git('branch', '--show-current').strip()
    if run_json:
        record = json.loads(Path(run_json).read_text())
        if 'headSha' not in record or 'jobs' not in record:
            raise CannotRun(f'{run_json} must be `gh run view <id> --json {RUN_FIELDS}`, not a jobs list alone')
    else:
        runs = [r for r in gh_json(['run', 'list', '--branch', branch, '--commit', target, '--event', 'push',
                                    '--workflow', 'ci.yml', '--json', 'databaseId,headSha'])
                if r['headSha'] == target]
        if not runs:
            raise CannotRun(f'no push run of ci.yml for {branch} at {target[:12]} yet')
        record = gh_json(['run', 'view', str(max(r['databaseId'] for r in runs)), '--json', RUN_FIELDS])
    workflow = run(['git', 'diff', '--quiet', 'origin/main', target, '--', '.github/workflows/ci.yml'], quiet=True)
    check(workflow.returncode == 0, 'ci.yml at the pushed commit is main\'s (no deselection or tolerance added)')
    run_id = record.get('databaseId')
    check(record.get('headSha') == target, f'run {run_id} is for {target[:12]} (got {str(record.get("headSha"))[:12]})')
    check(record.get('event') == 'push', f'run {run_id} was triggered by a push (got {record.get("event")})')
    check(record.get('headBranch') == branch, f'run {run_id} ran on {branch} (got {record.get("headBranch")})')
    if record.get('status') != 'completed':
        raise CannotRun(f'run {run_id} is {record.get("status")}; wait for it')
    by_name = {j['name']: j for j in record['jobs']}
    for name in CI_JOBS:
        check(name in by_name, f'job {name!r} is in the run')
        check(by_name[name].get('conclusion') == 'success',
              f'job {name!r} concluded success (got {by_name[name].get("conclusion")})')

    def log_of(name: str) -> str:
        if not log_dir:
            proc = run(['gh', 'run', 'view', str(run_id), '--job', str(by_name[name]['databaseId']), '--log'],
                       quiet=True)
            if proc.returncode != 0:
                raise CannotRun(f'could not read the log of {name}')
            return ANSI.sub('', proc.stdout)
        by_id = Path(log_dir) / f'{by_name[name]["databaseId"]}.log'
        by_label = Path(log_dir) / (re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_') + '.log')
        return ANSI.sub('', (by_id if by_id.exists() else by_label).read_text())

    stated = stated_unit_count()
    totals = []
    for name in CI_LINUX:
        steps = {s['name']: s.get('conclusion') for s in by_name[name].get('steps', [])}
        check(steps.get(CI_PROBE_STEP) == 'success', f'{name}: step {CI_PROBE_STEP!r} concluded success')
        line, counts = summary(log_of(name), name)
        check(counts['failed'] == counts['error'] == counts['skipped'] == 0, f'{name}: no failure, error or skip '
                                                                              f'({line!r})')
        check(counts['passed'] == stated, f'{name}: {counts["passed"]} passed == AGENTS.md\'s {stated}')
        totals.append(counts['passed'])
    log = log_of(CI_MACOS)
    line, counts = summary(log, CI_MACOS)
    check(counts['failed'] == counts['error'] == 0, f'{CI_MACOS}: no failure or error ({line!r})')
    check(counts['passed'] + counts['skipped'] == totals[0],
          f'{CI_MACOS}: {counts["passed"]} passed + {counts["skipped"]} skipped == Linux {totals[0]}')
    reasons = re.findall(r'SKIPPED \[(\d+)\] (\S+?):\d*:? ?(.*)', log)
    check(bool(reasons) and all(f.startswith('tests/fuse') and 'libfuse' in r for _, f, r in reasons),
          f'{CI_MACOS}: every skip is under tests/fuse and names libfuse ({len(reasons)} skip lines)')
    total = sum(int(n) for n, _, _ in reasons)
    check(total >= FUSE_UNIT_FLOOR and total == counts['skipped'],
          f'{CI_MACOS}: {total} libfuse skips >= {FUSE_UNIT_FLOOR}, matching the summary line')


MODES = {'commit': mode_commit, 'replay': mode_replay, 'model': mode_model, 'mutants': mode_mutants,
         'seeds': mode_seeds,
         'suite': mode_suite, 'records': mode_records, 'ledger': mode_ledger, 'prepush': mode_prepush,
         'ci': mode_ci}
OPTIONS = {'ci': ('run_json', 'log_dir', 'sha'), 'seeds': ('seeds',)}


def main(argv: list[str]) -> int:
    options, modes = {}, []
    for arg in argv:
        if arg.startswith(('--run-json=', '--log-dir=', '--sha=', '--seeds=')):
            key, value = arg[2:].split('=', 1)
            options[key.replace('-', '_')] = value
        elif arg in MODES:
            modes.append(arg)
        else:
            print(f'unknown argument: {arg}', file=sys.stderr)
            return 3
    if not modes:
        print(__doc__)
        return 3
    root = git('rev-parse', '--show-toplevel').strip()
    if not root:
        print('CANNOT RUN: not inside a git worktree', file=sys.stderr)
        return 3
    os.chdir(root)
    for name in modes:
        print(f'[{name}]', flush=True)
        try:
            MODES[name](**{k: v for k, v in options.items() if k in OPTIONS.get(name, ())})
        except Red as error:
            print(f'  RED   {error}')
            return 1
        except CannotRun as error:
            print(f'  CANNOT RUN  {error}')
            return 3
    print('GREEN')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
