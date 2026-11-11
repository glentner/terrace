# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the nested-rmdir-replay-recursion cycle.

Usage (from the cycle worktree root, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I \
        spec/nested-rmdir-replay-recursion/verify/drain_gate.py MODE [MODE ...]

Modes:
    regressions R1-R6  each new test is defined once, collects its stated number of cases, and ends in
                       assert_converged; the shape tests compare the source with the oracle and call their consumer
                       once; all pass on this tree; every fix case fails on the baseline engine with RecursionError
                       and every pin case passes there; every function and class test_replay.py held at the base is
                       unchanged; test_model.py, both conftest.py files, schema.sql and db.py are byte-identical to
                       the base; no skip or xfail is added; outside the functions the plan names (CHANGED), the code
                       of overlay.py, drain.py, writeback.py, catalog.py and journal.py is the base's (docstrings
                       aside), and so are their module and class bodies but for the new `_Frame`, so nothing gains
                       state; the replay calls neither itself nor a row's execution, so it cannot recurse
    mutants     R6     each mutant (planted into a scratch copy of HEAD's src/) makes every case it names fail for
                       its named cause and leaves the rest of test_replay.py runnable; every new case is named by one
    records     R7     AGENTS.md § 5's replay bullet, the six docstrings, test_replay.py's module docstring and the
                       measured counts say what the code does; § 10 is the base's but for its re-entrancy sentence,
                       which no longer names replay (and the lock's comment in overlay.py with it); the
                       high-blast-radius tables are the base's but for `held` and `queued_marks`;
                       no src/ or tests/ text cites an issues/ seed; added lines in src/ and tests/ are at most 120
                       characters and carry no spec ids
    seedfile    R7     the source-only scheduler seed exists, unshaped, with its reproduction and its ROADMAP.md
                       entry, its cites hold at HEAD, and writeback.py is the base's (a record, not a fix)
    suite       R8     CI=true: tools/lint.sh exits 0; pytest -n auto -m unit has 0 failed, 0 errors, passed > 548
                       and == AGENTS.md's stated count, the engine's share == its stated count, with test_model and
                       every new case passing; no .hypothesis/ is left in the tree
    seeds       R8     test_model passes for each --hypothesis-seed in --seeds=A-B (default 1-20), without CI,
                       two scratch trees built from HEAD, one worker each, no .hypothesis/ before any run
    ledger             on a clean tree, every deferral phrase in TECH.md's phase bodies names an issues/ seed that
                       ROADMAP.md lists
    prepush            clean tree, suite, records, seedfile, then no home-directory path in the branch diff or messages
    ci          R8     the push run for the pushed commit (--sha, default origin/<branch>; later commits may touch
                       only spec/): all four jobs concluded success, Linux passed == the stated count with no
                       skip, macOS skipped only tests/fuse naming libfuse. --run-json/--log-dir read a saved
                       `gh run view --json` record and job logs (<job id>.log or <job name>.log) instead

"The baseline engine" is `src/` at BASELINE (the GOAL's tree, where the suite counted 548), extracted with
`git archive` into scratch and put first on PYTHONPATH; each run checks which sosfs it imported. "The base" for file
comparisons is the merge base with origin/main. Counts come from pytest's JUnit XML, never from the exit code. Every
pytest run has HYPOTHESIS_STORAGE_DIRECTORY in its own scratch directory and runs with -p no:cacheprovider, so it
leaves neither .hypothesis/ nor .pytest_cache/ in the tree. Scratch and a copy of this run's output go under
.local/drain-gate/ (gitignored, survives a reboot).

Footprint: one subprocess at a time, except `seeds`, which runs two pytest processes at once (each its own scratch
tree, environment and process group, killed as a group on the first red or a per-seed timeout). `suite` and
`prepush` run pytest with -n auto (one worker per core, about 1.5 GB peak on a 10-core machine); every other pytest
run is serial and under 500 MB. The pump test bounds its waits at 10 s, and a mutant that lets a row run again inside
its own replay carries a tripwire that raises RecursionError past 100,000 stack frames, so it fails in seconds rather
than growing without end. Measured on the planning prototype (2026-11-10): see TECH.md. Nothing is deleted; the gate
never pushes and never writes to the tracked tree.

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


SLUG = 'nested-rmdir-replay-recursion'
BASELINE = '980f998'          # the GOAL's tree; src/ and tests/ are unchanged from it to the shaping commit
UNIT_FLOOR = 548              # R8: strictly more than this pass
MODEL = 'tests/engine/test_model.py'
MODEL_ID = ('tests.engine.test_model.TestOverlayModel', 'runTest')       # JUnit (classname, name)
REPLAY = 'tests/engine/test_replay.py'
OVERLAY = 'src/sosfs/engine/overlay.py'
DRAIN = 'src/sosfs/engine/drain.py'
WRITEBACK = 'src/sosfs/engine/writeback.py'
FROZEN = (MODEL, 'tests/engine/conftest.py', 'tests/conftest.py', 'src/sosfs/state/schema.sql',
          'src/sosfs/state/db.py')                                   # byte-identical to the base
SEED = 'issues/source-only-writeback-scheduler.md'                   # Q1's record, written in P2
LEDGER_SEEDS = ('source-only-writeback-scheduler',)
# The only functions whose code (docstrings aside) the fix changes; every other function in overlay.py and drain.py
# keeps the base's code (PLAN § 2).
CATALOG = 'src/sosfs/state/catalog.py'
JOURNAL = 'src/sosfs/state/journal.py'
# The functions whose code (docstrings aside) the fix changes or adds, by file and qualified name; every other function
# in these files keeps the base's code, and their module and class bodies are the base's but for `_Frame` (PLAN § 2).
CHANGED = {OVERLAY: {'OverlayEngine._execute_namespace', 'OverlayEngine._execute_namespace_locked',
                     'OverlayEngine._clear_inner_target', 'OverlayEngine._replay_through',
                     'OverlayEngine._holder_rows'},
           CATALOG: {'Catalog.held'}, JOURNAL: {'Journal.queued_marks'}, DRAIN: set(), WRITEBACK: set()}
NEW_CLASSES = {OVERLAY: {'_Frame'}}
# Every file the branch may change under src/ and tests/ (P1); anything else there is out of the plan.
TOUCHED = {OVERLAY, DRAIN, CATALOG, JOURNAL, REPLAY}
DOCUMENTED = ('_replay_through', '_execute_namespace', '_execute_namespace_locked', '_vacate', 'flush',
              '_clear_inner_target', '_holder_rows')

# Tests the build writes into tests/engine/test_replay.py (names and case counts fixed here, PLAN § 6).
SHAPE = 'test_one_pass_drains_nested_directories_removed_before_their_moved_out_entries'
POLL = 'test_one_scheduler_poll_runs_every_namespace_row_of_nested_removals'
PUMP = 'test_the_writeback_pump_outlives_nested_removals'
NAME = 'test_a_new_name_over_nested_removals'
STUCK = 'test_a_queue_left_by_nested_removals_drains_on_the_first_pass_after_a_restart'
BESIDE = 'test_shapes_beside_nested_removals_still_drain'
EEXIST = 'test_a_new_name_over_a_kept_moved_entry_still_raises_eexist'
STRAY = 'test_a_child_the_catalog_never_saw_still_stops_the_rmdir_row_with_enotempty'
CHAINS = 'test_one_pass_drains_hundreds_of_chains_removed_before_their_moved_out_entries'
CROSS = 'test_one_pass_drains_renames_crossing_each_others_subtrees'
CROSSED = 'test_a_new_name_over_renames_crossing_each_others_subtrees'
RACE = 'test_a_removal_queued_during_a_replay_runs_before_the_directory_it_holds'
REPLACED = 'test_a_rename_over_a_directory_whose_object_holds_nested_removals'
# Review cycle 1's remediation: R5's controls under a new name at the moved entry's name (F4); a row that waits inside
# a new name's replay past its last need stays queued, and the next flush drains it (F1); a pass over rows nothing
# stands in reads the queue a fixed number of times (P1).
NEWNAME = 'test_shapes_beside_nested_removals_take_a_new_name_where_the_moved_entry_was'
VACATED = 'test_a_row_waiting_inside_a_new_names_replay_past_its_last_need_stays_queued_for_the_next_flush'
PASSES = 'test_a_pass_over_rows_nothing_stands_in_reads_the_queue_a_fixed_number_of_times'
CASES = {SHAPE: 64, POLL: 6, PUMP: 1, NAME: 12, STUCK: 4, BESIDE: 36, EEXIST: 1, STRAY: 2, CHAINS: 8, CROSS: 20,
         CROSSED: 2, RACE: 2, REPLACED: 2, NEWNAME: 18, VACATED: 2, PASSES: 3}
ORACLE_TESTS = (SHAPE, POLL, PUMP, NAME, STUCK, CHAINS, CROSS, CROSSED, REPLACED, NEWNAME, VACATED,
                PASSES)                                                   # compare with the oracle's tree
RECURSED = r'^RecursionError'
CANNOT_MOVE = r'EngineError: Cannot move'
GREW = r'queue passes grew with the queue'
# A selector is (function, regex over the case's parameter id or None for every case). Fix selectors carry the cause
# each case fails with on the baseline engine: recursion, or (the R10 crossings the base refuses) `EngineError`.
# Causes match the JUnit failure's `message` attribute (the exception and its text), never the test's source.
FIX = ((SHAPE, None, RECURSED), (POLL, None, RECURSED), (PUMP, None, RECURSED), (NAME, None, RECURSED),
       (STUCK, None, RECURSED), (STRAY, r'after', RECURSED), (CHAINS, None, RECURSED),
       (CROSS, r'^(growing|nested|inverted)-', RECURSED), (CROSS, r'^listed-', CANNOT_MOVE),
       (CROSSED, r'^mkdir$', RECURSED), (CROSSED, r'^rename$', CANNOT_MOVE), (REPLACED, None, RECURSED),
       (VACATED, None, RECURSED), (PASSES, None, GREW))
PIN = ((BESIDE, None), (EEXIST, None), (STRAY, r'before'), (RACE, None), (NEWNAME, None))

# Mutant anchors: the built tree's own text. A mutant whose anchor is not found exactly once is red, so a build that
# reshapes a guarded line re-anchors it here in the same commit (PLAN § 6).
WAIT = '            if needs & stack or (stuck and stack != {mark(current)}):\n'
SKIP = "                    if other.op == 'data' or mark(other) in stack:\n"
PUSH = '                    frames.append(_Frame(nested))\n                    stack.add(mark(nested))\n'
FIXED_POINT = '            stuck = bool(needs) and frame.needs is not None and frame.needs <= needs\n'
RENAME_NEEDS = '                    ids = self._clear_inner_target(entry, pending)\n'
LATER = '            later()\n            return [other for other in queue if mark(other) not in done]\n'
# A row nothing stands in reads no queue: an `rmdir` whose directory's object holds no other entry, and a rename whose
# target lies outside its own object.
NO_HOLDER = ('        if not holders:\n'
             '            return set()                                    '
             '# nothing holds the place, so no queued row does\n')
OUTSIDE = '        held = _pure(entry.source_path)\n'
# A new name's replay (`_vacate`, `_purge_source`) runs the queue through the last row it needs, and no further.
ROOT_ROWS = '            frames[0].remaining = {mark(other) for other in queue if other.id in rows}\n'
STAYED = r"assert \[\('mkdir', '/z'\)\] =="
MARK = '            return row.id, row.entry, row.op, row.first_dirty\n'
EXTEND = ('                queue.extend(other for other in self.journal.queued(self.source.name) '
          'if mark(other) in fresh)\n')
# § 10's re-entrancy sentence at the base, and as it reads once replay runs every row under its caller's hold
# (GOAL B-Q1): the one change § 10 takes. The lock's comment in overlay.py changes with it.
REENTRANT_BASE = ('It is re-entrant because replay\n'
                  '  nests: `flush` → `_execute_namespace`, and `_vacate` → `_replay_through` → '
                  '`_execute_namespace`;\n'
                  '  `rename` takes it around its `_vacate`, and the cut around `_serve`, which takes it again '
                  'to promote\n'
                  '  off the source.\n')
REENTRANT_NOW = ('It is re-entrant because\n'
                 '  `rename` takes it around its `_vacate`, and the cut around `_serve`, which takes it again '
                 'to promote\n'
                 '  off the source; a replay does not nest it, since it runs every row it reaches under the hold '
                 'its caller\n'
                 '  took, through `_execute_namespace_locked`.\n')
LOCK_COMMENT_BASE = ('        # re-entrant because replay nests: `_vacate` replays rows through `_execute_namespace`, '
                     'which takes it.\n')
LOCK_COMMENT_NOW = ('        # re-entrant for `rename` around `_vacate` and the cut around `_serve`; a replay runs '
                    "under its caller's hold.\n")
RANGE = 't\'SELECT id FROM entry WHERE source_path >= {prefix} AND source_path < {prefix[:-1] + "0"}\')'
# A mutant that lets a row run again inside its own replay loops without end where the base recursed. This tripwire,
# planted with it, turns that into the RecursionError the base raises, so the kill is a failure, not a hang: no replay
# in test_replay.py pushes more than about 2,000 rows (measured on the planning prototype), so 20,000 is a runaway.
START = '        queue: List[Writeback] = []\n'
TRIPWIRE = PUSH + "                    pushes += 1\n" \
                  "                    if pushes > 20_000:\n" \
                  "                        raise RecursionError('replay ran past 20000 pushes')\n"
COUNTED = (OVERLAY, START, START + '        pushes = 0\n')
NOT_EMPTY = r'Directory not empty'
# A waiting row that never runs: the queue keeps it, the source keeps the directory, a new name meets that object, or
# the stray's row never acts.
NEVER_RUNS = r"Writeback\(|\['(?:rmdir|rename|mkdir|unlink)'|'d1': 'DIR'|Is a directory|DID NOT RAISE|assert \[\] == \["
SHORT = ((SHAPE, None), (POLL, None), (PUMP, None), (NAME, None), (STUCK, None))
# (label, ((file, exact text in the built tree, replacement), ...), selectors that must fail, why)
MUTANTS = (
    ('M1 no row waits: one reached inside the replay of a row it needs acts at once',
     ((OVERLAY, WAIT, '            if False:\n'),),
     SHORT + ((CHAINS, r'-2-'), (CROSSED, r'^mkdir$'), (VACATED, None)), NOT_EMPTY + '|' + CANNOT_MOVE),
    ('M2 the prefix skips no row on the stack',
     ((OVERLAY, SKIP, "                    if other.op == 'data':\n"), (OVERLAY, PUSH, TRIPWIRE), COUNTED),
     SHORT + ((CHAINS, None), (STRAY, r'after'), (CROSS, None), (CROSSED, None), (RACE, None), (REPLACED, None),
              (BESIDE, r'^one directory-'), (BESIDE, r'^entry removed between-'), (VACATED, None),
              (NEWNAME, r'^(one directory|entry removed between)-')), RECURSED),
    ('M3 the prefix skips only the row whose replay it is (the parked candidate)',
     ((OVERLAY, SKIP, "                    if other.op == 'data' or other is frame.row:\n"), (OVERLAY, PUSH, TRIPWIRE),
      COUNTED),
     ((CHAINS, None), (CROSS, r'^nested-')), RECURSED),
    ('M4 a nested row is not put on the stack',
     ((OVERLAY, PUSH, TRIPWIRE.replace('                    stack.add(mark(nested))\n', '')), COUNTED),
     ((NAME, None), (CROSSED, None), (RACE, None), (CHAINS, None), (CROSS, r'^nested-'), (VACATED, None),
      (NEWNAME, r'^(one directory|entry removed between)-')), RECURSED),
    ('M5 every row with a queued need waits, at the bottom too',
     ((OVERLAY, WAIT, '            if needs:\n'),),
     ((SHAPE, None), (STUCK, None), (CHAINS, None), (CROSS, None), (CROSSED, r'^mkdir$'), (STRAY, r'after'),
      (BESIDE, r'^(one directory|entry removed between)-(flush|poll|create|rename)$')), NEVER_RUNS),
    ('M6 the wait inverted: a row acts only when a row on the stack is among its needs',
     ((OVERLAY, WAIT, '            if not needs & stack:\n'),),
     ((BESIDE, None), (EEXIST, None), (STRAY, r'before'), (RACE, None), (NEWNAME, None)), NEVER_RUNS),
    ('M7 one prefix and no more: a row acts, or a rename raises, after its needs ran once',
     ((OVERLAY, FIXED_POINT, '            stuck = bool(needs) and frame.needs is not None\n'),),
     ((CROSS, r'^growing-'),), CANNOT_MOVE),
    ('M8 a rename needs nothing first',
     ((OVERLAY, RENAME_NEEDS, '                    ids = set()\n'),),
     ((CROSS, None), (CROSSED, None)), r'Error'),
    ('M9 the queue is never read again once a replay began',
     ((OVERLAY, LATER, '            return [other for other in queue if mark(other) not in done]\n'),),
     ((RACE, None),), NOT_EMPTY),
    ('M10 a directory\'s holders end one character early (`Catalog.held` range)',
     ((CATALOG, RANGE, RANGE.replace('{prefix[:-1] + "0"}', '{prefix + "0"}')),),
     SHORT + ((CHAINS, None), (REPLACED, None), (VACATED, None), (NEWNAME, r'^(one directory|entry removed between)-')),
     NOT_EMPTY),
    ('M11 a nested row whose pass freed nothing does not wait',
     ((OVERLAY, WAIT, '            if needs & stack:\n'),),
     ((CROSS, r'^inverted-'),), CANNOT_MOVE),
    ('M12 a row is known by its id alone (the replay\'s sets keyed by id; new rows still found by mark)',
     ((OVERLAY, MARK, '            return (row.id,)\n'),
      (OVERLAY, EXTEND, EXTEND.replace('if mark(other) in fresh',
                                       'if (other.id, other.entry, other.op, other.first_dirty) in fresh'))),
     ((RACE, r'True'),), NOT_EMPTY),
    ('M13 an `rmdir` reads the queue though its directory\'s object holds no other entry',
     ((OVERLAY, NO_HOLDER, ''),),
     ((PASSES, None),), GREW),
    ('M14 a rename reads the queue before it finds its target outside its own object',
     ((OVERLAY, OUTSIDE, '        queued()\n' + OUTSIDE),),
     ((PASSES, None),), GREW),
    ('M15 a new name\'s replay runs the whole queue, past the last row it needs',
     ((OVERLAY, ROOT_ROWS, ROOT_ROWS.replace('if other.id in rows', "if other.op != 'data'")),),
     ((VACATED, None),), STAYED),
)
# Equivalent or unreached, argued in PLAN § 4 and not run:
# - dropping the memo of waiting rows (`waiting`), or keying it otherwise, changes only how often a waiting row's
#   needs are read again, never what runs: the 500-chain cases take about 20 s each without it instead of about 2 s;
# - leaving the row a replay starts with off the stack lets its own prefix reach it once: that copy runs the same
#   prefix and acts, and the first then finds it gone, so the same rows act in the same order;
# - the bottom-stuck branch (an `rmdir` acts, a rename raises, after a bottom pass that freed nothing) is unreachable
#   from engine-written queues, since every need is held strictly inside the needing row's place, so mutants of what
#   it does, of `<=` against `==`, or of `stack != {mark(current)}` against `len(frames) > 1`, change nothing reachable;
# - a rename that needs every row inside its object, not only those between, and a row whose prefix restarts at its
#   cursor rather than at the queue's start, each matched the design on 12,000 differential runs (crossing and
#   directory-heavy walks) and pass every test: running more of the prefix, or skipping rows a scan already passed
#   and ran, leaves the same rows to act.

DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|\bdefer(?:s|red|ring|ral|rals)?\b|'
                      r'split (?:out|from)|stays? open|remains? open|left open|a later cycle|out of scope|owned by|'
                      r'record only|not a fix|\bhome(?:d|s)?\b(?![- ](?:director|path))', re.IGNORECASE)
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')    # written so this line cannot match itself
TOLERANCE = re.compile(r'\bxfail\b|\bskipif\b|mark\.skip\b|pytest\.skip\(', re.IGNORECASE)
SPEC_ID = re.compile(r'\b(?:R[1-9]|P[1-9]|Q[1-9])\b')
CITE = re.compile(r'(?<![\w/.-])((?:[\w-]+/)*([\w-]+\.py)):(\d+)(?:-(\d+))?')
PINNED = re.compile(r'\b(?:holds?|held|is|are|pinned)\s+at\s+`([0-9a-f]{7,40})`')
DATED = re.compile(r'^\s*(?:[-*]|\d+\.) \*?(?:Added |Corrected )?20\d\d-\d\d-\d\d')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
ROOT = Path.cwd()
LOCAL = Path('.local/drain-gate')
RUN_TIMEOUT = 600                 # every run but a seed's; the slowest, a mutant's whole file, takes about 30 s


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
    # Its own process group, killed whole on a timeout, so a mutant that hangs goes red rather than stalls the gate.
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

    def count(self, outcome: str) -> int:
        return sum(c.outcome == outcome for c in self.cases)

    def select(self, function: str, params: str | None = None) -> list[Case]:
        """The cases of test `function` in test_replay.py, narrowed to parameter ids matching `params`."""
        found = []
        for case in self.cases:
            if case.classname != CLASSNAME:
                continue
            name, _, rest = case.name.partition('[')
            if name == function and (params is None or re.search(params, rest.removesuffix(']'))):
                found.append(case)
        return found


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
                message = node.get('message') or ''         # the exception and its text, never the source
                break
        report.cases.append(Case(case.get('classname') or '', case.get('name') or '', outcome, message))
    return report


def pytest(args: list[str], label: str, env: dict[str, str] | None = None, cwd: str | None = None,
           drop: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess, Report]:
    where = scratch(label)
    xml = where / 'junit.xml'
    # Hypothesis writes .hypothesis/ into the working tree even under CI=true; its storage goes to this run's scratch.
    env = {'HYPOTHESIS_STORAGE_DIRECTORY': str(where / 'hypothesis'), **(env or {})}
    argv = ['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '-rfEs', '-o', 'junit_family=xunit1',
            f'--junitxml={xml}'] + args
    proc = run(argv, env=env, cwd=cwd, drop=drop)
    return proc, junit(xml)


def no_red(report: Report, scope: str) -> None:
    reds = [f'{c.classname}::{c.name} ({c.outcome})' for c in report.cases if c.outcome in ('failed', 'error')]
    check(not reds, f'{scope}: 0 failed and 0 errors (got {reds[:5]})')


CLASSNAME = REPLAY.removesuffix('.py').replace('/', '.')


def selected(report: Report, selectors: tuple) -> list[Case]:
    found = []
    for function, params in selectors:
        cases = report.select(function, params)
        check(bool(cases), f'{function}{f" [{params}]" if params else ""} selects at least one case')
        found += cases
    return found


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

def top_level(text: str) -> dict[str, str]:
    """Each top-level function and class of a module, by name, as its unparsed source."""
    return {n.name: ast.unparse(n) for n in ast.parse(text).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def code_of(text: str, name: str, owner: str | None = None) -> str:
    """Function `name` (a method of class `owner`, if given), unparsed without its docstring; '' unless defined once."""
    tree = ast.parse(text)
    scope = tree if owner is None else next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner),
                                            None)
    if scope is None:
        return ''
    found = [n for n in ast.walk(scope) if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(found) != 1:
        return ''
    node = found[0]
    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None),
                                                                        ast.Constant):
        node.body = node.body[1:]
    return ast.unparse(node)


