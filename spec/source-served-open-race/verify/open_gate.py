# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the source-served-open-race cycle.

Usage (from the cycle worktree root, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I spec/source-served-open-race/verify/open_gate.py MODE [MODE ...]

Modes:
    regressions R1-R5  each named test is defined once, walks before acting and ends in assert_converged; all pass
                       on this tree; each fix test fails on the baseline engine for its named cause (BASELINE_CAUSE);
                       each pin passes there; every test test_replay.py held at the base is still defined, with the
                       same source; `truncate` is byte-identical to the base; test_model.py and both conftest.py
                       files are byte-identical to the base; no skip or xfail is added
    mutants     R3,R5  each fix-removal mutant (planted into a scratch copy of HEAD's src/) makes every test it
                       names fail, and leaves the rest of test_replay.py runnable; every fix test is named by one
    records     R6     AGENTS.md § 5, § 10 and § 11 say how open and a promotion off the source resolve the place
                       and under which lock (§ 10 stating the promotion's whole-copy hold beside truncate's); both
                       high-blast-radius tables keep every base name and name every overlay.py function the branch
                       changes; the open/_serve open-violation row is gone and the identity-gap row names the new
                       seed and open; the docstrings of open, _serve and _lookup_again agree; no file outside spec/
                       names the retiring seed but the seed and its ROADMAP.md entry; PLAN.md and the
                       truncate-recording seed name the new seed; the new seed is unshaped and listed; the counts
                       agree with each other; touched seeds' file:line cites hold
    suite       R7     CI=true: tools/lint.sh exits 0; pytest -n auto -m unit has 0 failed, 0 errors, passed > 502
                       and == AGENTS.md's stated count, the engine's share == its stated count, with test_model and
                       every named test passing
    seeds       R7     test_model passes for each --hypothesis-seed in --seeds=A-B (default 1-20), without CI,
                       two scratch trees built from HEAD, one worker each, no .hypothesis/ before any run
    ledger             every deferral phrase in TECH.md's phase bodies names an issues/ seed that ROADMAP.md lists
    prepush            clean tree, suite, records, then no home-directory path in the branch diff or messages
    ci          R7     the push run for the pushed commit (--sha, default origin/<branch>; later commits may touch
                       only spec/): all four jobs concluded success, Linux passed == the stated count with no
                       skip, macOS skipped only tests/fuse naming libfuse. --run-json/--log-dir read a saved
                       `gh run view --json` record and job logs (<job id>.log or <job name>.log) instead

"The baseline engine" is `src/` at BASELINE (the GOAL's tree, where it counted 502), extracted with `git archive`
into scratch and put first on PYTHONPATH; each run checks which sosfs it imported. "The base" for file comparisons
is the merge base with origin/main; "the plan commit" (the commit that added this cycle's PLAN.md) is the base for
which seeds the build touched. Counts come from pytest's JUnit XML, never from the exit code. Scratch and a copy of
this run's output go under .local/open-gate/ (gitignored, survives a reboot).

Footprint: one subprocess at a time, except `seeds`, which runs two pytest processes at once (each its own scratch
tree, environment and process group, killed as a group on the first red or a per-seed timeout). `suite` and
`prepush` run pytest with -n auto (one worker per core, about 1.5 GB peak on a 10-core machine); every other pytest
run is serial and under 500 MB. Measured on the planning prototype (2026-11-08): see the cycle's TECH.md.
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


SLUG = 'source-served-open-race'
BASELINE = '5e0d997'          # the GOAL's tree; src/ and tests/ are unchanged from it to the shaping commits
UNIT_FLOOR = 502              # R7: strictly more than this pass
MODEL = 'tests/engine/test_model.py'
MODEL_ID = ('tests.engine.test_model.TestOverlayModel', 'runTest')       # JUnit (classname, name)
REPLAY = 'tests/engine/test_replay.py'
OVERLAY = 'src/sosfs/engine/overlay.py'
FROZEN = (MODEL, 'tests/engine/conftest.py', 'tests/conftest.py')       # byte-identical to the base
OWN_SEED = f'issues/{SLUG}.md'                     # retired by `code terrace roadmap` after landing
TOKEN_SEED = 'issues/entry-identity-token.md'                           # the identity gap's new owner (GOAL Q2)
RECORDING_SEED = 'issues/truncate-recording-after-ancestor-rename.md'
STRESS_SEED = 'issues/freethreaded-concurrency-stress.md'
# Claims the cycle shows false: the baseline's window handed back another file's bytes (R4's tests reproduce it).
OVERCLAIMS = ('Loud, never a wrong object', 'never a wrong object')
LEDGER_SEEDS = ('entry-identity-token', 'freethreaded-concurrency-stress')
STRESS_NOTE = ("other file's", 'stage', 'Reproduced by injection')   # the round trip's note

# Tests the build writes into tests/engine/test_replay.py (names fixed here, PLAN § 6).
OWN_RENAME = 'test_open_of_a_source_served_file_after_a_replay_commits_its_own_rename'
DIR_RENAME = 'test_open_of_a_source_served_file_after_a_replay_commits_its_directory_rename'
ONE_TIER = 'test_open_on_a_one_tier_stack_after_a_replay_commits_its_directory_rename'
PROMOTE_OWN = 'test_open_promotes_after_a_replay_commits_its_own_rename'
PROMOTE_DIR = 'test_open_promotes_after_a_replay_commits_its_directory_rename'
SWAP_OPEN = 'test_open_after_a_replay_moves_another_file_onto_the_old_source_place'
SWAP_PROMOTE = 'test_open_promotes_nothing_from_another_file_moved_onto_the_old_source_place'
OPEN_HOLDS = 'test_open_of_a_source_served_file_opens_under_the_source_lock'
PROMOTE_HOLDS = 'test_open_promotes_off_the_source_under_the_source_lock'
OPEN_INSIDE = 'test_open_of_a_source_served_file_resolves_inside_the_source_lock'
PROMOTE_INSIDE = 'test_open_promotes_from_where_a_replay_during_the_wait_moved_the_file'
MOVED_AWAY = 'test_open_after_a_concurrent_rename_moves_the_file_away'
REUSED = 'test_open_leaves_a_file_that_reused_the_entry_id_alone'
CARRIED = 'test_open_leaves_another_file_carried_onto_the_path_alone'
SYMLINK_ID = 'test_open_refuses_a_symlink_that_took_the_entry_id'
STAGED_FREE = 'test_open_of_a_staged_copy_takes_no_source_lock'
QUEUED = 'test_open_promotes_from_where_the_source_holds_a_file_behind_its_queued_rename'
NOTHING_NESTED = 'test_open_takes_no_path_lock_and_no_lookup_under_the_source_lock'
STAGE_PROMOTE = 'test_open_promotes_from_a_staged_copy_without_the_source_lock'
R1_TESTS = (OWN_RENAME, DIR_RENAME, ONE_TIER)
R2_TESTS = (PROMOTE_OWN, PROMOTE_DIR)
R4_TESTS = (SWAP_OPEN, SWAP_PROMOTE)
CONCURRENT = (MOVED_AWAY, REUSED, CARRIED, SYMLINK_ID)
FIX_TESTS = R1_TESTS + R2_TESTS + R4_TESTS + (OPEN_HOLDS, PROMOTE_HOLDS, OPEN_INSIDE, PROMOTE_INSIDE) + CONCURRENT
PIN_TESTS = (STAGED_FREE, QUEUED, NOTHING_NESTED, STAGE_PROMOTE)   # pass on the baseline too; `mutants` bites
NEW_TESTS = FIX_TESTS + PIN_TESTS
# Why each fix test fails on the baseline engine (measured on the planning prototype, PLAN § 4). The baseline opens
# and promotes from the place `_ensure` read, and never takes the source lock: R1 opens a place a replay emptied,
# R2 copies from it, R4 reads or copies the other file's bytes, and the lock-shaped tests see no lock.
BASELINE_CAUSE = {name: 'FileNotFoundError' for name in R1_TESTS}
BASELINE_CAUSE.update({name: r'TierError: No file at' for name in R2_TESTS})
BASELINE_CAUSE[SWAP_OPEN] = r"assert b'bbbb"
BASELINE_CAUSE[SWAP_PROMOTE] = r"assert b'berry' == b'apple'"
BASELINE_CAUSE.update({name: r'assert \[True\] == \[False\]' for name in (OPEN_HOLDS, PROMOTE_HOLDS)})
BASELINE_CAUSE.update({name: r'assert engine\._source_lock\.ran' for name in (OPEN_INSIDE, PROMOTE_INSIDE)})
BASELINE_CAUSE.update({name: 'DID NOT RAISE' for name in CONCURRENT})
# The earlier cycle's tests that open a source-served file behind a queued rename, with no replay in the call.
BYPASS_OPENS = ('test_size_through_a_bypass_stage_behind_its_own_rename',
                'test_size_through_a_bypass_stage_inside_a_renamed_directory')

# (id, file, ((exact text in the built tree, replacement), ...), tests that must fail). Anchors are the built
# engine's own text; a mutant whose anchor is not found exactly once is red, so a build that reshapes a guarded line
# re-anchors it here in the same commit (PLAN § 6). The decisions they attack: which tier's open or copy takes
# the source lock, whether the act runs before it is released, whether the entry is looked up again and when, how
# the lookup tells the entry apart and that it makes no nested lookup, which place the open and the copy use, and
# that a staged open or copy waits for nothing.
OPEN_SIDE = '            on_source = serving == self.source.name\n'
SERVE_SIDE = '            on_source = fastest.tier == self.source.name\n'
CHOSEN = 'self._source_lock if on_source else contextlib.nullcontext()'
AGAIN = '                current = self._lookup_again(entry) if on_source else entry\n'
OPEN_LOCK = f'            with {CHOSEN}:\n{AGAIN}                fd = '
SERVE_LOCK = f'            with {CHOSEN}:\n{AGAIN}                self.transfers.copy('
OPEN_AT = 'open_direct(_pure(self._held_path(current, serving)), direct)'
COPY_FROM = 'src_path=self._held_path(current, fastest.tier))'
DECIDED = "current is None or current.id != entry.id or current.kind != 'file'"
LOOKUP = f"        current = self.catalog.get(entry.path)\n        if {DECIDED}:\n"
MUTANTS = (
    ('M1 the open uses the place read before the lock', OVERLAY,
     ((OPEN_AT, OPEN_AT.replace('(current,', '(entry,')),),
     R1_TESTS + (SWAP_OPEN, OPEN_INSIDE)),
    ('M2 the open takes no source lock', OVERLAY,
     ((OPEN_LOCK, OPEN_LOCK.replace(CHOSEN, 'contextlib.nullcontext()')),),
     (OPEN_HOLDS, OPEN_INSIDE) + CONCURRENT),
    ('M3 the promotion copies from the place read before the lock', OVERLAY,
     ((COPY_FROM, COPY_FROM.replace('(current,', '(entry,')),),
     R2_TESTS + (SWAP_PROMOTE, PROMOTE_INSIDE)),
    ('M4 the promotion takes no source lock', OVERLAY,
     ((SERVE_LOCK, SERVE_LOCK.replace(CHOSEN, 'contextlib.nullcontext()')),),
     (PROMOTE_HOLDS, PROMOTE_INSIDE)),
    ('M5 the open does not look the entry up again', OVERLAY,
     ((OPEN_LOCK, OPEN_LOCK.replace('self._lookup_again(entry) if on_source else entry', 'entry')),),
     R1_TESTS + (SWAP_OPEN, OPEN_INSIDE, MOVED_AWAY)),
    ('M6 the promotion does not look the entry up again', OVERLAY,
     ((SERVE_LOCK, SERVE_LOCK.replace('self._lookup_again(entry) if on_source else entry', 'entry')),),
     R2_TESTS + (SWAP_PROMOTE, PROMOTE_INSIDE)),
    ('M7 the open looks the entry up again before the lock', OVERLAY,
     ((OPEN_LOCK, AGAIN[4:] + f'            with {CHOSEN}:\n                fd = '),),
     (OPEN_INSIDE, MOVED_AWAY)),
    ('M8 the promotion looks the entry up again before the lock', OVERLAY,
     ((SERVE_LOCK, AGAIN[4:] + f'            with {CHOSEN}:\n                self.transfers.copy('),),
     (PROMOTE_INSIDE,)),
    ('M9 the entry is looked up again by id alone', OVERLAY,
     ((LOOKUP, "        current = self.catalog.by_id(entry.id)\n"
               "        if current is None or current.kind != 'file':\n"),),
     (MOVED_AWAY, REUSED, CARRIED)),
    ('M10 the entry found again is not compared by id', OVERLAY,
     ((LOOKUP, LOOKUP.replace(DECIDED, "current is None or current.kind != 'file'")),),
     (CARRIED,)),
    ('M11 a missing entry is not refused', OVERLAY,
     ((LOOKUP, LOOKUP.replace(DECIDED, "current.id != entry.id or current.kind != 'file'")),),
     (MOVED_AWAY, REUSED)),
    ('M12 the kind is not decided again', OVERLAY,
     ((LOOKUP, LOOKUP.replace(DECIDED, 'current is None or current.id != entry.id')),),
     (SYMLINK_ID,)),
    ('M13 the open takes the lock for the wrong tier', OVERLAY,
     ((OPEN_SIDE, OPEN_SIDE.replace('==', '!=')),),
     R1_TESTS + (OPEN_HOLDS, STAGED_FREE)),
    ('M14 the promotion takes the lock for the wrong tier', OVERLAY,
     ((SERVE_SIDE, SERVE_SIDE.replace('==', '!=')),),
     R2_TESTS + (PROMOTE_HOLDS,)),
    ('M15 every open takes the source lock', OVERLAY,
     ((OPEN_LOCK, OPEN_LOCK.replace(CHOSEN, 'self._source_lock')),),
     (STAGED_FREE,)),
    ('M16 the open releases the source lock before it opens', OVERLAY,
     ((OPEN_LOCK, OPEN_LOCK.replace('\n                fd = ', '\n            fd = ')),),
     (OPEN_HOLDS,)),
    ('M17 the promotion releases the source lock before it copies', OVERLAY,
     ((SERVE_LOCK, SERVE_LOCK.replace('\n                self.transfers.copy(', '\n            self.transfers.copy(')),
      ("                                    now=self.clock(), src_path=",
       "                                now=self.clock(), src_path=")),
     (PROMOTE_HOLDS,)),
    ('M18 the promotion copies from the logical path', OVERLAY,
     ((COPY_FROM, 'src_path=None)'),),
     (QUEUED,)),
    ('M19 the open uses the logical path', OVERLAY,
     ((OPEN_AT, 'open_direct(_pure(path), direct)'),),
     BYPASS_OPENS + (OPEN_HOLDS,)),
    ('M20 the lookup under the source lock goes through _ensure', OVERLAY,
     ((LOOKUP, LOOKUP.replace('self.catalog.get(entry.path)', 'self._ensure(entry.path)')),),
     (NOTHING_NESTED,)),
    ('M21 every promotion takes the source lock', OVERLAY,
     ((SERVE_LOCK, SERVE_LOCK.replace(CHOSEN, 'self._source_lock')),),
     (STAGE_PROMOTE,)),
)


DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|deferr(?:al|als|ed)\b|'
                      r'split (?:out|from)|stays? open|out of scope', re.IGNORECASE)
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')    # written so this line cannot match itself
TOLERANCE = re.compile(r'\bxfail\b|\bskipif\b|mark\.skip\b|pytest\.skip\(', re.IGNORECASE)
CITE = re.compile(r'(?<![\w/.-])((?:[\w-]+/)*([\w-]+\.py)):(\d+)(?:-(\d+))?')
PIN = re.compile(r'\b(?:holds?|held|is|are|pinned)\s+at\s+`([0-9a-f]{7,40})`')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
ROOT = Path.cwd()
LOCAL = Path('.local/open-gate')
RUN_TIMEOUT = 300                 # every run but a seed's; the slowest, the suite, takes about 30 s


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
    xml = scratch(label) / 'junit.xml'
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
    helper = by_name.get('assert_converged')
    text = ast.unparse(helper) if helper is not None else ''
    missing = sorted(w for w in CONVERGED_NAMES if not re.search(rf'\b{w}\b', text))
    check(helper is not None and not missing,
          f'assert_converged checks the queues, unlisted directories, the catalog, the source, the view, the '
          f'sizes and a remount (missing {missing if helper else "the helper"})')
    walkers = {'walk', 'source_served'} | {n.name for n in defs if 'walk' in names_in(n)
                                           and not n.name.startswith('test_')}
    for name in NEW_TESTS:
        check([n.name for n in defs].count(name) == 1, f'{name} is defined once in {REPLAY}')
        node = by_name[name]
        last = node.body[-1]
        ends = isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) \
            and getattr(last.value.func, 'id', None) == 'assert_converged'
        check(ends and bool(names_in(node) & walkers), f'{name} walks the tree and ends in assert_converged(...)')


def unchanged_code() -> None:
    against = base()
    old = top_level(git('show', f'{against}:{REPLAY}'))
    new = top_level(Path(REPLAY).read_text())
    changed = sorted(name for name, source in old.items() if new.get(name) != source)
    check(not changed, f'every function and class {REPLAY} held at the base is still defined, unchanged '
                       f'({len(old)} checked; changed {changed})')
    before = function_source(git('show', f'{against}:{OVERLAY}'), 'truncate')
    after = function_source(Path(OVERLAY).read_text(), 'truncate')
    check(bool(before) and before == after, 'OverlayEngine.truncate is byte-identical to the base')


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


# -- fix-removal mutants ------------------------------------------------------------------------------------

def mode_mutants() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    named = {name for *_, tests in MUTANTS for name in tests}
    check(set(FIX_TESTS + PIN_TESTS) <= named, f'every new test is named by a mutant (unnamed '
                                               f'{sorted(set(FIX_TESTS + PIN_TESTS) - named)})')
    for label, file, edits, tests in MUTANTS:
        src = extract_src('HEAD', 'mutant')
        target = src.parent / file
        text = target.read_text()
        for number, (old, new) in enumerate(edits, 1):
            check(text.count(old) == 1, f'{label}: anchor {number} occurs once in {file}')
            text = text.replace(old, new)
        target.write_text(text)
        report = with_engine(src, ['-m', 'unit', REPLAY], 'mutant')
        check(report.count('passed') > 0, f'{label}: the rest of {REPLAY} still runs ({report.count("passed")} '
                                          f'passed)')
        for name in tests:
            check(report.outcome(CLASSNAME, name) == ['failed'], f'{label}: {name} fails')


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


def mode_records() -> None:
    against = base()
    check(git('status', '--porcelain', '--', 'AGENTS.md', 'PLAN.md', 'ROADMAP.md', 'issues', 'src', 'tests').strip()
          == '', 'AGENTS.md, PLAN.md, ROADMAP.md, issues/, src/ and tests/ are committed (records read them)')
    agents = Path('AGENTS.md').read_text()
    five = section(agents, '### 5. Committed source path')
    reading = [b for b in bullets(five) if '_stat_held' in b]
    check(len(reading) == 1, '§ 5: one bullet says how reads of the source resolve (it names _stat_held)')
    check(all(word(n, reading[0]) for n in ('open', '_serve', '_lookup_again', '_held_path'))
          and 'source lock' in reading[0] and re.search(r'promot', reading[0]) is not None,
          '§ 5: that bullet names open, _serve (a promotion), _lookup_again, _held_path and the source lock')
    check(re.search(r'opens? .{0,120}before .{0,40}releas', reading[0]) is not None,
          '§ 5: that bullet says the object is opened before the lock is released')
    ten = section(agents, '### 10. Concurrency')
    mutex = [b for b in bullets(ten) if 'source-namespace mutex' in b]
    check(len(mutex) == 1, '§ 10: one source-mutex bullet')
    takers = [s for s in re.split(r'(?<=[.;])\s+', mutex[0]) if '`_record` takes it' in s]
    check(len(takers) == 1 and re.search(r'`open` to [^;.]*open', takers[0]) is not None
          and re.search(r'promotion off the source', takers[0]) is not None and word('truncate', mutex[0]),
          '§ 10: the takers sentence names open (to open a source-served copy) and a promotion off the source, and '
          'the bullet names truncate')
    sentences = re.split(r'(?<=\.)\s+', mutex[0])
    hold = [s for s in sentences if re.search(r'promot', s) and word('open', s) and 'whole' in s and 'delay' in s]
    check(bool(hold), '§ 10: a sentence states that a promotion off the source on open holds the mutex for the '
                      'whole copy, and what it delays')
    check(any('S3' in s or 'archive' in s or 'passthrough' in s for s in sentences if re.search(r'promot', s)),
          '§ 10: the promotion hold says the stage bound does not hold for a source with no passthrough')
    eleven = section(agents, '### 11. The engine API contract')
    calling = [b for b in bullets(eleven) if 'Promotion runs on the calling thread' in b]
    check(len(calling) == 1 and ('source lock' in calling[0] or 'source mutex' in calling[0]),
          '§ 11: the promotion bullet says a promotion off the source also holds the source mutex')
    old_tables, new_tables = tables(git('show', f'{against}:AGENTS.md')), tables(agents)
    check(len(old_tables) == len(new_tables) == 2, 'both high-blast-radius tables are found')
    functions = changed_functions(against) - {'__init__'}     # its change is the source lock's comment
    check({'open', '_serve', '_lookup_again'} <= functions,
          f'the branch changes open, _serve and _lookup_again ({sorted(functions)})')
    check('truncate' not in functions, 'the branch leaves truncate unchanged')
    for index, (old, new) in enumerate(zip(old_tables, new_tables), 1):
        lost = sorted(set(re.findall(r'`([^`]+)`', old)) - set(re.findall(r'`([^`]+)`', new)))
        lost = [t for t in lost if not all(word(part, new) for part in re.split(r'[ ,/]+', t) if part)]
        check(not lost, f'table {index} keeps every name it had at the base (lost {lost})')
        missing = sorted(f for f in functions if not word(f, new))
        check(not missing, f'table {index} names every overlay.py function the branch changes (missing {missing})')
    violations = section(agents, '### Known open violations')
    rows = [line for line in violations.splitlines() if line.startswith('| ')]
    check(not any(OWN_SEED in r for r in rows), f'no open-violation row names {OWN_SEED}')
    check(not any('_serve' in r and 'outside the source lock' in r for r in rows),
          'no open-violation row still lists open or _serve reading source_path outside the source lock')
    gap = [r for r in rows if TOKEN_SEED in r]
    check(len(gap) == 1 and 'reused' in gap[0] and 'injection' in gap[0] and word('open', gap[0])
          and 'truncate' in gap[0], f'one row names the identity gap (ids reused, by injection) for truncate and '
                                    f'open, owned by {TOKEN_SEED}')
    recording = [r for r in rows if RECORDING_SEED in r]
    check(len(recording) == 1 and 'ENOENT' in recording[0], f'the recording row ({RECORDING_SEED}) still stands')
    source = Path(OVERLAY).read_text()
    for name in ('open', '_serve'):
        doc = docstring(source, name)
        check('source lock' in doc and 'again' in doc and 'ENOENT' in doc and 'reused' in doc,
              f'the {name} docstring says it looks the entry up again under the source lock, when it raises ENOENT, '
              f'and that a file which took a reused id passes')
    check(re.search(r'(?<!\w)never\b[^.]*another entry', ' '.join((docstring(source, 'open') + ' '
                    + docstring(source, '_serve')).split())) is None,
          'neither docstring says another entry is never opened or copied (the reused id is)')
    module = ' '.join(Path(REPLAY).read_text().split('"""')[1].split())
    check('reused' in module and re.search(r'never another entry', module) is None,
          f'{REPLAY}\'s module docstring states the reused id and claims no "never another entry"')
    check('reused' in reading[0] and re.search(r'never at the old one or at another entry', reading[0]) is None,
          '§ 5: the reading bullet qualifies "not another entry\'s object" by the reused id')
    check('whole copy' in docstring(source, '_serve'), 'the _serve docstring says the lock is held for the whole copy')
    check('reused' in docstring(source, '_lookup_again'), 'the _lookup_again docstring states the reused-id gap')
    tracked = [f for f in git('ls-files').split() if not f.startswith('spec/') and Path(f).is_file()]
    own_file = Path(OWN_SEED).name                # sibling links in issues/ name it without the directory
    naming = sorted(f for f in tracked if own_file in Path(f).read_text(errors='replace') and f != OWN_SEED)
    check(set(naming) <= {'ROADMAP.md'}, f'outside spec/, only ROADMAP.md names {OWN_SEED} (got {naming})')
    roadmap = Path('ROADMAP.md').read_text()
    lines = [line for line in roadmap.splitlines() if OWN_SEED in line]
    lines = [line for line in roadmap.splitlines() if own_file in line]
    check(len(lines) <= 1 and all(line.startswith('**Seed:**') for line in lines),
          f'ROADMAP.md names {OWN_SEED} only on its own entry\'s Seed line, which `code terrace roadmap` retires')
    live = ['AGENTS.md', 'PLAN.md', OVERLAY, REPLAY] + sorted(str(f) for f in Path('issues').glob('*.md')
                                                            if str(f) != OWN_SEED)
    for phrase in OVERCLAIMS:
        hits = [f for f in live if phrase in ' '.join(Path(f).read_text().split())]
        check(not hits, f'no live record claims {phrase!r} (got {hits})')
    token = Path(TOKEN_SEED).read_text() if Path(TOKEN_SEED).exists() else ''
    check(re.search(r'^status: unshaped$', token, re.MULTILINE) is not None
          and re.search(r'^kind: fix$', token, re.MULTILINE) is not None and TOKEN_SEED in roadmap,
          f'{TOKEN_SEED} exists, is status: unshaped and kind: fix, and ROADMAP.md lists it')
    check(word('open', token) and 'truncate' in token and 'schema' in token,
          f'{TOKEN_SEED} carries the gap for truncate and open, and the schema change closing it needs')
    plan = Path('PLAN.md').read_text()
    start = plan.index('* **Write-back namespace replay under path mutation**')
    following = plan.find('\n* **', start + 1)
    risk = ' '.join(plan[start:following if following > 0 else len(plan)].split())
    check(TOKEN_SEED in risk and own_file not in risk and word('open', risk.split('Still open')[-1]),
          f'PLAN.md\'s namespace-replay risk names {TOKEN_SEED} for the gap open shares, and not {own_file}')
    related = [b for b in bullets(Path(RECORDING_SEED).read_text()) if b.startswith('Related:')]
    check(len(related) == 1 and TOKEN_SEED in related[0] and own_file not in related[0],
          f'{RECORDING_SEED}: its Related line names {TOKEN_SEED} in place of {own_file}')
    added = ' '.join(line[1:] for line in git('diff', '-U0', against, '--', STRESS_SEED).splitlines()
                     if line.startswith('+') and not line.startswith('+++'))
    note = ' '.join(added.split())
    check(SLUG in note and all(token in note for token in STRESS_NOTE) and word('open', note),
          f'{STRESS_SEED}: the branch\'s note records the round trip through open, the other file\'s stage bytes '
          f'reproduced by injection')
    stated = stated_unit_count()
    check(stated > UNIT_FLOOR, f'AGENTS.md states {stated} unit tests > {UNIT_FLOOR}')
    totals = re.findall(r'(\d+) tests \((\d+) `unit`, (\d+) `integration`\)', agents)
    check(len(totals) == 1 and int(totals[0][0]) == int(totals[0][1]) + int(totals[0][2])
          and int(totals[0][1]) == stated, f'the repository map\'s total is unit + integration ({totals})')
    check(len(engine_count(agents)) == 1, f'AGENTS.md states one engine-test count ({engine_count(agents)})')
    check(re.search(r'\bopen\b', ' '.join(Path(REPLAY).read_text().split('"""')[1].split())) is not None,
          f'{REPLAY}\'s module docstring names the open class it now guards')
    touched = set(git('diff', '--name-only', against, 'HEAD').split())
    needed = {OVERLAY, REPLAY, 'AGENTS.md', 'PLAN.md', 'ROADMAP.md', TOKEN_SEED, RECORDING_SEED}
    check(needed <= touched, f'the branch diff carries the fix and its records (missing {sorted(needed - touched)})')
    cites_hold(against)


# -- the full gate -------------------------------------------------------------------------------------------

def mode_suite() -> None:
    sync = run(['uv', 'sync', '--locked', '-q'])
    check(sync.returncode == 0, f'uv sync --locked exits 0 (got {sync.returncode})')
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
    token = f'open-gate-{uuid.uuid4().hex}'
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
        child = subprocess.Popen(argv, cwd=str(tree), env={**environment, **venv},
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
