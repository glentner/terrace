# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the source-path-truncate-and-stat cycle.

Usage (from the cycle worktree root, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I spec/source-path-truncate-and-stat/verify/truncate_gate.py MODE [MODE ...]

Modes:
    regressions R1-R3  each named test is defined once, walks before acting and ends in assert_converged (which
                       still compares sizes); all pass on this tree; each fix test fails on the baseline engine
                       for its named cause (BASELINE_CAUSE); each pin passes on the baseline engine; on the engine
                       each review cycle graded (REVIEWED, REVIEWED2), that cycle's regressions fail for their
                       causes (REVIEW_CAUSE, REVIEW2_CAUSE) and every other named test passes; test_model.py and
                       the engine conftest are byte-identical to the base; no skip or xfail is added
    mutants     R1-R3  each fix-removal mutant (planted into a scratch copy of HEAD's src/) makes every test it
                       names fail, and leaves the rest of test_replay.py runnable; each recorded equivalent
                       mutant's anchor still occurs once
    records     R4     AGENTS.md: § 5 and § 10 name the handle-less truncate, both high-blast-radius tables keep
                       every base name and name every overlay.py function the branch changes, the § 5
                       open-violation rows hand the old sizes to issues/audit-command.md and name the open/_serve
                       window's seed, the identity gap (file only) and the recording keyed by a moved path (its
                       own seed, listed in ROADMAP.md), the test counts agree with each other; no live record
                       repeats a claim review cycle 2 found false; PLAN.md no longer calls truncate open; the
                       audit seed carries the probe's facts; touched seeds' file:line cites hold
    suite       R5     CI=true: tools/lint.sh exits 0; pytest -n auto -m unit has 0 failed, 0 errors, passed >
                       474 and == AGENTS.md's stated count, with test_model and every named test passing
    seeds       R6     test_model passes for each --hypothesis-seed in --seeds=A-B (default 1-20), without CI,
                       two scratch trees built from HEAD, one worker each, no .hypothesis/ before any run
    ledger             every deferral phrase in TECH.md's phase bodies names an issues/ seed that ROADMAP.md lists
    prepush            clean tree, suite, records, then no home-directory path in the branch diff or messages
    ci          R7     the push run for the pushed commit (--sha, default origin/<branch>; later commits may touch
                       only spec/): all four jobs concluded success, Linux passed == the stated count with no
                       skip, macOS skipped only tests/fuse naming libfuse. --run-json/--log-dir read a saved
                       `gh run view --json` record and job logs (<job id>.log or <job name>.log) instead

"The baseline engine" is `src/` at BASELINE (the GOAL's tree; its src/ is 0e6d14c's), extracted with
`git archive` into scratch and put first on PYTHONPATH; each run checks which sosfs it imported. "The base" for
file comparisons is the merge base with origin/main; "the plan commit" (the commit that added this cycle's
PLAN.md) is the base for which seeds the build touched. Counts come from pytest's JUnit XML, never from the exit
code. Scratch and a copy of this run's output go under .local/truncate-gate/ (gitignored, survives a reboot).

Footprint: one subprocess at a time, except `seeds`, which runs two pytest processes at once (each its own
scratch tree, environment and process group, killed as a group on the first red or a per-seed timeout).
`suite` and `prepush` run pytest with -n auto (one worker per core, about 1.5 GB peak on a 10-core machine);
every other pytest run is serial and under 500 MB. Measured on the planning prototype (2026-11-06):
`regressions`, `mutants` (then eleven serial test_replay runs, one per mutant) and `records` together 10 s,
`suite` about 30 s, `seeds` 4 min 46 s for 20 seeds. Nothing is deleted; the gate never pushes and never writes to the tracked tree.

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


SLUG = 'source-path-truncate-and-stat'
BASELINE = '0a88f30'          # the GOAL's tree; src/ unchanged since 0e6d14c, where the GOAL counted 474
UNIT_FLOOR = 474              # R5: strictly more than this pass
MODEL = 'tests/engine/test_model.py'
MODEL_ID = ('tests.engine.test_model.TestOverlayModel', 'runTest')       # JUnit (classname, name)
REPLAY = 'tests/engine/test_replay.py'
OVERLAY = 'src/sosfs/engine/overlay.py'
FROZEN = (MODEL, 'tests/engine/conftest.py', 'tests/conftest.py')       # byte-identical to the base (GOAL Q2)
OWNER = 'issues/audit-command.md'
WINDOW_SEED = 'issues/source-served-open-race.md'                      # filed by the plan commit
OWN_SEED = f'issues/{SLUG}.md'
RECORDING_SEED = 'issues/truncate-recording-after-ancestor-rename.md'  # review cycle 2 F6, ruled out of the cycle
# Claims review cycle 2 found false (its reopened F1): the cut re-deciding before-the-wait values, rather than
# deciding once under the lock it always takes. Read with whitespace collapsed.
OVERCLAIMS = ('decides again under that lock everything', 'Everything decided before that lock is decided again',
              'closes it there (its `spec/` PLAN § 2.1 and review cycle 1)', 'the copy that serves it found again',
              'takes it to cut one')

# Tests the build writes into tests/engine/test_replay.py (names fixed here, PLAN § 6).
FIX_TESTS = (                    # fail on the baseline engine with FileNotFoundError
    'test_truncate_without_a_handle_behind_its_own_rename',
    'test_truncate_without_a_handle_grows_behind_its_own_rename',
    'test_truncate_without_a_handle_inside_a_renamed_directory',
    'test_truncate_without_a_handle_of_an_adopted_file_inside_a_renamed_directory',
    'test_truncate_without_a_handle_of_a_file_created_inside_a_renamed_directory',
    'test_truncate_without_a_handle_of_a_committed_file_behind_its_own_rename',
    'test_truncate_without_a_handle_after_a_queued_swap',
    'test_truncate_without_a_handle_cuts_under_the_source_lock',
    'test_truncate_without_a_handle_resolves_inside_the_source_lock',
    'test_truncate_without_a_handle_after_a_concurrent_rename_moves_the_file_away',
    'test_truncate_without_a_handle_after_a_concurrent_removal',
    'test_truncate_without_a_handle_leaves_a_file_that_reused_the_entry_id_alone',
)
CONCURRENT = FIX_TESTS[-3:]          # the interleaved user ops: rename away, removal, a reused entry id
# Review cycle 1. F2: a faster copy taken during the wait is the one cut (fails on the reviewed engine too). F3: a
# different file carried onto the path is rejected by the id check alone, and the entry checks before the lock.
FASTER_COPY = 'test_truncate_without_a_handle_cuts_a_faster_copy_made_during_the_wait'
CARRIED = 'test_truncate_without_a_handle_leaves_another_file_carried_onto_the_path_alone'
MISSING = 'test_truncate_without_a_handle_of_a_missing_path_or_a_directory'
REVIEWED = 'aec3676'          # the review's tree (its src/ is c3f31ff's, the reviewed commit)
REVIEW_CAUSE = {FASTER_COPY: r"assert residencies == \{'project': \('clean', 10\), 'scratch': \('dirty', 4\)\}"}
# Review cycle 2 (Geoffrey's escalation ruling): every handle-less cut takes the source lock and decides under it,
# once, the entry, its kind, the serving copy, the held place and the write keys, and never follows a link. F4: a
# stage-served cut holds the lock, and a stage copy evicted during the wait is replaced under it, never cut around
# it; the review's wrong-object interleaving, moved into the wait, is refused. F5: a symlink or a directory that
# took the id is refused with ENOENT; a link at the held place is not followed. F3: a symlink is refused before the
# lock (the review's M03); the faster-copy test asserts the tier counters (the review's M20).
STAGED_HOLDS = 'test_truncate_without_a_handle_of_a_staged_copy_holds_the_source_lock'
FLIP = 'test_truncate_without_a_handle_after_the_serving_copy_flips_to_the_source'
WRONG_OBJECT = 'test_truncate_without_a_handle_leaves_a_file_moved_onto_the_old_source_place_alone'
SYMLINK_ID = 'test_truncate_without_a_handle_refuses_a_symlink_that_took_the_entry_id'
DIRECTORY_ID = 'test_truncate_without_a_handle_refuses_a_directory_that_took_the_entry_id'
SYMLINK = 'test_truncate_without_a_handle_of_a_symlink'
NOFOLLOW = 'test_truncate_without_a_handle_never_follows_a_link_at_the_held_place'
CYCLE2_FIX = (STAGED_HOLDS, FLIP, WRONG_OBJECT, SYMLINK_ID, DIRECTORY_ID, NOFOLLOW)
REVIEWED2 = 'ea38083'         # review cycle 2's reviewed commit
REVIEW2_CAUSE = {
    STAGED_HOLDS: r'assert \[True\] == \[False\]',
    FLIP: r'assert engine\._source_lock\.ran',
    WRONG_OBJECT: 'DID NOT RAISE',
    SYMLINK_ID: 'DID NOT RAISE',
    DIRECTORY_ID: r"AttributeError: 'NoneType' object has no attribute 'tier'",
    NOFOLLOW: 'DID NOT RAISE',
}
INTERLEAVED = CONCURRENT + (FASTER_COPY, CARRIED)
# Why each fix test fails on the baseline engine. The baseline cuts at the logical path and takes no lock, so most
# raise FileNotFoundError; the interleaved user ops never fire there (no lock is taken), so the call does not
# raise where those tests expect it to.
BASELINE_CAUSE = {name: 'FileNotFoundError' for name in FIX_TESTS}
BASELINE_CAUSE.update({name: 'DID NOT RAISE' for name in CONCURRENT + (CARRIED, WRONG_OBJECT, SYMLINK_ID,
                                                                     DIRECTORY_ID)})
BASELINE_CAUSE[FASTER_COPY] = r'assert engine\._source_lock\.ran'
BASELINE_CAUSE[FLIP] = r'assert engine\._source_lock\.ran'
BASELINE_CAUSE[STAGED_HOLDS] = r'assert \[True\] == \[False\]'
BASELINE_CAUSE[NOFOLLOW] = r'assert 2 == '                 # ENOENT at the logical path, not ELOOP at the link
PIN_TESTS = (                    # pass on the baseline engine too; `mutants` is their teeth
    'test_truncate_without_a_handle_of_a_staged_copy_inside_a_renamed_directory',
    'test_size_through_a_bypass_stage_behind_its_own_rename',
    'test_size_through_a_bypass_stage_inside_a_renamed_directory',
    'test_release_of_a_source_served_copy_stats_under_the_source_lock',
    MISSING,
    SYMLINK,
)
EARLIER_SIZE_TEST = 'test_size_of_a_file_created_on_the_source_inside_renamed_directory'
FIX_TESTS += (FASTER_COPY, CARRIED) + CYCLE2_FIX
NEW_TESTS = FIX_TESTS + PIN_TESTS
CYCLE1_TESTS = tuple(name for name in NEW_TESTS if name not in CYCLE2_FIX + (SYMLINK,))
STAGED = 'test_truncate_without_a_handle_of_a_staged_copy_inside_a_renamed_directory'

# (id, file, ((exact text in the built tree, replacement), ...), tests that must fail). Anchors are the built
# engine's own text; a mutant whose anchor is not found exactly once is red, so a build that reshapes a guarded line
# re-anchors it here in the same commit (PLAN § 6). The decisions they attack: where the cut lands, under which
# lock, from which lookup of the entry and with which checks, which copy serves, whether the lock is released,
# which counter moves, the dirty mark's order (§ 1), whether the open follows a link, and the stat and the lock R2
# pins. Review cycle 2 re-anchored every mutant on the restructured cut and added M21-M29.
LOCKED = '                with self._source_lock:\n                    current = self.catalog.get(path)'
DECIDED = "current is None or current.id != entry.id or current.kind != 'file'"
GUARD = ("current = self.catalog.get(path)\n"
         f"                    if {DECIDED}:\n"
         "                        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)\n")
SERVED = ('                    serving = self._serve(current)\n'
          '                    held = self._held_path(current, serving)\n')
MARK = ('                    if upstream is not None:\n'
        '                        self.journal.mark_dirty(path, serving, to_tier=upstream.name, now=now)\n')
ENSURED = ("                entry = self._ensure(path)\n"
           "                if entry is None:\n"
           "                    raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)\n"
           "                if entry.kind != 'file':\n"
           "                    raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)\n"
           "                with self._source_lock:\n")
OPENED = 'open_direct(_pure(held), os.O_WRONLY | os.O_NOFOLLOW)'
CUT_END = ('                        os.close(fd)\n'
           '                    previous = self.catalog.get_residency(path, serving)\n')
COUNTED = '                        if previous is not None and serving != self.source.name:\n'
HELD = 'return entry.source_path if tier == self.source.name and entry.source_path is not None else entry.path'
MUTANTS = (
    ('M1 the cut opens the logical path', OVERLAY,
     ((OPENED, OPENED.replace('_pure(held)', '_pure(path)')),),
     FIX_TESTS[:7]),
    ('M2 the cut takes no source lock', OVERLAY,
     ((LOCKED, LOCKED.replace('self._source_lock', 'contextlib.nullcontext()')),),
     ('test_truncate_without_a_handle_cuts_under_the_source_lock',
      'test_truncate_without_a_handle_resolves_inside_the_source_lock') + INTERLEAVED
     + (STAGED_HOLDS, FLIP, WRONG_OBJECT, SYMLINK_ID, DIRECTORY_ID)),
    ('M3 the cut resolves the entry read before the lock', OVERLAY,
     ((SERVED, SERVED.replace('(current', '(entry')),),
     ('test_truncate_without_a_handle_resolves_inside_the_source_lock',)),
    ('M4 the lock is taken only when the copy served before the wait is the source', OVERLAY,
     ((LOCKED, LOCKED.replace('with self._source_lock:',
                              'with self._source_lock if self._serve(entry) == self.source.name '
                              'else contextlib.nullcontext():')),),
     (STAGED_HOLDS, FLIP, WRONG_OBJECT)),
    ('M5 the held place is applied to the wrong tier', OVERLAY,
     ((HELD, HELD.replace('tier == self.source.name', 'tier != self.source.name')),),
     (STAGED, 'test_truncate_without_a_handle_behind_its_own_rename')),
    ('M6 the entry is looked up by id alone', OVERLAY,
     ((GUARD, 'current = self.catalog.by_id(entry.id)\n'),),
     CONCURRENT),
    ('M7 a served copy is stat\'ed at its logical path', OVERLAY,
     (('return self.tiers[tier].stat(_pure(self._held_path(entry, tier)))',
       'return self.tiers[tier].stat(_pure(entry.path))'),),
     ('test_size_through_a_bypass_stage_behind_its_own_rename',
      'test_size_through_a_bypass_stage_inside_a_renamed_directory', EARLIER_SIZE_TEST)),
    ('M8 recording a served copy takes no source lock', OVERLAY,
     (('with self._source_lock if on_source else contextlib.nullcontext():\n                entry = self.catalog.by_id(handle.entry_id)',
       'with contextlib.nullcontext():\n                entry = self.catalog.by_id(handle.entry_id)'),),
     ('test_release_of_a_source_served_copy_stats_under_the_source_lock',)),
    ('M9 the cut never releases the source lock', OVERLAY,
     ((LOCKED, LOCKED.replace('with self._source_lock:',
                              'with (self._source_lock.__enter__(), contextlib.nullcontext())[1]:')),),
     ('test_truncate_without_a_handle_cuts_under_the_source_lock',)),
    ('M10 the cut adjusts the source counter', OVERLAY,
     (('if previous is not None and serving != self.source.name:\n                            self.registry.adjust_usage(serving,',
       'if previous is not None:\n                            self.registry.adjust_usage(serving,'),),
     ('test_truncate_without_a_handle_behind_its_own_rename',)),
    ('M11 the dirty mark commits after the cut', OVERLAY,
     ((MARK, ''), (CUT_END, CUT_END.replace('                    previous',
                                            '                    if upstream is not None:\n'
                                            '                        self.journal.mark_dirty(path, serving, '
                                            'to_tier=upstream.name, now=now)\n'
                                            '                    previous'))),
     (STAGED,)),
    ('M12 the serving copy is decided before the wait', OVERLAY,
     ((LOCKED, '                serving = self._serve(entry)\n' + LOCKED),
      (SERVED, '                    held = self._held_path(current, serving)\n')),
     (FASTER_COPY, FLIP)),
    ('M13 the entry found under the lock is not compared by id', OVERLAY,
     ((GUARD, GUARD.replace(DECIDED, "current is None or current.kind != 'file'")),),
     (CARRIED, WRONG_OBJECT)),
    ('M14 a missing entry under the lock is not refused', OVERLAY,
     ((GUARD, GUARD.replace(DECIDED, "current.id != entry.id or current.kind != 'file'")),),
     ('test_truncate_without_a_handle_after_a_concurrent_removal',
      'test_truncate_without_a_handle_leaves_a_file_that_reused_the_entry_id_alone')),
    ('M15 a path with no entry is not refused', OVERLAY,
     ((ENSURED, ENSURED.replace("                if entry is None:\n"
                                "                    raise FileNotFoundError(errno.ENOENT, 'No such file or directory', "
                                "path)\n", '')),),
     (MISSING,)),
    ('M16 a directory is not refused before the lock', OVERLAY,
     ((ENSURED, ENSURED.replace("                if entry.kind != 'file':\n"
                                "                    raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)\n",
                                '')),),
     (MISSING, SYMLINK)),
    ('M17 the cut skips the promotion', OVERLAY,
     ((SERVED, SERVED.replace('self._serve(current)', 'self.catalog.fastest_copy(path).tier')),),
     (STAGED, FLIP)),
    ('M18 the cut marks nothing dirty', OVERLAY,
     ((MARK, ''),),
     (STAGED, FASTER_COPY, FLIP)),
    ('M19 the cut records no size in the entry', OVERLAY,
     (('                        self.catalog.update_meta(path, size=length, mtime=now)\n', ''),),
     FIX_TESTS[:7]),
    ('M20 the cut records no size in the residency', OVERLAY,
     (('                        self.catalog.update_residency(path, serving, size=length, mtime=now)\n', ''),),
     ('test_truncate_without_a_handle_behind_its_own_rename', 'test_truncate_without_a_handle_inside_a_renamed_directory',
      STAGED)),
    ('M21 the kind is not decided under the lock', OVERLAY,
     ((GUARD, GUARD.replace(DECIDED, 'current is None or current.id != entry.id')),),
     (SYMLINK_ID, DIRECTORY_ID)),
    ('M22 the cut follows a symbolic link', OVERLAY,
     ((OPENED, OPENED.replace(' | os.O_NOFOLLOW', '')),),
     (NOFOLLOW,)),
    ('M23 only a directory is refused before the lock', OVERLAY,
     ((ENSURED, ENSURED.replace("if entry.kind != 'file':", "if entry.kind == 'dir':")),),
     (SYMLINK,)),
    ('M24 the cut moves no counter', OVERLAY,
     ((COUNTED, '                        if False:\n'),),
     (STAGED, FASTER_COPY, FLIP)),
    ('M25 a staged copy is cut at the source path', OVERLAY,
     ((SERVED, SERVED.replace('self._held_path(current, serving)', 'self._held_path(current, self.source.name)')),),
     (STAGED,)),
    ('M26 every tier holds the bytes at the source path', OVERLAY,
     ((HELD, HELD.replace('tier == self.source.name and entry.source_path is not None',
                          'entry.source_path is not None')),),
     (STAGED,)),
    ('M27 the cut takes no path lock', OVERLAY,
     (('            with self.locks.hold(path):\n' + ENSURED, '            with contextlib.nullcontext():\n' + ENSURED),),
     (STAGED_HOLDS,)),
    ('M28 the entry is not read again under the lock', OVERLAY,
     ((GUARD, GUARD.replace('current = self.catalog.get(path)', 'current = entry')),),
     ('test_truncate_without_a_handle_resolves_inside_the_source_lock',) + CONCURRENT),
    ('M29 every tier holds the bytes at the logical path', OVERLAY,
     ((HELD, 'return entry.path'),),
     FIX_TESTS[:7] + ('test_size_through_a_bypass_stage_behind_its_own_rename',)),
)
# Equivalent mutants, kept beside the code they leave unchanged in effect: `mutants` checks each anchor still occurs
# once, and they were run by hand under CI=true and -m unit (the cycle's TECH.md records the runs and E1's re-judging).
EQUIVALENT = (
    ('E1 the counter guard drops its missing-residency test', OVERLAY,
     ((COUNTED, COUNTED.replace('previous is not None and ', '')),),
     'under the path lock and the source lock the serving copy was just found or made by _serve, so its row exists '
     'while the entry stays at the path; it differs only where an ancestor rename already past its _vacate moves '
     'the entry before the recording (review cycle 2 F6, recorded and deferred), raising AttributeError there '
     'where this tree leaves the size unrecorded'),
    ('E2 the held place drops its NULL test', OVERLAY,
     ((HELD, HELD.replace(' and entry.source_path is not None', '')),),
     'a copy on the source sets source_path in the transaction that records it (adoption, create, commit), so a '
     'source residency never meets a NULL source_path'),
)


DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|deferr(?:al|als|ed)\b|'
                      r'split (?:out|from)|stays? open|out of scope', re.IGNORECASE)
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')    # written so this line cannot match itself
TOLERANCE = re.compile(r'\bxfail\b|\bskipif\b|mark\.skip\b|pytest\.skip\(', re.IGNORECASE)
CITE = re.compile(r'(?<![\w/.-])((?:[\w-]+/)*([\w-]+\.py)):(\d+)(?:-(\d+))?')
PIN = re.compile(r'\b(?:holds?|held|is|are|pinned)\s+at\s+`([0-9a-f]{7,40})`')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
ROOT = Path.cwd()
LOCAL = Path('.local/truncate-gate')


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
    proc = subprocess.run(argv, env={**inherited, **(env or {})}, text=True, capture_output=True, cwd=cwd)
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
    check(re.search(r'assert sizes\(engine\) == counts', text) and re.search(r'assert sizes\(again\) == counts', text),
          'assert_converged compares every reported size with the byte count, before and after the remount')
    walkers = {'walk'} | {n.name for n in defs if 'walk' in names_in(n) and not n.name.startswith('test_')}
    for name in NEW_TESTS:
        check([n.name for n in defs].count(name) == 1, f'{name} is defined once in {REPLAY}')
        node = by_name[name]
        last = node.body[-1]
        ends = isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) \
            and getattr(last.value.func, 'id', None) == 'assert_converged'
        check(ends and bool(names_in(node) & walkers), f'{name} walks the tree and ends in assert_converged(...)')


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
    frozen_files()
    _, report = pytest(['-m', 'unit', REPLAY], 'regressions-here')
    no_red(report, f'{REPLAY} on this tree')
    for name in NEW_TESTS + (EARLIER_SIZE_TEST,):
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
    for rev, names, causes in ((REVIEWED, CYCLE1_TESTS, REVIEW_CAUSE), (REVIEWED2, NEW_TESTS, REVIEW2_CAUSE)):
        report = with_engine(extract_src(rev, 'reviewed'), ['-m', 'unit', REPLAY], f'regressions-reviewed-{rev}')
        for name in names:
            got, message = report.outcome(CLASSNAME, name), report.message(CLASSNAME, name)
            if name in causes:
                check(got == ['failed'] and re.search(causes[name], message) is not None,
                      f'{name} fails on the reviewed engine ({rev}) for its cause (got {got}, {message[:100]!r})')
            else:
                check(got == ['passed'], f'{name} passes on the reviewed engine ({rev})')


