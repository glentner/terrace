# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the entry-identity-token cycle.

Usage (from the cycle worktree root, $WT, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I \
        spec/entry-identity-token/verify/identity_gate.py MODE [MODE ...]

The gate reads the git repository its working directory sits in, so it can be run by absolute path from the root
of another checkout of the branch (a scratch clone, say) and gates that checkout's committed state.

Modes:
    regressions  each named test is defined once and ends in assert_converged, and the refusal tests assert that no
                 watch is left open; all pass on this tree; each fix test fails on the baseline engine for its
                 named cause (`DID NOT RAISE`) and each pin passes there; every function and class test_replay.py
                 held at the base is unchanged; test_model.py, both conftest.py files, schema.sql and db.py are
                 byte-identical to the base and SCHEMA_VERSION is the base's; no skip or xfail is added. Static:
                 `truncate` is the base's code but for opening the watch and testing it in its check under the
                 source lock (`truncate_kept`); every purge goes through `_purge`, inside the caller's transaction,
                 at the base's purge sites, and records its id (`purges_watched`); the watch mutex is a leaf, held
                 only around the watch state and `_named`'s one catalog read, the watch opens before the entry is
                 named and is cleared with the read that names it (`_named`, or inside `_adopt`'s transaction),
                 `_ensure` and `_adopt` keep their signatures, and every lookup under the source lock and every
                 `_serve` is given the call's watch (`watch_leaf`); a user rename's catalog move stays under the
                 source lock (`catalog_moves_held`, cycle 5's property)
    mutants      each mutant (planted into a scratch copy of HEAD's src/) makes every test it names fail for its
                 named cause and leaves the rest of test_replay.py runnable; every new test is named by one
    records      AGENTS.md § 5, § 10, the high-blast-radius tables, § Known open violations and the counts; the
                 truncate, open, _serve and _lookup_again docstrings (and _ensure's, _named's and _adopt's for
                 stale or unscoped claims), Catalog.purge's, the source lock's and the
                 watch mutex's comments; root PLAN.md; the stress seed's dated line under its queue-row identity
                 bullet (Q5's home); test_replay.py's module docstring; outside spec/ only the seed and its
                 ROADMAP.md Seed line name the seed; no seed path in src/ or tests/; added lines in src/ and
                 tests/ are at most 120 characters and carry no spec ids; every seed's file:line cites of changed
                 files hold or are pinned, and every pin the branch writes holds its cites at the commit it
                 names (`pins_true`, added in remediation)
    suite        CI=true: tools/lint.sh exits 0; pytest -n auto -m unit has 0 failed, 0 errors, passed > 530 and
                 == AGENTS.md's stated count, the engine's share == its stated count, with test_model and every
                 named test passing; no .hypothesis/ is left in the tree
    seeds        test_model passes for each --hypothesis-seed in --seeds=A-B (default 1-20), without CI, two
                 scratch trees built from HEAD, one worker each, no .hypothesis/ before any run
    ledger       every deferral phrase in TECH.md's phase bodies names an issues/ seed that ROADMAP.md lists
    prepush      clean tree, suite, records, then no home-directory path in the branch diff or messages
    ci           the push run for the pushed commit (--sha, default origin/<branch>; later commits may touch only
                 spec/): all four jobs concluded success, Linux passed == the stated count with no skip, macOS
                 skipped only tests/fuse naming libfuse. --run-json/--log-dir read a saved `gh run view --json`
                 record and job logs (<job id>.log or <job name>.log) instead

"The baseline engine" is `src/` at BASELINE (the GOAL's tree, where it counted 530), extracted with `git archive`
into scratch and put first on PYTHONPATH; each run checks which sosfs it imported. "The base" for file comparisons
is the merge base with origin/main (the same commit: the shaping commits change neither src/ nor tests/); "the
plan commit" (the commit that added this cycle's PLAN.md) is the base for which seeds the build touched. Counts
come from pytest's JUnit XML, never from the exit code. Every pytest run has HYPOTHESIS_STORAGE_DIRECTORY in its
own scratch directory and runs with -p no:cacheprovider, so it leaves neither .hypothesis/ nor .pytest_cache/ in
the tree. Scratch and a copy of this run's output go under .local/identity-gate/ (gitignored, survives a reboot).

Footprint: one subprocess at a time, except `seeds`, which runs two pytest processes at once (each its own scratch
tree, environment and process group, killed as a group on the first red or a per-seed timeout). `suite` and
`prepush` run pytest with -n auto (one worker per core, about 1.5 GB peak on a 10-core machine); every other pytest
run is serial and under 500 MB. The new tests inject their interleaving on one helper thread and bound every wait
at 10 s, so a mutant that deadlocks them fails in seconds. Measured on the planning prototype (2026-11-09, a
scratch clone: a plan commit, then a build commit of the prototype's working tree, on 5893e1f): `regressions` 4 s
(one run of test_replay.py here, one on the baseline, about 2 s each), `mutants` 17 s (ten runs), `records` 1 s,
`suite` 30 s, `seeds` 293 s (twenty seeds, two lanes); see TECH.md.
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


SLUG = 'entry-identity-token'
BASELINE = 'c7d2049'          # the GOAL's tree and the merge base; 5893e1f's src/ and tests/ equal it
UNIT_FLOOR = 530              # strictly more than this pass
MODEL = 'tests/engine/test_model.py'
MODEL_ID = ('tests.engine.test_model.TestOverlayModel', 'runTest')       # JUnit (classname, name)
REPLAY = 'tests/engine/test_replay.py'
OVERLAY = 'src/sosfs/engine/overlay.py'
CATALOG = 'src/sosfs/state/catalog.py'
SCHEMA = 'src/sosfs/state/schema.sql'
DB = 'src/sosfs/state/db.py'
FROZEN = (MODEL, 'tests/engine/conftest.py', 'tests/conftest.py', SCHEMA, DB)     # byte-identical to the base
OWN_SEED = f'issues/{SLUG}.md'                     # retired by `code terrace roadmap` after landing
STRESS_SEED = 'issues/freethreaded-concurrency-stress.md'               # Q5's home, and the stage-served window's
LEDGER_SEEDS = ('freethreaded-concurrency-stress',)
ENGINE = 'OverlayEngine'
WATCH_CLASS = 'PurgeWatch'
WATCH_MUTEX = 'self._watches_mutex'                # the engine's; a watch holds it as `self._mutex`
# Where the base purged (OverlayEngine method -> number of `self.catalog.purge` calls). HEAD calls `self._purge`
# at exactly these, and `catalog.purge` only inside `_purge`.
PURGE_SITES = {'_remove': 1, '_execute_namespace_locked': 2, '_vacate': 1}
# Every call to these passes the call's watch as its second argument, from exactly these callers.
WATCHED_CALLS = {'_lookup_again': {'open': 1, '_serve': 1}, '_serve': {'truncate': 1, 'open': 1}}
# The only calls a body holding the watch mutex may make: set and list operations on the watch state.
LEAF_CALLS = frozenset({'append', 'remove', 'add', 'clear', 'discard'})
LOCKED_OUT = ('self.db', 'self.catalog', 'self.journal', 'self.locks', 'self._source_lock', 'self._handles_mutex',
              'self._ensure', 'self.transfers', 'self.tiers', 'self.registry')
# Words that the fix makes false wherever they stood (§ 5, § 10, the engine docstrings it touches, the module
# docstring, PLAN.md and the violations table), and the words of the naming loop this design replaced. Checked on
# whitespace-flattened text.
STALE = ('cannot tell the entry from a file that', 'only a schema change', 'short of the reused id',
         'short of a file that took', 'Closing it needs an identity token', 'reused id is in',
         'path, id and kind only', 'only by path, id and kind', 'path, id and kind, raising',
         'names it again', 'still naming', '`_name`', 'names the entry again')
# Q5: the lookups by entry id after a wait that no watch covers, named in the stress seed's dated line.
Q5_NAMES = ('_handles', 'Handle.entry_id', '_mark_handle_dirty', '_record', 'getattr', '_commit_hop', 'evict_copy',
            'commit_row', 'flush', 'dirty_copies')
# The names both high-blast-radius tables carry, by the file whose rows carry them.
TABLE_NAMES = {'overlay.py': ('_ensure', '_named', '_adopt', '_watch', '_purge'), 'catalog.py': ('purge',)}

# Tests the build writes into tests/engine/test_replay.py (names fixed here, PLAN § 6).
SOURCE_CUT = 'test_truncate_without_a_handle_refuses_a_source_served_file_that_took_the_entry_id'           # T1
STAGED_CUT = 'test_truncate_without_a_handle_refuses_a_staged_file_that_took_the_entry_id'                  # T2
SOURCE_OPEN = 'test_open_refuses_a_source_served_file_that_took_the_entry_id'                               # T3
PROMOTE = 'test_open_that_promotes_refuses_a_file_that_took_the_entry_id'                                   # T4
CUT_PURGED = 'test_truncate_without_a_handle_cuts_when_another_entry_is_purged_during_the_wait'             # T5
CUT_CREATED = 'test_truncate_without_a_handle_cuts_when_another_file_is_created_during_the_wait'            # T6
OPEN_PURGED = 'test_open_of_a_source_served_file_opens_when_another_entry_is_purged_during_the_wait'        # T7
OPEN_CREATED = 'test_open_of_a_source_served_file_opens_when_another_file_is_created_during_the_wait'       # T8
PROMOTE_PURGED = 'test_open_that_promotes_promotes_when_another_entry_is_purged_during_the_wait'            # T9
PROMOTE_CREATED = 'test_open_that_promotes_promotes_when_another_file_is_created_during_the_wait'           # T10
BEFORE_FIRST = 'test_truncate_without_a_handle_names_a_file_that_took_a_purged_id_before_its_first_lookup'  # T11
FIRST_LOOKUP = 'test_truncate_without_a_handle_refuses_once_its_entry_is_purged_after_its_first_lookup'     # T12
NO_SOURCE = 'test_truncate_without_a_handle_refuses_a_file_that_took_the_id_of_an_entry_removed_with_no_source_copy'
RIGHT_AFTER = 'test_truncate_without_a_handle_refuses_a_file_that_took_its_id_right_after_its_first_lookup'  # T14
ADOPTED = 'test_truncate_without_a_handle_cuts_a_file_it_adopts_with_an_id_another_purge_freed'            # T15
OPEN_RIGHT_AFTER = 'test_open_refuses_a_source_served_file_that_took_its_id_right_after_its_first_lookup'     # T16
PROMOTE_RIGHT_AFTER = 'test_open_that_promotes_refuses_a_file_that_took_its_id_right_after_its_first_lookup'  # T17
COMMITTED_FIRST = 'test_truncate_without_a_handle_is_not_refused_by_a_purge_that_committed_before_it_named_its_file'
CUTS = (SOURCE_CUT, STAGED_CUT)                    # the cut refuses (R1)
OPENS = (SOURCE_OPEN, OPEN_RIGHT_AFTER)            # a source-served open refuses (R2)
PROMOTES = (PROMOTE, PROMOTE_RIGHT_AFTER)          # an open that promotes off the source refuses (R3)
REFUSALS = CUTS + OPENS + PROMOTES
OTHERS = (CUT_PURGED, CUT_CREATED, OPEN_PURGED, OPEN_CREATED, PROMOTE_PURGED, PROMOTE_CREATED)     # R4
FIX_TESTS = REFUSALS + (FIRST_LOOKUP, NO_SOURCE, RIGHT_AFTER)                                     # T13 is NO_SOURCE
PIN_TESTS = OTHERS + (BEFORE_FIRST, ADOPTED, COMMITTED_FIRST)      # pass on the baseline; M3, M4, M10, M13 bite
NEW_TESTS = FIX_TESTS + PIN_TESTS
WATCH_CLEARED = FIX_TESTS                          # each asserts `engine._watches == []` after its refusal
# Why each fix test fails on the baseline engine (measured by the planner): the lookup under the lock passes the
# file that took the id, so the call cuts, opens or promotes it and `raises(FileNotFoundError)` sees nothing.
NOT_RAISED = r'DID NOT RAISE'
BASELINE_CAUSE = dict.fromkeys(FIX_TESTS, NOT_RAISED)
# A pin killed by a refusal it should not meet: the call raises ENOENT out of the test (measured: "FileNotFoundError:
# [Errno 2] No such file or directory: '/data/big.bin'", '/data/s.bin' for a promotion, '/deep/f.bin' for T15).
REFUSED = r"FileNotFoundError: \[Errno 2\] No such file or directory: '/(?:data|deep)/"

# (id, ((file, exact text in the built tree, replacement), ...), tests that must fail, why). Anchors are the built
# tree's own text; a mutant whose anchor is not found exactly once is red, so a build that reshapes a guarded line
# re-anchors it here in the same commit (PLAN § 6). `why` is the regex each named test's failure must match (a dict:
# one per test), so a kill counts only through the assertion that guards the decision. Tests a mutant kills beyond
# the ones it names (M3 also kills replay-during-the-wait tests from earlier cycles) are printed, not red.
# M1 and M6 attack the two checks under the source lock (`_lookup_again`, which open and a promotion use, and the
# cut's own, which stays inline so cycle 5's tests that stop at `_lookup_again` still stop at the promotion's);
# M2-M3 the watch's record and its scope; M4, M5, M10 and M11-M12 the naming (the clear made with the read that names
# the entry, for a cataloged entry and for an adopted one, and never again after it, by the cut, `open` or a
# promotion); M7-M8 the watch each `_lookup_again` site is given; M9 the purge site that runs with no replay; M13 the
# record made before the purge commits.
WATCHED = " or entry.id in watch"
LOOKUP_GUARD = ("        if current is None or current.id != entry.id or current.kind != 'file' or entry.id in watch:\n"
                "            raise FileNotFoundError(errno.ENOENT, 'No such file or directory', entry.path)\n")
CUT_GUARD = ("                    if current is None or current.id != entry.id or current.kind != 'file'"
             " or entry.id in watch:\n"
             "                        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)\n")
RECORDING = '                    watch.add(entry_id)\n'
MEMBERSHIP = '            return entry_id in self._ids\n'
NAMED_READ = ('        with self._watches_mutex:\n'
              '            watch.clear()\n'
              '            return self.catalog.get(path)\n')
CUT_NAMING = ('                entry = self._ensure(path)\n'
              '                if entry is None:\n'
              "                    raise FileNotFoundError(errno.ENOENT, 'No such file or directory', path)\n"
              "                if entry.kind != 'file':\n")
LATE_CLEAR = ('                self._naming.watch = None\n'
              '                entry = self._ensure(path)\n'
              '                with self._watches_mutex:\n'
              '                    watch.clear()\n')
ADOPT_CLEAR = ("                if (watch := getattr(self._naming, 'watch', None)) is not None:\n"
               '                    with self._watches_mutex:\n'
               '                        watch.clear()\n')
FRESH = f'{WATCH_CLASS}({WATCH_MUTEX})'
OPEN_LOOKUP = ('                current = self._lookup_again(entry, watch) if on_source else entry\n'
               '                fd = self.tiers[serving].open_direct(')
PROMOTE_LOOKUP = ('                current = self._lookup_again(entry, watch) if on_source else entry\n'
                  '                self.transfers.copy(')
REMOVE_PURGE = '                self._purge(path)\n\n    def _purge_source('
OPEN_NAMING = ('        with self.locks.hold(path), self._watch() as watch:\n'
               '            entry = self._ensure(path)\n'
               '            if entry is None:\n')
OPEN_LATE_CLEAR = ('            entry = self._ensure(path)\n'
                   '            with self._watches_mutex:\n'
                   '                watch.clear()\n')
PROMOTE_DECIDED = '            on_source = fastest.tier == self.source.name\n'
LATE_RECORD = ('                purged = self.catalog.purge(path)\n'
               '        if not (on_source and not replaced) and purged is not None:\n'
               '            with self._watches_mutex:\n'
               '                for watch in self._watches:\n'
               '                    watch.add(purged)\n\n    def _purge_source(')
REFUSED_LATE = r"FileNotFoundError\(2, 'No such file or directory'"
MUTANTS = (
    ('M1 _lookup_again (open\'s and a promotion\'s lookup under the lock) ignores the watch',
     ((OVERLAY, LOOKUP_GUARD, LOOKUP_GUARD.replace(WATCHED, '')),), OPENS + PROMOTES, NOT_RAISED),
    ('M2 a purge records nothing in the open watches',
     ((OVERLAY, RECORDING, '                    pass\n'),), FIX_TESTS, NOT_RAISED),
    ('M3 the watch matches any purge, not the entry\'s',
     ((OVERLAY, MEMBERSHIP, '            return bool(self._ids)\n'),), OTHERS, REFUSED),
    ('M4 the read that names a cataloged entry leaves the watch uncleared',
     ((OVERLAY, NAMED_READ, NAMED_READ.replace('            watch.clear()\n', '')),), (BEFORE_FIRST,), REFUSED),
    ('M5 the cut clears its watch after its first lookup returns, not with the read',
     ((OVERLAY, CUT_NAMING, CUT_NAMING.replace('                entry = self._ensure(path)\n', LATE_CLEAR)),),
     (FIRST_LOOKUP, RIGHT_AFTER), NOT_RAISED),
    ('M6 the cut\'s own check under the source lock ignores the watch',
     ((OVERLAY, CUT_GUARD, CUT_GUARD.replace(WATCHED, '')),), CUTS + (FIRST_LOOKUP, NO_SOURCE, RIGHT_AFTER),
     NOT_RAISED),
    ('M7 a source-served open looks again with a watch of its own',
     ((OVERLAY, OPEN_LOOKUP, OPEN_LOOKUP.replace('_lookup_again(entry, watch)', f'_lookup_again(entry, {FRESH})')),),
     OPENS, NOT_RAISED),
    ('M8 a promotion off the source looks again with a watch of its own',
     ((OVERLAY, PROMOTE_LOOKUP,
       PROMOTE_LOOKUP.replace('_lookup_again(entry, watch)', f'_lookup_again(entry, {FRESH})')),),
     PROMOTES, NOT_RAISED),
    ('M9 a removal with no source copy purges without recording',
     ((OVERLAY, REMOVE_PURGE, REMOVE_PURGE.replace('self._purge(path)', 'self.catalog.purge(path)')),),
     (NO_SOURCE,), NOT_RAISED),
    ('M10 an adoption leaves the watch uncleared',
     ((OVERLAY, ADOPT_CLEAR, ''),), (ADOPTED,), REFUSED),
    ('M11 open clears its watch after its first lookup returns, not with the read',
     ((OVERLAY, OPEN_NAMING, OPEN_NAMING.replace('            entry = self._ensure(path)\n', OPEN_LATE_CLEAR)),),
     (OPEN_RIGHT_AFTER, PROMOTE_RIGHT_AFTER), NOT_RAISED),
    ('M12 a promotion off the source clears the watch before its lookup under the lock',
     ((OVERLAY, PROMOTE_DECIDED, PROMOTE_DECIDED + '            with self._watches_mutex:\n'
                                                   '                watch.clear()\n'),),
     (PROMOTE_RIGHT_AFTER,), NOT_RAISED),
    ('M13 a removal with no source copy records its purge after its transaction commits',
     ((OVERLAY, REMOVE_PURGE, LATE_RECORD),), (COMMITTED_FIRST,), REFUSED_LATE),
)
# Mutants not run here (E1 and E2 were run in planning and kill nothing in test_replay.py):
# E1 `_vacate`'s purge left outside a transaction of its own: guarded statically (`purges_watched`). Autocommit
#    commits the DELETE before the record, so a thread can run between the two, adopt or create a file that takes
#    the id and name it, and the late record then refuses that file: a false refusal, the class M13 runs for
#    `_remove`. The second review showed the window is reachable (2026-11-09); the tombstone `_vacate` purges is
#    never a named entry, so no test here reaches it, and the static check is the guard.
# E2 the rename-tombstone purge in `_execute_namespace_locked` unrecorded: the tombstone it purges is a rename's
#    old path, which no call can have named (a lookup masks a tombstone), so no watch can be waiting on its id.
#    `purges_watched` guards that site statically.
# E3 recording after COMMIT instead of before it: guarded statically (`purges_watched` pins the record inside the
#    caller's transaction), and run as M13 for `_remove`'s purge. A thread placed between the commit and the record
#    adopts a file that takes the freed id and names it, and the late record refuses it (the second review's probe,
#    now the test COMMITTED_FIRST).
# E4 `current.kind != 'file'` dropped from both checks under the source lock: every non-file that reaches them
#    has a different id or an id the watch holds, so the id check or the watch refuses first (planning: test_replay
#    passes whole).
# Argued, not testable: that `_named` reads and clears in one hold of the watch mutex. No deterministic test can run
# a thread between the two without deadlocking on the mutex; `watch_leaf` pins the shape statically.


DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|\bdefer(?:s|red|ring|ral|rals)?\b|'
                      r'split (?:out|from)|stays? open|remains? open|left open|a later cycle|out of scope|owned by|'
                      r'\bhome(?:d|s)?\b(?![- ](?:director|path))', re.IGNORECASE)
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')    # written so this line cannot match itself
TOLERANCE = re.compile(r'\bxfail\b|\bskipif\b|mark\.skip\b|pytest\.skip\(', re.IGNORECASE)
SPEC_ID = re.compile(r'\b(?:R[1-9]|P[1-9]|Q[1-9])\b')
CITE = re.compile(r'(?<![\w/.-])((?:[\w-]+/)*([\w-]+\.py)):(\d+)(?:-(\d+))?')
PIN = re.compile(r'\b(?:holds?|held|is|are|pinned)\s+at\s+`([0-9a-f]{7,40})`')
PURGED_ID = re.compile(r"\bid\b[^.;]{0,100}\bpurge|\bpurge[^.;]{0,100}\bid\b")
WATCH_WORD = re.compile(r'\bwatch(?:es)?\b')
STAGE_SCOPED = re.compile(r'\bstage\b[^.]{0,120}without the source lock')
# An unscoped claim that a call never acts on a file that took the id, or always refuses one, is false for the
# stage-served `open` window (and a sentence that bounds no window claims it for all of them). A sentence that
# makes one passes only if it names a window: a stage, a source-served copy, an exception, or a bound on "every
# window". Case-insensitive, any tense of "take".
NEVER_TOOK = re.compile(r'\bnever\b[^.]{0,80}?\b(?:open(?:s|ed)?|promot(?:e|es|ed)|act(?:s|ed)?|cuts?)\b[^.]{0,80}?'
                        r'\b(?:took|taken|takes|taking)\b', re.IGNORECASE)
ALWAYS = re.compile(r'\balways refuses\b|\bin every window\b', re.IGNORECASE)
SCOPED = re.compile(r'\b(?:stage|unless|except|short of|outside|source-served|off the source)\b'
                    r'|\bin every window (?:after|from|between|once|of)\b', re.IGNORECASE)
ONLY_PURGED = re.compile(r'only (?:when|if)[^.]{0,80}purged')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
ROOT = Path.cwd()
LOCAL = Path('.local/identity-gate')
RUN_TIMEOUT = 300                 # every run but a seed's; the slowest, the suite, takes about 60 s
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


def run(argv: list[str], env: dict[str, str] | None = None, quiet: bool = False, cwd: str | None = None,
        drop: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    say(f'  run   {" ".join(argv)}')
    saved = dict(os.environ)                 # a child's environment starts from a saved copy, never a cleared one
    inherited = {k: v for k, v in saved.items() if k != 'PYTHONPATH' and k not in drop}
    # Its own process group, killed whole on a timeout: a mutant that leaves a lock held, or a test whose helper
    # thread waits on a lock the test holds, hangs rather than fails, and must go red, not stall the gate.
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


# -- source structure ----------------------------------------------------------------------------------------

def parents_of(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    found = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            found[child] = node
    return found


def lineage(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[tuple[ast.AST, ast.AST]]:
    """(ancestor, the child of it on the way down to `node`), innermost first."""
    out, child = [], node
    while child in parents:
        out.append((parents[child], child))
        child = parents[child]
    return out


def owner(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> tuple[str, str]:
    """(class, function) that lexically holds `node`, innermost function first; '' for none."""
    function = next((a.name for a, _ in lineage(node, parents) if isinstance(a, ast.FunctionDef)), '')
    klass = next((a.name for a, _ in lineage(node, parents) if isinstance(a, ast.ClassDef)), '')
    return klass, function


def held_by(node: ast.AST, parents: dict[ast.AST, ast.AST], context: str) -> bool:
    """`node` sits in the body of a `with` one of whose items is `context` (unparsed), within its function."""
    for ancestor, child in lineage(node, parents):
        if isinstance(ancestor, ast.FunctionDef):
            return False
        if isinstance(ancestor, ast.With) and child in ancestor.body \
                and any(ast.unparse(item.context_expr) == context for item in ancestor.items):
            return True
    return False


def method(text: str | ast.Module, name: str, klass: str = ENGINE) -> ast.FunctionDef | None:
    """
    Method `name` of class `klass` in `text` (source, or a parsed module whose nodes the caller's parent map
    covers), or None when the class does not define it once.
    """
    for node in (ast.parse(text) if isinstance(text, str) else text).body:
        if isinstance(node, ast.ClassDef) and node.name == klass:
            found = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == name]
            return found[0] if len(found) == 1 else None
    return None


def self_calls(tree: ast.AST, name: str) -> list[ast.Call]:
    """Every call `self.<name>(...)`."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == name and isinstance(n.func.value, ast.Name) and n.func.value.id == 'self']


def catalog_calls(tree: ast.AST, name: str) -> list[ast.Call]:
    """Every call `<...>.catalog.<name>(...)`."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == name and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == 'catalog']


def body_of(node: ast.FunctionDef) -> list[ast.stmt]:
    """The function's statements without its docstring."""
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


# -- regressions ---------------------------------------------------------------------------------------------

def top_level(text: str) -> dict[str, str]:
    """Each top-level function and class of a module, by name, as its unparsed source."""
    return {n.name: ast.unparse(n) for n in ast.parse(text).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


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
    for name in WATCH_CLEARED:
        asserted = any(isinstance(n, ast.Assert) and ast.unparse(n.test) == 'engine._watches == []'
                       for n in ast.walk(by_name[name]))
        check(asserted, f'{name} asserts that no watch is left open (`engine._watches == []`)')


def unchanged_code() -> None:
    against = base()
    old = top_level(git('show', f'{against}:{REPLAY}'))
    new = top_level(Path(REPLAY).read_text())
    changed = sorted(name for name, source in old.items() if new.get(name) != source)
    check(not changed, f'every function and class {REPLAY} held at the base is still defined, unchanged '
                       f'({len(old)} checked; changed {changed})')


class DropWatch(ast.NodeTransformer):
    """`self._serve(x, watch)` -> `self._serve(x)`: the one argument the fix adds to the cut's statements."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == '_serve' and len(node.args) == 2 \
                and isinstance(node.args[1], ast.Name) and node.args[1].id == 'watch':
            node.args = node.args[:1]
        return node


def truncate_kept() -> None:
    """
    Geoffrey's signed-off property: `truncate` is the base's code but for its lookup prologue. Its handle branch and
    the statements before it are the base's; the handle-less branch takes the path lock and opens the watch in one
    `with`, names its entry and refuses exactly as the base does (`_ensure` names it with the watch open), and
    under the source lock keeps the base's
    inline check with only ` or entry.id in watch` appended (inline, not `_lookup_again`, so cycle 5's tests that stop
    at `_lookup_again` still stop at the promotion's lookup); from `serving = self._serve(` through the recording, its
    statements are the base's with the `_serve` watch argument normalized away.
    """
    before = method(git('show', f'{base()}:{OVERLAY}'), 'truncate')
    after = method(Path(OVERLAY).read_text(), 'truncate')
    check(before is not None and after is not None, 'OverlayEngine.truncate is defined once, at the base and here')
    old, new = body_of(before), body_of(after)
    branch = lambda body: [i for i, s in enumerate(body) if isinstance(s, ast.If)            # noqa: E731
                           and ast.unparse(s.test) == 'handle is not None']
    check(len(branch(old)) == len(branch(new)) == 1, 'truncate branches once on `handle is not None`')
    i, j = branch(old)[0], branch(new)[0]
    check([ast.unparse(s) for s in old[:i]] == [ast.unparse(s) for s in new[:j]] and old[i + 1:] == [] == new[j + 1:],
          'truncate\'s statements around its handle branch are the base\'s')
    check([ast.unparse(s) for s in old[i].body] == [ast.unparse(s) for s in new[j].body],
          'truncate\'s handle branch is the base\'s')
    check(len(old[i].orelse) == len(new[j].orelse) == 1 and all(isinstance(b.orelse[0], ast.With)
                                                                for b in (old[i], new[j])),
          'the handle-less branch is one `with` statement, at the base and here')
    held_old, held_new = old[i].orelse[0], new[j].orelse[0]
    items = [ast.unparse(item) for item in held_new.items]
    check(items == ['self.locks.hold(path)', 'self._watch() as watch'],
          f'the handle-less branch takes the path lock and opens the watch in one `with` ({items})')
    check(ast.unparse(held_old.body[0]) == ast.unparse(held_new.body[0]) == 'entry = self._ensure(path)',
          'the handle-less branch names its entry through `_ensure(path)`, as the base does')
    check([ast.unparse(s) for s in held_old.body[1:-1]] == [ast.unparse(s) for s in held_new.body[1:-1]],
          'the refusals between the naming and the source lock are the base\'s')
    lock_old, lock_new = held_old.body[-1], held_new.body[-1]
    check(all(isinstance(s, ast.With) and [ast.unparse(x) for x in s.items] == ['self._source_lock']
              for s in (lock_old, lock_new)), 'the cut ends in `with self._source_lock:`, at the base and here')
    starts = lambda body: [k for k, s in enumerate(body) if isinstance(s, ast.Assign)          # noqa: E731
                           and ast.unparse(s).startswith('serving = self._serve(')]
    check(len(starts(lock_old.body)) == len(starts(lock_new.body)) == 1, 'the cut decides `serving` once')
    k_old, k_new = starts(lock_old.body)[0], starts(lock_new.body)[0]
    guard = "current is None or current.id != entry.id or current.kind != 'file'"
    base_prologue = [ast.unparse(s) for s in lock_old.body[:k_old]]
    check(len(base_prologue) == 2 and base_prologue[0] == 'current = self.catalog.get(path)'
          and base_prologue[1].startswith(f'if {guard}:'), 'the base cut looks its entry up inline under the lock')
    prologue = [ast.unparse(s) for s in lock_new.body[:k_new]]
    wanted = [base_prologue[0],             # re-unparsed, so the comparison is of trees, not of spellings
              ast.unparse(ast.parse(base_prologue[1].replace(f'if {guard}:', f'if {guard}{WATCHED}:', 1)))]
    check(prologue == wanted, 'under the source lock the cut\'s own inline check is the base\'s with only '
                              f'`{WATCHED.strip()}` appended to its condition ({prologue})')
    tail_old = [ast.unparse(s) for s in lock_old.body[k_old:]]
    tail_new = [ast.unparse(DropWatch().visit(s)) for s in lock_new.body[k_new:]]
    check(tail_old == tail_new, 'from `serving = self._serve(` through its recording the cut is the base\'s code, '
                                'the `_serve` watch argument aside')


def catalog_moves_held() -> None:
    """Every `catalog.rename` call in src/ is inside `rename`'s `with self._source_lock:` block (cycle 5)."""
    calls = []
    for path in sorted(Path('src').rglob('*.py')):
        for node in catalog_calls(ast.parse(path.read_text()), 'rename'):
            calls.append((str(path), node.lineno))
    check(len(calls) == 1 and calls[0][0] == OVERLAY, f'src/ calls catalog.rename once, in overlay.py ({calls})')
    setters = [(str(f), n.name) for f in sorted(Path('src').rglob('*.py'))
               for n in ast.walk(ast.parse(f.read_text())) if isinstance(n, ast.FunctionDef)
               and re.search(r'SET\s+path\b', ast.get_source_segment(f.read_text(), n) or '')
               and not any(isinstance(c, ast.FunctionDef) and c is not n for c in ast.walk(n))]
    check(setters == [(CATALOG, 'rename')],
          f'Catalog.rename is the only function in src/ whose SQL sets an entry\'s path ({setters})')
    fields = re.search(r'META_FIELDS[^=]*=\s*frozenset\(\s*\{([^}]*)\}', Path(CATALOG).read_text())
    check(fields is not None and "'path'" not in fields.group(1) and "'parent'" not in fields.group(1),
          'META_FIELDS lets no update_meta call set an entry\'s path or parent')
    rename = method(Path(OVERLAY).read_text(), 'rename')
    check(rename is not None, 'OverlayEngine.rename is defined once')
    held = False
    for node in ast.walk(rename):
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


def schema_version(text: str) -> object:
    for node in ast.parse(text).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == 'SCHEMA_VERSION' and isinstance(node.value, ast.Constant):
            return node.value.value
    return None


def frozen_files() -> None:
    """The model, both conftests and the schema (schema.sql, db.py) are the base's; no tolerance is added."""
    against = base()
    for name in FROZEN:
        before = subprocess.run(['git', 'show', f'{against}:{name}'], capture_output=True).stdout
        after = Path(name).read_bytes() if Path(name).exists() else b''
        check(before == after, f'{name} is byte-identical to the base ({against[:12]})')
    old, new = schema_version(git('show', f'{against}:{DB}')), schema_version(Path(DB).read_text())
    check(old is not None and old == new, f'SCHEMA_VERSION is the base\'s ({old} -> {new}): no schema change')
    added = git('diff', '-U0', against, '--', 'tests/').splitlines()
    hits = [line for line in added if line.startswith('+') and not line.startswith('+++') and TOLERANCE.search(line)]
    check(not hits, f'no skip or xfail added under tests/ (got {hits[:3]})')


def purges_watched() -> None:
    """
    Every entry purge goes through `OverlayEngine._purge`, which joins its caller's transaction, at the base's purge
    sites, and records the id `Catalog.purge` returns in every open watch while holding the watch mutex.
    """
    against = base()
    text = Path(OVERLAY).read_text()
    tree = ast.parse(text)
    parents = parents_of(tree)
    old_tree = ast.parse(git('show', f'{against}:{OVERLAY}'))
    old_parents = parents_of(old_tree)
    old_sites: dict[str, int] = {}
    for call in catalog_calls(old_tree, 'purge'):
        klass, function = owner(call, old_parents)
        key = function if klass == ENGINE else f'{klass}.{function}'
        old_sites[key] = old_sites.get(key, 0) + 1
    check(old_sites == PURGE_SITES, f'the base purged at {PURGE_SITES} ({old_sites})')
    found = []
    for path in sorted(Path('src').rglob('*.py')):
        module = ast.parse(path.read_text())
        found += [(str(path), owner(c, parents_of(module)), c) for c in catalog_calls(module, 'purge')]
    check(len(found) == 1 and found[0][0] == OVERLAY and found[0][1] == (ENGINE, '_purge'),
          f'src/ calls catalog.purge once, in OverlayEngine._purge ({[(f, o) for f, o, _ in found]})')
    call = catalog_calls(tree, 'purge')[0]                  # the same call, in this function's own parse
    assign = parents.get(call)
    check(isinstance(assign, ast.Assign) and len(assign.targets) == 1 and isinstance(assign.targets[0], ast.Name),
          '_purge keeps the id Catalog.purge returns')
    purged = assign.targets[0].id
    purge = method(tree, '_purge')
    recorded = [n for n in ast.walk(purge) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == 'add' and [ast.unparse(a) for a in n.args] == [purged]
                and held_by(n, parents, WATCH_MUTEX)
                and any(isinstance(a, ast.For) and ast.unparse(a.iter) == 'self._watches'
                        for a, _ in lineage(n, parents))]
    check(len(recorded) == 1, f'_purge adds `{purged}` to every open watch (`for ... in self._watches`) while '
                              f'holding the watch mutex')
    check(not any(isinstance(n, ast.Attribute) and n.attr in ('transaction', '_source_lock', 'commit')
                  for n in ast.walk(purge)), '_purge opens no transaction and takes no source lock: it joins its '
                                             'caller\'s')
    sites: dict[str, int] = {}
    for site in self_calls(tree, '_purge'):
        klass, function = owner(site, parents)
        key = function if klass == ENGINE else f'{klass}.{function}'
        sites[key] = sites.get(key, 0) + 1
        check(held_by(site, parents, 'self.db.transaction()'),
              f'{key}\'s _purge call (line {site.lineno}) sits inside `with self.db.transaction():`')
    check(sites == PURGE_SITES, f'_purge is called from exactly the base\'s purge sites ({sites})')
    sql = [(str(p), p.read_text()) for p in sorted(Path('src').rglob('*')) if p.suffix in ('.py', '.sql')]
    deletes = [(p, m.group(0)) for p, t in sql for m in re.finditer(r'DELETE\s+FROM\s+entry\b[^\n]*', t, re.I)]
    check(len(deletes) == 1 and deletes[0][0] == CATALOG and re.search(r'RETURNING\s+id\b', deletes[0][1]),
          f'src/ deletes entry rows once, in catalog.py, `RETURNING id` ({deletes})')
    replaces = [p for p, t in sql if re.search(r'(?:INSERT\s+OR\s+REPLACE|\bREPLACE)\s+INTO\s+entry\b', t, re.I)]
    check(not replaces, f'nothing in src/ replaces an entry row, which would delete one unwatched ({replaces})')


def watch_leaf() -> None:
    """
    The watch mutex is a plain `threading.Lock`, held only around operations on the watch state and, in `_named`
    alone, the one catalog read that names a call's entry; no source lock is taken inside a transaction. The watch
    opens before the entry is named, is the calling thread's naming watch while open, and closes in a `finally`. The
    read that names the entry clears the watch in the same hold (`_named`), or inside the insert's transaction for an
    adopted entry (`_adopt`). `_ensure` and `_adopt` keep the base's signatures (test helpers replace `_ensure` with a
    one-argument callable), and every lookup under the source lock and every `_serve` is given the call's watch.
    """
    text = Path(OVERLAY).read_text()
    tree = ast.parse(text)
    parents = parents_of(tree)
    old_tree = ast.parse(git('show', f'{base()}:{OVERLAY}'))
    made = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1
            and ast.unparse(n.targets[0]) == WATCH_MUTEX]
    check(len(made) == 1 and ast.unparse(made[0].value) == 'threading.Lock()' and owner(made[0], parents)
          == (ENGINE, '__init__'), f'{WATCH_MUTEX} is made once, in __init__, a threading.Lock() (not re-entrant)')
    local = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1
             and ast.unparse(n.targets[0]) == 'self._naming']
    check(len(local) == 1 and ast.unparse(local[0].value) == 'threading.local()' and owner(local[0], parents)
          == (ENGINE, '__init__'), 'self._naming is made once, in __init__, a threading.local() (each thread\'s watch)')
    holds = [n for n in ast.walk(tree) if isinstance(n, ast.With) and any(
        ast.unparse(i.context_expr) == WATCH_MUTEX
        or (ast.unparse(i.context_expr) == 'self._mutex' and owner(n, parents)[0] == WATCH_CLASS) for i in n.items)]
    where_held = sorted('.'.join(owner(h, parents)) for h in holds)
    check(len(holds) >= 5, f'the watch mutex is held in {len(holds)} places ({where_held})')
    reads = 0
    for hold in holds:
        klass, function = owner(hold, parents)
        where = f'{klass}.{function}'
        check(len(hold.items) == 1, f'{where}: the watch mutex is taken alone, with nothing else in its `with`')
        inner = [n for s in hold.body for n in ast.walk(s)]
        check(not any(isinstance(n, (ast.With, ast.AsyncWith, ast.Yield, ast.YieldFrom, ast.Await)) for n in inner),
              f'{where}: nothing is acquired or yielded while the watch mutex is held (line {hold.lineno})')
        calls = [n for n in inner if isinstance(n, ast.Call)]
        read = [c for c in calls if ast.unparse(c) == 'self.catalog.get(path)']
        if (klass, function) == (ENGINE, '_named'):
            check(len(read) == 1, f'{where}: the hold makes exactly one catalog read, `self.catalog.get(path)`')
            reads += 1
        bad = [ast.unparse(c.func) for c in calls if (klass, function) != (ENGINE, '_named') or c not in read
               if not isinstance(c.func, ast.Attribute) or c.func.attr not in LEAF_CALLS
               or ast.unparse(c.func.value).startswith(LOCKED_OUT)]
        check(not bad, f'{where}: the watch mutex guards only operations on the watch state '
                       f'(line {hold.lineno}; {bad})')
    check(reads == 1, '_named holds the watch mutex once, around its read and its clear')
    acquired = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ('acquire', 'release')
                and ast.unparse(n.value) in (WATCH_MUTEX, 'self._mutex')]
    check(not acquired, f'the watch mutex is taken only by `with` ({acquired})')
    for verb in ('add', 'clear'):
        found = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == verb and ast.unparse(n.func.value) == 'watch']
        check(bool(found) and all(held_by(n, parents, WATCH_MUTEX) for n in found),
              f'every `watch.{verb}` ({len(found)}) holds the watch mutex')
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        exprs = [ast.unparse(i.context_expr) for i in node.items]
        where = '.'.join(owner(node, parents))
        sourced = any('self._source_lock' in e for e in exprs)
        if 'self.db.transaction()' in exprs and sourced:
            check(not any('self._source_lock' in e for e in exprs[exprs.index('self.db.transaction()'):]),
                  f'{where}: no source lock is taken after a transaction in one `with` (line {node.lineno})')
        if sourced:
            check(not held_by(node, parents, 'self.db.transaction()'),
                  f'{where}: no source lock is taken inside `with self.db.transaction():` (line {node.lineno})')
    check(not any(held_by(c, parents, 'self.db.transaction()') for c in self_calls(tree, '_vacate')),
          'no _vacate (which takes the source lock and opens a transaction) runs inside a transaction')

    # The watch: opened, made the thread's naming watch, and both undone in a `finally`.
    opened = method(tree, '_watch')
    check(opened is not None and any(ast.unparse(d) == 'contextlib.contextmanager' for d in opened.decorator_list),
          '_watch is a context manager')
    statements = [ast.unparse(s) for s in body_of(opened)]
    tries = [s for s in body_of(opened) if isinstance(s, ast.Try)]
    check(len(tries) == 1 and "previous = getattr(self._naming, 'watch', None)" in statements
          and statements.index("previous = getattr(self._naming, 'watch', None)") < body_of(opened).index(tries[0]),
          '_watch saves the thread\'s previous naming watch before its `try`')
    inside = [ast.unparse(n) for s in tries[0].body for n in ast.walk(s)] if tries else []
    appended = [n for n in ast.walk(opened)
                if isinstance(n, ast.Call) and ast.unparse(n) == 'self._watches.append(watch)']
    check(len(appended) == 1 and any(appended[0] in list(ast.walk(s)) for s in tries[0].body)
          and 'self._naming.watch = watch' in inside,
          '_watch registers the watch and makes it the naming watch inside its `try`, so no exception leaks it')
    final = [ast.unparse(s) for s in tries[0].finalbody] if tries else []
    check(any(isinstance(n, ast.Yield) for s in tries[0].body for n in ast.walk(s)) and 'self._naming.watch = previous'
          in final and any('self._watches.remove(watch)' in s for s in final),
          f'_watch yields inside a `try` whose `finally` restores the previous naming watch and removes the watch '
          f'({final})')

    # The naming: the base's calls and signatures, the clear made with the read that names the entry.
    for name in ('_ensure', '_adopt'):
        old, new = method(old_tree, name), method(tree, name)
        check(old is not None and new is not None and ast.unparse(old.args) == ast.unparse(new.args),
              f'{name} keeps the base\'s signature ({ast.unparse(new.args) if new else None})')
    for name in ('truncate', 'open'):
        node = method(tree, name)
        check(node is not None, f'OverlayEngine.{name} is defined once')
        withs = [n for n in ast.walk(node) if isinstance(n, ast.With) and any(
            ast.unparse(i.context_expr) == 'self._watch()' for i in n.items)]
        check(len(withs) == 1, f'{name} opens one watch')
        ensures = self_calls(node, '_ensure')
        check(len(ensures) == 1 and ast.unparse(ensures[0]) == 'self._ensure(path)'
              and held_by(ensures[0], parents, 'self._watch()'),
              f'{name} names its entry once, by `self._ensure(path)` (one argument), inside the watch\'s `with`')
    ensure = method(tree, '_ensure')
    first = [ast.unparse(s) for s in body_of(ensure)][:2]
    check(first == ['path = normalize(path)', 'entry = self._named(path)'],
          f'_ensure\'s first read is `self._named(path)` ({first})')
    named_by = {}
    for call in self_calls(tree, '_named'):
        named_by[owner(call, parents)[1]] = named_by.get(owner(call, parents)[1], 0) + 1
        check(ast.unparse(call) == 'self._named(path)', f'{ast.unparse(call)} names by path alone')
    check(named_by == {'_ensure': 1, '_adopt': 1}, f'_named is called from _ensure and _adopt\'s race branch '
                                                   f'({named_by})')
    named = method(tree, '_named')
    check(named is not None and 'getattr(self._naming, \'watch\', None)' in ast.unparse(named),
          '_named reads the calling thread\'s naming watch')
    hold = [h for h in holds if owner(h, parents) == (ENGINE, '_named')]
    order = [ast.unparse(s) for s in hold[0].body] if len(hold) == 1 else []
    check(order == ['watch.clear()', 'return self.catalog.get(path)'],
          f'_named clears the watch and reads the entry in one hold of the watch mutex ({order})')
    adopt = method(tree, '_adopt')
    clears = [h for h in holds if owner(h, parents) == (ENGINE, '_adopt')]
    check(len(clears) == 1 and held_by(clears[0], parents, 'self.db.transaction()')
          and [ast.unparse(s) for s in clears[0].body] == ['watch.clear()'],
          '_adopt clears the naming watch once, holding the watch mutex, inside its insert\'s transaction')
    handlers = [h for h in ast.walk(adopt) if isinstance(h, ast.ExceptHandler)
                and 'IntegrityError' in ast.unparse(h.type or ast.Constant(None))]
    check(len(handlers) == 1 and any(ast.unparse(c) == 'self._named(path)' for c in ast.walk(handlers[0])
                                     if isinstance(c, ast.Call)),
          '_adopt\'s lost race reads the winner through `_named`, clearing the watch with that read')
    cleared: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'clear' \
                and ast.unparse(n.func.value) == 'watch':
            cleared[owner(n, parents)[1]] = cleared.get(owner(n, parents)[1], 0) + 1
    check(cleared == {'_named': 1, '_adopt': 1},
          f'`watch.clear()` occurs exactly once in _named and once in _adopt, never after a lookup ({cleared})')
    for name in ('_serve', '_lookup_again', '_held_path'):
        node = method(tree, name)
        naming = [ast.unparse(c) for c in ast.walk(node) if isinstance(c, ast.Call)
                  and ast.unparse(c.func) in ('self._ensure', 'self._named', 'self._adopt')] if node else ['missing']
        check(not naming, f'{name} never names an entry again ({naming})')
    named_body = [ast.unparse(s) for s in body_of(named)] if named is not None else []
    with_index = next((i for i, s in enumerate(body_of(named)) if isinstance(s, ast.With)), None) if named else None
    check('self.db.connection' in named_body and with_index is not None
          and named_body.index('self.db.connection') < with_index,
          '_named opens the thread\'s connection before it takes the watch mutex (no pragmas under the leaf)')
    for callee, callers in WATCHED_CALLS.items():
        found: dict[str, int] = {}
        for call in self_calls(tree, callee):
            klass, function = owner(call, parents)
            found[function] = found.get(function, 0) + 1
            check(len(call.args) == 2 and ast.unparse(call.args[1]) == 'watch',
                  f'{function}\'s {callee} call (line {call.lineno}) passes the call\'s watch')
        check(found == callers, f'{callee} is called from {callers} ({found})')


def mode_regressions() -> None:
    check(git('status', '--porcelain', '--', 'src', 'tests').strip() == '', 'src/ and tests/ are committed')
    regression_shape()
    unchanged_code()
    truncate_kept()
    catalog_moves_held()
    frozen_files()
    purges_watched()
    watch_leaf()
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
        extra = sorted(c.name for c in report.cases if c.outcome in ('failed', 'error') and c.name not in tests)
        if extra:
            say(f'  note  {label}: also kills {len(extra)} test(s) it does not name: {extra}')


# -- records -------------------------------------------------------------------------------------------------

def flat(text: str) -> str:
    return ' '.join(text.split())


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


def changed_methods(against: str) -> set[str]:
    """OverlayEngine methods at HEAD whose body holds a line the branch added or changed (not the watch class's)."""
    lines = set()
    for hunk in re.finditer(r'^@@ -\S+ \+(\d+)(?:,(\d+))? @@', git('diff', '-U0', against, '--', OVERLAY),
                            re.MULTILINE):
        start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
        lines |= set(range(start, start + max(count, 1)))
    found = set()
    for node in ast.parse(Path(OVERLAY).read_text()).body:
        if isinstance(node, ast.ClassDef) and node.name == ENGINE:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and lines & set(range(sub.lineno, sub.end_lineno + 1)):
                    found.add(sub.name)
    return found


def word(name: str, text: str) -> bool:
    return re.search(rf'(?<![\w.]){re.escape(name)}(?!\w)', text) is not None


def names_in_row(name: str, row: str) -> bool:
    """`name` is a backticked name in the table row (alone, in a `/`- or `,`-list, or as `<owner>.name`)."""
    parts = [p for token in re.findall(r'`([^`]+)`', row) for p in re.split(r'[^\w.]+', token) if p]
    return any(p == name or p.endswith(f'.{name}') for p in parts)


def unit_counts(text: str) -> dict[str, set[int]]:
    return {'pytest -m unit # N tests': set(int(n) for n in re.findall(r'pytest -m unit\s+# (\d+) tests', text)),
            '(N `unit`': set(int(n) for n in re.findall(r'\((\d+) `unit`', text)),
            'N deselected': set(int(n) for n in re.findall(r'(\d+) deselected', text)),
            'N unit tests': set(int(n) for n in re.findall(r'(\d+) unit tests', text))}


def stated_unit_count() -> int:
    by_site = unit_counts(Path('AGENTS.md').read_text())
    found = set().union(*by_site.values())
    check(len(found) == 1 and all(by_site.values()),
          f'AGENTS.md states one unit-test count, at each of its sites ({by_site})')
    return found.pop()


def engine_count(text: str) -> set[int]:
    return set(int(n) for n in re.findall(r'(\d+) engine tests', text))


def docstring(text: str, name: str, klass: str = ENGINE) -> str:
    node = method(text, name, klass)
    return flat(ast.get_docstring(node) or '') if node is not None else ''


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r'(?<=[.:;])\s+(?=[A-Z`(])', flat(text)) if s]


def truncate_doc_kept(against: str, doc: str) -> None:
    """Every sentence of the base truncate docstring stands, but the reused-id pair the fix makes false."""
    old = docstring(git('show', f'{against}:{OVERLAY}'), 'truncate')
    dropped = [s for s in sentences(old) if s.startswith('So does a non-file') or any(p in s for p in STALE)]
    check(len(dropped) == 2, f'the base truncate docstring holds the reused-id pair ({dropped})')
    lost = [s for s in sentences(old) if s not in dropped and s not in doc]
    check(not lost, f'the truncate docstring keeps every other sentence of the base\'s (lost {lost[:2]})')


def comment_above(text: str, marker: str, skip: str = '') -> str:
    """The comment block directly above the first line holding `marker` (lines starting with `skip` passed over)."""
    lines = text.splitlines()
    at = next((i for i, line in enumerate(lines) if marker in line), None)
    if at is None:
        return ''
    block = []
    for line in reversed(lines[:at]):
        stripped = line.strip()
        if skip and stripped.startswith(skip):
            continue
        if not stripped.startswith('#'):
            break
        block.append(stripped.lstrip('#').strip())
    return ' '.join(reversed(block))


def added_lines(against: str, *paths: str) -> list[tuple[str, str]]:
    """(file, text) for each line the branch adds under `paths`."""
    out, current = [], ''
    for line in git('diff', '-U0', against, '--', *paths, UNSHOWN).splitlines():
        if line.startswith('+++ '):
            current = line[6:] if line.startswith('+++ b/') else ''
        elif line.startswith('+') and current:
            out.append((current, line[1:]))
    return out


DATED = re.compile(r'^\s*(?:[-*]|\d+\.) \*?(?:Added |Corrected )?20\d\d-\d\d-\d\d')
STARTS_DATED = re.compile(r'^\s*(?:(?:[-*]|\d+\.) )?[*_(]*(?:added|corrected|recorded)?[^\n]{0,30}20\d\d-\d\d-\d\d',
                          re.IGNORECASE)


def units(text: str) -> list[list | None]:
    """[indent, kind, own lines] per list item or paragraph; None marks a blank line."""
    out: list[list | None] = []
    for line in text.splitlines():
        if not line.strip():
            out.append(None)
            continue
        item = re.match(r'^(\s*)(?:[-*]|\d+\.) ', line)
        if item:
            out.append([len(item.group(1)), 'item', [line]])
        elif out and out[-1] is not None:
            out[-1][2].append(line)
        else:
            out.append([len(line) - len(line.lstrip()), 'para', [line]])
    return out


def cite_blocks(text: str) -> list[str]:
    """
    One block per list item or paragraph: its own lines, the deeper-indented items nested under it, and, for a
    paragraph, a dated item that follows it directly. A pin ("holds at `<sha>`") counts for every cite in its
    block, so a dated line pins the item it sits under (and the items nested under it), never a sibling or a
    distant one; a pin in a nested item pins only that item and what nests under it.
    """
    found = units(text)
    out = []
    for index, unit in enumerate(found):
        if unit is None:
            continue
        indent, kind, lines = unit
        block = list(lines)
        for later in found[index + 1:]:
            if later is None or later[0] <= indent:
                if kind == 'para' and later is not None and later[0] == indent and DATED.match(later[2][0]) \
                        and later is found[index + 1]:
                    block += later[2]
                break
            block += later[2]
        out.append('\n'.join(block))
    return out


def cites_hold(against: str) -> None:
    """
    In every issues/ seed (not only those the plan or the build touched: an untouched seed's cite drifts as surely):
    - every pin names a commit, and exempts the cites in its block;
    - every unpinned file:line cite of a file the branch changes still points at the text it pointed at in the base
      (each failure is printed with both texts, all of them before the check goes red);
    - in the seeds this cycle touched, every dated line it adds that cites a file:line carries its own pin.
    A pin is "holds at `<sha>`" (or held/is/are/pinned at) anywhere in the cite's block (see cite_blocks).
    """
    changed = {Path(f).name for f in git('diff', '--name-only', against, 'HEAD', '--', '.', UNSHOWN).split()
               if f.endswith('.py')}
    since = f'{plan_commit()}~1'
    seeds = [f for f in git('ls-files', '--', 'issues/*.md').split() if Path(f).exists()]
    touched = set(git('diff', '--name-only', since, 'HEAD', '--', 'issues').split())
    seen = pinned_seen = 0
    drifted, verified = [], set()
    for seed in seeds:
        text = Path(seed).read_text()
        for block in cite_blocks(text):
            pins = PIN.findall(block)
            for sha in sorted(set(pins) - verified):
                known = run(['git', 'rev-parse', '--verify', '--quiet', f'{sha}^{{commit}}'], quiet=True)
                check(known.returncode == 0, f'{seed}: the pin `{sha}` names a commit')
                verified.add(sha)
            for match in CITE.finditer(block):
                where, name, low = match.group(1), match.group(2), int(match.group(3))
                high = int(match.group(4) or low)
                if name not in changed:
                    continue
                full = next((f for f in git('ls-files', f'*{where}').split() if f.endswith(where)), None)
                check(full is not None, f'{seed}: {match.group(0)} names a tracked file')
                if pins:
                    # Pinned: exempt. Not checked against the pin's tree, since a pinned dated line may cite where
                    # the text stood on another tree ("on the X tree it is at overlay.py:1038").
                    pinned_seen += 1
                    continue
                seen += 1
                old = git('show', f'{against}:{full}').splitlines()[low - 1:high]
                new = Path(full).read_text().splitlines()[low - 1:high]
                if old != new and (seed, match.group(0)) not in [(s, c) for s, c, _, _ in drifted]:
                    drifted.append((seed, match.group(0), old, new))
        if seed not in touched:
            continue
        added = {line for file, line in added_lines(since, seed)}
        for unit in units(text):
            if unit is None or unit[2][0] not in added or not STARTS_DATED.match(unit[2][0]):
                continue
            own = '\n'.join(unit[2])
            if CITE.search(own):
                first = unit[2][0].strip()[:60]
                check(PIN.search(own) is not None, f'{seed}: the dated line this cycle adds, {first!r}, cites a '
                                                   f'file:line and carries its own "holds at `<sha>`"')
    for seed, cite, old, new in drifted:
        say(f'  drift {seed}: {cite}')
        say(f'        base: {old}')
        say(f'        HEAD: {new}')
    check(not drifted, f'every unpinned cite of a changed file still holds what it cited at the base, or is pinned '
                       f'({len(drifted)} drifted: {[f"{s}: {c}" for s, c, _, _ in drifted]})')
    say(f'  ok    {seen} unpinned and {pinned_seen} pinned cite(s) of changed files in {len(seeds)} seed(s) checked')


def mode_records() -> None:
    against = base()
    check(git('status', '--porcelain', '--', 'AGENTS.md', 'PLAN.md', 'ROADMAP.md', 'issues', 'src', 'tests').strip()
          == '', 'AGENTS.md, PLAN.md, ROADMAP.md, issues/, src/ and tests/ are committed (records read them)')
    agents = Path('AGENTS.md').read_text()
    source = Path(OVERLAY).read_text()
    five = section(agents, '### 5. Committed source path')
    ten = section(agents, '### 10. Concurrency')
    violations = section(agents, '### Known open violations')
    rows = [line for line in violations.splitlines() if line.startswith('| ')]
    docs = {name: docstring(source, name) for name in ('truncate', 'open', '_serve', '_lookup_again')}
    naming = {name: docstring(source, name) for name in ('_ensure', '_named', '_adopt', '_watch', '_purge')}
    module = flat(Path(REPLAY).read_text().split('"""')[1])
    plan = Path('PLAN.md').read_text()

    # The fix makes these words false wherever they stood; no record says them, per window or otherwise.
    stress_added = '\n'.join(text for file, text in added_lines(against, STRESS_SEED))
    for where, text in (('§ 5', five), ('§ 10', ten), *((f'the {n} docstring', d) for n, d in docs.items()),
                        *((f'the {n} docstring', d) for n, d in naming.items()),
                        (f'{REPLAY}\'s module docstring', module), ('PLAN.md', plan),
                        ('the violations table', '\n'.join(rows)), (f'{STRESS_SEED}\'s added lines', stress_added)):
        hits = [s for s in STALE if s in flat(text)]
        check(not hits, f'{where} no longer says {hits}')
        claims = [s for s in sentences(text) if (NEVER_TOOK.search(s) or ALWAYS.search(s)) and not SCOPED.search(s)]
        check(not claims, f'{where} makes no unscoped "never acts on a file that took the id" or "always refuses" / '
                          f'"in every window" claim: the stage-served open stays open ({[c[:100] for c in claims]})')
        check(ONLY_PURGED.search(flat(text)) is None,
              f'{where} does not say a refusal comes "only" when the entry was purged (a rolled-back purge refuses)')

    # § 5: the lookup under the lock checks the watch; the stage-served open keeps its scoping.
    looked = [b for b in bullets(five) if '_lookup_again' in b]
    check(len(looked) >= 1 and all(WATCH_WORD.search(b) and PURGED_ID.search(b) for b in looked),
          '§ 5: each bullet naming `_lookup_again` says it also refuses an id its watch saw purged')
    check(WATCH_WORD.search(flat(five)) is not None and re.search(r'\bpurge watch\b', flat(five)) is not None,
          '§ 5 names the purge watch')
    check(STAGE_SCOPED.search(flat(five)) is not None,
          '§ 5 keeps the stage scoping: a copy served off a stage is opened without the source lock')

    # § 10: the watch mutex closes the lock order and is a leaf, taken inside a transaction by _purge.
    order = re.search(r'Lock order: \*\*path locks → source mutex → `_handles_mutex` → SQLite write lock'
                      r' → ([^*→]*)\*\*', flat(ten))
    check(order is not None and WATCH_WORD.search(order.group(1)) is not None,
          '§ 10: the lock order ends with the watch mutex, after the SQLite write lock')
    leaf = [b for b in bullets(ten) if WATCH_WORD.search(b) and re.search(r'\bleaf\b', b)]
    holders = ('_purge', '_lookup_again', '_named', '_adopt', '_watch')
    check(len(leaf) == 1 and all(word(n, leaf[0]) for n in holders) and 'transaction' in leaf[0],
          f'§ 10: one bullet calls the watch mutex a leaf, names its holders {holders} and says _purge takes it '
          f'inside its transaction')
    lock_comment = comment_above(source, 'self._source_lock = threading.RLock()')
    check(re.search(r'SQLite write lock,? then[^.]{0,80}\bwatch', lock_comment) is not None,
          'the source lock\'s comment in __init__ carries the same order, ending with the watch mutex')
    check(re.search(r'\bleaf\b', comment_above(source, f'{WATCH_MUTEX} = threading.Lock()',
                                               skip='self._watches')) is not None,
          'the watch mutex has its own comment, which calls it a leaf')

    # The docstrings: each says the call or lookup refuses a file that took the entry's id after a purge.
    for name, doc in docs.items():
        check(PURGED_ID.search(doc) is not None and WATCH_WORD.search(doc) is not None,
              f'the {name} docstring says a file that took the entry\'s id after a purge is refused, through a watch')
    for name in ('open', '_serve'):
        check(STAGE_SCOPED.search(docs[name]) is not None,
              f'the {name} docstring keeps the stage scoping (a stage copy is served or promoted without the lock)')
    truncate_doc_kept(against, docs['truncate'])
    check('issues/' not in ' '.join(docs.values()), 'no engine docstring the fix touches cites an issues/ seed')
    purge = docstring(Path(CATALOG).read_text(), 'purge', 'Catalog')
    check(re.search(r'\bid\b', purge) is not None and 'None' in purge,
          'the Catalog.purge docstring says it returns the purged id, or None')
    check(PURGED_ID.search(module) is not None,
          f'{REPLAY}\'s module docstring says the lookup under the lock refuses a file that took a purged id')
    check(re.search(r'\bpurge watch|\bwatch\b[^.]{0,80}\bpurge|\bpurge[^.]{0,80}\bwatch\b', flat(plan)) is not None,
          'PLAN.md says the lookup under the source lock refuses an id its purge watch saw purged')

    # The tables: every old name stays; the purge, the naming helper and every changed engine method are named.
    old_tables, new_tables = tables(git('show', f'{against}:AGENTS.md')), tables(agents)
    check(len(old_tables) == len(new_tables) == 2, 'both high-blast-radius tables are found')
    functions = changed_methods(against) - {'__init__'}           # its change is the two mutexes' comments
    check({'truncate', 'open', '_serve', '_lookup_again', '_ensure', '_adopt', '_named', '_watch', '_purge'}
          <= functions, f'the branch changes the watched calls and the naming, and adds _named, _watch and _purge '
                        f'({sorted(functions)})')
    for index, (old, new) in enumerate(zip(old_tables, new_tables), 1):
        lost = sorted(set(re.findall(r'`([^`]+)`', old)) - set(re.findall(r'`([^`]+)`', new)))
        lost = [t for t in lost if not all(word(part, new) for part in re.split(r'[ ,/]+', t) if part)]
        check(not lost, f'table {index} keeps every name it had at the base (lost {lost})')
        wanted = {'overlay.py': set(TABLE_NAMES['overlay.py']) | functions,
                  'catalog.py': set(TABLE_NAMES['catalog.py'])}
        for file, names in wanted.items():
            lines = [line for line in new.splitlines() if file in line]
            missing = sorted(n for n in names if not any(names_in_row(n, line) for line in lines))
            check(not missing, f'table {index}: its {file} rows name {sorted(names)} (missing {missing})')

    # Known open violations: the reused-id row is gone; what stays open is homed in the stress seed.
    check(not any(OWN_SEED in r for r in rows), f'no open-violation row names {OWN_SEED}')

    # The stress seed: a dated line from this cycle under the queue-row identity bullet names every Q5 site.
    stress = Path(STRESS_SEED).read_text()
    added = flat(' '.join(text for file, text in added_lines(against, STRESS_SEED)))
    queue = [b for b in bullets(stress) if b.startswith('Queue-row identity is by value')]
    check(len(queue) == 1 and SLUG in queue[0] and re.search(r'20\d\d-\d\d-\d\d.{0,200}' + re.escape(SLUG)
                                                             + r'|' + re.escape(SLUG) + r'.{0,200}20\d\d-\d\d-\d\d',
                                                             queue[0]) is not None,
          f'{STRESS_SEED}: the queue-row identity bullet carries a dated line naming {SLUG}')
    missing = [n for n in Q5_NAMES if not word(n, queue[0]) or not word(n, added)]
    check(SLUG in added and not missing, f'{STRESS_SEED}: the branch adds that dated line, naming every lookup by '
                                         f'entry id after a wait that no watch covers (missing {missing})')

    # Outside spec/, only the seed itself and its ROADMAP.md Seed line name the seed; nothing in src/ or tests/
    # cites an issues/ seed (Geoffrey's 2026-11-09 directive: seed paths stay out of source text).
    cited = run(['git', 'grep', '-n', 'issues/', 'HEAD', '--', 'src', 'tests'], quiet=True)
    check(cited.returncode == 1 and not cited.stdout.strip(),
          f'no file in src/ or tests/ at HEAD cites an issues/ seed (git grep: {cited.stdout.strip()[:120]!r})')
    tracked = [f for f in git('ls-files', '--', '.', UNSHOWN).split()
               if not f.startswith(('spec/', '.security/')) and Path(f).is_file()]
    own_file = Path(OWN_SEED).name
    naming = sorted(f for f in tracked if own_file in Path(f).read_text(errors='replace') and f != OWN_SEED)
    check(set(naming) <= {'ROADMAP.md'}, f'outside spec/, only ROADMAP.md names {OWN_SEED} (got {naming})')
    lines = [line for line in Path('ROADMAP.md').read_text().splitlines() if own_file in line]
    check(len(lines) <= 1 and all(line.startswith('**Seed:**') for line in lines),
          f'ROADMAP.md names {OWN_SEED} only on its own entry\'s Seed line, which `code terrace roadmap` retires')

    long = [f'{file}: {len(text)}' for file, text in added_lines(against, 'src', 'tests') if len(text) > 120]
    check(not long, f'no line the branch adds in src/ or tests/ exceeds 120 characters ({long[:3]})')
    ids = [f'{file}: {text.strip()[:60]}' for file, text in added_lines(against, 'src', 'tests', 'README.md')
           if SPEC_ID.search(text)]
    check(not ids, f'no spec id in a line the branch adds to src/, tests/ or README.md ({ids[:3]})')

    # The counts (AGENTS.md's quick-start, repository map, engine share, integration gate and § Verify).
    stated = stated_unit_count()
    check(stated > UNIT_FLOOR, f'AGENTS.md states {stated} unit tests > {UNIT_FLOOR}')
    totals = re.findall(r'(\d+) tests \((\d+) `unit`, (\d+) `integration`\)', agents)
    check(len(totals) == 1 and int(totals[0][0]) == int(totals[0][1]) + int(totals[0][2])
          and int(totals[0][1]) == stated, f'the repository map\'s total is unit + integration ({totals})')
    engines, old_engines = engine_count(agents), engine_count(git('show', f'{against}:AGENTS.md'))
    check(len(engines) == 1 and len(old_engines) == 1 and engines.pop() > old_engines.pop(),
          f'AGENTS.md states one engine-test count, above the base\'s ({engine_count(agents)})')

    touched = set(git('diff', '--name-only', against, 'HEAD', '--', '.', UNSHOWN).split())
    needed = {OVERLAY, CATALOG, REPLAY, 'AGENTS.md', 'PLAN.md', STRESS_SEED}
    check(needed <= touched, f'the branch diff carries the fix and its records (missing {sorted(needed - touched)})')
    cites_hold(against)
    pins_true(against)


# What each cite in a block that carries a pin this branch writes says is at its lines, as regexes that must all
# match the cited range at the pin. Keyed by (seed, cite as written). A cite in such a block with no entry here is
# red: the check never passes a cite it has no text for. Added in remediation (review cycle 1, F1): the plan's pins
# said "holds at `c7d2049`" where the cites had drifted before that commit, and `cites_hold` only checked that a pin
# names a commit.
PINNED_TEXT: dict[tuple[str, str], tuple[str, ...]] = {
    ('issues/archive-finalization-integration.md', 'overlay.py:870'): (r'def flush\b',),
    ('issues/archive-spec-plumbing.md', 'src/sosfs/tier/spec.py:28'): (r'\bTIER_KEYS\b',),
    ('issues/archive-spec-plumbing.md', 'spec.py:204'): (r'_reject_unknown\(',),
    ('issues/archive-spec-plumbing.md', 'spec.py:25'): (r'\bSUPPORTED_TYPES\b',),
    ('issues/archive-spec-plumbing.md', 'spec.py:24'): (r'\bTIER_TYPES\b',),
    ('issues/archive-spec-plumbing.md', 'spec.py:209-210'): (r'not yet supported',),
    ('issues/archive-spec-plumbing.md', 'src/sosfs/engine/overlay.py:109-126'): (r"tier_spec\.type == 's3'",
                                                                                  r'\belse:'),
    ('issues/archive-spec-plumbing.md', 'overlay.py:127-128'): (r'is not MarkerStatus\.OK', r'raise EngineError'),
    ('issues/archive-spec-plumbing.md', 'state/registry.py:106-110'): (r'def attach\(',),
    ('issues/archive-spec-plumbing.md', 'registry.py:112-117'): (r'def attach_tier\(',),
    ('issues/archive-spec-plumbing.md', 's3.py:286'): (r'def read_marker\(',),
    ('issues/archive-spec-plumbing.md', 'archive.py:271-274'): (r'def _guard\(',),
    ('issues/archive-spec-plumbing.md', 'overlay.py:200-201'): (r'No passthrough-capable tier',),
    ('issues/archive-spec-plumbing.md', 'spec.py:304-324'): (r'def _validate_stack\(',),
    ('issues/archive-spec-plumbing.md', 'archive.py:239-241'): (r'staged re-sync\) archive requires',),
    ('issues/archive-tier-test-coverage.md', 'tier/spec.py:25'): (r'\bSUPPORTED_TYPES\b',),
    ('issues/archive-tier-test-coverage.md', 'engine/overlay.py:109-126'): (r"tier_spec\.type == 's3'", r'\belse:'),
    ('issues/audit-command.md', 'src/sosfs/engine/transfer.py:78-108'): (r'journal\.begin\(',),
    ('issues/audit-command.md', 'transfer.py:78-108'): (r'journal\.begin\(',),
    ('issues/audit-command.md', 'src/sosfs/engine/transfer.py:85-118'): (r'journal\.begin\(',),
    ('issues/audit-command.md', 'db.py:34'): (r'\bSCHEMA_VERSION\b',),
    ('issues/audit-command.md', 'db.py:86'): (r'SchemaMismatch',),
    ('issues/audit-command.md', 'overlay.py:949'): (r'journal\.confirm\(intent\).*before any purge',),
    ('issues/audit-command.md', 'src/sosfs/engine/overlay.py:1061'): (r'journal\.confirm\(intent\).*before any purge',),
    ('issues/benchmark-harness.md', 'src/sosfs/tier/posix.py:70'): (r'def open_direct\(',),
    ('issues/benchmark-harness.md', 'src/sosfs/engine/overlay.py:639-654'): (r'def read\(', r'os\.pread\(',
                                                                             r'def write\(', r'os\.pwrite\('),
    ('issues/invariant-audit-gaps.md', 'src/sosfs/state/catalog.py:88'): (r'\bRESIDENCY_FIELDS\b',),
    ('issues/invariant-audit-gaps.md', 'src/sosfs/state/catalog.py:196-203'): (r'INSERT INTO residency',
                                                                               r'\bchecksum\b'),
    ('issues/invariant-audit-gaps.md', 'src/sosfs/tier/spec.py:287-289'): (r"overlay\.get\('checksum'",),
    ('issues/invariant-audit-gaps.md', 'src/sosfs/state/registry.py:151-157'): (r'def bump_generation\(',),
    ('issues/invariant-audit-gaps.md', 'tests/state/test_registry.py:92-97'): (r'\bbump_generation\(',),
    ('issues/invariant-audit-gaps.md', 'src/sosfs/engine/overlay.py:128'): (r"raise EngineError\(f'Tier .*marker",),
    ('issues/rename-to-terrace.md', 'engine/overlay.py:127-128'): (r'is not MarkerStatus\.OK', r'raise EngineError'),
    ('issues/source-commit-staged-publish.md', 'src/sosfs/engine/overlay.py:835-840'): (r'with self\._source_lock',),
}
# The one cite in such a block that names no commit: its dated line places it "on the `deferred-rename-source-orphan`
# tree", a branch tree, so no pin in the block can hold it. It is exempt by key alone; any other cite is checked.
OTHER_TREE = {('issues/audit-command.md', 'src/sosfs/engine/overlay.py:1038')}
INLINE_PIN = re.compile(CITE.pattern + r'`\s*\((?:holds?|held|is|are|pinned)\s+at\s+`([0-9a-f]{7,40})`\)')


def pins_true(against: str) -> None:
    """
    Every pin this branch writes into an issues/ seed is true at the commit it names:
    - a pin written inline after a cite ("`x.py:N` (holds at `<sha>`)") holds that cite at that very commit;
    - every other cite in a block (see cite_blocks) that carries such a pin holds at one of the block's pins.
    A cite holds at a commit when its file exists there, its line range lies inside the file, and every regex
    PINNED_TEXT gives it matches the cited lines. Each failure is printed with the lines it found, all of them
    before the check goes red.
    """
    added = {(file, line) for file, line in added_lines(against, 'issues')}
    files = git('ls-tree', '-r', '--name-only', 'HEAD').split()
    shown: dict[tuple[str, str], list[str] | None] = {}

    def lines_at(sha: str, full: str) -> list[str] | None:
        if (sha, full) not in shown:
            proc = run(['git', 'show', f'{sha}:{full}'], quiet=True)
            shown[sha, full] = proc.stdout.splitlines() if proc.returncode == 0 else None
        return shown[sha, full]

    def holds(seed: str, cite: re.Match, sha: str) -> tuple[bool, str]:
        where, low = cite.group(1), int(cite.group(3))
        high = int(cite.group(4) or low)
        full = next((f for f in files if f == where or f.endswith('/' + where)), None)
        text = lines_at(sha, full) if full else None
        if text is None or high > len(text):
            return False, f'{where} has no lines {low}-{high} at {sha}'
        cited = '\n'.join(text[low - 1:high])
        wanted = PINNED_TEXT[seed, cite.group(0)]
        shown_lines = ' | '.join(s.strip() for s in text[low - 1:high])[:160]
        return all(re.search(p, cited) for p in wanted), f'at {sha}: {shown_lines}'

    false, unknown, inline_seen, block_seen = [], [], 0, 0
    for seed in sorted({file for file, _ in added}):
        text = Path(seed).read_text()
        mine = {line for file, line in added if file == seed}
        for line in sorted(mine):
            for match in INLINE_PIN.finditer(line):
                cite = CITE.match(match.group(0))
                if (seed, cite.group(0)) not in PINNED_TEXT:
                    unknown.append(f'{seed}: {cite.group(0)}')
                    continue
                inline_seen += 1
                ok, found = holds(seed, cite, match.group(5))
                if not ok:
                    false.append(f'{seed}: {cite.group(0)} (holds at `{match.group(5)}`) is false; {found}')
        for block in cite_blocks(text):
            if not any(PIN.search(line) and line in mine for line in block.splitlines()):
                continue
            pins = sorted(set(PIN.findall(block)))
            for cite in CITE.finditer(block):
                if (seed, cite.group(0)) in OTHER_TREE:
                    continue
                if (seed, cite.group(0)) not in PINNED_TEXT:
                    unknown.append(f'{seed}: {cite.group(0)}')
                    continue
                block_seen += 1
                results = [holds(seed, cite, sha) for sha in pins]
                if not any(ok for ok, _ in results):
                    false.append(f'{seed}: {cite.group(0)} holds at none of its block\'s pins {pins}; '
                                 f'{"; ".join(found for _, found in results)}')
    for line in dict.fromkeys(false):
        say(f'  false {line}')
    check(not unknown, f'every cite in a block carrying a pin this branch writes has its cited text in PINNED_TEXT '
                       f'(missing {sorted(set(unknown))})')
    check(inline_seen > 0 and block_seen > 0, f'the pins this branch writes were found ({inline_seen} inline, '
                                              f'{block_seen} cites in their blocks)')
    check(not false, f'every pin this branch writes holds its cites at the commit it names '
                     f'({len(set(false))} false: {[f.split(" ")[1] for f in dict.fromkeys(false)]})')


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
    check(stray or not Path('.hypothesis').exists(), 'the suite left no .hypothesis/ in the tree')
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
    """From committed state: no CI, no .hypothesis/ before any run, two workers at most."""
    low, high = (int(x) for x in seeds.split('-'))
    check(git('status', '--porcelain').strip() == '', 'the tree is committed (the runs read HEAD)')
    lanes = [list(range(low + i, high + 1, 2)) for i in (0, 1)]
    workers = [(seed_tree(f'seeds-{i}'), lane) for i, lane in enumerate(lanes) if lane]
    aside = scratch('seeds-hypothesis')
    token = f'identity-gate-{uuid.uuid4().hex}'
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
    try:
        left = subprocess.run(['pgrep', '-f', token], capture_output=True, text=True)
    except OSError:
        left = None
    if left is not None and left.returncode in (0, 1):   # pgrep may be blocked in a sandbox; the group kill stands
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
    text = git('log', '--format=%B', 'origin/main..HEAD') + git('diff', 'origin/main...HEAD', '--', '.', UNSHOWN)
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