def defs(text: str) -> dict[str, str]:
    """Every function of a module by qualified name (Class.method), unparsed without its docstring."""
    found = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f'{prefix}{child.name}.')
            elif isinstance(child, ast.FunctionDef):
                body = child.body
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None),
                                                                         ast.Constant):
                    child.body = body[1:] or [ast.Pass()]
                found[f'{prefix}{child.name}'] = ast.unparse(child)
    visit(ast.parse(text), '')
    return found


def regression_shape() -> None:
    module = ast.parse(Path(REPLAY).read_text())
    defs = [n for n in module.body if isinstance(n, ast.FunctionDef)]
    by_name = {n.name: n for n in defs}
    for name in CASES:
        check([n.name for n in defs].count(name) == 1, f'{name} is defined once in {REPLAY}')
        last = by_name[name].body[-1]
        call = last.value if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) else None
        check(call is not None and getattr(call.func, 'id', None) == 'assert_converged',
              f'{name} ends in assert_converged(...)')
    for name in ORACLE_TESTS:
        check('tree(oracle)' in ast.unparse(by_name[name]), f'{name} compares the source tree with the oracle\'s')
    def has_loop(statements: list) -> bool:
        return any(isinstance(n, (ast.For, ast.While, ast.AsyncFor)) for stmt in statements for n in ast.walk(stmt))

    for name in (SHAPE, STUCK, CHAINS):
        body = by_name[name].body
        at = [i for i, stmt in enumerate(body)
              if isinstance(stmt, ast.If) and ast.unparse(stmt.test) == "call == 'flush'"]
        tail = body[at[-1]:] if at else body
        text = '\n'.join(ast.unparse(stmt) for stmt in tail)
        once = text.count('engine.flush()') == 1 and text.count('drain(engine)') == 1
        check(bool(at) and once and not has_loop(tail),
              f'{name} calls its consumer once (one flush or one drain, no loop) before it asserts the queue drained')
    for name in (CROSSED, REPLACED, VACATED):
        text = ast.unparse(by_name[name])
        check(text.count('engine.flush()') == 1 and not has_loop(by_name[name].body), f'{name} calls flush once, '
                                                                                      f'no loop')
    text = ast.unparse(by_name[CROSS])
    check(text.count('drain(engine)') == 1 and text.count('.poll(') == 1 and text.count('engine.flush()') == 2
          and not has_loop(by_name[CROSS].body),
          f'{CROSS} calls one flush, one drain or one poll (and a flush after the poll, for data), no loop')
    check(ast.unparse(by_name[POLL]).count('.poll(') == 1 and not has_loop(by_name[POLL].body),
          f'{POLL} runs one poll, no loop')
    pump = ast.unparse(by_name[PUMP])
    check('time.monotonic() + 10' in pump and 'pump.stop(' in pump and 'pump.is_alive()' in pump,
          f'{PUMP} bounds its waits at 10 s, asserts the thread alive, and stops it (re-raising its death)')


