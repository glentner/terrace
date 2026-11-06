# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the toolchain-repair cycle.

Usage (from the cycle worktree root, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I spec/toolchain-repair/verify/toolchain_gate.py MODE [MODE ...]

Modes:
    deps     R1, R6   fresh-cache `uv sync` leaves uv.lock unchanged, builds nothing but the project and
                      pyyaml, resolves no pydantic-core; the S3 tier tests then run with none skipped
    tier     R5, R7   test_posix.py passes in a directory whose group is not the process's; with boto3 or
                      moto.server unimportable the S3 tests skip naming it and test_posix.py still runs
    fuse     R3 | R4  without libfuse every selected tests/fuse test skips naming libfuse; with libfuse
                      every -m unit test there runs and none skips
    suite    R2, R8   the profile's verify: lint clean, 0 errors, passed > 0, no failed test but test_model's
                      runTest; then -m 'not integration' with the same, and >= 405 passed outside tests/fuse,
                      counting runTest whether it passed or failed
    prepush           clean tree, then suite, then no home-directory path in the branch diff or messages
    ci       R9 (R1, R3, R4, R6 on CI)  the push run for the pushed commit (--sha, default origin/<branch>; later
                      commits may touch only spec/): Lint & build concluded success; each unit job concluded
                      success, or failed in its test step with test_model's runTest its only failed or
                      erroring test; the libfuse probe passed, Linux runs >= 449 with no skip, macOS skips
                      only tests/fuse naming libfuse. --run-json/--log-dir read saved `gh run view --json`
                      output and job logs (<job id>.log or <job name>.log) instead
    ledger            every deferral phrase in TECH.md's phase bodies names an issues/ seed with a
                      ROADMAP.md entry
    claims            review cycle 1, F2 and F3: no live text says test_model's defect spares macOS, and every
                      description of the CI unit step's -rfEs output names pytest's folded SKIPPED line

Every count comes from pytest's JUnit XML (xunit1, which carries the test's file), never from the exit code.
The tolerated test is matched by its classname and name, never its file: for a Hypothesis state machine the
file points into Hypothesis's stateful.py.

Footprint: one subprocess at a time. `suite` and `prepush` run pytest with `-n auto` (one worker per core,
about 1.5 GB peak on a 10-core machine); every other pytest run is serial (2 processes, under 500 MB).
`deps` downloads the locked set into a fresh cache (about 150 MB). Scratch goes under $TMPDIR and is left
in place for inspection; nothing is deleted. The gate never pushes and never writes to the tree.

