# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the truncate-recording-after-ancestor-rename cycle.

Usage (from the cycle worktree root, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I \
        spec/truncate-recording-after-ancestor-rename/verify/cut_gate.py MODE [MODE ...]

Modes:
    regressions R1-R6  each named test is defined once and ends in assert_converged; all pass on this tree; each fix
                       test fails on the baseline engine for its named cause (BASELINE_CAUSE); each pin passes
                       there; every function and class test_replay.py held at the base is unchanged; test_model.py
                       and both conftest.py files are byte-identical to the base; no skip or xfail is added;
                       `truncate`'s code is the base's but for the counter guard's `previous is not None` (its
                       docstring may change); `Catalog.rename` is the only SQL in src/ that sets an entry's path,
                       and its one call sits inside `rename`'s `with self._source_lock:` block, which begins with
                       `_vacate` (the gate pins the chosen shape, not only R4)
    mutants     R6     each mutant (planted into a scratch copy of HEAD's src/) makes every test it names fail for
                       its named cause and leaves the rest of test_replay.py runnable; every new test is named by one
    records     R7     AGENTS.md § 5, § 10, the high-blast-radius tables, § Known open violations, the counts;
                       the truncate and rename docstrings and the source lock's comment; root PLAN.md; the stress
                       seed's dated lines (Q3's home, the replay bullet, the stage-move lag); test_replay.py's
                       module docstring; outside spec/ only the seed and its ROADMAP.md Seed line name the seed;
                       added lines in src/ and tests/ are at most 120 characters and carry no spec ids; touched
                       seeds' file:line cites hold
    suite       R8     CI=true: tools/lint.sh exits 0; pytest -n auto -m unit has 0 failed, 0 errors, passed > 521
                       and == AGENTS.md's stated count, the engine's share == its stated count, with test_model and
                       every named test passing; no .hypothesis/ is left in the tree
    seeds       R8     test_model passes for each --hypothesis-seed in --seeds=A-B (default 1-20), without CI,
                       two scratch trees built from HEAD, one worker each, no .hypothesis/ before any run
    ledger             every deferral phrase in TECH.md's phase bodies names an issues/ seed that ROADMAP.md lists
    prepush            clean tree, suite, records, then no home-directory path in the branch diff or messages
    ci          R8     the push run for the pushed commit (--sha, default origin/<branch>; later commits may touch
                       only spec/): all four jobs concluded success, Linux passed == the stated count with no
                       skip, macOS skipped only tests/fuse naming libfuse. --run-json/--log-dir read a saved
                       `gh run view --json` record and job logs (<job id>.log or <job name>.log) instead