def unchanged_code() -> None:
    against = base()
    old = top_level(git('show', f'{against}:{REPLAY}'))
    new = top_level(Path(REPLAY).read_text())
    changed = sorted(name for name, source in old.items() if new.get(name) != source)
    check(not changed, f'every function and class {REPLAY} held at the base is still defined, unchanged '
                       f'({len(old)} checked; changed {changed})')


def bodies(text: str) -> dict[str, str]:
    """The module's statements and each class's, functions left out, by owner ('' for the module), unparsed."""
    found = {}

    def visit(node: ast.AST, owner: str) -> None:
        kept = []
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                visit(child, f'{owner}{child.name}.')
            elif not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kept.append(child)
        if kept and isinstance(kept[0], ast.Expr) and isinstance(getattr(kept[0], 'value', None), ast.Constant):
            kept = kept[1:]                                 # a docstring
        found[owner] = '\n'.join(ast.unparse(stmt) for stmt in kept)
    visit(ast.parse(text), '')
    return found


def calls(node: ast.AST) -> set[str]:
    return {sub.func.attr for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)}


def confined() -> None:
    """
    The plan's shape: only the named functions change code or join; module and class bodies are the base's, so no
    engine, class or module state is added; the replay never calls itself or a row's execution, so it cannot recurse.
    """
    against = base()
    for path, allowed in CHANGED.items():
        before_text, after_text = git('show', f'{against}:{path}'), Path(path).read_text()
        before, after = defs(before_text), defs(after_text)
        gone = sorted(set(before) - set(after))
        check(not gone, f'{path} keeps every function the base defines (lost {gone})')
        moved = {name for name in after if before.get(name) != after[name]}
        check(moved == allowed, f'{path}: the functions whose code changed or joined are {sorted(allowed)} '
                                f'(got {sorted(moved)})')
        old, new = bodies(before_text), bodies(after_text)
        added = {owner.rstrip('.') for owner in set(new) - set(old)}
        check(added == NEW_CLASSES.get(path, set()), f'{path}: the classes added are '
                                                     f'{sorted(NEW_CLASSES.get(path, set()))} (got {sorted(added)})')
        changed = sorted(owner or '<module>' for owner in old if new.get(owner) != old[owner])
        check(not changed, f'{path}: module and class bodies are the base\'s (changed {changed})')
    touched = set(git('diff', '--name-only', against, 'HEAD', '--', 'src', 'tests').split())
    check(touched <= TOUCHED, f'the branch changes only {sorted(TOUCHED)} under src/ and tests/ (also: '
                              f'{sorted(touched - TOUCHED)})')
    tree = ast.parse(Path(OVERLAY).read_text())
    methods = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ('_replay_through', '_execute_namespace_locked', '_clear_inner_target', '_holder_rows'):
        reach = calls(methods[name]) & {'_replay_through', '_execute_namespace'}
        check(not reach, f'{name} calls neither _replay_through nor _execute_namespace (got {sorted(reach)}): no '
                         f'recursion')
    replay = ast.unparse(methods['_replay_through'])
    check('needs & stack' in replay and '_execute_namespace_locked(' in replay,
          '_replay_through checks a row\'s needs against the stack and acts through _execute_namespace_locked')


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
    confined()
    frozen_files()
    _, report = pytest(['-m', 'unit', REPLAY], 'regressions-here')
    no_red(report, f'{REPLAY} on this tree')
    for name, count in CASES.items():
        cases = report.select(name)
        check(len(cases) == count and all(c.outcome == 'passed' for c in cases),
              f'{name}: {count} cases collected, all passed (got {len(cases)}, '
              f'{sorted({c.outcome for c in cases})})')
    report = with_engine(extract_src(BASELINE, 'baseline'), ['-m', 'unit', REPLAY], 'regressions-baseline')
    fix = []
    for function, params, cause in FIX:
        cases = selected(report, ((function, params),))
        wrong = [f'{c.name} ({c.outcome}: {c.message[:60]!r})' for c in cases
                 if c.outcome != 'failed' or re.search(cause, c.message) is None]
        check(not wrong, f'{len(cases)} {function}{f" [{params}]" if params else ""} cases fail on the baseline '
                         f'engine with /{cause}/ (not: {wrong[:3]})')
        fix += cases
    pins = selected(report, PIN)
    wrong = [f'{c.name} ({c.outcome})' for c in pins if c.outcome != 'passed']
    check(not wrong, f'{len(pins)} pin cases pass on the baseline engine (not: {wrong[:4]})')
    check(len(fix) + len(pins) == sum(CASES.values()), f'fix and pin cases partition the {sum(CASES.values())} new '
                                                       f'cases ({len(fix)} + {len(pins)})')