Exit status: 0 green, 1 red, 3 could not run (a precondition missing; never read as green).
"""

from __future__ import annotations

import os
import re
import sys
import json
import shutil
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


SLUG = 'toolchain-repair'
FUSE_UNIT_FLOOR = 44          # test_adapter.py 27 + test_mount.py 17 (`def test_` count at plan time)
FUSE_ALL_FLOOR = 51           # plus test_integration.py's 7
S3_FLOOR = {'tests/tier/test_s3.py': 21, 'tests/tier/test_s3_engine.py': 8}
POSIX_FLOOR = 23
R8_FLOOR = 405
ALLOWED_SOURCE_BUILDS = {'sosfs', 'pyyaml'}       # the project; pyyaml publishes no cp315 wheel (2026-11-05)
CI_JOBS = ('Unit (Linux, 3.15)', 'Unit (Linux, 3.15t)', 'Unit (macOS, engine-only)', 'Lint & build')
CI_LINUX = ('Unit (Linux, 3.15)', 'Unit (Linux, 3.15t)')
CI_MACOS = 'Unit (macOS, engine-only)'
CI_PROBE_STEP = 'Assert libfuse loads'
CI_TEST_STEP = 'Unit tests'
# The namespace-replay defect deferred to issues/deferred-rename-source-orphan.md (GOAL R2, R8 and R9, amended
# 2026-11-05): the one test a CI unit job or the local suite may fail on, matched by its node id, never by file
CI_TOLERATED = 'tests/engine/test_model.py::TestOverlayModel::runTest'
_module, _class, _name = CI_TOLERATED.split('::')
TOLERATED_JUNIT = (_module.removesuffix('.py').replace('/', '.') + '.' + _class, _name)   # (classname, name)
DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|deferred', re.IGNORECASE)
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')    # written so this line cannot match itself
UNIT_FLOOR = 449              # AGENTS.md's measured -m unit count; every one runs on Linux
HOLLOWING = ('UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC', 'UV_OFFLINE')   # each makes a sync or lock check vacuous
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


def run(argv: list[str], env: dict[str, str] | None = None, quiet: bool = False) -> subprocess.CompletedProcess:
    print(f'  run   {" ".join(argv)}', flush=True)
    inherited = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    merged = {**inherited, **(env or {})}
    proc = subprocess.run(argv, env=merged, text=True, capture_output=True)
    if not quiet:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
        for line in tail:
            print(f'        {line}')
    return proc


# -- JUnit ---------------------------------------------------------------------------------------------------

@dataclass
class Case:
    file: str
    name: str
    outcome: str                  # passed | skipped | failed | error
    message: str = ''
    classname: str = ''

    @property
    def tolerated(self) -> bool:
        return (self.classname, self.name) == TOLERATED_JUNIT

    @property
    def fuse(self) -> bool:
        return self.file.startswith('tests/fuse') or self.classname.startswith('tests.fuse.')


@dataclass
class Report:
    cases: list[Case] = field(default_factory=list)

    def where(self, prefix: str = '', outcome: str = '') -> list[Case]:
        return [c for c in self.cases if c.file.startswith(prefix) and (not outcome or c.outcome == outcome)]

    def count(self, prefix: str = '', outcome: str = '') -> int:
        return len(self.where(prefix, outcome))


def junit(path: Path) -> Report:
    if not path.exists():
        raise Red(f'pytest wrote no JUnit report at {path}')
    report = Report()
    for case in ET.parse(path).getroot().iter('testcase'):
        file = case.get('file') or ''
        outcome, message = 'passed', ''
        for tag in ('error', 'failure', 'skipped'):
            node = case.find(tag)
            if node is not None:
                outcome = {'failure': 'failed', 'error': 'error'}.get(tag, tag)
                message = node.get('message') or ''
                break
        report.cases.append(Case(file=file, name=case.get('name') or '', outcome=outcome, message=message,
                                 classname=case.get('classname') or ''))
    return report


def pytest(args: list[str], label: str, env: dict[str, str] | None = None,
           runner: list[str] | None = None) -> tuple[subprocess.CompletedProcess, Report]:
    xml = scratch(label) / 'junit.xml'
    argv = (runner or ['uv', 'run', 'pytest']) + ['-p', 'no:cacheprovider', '-rs', '-o', 'junit_family=xunit1',
                                                  f'--junitxml={xml}'] + args
    proc = run(argv, env=env)
    return proc, junit(xml)


def no_red(report: Report, scope: str) -> None:
    check(report.count(outcome='failed') == 0, f'{scope}: 0 failed (got {report.count(outcome="failed")})')
    check(report.count(outcome='error') == 0, f'{scope}: 0 errors (got {report.count(outcome="error")})')


# -- modes ---------------------------------------------------------------------------------------------------

def refuse_hollowing() -> None:
    """A caller's UV_FROZEN, UV_NO_SYNC and kin would let a sync or lock check pass without checking."""
    present = [name for name in HOLLOWING if os.environ.get(name)]
    if present:
        raise CannotRun(f'unset {present} first: each makes a sync or lock assertion vacuous')


def sync_exact() -> None:
    """Bring the project environment to exactly the lock (sync is exact by default; --locked refuses drift)."""
    proc = run(['uv', 'sync', '--locked'])
    check(proc.returncode == 0, f'uv sync --locked exits 0 (got {proc.returncode})')