"The baseline engine" is `src/` at BASELINE (the GOAL's tree, where it counted 521), extracted with `git archive`
into scratch and put first on PYTHONPATH; each run checks which sosfs it imported. "The base" for file comparisons
is the merge base with origin/main; "the plan commit" (the commit that added this cycle's PLAN.md) is the base for
which seeds the build touched. Counts come from pytest's JUnit XML, never from the exit code. Every pytest run has
HYPOTHESIS_STORAGE_DIRECTORY in its own scratch directory and runs with -p no:cacheprovider, so it leaves neither
.hypothesis/ nor .pytest_cache/ in the tree. Scratch and a copy of this run's output go under .local/cut-gate/
(gitignored, survives a reboot).

Footprint: one subprocess at a time, except `seeds`, which runs two pytest processes at once (each its own scratch
tree, environment and process group, killed as a group on the first red or a per-seed timeout). `suite` and
`prepush` run pytest with -n auto (one worker per core, about 1.5 GB peak on a 10-core machine); every other pytest
run is serial and under 500 MB. The new tests start two daemon threads each and bound every wait at 10 s, so a
mutant that deadlocks them fails in seconds. Measured on the planning prototype (2026-11-08): see TECH.md.
Nothing is deleted; the gate never pushes and never writes to the tracked tree.

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
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


SLUG = 'truncate-recording-after-ancestor-rename'
BASELINE = '630a406'          # the GOAL's tree; src/ and tests/ are unchanged from it to the shaping commits
UNIT_FLOOR = 521              # R8: strictly more than this pass
MODEL = 'tests/engine/test_model.py'
MODEL_ID = ('tests.engine.test_model.TestOverlayModel', 'runTest')       # JUnit (classname, name)
REPLAY = 'tests/engine/test_replay.py'
OVERLAY = 'src/sosfs/engine/overlay.py'
FROZEN = (MODEL, 'tests/engine/conftest.py', 'tests/conftest.py')       # byte-identical to the base
OWN_SEED = f'issues/{SLUG}.md'                     # retired by `code terrace roadmap` after landing
STRESS_SEED = 'issues/freethreaded-concurrency-stress.md'               # Q3's home
TOKEN_SEED = 'issues/entry-identity-token.md'
# Q3: the calls that hold a path lock and write the catalog by path, named in the stress seed's dated line.
SAME_SHAPE = ('evict_copy', '_set_meta', '_mark_handle_dirty', '_record', 'commit_row', '_commit_hop',
              'commit_upstream', 'commit_to_shared', 'create', 'mkdir', '_remove')
LEDGER_SEEDS = ('freethreaded-concurrency-stress', 'entry-identity-token')
# The sentence the fix makes false wherever it stood (truncate's docstring, § 5, the open-violation row).
STALE = ('match no row', 'moves the catalog without the source lock')

# Tests the build writes into tests/engine/test_replay.py (names fixed here, PLAN § 6).
STAGED = 'test_truncate_without_a_handle_of_a_staged_copy_meets_a_directory_rename_past_its_vacate'
COUNTED = 'test_truncate_without_a_handle_counts_its_cut_under_a_directory_rename_past_its_vacate'
SERVING = 'test_truncate_without_a_handle_decides_its_serving_copy_outside_a_directory_rename_past_its_vacate'
DIRTY = 'test_truncate_without_a_handle_marks_dirty_outside_a_directory_rename_past_its_vacate'
SOURCE = 'test_truncate_without_a_handle_of_a_source_served_file_meets_an_ancestor_rename_past_its_vacate'
PROMOTE = 'test_truncate_without_a_handle_promotes_outside_a_directory_rename_past_its_vacate'
LOOKUP = 'test_truncate_without_a_handle_promotion_looks_up_outside_a_directory_rename_past_its_vacate'
WAITING = 'test_truncate_without_a_handle_records_its_cut_before_a_waiting_directory_rename_moves_it'
STAGE_FREE = 'test_rename_moves_stage_copies_outside_the_source_lock'
FIX_TESTS = (STAGED, COUNTED, SERVING, DIRTY, SOURCE, PROMOTE)
PIN_TESTS = (LOOKUP, WAITING, STAGE_FREE)          # pass on the baseline too; M2, M7 and M4 bite them
NEW_TESTS = FIX_TESTS + PIN_TESTS
# Why each fix test fails on the baseline engine (measured on the planning prototype, PLAN § 4): the rename's
# catalog move lands inside the cut, so the recording misses (the sizes), the counter moves alone, the serving
# decision finds no copy, the dirty mark or the promotion's recording finds no entry.
BASELINE_CAUSE = {STAGED: r'assert 5 == 2', SOURCE: r'assert 200 == 2', COUNTED: r'AssertionError: scratch',
                  SERVING: r'EngineError', DIRTY: r'LookupError', PROMOTE: r'LookupError'}
# The same tests on HEAD with the hold removed (M1, M3): HEAD has no `previous is not None`, so the staged recording
# that the baseline skipped silently now fails loudly reading a residency the move took off the path.
REMOVED_CAUSE = {**BASELINE_CAUSE, STAGED: r"'NoneType' object has no attribute 'size'"}

# (id, ((file, exact text in the built tree, replacement), ...), tests that must fail, why). Anchors are the built
# tree's own text; a mutant whose anchor is not found exactly once is red, so a build that reshapes a guarded line
# re-anchors it here in the same commit (PLAN § 6). `why` is the regex each named test's failure must match (a dict:
# one per test), so a kill counts only through the assertion that guards the decision. M1-M4 attack
# the fix's decisions: whether the rename's catalog move commits under the source lock, for which renames, in which
# lock order, and whether the stage moves stay outside. M5-M7 attack the tests' strength with engines the
# fix-removal mutants do not cover: a promotion that refuses after its copy and strands the stage bytes, a cut that
# records by id and then refuses having cut, and a counter that moves only when a cut grows the copy.
HOLD = ('            with self._source_lock:\n'
        '                self._vacate(new, mover=entry.id)\n'
        '                now = self.clock()\n'
        '                with self.db.transaction():\n')
UNHELD = HOLD.replace('with self._source_lock:', 'with contextlib.nullcontext():')
STAGE_MOVE = '                    tier.rename(_pure(old), _pure(new))\n'
TRANSFER = 'src/sosfs/engine/transfer.py'
RECORD = '        with self.db.transaction():\n            previous = self.catalog.get_residency(path, dest.name)\n'
COUNTER = ('                        if serving != self.source.name:\n'
           '                            self.registry.adjust_usage(serving, bytes=length - previous.size)\n')
RECORDING = ('                    previous = self.catalog.get_residency(path, serving)\n'
             '                    with self.db.transaction():\n'
             '                        self.catalog.update_meta(path, size=length, mtime=now)\n'
             '                        self.catalog.update_residency(path, serving, size=length, mtime=now)\n'
             + COUNTER)
MUTANTS = (
    ('M1 the rename commits its catalog move outside the source lock',
     ((OVERLAY, HOLD, UNHELD),), FIX_TESTS, REMOVED_CAUSE),
    ('M2 the rename takes the source lock inside its catalog transaction',
     ((OVERLAY, HOLD, '            self._vacate(new, mover=entry.id)\n'
                      '            now = self.clock()\n'
                      '            with self.db.transaction(), self._source_lock:\n'
                      '                if True:\n'),),
     FIX_TESTS + (LOOKUP,), r'database is locked'),
    ('M3 only a file rename holds the source lock',
     ((OVERLAY, HOLD, HOLD.replace('with self._source_lock:',
                                   'with self._source_lock if not moving_dir else contextlib.nullcontext():')),),
     FIX_TESTS, REMOVED_CAUSE),
    ('M4 the rename moves the stage copies under the source lock',
     ((OVERLAY, STAGE_MOVE, '                    with self._source_lock:\n'
                            '                        tier.rename(_pure(old), _pure(new))\n'),),
     (STAGE_FREE,), r'assert \[False\] == \[True\]'),
    ('M5 a promotion whose entry moved refuses after its copy, leaving the stage bytes',
     ((OVERLAY, HOLD, UNHELD),
      (TRANSFER, RECORD, "        if self.catalog.get(path, include_deleted=True) is None:\n"
                         "            self.journal.rollback(intent)\n"
                         "            raise FileNotFoundError(2, 'No such file or directory', path)\n" + RECORD)),
     (PROMOTE,), r"'moved/s\.txt': b'small'"),
    ('M6 the cut records by id, then refuses when the file moved',
     ((OVERLAY, HOLD, UNHELD),
      (OVERLAY, RECORDING, RECORDING.replace('(path, ', '(moved, ').replace(
          '                    previous =', '                    moved = self.catalog.by_id(current.id).path\n'
                                           '                    previous =')
       + "                    if moved != path:\n"
         "                        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)\n")),
     (STAGED, SOURCE), r'assert 2 == (?:5|200)'),
    ('M7 the stage counter moves only when the cut grows the copy',
     ((OVERLAY, COUNTER, COUNTER.replace('if serving != self.source.name:',
                                         'if serving != self.source.name and length > previous.size:')),),
     (WAITING,), r'AssertionError: scratch'),
)
# Equivalent mutants, argued in PLAN § 4 and not run: `_vacate` before the hold (the cut then returns, recorded);
# only a directory rename holds (a file's own rename needs the path lock the cut holds).


DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|\bdefer(?:s|red|ring|ral|rals)?\b|'
                      r'split (?:out|from)|stays? open|remains? open|left open|a later cycle|out of scope|owned by|'
                      r'\bhome(?:d|s)?\b(?![- ](?:director|path))', re.IGNORECASE)
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')    # written so this line cannot match itself
TOLERANCE = re.compile(r'\bxfail\b|\bskipif\b|mark\.skip\b|pytest\.skip\(', re.IGNORECASE)
SPEC_ID = re.compile(r'\b(?:R[1-9]|P[1-9]|Q[1-9])\b')
CITE = re.compile(r'(?<![\w/.-])((?:[\w-]+/)*([\w-]+\.py)):(\d+)(?:-(\d+))?')
PIN = re.compile(r'\b(?:holds?|held|is|are|pinned)\s+at\s+`([0-9a-f]{7,40})`')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
ROOT = Path.cwd()
LOCAL = Path('.local/cut-gate')
RUN_TIMEOUT = 300                 # every run but a seed's; the slowest, the suite, takes about 60 s


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


def run(argv: list[str], env: dict[str, str] | None = None, quiet: bool = False, cwd: str | None = None,
        drop: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    say(f'  run   {" ".join(argv)}')
    saved = dict(os.environ)                 # a child's environment starts from a saved copy, never a cleared one
    inherited = {k: v for k, v in saved.items() if k != 'PYTHONPATH' and k not in drop}
    # Its own process group, killed whole on a timeout: a mutant that leaves the source lock held, or a test whose
    # helper thread waits on a lock the test holds, hangs rather than fails, and must go red, not stall the gate.
    child = subprocess.Popen(argv, env={**inherited, **(env or {})}, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=cwd, start_new_session=True)
    try:
        out, err = child.communicate(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        raise Red(f'{" ".join(argv[:6])} ... ran past {RUN_TIMEOUT} s (a hang: its process group was killed)')
    proc = subprocess.CompletedProcess(argv, child.returncode, out, err)
    if not quiet:
        for line in (proc.stdout + proc.stderr).strip().splitlines()[-6:]:
            say(f'        {line}')
    return proc


def git(*args: str) -> str:
    return run(['git', *args], quiet=True).stdout


def base() -> str:
    found = git('merge-base', 'origin/main', 'HEAD').strip()
    if not found:
        raise CannotRun('no merge base with origin/main')
    return found


def plan_commit() -> str:
    found = git('log', '--diff-filter=A', '--format=%H', '--', f'spec/{SLUG}/PLAN.md').split()
    if not found:
        raise CannotRun(f'no commit added spec/{SLUG}/PLAN.md')
    return found[-1]


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

    def message(self, classname: str, name: str) -> str:
        return ' '.join(c.message for c in self.cases if (c.classname, c.name) == (classname, name))

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
                message = (node.get('message') or '') + ' ' + (node.text or '')[-2000:]
                break
        report.cases.append(Case(case.get('classname') or '', case.get('name') or '', outcome, message))
    return report


def pytest(args: list[str], label: str, env: dict[str, str] | None = None, cwd: str | None = None,
           drop: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess, Report]:
    where = scratch(label)
    xml = where / 'junit.xml'
    # Hypothesis writes .hypothesis/ (its constants cache) into the working tree even under CI=true; its storage
    # goes to this run's scratch instead, so no run leaves one behind.
    env = {'HYPOTHESIS_STORAGE_DIRECTORY': str(where / 'hypothesis'), **(env or {})}
    argv = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-rfEs', '-o', 'junit_family=xunit1',
            f'--junitxml={xml}'] + args
    proc = run(argv, env=env, cwd=cwd, drop=drop)
    return proc, junit(xml)


def no_red(report: Report, scope: str) -> None:
    reds = [f'{c.classname}::{c.name} ({c.outcome})' for c in report.cases if c.outcome in ('failed', 'error')]
    check(not reds, f'{scope}: 0 failed and 0 errors (got {reds})')


CLASSNAME = REPLAY.removesuffix('.py').replace('/', '.')


# -- engines from committed state ----------------------------------------------------------------------------

def extract_src(rev: str, label: str) -> Path:
    """`src/` at `rev`, extracted from git (committed state, never the working copy)."""
    where = scratch(label)
    archive = subprocess.run(['git', 'archive', rev, 'src'], capture_output=True)
    if archive.returncode != 0:
        raise CannotRun(f'git archive {rev} failed: {archive.stderr.decode()[-200:]}')
    subprocess.run(['tar', '-x', '-C', str(where)], input=archive.stdout, check=True)
    return where / 'src'


def with_engine(src: Path, args: list[str], label: str) -> Report:
    """Run pytest with the engine at `src` first on the import path, asserting that is what loaded."""
    probe = run(['uv', 'run', '--frozen', 'python', '-c', 'import sosfs; print(sosfs.__file__)'],
                env={'PYTHONPATH': str(src)}, quiet=True)
    check(probe.stdout.strip().startswith(str(src)), f'{label}: the engine under test is the one imported '
                                                      f'({probe.stdout.strip() or probe.stderr.strip()[-120:]})')
    _, report = pytest(args, label, env={'PYTHONPATH': str(src)})
    return report



# -- regressions ---------------------------------------------------------------------------------------------

CONVERGED_NAMES = {'queued', 'pending', 'listed', 'deleted', 'tree', 'view', 'sizes', 'stop', 'start'}



def names_in(node: ast.AST) -> set[str]:
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def top_level(text: str) -> dict[str, str]:
    """Each top-level function and class of a module, by name, as its unparsed source."""
    return {n.name: ast.unparse(n) for n in ast.parse(text).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def function_source(text: str, name: str) -> str:
    """The exact source lines of method `name` of OverlayEngine, or '' when it is not defined once."""
    lines = text.splitlines()
    found = [n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == name]
    return '\n'.join(lines[found[0].lineno - 1:found[0].end_lineno]) if len(found) == 1 else ''



def regression_shape() -> None:
    module = ast.parse(Path(REPLAY).read_text())
    defs = [n for n in module.body if isinstance(n, ast.FunctionDef)]
    by_name = {n.name: n for n in defs}
    for name in NEW_TESTS:
        check([n.name for n in defs].count(name) == 1, f'{name} is defined once in {REPLAY}')
    helpers = {n.name for n in defs if not n.name.startswith('test_')
               and isinstance(n.body[-1], ast.Expr) and isinstance(n.body[-1].value, ast.Call)
               and getattr(n.body[-1].value.func, 'id', None) == 'assert_converged'}
    for name in NEW_TESTS:
        last = by_name[name].body[-1]
        call = last.value if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) else None
        ends = call is not None and getattr(call.func, 'id', None) in {'assert_converged'} | helpers
        check(ends, f'{name} ends in assert_converged(...), directly or through a helper that does')
    waiting = ast.unparse(by_name[WAITING])
    check("outcome['stopped']" in waiting and "outcome['waited']" in waiting,
          f'{WAITING} asserts that the cut reached its stop and that the rename waited (a race, not a sequence)')


def unchanged_code() -> None:
    against = base()
    old = top_level(git('show', f'{against}:{REPLAY}'))
    new = top_level(Path(REPLAY).read_text())
    changed = sorted(name for name, source in old.items() if new.get(name) != source)
    check(not changed, f'every function and class {REPLAY} held at the base is still defined, unchanged '
                       f'({len(old)} checked; changed {changed})')


def code_of(text: str, name: str) -> str:
    """Method `name` of OverlayEngine, unparsed, without its docstring; '' when it is not defined once."""
    found = [n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(found) != 1:
        return ''
    node = found[0]
    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None),
                                                                        ast.Constant):
        node.body = node.body[1:]
    return ast.unparse(node)


def truncate_kept() -> None:
    """Geoffrey's signed-off property: `truncate`'s code is the base's but for the unreachable guard."""
    before = code_of(git('show', f'{base()}:{OVERLAY}'), 'truncate')
    after = code_of(Path(OVERLAY).read_text(), 'truncate')
    guard = 'if previous is not None and serving != self.source.name:'
    check(before.count(guard) == 1, 'the base truncate carries the counter guard once')
    check(bool(after) and after == before.replace(guard, 'if serving != self.source.name:'),
          'OverlayEngine.truncate is the base\'s code with only `previous is not None` dropped from the guard')


def catalog_moves_held() -> None:
    """Every `catalog.rename` call in src/ is inside `rename`'s `with self._source_lock:` block."""
    calls = []
    for path in sorted(Path('src').rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'rename' \
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == 'catalog':
                calls.append((str(path), node.lineno))
    check(len(calls) == 1 and calls[0][0] == OVERLAY, f'src/ calls catalog.rename once, in overlay.py ({calls})')
    catalog = Path('src/sosfs/state/catalog.py').read_text()
    setters = [(str(f), n.name) for f in sorted(Path('src').rglob('*.py'))
               for n in ast.walk(ast.parse(f.read_text())) if isinstance(n, ast.FunctionDef)
               and re.search(r'SET\s+path\b', ast.get_source_segment(f.read_text(), n) or '')
               and not any(isinstance(c, ast.FunctionDef) and c is not n for c in ast.walk(n))]
    check(setters == [('src/sosfs/state/catalog.py', 'rename')],
          f'Catalog.rename is the only function in src/ whose SQL sets an entry\'s path ({setters})')
    fields = re.search(r'META_FIELDS[^=]*=\s*frozenset\(\s*\{([^}]*)\}', catalog)
    check(fields is not None and "'path'" not in fields.group(1) and "'parent'" not in fields.group(1),
          'META_FIELDS lets no update_meta call set an entry\'s path or parent')
    method = [n for n in ast.walk(ast.parse(Path(OVERLAY).read_text()))
              if isinstance(n, ast.FunctionDef) and n.name == 'rename']
    check(len(method) == 1, 'OverlayEngine.rename is defined once')
    held = False
    for node in ast.walk(method[0]):
        if isinstance(node, ast.With) and any(ast.unparse(item.context_expr) == 'self._source_lock'
                                              for item in node.items):
            inner = {sub.lineno for sub in ast.walk(node) if isinstance(sub, ast.Call)}
            held = held or calls[0][1] in inner
            check(ast.unparse(node.body[0]).startswith('self._vacate('),
                  'the hold begins with the rename\'s _vacate')
            check(not any(isinstance(s, ast.Attribute) and s.attr in ('hold', '_ensure', 'getattr', 'readdir')
                          for s in ast.walk(node)), 'nothing in the hold takes a path lock, _ensure, getattr or '
                                                    'readdir')
    check(held, 'the catalog.rename call is inside rename\'s `with self._source_lock:` block')


def frozen_files() -> None:
    against = base()
    for name in FROZEN:
        before = subprocess.run(['git', 'show', f'{against}:{name}'], capture_output=True).stdout
        after = Path(name).read_bytes() if Path(name).exists() else b''
        check(before == after, f'{name} is byte-identical to the base ({against[:12]})')
    added = git('diff', '-U0', against, '--', 'tests/').splitlines()
    hits = [line for line in added if line.startswith('+') and not line.startswith('+++') and TOLERANCE.search(line)]
    check(not hits, f'no skip or xfail added under tests/ (got {hits[:3]})')


def mode_regressions() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    regression_shape()
    unchanged_code()
    truncate_kept()
    catalog_moves_held()
    frozen_files()
    _, report = pytest(['-m', 'unit', REPLAY], 'regressions-here')
    no_red(report, f'{REPLAY} on this tree')
    for name in NEW_TESTS:
        check(report.outcome(CLASSNAME, name) == ['passed'], f'{name} ran once and passed')
    report = with_engine(extract_src(BASELINE, 'baseline'), ['-m', 'unit', REPLAY], 'regressions-baseline')
    for name in FIX_TESTS:
        got = report.outcome(CLASSNAME, name)
        cause = BASELINE_CAUSE[name]
        check(got == ['failed'] and re.search(cause, report.message(CLASSNAME, name)) is not None,
              f'{name} fails on the baseline engine for its cause, /{cause}/ (got {got}, '
              f'{report.message(CLASSNAME, name)[:100]!r})')
    for name in PIN_TESTS:
        check(report.outcome(CLASSNAME, name) == ['passed'], f'{name} passes on the baseline engine (a pin)')


# -- mutants -------------------------------------------------------------------------------------------------

def mode_mutants() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    named = {name for _, _, tests, _ in MUTANTS for name in tests}
    check(set(NEW_TESTS) <= named, f'every new test is named by a mutant ({sorted(set(NEW_TESTS) - named)})')
    for label, edits, tests, why in MUTANTS:
        src = extract_src('HEAD', 'mutant')
        texts: dict[str, str] = {}
        for number, (file, old, new) in enumerate(edits, 1):
            text = texts.get(file, (src.parent / file).read_text())
            check(text.count(old) == 1, f'{label}: anchor {number} occurs once in {file}')
            texts[file] = text.replace(old, new)
        for file, text in texts.items():
            (src.parent / file).write_text(text)
        report = with_engine(src, ['-m', 'unit', REPLAY], 'mutant')
        check(report.count('passed') > 0, f'{label}: the rest of {REPLAY} still runs ({report.count("passed")} '
                                          f'passed)')
        for name in tests:
            cause = why[name] if isinstance(why, dict) else why
            message = report.message(CLASSNAME, name)
            check(report.outcome(CLASSNAME, name) == ['failed'] and re.search(cause, message) is not None,
                  f'{label}: {name} fails for its cause, /{cause}/ (got {report.outcome(CLASSNAME, name)}, '
                  f'{message[:100]!r})')


# -- records -------------------------------------------------------------------------------------------------

def section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading):]
    stop = re.search(r'^#{2,3} ', rest, re.MULTILINE)
    return rest[:stop.start()] if stop else rest