# -- mutants -------------------------------------------------------------------------------------------------

def mode_mutants() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    named: set[str] = set()
    for label, edits, selectors, why in MUTANTS:
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
        cases = selected(report, selectors)
        wrong = [f'{c.name} ({c.outcome}: {c.message[:80]!r})' for c in cases
                 if c.outcome != 'failed' or re.search(why, c.message) is None]
        check(not wrong, f'{label}: {len(cases)} named cases fail for its cause, /{why}/ (not: {wrong[:3]})')
        named |= {c.name for c in cases}
    missing = sorted(set(collected_new()) - named)
    check(not missing, f'every new case is named by a mutant (missing {missing[:5]})')


def collected_new() -> list[str]:
    """The case names of the new tests, as collected on this tree."""
    proc = run(['uv', 'run', '--frozen', 'pytest', '-p', 'no:cacheprovider', '--co', '-q', '-m', 'unit', REPLAY],
               quiet=True, env={'HYPOTHESIS_STORAGE_DIRECTORY': str(scratch('collect') / 'hypothesis')})
    names = [line.split('::', 1)[1] for line in proc.stdout.splitlines() if line.startswith(f'{REPLAY}::')]
    found = [n for n in names if n.partition('[')[0] in CASES]
    check(len(found) == sum(CASES.values()), f'{len(found)} new cases collected == {sum(CASES.values())}')
    return found


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