def mode_deps() -> None:
    """R1, R6."""
    refuse_hollowing()
    python = run(['uv', 'python', 'find', '3.15'], quiet=True).stdout.strip()
    if not python:
        raise CannotRun('no Python 3.15 interpreter found by uv')
    level = run([python, '-c', 'import sys; print(*sys.version_info[:2], sys.version_info.releaselevel)'],
                quiet=True).stdout.split()
    if level != ['3', '15', 'final']:
        raise CannotRun(f'Python 3.15 interpreter is not a final release: {level}')
    print(f'  info  interpreter {python} ({" ".join(level)})')

    lock = Path('uv.lock')
    before = lock.read_bytes()
    names = set(re.findall(r'^name = "([^"]+)"', before.decode(), flags=re.MULTILINE))
    check('pydantic-core' not in names, 'uv.lock resolves no pydantic-core')

    root = scratch('deps')
    env = {'UV_CACHE_DIR': str(root / 'cache'), 'UV_PROJECT_ENVIRONMENT': str(root / 'venv')}
    sync = run(['uv', 'sync', '--python', python], env=env)
    check(sync.returncode == 0, f'fresh-cache uv sync exits 0 (got {sync.returncode})')
    check(lock.read_bytes() == before, 'uv.lock is byte-identical after the sync')
    built = {m.group(1).lower() for m in re.finditer(r'^\s*Built ([A-Za-z0-9_.-]+)', sync.stderr, re.MULTILINE)}
    check(bool(built) and built <= ALLOWED_SOURCE_BUILDS,
          f'source builds are only {sorted(ALLOWED_SOURCE_BUILDS)} (got {sorted(built)})')

    proc, report = pytest(['-m', 'unit', 'tests/tier/test_s3.py', 'tests/tier/test_s3_engine.py',
                           'tests/tier/test_archive.py'], 'deps-s3', env=env,
                          runner=['uv', 'run', '--no-sync', 'pytest'])
    check(proc.returncode == 0, f'S3 tier run exits 0 (got {proc.returncode})')
    no_red(report, 'S3 tier run')
    check(report.count(outcome='skipped') == 0, f'S3 tier run: 0 skipped (got {report.count(outcome="skipped")})')
    for file, floor in S3_FLOOR.items():
        got = report.count(file, 'passed')
        check(got >= floor, f'{file}: {got} passed >= {floor}')


def foreign_group_dir() -> Path:
    """A directory whose group is not the process's primary group, set-gid so children inherit it."""
    base = scratch('foreign-group')
    if os.stat(base).st_gid == os.getgid():
        for gid in os.getgroups():
            if gid == os.getgid():
                continue
            try:
                os.chown(base, -1, gid)
                break
            except PermissionError:
                continue
    os.chmod(base, 0o2775)
    if os.stat(base).st_gid == os.getgid():
        raise CannotRun('no directory with a group other than the primary group could be made')
    return base


PROBES = {
    # Each must turn test_put_and_stat_applies_meta red; a green probe means the test lost that assertion.
    'gid_probe': ('import os\n'
                  'from sosfs.tier.posix import PosixTier\n'
                  'apply = PosixTier._apply_meta\n'
                  'def chown_to_primary(real, meta):\n'
                  '    apply(real, meta)\n'
                  '    os.chown(real, -1, os.getgid())\n'
                  'PosixTier._apply_meta = staticmethod(chown_to_primary)\n'),
    'meta_probe': ('import os\n'
                   'from sosfs.tier.posix import PosixTier\n'
                   'apply = PosixTier._apply_meta\n'
                   'def wrong_mode_and_mtime(real, meta):\n'
                   '    apply(real, meta)\n'
                   '    os.chmod(real, 0o600)\n'
                   '    os.utime(real, (1.0, 1.0))\n'
                   'PosixTier._apply_meta = staticmethod(wrong_mode_and_mtime)\n'),
}


BLOCK = ('import sys\n'
         'sys.modules[sys.argv[1]] = None\n'
         'import pytest\n'
         'raise SystemExit(pytest.main(sys.argv[2:]))\n')