def bullets(text: str) -> list[str]:
    out, current = [], []
    for line in text.splitlines():
        if line.startswith('- '):
            if current:
                out.append(' '.join(current))
            current = [line[2:].strip()]
        elif line.startswith('  ') and current:
            current.append(line.strip())
        else:
            if current:
                out.append(' '.join(current))
            current = []
    if current:
        out.append(' '.join(current))
    return out


def tables(text: str) -> list[str]:
    """The two high-blast-radius tables' text, in order."""
    region = section(text, '### High-blast-radius paths (human gate)')
    found = re.findall(r'(?:^\|.*\|\n)+', region, re.MULTILINE)
    return [t for t in found if 'engine/overlay.py' in t][:2]


def changed_functions(against: str) -> set[str]:
    """overlay.py functions at HEAD whose body holds a line the branch added or changed."""
    lines = set()
    for hunk in re.finditer(r'^@@ -\S+ \+(\d+)(?:,(\d+))? @@', git('diff', '-U0', against, '--', OVERLAY),
                            re.MULTILINE):
        start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
        lines |= set(range(start, start + max(count, 1)))
    found = set()
    for node in ast.walk(ast.parse(Path(OVERLAY).read_text())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if lines & set(range(node.lineno, node.end_lineno + 1)):
                found.add(node.name)
    return found


def word(name: str, text: str) -> bool:
    return re.search(rf'(?<![\w.]){re.escape(name)}(?!\w)', text) is not None


def unit_counts(text: str) -> set[int]:
    found = set(int(n) for n in re.findall(r'(\d+) unit tests', text))
    found |= set(int(n) for n in re.findall(r'\((\d+) `unit`', text))
    found |= set(int(n) for n in re.findall(r'pytest -m unit\s+# (\d+) tests', text))
    found |= set(int(n) for n in re.findall(r'(\d+) deselected', text))
    return found


def stated_unit_count() -> int:
    found = unit_counts(Path('AGENTS.md').read_text())
    check(len(found) == 1, f'AGENTS.md states one unit-test count (got {sorted(found)})')
    return found.pop()


DATED = re.compile(r'^\s*(?:[-*]|\d+\.) \*?(?:Added |Corrected )?20\d\d-\d\d-\d\d')


def cite_blocks(text: str) -> list[str]:
    """
    One block per list item or paragraph: its own lines, the deeper-indented items nested under it, and, for a
    paragraph, a dated item that follows it directly. A pin ("holds at `<sha>`") counts for the cites in its
    block, so a dated line pins the item it sits under, never a sibling or a distant one.
    """
    units: list[list] = []                       # [indent, kind, lines]; None marks a blank line
    for line in text.splitlines():
        if not line.strip():
            units.append(None)
            continue
        item = re.match(r'^(\s*)(?:[-*]|\d+\.) ', line)
        if item:
            units.append([len(item.group(1)), 'item', [line]])
        elif units and units[-1] is not None:
            units[-1][2].append(line)
        else:
            units.append([len(line) - len(line.lstrip()), 'para', [line]])
    out = []
    for index, unit in enumerate(units):
        if unit is None:
            continue
        indent, kind, lines = unit
        block = list(lines)
        for later in units[index + 1:]:
            if later is None or later[0] <= indent:
                if kind == 'para' and later is not None and later[0] == indent and DATED.match(later[2][0]) \
                        and later is units[index + 1]:
                    block += later[2]
                break
            block += later[2]
        out.append('\n'.join(block))
    return out


def cites_hold(against: str) -> None:
    """
    In each issues/ seed the plan or the build touched, every file:line cite of a file the branch changes is
    pinned (its block says it "holds at `<sha>`", see cite_blocks) or still points at the line it pointed at in
    the base, unchanged.
    """
    changed = {Path(f).name for f in git('diff', '--name-only', against, 'HEAD').split() if f.endswith('.py')}
    touched = [f for f in git('diff', '--name-only', f'{plan_commit()}~1', 'HEAD').split()
               if f.startswith('issues/') and Path(f).exists()]
    seen = 0
    for seed in touched:
        for block in cite_blocks(Path(seed).read_text()):
            pinned = bool(PIN.search(block))
            for match in CITE.finditer(block):
                where, name, low = match.group(1), match.group(2), int(match.group(3))
                high = int(match.group(4) or low)
                if name not in changed or pinned:
                    continue
                seen += 1
                full = next((f for f in git('ls-files', f'*{where}').split() if f.endswith(where)), None)
                check(full is not None, f'{seed}: {match.group(0)} names a tracked file')
                old = git('show', f'{against}:{full}').splitlines()[low - 1:high]
                new = Path(full).read_text().splitlines()[low - 1:high]
                check(old == new, f'{seed}: {match.group(0)} still holds what it cited at the base, or is pinned')
    say(f'  ok    {seen} unpinned cite(s) of changed files in {len(touched)} touched seed(s) checked')



def docstring(text: str, name: str) -> str:
    found = [n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == name]
    return ' '.join((ast.get_docstring(found[0]) or '').split()) if len(found) == 1 else ''


def engine_count(text: str) -> set[int]:
    return set(int(n) for n in re.findall(r'(\d+) engine tests', text))




def added_lines(against: str, *paths: str) -> list[tuple[str, str]]:
    """(file, text) for each line the branch adds under `paths`."""
    out, current = [], ''
    for line in git('diff', '-U0', against, '--', *paths).splitlines():
        if line.startswith('+++ '):
            current = line[6:] if line.startswith('+++ b/') else ''
        elif line.startswith('+') and current:
            out.append((current, line[1:]))
    return out


def init_comment(text: str) -> str:
    """The comment block above `self._source_lock = threading.RLock()` in OverlayEngine.__init__."""
    lines = text.splitlines()
    at = next(i for i, line in enumerate(lines) if 'self._source_lock = threading.RLock()' in line)
    block = []
    for line in reversed(lines[:at]):
        if not line.strip().startswith('#'):
            break
        block.append(line.strip().lstrip('#').strip())
    return ' '.join(reversed(block))


def mode_records() -> None:
    against = base()
    check(git('status', '--porcelain', '--', 'AGENTS.md', 'PLAN.md', 'ROADMAP.md', 'issues', 'src', 'tests').strip()
          == '', 'AGENTS.md, PLAN.md, ROADMAP.md, issues/, src/ and tests/ are committed (records read them)')
    agents = Path('AGENTS.md').read_text()
    source = Path(OVERLAY).read_text()
    flat = lambda text: ' '.join(text.split())                              # noqa: E731
    five = section(agents, '### 5. Committed source path')
    reading = [b for b in bullets(five) if '_stat_held' in b]
    check(len(reading) == 1, '§ 5: one bullet says how reads and the cut resolve the source (it names _stat_held)')
    check(re.search(r'ancestor', reading[0]) is not None and 'catalog move' in reading[0]
          and word('rename', reading[0]) and re.search(r'`?ENOENT`? having cut nothing', reading[0]) is not None
          and re.search(r'waits[^.;]{0,80}recorded', reading[0]) is not None,
          '§ 5: that bullet says an ancestor\'s rename that moves the catalog first makes the cut raise ENOENT having '
          'cut nothing, and one that reaches the lock meanwhile waits until the cut is recorded')
    ten = section(agents, '### 10. Concurrency')
    mutex = [b for b in bullets(ten) if 'source-namespace mutex' in b]
    check(len(mutex) == 1, '§ 10: one source-mutex bullet')
    check(re.search(r'`rename`[^.;]{0,160}`_vacate`[^.;]{0,160}catalog move', mutex[0]) is not None,
          '§ 10: the mutex bullet names a user `rename` as a holder from its `_vacate` through its catalog move')
    check(re.search(r'stage[^.;]{0,120}(?:after|outside|without)', mutex[0]) is not None,
          '§ 10: the mutex bullet says the rename moves its stage copies after releasing it')
    for where, text in (('§ 5', five), ('§ 10', ten), ('the truncate docstring', docstring(source, 'truncate')),
                        ('the rename docstring', docstring(source, 'rename'))):
        hits = [s for s in STALE if s in flat(text)]
        check(not hits, f'{where} no longer says {hits}')
    old_tables, new_tables = tables(git('show', f'{against}:AGENTS.md')), tables(agents)
    check(len(old_tables) == len(new_tables) == 2, 'both high-blast-radius tables are found')
    functions = changed_functions(against) - {'__init__'}      # its change is the source lock's comment
    check({'rename', 'truncate'} <= functions, f'the branch changes rename and truncate ({sorted(functions)})')
    for index, (old, new) in enumerate(zip(old_tables, new_tables), 1):
        lost = sorted(set(re.findall(r'`([^`]+)`', old)) - set(re.findall(r'`([^`]+)`', new)))
        lost = [t for t in lost if not all(word(part, new) for part in re.split(r'[ ,/]+', t) if part)]
        check(not lost, f'table {index} keeps every name it had at the base (lost {lost})')
        missing = sorted(f for f in functions if not word(f, new))
        check(not missing, f'table {index} names every overlay.py function the branch changes (missing {missing})')
    violations = section(agents, '### Known open violations')
    rows = [line for line in violations.splitlines() if line.startswith('| ')]
    check(not any(OWN_SEED in r for r in rows), f'no open-violation row names {OWN_SEED}')
    check(not any(s in r for r in rows for s in STALE), 'no open-violation row still says the recording misses')
    gap = [r for r in rows if TOKEN_SEED in r]
    check(len(gap) == 1, f'the identity-gap row ({TOKEN_SEED}) still stands')
    doc = docstring(source, 'truncate')
    check(word('rename', doc) and 'catalog move' in doc
          and re.search(r'ENOENT before anything is marked or cut', doc) is not None
          and re.search(r'carried the file back', doc) is not None,
          'the truncate docstring scopes the lookup\'s ENOENT: before anything is marked or cut, unless another '
          'rename carried the file back')
    # retuned 2026-11-09: Geoffrey's directive (KB board 2026/11-09-docstring-seed-cites) bars seed paths in source docstrings
    check(re.search(r'stage copies after releasing[^.]*ENOENT after its dirty mark', doc) is not None
          and 'issues/' not in doc,
          'the truncate docstring scopes the stage-move lag\'s ENOENT (after the dirty mark) and cites no issues/ seed')
    cited = run(['git', 'grep', '-n', 'issues/', 'HEAD', '--', 'src', 'tests'], quiet=True)
    check(cited.returncode == 1 and not cited.stdout.strip(),
          f'no file in src/ or tests/ at HEAD cites an issues/ seed (git grep: {cited.stdout.strip()[:120]!r})')
    overclaims = [w for w, t in (('§ 5', five), ('§ 10', ten), ('truncate', doc),
                                 ('rename', docstring(source, 'rename')))
                  if re.search(r'never raises `?ENOENT|never leaves a dirty mark', flat(t))]
    check(not overclaims, f'no record says the cut never raises ENOENT or never leaves a bare dirty mark '
                          f'({overclaims})')
    check('source lock' in docstring(source, 'rename') and 'catalog move' in docstring(source, 'rename'),
          'the rename docstring says it holds the source lock through its catalog move')
    check(word('rename', init_comment(source)) and 'catalog move' in init_comment(source),
          'the source lock\'s comment in __init__ names the rename\'s catalog move among its holds')
    module = flat(Path(REPLAY).read_text().split('"""')[1])
    check('catalog move' in module and '_vacate' in module,
          f'{REPLAY}\'s module docstring names the class it now guards (a rename past its _vacate, its catalog move)')
    plan = Path('PLAN.md').read_text()
    check(Path(OWN_SEED).name not in plan and not any(s in flat(plan) for s in STALE),
          f'PLAN.md no longer names {Path(OWN_SEED).name} or says the recording misses')
    stress = Path(STRESS_SEED).read_text()
    added = flat(' '.join(text for file, text in added_lines(against, STRESS_SEED)))
    dated = [b for b in bullets(stress) if SLUG in b and re.search(r'20\d\d-\d\d-\d\d', b)]
    check(len(dated) >= 2 and SLUG in added, f'{STRESS_SEED}: the branch adds dated lines naming {SLUG} '
                                             f'({len(dated)} found)')
    missing = [name for name in SAME_SHAPE if not word(name, added)]
    check(not missing, f'{STRESS_SEED}: Q3\'s dated line names every same-shape call (missing {missing})')
    check('ENOTEMPTY' in added and re.search(r'stage', added) is not None,
          f'{STRESS_SEED}: a dated line records the stage moves lagging the catalog move (ENOTEMPTY)')
    replay = [b for b in bullets(stress) if b.startswith('**A replay racing a user rename')]
    check(len(replay) == 1 and SLUG in replay[0] and re.search(r'20\d\d-\d\d-\d\d', replay[0]) is not None,
          f'{STRESS_SEED}: the replay-racing-a-rename bullet carries a dated line from {SLUG}')
    tracked = [f for f in git('ls-files').split() if not f.startswith('spec/') and Path(f).is_file()]
    own_file = Path(OWN_SEED).name
    naming = sorted(f for f in tracked if own_file in Path(f).read_text(errors='replace') and f != OWN_SEED)
    check(set(naming) <= {'ROADMAP.md'}, f'outside spec/, only ROADMAP.md names {OWN_SEED} (got {naming})')
    roadmap = Path('ROADMAP.md').read_text()
    lines = [line for line in roadmap.splitlines() if own_file in line]
    check(len(lines) <= 1 and all(line.startswith('**Seed:**') for line in lines),
          f'ROADMAP.md names {OWN_SEED} only on its own entry\'s Seed line, which `code terrace roadmap` retires')
    long = [f'{file}: {len(text)}' for file, text in added_lines(against, 'src', 'tests') if len(text) > 120]
    check(not long, f'no line the branch adds in src/ or tests/ exceeds 120 characters ({long[:3]})')
    ids = [f'{file}: {text.strip()[:60]}' for file, text in added_lines(against, 'src', 'tests', 'README.md')
           if SPEC_ID.search(text)]
    check(not ids, f'no spec id in a line the branch adds to src/, tests/ or README.md ({ids[:3]})')
    stated = stated_unit_count()
    check(stated > UNIT_FLOOR, f'AGENTS.md states {stated} unit tests > {UNIT_FLOOR}')
    totals = re.findall(r'(\d+) tests \((\d+) `unit`, (\d+) `integration`\)', agents)
    check(len(totals) == 1 and int(totals[0][0]) == int(totals[0][1]) + int(totals[0][2])
          and int(totals[0][1]) == stated, f'the repository map\'s total is unit + integration ({totals})')
    check(len(engine_count(agents)) == 1, f'AGENTS.md states one engine-test count ({engine_count(agents)})')
    touched = set(git('diff', '--name-only', against, 'HEAD').split())
    needed = {OVERLAY, REPLAY, 'AGENTS.md', 'PLAN.md', STRESS_SEED}
    check(needed <= touched, f'the branch diff carries the fix and its records (missing {sorted(needed - touched)})')
    cites_hold(against)


# -- the full gate -------------------------------------------------------------------------------------------


def mode_suite() -> None:
    sync = run(['uv', 'sync', '--locked', '-q'])
    check(sync.returncode == 0, f'uv sync --locked exits 0 (got {sync.returncode})')
    stray = Path('.hypothesis').exists()
    lint = run(['tools/lint.sh'], env={'CI': 'true'})
    check(lint.returncode == 0, f'tools/lint.sh exits 0 (got {lint.returncode})')
    proc, report = pytest(['-n', 'auto', '-m', 'unit'], 'suite', env={'CI': 'true'})
    no_red(report, '-m unit under CI=true')
    passed = report.count('passed')
    check(proc.returncode == 0, f'pytest exits 0 (got {proc.returncode})')
    check(passed > UNIT_FLOOR, f'-m unit: {passed} passed > {UNIT_FLOOR}')
    check(report.outcome(*MODEL_ID) == ['passed'], 'test_model passed')
    for name in NEW_TESTS:
        check(report.outcome(CLASSNAME, name) == ['passed'], f'{name} passed')
    stated = stated_unit_count()
    check(passed + report.count('skipped') == stated, f'AGENTS.md states {stated} unit tests; the run collected '
                                                      f'{passed} passed + {report.count("skipped")} skipped')
    engine = sum(c.classname.startswith('tests.engine.') for c in report.cases)
    check(engine_count(Path('AGENTS.md').read_text()) == {engine},
          f'AGENTS.md\'s engine-test count is the {engine} engine tests the run collected')
    check(stray or not Path('.hypothesis').exists(), 'the suite left no .hypothesis/ in the tree')


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
    """R6 from committed state: no CI, no .hypothesis/ before any run, two workers at most."""
    low, high = (int(x) for x in seeds.split('-'))
    check(git('status', '--porcelain').strip() == '', 'the tree is committed (the runs read HEAD)')
    lanes = [list(range(low + i, high + 1, 2)) for i in (0, 1)]
    workers = [(seed_tree(f'seeds-{i}'), lane) for i, lane in enumerate(lanes) if lane]
    aside = scratch('seeds-hypothesis')
    token = f'cut-gate-{uuid.uuid4().hex}'
    saved = dict(os.environ)
    environment = {k: v for k, v in saved.items() if k not in ('PYTHONPATH', *CI_VARS)}

    def start(tree: Path, venv: dict[str, str], seed: int) -> tuple[subprocess.Popen, Path]:
        db = tree / '.hypothesis'
        if db.exists():
            shutil.move(str(db), str(aside / f'before-seed-{seed}'))   # moved aside, never deleted
        check(not db.exists(), f'seed {seed}: the tree holds no .hypothesis/')
        xml = scratch(f'seed-{seed}') / 'junit.xml'
        argv = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'junit_family=xunit1',
                '-o', f'junit_suite_name={token}', f'--junitxml={xml}', MODEL, f'--hypothesis-seed={seed}']
        say(f'  run   seed {seed}: {" ".join(argv)}')
        storage = {'HYPOTHESIS_STORAGE_DIRECTORY': str(xml.parent / 'hypothesis')}     # never shared by seeds
        child = subprocess.Popen(argv, cwd=str(tree), env={**environment, **venv, **storage},
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
    for name in LEDGER_SEEDS:
        check(name in named, f'the ledger reached the deferral to issues/{name}.md')


def mode_prepush() -> None:
    check(git('status', '--porcelain').strip() == '', 'working tree is clean')
    mode_suite()
    mode_records()
    text = git('log', '--format=%B', 'origin/main..HEAD') + git('diff', 'origin/main...HEAD')
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
    """R7 from the run record: every job concluded success; nothing is tolerated."""
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
    total = sum(int(n) for n, _, _ in reasons)
    check(total >= FUSE_UNIT_FLOOR and total == counts['skipped'],
          f'{CI_MACOS}: {total} libfuse skips >= {FUSE_UNIT_FLOOR}, matching the summary line')


MODES = {'regressions': mode_regressions, 'mutants': mode_mutants, 'records': mode_records, 'suite': mode_suite,
         'seeds': mode_seeds, 'ledger': mode_ledger, 'prepush': mode_prepush, 'ci': mode_ci}
OPTIONS = {'ci': ('run_json', 'log_dir', 'sha'), 'seeds': ('seeds',)}


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
    for key in ('run_json', 'log_dir'):          # relative paths name places in the worktree
        if options.get(key):
            options[key] = str((ROOT / options[key]).resolve())
    status = 0
    try:
        for name in modes:
            say(f'[{name}]')
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
        else:
            say('GREEN')
    finally:
        (ROOT / LOCAL).mkdir(parents=True, exist_ok=True)
        (ROOT / LOCAL / f'{time.strftime("%Y%m%d-%H%M%S")}-{"-".join(modes)}.log').write_text('\n'.join(LOG) + '\n')
    return status


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