def engine_count(text: str) -> set[int]:
    return set(int(n) for n in re.findall(r'(\d+) engine tests', text))


def docstring(text: str, name: str) -> str:
    found = [n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == name]
    return ' '.join((ast.get_docstring(found[0]) or '').split()) if len(found) == 1 else ''


def added_lines(against: str, *paths: str) -> list[tuple[str, str]]:
    """(file, text) for each line the branch adds under `paths`."""
    out, current = [], ''
    for line in git('diff', '-U0', against, '--', *paths).splitlines():
        if line.startswith('+++ '):
            current = line[6:] if line.startswith('+++ b/') else ''
        elif line.startswith('+') and current:
            out.append((current, line[1:]))
    return out


def mode_records() -> None:
    against = base()
    check(git('status', '--porcelain', '--', 'AGENTS.md', 'ROADMAP.md', 'issues', 'src', 'tests').strip() == '',
          'AGENTS.md, ROADMAP.md, issues/, src/ and tests/ are committed (records read them)')
    agents, old_agents = Path('AGENTS.md').read_text(), git('show', f'{against}:AGENTS.md')
    source, old_source = Path(OVERLAY).read_text(), git('show', f'{against}:{OVERLAY}')
    five = section(agents, '### 5. Committed source path')
    replay = [b for b in bullets(five) if b.startswith('**`_replay_through` is the one place')]
    check(len(replay) == 1, '§ 5: one bullet opens "`_replay_through` is the one place a namespace op jumps its '
                            'policy clock"')
    text = replay[0]
    check(re.search(r'explicit stack walked in a loop, never a recursion', text) is not None
          and re.search(r'never runs again inside its own replay', text) is not None,
          '§ 5: the replay bullet says the replay is an explicit stack in a loop, and a row on it never runs again')
    check(re.search(r'reads its needs again, until none is queued', text) is not None
          and '`_holder_rows`' in text and '`_clear_inner_target`' in text and '`Catalog.held`' in text,
          '§ 5: the bullet names what an rmdir and a rename need, and that a row reads its needs again until none')
    check(re.search(r'reached inside the replay of a row it needs[^.]*cannot act yet: it returns, stays queued', text)
          is not None, '§ 5: the bullet says a row reached inside the replay of a row it needs stays queued')
    check(re.search(r'`flush` and `drain` start a replay at every queued row they read[^.]*runs when the same call '
                    r'reaches it[^.]*one pass leaves none', text) is not None
          and re.search(r'scheduler poll does the same for the rows it reads, the due ones', text) is not None,
          '§ 5: the bullet says flush and drain start each row they read at the bottom, so a row that waited runs in '
          'the same call and one pass leaves none, and a poll does the same for its due rows')
    check(re.search(r'`_vacate` and `_purge_source` run every row up to the last they need and stop there: a row that '
                    r'waited inside their replay and lies past that row stays queued after the user call returns',
                    text) is not None,
          '§ 5: the bullet says a row that waited inside a new name\'s replay past its last need stays queued')
    check(re.search(r'The queue is read only for a row that can need one[^.]*\. A pass over other rows reads the '
                    r'queue a fixed number of times, not once per row', text) is not None,
          '§ 5: the bullet says only a row that can need a queued row reads the queue')
    # Review cycle 1, F1-F2: no record says a waiting row runs by the loop that started the replay, or that no row
    # waits in a flush's replays, or that a vacate runs every row that waited; evaluated over every file an agent or
    # a reader acts on (AGENTS.md, src/, tests/), whitespace folded so a phrase split across lines is still found.
    overclaims = ('at the latest the loop that started', 'at the latest the loop', 'where no row waits',
                  'runs here too')
    found = []
    for name in ['AGENTS.md'] + git('ls-files', 'src', 'tests').split():
        flat = ' '.join(Path(name).read_text(errors='replace').replace('#', ' ').split())
        found += [f'{name}: {phrase!r}' for phrase in overclaims if phrase in flat]
    check(not found, f'no record in AGENTS.md, src/ or tests/ overclaims when a waiting row runs ({found[:3]})')
    stuck = r'freed none of them waits too, unless it is at the bottom; there a rename raises'
    check(re.search(stuck, text) is not None and re.search(r'never by its id alone', text) is not None
          and re.search(r'removal queues while a replay\s+runs', text) is not None and '`Journal.queued_marks`' in text,
          '§ 5: the bullet states when a rename raises and how a removal queued meanwhile is read')
    check(re.search(r'queue \*prefix\* in order', text) is not None and 'escape hatch' in text,
          '§ 5: the bullet keeps the prefix-in-order rule and the escape hatch')
    old_ten, new_ten = section(old_agents, '### 10. Concurrency'), section(agents, '### 10. Concurrency')
    check(old_ten.count(REENTRANT_BASE) == 1 and new_ten == old_ten.replace(REENTRANT_BASE, REENTRANT_NOW),
          '§ 10 is the base\'s but for its re-entrancy sentence, which no longer names replay: no lock gains or '
          'loses a holder, and the engine gains no state to guard')
    check(old_source.count(LOCK_COMMENT_BASE) == 1 and LOCK_COMMENT_BASE not in source
          and source.count(LOCK_COMMENT_NOW) == 1,
          'the source lock\'s comment in overlay.py gives the same reasons as § 10, and no longer names replay')
    old_tables, new_tables = tables(old_agents), tables(agents)
    check(len(old_tables) == len(new_tables) == 2, 'both high-blast-radius tables are found')
    for old_table, new_table in zip(old_tables, new_tables):
        stripped = new_table.replace('`held`, ', '').replace('`held`/', '').replace(', `queued_marks`', '')
        check(stripped.replace('/`queued_marks`', '') == old_table,
              'a high-blast-radius table is the base\'s but for `Catalog.held` and `Journal.queued_marks`')
    for name in sorted({q.rpartition('.')[2] for names in CHANGED.values() for q in names}):
        check(all(re.search(rf'`{name}`|/`?{name}`?/|/`?{name}`?\b', t) for t in new_tables),
              f'both high-blast-radius tables list {name}')
    docs = {name: docstring(source, name) for name in DOCUMENTED}
    docs['drain'] = docstring(Path(DRAIN).read_text(), 'drain')
    replay_doc = docs['_replay_through']
    check(re.search(r'explicit stack, walked in a loop', replay_doc) is not None
          and re.search(r'never runs again inside its own replay', replay_doc) is not None
          and re.search(r'stays queued', replay_doc) is not None and re.search(r'never waits', replay_doc) is not None
          and re.search(r'runs when the same call reaches it', replay_doc) is not None
          and re.search(r'stays queued for a later call', replay_doc) is not None
          and re.search(r'costs no pass', replay_doc) is not None,
          '_replay_through\'s docstring says: an explicit stack in a loop, no row runs again inside its own replay, '
          'a nested row that needs one on it stays queued, the bottom row never waits')
    check('_replay_through' in docs['_execute_namespace'], '_execute_namespace\'s docstring points to the replay')
    check(re.search(r'once `_replay_through`', docs['_execute_namespace_locked']) is not None,
          '_execute_namespace_locked\'s docstring says it is the act, once the replay ran what the row needs')
    check(re.search(r'needs first', docs['_clear_inner_target']) is not None
          and re.search(r'raises', docs['_clear_inner_target']) is not None,
          '_clear_inner_target\'s docstring says it returns the rows a rename needs first, and when it raises')
    check('`queued`' in docs['_holder_rows'], '_holder_rows\'s docstring names the queue a replay hands it')
    check(re.search(r'starts its own replay', docs['_vacate']) is not None
          and re.search(r'stays queued for the next `flush`', docs['_vacate']) is not None,
          '_vacate\'s docstring says each row that frees the place starts its own replay, and a row that waited past '
          'the last stays queued for the next flush')
    check(re.search(r'runs when this call reaches it', docs['flush']) is not None,
          'flush\'s docstring says a row that waits inside another\'s replay runs when the flush reaches it')
    check(re.search(r'read only when', docs['_replay_through']) is not None
          and re.search(r'read first, and the queue only\s+when some entry holds the place', docs['_holder_rows'])
          and re.search(r'without reading the queue', docs['_clear_inner_target']) is not None,
          'the replay\'s, _holder_rows\'s and _clear_inner_target\'s docstrings say when the queue is read')
    for name in ('flush', 'drain'):
        check(re.search(r'starts its own replay', docs[name]) is not None
              and re.search(r'leaves? none (?:of them )?queued', docs[name]) is not None,
              f'{name}\'s docstring says each namespace row it reads starts its own replay, so one pass leaves none')
    for name, doc in docs.items():
        old = docstring(old_source if name != 'drain' else git('show', f'{against}:{DRAIN}'), name)
        check(doc != old, f'{name}\'s docstring changed from the base\'s')
    module = ' '.join(Path(REPLAY).read_text().split('"""')[1].split())
    check(re.search(r'one pass of `flush`, `drain` or a scheduler poll', module) is not None and 'nested' in module
          and 'five hundred chains' in module and 'restarted engine' in module and "each other's subtrees" in module
          and 'stays queued, and one flush then drains it' in module and 'a fixed number of times' in module,
          f'{REPLAY}\'s module docstring names the class it now guards (nested and sibling removals, one pass, a '
          f'restarted engine)')
    cited = run(['git', 'grep', '-n', 'issues/', 'HEAD', '--', 'src', 'tests'], quiet=True)
    check(cited.returncode == 1 and not cited.stdout.strip(),
          f'no file in src/ or tests/ at HEAD cites an issues/ seed (git grep: {cited.stdout.strip()[:120]!r})')
    long = [f'{file}: {len(text)}' for file, text in added_lines(against, 'src', 'tests') if len(text) > 120]
    check(not long, f'no line the branch adds in src/ or tests/ exceeds 120 characters ({long[:3]})')
    ids = [f'{file}: {text.strip()[:60]}' for file, text in added_lines(against, 'src', 'tests', 'README.md')
           if SPEC_ID.search(text)]
    check(not ids, f'no spec id in a line the branch adds to src/, tests/ or README.md ({ids[:3]})')
    stated = stated_unit_count()
    check(stated == UNIT_FLOOR + sum(CASES.values()), f'AGENTS.md states {stated} unit tests == {UNIT_FLOOR} + the '
                                                      f'{sum(CASES.values())} new cases')
    totals = re.findall(r'(\d+) tests \((\d+) `unit`, (\d+) `integration`\)', agents)
    check(len(totals) == 1 and int(totals[0][0]) == int(totals[0][1]) + int(totals[0][2])
          and int(totals[0][1]) == stated, f'the repository map\'s total is unit + integration ({totals})')
    old_engine = engine_count(old_agents)
    check(len(old_engine) == 1 and engine_count(agents) == {old_engine.pop() + sum(CASES.values())},
          f'AGENTS.md\'s engine-test count grew by the {sum(CASES.values())} new cases ({engine_count(agents)})')
    touched = set(git('diff', '--name-only', against, 'HEAD').split())
    needed = {OVERLAY, DRAIN, REPLAY, CATALOG, JOURNAL, 'AGENTS.md'}
    check(needed <= touched, f'the branch diff carries the fix and its records (missing {sorted(needed - touched)})')
    cites_hold(against)