def mode_tier() -> None:
    """R5, R7."""
    refuse_hollowing()
    sync_exact()
    base = foreign_group_dir()
    proc, report = pytest([f'--basetemp={base / "t"}', 'tests/tier/test_posix.py'], 'tier-r7')
    cases = [c for c in report.where('tests/tier/test_posix.py') if c.name == 'test_put_and_stat_applies_meta']
    check(len(cases) == 1 and cases[0].outcome == 'passed', 'test_put_and_stat_applies_meta passed')
    check(report.count('tests/tier/test_posix.py', 'passed') >= POSIX_FLOOR,
          f'test_posix.py: {report.count("tests/tier/test_posix.py", "passed")} passed >= {POSIX_FLOOR}')
    no_red(report, 'test_posix.py run')
    check(proc.returncode == 0, f'test_posix.py run exits 0 (got {proc.returncode})')
    used = sorted((base / 't').glob('test_put_and_stat*'))
    check(bool(used) and all(os.stat(p).st_gid != os.getgid() for p in used),
          f'the test ran in a directory whose group ({[os.stat(p).st_gid for p in used]}) is not {os.getgid()}')

    probes = scratch('probes')
    for name, source in PROBES.items():
        (probes / f'{name}.py').write_text(source)
        target = 'tests/tier/test_posix.py::test_put_and_stat_applies_meta'
        proc, report = pytest(['-p', name, f'--basetemp={base / name}', target], f'tier-{name}',
                              env={'PYTHONPATH': str(probes)})
        cases = report.where('tests/tier/test_posix.py')
        check(len(cases) == 1 and cases[0].outcome == 'failed',
              f'{name}: the ownership test fails when put leaves the file {name.split("_")[0]} wrong')

    for blocked in ('boto3', 'moto.server'):
        proc, report = pytest(['-m', 'unit', 'tests/tier'], f'tier-r5-{blocked}',
                              runner=['uv', 'run', 'python', '-I', '-c', BLOCK, blocked])
        scope = f'tests/tier with {blocked} unimportable'
        no_red(report, scope)
        check(proc.returncode == 0, f'{scope}: exits 0 (got {proc.returncode})')
        for file, floor in S3_FLOOR.items():
            cases = report.where(file)
            check(len(cases) >= floor and all(c.outcome == 'skipped' and f'need {blocked}' in c.message for c in cases),
                  f'{scope}: all {len(cases)} tests in {file} skipped naming {blocked} (floor {floor})')
        skipped = report.where(outcome='skipped')
        check(all(f'need {blocked}' in c.message for c in skipped), f'{scope}: every skip says it needs {blocked}')
        got = report.count('tests/tier/test_posix.py', 'passed')
        check(got >= POSIX_FLOOR and report.count('tests/tier/test_posix.py') == got,
              f'{scope}: test_posix.py collected and passed in full ({got} >= {POSIX_FLOOR})')
        check(report.count('tests/tier/test_archive.py', 'passed') >= 1,
              f'{scope}: the non-S3 archive tests still run')


def libfuse_loads() -> bool:
    probe = run(['uv', 'run', 'python', '-c', 'import mfusepy'], quiet=True)
    if probe.returncode == 0:
        return True
    if 'OSError' not in probe.stderr:
        raise CannotRun(f'mfusepy failed for a reason other than libfuse: {probe.stderr.strip()[-300:]}')
    return False