# -- fix-removal mutants ------------------------------------------------------------------------------------

def mode_mutants() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
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
    text = (ROOT / OVERLAY).read_text()
    for label, file, edits, why in EQUIVALENT:
        for number, (old, _) in enumerate(edits, 1):
            check(text.count(old) == 1, f'{label} (equivalent: {why[:60]}...): anchor {number} occurs once in {file}')


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


def mode_records() -> None:
    against = base()
    check(git('status', '--porcelain', '--', 'AGENTS.md', 'PLAN.md', 'ROADMAP.md', 'issues', 'src', 'tests').strip()
          == '', 'AGENTS.md, PLAN.md, ROADMAP.md, issues/, src/ and tests/ are committed (records read them)')
    agents = Path('AGENTS.md').read_text()
    five = section(agents, '### 5. Committed source path')
    reading = [b for b in bullets(five) if '_stat_held' in b]
    check(len(reading) == 1 and word('truncate', reading[0]),
          '§ 5: the bullet that resolves reads through the committed source path names truncate')
    ten = section(agents, '### 10. Concurrency')
    mutex = [b for b in bullets(ten) if 'source-namespace mutex' in b]
    check(len(mutex) == 1 and word('truncate', mutex[0]), '§ 10: the source-mutex bullet names truncate as a taker')
    old_tables, new_tables = tables(git('show', f'{against}:AGENTS.md')), tables(agents)
    check(len(old_tables) == len(new_tables) == 2, 'both high-blast-radius tables are found')
    for index, (old, new) in enumerate(zip(old_tables, new_tables), 1):
        lost = sorted(set(re.findall(r'`([^`]+)`', old)) - set(re.findall(r'`([^`]+)`', new)))
        lost = [t for t in lost if not all(word(part, new) for part in re.split(r'[ ,/]+', t) if part)]
        check(not lost, f'table {index} keeps every name it had at the base (lost {lost})')
        functions = changed_functions(against)
        missing = sorted(f for f in functions if not word(f, new))
        check(bool(functions) and not missing, f'table {index} names every overlay.py function the branch changes '
                                               f'({sorted(functions)}; missing {missing})')
    violations = section(agents, '### Known open violations')
    rows = [line for line in violations.splitlines() if re.match(r'\| [0-9, ]*\b5\b[0-9, ]* \|', line)]
    check(not any(OWN_SEED in r for r in rows), f'no § 5 open-violation row names {OWN_SEED}')
    check(not any('`truncate` without a handle' in r for r in rows),
          'no § 5 open-violation row still lists `truncate` without a handle')
    check('never cut' not in five, '§ 5 claims no file is never cut by the handle-less truncate')
    gap = [r for r in rows if WINDOW_SEED in r and 'reused' in r and 'injection' in r]
    check(len(gap) == 1, f'one § 5 row names the identity gap (ids reused), reproduced by injection, and {WINDOW_SEED}')
    seed_text = Path(WINDOW_SEED).read_text()
    paragraphs = [b for b in seed_text.split('\n\n') if 'identity check' in b]
    check(bool(paragraphs) and all('reproduced by injection' in b and 'not reproduced' not in b for b in paragraphs),
          f'{WINDOW_SEED}: the identity-gap paragraph says it reproduces by injection')
    sizes = [r for r in rows if OWNER in r and 'entry.size' in r]
    check(len(sizes) == 1, f'one § 5 row hands the old sizes (entry.size) to {OWNER}')
    window = [r for r in rows if WINDOW_SEED in r and '_serve' in r]
    check(len(window) == 1, f'one § 5 row names the open/_serve window and {WINDOW_SEED}')
    check('symbolic link' in gap[0] and 'refused' in gap[0],
          'the identity-gap row says a symbolic link or a directory that takes the id is refused')
    recording = [r for r in rows if RECORDING_SEED in r and '_vacate' in r and 'entry.size' in r]
    check(len(recording) == 1, f'one § 5 row names the recording keyed by a moved path (review cycle 2 F6) and '
                               f'{RECORDING_SEED}')
    check(Path(RECORDING_SEED).exists() and RECORDING_SEED in Path('ROADMAP.md').read_text()
          and re.search(r'^status: unshaped$', Path(RECORDING_SEED).read_text(), re.MULTILINE) is not None,
          f'{RECORDING_SEED} exists, is status: unshaped, and ROADMAP.md lists it')
    live = ['AGENTS.md', 'PLAN.md', 'ROADMAP.md', OVERLAY, REPLAY] + sorted(str(f) for f in Path('issues').glob('*.md'))
    for phrase in OVERCLAIMS:
        hits = [f for f in live if phrase in ' '.join(Path(f).read_text().split())]
        check(not hits, f'no live record claims {phrase!r} (got {hits})')
    check(OWN_SEED not in agents, f'AGENTS.md names {OWN_SEED} nowhere')
    stated = stated_unit_count()
    check(stated > UNIT_FLOOR, f'AGENTS.md states {stated} unit tests > {UNIT_FLOOR}')
    totals = re.findall(r'(\d+) tests \((\d+) `unit`, (\d+) `integration`\)', agents)
    check(len(totals) == 1 and int(totals[0][0]) == int(totals[0][1]) + int(totals[0][2]),
          f'the repository map\'s total is unit + integration ({totals})')
    plan = Path('PLAN.md').read_text()
    check(OWN_SEED not in plan, f'PLAN.md names {OWN_SEED} nowhere')
    check(re.search(r'Still open:\s+`truncate`', plan) is None, 'PLAN.md § Risks no longer lists truncate as open')
    check(OWNER in plan and WINDOW_SEED in plan, f'PLAN.md § Risks names {OWNER} and {WINDOW_SEED}')
    owner = Path(OWNER).read_text()
    check(re.search(r'^status: unshaped$', owner, re.MULTILINE) is not None, f'{OWNER} is status: unshaped')
    added = '\n'.join(line[1:] for line in git('diff', '-U0', against, '--', OWNER).splitlines()
                      if line.startswith('+') and not line.startswith('+++'))
    note = ' '.join(added.split())
    for token in ('entry.size', 'residency', 'release', 'remount', 'flush', 'fsync', 'quota counter', 'fe26962',
                  SLUG):
        check(token in note, f'{OWNER}: the branch\'s note carries {token!r}')
    check(re.search(r'(?:does not|never) heal|stays? (?:at|wrong)', note) is not None,
          f'{OWNER}: the note says which size does not heal')
    check(re.search(r'quota counters?[^.]*(?:unaffected|untouched|never)', note) is not None,
          f'{OWNER}: the note says the staged-tier quota counters are unaffected')
    check(Path(WINDOW_SEED).exists() and WINDOW_SEED in Path('ROADMAP.md').read_text(),
          f'{WINDOW_SEED} exists and ROADMAP.md lists it')
    touched = set(git('diff', '--name-only', against, 'HEAD').split())
    needed = {OVERLAY, REPLAY, 'AGENTS.md', 'PLAN.md', OWNER}
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
    for name in NEW_TESTS + (EARLIER_SIZE_TEST,):
        check(report.outcome(CLASSNAME, name) == ['passed'], f'{name} passed')
    stated = stated_unit_count()
    check(passed + report.count('skipped') == stated, f'AGENTS.md states {stated} unit tests; the run collected '
                                                      f'{passed} passed + {report.count("skipped")} skipped')


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
    token = f'truncate-gate-{uuid.uuid4().hex}'
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
    for name in ('audit-command', 'source-served-open-race', Path(RECORDING_SEED).stem):
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