def cite_blocks(text: str) -> list[str]:
    """
    One block per list item or paragraph: its own lines, the deeper-indented items nested under it, and, for a
    paragraph, a dated item that follows it directly. A pin ("holds at `<sha>`") counts for the cites in its
    block, so a dated line pins the item it sits under, never a sibling or a distant one.
    """
    units: list = []                             # [indent, kind, lines]; None marks a blank line
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


def spanned(text: str, low: int, high: int) -> str | None:
    """The name of the function defined exactly on lines low..high of `text`, if one is."""
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.lineno == low and node.end_lineno == high:
            return node.name
    return None


def cites_hold(against: str) -> None:
    """
    In every issues/ seed, each file:line cite of a .py file the branch changes is pinned (its block says it
    "holds at `<sha>`") or still points at the text it pointed at in the base.
    """
    changed = {Path(f).name for f in git('diff', '--name-only', against, 'HEAD').split() if f.endswith('.py')}
    seen = 0
    for seed in sorted(str(p) for p in Path('issues').glob('*.md')):
        for block in cite_blocks(Path(seed).read_text()):
            pinned = bool(PINNED.search(block))
            for match in CITE.finditer(block):
                where, name, low = match.group(1), match.group(2), int(match.group(3))
                high = int(match.group(4) or low)
                if name not in changed or pinned:
                    continue
                full = next((f for f in git('ls-files', f'*{where}').split() if f.endswith(where)), None)
                if full is None:
                    continue                      # a bare `name.py:N` of another directory's file
                seen += 1
                before, after = git('show', f'{against}:{full}'), Path(full).read_text()
                same = before.splitlines()[low - 1:high] == after.splitlines()[low - 1:high]
                spans = spanned(before, low, high)          # a cite of a whole function holds if it still spans it
                check(same or (spans is not None and spans == spanned(after, low, high)),
                      f'{seed}: {match.group(0)} still holds what it cited at the base (or the whole function it '
                      f'spans), or is pinned')
    say(f'  ok    {seen} unpinned cite(s) of changed files across issues/ checked')