def mode_fuse() -> None:
    """R3 where libfuse cannot be loaded, R4 where it can; either way the skip reaches no other package."""
    refuse_hollowing()
    sync_exact()
    proc, report = pytest(['-m', 'unit', 'tests/fuse', 'tests/tier/test_posix.py'], 'fuse-neighbour')
    got = report.count('tests/tier/test_posix.py', 'passed')
    check(got >= POSIX_FLOOR and report.count('tests/tier/test_posix.py', 'skipped') == 0,
          f'collected beside tests/fuse, test_posix.py still runs in full ({got} passed, 0 skipped)')
    if libfuse_loads():
        print('  info  libfuse loads: grading R4')
        proc, report = pytest(['-m', 'unit', 'tests/fuse'], 'fuse-r4')
        no_red(report, 'tests/fuse -m unit')
        check(report.count(outcome='skipped') == 0, f'tests/fuse -m unit: 0 skipped (got {report.count(outcome="skipped")})')
        check(report.count('tests/fuse', 'passed') >= FUSE_UNIT_FLOOR,
              f'tests/fuse -m unit: {report.count("tests/fuse", "passed")} passed >= {FUSE_UNIT_FLOOR}')
        check(proc.returncode == 0, f'tests/fuse -m unit exits 0 (got {proc.returncode})')
        return
    print('  info  libfuse cannot be loaded: grading R3')
    for selection, floor in ((['-m', 'unit'], FUSE_UNIT_FLOOR), (['-m', 'not integration'], FUSE_UNIT_FLOOR),
                             ([], FUSE_ALL_FLOOR)):
        scope = f'tests/fuse {" ".join(selection) or "(no -m)"}'
        proc, report = pytest(selection + ['tests/fuse'], 'fuse-r3')
        no_red(report, scope)
        cases = report.cases
        check(len(cases) >= floor, f'{scope}: {len(cases)} tests reported >= {floor}')
        check(all(c.outcome == 'skipped' and 'libfuse' in c.message for c in cases),
              f'{scope}: every test skipped with a reason naming libfuse')
        check(proc.returncode == 0, f'{scope}: exits 0 (got {proc.returncode})')


def tolerated_red(proc: subprocess.CompletedProcess, report: Report, scope: str) -> int:
    """R2, R8 as amended: 0 errors, and no failed test but CI_TOLERATED, which ran; 1 if it failed, else 0."""
    errors = [f'{c.classname}::{c.name}' for c in report.where(outcome='error')]
    check(not errors, f'{scope}: 0 errors (got {errors})')
    others = [f'{c.classname}::{c.name}' for c in report.where(outcome='failed') if not c.tolerated]
    check(not others, f'{scope}: no failed test other than {CI_TOLERATED} (got {others})')
    mine = [c for c in report.cases if c.tolerated]
    check(len(mine) == 1 and mine[0].outcome in ('passed', 'failed'),
          f'{scope}: {CI_TOLERATED} ran once ({[c.outcome for c in mine]})')
    failed = int(mine[0].outcome == 'failed')
    check(proc.returncode == failed, f'{scope}: exits {failed} (got {proc.returncode})')
    if failed:
        print(f'  info  {scope}: {CI_TOLERATED} failed, the deferred defect R2 and R8 accept '
              f'({mine[0].message[:120]!r})')
    return failed


def mode_suite() -> None:
    """R2, R8: the profile's verify, then the wider selection, in an environment exactly matching the lock."""
    refuse_hollowing()
    sync_exact()
    lint = run(['tools/lint.sh'])
    check(lint.returncode == 0, f'tools/lint.sh exits 0 (got {lint.returncode})')
    proc, report = pytest(['-n', 'auto', '-m', 'unit'], 'suite-r2')
    tolerated_red(proc, report, '-m unit')
    check(report.count(outcome='passed') > 0, f'-m unit: {report.count(outcome="passed")} passed > 0')
    print(f'  info  -m unit: {report.count(outcome="passed")} passed, {report.count(outcome="failed")} failed, '
          f'{report.count(outcome="skipped")} skipped')

    proc, report = pytest(['-n', 'auto', '-m', 'not integration'], 'suite-r8')
    failed = tolerated_red(proc, report, "-m 'not integration'")
    outside = [c for c in report.where(outcome='passed') if not c.fuse]
    check(len(outside) + failed >= R8_FLOOR,
          f"-m 'not integration': {len(outside)} passed + {failed} tolerated outside tests/fuse >= {R8_FLOOR}")


def git(*args: str) -> str:
    return run(['git', *args], quiet=True).stdout


def mode_prepush() -> None:
    """Before the push: a clean tree, the full local gate, no home-directory paths leaving the machine."""
    check(git('status', '--porcelain').strip() == '', 'working tree is clean')
    mode_suite()
    text = git('log', '--format=%B', 'origin/main..HEAD') + git('diff', 'origin/main..HEAD')
    hits = sorted(set(HOME_PATH.findall(text)))
    check(not hits, f'no home-directory paths in the branch diff or commit messages (got {hits})')


