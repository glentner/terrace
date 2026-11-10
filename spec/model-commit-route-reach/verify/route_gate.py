# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the model-commit-route-reach cycle.

Usage (from the cycle worktree root, $WT, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I \
        spec/model-commit-route-reach/verify/route_gate.py MODE [MODE ...]

The gate reads the git repository its working directory sits in, so it can be run by absolute path from the root
of another checkout of the branch (a scratch clone, say) and gates that checkout's committed state.

Modes:
    model        R5: test_model.py keeps, unchanged and in order, every module-level statement (imports,
                 strategies, snapshot, settings) and every OverlayMachine method it has at the base, and its bases
                 and keywords; it adds exactly the two strategies (just above the class, so no cited line moves),
                 the two composites and the helper named below, the rules drawing only named module strategies. The
                 added methods are graded by allowlist: only the node kinds in NODE_KINDS (no with, class, lambda,
                 yield, return, raise, assert, del, import, global or except); per scope (the body, engine_side,
                 oracle_side), only the calls SCOPED_CALLS names, the oracle side touching nothing of the engine;
                 stores in any context only to unshadowing locals and to self.clock.now as max(self.clock.now, ...);
                 nested defs undecorated; each composite runs self.parity(engine_side, oracle_side) once. The
                 route composite runs under parity and reaches each route the way GOAL.md defines it (an eviction
                 through the evict rule, fsync through the engine's fsync, the scheduler through a scheduler poll);
                 the move-out composite runs its four steps in order under parity; the class docstring counts the
                 composites in words and carries the phrases PLAN.md § 2.1 lists
    reach        R1, R3: the census (route_census.py, a pytest plugin wrapping OverlayEngine._commit_hop from
                 outside the test module; Hypothesis pools no int, float or bytes constant from it) over the
                 standalone fixed CI draw (`pytest tests/engine/test_model.py`, CI=true): on HEAD each route makes
                 at least its floor of queued-rename commits, R1's routes with commits that land off the logical
                 path (the scheduler's inside a scheduler poll), at least one lagging commit under a renamed
                 directory, with the own/ancestor split printed; on the base it reproduces the figures PLAN.md
                 records, exactly; both runs pass and run the whole draw
    detect       R4: the scheduler-route mutant (planted into a scratch copy of HEAD's src/) makes test_model fail
                 under CI=true with a falsifying example (shrinking off; the unmutated model passes the same run) and
                 makes test_scheduler_poll_settles_data_before_rename fail, while the eviction and fsync replay tests
                 pass under it and the base's model passes under it on its own CI draw; the mutant changes only
                 commit_row's call and _commit_hop's opt-in
    mutants      R6: M1, M2, M3 and M7 (deferred-rename-source-orphan's MUTANTS, verbatim) each make test_model fail
                 under CI=true with a falsifying example (shrinking off), after the unmutated model passes that run;
                 a failure whose last raised error is an API drift (ImportError, AttributeError, ...) is not a kill
    records      R7 and the Constraints: the records are committed; src/, tests/engine/conftest.py and
                 tests/engine/test_replay.py are byte-identical to the base and test_model.py is the only tests/
                 file changed; since the base, and since the plan commit, the branch touches only the files PLAN.md
                 allows; AGENTS.md states the serial suite time as SERIAL_SECONDS; issues/model-source-served-reach.md
                 (Q1's home) is unshaped, carries the 2026-11-06 history and is listed in ROADMAP.md; outside spec/
                 only ROADMAP.md's Seed line names the own seed; added src/ and tests/ lines cite no seed or spec id
                 and fit 120 characters; every file:line cite of test_model.py outside spec/ still names its text
    timing       R7: the serial suite (CI=true pytest -m unit), by pytest's own duration, is not more than 10 s under
                 AGENTS.md's figure (load only slows a run; a run over it by more than 10 s is a warning)
    suite        R8: CI=true: tools/lint.sh exits 0; pytest -n auto -m unit has 0 failed, 0 errors, passed >= 548
                 and == AGENTS.md's stated count (pytest's summary line agreeing with the JUnit count), the engine's
                 share == its stated count, test_model and the three route replay tests pass; no .hypothesis/ is
                 left in the tree
    reach_seeds  R2: the census, without CI, for each --hypothesis-seed in --seeds=A-B (default 1-20): every run
                 passes and makes at least one polled, lagging queued-rename commit through the scheduler route and
                 one lagging one through the eviction route; two scratch trees built from HEAD, one worker each
    seeds        R8: test_model passes for each --hypothesis-seed in --seeds=A-B (default 1-20), without CI and
                 without the census, two scratch trees built from HEAD, no .hypothesis/ before any run
    ledger       every deferral phrase in TECH.md's phase bodies names an issues/ seed that ROADMAP.md lists
    prepush      clean tree, suite, records, then no home-directory path in the branch diff or messages
    ci           R8: the push run for the pushed commit (--sha, default origin/<branch>; later commits may touch
                 only spec/): all four jobs concluded success, Linux passed == the stated count with no skip,
                 macOS skipped only tests/fuse naming libfuse. --run-json/--log-dir read a saved
                 `gh run view --json` record and job logs (<job id>.log or <job name>.log) instead

"The base" is BASELINE (980f998, the GOAL's base and the merge base); "the plan commit" is the commit that added
this cycle's PLAN.md, the base for which files the build touched. Every pytest run reads committed state: `reach`,
`detect`'s base run and both seed modes run in scratch trees from `git archive` (HEAD, or the base), each with
its own environment; `detect` and `mutants` put a mutant copy of HEAD's src/ first on PYTHONPATH and check that it
is the sosfs imported. The census plugin is taken from HEAD's tree, never the working copy. Counts come from
pytest's JUnit XML and the census's JSON, never from an exit code. Every pytest run has its own
HYPOTHESIS_STORAGE_DIRECTORY and -p no:cacheprovider, so none leaves .hypothesis/ or .pytest_cache/ in the tree.
Scratch and a copy of this run's output go under .local/route-gate/ (gitignored). Nothing is deleted; the gate
never pushes and never writes to the tracked tree.

Footprint: one subprocess at a time, except `seeds` and `reach_seeds`, which run two pytest processes at once
(each its own scratch tree, environment and process group, marked with a token in argv, killed as a group on the
first red or a per-run timeout, and checked gone by that token). Every process group the gate starts is tracked; a
SIGTERM, SIGHUP or SIGINT to the gate kills them all before it exits 3, and the whole invocation has a DEADLINE
(570 s, under the 10-minute command cap) past which it kills its runs and exits 3. `suite` and `prepush` run
pytest with -n auto (one worker per core, about 1.5 GB peak on a 10-core machine); every other pytest run is serial
and under 500 MB. Each scratch tree syncs its own environment (about 100 MB on disk, measured in planning), so a P1
run leaves about 300 MB under .local/route-gate/; nothing is deleted, and old runs may be moved aside by hand.
Measured on the planning lab (see PLAN.md § 6 for each mode's time and the run that measured it).

Exit status: 0 green, 1 red, 3 could not run (a precondition missing; never read as green).
"""

from __future__ import annotations

import os
import re
import ast
import builtins
import sys
import json
import time
import uuid
import shutil
import signal
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


SLUG = 'model-commit-route-reach'
BASELINE = '980f998'          # the GOAL's base and the merge base with origin/main
UNIT_FLOOR = 548              # at least this many pass (the base's count)
SERIAL_SECONDS = 40           # AGENTS.md's serial suite time, rounded (PLAN § 6: 34 s at the base, 42 s built)
MODEL = 'tests/engine/test_model.py'
MODEL_ID = ('tests.engine.test_model.TestOverlayModel', 'runTest')       # JUnit (classname, name)
REPLAY = 'tests/engine/test_replay.py'
REPLAY_CLASS = 'tests.engine.test_replay'
OVERLAY = 'src/sosfs/engine/overlay.py'
FROZEN = ('src', 'tests/engine/conftest.py', REPLAY)                     # byte-identical to the base
OWN_SEED = f'issues/{SLUG}.md'                     # retired by `code terrace roadmap` after landing
SOURCE_SEED = 'issues/model-source-served-reach.md'                     # Q1's home, written in P1
SOURCE_SEED_CARRIES = ('bypass_size', '2026-11-06', 'source-path-truncate-and-stat', f'spec/{SLUG}/')
# What the branch may touch after the plan commit (P1's records and the phases' TECH.md state).
ALLOWED = (MODEL, 'AGENTS.md', SOURCE_SEED, 'ROADMAP.md')
MACHINE = 'OverlayMachine'

# The model's additions (names fixed here, PLAN § 2.1): two composites, a helper, and two strategies.
COMPOSITE = 'write_rename_commit_through'      # the scheduler, eviction and fsync routes
MOVE_OUT = 'move_out_rmdir_unlink'             # a child's removal queued behind its old directory's
NEW_RULES = (COMPOSITE, MOVE_OUT)
REAL_STEPS = ('os.rename', 'os.rmdir', 'os.unlink')       # the move-out composite's oracle side, in order
NEW_HELPERS = ('poll_data_due',)
NEW_STRATEGIES = {'LIFT': "st.booleans()", 'ROUTE': "st.sampled_from(['poll', 'fsync', 'evict'])"}
BASE_COMPOSITES = 3           # the class docstring's count at the base ("three composites")
# R5 is graded by allowlist (two adversarial passes showed a denylist, and then a name-only allowlist, pass edits
# that swallow the model's assertions, rebind `self.oracle` or copy the engine's bytes into the oracle). Each added
# method may call only its own set below, by scope: the method body, its `engine_side` closure (engine calls, no
# `os`) and its `oracle_side` closure (`os` and `self.real`, nothing of the engine). Calls are normalized: a call's
# own arguments are dropped and a local receiver reads `_` (`self.real(path).exists` is `self.real().exists`).
# Stores: plain local names (none shadowing a builtin, `self` or `os`) and `self.clock.now` as `max(self.clock.now,
# ...)`, nothing else, in any context (comprehension targets included). Nested defs take no decorator.
SCOPED_CALLS = {
    'poll_data_due': {'body': {'self.engine.journal.queued_for', 'min', 'max', 'self.scheduler.deadline',
                               'self.scheduler.poll'}},
    'write_rename_commit_through': {
        'body': {'rule', '_.rsplit', 'len', 'self.real', 'self.real().exists', 'self.parity'},
        'engine_side': {'self.engine.create', 'self.engine.commit_upstream', 'self.engine.open', 'self.engine.write',
                        'self.poll_data_due', 'self.engine.rename', 'self.engine.fsync', 'self.engine.release',
                        'self.evict'},
        'oracle_side': {'os.open', 'os.pwrite', 'os.rename', 'os.close', 'self.real'}},
    'move_out_rmdir_unlink': {
        'body': {'rule', '_.rsplit', 'self.real', 'self.real().exists', 'self.parity'},
        'engine_side': {'self.engine.create', 'self.engine.commit_upstream', 'self.engine.rename',
                        'self.engine.rmdir', 'self.engine.unlink'},
        'oracle_side': {'os.close', 'os.open', 'os.rename', 'os.rmdir', 'os.unlink', 'self.real'}},
}
RESERVED = frozenset(dir(builtins)) | {'self', 'os'}
NODE_KINDS = frozenset({'Add', 'And', 'Assign', 'Attribute', 'BinOp', 'BitOr', 'BoolOp', 'Call', 'Compare',
                        'Constant', 'Eq', 'NotEq', 'Lt', 'LtE', 'Gt', 'GtE', 'In', 'NotIn', 'Is', 'IsNot', 'Or',
                        'Expr', 'FunctionDef', 'GeneratorExp', 'If', 'IfExp', 'ListComp', 'List', 'Tuple', 'Load',
                        'Name', 'Not', 'Slice', 'Store', 'Subscript', 'Try', 'UnaryOp', 'USub', 'arg', 'arguments',
                        'comprehension', 'keyword', 'Pass', 'JoinedStr', 'FormattedValue', 'For'})

# R1 and R3: the floor per route on HEAD's standalone fixed CI draw, own and ancestor renames together (PLAN § 3).
# R1's routes are floored on the commits that exercise the lag: scheduler on polled and lagging commits, eviction on
# lagging ones. R3's routes are floored on the GOAL's count, as the base is.
FLOORS = {'scheduler': 50, 'eviction': 50, 'fsync': 50, 'direct': 17}
R1_KEYS = {'scheduler': 'polled_lagging', 'eviction': 'lagging'}
ANCESTOR_FLOOR = 1            # summed over the route composite's three routes (PLAN deviation row 7)
# The census of the base over its fixed CI draw, as PLAN § 3 records it: (own, ancestor) per route.
BASE_COUNTS = {'scheduler': (2, 0), 'eviction': (0, 0), 'fsync': (0, 0), 'direct': (34, 0)}
SEED_ROUTES = ('scheduler', 'eviction')            # R2: at least one each, on every seed
EXAMPLES = 1000               # the settings' max_examples; each example starts two engines (run and remount)

# R4: the scheduler-route mutant. A commit through commit_row lands at the entry's logical path, as the defect
# deferred-rename-source-orphan closed did on every route; every other caller of _commit_hop keeps the fix.
SCHEDULER_MUTANT = (
    ('    def _commit_hop(self, path: str, holder: str, upstream: str) -> None:\n',
     '    def _commit_hop(self, path: str, holder: str, upstream: str, logical: bool = False) -> None:\n'),
    ('                where = entry.source_path or self._place_parents(path, self.clock())\n',
     '                where = path if logical else (entry.source_path or self._place_parents(path, self.clock()))\n'),
    ('            self._commit_hop(entry.path, row.from_tier, row.to_tier)\n',
     '            self._commit_hop(entry.path, row.from_tier, row.to_tier, logical=True)\n'),
)
SCHEDULER_TEST = 'test_scheduler_poll_settles_data_before_rename'
SPARED_TESTS = ('test_eviction_commit_before_queued_rename', 'test_fsync_commit_policy_before_queued_rename')

# R6: the fix-removal mutants the model must fail (spec/deferred-rename-source-orphan/verify/replay_gate.py,
# MUTANTS, marked for the model). Anchors are this tree's text; each must occur exactly once.
MODEL_MUTANTS = (
    ('M1 commit at the logical path', 'src/sosfs/engine/overlay.py',
     'where = entry.source_path or self._place_parents(path, self.clock())', 'where = path'),
    ('M2 stale rows looked up by id alone', 'src/sosfs/state/journal.py',
     'same = key(found) == key(row)', 'same = True'),
    ('M3 vacate ignores other holders', 'src/sosfs/engine/overlay.py',
     'rows = self._holder_rows(self._target(path), exclude=mover)', 'rows = set()'),
    ('M7 an rmdir row runs before its moved-out children', 'src/sosfs/engine/overlay.py',
     "if entry is not None and row.op == 'rmdir' and entry.source_path is not None:", 'if False:'),
)
NO_SHRINK = '''
from hypothesis import Phase, settings
settings.register_profile('route-gate-no-shrink', parent=settings.get_profile('ci'),
                          phases=[Phase.explicit, Phase.reuse, Phase.generate])
settings.load_profile('route-gate-no-shrink')
'''
API_DRIFT = ('ImportError', 'AttributeError', 'NameError', 'TypeError', 'ModuleNotFoundError', 'SyntaxError')
RAISED = re.compile(r'^E\s+(?:\w+\.)*(\w+)(?::|$)', re.MULTILINE)          # each `E   [module.]Name: ...` line
CRASHED = re.compile(r'^(?:\w+\.)*(\w+)(?::|$)')                            # the reprcrash's exception type
FALSIFYING = re.compile(r'Falsifying example|state = OverlayMachine\(\)')

NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
           'ten': 10, 'eleven': 11, 'twelve': 12}
COMPOSITES = re.compile(r'\b([a-z]+|\d+) composites?\b', re.IGNORECASE)
DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|\bdefer(?:s|red|ring|ral|rals)?\b|'
                      r'split (?:out|from)|stays? open|remains? open|left open|a later cycle|out of scope|owned by|'
                      r'\bhome(?:d|s)?\b(?![- ](?:director|path))', re.IGNORECASE)
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')    # written so this line cannot match itself
SPEC_ID = re.compile(r'\b(?:R[1-9]|P[1-9]|Q[1-9]|M[1-9]|F[1-9])\b')
SEED_PATH = re.compile(r'issues/[\w-]+\.md|spec/[\w-]+/')
CITE = re.compile(r'test_model\.py`?:(\d+)(?:-(\d+))?')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
ROOT = Path.cwd()
LOCAL = Path('.local/route-gate')
RUN_TIMEOUT = 300                 # every run but a seed's; the slowest, the suite, takes under a minute
DEADLINE = 570                    # the whole invocation, under the 10-minute cap of the command running it
STARTED = time.monotonic()
GROUPS: set[int] = set()          # process groups this gate started and has not reaped
UNSHOWN = ':(exclude).security'   # never read, diffed or named by this gate


class Red(Exception):
    """A gate assertion failed."""


class CannotRun(Exception):
    """A precondition is missing; the gate proves nothing either way."""


LOG: list[str] = []


def say(line: str) -> None:
    print(line, flush=True)
    LOG.append(line)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Red(message)
    say(f'  ok    {message}')


def scratch(label: str) -> Path:
    where = ROOT / LOCAL / f'{time.strftime("%Y%m%d-%H%M%S")}-{label}-{uuid.uuid4().hex[:6]}'
    where.mkdir(parents=True)
    return where


# Hypothesis loads its ci profile (derandomize, no database) when any of these is set (hypothesis._settings).
CI_VARS = ('CI', 'TF_BUILD', 'BUILDKITE', 'CIRCLECI', 'CIRRUS_CI', 'CODEBUILD_BUILD_ID', 'GITHUB_ACTIONS',
           'GITLAB_CI', 'HEROKU_TEST_RUN_ID', 'TEAMCITY_VERSION', 'bamboo.buildKey', 'TRAVIS', 'JENKINS_URL',
           'BITBUCKET_BUILD_NUMBER', '__TOX_ENVIRONMENT_VARIABLE_ORIGINAL_CI')


def run(argv: list[str], env: dict[str, str] | None = None, quiet: bool = False, cwd: str | None = None,
        drop: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    say(f'  run   {" ".join(argv)}')
    saved = dict(os.environ)                 # a child's environment starts from a saved copy, never a cleared one
    inherited = {k: v for k, v in saved.items() if k != 'PYTHONPATH' and k not in drop}
    # Its own process group, killed whole on a timeout, so a hang goes red instead of stalling the gate.
    budget = min(RUN_TIMEOUT, left())
    child = subprocess.Popen(argv, env={**inherited, **(env or {})}, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=cwd, start_new_session=True)
    GROUPS.add(child.pid)
    try:
        out, err = child.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        kill_group(child.pid)
        child.communicate()
        if budget < RUN_TIMEOUT:
            raise CannotRun(f'the gate reached its {DEADLINE} s deadline during {" ".join(argv[:6])} ...')
        raise Red(f'{" ".join(argv[:6])} ... ran past {RUN_TIMEOUT} s (a hang: its process group was killed)')
    GROUPS.discard(child.pid)
    proc = subprocess.CompletedProcess(argv, child.returncode, out, err)
    if not quiet:
        for line in (proc.stdout + proc.stderr).strip().splitlines()[-6:]:
            say(f'        {line}')
    return proc


def left() -> float:
    remaining = DEADLINE - (time.monotonic() - STARTED)
    if remaining <= 0:
        raise CannotRun(f'the gate reached its {DEADLINE} s deadline; split the modes across invocations')
    return remaining


def kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):     # macOS: an exited, unreaped leader answers EPERM
        pass
    GROUPS.discard(pgid)


def on_signal(number: int, _frame) -> None:
    """A signal to the gate (the caller's timeout, a hangup) kills every group it started before it exits."""
    started = sorted(GROUPS)
    for pgid in started:
        kill_group(pgid)
    say(f'  CANNOT RUN  the gate was sent signal {number}; its {len(started)} running process groups were killed')
    raise SystemExit(3)


def git(*args: str) -> str:
    return run(['git', *args], quiet=True).stdout


def git_ok(*args: str) -> bool:
    return run(['git', *args], quiet=True).returncode == 0


def plan_commit() -> str:
    found = git('log', '--diff-filter=A', '--format=%H', '--', f'spec/{SLUG}/PLAN.md').split()
    if not found:
        raise CannotRun(f'no commit added spec/{SLUG}/PLAN.md')
    return found[-1]


def show(rev: str, path: str) -> str:
    proc = run(['git', 'show', f'{rev}:{path}'], quiet=True)
    if proc.returncode != 0:
        raise CannotRun(f'git show {rev}:{path} failed')
    return proc.stdout


# -- JUnit ---------------------------------------------------------------------------------------------------

@dataclass
class Case:
    classname: str
    name: str
    outcome: str                  # passed | skipped | failed | error
    message: str = ''
    crash: str = ''               # the `message` attribute: pytest's reprcrash, the last exception in the chain


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
        outcome, message, crash = 'passed', '', ''
        for tag in ('error', 'failure', 'skipped'):
            node = case.find(tag)
            if node is not None:
                outcome = {'failure': 'failed', 'error': 'error'}.get(tag, tag)
                crash = node.get('message') or ''
                message = crash + ' ' + (node.text or '')
                break
        report.cases.append(Case(case.get('classname') or '', case.get('name') or '', outcome, message, crash))
    return report


def pytest(args: list[str], label: str, env: dict[str, str] | None = None, cwd: str | None = None,
           drop: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess, Report]:
    where = scratch(label)
    xml = where / 'junit.xml'
    env = {'HYPOTHESIS_STORAGE_DIRECTORY': str(where / 'hypothesis'), **(env or {})}
    argv = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-rfEs', '-o', 'junit_family=xunit1',
            f'--junitxml={xml}'] + args
    proc = run(argv, env=env, cwd=cwd, drop=drop)
    (where / 'output.txt').write_text(proc.stdout + proc.stderr)
    return proc, junit(xml)


def no_red(report: Report, scope: str) -> None:
    reds = [f'{c.classname}::{c.name} ({c.outcome})' for c in report.cases if c.outcome in ('failed', 'error')]
    check(not reds, f'{scope}: 0 failed and 0 errors (got {reds})')


def failed_for_cause(report: Report) -> bool:
    """test_model failed (not errored) with a falsifying example, never on an API the engine under test lacks."""
    cases = [c for c in report.cases if (c.classname, c.name) == MODEL_ID]
    if len(cases) != 1 or cases[0].outcome != 'failed' or FALSIFYING.search(cases[0].message) is None:
        return False
    crash = cases[0].crash.strip()
    found = CRASHED.match(crash)
    last = 'AssertionError' if crash.startswith('assert ') else found.group(1) if found else None   # a bare assert
    chain = set(RAISED.findall(cases[0].message))
    return last is not None and last not in API_DRIFT and not (chain & set(API_DRIFT))


CONTROL: list[bool] = []


def control() -> None:
    """Once per invocation: the unmutated model passes the same no-shrink CI run, so a kill is the mutant's."""
    if CONTROL:
        return
    plugin = scratch('no-shrink')
    (plugin / 'route_gate_no_shrink.py').write_text(NO_SHRINK)
    _, report = pytest(['-p', 'route_gate_no_shrink', MODEL], 'control-model',
                       env={'PYTHONPATH': str(plugin), 'CI': 'true'})
    check(report.outcome(*MODEL_ID) == ['passed'], 'control: the unmutated model passes the no-shrink CI run')
    CONTROL.append(True)


# -- trees and engines from committed state ------------------------------------------------------------------

def tree_at(rev: str, label: str, ci: bool) -> tuple[Path, dict[str, str]]:
    """A scratch tree of `rev` (committed state) with its own environment, importing its own sosfs."""
    tree = scratch(label) / 'tree'
    tree.mkdir()
    archive = subprocess.run(['git', 'archive', rev], capture_output=True)
    if archive.returncode != 0:
        raise CannotRun(f'git archive {rev} failed: {archive.stderr.decode()[-200:]}')
    subprocess.run(['tar', '-x', '-C', str(tree)], input=archive.stdout, check=True)
    venv = {'UV_PROJECT_ENVIRONMENT': str(tree.parent / 'venv')}     # its own environment, never the caller's
    drop = () if ci else CI_VARS
    probe = run(['uv', 'run', '--frozen', 'python', '-c', 'import sosfs; print(sosfs.__file__)'], env=venv,
                cwd=str(tree), drop=drop, quiet=True)
    found = probe.stdout.strip()
    check(found.startswith(str(tree.resolve())) or found.startswith(str(tree)),
          f'{label}: the scratch tree imports its own sosfs ({found or probe.stderr.strip()[-160:]})')
    if not ci:
        seen = run(['uv', 'run', '--frozen', 'python', '-c',
                    'from hypothesis._settings import is_in_ci; print(is_in_ci())'],
                   env=venv, cwd=str(tree), drop=CI_VARS, quiet=True)
        check(seen.stdout.strip() == 'False', f'{label}: Hypothesis does not see a CI environment')
    return tree, venv


def plant(edits: tuple[tuple[str, str, str], ...], label: str) -> Path:
    """HEAD's src/ in scratch with each (file, old, new) applied; each `old` must occur exactly once."""
    where = scratch(label)
    archive = subprocess.run(['git', 'archive', 'HEAD', 'src'], capture_output=True, check=True)
    subprocess.run(['tar', '-x', '-C', str(where)], input=archive.stdout, check=True)
    for file, old, new in edits:
        target = where / file
        text = target.read_text()
        check(text.count(old) == 1, f'{label}: its anchor occurs once in {file} ({old.strip()[:60]!r})')
        target.write_text(text.replace(old, new))
    return where / 'src'


def mutant_env(src: Path, label: str, shrink: bool, cwd: str | None = None,
               venv: dict[str, str] | None = None) -> dict[str, str]:
    """PYTHONPATH with the mutant src/ first (and the no-shrink plugin), after checking it is what imports."""
    paths = [str(src)]
    if not shrink:
        plugin = scratch('no-shrink')
        (plugin / 'route_gate_no_shrink.py').write_text(NO_SHRINK)
        paths.append(str(plugin))
    env = {'PYTHONPATH': os.pathsep.join(paths), **(venv or {})}
    probe = run(['uv', 'run', '--frozen', 'python', '-c', 'import sosfs; print(sosfs.__file__)'], env=env,
                cwd=cwd, quiet=True)
    check(probe.stdout.strip().startswith(str(src)), f'{label}: the mutant engine is the one imported '
                                                      f'({probe.stdout.strip() or probe.stderr.strip()[-120:]})')
    return env


def model_fails(src: Path, label: str, cwd: str | None = None, venv: dict[str, str] | None = None) -> Report:
    env = mutant_env(src, label, shrink=False, cwd=cwd, venv=venv)
    _, report = pytest(['-p', 'route_gate_no_shrink', MODEL], label, env={**env, 'CI': 'true'}, cwd=cwd)
    return report


# -- the census ----------------------------------------------------------------------------------------------

def census(tree: Path, venv: dict[str, str], plugin_dir: Path, label: str, ci: bool,
           seed: int | None = None) -> dict:
    """One instrumented run of test_model in `tree`; returns the census, after checking the run passed whole."""
    where = scratch(label)
    out = where / 'census.json'
    env = {**venv, 'PYTHONPATH': str(plugin_dir), 'ROUTE_CENSUS_OUT': str(out)}
    if ci:
        env['CI'] = 'true'
    args = ['-p', 'route_census', MODEL] + ([f'--hypothesis-seed={seed}'] if seed is not None else [])
    _, report = pytest(args, label, env=env, cwd=str(tree), drop=() if ci else CI_VARS)
    check(report.outcome(*MODEL_ID) == ['passed'], f'{label}: test_model passes under the census')
    if not out.exists():
        raise Red(f'{label}: the census wrote nothing')
    found = json.loads(out.read_text())
    check(found['overlay'].startswith(str(tree.resolve())) or found['overlay'].startswith(str(tree)),
          f'{label}: the census wrapped the tree\'s own engine')
    check(found['engines'] >= 2 * EXAMPLES, f'{label}: the run started {found["engines"]} engines, '
                                            f'>= {2 * EXAMPLES} (the whole draw ran)')
    return found


def total(found: dict, route: str) -> int:
    return found['routes'][route]['own'] + found['routes'][route]['ancestor']


def show_census(found: dict, label: str) -> None:
    for route, bucket in found['routes'].items():
        say(f'  count {label} {route}: {bucket["own"] + bucket["ancestor"]} (own {bucket["own"]}, '
            f'ancestor {bucket["ancestor"]}; lagging {bucket["lagging"]}; polled {bucket["polled"]}; '
            f'by rule {bucket["rules"]}); every hop to the source {found["to_source"].get(route, 0)}')
    for route, bucket in sorted(found['other'].items()):
        say(f'  count {label} {route}: own {bucket["own"]}, ancestor {bucket["ancestor"]}')


def reached(found: dict, label: str, floors: dict[str, int]) -> None:
    """Each route makes its floor; R1's routes make it with commits that exercise the lag (R1_KEYS)."""
    for route, floor in floors.items():
        check(floor >= 1 and total(found, route) >= floor,
              f'{label}: {total(found, route)} queued-rename commits through the {route} route >= {floor}')
        if route in R1_KEYS:
            got = found['routes'][route][R1_KEYS[route]]
            check(got >= floor, f'{label}: {got} of them are {R1_KEYS[route]} (land off the logical path'
                                f'{", inside a scheduler poll" if route == "scheduler" else ""}), >= {floor}')


def plugin_of(tree: Path) -> Path:
    where = tree / f'spec/{SLUG}/verify'
    if not (where / 'route_census.py').exists():
        raise CannotRun(f'HEAD has no spec/{SLUG}/verify/route_census.py')
    return where


def mode_reach() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests', f'spec/{SLUG}').strip() == '',
          'src/, tests/ and the gate are committed (the runs read HEAD)')
    head, venv = tree_at('HEAD', 'reach-head', ci=True)
    plugin = plugin_of(head)
    probe = run(['uv', 'run', '--frozen', 'python', '-c',
                 'import route_census; from hypothesis.internal.constants_ast import constants_from_module; '
                 'print(sorted({type(c).__name__ for c in constants_from_module(route_census)}))'],
                env={**venv, 'PYTHONPATH': str(plugin)}, cwd=str(head), quiet=True)
    pooled = probe.stdout.strip()
    check(probe.returncode == 0 and not ({'int', 'float', 'bytes'} & set(re.findall(r"'(\w+)'", pooled))),
          f'Hypothesis pools no int, float or bytes constant from the census, so the draw it measures is the '
          f'standalone draw (types pooled: {pooled or probe.stderr.strip()[-120:]})')
    imports = sorted({n.module for n in ast.walk(ast.parse((plugin / 'route_census.py').read_text()))
                      if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('sosfs')})
    check(imports == ['sosfs.engine'], f'the census imports only sosfs.engine.overlay, which the model loads '
                                       f'anyway (got {imports})')
    found = census(head, venv, plugin, 'reach-head', ci=True)
    show_census(found, 'HEAD')
    reached(found, 'HEAD, fixed CI draw', FLOORS)
    lifted = sum(found['routes'][route]['ancestor_lagging'] for route in ('scheduler', 'eviction', 'fsync'))
    check(lifted >= ANCESTOR_FLOOR, f'HEAD, fixed CI draw: {lifted} lagging commits under a renamed directory '
                                    f'(ancestor) over the scheduler, eviction and fsync routes, >= {ANCESTOR_FLOOR}')
    base, base_venv = tree_at(BASELINE, 'reach-base', ci=True)
    found = census(base, base_venv, plugin, 'reach-base', ci=True)
    show_census(found, 'base')
    for route, (own, ancestor) in BASE_COUNTS.items():
        got = (found['routes'][route]['own'], found['routes'][route]['ancestor'])
        check(got == (own, ancestor), f'base, fixed CI draw: the {route} route counts {got} (own, ancestor), '
                                      f'as PLAN.md records ({own}, {ancestor})')


# -- R4 and R6: mutants --------------------------------------------------------------------------------------

def methods_of(text: str, klass: str) -> dict[str, str]:
    for node in ast.parse(text).body:
        if isinstance(node, ast.ClassDef) and node.name == klass:
            return {n.name: ast.dump(n) for n in node.body if isinstance(n, ast.FunctionDef)}
    return {}


def mode_detect() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    src = plant(tuple((OVERLAY, old, new) for old, new in SCHEDULER_MUTANT), 'scheduler-mutant')
    planted = (src / 'sosfs/engine/overlay.py').read_text()
    before = methods_of(show('HEAD', OVERLAY), 'OverlayEngine')
    after = methods_of(planted, 'OverlayEngine')
    changed = sorted(name for name in before if before[name] != after.get(name))
    check(changed == ['_commit_hop', 'commit_row'] and set(after) == set(before),
          f'the mutant changes only _commit_hop and commit_row (got {changed})')
    check(planted.count('logical=True') == 1 and 'logical: bool = False' in planted,
          'only commit_row opts into the logical path; every other caller keeps the fix')
    env = mutant_env(src, 'scheduler-mutant replay', shrink=True)
    _, report = pytest(['-m', 'unit', REPLAY], 'scheduler-mutant-replay', env=env)
    check(report.outcome(REPLAY_CLASS, SCHEDULER_TEST) == ['failed'], f'under the mutant {SCHEDULER_TEST} fails')
    for name in SPARED_TESTS:
        check(report.outcome(REPLAY_CLASS, name) == ['passed'], f'under the mutant {name} passes (route spared)')
    control()
    report = model_fails(src, 'scheduler-mutant-model')
    check(failed_for_cause(report), 'under the mutant test_model fails under CI=true with a falsifying example')
    base, venv = tree_at(BASELINE, 'scheduler-mutant-base', ci=True)
    env = mutant_env(src, 'scheduler-mutant base model', shrink=True, cwd=str(base), venv=venv)
    _, report = pytest([MODEL], 'scheduler-mutant-base-model', env={**env, 'CI': 'true'}, cwd=str(base))
    check(report.outcome(*MODEL_ID) == ['passed'],
          'the base\'s model passes under the mutant on its own CI draw (the base\'s draw does not catch it)')


def mode_mutants() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    control()
    for label, file, old, new in MODEL_MUTANTS:
        src = plant(((file, old, new),), label.split()[0])
        report = model_fails(src, f'{label.split()[0]}-model')
        check(failed_for_cause(report), f'{label}: test_model fails under CI=true with a falsifying example')


# -- R5: the model keeps what it has -------------------------------------------------------------------------

def parse_model(text: str) -> tuple[list[ast.stmt], ast.ClassDef]:
    module = ast.parse(text)
    classes = [n for n in module.body if isinstance(n, ast.ClassDef) and n.name == MACHINE]
    if len(classes) != 1:
        raise Red(f'{MODEL} defines {MACHINE} once')
    return [n for n in module.body if n is not classes[0]], classes[0]


def called(node: ast.AST) -> list[str]:
    """Each call in `node` as dotted text (`self.engine.rename`), in source order."""
    calls = sorted((n for n in ast.walk(node) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))
    return [ast.unparse(n.func) for n in calls]


def normal(func: ast.expr) -> str:
    """A call target with inner calls' arguments dropped and a local receiver read as `_`."""
    if isinstance(func, ast.Call):
        return normal(func.func) + '()'
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id not in ('self', 'os'):
            return f'_.{func.attr}'
        return f'{normal(func.value)}.{func.attr}'
    return ast.unparse(func)


def scopes(method: ast.FunctionDef) -> dict[str, list[ast.AST]]:
    """The method body (nested defs excluded) and each nested def, by name."""
    nested = [n for n in ast.walk(method) if isinstance(n, ast.FunctionDef) and n is not method]
    inner = {id(x) for n in nested for x in ast.walk(n)}
    body = [n for n in ast.walk(method) if id(n) not in inner and n is not method]
    return {'body': body + list(method.decorator_list), **{n.name: list(ast.walk(n)) for n in nested}}


def mode_model() -> None:
    base_rest, base_class = parse_model(show(BASELINE, MODEL))
    head_rest, head_class = parse_model(show('HEAD', MODEL))
    added_assigns = [n for n in head_rest if isinstance(n, ast.Assign) and len(n.targets) == 1
                     and isinstance(n.targets[0], ast.Name) and n.targets[0].id in NEW_STRATEGIES]
    check({n.targets[0].id: ast.unparse(n.value) for n in added_assigns} == NEW_STRATEGIES,
          f'the module adds exactly the strategies {NEW_STRATEGIES}')
    kept = [n for n in head_rest if n not in added_assigns]
    check([ast.dump(n) for n in kept] == [ast.dump(n) for n in base_rest],
          'every other module-level statement (imports, strategies, snapshot, settings) is the base\'s, in order')
    body = ast.parse(show('HEAD', MODEL)).body
    at = [i for i, n in enumerate(body) if isinstance(n, ast.ClassDef) and n.name == MACHINE][0]
    check([ast.dump(n) for n in body[at - len(added_assigns):at]] == [ast.dump(n) for n in added_assigns],
          'the added strategies sit just above the class, so no line a seed cites before it moves')
    check([ast.dump(b) for b in head_class.bases] == [ast.dump(b) for b in base_class.bases]
          and [ast.dump(k) for k in head_class.keywords] == [ast.dump(k) for k in base_class.keywords]
          and not head_class.decorator_list, f'{MACHINE} keeps its bases and keywords and takes no decorator')
    kinds = {type(n).__name__ for n in head_class.body[1:]}
    check(kinds <= {'FunctionDef'}, f'the class body holds only its docstring and methods (got {sorted(kinds)})')
    base_methods = {n.name: n for n in base_class.body if isinstance(n, ast.FunctionDef)}
    head_methods = {n.name: n for n in head_class.body if isinstance(n, ast.FunctionDef)}
    for name, node in base_methods.items():
        check(name in head_methods and ast.dump(head_methods[name]) == ast.dump(node),
              f'{name} is the base\'s, decorators, body and docstring')
    added = set(head_methods) - set(base_methods)
    check(added == set(NEW_RULES) | set(NEW_HELPERS),
          f'the class adds exactly {sorted(NEW_RULES + NEW_HELPERS)} (got {sorted(added)})')
    strategies = {t.id for n in head_rest if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    for name in sorted(added):
        node = head_methods[name]
        kinds = sorted({type(n).__name__ for n in ast.walk(node)} - NODE_KINDS)
        check(not kinds, f'{name}: uses only the allowed node kinds (got {kinds}: no with, class, lambda, yield, '
                         f'return, raise, assert, del, import, global or except)')
        parts = scopes(node)
        check(set(parts) <= set(SCOPED_CALLS[name]), f'{name}: its nested defs are {sorted(set(parts) - {"body"})}, '
                                                     f'as PLAN § 2.1 names them')
        for scope, nodes in parts.items():
            got = {normal(n.func) for n in nodes if isinstance(n, ast.Call)}
            check(got <= SCOPED_CALLS[name][scope], f'{name} ({scope}): calls only {sorted(SCOPED_CALLS[name][scope])} '
                                                    f'(got extra {sorted(got - SCOPED_CALLS[name][scope])})')
        oracle = [ast.unparse(n) for n in parts.get('oracle_side', []) if isinstance(n, ast.Attribute)
                  and ast.unparse(n).startswith(('self.engine', 'self.scheduler', 'self.clock', 'self.evict'))]
        check(not oracle, f'{name}: the oracle side touches nothing of the engine (got {oracle})')
        nested = [n for n in ast.walk(node) if isinstance(n, ast.FunctionDef) and n is not node]
        check(not any(n.decorator_list for n in nested), f'{name}: nested defs take no decorator')
        names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
        names += [a.arg for n in ast.walk(node) if isinstance(n, ast.arguments)
                  for a in n.posonlyargs + n.args + n.kwonlyargs if n is not node.args or a.arg != 'self']
        names += [n.name for n in nested]
        shadow = sorted(set(names) & RESERVED)
        check(not shadow, f'{name}: no local, argument or nested def shadows a builtin, self or os (got {shadow})')
        stored = [ast.unparse(n) for n in ast.walk(node)
                  if isinstance(n, (ast.Attribute, ast.Subscript, ast.Starred)) and isinstance(n.ctx, ast.Store)]
        check(all(s == 'self.clock.now' for s in stored), f'{name}: stores only to locals and self.clock.now, in any '
                                                         f'context (got {stored})')
        stores = [n for n in ast.walk(node) if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))]
        moves = [n.value for n in stores if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == 'self.clock.now']
        check(all(isinstance(v, ast.Call) and ast.unparse(v.func) == 'max' and v.args
                  and ast.unparse(v.args[0]) == 'self.clock.now' for v in moves)
              and not any(isinstance(n, (ast.AugAssign, ast.AnnAssign)) and ast.unparse(n.target) == 'self.clock.now'
                          for n in stores),
              f'{name}: moves the clock only forward, as max(self.clock.now, ...)')
        if name in NEW_HELPERS:
            check(not node.decorator_list, f'{name} is a helper, not a rule')
            continue
        check(len(node.decorator_list) == 1 and isinstance(node.decorator_list[0], ast.Call)
              and ast.unparse(node.decorator_list[0].func) == 'rule'
              and not node.decorator_list[0].args
              and all(isinstance(k.value, ast.Name) and k.value.id in strategies
                      for k in node.decorator_list[0].keywords),
              f'{name} is a @rule drawing only named module strategies')
    parity = [n for rule_name in NEW_RULES for n in ast.walk(head_methods[rule_name])
              if isinstance(n, ast.Call) and normal(n.func) == 'self.parity']
    check([ast.unparse(n) for n in parity] == ['self.parity(engine_side, oracle_side)'] * 2,
          'each composite runs self.parity(engine_side, oracle_side), once')
    calls = called(head_methods[COMPOSITE])
    for call, times in (('self.parity', 1), ('self.engine.rename', 1), ('os.rename', 1), ('self.engine.create', 1),
                        ('self.engine.commit_upstream', 1), ('self.engine.write', 1), ('self.engine.fsync', 1),
                        ('self.evict', 1), ('self.poll_data_due', 2)):
        check(calls.count(call) == times, f'{COMPOSITE} calls {call} {times} time(s) (got {calls.count(call)})')
    check(calls.index('self.engine.commit_upstream') < calls.index('self.engine.rename'),
          f'{COMPOSITE} commits the file before the rename, so the source holds it and the later commit lags')
    calls = called(head_methods[MOVE_OUT])
    order = ['self.engine.commit_upstream', 'self.engine.rename', 'self.engine.rmdir', 'self.engine.unlink']
    check([c for c in calls if c in order] == order and calls.count('self.parity') == 1
          and [c for c in calls if c in REAL_STEPS] == list(REAL_STEPS),
          f'{MOVE_OUT} commits, moves the file out, removes its directory and unlinks it, in that order, on both '
          f'sides, under parity once')
    check('self.scheduler.poll' in called(head_methods['poll_data_due']),
          'poll_data_due commits through a scheduler poll (the scheduler route)')
    head_doc = ast.get_docstring(head_class) or ''
    words = [m.group(1).lower() for m in COMPOSITES.finditer(head_doc)]
    counts = [int(w) if w.isdigit() else NUMBERS.get(w) for w in words]
    check(counts == [BASE_COMPOSITES + len(NEW_RULES)],
          f'the class docstring counts {BASE_COMPOSITES + len(NEW_RULES)} composites, once (got {words})')
    flat = ' '.join(head_doc.split())
    for phrase in ('scheduler poll', 'eviction', '`fsync`', 'its directory', 'moved out of a directory'):
        check(phrase in flat, f'the class docstring names {phrase!r} in the new composites')


# -- R7 and the Constraints: records -------------------------------------------------------------------------

def added_lines(against: str, *paths: str) -> list[tuple[str, str]]:
    out, current = [], ''
    for line in git('diff', '-U0', against, 'HEAD', '--', *paths).splitlines():
        if line.startswith('+++ '):
            current = line[6:]
        elif line.startswith('+') and not line.startswith('+++'):
            out.append((current, line[1:]))
    return out


def mode_records() -> None:
    check(git_ok('cat-file', '-e', f'{BASELINE}^{{commit}}'), f'the base {BASELINE} is in the repository')
    dirty = git('status', '--porcelain', '--', 'AGENTS.md', 'ROADMAP.md', 'issues', 'src', 'tests').split('\n')
    dirty = [line for line in dirty if line.strip()]
    check(not dirty, f'the records are committed, so what is graded is what the commit holds (got {dirty})')
    whole = git('diff', '--name-only', BASELINE, 'HEAD').split()
    beyond = [f for f in whole if f not in ALLOWED + (OWN_SEED,) and not f.startswith(f'spec/{SLUG}/')]
    check(not beyond, f'since the base the branch touches only {list(ALLOWED) + [OWN_SEED]} and spec/{SLUG}/ '
                      f'(got {beyond})')
    for path in FROZEN:
        check(git_ok('diff', '--quiet', BASELINE, 'HEAD', '--', path), f'{path} is byte-identical to the base')
    tests = git('diff', '--name-only', BASELINE, 'HEAD', '--', 'tests').split()
    check(tests == [MODEL], f'{MODEL} is the only tests/ file the branch changes (got {tests})')
    since = git('diff', '--name-only', plan_commit(), 'HEAD').split()
    stray = [f for f in since if f not in ALLOWED and not f.startswith(f'spec/{SLUG}/')]
    check(not stray, f'since the plan commit the branch touches only {list(ALLOWED)} and spec/{SLUG}/ '
                     f'(got {stray})')
    added = added_lines(BASELINE, 'src', 'tests')
    check(bool(added), f'the branch adds lines under tests/ ({len(added)})')
    long = [f'{file}: {len(line)}' for file, line in added if len(line) > 120]
    check(not long, f'every added src/ and tests/ line fits 120 characters (got {long})')
    cites = [f'{file}: {line.strip()[:70]!r}' for file, line in added
             if SPEC_ID.search(line) or SEED_PATH.search(line) or SLUG in line]
    check(not cites, f'no added src/ or tests/ line cites a spec id or seed (got {cites})')
    agents = Path('AGENTS.md').read_text()
    timings = re.findall(r'unit\s+# \d+ tests, ~(\d+) s|\d+ unit tests in ~(\d+) s serial', agents)
    stated = sorted({int(a or b) for a, b in timings})
    check(len(timings) == 2 and stated == [SERIAL_SECONDS],
          f'AGENTS.md states the serial unit suite at ~{SERIAL_SECONDS} s in § Commands and § Verification '
          f'(got {stated} from {len(timings)} places)')
    seed = Path(SOURCE_SEED)
    check(seed.exists(), f'{SOURCE_SEED} exists')
    text = seed.read_text()
    check(re.search(r'^status: unshaped$', text, re.MULTILINE) is not None, f'{SOURCE_SEED} is status: unshaped')
    for token in SOURCE_SEED_CARRIES:
        check(token in text, f'{SOURCE_SEED} carries {token!r}')
    roadmap = Path('ROADMAP.md').read_text()
    check(f'**Seed:** [`{SOURCE_SEED}`]({SOURCE_SEED})' in roadmap, f'ROADMAP.md lists {SOURCE_SEED} as a Seed')
    check(re.search(rf'^status: adopted:{SLUG}$', Path(OWN_SEED).read_text(), re.MULTILINE) is not None,
          f'{OWN_SEED} stays adopted:{SLUG} until the roadmap step retires it')
    naming = sorted({hit.split(':', 1)[1] for hit in
                     git('grep', '-l', '-F', OWN_SEED, 'HEAD', '--', '.', ':(exclude)spec', UNSHOWN).split()})
    check('ROADMAP.md' in naming and set(naming) <= {OWN_SEED, 'ROADMAP.md'},
          f'outside spec/ only ROADMAP.md (and the seed itself) name {OWN_SEED} (got {naming})')
    lines = [line for line in roadmap.splitlines() if OWN_SEED in line]
    check(len(lines) == 1 and lines[0].startswith('**Seed:**'),
          f'ROADMAP.md names {OWN_SEED} only in its Seed line, so retiring the entry leaves no dangling name')
    base_lines = show(BASELINE, MODEL).splitlines()
    head_lines = show('HEAD', MODEL).splitlines()
    cited = git('grep', '-n', '-E', r'test_model\.py`?:[0-9]', 'HEAD', '--', '.', ':(exclude)spec', UNSHOWN)
    for hit in cited.splitlines():
        for match in CITE.finditer(hit):
            low = int(match.group(1))
            high = int(match.group(2) or low)
            check(base_lines[low - 1:high] == head_lines[low - 1:high],
                  f'{hit.split(":", 2)[1]}: test_model.py:{low}{"-" + str(high) if high != low else ""} '
                  f'names the text it named at the base')


# -- R8: the suite and the seeds -----------------------------------------------------------------------------

def stated_unit_count() -> int:
    text = Path('AGENTS.md').read_text()
    found = set(int(n) for n in re.findall(r'(\d+) unit tests', text))
    found |= set(int(n) for n in re.findall(r'\((\d+) `unit`', text))
    found |= set(int(n) for n in re.findall(r'pytest -m unit\s+# (\d+) tests', text))
    found |= set(int(n) for n in re.findall(r'(\d+) deselected', text))
    check(len(found) == 1, f'AGENTS.md states one unit-test count (got {sorted(found)})')
    return found.pop()


def stated_engine_count() -> set[int]:
    return {int(n) for n in re.findall(r'(\d+) engine tests', Path('AGENTS.md').read_text())}


def mode_suite() -> None:
    sync = run(['uv', 'sync', '--locked', '-q'])
    check(sync.returncode == 0, f'uv sync --locked exits 0 (got {sync.returncode})')
    stray = Path('.hypothesis').exists()
    lint = run(['tools/lint.sh'], env={'CI': 'true'})
    check(lint.returncode == 0, f'tools/lint.sh exits 0 (got {lint.returncode})')
    began = time.monotonic()
    proc, report = pytest(['-n', 'auto', '-m', 'unit'], 'suite', env={'CI': 'true'})
    say(f'  time  pytest -n auto -m unit: {time.monotonic() - began:.0f} s')
    no_red(report, '-m unit under CI=true')
    passed = report.count('passed')
    check(proc.returncode == 0, f'pytest exits 0 (got {proc.returncode})')
    line, counts = summary(ANSI.sub('', proc.stdout), 'the suite')
    check(counts['passed'] == passed and counts['failed'] == counts['error'] == 0,
          f'pytest\'s summary line ({line!r}) agrees with the JUnit count ({passed} passed)')
    check(passed >= UNIT_FLOOR, f'-m unit: {passed} passed >= {UNIT_FLOOR}')
    check(report.outcome(*MODEL_ID) == ['passed'], 'test_model passed')
    for name in (SCHEDULER_TEST,) + SPARED_TESTS:
        check(report.outcome(REPLAY_CLASS, name) == ['passed'], f'{name} passed')
    check(stray or not Path('.hypothesis').exists(), 'the suite left no .hypothesis/ in the tree')
    stated = stated_unit_count()
    check(passed + report.count('skipped') == stated, f'AGENTS.md states {stated} unit tests; the run collected '
                                                      f'{passed} passed + {report.count("skipped")} skipped')
    engine = sum(c.classname.startswith('tests.engine.') for c in report.cases)
    check(stated_engine_count() == {engine}, f'AGENTS.md\'s engine-test count is the {engine} the run collected')


def mode_timing() -> None:
    """R7: the serial suite's wall time, measured here, rounds to AGENTS.md's figure (within ten seconds)."""
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    proc, report = pytest(['-m', 'unit'], 'timing', env={'CI': 'true'})
    no_red(report, 'serial -m unit under CI=true')
    stated = re.search(r'=+ [^=]*\bpassed\b[^=]* in ([0-9.]+)s', ANSI.sub('', proc.stdout))
    check(stated is not None, 'pytest printed its summary line with the suite\'s duration')
    took = float(stated.group(1))                    # pytest's own figure: the session, not uv's start-up
    say(f'  time  serial pytest -m unit: {took:.0f} s (pytest\'s summary line)')
    # Load on a shared host only slows a run, so the lower bound is exact and the upper one is a warning: a run more
    # than 10 s under AGENTS.md's figure shows the figure overstates; one over it is re-measured on an idle host.
    check(took >= SERIAL_SECONDS - 10, f'the serial suite took {took:.0f} s by pytest\'s count, not more than 10 s '
                                       f'under AGENTS.md\'s ~{SERIAL_SECONDS} s')
    if took > SERIAL_SECONDS + 10:
        say(f'  WARN  {took:.0f} s is more than 10 s over AGENTS.md\'s ~{SERIAL_SECONDS} s: re-measure on an idle host '
            f'before changing the figure')


SEED_TIMEOUT = 300                # a passing seed takes about 30 s; a failing one shrinks for minutes


def seed_runs(seeds: str, with_census: bool) -> None:
    """From committed state: no CI, no .hypothesis/ before any run, two workers at most."""
    low, high = (int(x) for x in seeds.split('-'))
    check(git('status', '--porcelain', '--', 'src', 'tests', f'spec/{SLUG}').strip() == '',
          'src/, tests/ and the gate are committed (the runs read HEAD)')
    lanes = [list(range(low + i, high + 1, 2)) for i in (0, 1)]
    label = 'reach-seeds' if with_census else 'seeds'
    workers = [(tree_at('HEAD', f'{label}-{i}', ci=False), lane) for i, lane in enumerate(lanes) if lane]
    aside = scratch(f'{label}-hypothesis')
    token = f'route-gate-{uuid.uuid4().hex}'
    saved = dict(os.environ)
    environment = {k: v for k, v in saved.items() if k not in ('PYTHONPATH', *CI_VARS)}

    def start(tree: Path, venv: dict[str, str], seed: int) -> tuple[subprocess.Popen, Path, Path]:
        db = tree / '.hypothesis'
        if db.exists():
            shutil.move(str(db), str(aside / f'before-seed-{seed}'))   # moved aside, never deleted
        check(not db.exists(), f'seed {seed}: the tree holds no .hypothesis/')
        where = scratch(f'{label}-{seed}')
        xml, out = where / 'junit.xml', where / 'census.json'
        argv = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'junit_family=xunit1',
                '-o', f'junit_suite_name={token}', f'--junitxml={xml}']
        extra = {'HYPOTHESIS_STORAGE_DIRECTORY': str(where / 'hypothesis')}     # never shared by seeds
        if with_census:
            argv += ['-p', 'route_census']
            extra.update(PYTHONPATH=str(plugin_of(tree)), ROUTE_CENSUS_OUT=str(out))
        argv += [MODEL, f'--hypothesis-seed={seed}']
        say(f'  run   seed {seed}: {" ".join(argv)}')
        with open(where / 'output.txt', 'w') as log:
            child = subprocess.Popen(argv, cwd=str(tree), env={**environment, **venv, **extra},
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        GROUPS.add(child.pid)
        return child, xml, out

    running: dict[int, tuple] = {}
    queues = [list(lane) for _, lane in workers]
    try:
        while any(queues) or running:
            for index, ((tree, venv), _) in enumerate(workers):
                if index not in running and queues[index]:
                    seed = queues[index].pop(0)
                    running[index] = (seed, *start(tree, venv, seed), time.monotonic())
            time.sleep(0.5)
            left()                                  # past the deadline: CannotRun, and `finally` kills the runs
            for index, (seed, child, xml, out, began) in list(running.items()):
                if child.poll() is None:
                    if time.monotonic() - began > SEED_TIMEOUT:
                        raise Red(f'seed {seed}: test_model ran past {SEED_TIMEOUT} s (a failing seed shrinks)')
                    continue
                del running[index]
                GROUPS.discard(child.pid)
                outcome = junit(xml).outcome(*MODEL_ID)
                check(outcome == ['passed'], f'seed {seed}: test_model passes ({outcome})')
                if with_census:
                    found = json.loads(out.read_text()) if out.exists() else None
                    check(found is not None and found['engines'] >= 2 * EXAMPLES,
                          f'seed {seed}: the census ran the whole draw')
                    show_census(found, f'seed {seed}')
                    reached(found, f'seed {seed}', {route: 1 for route in SEED_ROUTES})
    finally:
        for seed, child, *_ in running.values():
            if child.poll() is None:
                kill_group(child.pid)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                say(f'  WARN  seed {seed}: its run did not exit within 10 s of SIGKILL')
            GROUPS.discard(child.pid)
    try:
        leftover = subprocess.run(['pgrep', '-f', token], capture_output=True, text=True)
    except OSError:
        leftover = None
    if leftover is not None and leftover.returncode in (0, 1):   # pgrep may be blocked in a sandbox; the kill stands
        check(leftover.stdout.strip() == '', f'no seed run outlives the gate (got {leftover.stdout.split()})')


def mode_seeds(seeds: str = '1-20') -> None:
    seed_runs(seeds, with_census=False)


def mode_reach_seeds(seeds: str = '1-20') -> None:
    seed_runs(seeds, with_census=True)


# -- the ledger and the push ---------------------------------------------------------------------------------

def blocks(text: str) -> list[str]:
    """Prose paragraphs and list items (with their continuation lines); headings are skipped."""
    out, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        starts_item = re.match(r'(- |\* |\d+\. )', stripped) is not None
        if not stripped or stripped.startswith('#') or starts_item or stripped.startswith('**Verify'):
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
        if not DEFERRAL.search(block.replace(SLUG, '')):       # the slug itself is not deferral wording
            continue
        seeds = re.findall(r'issues/([a-z0-9-]+)\.md', block)
        check(bool(seeds), f'deferral names a seed: {block[:100]!r}')
        for name in seeds:
            check(Path(f'issues/{name}.md').exists(), f'issues/{name}.md exists')
            check(f'issues/{name}.md' in roadmap, f'ROADMAP.md lists issues/{name}.md')
            named.add(name)
    check('model-source-served-reach' in named, f'the ledger reached the deferral to {SOURCE_SEED}')


def mode_prepush() -> None:
    check(git('status', '--porcelain').strip() == '', 'working tree is clean')
    mode_suite()
    mode_records()
    text = git('log', '--format=%B', 'origin/main..HEAD') + git('diff', 'origin/main...HEAD', '--', '.', UNSHOWN)
    hits = sorted(set(HOME_PATH.findall(text)))
    check(not hits, f'no home-directory paths in the branch diff or commit messages (got {hits})')


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
    """From the run record: every job concluded success; nothing is tolerated."""
    target = pushed_sha(sha)
    branch = git('branch', '--show-current').strip()
    if run_json:
        if not Path(run_json).exists():
            raise CannotRun(f'{run_json} does not exist yet: the foreman saves the run record there')
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
    check(git_ok('diff', '--quiet', 'origin/main', target, '--', '.github/workflows/ci.yml'),
          'ci.yml at the pushed commit is main\'s (no deselection or tolerance added)')
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
        found = by_id if by_id.exists() else by_label
        if not found.exists():
            raise CannotRun(f'no saved log for {name} in {log_dir}')
        return ANSI.sub('', found.read_text())

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
    skipped = sum(int(n) for n, _, _ in reasons)
    check(skipped >= FUSE_UNIT_FLOOR and skipped == counts['skipped'],
          f'{CI_MACOS}: {skipped} libfuse skips >= {FUSE_UNIT_FLOOR}, matching the summary line')


MODES = {'model': mode_model, 'reach': mode_reach, 'detect': mode_detect, 'mutants': mode_mutants,
         'timing': mode_timing,
         'records': mode_records, 'suite': mode_suite, 'reach_seeds': mode_reach_seeds, 'seeds': mode_seeds,
         'ledger': mode_ledger, 'prepush': mode_prepush, 'ci': mode_ci}
OPTIONS = {'ci': ('run_json', 'log_dir', 'sha'), 'seeds': ('seeds',), 'reach_seeds': ('seeds',)}


def main(argv: list[str]) -> int:
    global ROOT
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
    ROOT = Path(root)
    for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(number, on_signal)
    for key in ('run_json', 'log_dir'):          # relative paths name places in the worktree
        if options.get(key):
            options[key] = str((ROOT / options[key]).resolve())
    status = 0
    try:
        for name in modes:
            say(f'[{name}]')
            began = time.monotonic()
            try:
                MODES[name](**{k: v for k, v in options.items() if k in OPTIONS.get(name, ())})
            except Red as error:
                say(f'  RED   {error}')
                status = 1
                break
            except CannotRun as error:
                say(f'  CANNOT RUN  {error}')
                status = 3
                break
            finally:
                say(f'  time  {name}: {time.monotonic() - began:.0f} s')
        else:
            say('GREEN')
    finally:
        (ROOT / LOCAL).mkdir(parents=True, exist_ok=True)
        (ROOT / LOCAL / f'{time.strftime("%Y%m%d-%H%M%S")}-{"-".join(modes)}.log').write_text('\n'.join(LOG) + '\n')
    return status


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