def mode_seedfile() -> None:
    against = base()
    check(git('status', '--porcelain', '--', 'ROADMAP.md', 'issues', 'src').strip() == '',
          'ROADMAP.md, issues/ and src/ are committed')
    check(Path(SEED).exists(), f'{SEED} exists')
    text = Path(SEED).read_text()
    front = text.split('---')[1] if text.startswith('---') else ''
    check(re.search(r'^status: unshaped$', front, re.MULTILINE) is not None
          and re.search(r'^kind: fix$', front, re.MULTILINE) is not None,
          f'{SEED} is an unshaped fix seed')
    flat = ' '.join(text.split())
    for needle in ("KeyError: 'project'", 'boundary_policies', 'deadline', 'build_spec(', 'tiers=1',
                   'WritebackScheduler(', '.poll(', '8d2265f', 'nested-rmdir-replay-recursion'):
        check(needle in flat, f'{SEED} carries {needle!r} (its reproduction, cause, blame and origin)')
    # Review cycle 1, F3: a restart does not run the stuck row; the restarted scheduler's first scan raises again.
    check('a restart runs it' not in flat and 'A restart does not' in flat and 'raises the same `KeyError`' in flat,
          f'{SEED} says only a flush or a drain runs the stuck row, and that a restart raises again')
    roadmap = Path('ROADMAP.md').read_text()
    lines = [line for line in roadmap.splitlines() if Path(SEED).name in line]
    check(len(lines) == 1 and lines[0].startswith('**Seed:**'), f'ROADMAP.md lists {SEED} on one Seed line')
    queued = re.split(r'^## ', roadmap.split('\n## Queued', 1)[-1], flags=re.MULTILINE)[0]
    check(Path(SEED).name in queued, f'the entry sits under ROADMAP.md\'s Queued section')
    check(git('diff', against, 'HEAD', '--', WRITEBACK).strip() == '', f'{WRITEBACK} is the base\'s (a record only)')
    seen = 0
    for match in CITE.finditer(text):
        where, low, high = match.group(1), int(match.group(3)), int(match.group(4) or match.group(3))
        full = next((f for f in git('ls-files', f'*{where}').split() if f.endswith(where)), None)
        check(full is not None, f'{SEED}: {match.group(0)} names a tracked file')
        lines_now = Path(full).read_text().splitlines()
        check(high <= len(lines_now) and all(lines_now[i - 1].strip() for i in range(low, high + 1)),
              f'{SEED}: {match.group(0)} points at non-blank lines at HEAD')
        seen += 1
    check(seen >= 3, f'{SEED} cites at least three file:line anchors ({seen})')
    ids = [line for line in text.splitlines() if re.search(r'\bspec/[a-z]', line) and SLUG not in line]
    check(not ids, f'{SEED} cites no other cycle\'s spec ({ids[:2]})')


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
    for name, count in CASES.items():
        cases = report.select(name)
        check(len(cases) == count and all(c.outcome == 'passed' for c in cases), f'{name}: {count} cases passed')
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
    """R8 from committed state: no CI, no .hypothesis/ before any run, two workers at most."""
    low, high = (int(x) for x in seeds.split('-'))
    check(git('status', '--porcelain').strip() == '', 'the tree is committed (the runs read HEAD)')
    lanes = [list(range(low + i, high + 1, 2)) for i in (0, 1)]
    workers = [(seed_tree(f'seeds-{i}'), lane) for i, lane in enumerate(lanes) if lane]
    aside = scratch('seeds-hypothesis')
    token = f'drain-gate-{uuid.uuid4().hex}'
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
    check(git('status', '--porcelain').strip() == '', 'the tree is clean (the ledger reads committed TECH.md, issues/ '
                                                      'and ROADMAP.md)')
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
    mode_seedfile()
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
    """R8 from the run record: every job concluded success; nothing is tolerated."""
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


MODES = {'regressions': mode_regressions, 'mutants': mode_mutants, 'records': mode_records,
         'seedfile': mode_seedfile, 'suite': mode_suite, 'seeds': mode_seeds, 'ledger': mode_ledger,
         'prepush': mode_prepush, 'ci': mode_ci}
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