def gh_json(args: list[str]) -> object:
    if shutil.which('gh') is None:
        raise CannotRun('gh is not available')
    proc = run(['gh', *args], quiet=True)
    if proc.returncode != 0:
        raise CannotRun(f'gh {" ".join(args)} failed: {proc.stderr.strip()[-300:]}')
    return json.loads(proc.stdout)


def gh_log(run_id: int, job_id: int) -> str:
    proc = run(['gh', 'run', 'view', str(run_id), '--job', str(job_id), '--log'], quiet=True)
    if proc.returncode != 0:
        raise CannotRun(f'could not read the log of job {job_id}: {proc.stderr.strip()[-300:]}')
    return proc.stdout


SUMMARY = re.compile(r'=+ (.*\b(?:passed|failed|error|errors|skipped)\b.*) in [0-9.]+s')
SHORT_SUMMARY = 'short test summary info'
RED_LINE = re.compile(r'(?:^|\dZ )(FAILED|ERROR) (\S+)')      # bare, or after a `gh run view --log` timestamp
RUN_FIELDS = 'databaseId,headSha,headBranch,event,status,conclusion,jobs'


@dataclass
class Outcome:
    line: str                     # pytest's final summary line
    passed: int
    skipped: int
    tolerated: int                # 1 when the job's one failed or erroring test is CI_TOLERATED, else 0


def summary(log: str, job: str) -> tuple[str, int, int, int, int]:
    """The single pytest summary line of a job log, with its passed, skipped, failed and error counts."""
    lines = SUMMARY.findall(log)
    check(len(lines) == 1, f'{job}: exactly one pytest summary line in the log (got {len(lines)})')
    line = lines[0]
    count = lambda word: int(m.group(1)) if (m := re.search(rf'\b(\d+) {word}\b', line)) else 0
    return line, count('passed'), count('skipped'), count('failed'), count('errors?')


def red_tests(log: str) -> list[tuple[str, str]]:
    """The FAILED and ERROR node ids of pytest's short test summary, which `-rfEs` keeps in the log."""
    lines = log.splitlines()
    starts = [i for i, text in enumerate(lines) if SHORT_SUMMARY in text]
    if not starts:
        return []
    found = []
    for text in lines[starts[-1] + 1:]:
        if SUMMARY.search(text):
            break
        if m := RED_LINE.search(text):
            found.append((m.group(1), m.group(2)))
    return found


def unit_outcome(job: dict, log: str) -> Outcome:
    """A unit job is green when it succeeded, or failed in its test step on CI_TOLERATED alone."""
    name, conclusion = job['name'], job.get('conclusion')
    line, passed, skipped, failed, errors = summary(log, name)
    reds = red_tests(log)
    check(len(reds) == failed + errors,
          f'{name}: {len(reds)} FAILED/ERROR lines match the summary\'s {failed} failed + {errors} errors ({line!r})')
    if conclusion == 'success':
        check(failed == 0 and errors == 0, f'{name}: concluded success with no failure or error ({line!r})')
        return Outcome(line, passed, skipped, 0)
    check(conclusion == 'failure', f'{name}: concluded success or failure (got {conclusion})')
    steps = [s['name'] for s in job.get('steps', []) if s.get('conclusion') == 'failure']
    check(steps == [CI_TEST_STEP], f'{name}: the only failed step is {CI_TEST_STEP!r} (got {steps})')
    ids = sorted({node for _, node in reds})
    check(ids == [CI_TOLERATED], f'{name}: the only failed or erroring test is {CI_TOLERATED} (got {ids})')
    print(f'  info  {name}: failed on {CI_TOLERATED} alone, the deferred defect R9 accepts')
    return Outcome(line, passed, skipped, 1)


def pushed_sha(sha: str) -> str:
    """The commit the run must be for: --sha, else the branch's remote-tracking ref (the last push), else HEAD.

    Anything committed between it and HEAD stays inside spec/, so a later state commit keeps the run valid.
    """
    head = git('rev-parse', 'HEAD').strip()
    branch = git('branch', '--show-current').strip()
    pushed = git('rev-parse', '--verify', '--quiet', f'refs/remotes/origin/{branch}').strip()
    target = git('rev-parse', sha).strip() if sha else (pushed or head)
    if not target:
        raise CannotRun(f'unknown commit {sha!r}')
    moved = [f for f in git('diff', '--name-only', target, head).split() if not f.startswith(f'spec/{SLUG}/')]
    check(not moved, f'commits after {target[:12]} touch only spec/{SLUG}/ (got {moved})')
    return target


def mode_ci(run_json: str = '', log_dir: str = '', sha: str = '') -> None:
    """R9 from the record of the push run for the pushed commit; R1, R3, R4, R6 read from the same run."""
    target = pushed_sha(sha)
    branch = git('branch', '--show-current').strip()
    if run_json:
        record = json.loads(Path(run_json).read_text())
        if 'headSha' not in record or 'jobs' not in record:
            raise CannotRun(f'{run_json} must be `gh run view <id> --json {RUN_FIELDS}`, not a jobs list alone')
    else:
        runs = gh_json(['run', 'list', '--branch', branch, '--commit', target, '--event', 'push',
                        '--workflow', 'ci.yml', '--json', 'databaseId,headSha'])
        runs = [r for r in runs if r['headSha'] == target]
        if not runs:
            raise CannotRun(f'no push run of ci.yml for {branch} at {target[:12]} yet')
        record = gh_json(['run', 'view', str(max(r['databaseId'] for r in runs)), '--json', RUN_FIELDS])
    run_id = record.get('databaseId')
    check(record.get('headSha') == target, f'run {run_id} is for {target[:12]} (got {str(record.get("headSha"))[:12]})')
    check(record.get('event') == 'push', f'run {run_id} was triggered by a push (got {record.get("event")})')
    check(record.get('headBranch') == branch, f'run {run_id} ran on {branch} (got {record.get("headBranch")})')
    if record.get('status') != 'completed':
        raise CannotRun(f'run {run_id} is {record.get("status")}; wait for it')
    jobs = record['jobs']
    by_name = {j['name']: j for j in jobs}
    for name in CI_JOBS:
        check(name in by_name, f'job {name!r} is in the run')
    conclusion = by_name['Lint & build'].get('conclusion')
    check(conclusion == 'success', f"job 'Lint & build' concluded success (got {conclusion})")

    def log_of(name: str) -> str:
        if log_dir:
            by_id = Path(log_dir) / f'{by_name[name]["databaseId"]}.log'
            by_label = Path(log_dir) / (re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_') + '.log')
            text = (by_id if by_id.exists() else by_label).read_text()
        else:
            text = gh_log(run_id, by_name[name]['databaseId'])
        return ANSI.sub('', text)

    linux_total = []
    for name in CI_LINUX:
        steps = {s['name']: s.get('conclusion') for s in by_name[name].get('steps', [])}
        check(steps.get(CI_PROBE_STEP) == 'success', f'{name}: step {CI_PROBE_STEP!r} concluded success')
        result = unit_outcome(by_name[name], log_of(name))
        check(result.skipped == 0, f'{name}: no skip ({result.line!r})')
        total = result.passed + result.tolerated
        check(total >= UNIT_FLOOR, f'{name}: {result.passed} passed + {result.tolerated} tolerated >= {UNIT_FLOOR}')
        linux_total.append(total)
    log = log_of(CI_MACOS)
    result = unit_outcome(by_name[CI_MACOS], log)
    passed, skipped = result.passed, result.skipped
    check(passed + skipped + result.tolerated == linux_total[0],
          f'{CI_MACOS}: {passed} passed + {skipped} skipped + {result.tolerated} tolerated == Linux {linux_total[0]}')
    reasons = re.findall(r'SKIPPED \[(\d+)\] (\S+?):\d*:? ?(.*)', log)
    check(bool(reasons) and all(f.startswith('tests/fuse') and 'libfuse' in r for _, f, r in reasons),
          f'{CI_MACOS}: every skip is under tests/fuse and names libfuse ({len(reasons)} skip lines)')
    total = sum(int(n) for n, _, _ in reasons)
    check(total >= FUSE_UNIT_FLOOR and total == skipped,
          f'{CI_MACOS}: {total} libfuse skips >= {FUSE_UNIT_FLOOR}, matching the summary line')


def mode_ledger() -> None:
    """Every deferral phrase in a TECH.md phase body names an issues/ seed that ROADMAP.md lists."""
    tech = Path(f'spec/{SLUG}/TECH.md').read_text().split('\n---\n', 2)[-1]
    roadmap = Path('ROADMAP.md').read_text()
    found = 0
    for line in tech.splitlines():
        if line.lstrip().startswith('#') or not DEFERRAL.search(line):
            continue
        found += 1
        seeds = re.findall(r'issues/([a-z0-9-]+)\.md', line)
        check(bool(seeds), f'deferral names a seed: {line.strip()[:90]!r}')
        for seed in seeds:
            check(Path(f'issues/{seed}.md').exists(), f'issues/{seed}.md exists')
            check(f'issues/{seed}.md' in roadmap, f'ROADMAP.md lists issues/{seed}.md')
    print(f'  info  {found} deferral line(s) checked')


# Review cycle 1, F2: both CI runs' macOS jobs and the Mac under CI=true fail test_model's runTest
MAC_DENIAL = re.compile(r'(?:did|does|do)\s+not\s+show\s+on\s+(?:the\s+)?mac|neither\s+shows?\s+on\s+(?:the\s+)?mac'
                        r'|reproduced\s+by\s+ci\s+on\s+linux\s+\(', re.IGNORECASE)
MAC_SITES = ('AGENTS.md', 'ROADMAP.md', 'issues')
# Review cycle 1, F3: -rfEs prints one `SKIPPED [n] file:line: reason` line per skip location, not per reason
SKIP_CLAIM = re.compile(r'per\s+skip(?:ped)?\s+reason|line\s+per\s+skip\s+reason', re.IGNORECASE)
SKIP_FORM = 'SKIPPED [n] file:line: reason'
SKIP_SITES = ('AGENTS.md', '.github/workflows/ci.yml')


def mode_claims() -> None:
    """F2 and F3 of review cycle 1, as classes: the false claim is absent from every live site."""
    files = []
    for site in MAC_SITES:
        root = Path(site)
        files += sorted(root.rglob('*.md')) if root.is_dir() else [root]
    check(len(files) > 2, f'{len(files)} live files read for claims about where test_model fails')
    hits = [f'{f}: {m.group(0)!r}' for f in files for m in MAC_DENIAL.finditer(f.read_text())]
    check(not hits, f'no live text says the test_model defect spares macOS (got {hits})')
    seed = Path('issues/deferred-rename-source-orphan.md').read_text()
    check(all(token in seed for token in ('42023846602', '42059324110', 'macOS', 'CI=true')),
          'the seed names both runs, macOS and the CI=true reproduction')
    for site in SKIP_SITES:
        text = Path(site).read_text()
        check('-rfEs' in text, f'{site} describes the -rfEs unit step')
        check(not SKIP_CLAIM.search(text), f'{site}: no "line per skip reason" claim')
        check(SKIP_FORM in text, f'{site}: names the {SKIP_FORM!r} line pytest prints')


MODES = {'deps': mode_deps, 'tier': mode_tier, 'fuse': mode_fuse, 'suite': mode_suite, 'prepush': mode_prepush,
         'ci': mode_ci, 'ledger': mode_ledger, 'claims': mode_claims}


def main(argv: list[str]) -> int:
    options = {}
    modes = []
    for arg in argv:
        if arg.startswith(('--run-json=', '--log-dir=', '--sha=')):
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
            MODES[name](**options) if name == 'ci' else MODES[name]()
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
