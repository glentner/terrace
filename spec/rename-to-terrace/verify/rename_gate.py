# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gates for the rename-to-terrace cycle.

Usage (from the cycle worktree root, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I spec/rename-to-terrace/verify/rename_gate.py MODE [MODE ...]

Modes:
    identity   R2, R3      the package directory moved and nothing tracked carries the old name in its path;
                           no collapsed identifier; the only old-name text left in the code scope is a
                           contract form the second phase owns; `uv lock --check`; the environment holds
                           `terrace` and no old distribution; the CLI's version and help in a throwaway
                           site; `uv build` yields one sdist and one wheel with the new name and URL
    contracts  R4 to R9    no old name in the code scope; markers, the partial directory and the S3
                           sentinel under their new names; an overlay written by the base commit's code is
                           refused through the real CLI and its tier roots are left byte for byte; the
                           environment prefix, the path namespace, the two attributes and the state store
    census     R1          the GOAL's three checks
    docs       R11, R8     the CHANGELOG entry names every moved thing, old and new; PLAN.md's technology
                           row; AGENTS.md no longer calls the rename pending; this cycle's records say
                           the real-mount check was not run
    ledger                 every deferral phrase in TECH.md's phase bodies names an issues/ seed that
                           ROADMAP.md lists
    suite      R10         the profile's verify: lint exits 0, and the unit lane reports exactly the base
                           commit's passed count with no failure, error or skip
    prepush                a clean tree, no home-directory path in the branch diff or messages, then census
    all                    identity, contracts, census, docs, ledger, suite

Old names appear in this file on purpose: `spec/` is outside the census, and the drives that show an old
name has no effect have to spell it.

Every mode first refuses a tree whose tracked files differ from the index, so what is graded is what a
commit would hold: stage, then run. `contracts` and `suite` exit 3 where libfuse cannot be loaded, since the
adapter tests would skip and a count or a named test would read red for no fault of the tree.

Every test count comes from pytest's JUnit XML, never from the exit code. A drive that needs the product
runs it in a child process: the project interpreter with `-I`, or the CLI under `tools/temp_site.sh`. A
child's environment is built from a saved copy of this process's, with every variable under either
prefix removed, so an exported setting cannot reach a drive.

R8's check on a real mount (`tests/fuse/test_integration.py`, `-m integration`) is NOT RUN by any mode:
the integration lane runs nowhere. `contracts` says so in its output.

Footprint: one child process at a time. `suite` runs pytest with `-n auto` (one worker per core, about
1.5 GB peak on a 10-core machine); every other child is serial and under 500 MB. `contracts` starts one
in-process moto server on a loopback port and stops it in the same child. Scratch goes under $TMPDIR and
is left in place for inspection; the gate deletes nothing and writes nothing to the tree. Each run keeps its
children's bytecode in one scratch directory (`pycache_prefix`), so a child never reads a stale `.pyc` left
beside a source that was edited and restored within the same second. The one
long-lived child, the mount that must refuse, runs in its own process group under a 60 s limit and the
group is killed if it is still alive.

Exit status: 0 green, 1 red, 3 could not run (a precondition missing; never read as green).
"""

from __future__ import annotations

import io
import os
import re
import sys
import json
import signal
import hashlib
import tarfile
import tomllib
import zipfile
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


SLUG = 'rename-to-terrace'
BASE = 'bf438b4'                  # the commit the cycle branch was cut from
BASE_PASSED = 731                 # the unit lane's passed count on BASE, measured by the foreman on this host
VERSION = '0.1.0.dev0'
URL = 'https://github.com/glentner/terrace'
SUBCOMMANDS = ('mount', 'umount', 'status', 'flush', 'commit', 'audit', 'config')
R1_PATHSPEC = ('.', ':!GOAL.md', ':!CHANGELOG.md', ':!spec', ':!issues/rename-to-terrace.md')
CODE_SCOPE = ('src', 'tests', 'tools', 'pyproject.toml', 'docker-compose.yml', '.github', 'uv.lock')
LINE_KEPT = ('src', 'tests', 'tools')
# What the first phase may leave for the second: a name written to a tier root, a path component, the
# attribute namespace, the marker's first line, and the upper-case prefix where no letter follows it.
CONTRACT_FORM = re.compile(r'(?<![A-Za-z0-9_])\.sosfs|user\.sosfs\.|SOSFS(?![A-Za-z])')
CONTRACT_SITE = {                  # the same, for the three files where the form is not self-evident
    'src/terrace/core/platform.py': re.compile(r"os\.path\.join\([a-z_.]+, (?:'[a-z]+', ){0,2}'sosfs'"),
    'tools/temp_site.sh': re.compile(r'<state>/sosfs/log'),
    'src/terrace/state/lifecycle.py': re.compile(r'# sosfs managed-location marker'),
}
OLD_NAME = re.compile(r'sosfs', re.IGNORECASE)
HOLLOWING = ('UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC', 'UV_OFFLINE')    # each makes a sync or lock check vacuous
PREFIXES = ('TERRACE_', 'SOSFS_')
HOME_PATH = re.compile(r'/Users/[a-z]|/home/[a-z]|~/K[B]')            # written so this line cannot match itself
DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|deferred|leaves? that behaviour',
                      re.IGNORECASE)
GUARD_TESTS = (                    # the unit tests that grade the managed tree and the attribute names
    ('tests/tier/test_posix.py', 'test_usage_excludes_internals'),
    ('tests/tier/test_posix.py', 'test_managed_internals_hidden_and_guarded'),
    ('tests/tier/test_posix.py', 'test_set_meta_refuses_internal'),
    ('tests/tier/test_s3.py', 'test_managed_internals_hidden_and_guarded'),
    ('tests/tier/test_s3.py', 'test_set_meta_refuses_internal'),
    ('tests/tier/test_archive.py', 'test_managed_internals_hidden'),
    ('tests/tier/test_archive.py', 'test_write_refuses_internal'),
    ('tests/fuse/test_adapter.py', 'test_listxattr_returns_tiering_attrs'),
    ('tests/fuse/test_adapter.py', 'test_getxattr_tiers_tracks_residency'),
    ('tests/fuse/test_adapter.py', 'test_getxattr_dirty_flag'),
    ('tests/fuse/test_adapter.py', 'test_getxattr_unknown_name_is_enoattr'),
)
MOVED = (                          # (what, old, new): every pair the CHANGELOG entry has to name
    ('distribution and import package', '`sosfs`', '`terrace`'),
    ('console script', '`sosfs`', '`terrace`'),
    ('environment prefix', '`SOSFS_`', '`TERRACE_`'),
    ('marker directory', '`.sosfs/`', '`.terrace/`'),
    ('partial directory', '`.sosfs/partial/`', '`.terrace/partial/`'),
    ('sentinel', '`.sosfs.keep`', '`.terrace.keep`'),
    ('extended attributes', '`user.sosfs.tiers`', '`user.terrace.tiers`'),
    ('extended attributes', '`user.sosfs.dirty`', '`user.terrace.dirty`'),
    ('local configuration file', '.sosfs.toml`', '.terrace.toml`'),
    ('per-user configuration', '`$XDG_CONFIG_HOME/sosfs/config.toml`', '`$XDG_CONFIG_HOME/terrace/config.toml`'),
    ('per-user configuration', '`~/.config/sosfs/config.toml`', '`~/.config/terrace/config.toml`'),
    ('per-user state', '`$XDG_STATE_HOME/sosfs`', '`$XDG_STATE_HOME/terrace`'),
    ('per-user state', '`~/.local/state/sosfs`', '`~/.local/state/terrace`'),
    ('logs', '`$XDG_STATE_HOME/sosfs/log`', '`$XDG_STATE_HOME/terrace/log`'),
    ('logs', '`~/.local/state/sosfs/log`', '`~/.local/state/terrace/log`'),
    ('system-wide configuration', '`/etc/sosfs/config.toml`', '`/etc/terrace/config.toml`'),
    ('system-wide', '`/var/log/sosfs`', '`/var/log/terrace`'),
    ('system-wide', '`/var/lib/sosfs`', '`/var/lib/terrace`'),
)
SAVED_ENV = dict(os.environ)


class Red(Exception):
    """A gate assertion failed."""


class CannotRun(Exception):
    """A precondition is missing; the gate proves nothing either way."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Red(message)
    print(f'  ok    {message}')


def scratch(label: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f'{SLUG}-{label}-', dir=SAVED_ENV.get('TMPDIR'))).resolve()


_pycache: list[str] = []


def pycache() -> str:
    """One bytecode directory per gate run, outside the tree: no child reads or leaves a `.pyc` beside a source."""
    if not _pycache:
        _pycache.append(str(scratch('pycache')))
    return _pycache[0]


def child_env(extra: dict[str, str] | None = None, drop: tuple[str, ...] = ()) -> dict[str, str]:
    """A child's environment: the saved copy, less PYTHONPATH, both prefixes and `drop`, plus `extra`."""
    env = {key: value for key, value in SAVED_ENV.items()
           if key != 'PYTHONPATH' and not key.startswith(PREFIXES) and key not in drop}
    env['PYTHONPYCACHEPREFIX'] = pycache()
    env.update(extra or {})
    return env


def isolated(python: str) -> list[str]:
    """The interpreter with `-I`, which ignores PYTHONPYCACHEPREFIX, so the prefix is passed as an option."""
    return [python, '-I', '-X', f'pycache_prefix={pycache()}']


def run(argv: list[str], env: dict[str, str] | None = None, quiet: bool = False,
        cwd: Path | None = None, echo: bool = True) -> subprocess.CompletedProcess:
    if echo:
        print(f'  run   {" ".join(argv)[:150]}', flush=True)
    proc = subprocess.run(argv, env=env if env is not None else child_env(), text=True, capture_output=True, cwd=cwd)
    if not quiet:
        for line in (proc.stdout + proc.stderr).strip().splitlines()[-6:]:
            print(f'        {line}')
    return proc


def git(*args: str) -> str:
    proc = run(['git', *args], quiet=True, echo=False)
    if proc.returncode not in (0, 1):                                  # 1 is `git grep` finding nothing
        raise CannotRun(f'git {" ".join(args)[:80]} failed: {proc.stderr.strip()[-200:]}')
    return proc.stdout


def refuse_hollowing() -> None:
    present = [name for name in HOLLOWING if SAVED_ENV.get(name)]
    if present:
        raise CannotRun(f'unset {present} first: each makes a sync or lock assertion vacuous')


_python: list[str] = []


def project_python() -> str:
    """The project environment's interpreter, after an exact sync to the lock."""
    if not _python:
        refuse_hollowing()
        proc = run(['uv', 'sync', '--locked'])
        check(proc.returncode == 0, f'uv sync --locked exits 0 (got {proc.returncode})')
        proc = run(['uv', 'run', '--locked', 'python', '-I', '-c', 'import sys; print(sys.executable)'], quiet=True)
        if proc.returncode != 0 or not proc.stdout.strip():
            raise CannotRun(f'cannot find the project interpreter: {proc.stderr.strip()[-200:]}')
        _python.append(proc.stdout.strip().splitlines()[-1])
    return _python[0]


def need_libfuse() -> None:
    """Without libfuse every tests/fuse test skips, so a count or a named test would read red for no fault."""
    probe = run([*isolated(project_python()), '-c', 'import mfusepy'], quiet=True)
    if probe.returncode != 0:
        raise CannotRun('libfuse cannot be loaded here (import mfusepy fails): the adapter tests would all skip')


def drive(source: str, args: list[str], label: str, env: dict[str, str] | None = None,
          cwd: Path | None = None, wrap: list[str] | None = None) -> dict:
    """Run a driver program under the project interpreter with `-I`; its last stdout line is JSON."""
    program = scratch(label) / f'{label}_driver.py'
    program.write_text(source)
    proc = run((wrap or []) + [*isolated(project_python()), str(program), *args], env=env, cwd=cwd)
    if proc.returncode == 3:
        raise CannotRun(f'{label}: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "exit 3"}')
    if proc.returncode != 0:
        raise Red(f'{label}: the drive exited {proc.returncode}: {proc.stderr.strip()[-300:]}')
    return json.loads(proc.stdout.strip().splitlines()[-1])


# -- JUnit ---------------------------------------------------------------------------------------------------

@dataclass
class Case:
    file: str
    name: str
    outcome: str                  # passed | skipped | failed | error
    message: str = ''
    classname: str = ''


@dataclass
class Report:
    cases: list[Case] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return len([case for case in self.cases if case.outcome == outcome])

    def find(self, file: str, name: str) -> list[Case]:
        module = file.removesuffix('.py').replace('/', '.')
        return [case for case in self.cases
                if case.name == name and (case.file == file or case.classname.startswith(module))]


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
        report.cases.append(Case(file=case.get('file') or '', name=case.get('name') or '', outcome=outcome,
                                 message=message, classname=case.get('classname') or ''))
    return report


def pytest(args: list[str], label: str,
           env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess, Report]:
    xml = scratch(label) / 'junit.xml'
    proc = run(['uv', 'run', '--locked', 'pytest', '-p', 'no:cacheprovider', '-rs', '-o', 'junit_family=xunit1',
                f'--junitxml={xml}', *args], env=env)
    return proc, junit(xml)


# -- shared reads --------------------------------------------------------------------------------------------

def base_file(path: str) -> str:
    proc = run(['git', 'show', f'{BASE}:{path}'], quiet=True, echo=False)
    if proc.returncode != 0:
        raise CannotRun(f'{BASE}:{path} is not readable; is the base commit present?')
    return proc.stdout


def old_name_lines(*pathspec: str) -> list[str]:
    return git('grep', '-nIi', '-e', 'sosfs', '-e', 'simple.overlay.service', '--', *pathspec).splitlines()


def nothing_unstaged() -> None:
    proc = run(['git', 'diff', '--quiet'], quiet=True)
    check(proc.returncode == 0, 'no tracked file differs from the index, so the tree read is the tree staged')


def snapshot(root: Path) -> dict[str, list]:
    """Every directory and file under `root`: a directory by name, a file by size and SHA-256."""
    found: dict[str, list] = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            found[name] = ['link', os.readlink(path)]
        elif path.is_dir():
            found[name] = ['dir']
        else:
            found[name] = ['file', path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()]
    return found


def spec_text(root: Path, state: bool = True) -> str:
    lines = ['[overlay]', 'name = "probe"']
    if state:
        lines.append(f'state = "{root / "state"}"')
    for name, role in (('project', 'source'), ('scratch', 'stage')):
        lines += ['', '[[tier]]', f'name = "{name}"', 'type = "posix"', f'path = "{root / name}"', f'role = "{role}"']
    return '\n'.join(lines) + '\n'


def overlay_dir(label: str, state: bool = True) -> Path:
    """A fresh overlay directory: an empty source root and a spec naming it, a stage root and a state directory."""
    root = scratch(label)
    (root / 'project').mkdir()
    (root / 'mnt').mkdir()
    (root / 'spec.toml').write_text(spec_text(root, state=state))
    return root


def site_env(root: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """A child environment whose per-user paths land inside `root`."""
    return child_env({'XDG_CONFIG_HOME': str(root / 'xdg-config'), 'XDG_STATE_HOME': str(root / 'xdg-state'),
                      **(extra or {})})


def sqlite_scalar(database: Path, statement: str) -> object:
    import sqlite3
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as conn:
        return conn.execute(statement).fetchone()[0]


# -- identity ------------------------------------------------------------------------------------------------

def mode_identity() -> None:
    """R2 and R3: the package, the distribution and the console script carry the new name."""
    check(Path('src/terrace/__init__.py').is_file(), 'src/terrace/ holds the package')
    check(not Path('src/sosfs').exists(), 'src/sosfs is gone from disk, so no stale namespace package imports')
    named = [name for name in git('ls-files').splitlines() if OLD_NAME.search(name)]
    check(not named, f'no tracked path carries the old name (got {named[:3]})')
    collapsed = git('grep', '-nI', '-E', 'TERRACE[A-Za-z]', '--', *R1_PATHSPEC).splitlines()
    check(not collapsed, f'no identifier of the collapsed form, TERRACE then a letter (got {collapsed[:3]})')
    stray = []
    for line in old_name_lines(*CODE_SCOPE):
        name, _, text = line.split(':', 2)
        text = CONTRACT_FORM.sub('', text)
        if name in CONTRACT_SITE:
            text = CONTRACT_SITE[name].sub('', text)
        if OLD_NAME.search(text) or re.search('simple.overlay.service', text, re.IGNORECASE):
            stray.append(line[:120])
    check(not stray, f'every old name left in the code scope is a contract form of the second phase (got {stray[:3]})')
    base_src = git('ls-tree', '-r', '--name-only', BASE, '--', *LINE_KEPT).splitlines()
    now_src = git('ls-files', '--', *LINE_KEPT).splitlines()
    moved = sorted(name.replace('src/sosfs/', 'src/terrace/', 1) for name in base_src)
    check(moved == sorted(now_src),
          'src/, tests/ and tools/ hold the base commit\'s files, the package directory moved')
    here = lambda name: Path(name.replace('src/sosfs/', 'src/terrace/', 1))
    changed = [name for name in base_src if base_file(name).count('\n') != here(name).read_text().count('\n')]
    check(not changed, f'no file under src/, tests/ or tools/ gained or lost a line (got {changed[:3]})')
    check(Path('tools/lint.sh').read_text() == base_file('tools/lint.sh').replace('sosfs', 'terrace'),
          'tools/lint.sh is the base commit\'s file with the name changed and nothing else: the static gate is intact')

    refuse_hollowing()
    proc = run(['uv', 'lock', '--check'])
    check(proc.returncode == 0, f'uv lock --check exits 0 (got {proc.returncode})')
    base_lock = tomllib.loads(base_file('uv.lock'))
    lock = tomllib.loads(Path('uv.lock').read_text())
    for package in base_lock['package']:
        if package['name'] == 'sosfs':
            package['name'] = 'terrace'
    by_name = lambda packages: sorted(packages, key=lambda package: (package['name'], package.get('version', '')))
    check(by_name(lock['package']) == by_name(base_lock['package']),
          'uv.lock differs from the base commit only in the name of the project\'s own entry')

    python = project_python()
    probe = ('import importlib.metadata as m, importlib.util as u, json\n'
             'def has(name):\n'
             '    try:\n'
             '        return m.version(name)\n'
             '    except m.PackageNotFoundError:\n'
             '        return None\n'
             'import terrace\n'
             'from terrace.core import exceptions\n'
             'print(json.dumps({"new": has("terrace"), "old": has("sosfs"),\n'
             '                  "old_module": u.find_spec("sosfs") is not None,\n'
             '                  "website": terrace.__website__, "app": hasattr(terrace, "TerraceApp"),\n'
             '                  "error": hasattr(exceptions, "TerraceError")}))\n')
    site = scratch('identity-site')
    seen = drive(probe, [], 'identity', env=site_env(site))
    check(seen['new'] == VERSION, f'the environment holds distribution terrace {VERSION} (got {seen["new"]})')
    check(seen['old'] is None and not seen['old_module'], 'the environment holds no old distribution and no old module')
    check(seen['app'] and seen['error'], 'the package exports TerraceApp and TerraceError')
    check(seen['website'] == URL, f'terrace.__website__ is {URL} (got {seen["website"]})')

    version_line = re.compile(r'^terrace v\d+\.\d+\.\d+', re.MULTILINE)
    console = run(['tools/temp_site.sh', 'uv', 'run', '--locked', 'terrace', '--version'])
    check(console.returncode == 0 and version_line.search(console.stdout) is not None,
          f'`uv run terrace --version` exits 0 and prints a line matching {version_line.pattern!r}')
    module = run(['tools/temp_site.sh', 'uv', 'run', '--locked', 'python', '-m', 'terrace', '--version'])
    check(module.returncode == 0 and module.stdout == console.stdout,
          '`python -m terrace --version` prints the same line')
    usage = run(['tools/temp_site.sh', 'uv', 'run', '--locked', 'terrace', '--help'], quiet=True)
    listed = [name for name in SUBCOMMANDS if re.search(rf'^  {name}\b', usage.stdout, re.MULTILINE)]
    check(usage.returncode == 0 and listed == list(SUBCOMMANDS),
          f'`terrace --help` exits 0 and lists {len(SUBCOMMANDS)} subcommands')
    check(URL in usage.stdout and OLD_NAME.search(usage.stdout) is None,
          'the help text gives the new URL and no old name')

    out = scratch('build')
    proc = run(['uv', 'build', '--out-dir', str(out)])
    check(proc.returncode == 0, f'uv build exits 0 (got {proc.returncode})')
    built = sorted(path.name for path in out.iterdir() if path.name != '.gitignore')
    wheels = [name for name in built if re.fullmatch(rf'terrace-{re.escape(VERSION)}-.+\.whl', name)]
    check(len(built) == 2 and f'terrace-{VERSION}.tar.gz' in built and len(wheels) == 1,
          f'the build produced exactly one sdist and one wheel named terrace-{VERSION} (got {built})')
    with zipfile.ZipFile(out / wheels[0]) as wheel:
        names = wheel.namelist()
        metadata = wheel.read(next(name for name in names if name.endswith('.dist-info/METADATA'))).decode()
    check(re.search(r'^Name: terrace$', metadata, re.MULTILINE) is not None,
          'the wheel\'s metadata names the distribution terrace')
    check(re.search(rf'^Project-URL: Repository, {re.escape(URL)}$', metadata, re.MULTILINE) is not None,
          'the wheel\'s metadata gives the repository URL')
    check('terrace/state/schema.sql' in names and not [name for name in names if OLD_NAME.search(name)],
          'the wheel ships terrace/state/schema.sql and no path under the old name')
    with tarfile.open(out / f'terrace-{VERSION}.tar.gz') as sdist:
        check(not [name for name in sdist.getnames() if OLD_NAME.search(name)],
              'the sdist holds no path under the old name')
    project = tomllib.loads(Path('pyproject.toml').read_text())['project']
    link, scripts = project['urls']['Repository'], project['scripts']
    check(project['name'] == 'terrace' and link == URL and scripts == {'terrace': 'terrace:main'},
          'pyproject.toml names the distribution, the console script and the repository URL')


# -- contracts -----------------------------------------------------------------------------------------------

FRESH = r'''
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
moves = []
replace = os.replace
def spy(src, dst, *args, **kwargs):
    moves.append(os.fspath(src))
    return replace(src, dst, *args, **kwargs)
os.replace = spy
from terrace.tier.spec import OverlaySpec
from terrace.engine.overlay import OverlayEngine
from terrace.state import journal, lifecycle, db
engine = OverlayEngine(OverlaySpec.load(root / 'spec.toml'))
engine.start()
engine.create('/made.txt', 0o644)
handle = engine.open('/made.txt', os.O_WRONLY)
engine.write(handle, b'payload', 0)
engine.release(handle)
engine.flush()
engine.stop()
print(json.dumps({'moves': moves, 'temp_name': journal.temp_name(3, '/data/x.bin'),
                  'new_is_temp': journal.is_temp_name('.terrace/partial/3-x.bin'),
                  'old_is_temp': journal.is_temp_name('.sosfs/partial/3-x.bin'),
                  'marker_dir': lifecycle.MARKER_DIR, 'partial_dir': journal.PARTIAL_DIR,
                  'schema_version': db.SCHEMA_VERSION}))
'''

BASE_MAKE = r'''
import importlib.metadata, json, os, sys
from pathlib import Path
root, base_src = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, base_src)
importlib.metadata.version = lambda name: '0.0.0'      # the base tree is run from an archive, not installed
import sosfs
from sosfs.tier.spec import OverlaySpec
from sosfs.engine.overlay import OverlayEngine
if not sosfs.__file__.startswith(base_src):
    sys.exit('the base package did not load from the archive')
engine = OverlayEngine(OverlaySpec.load(root / 'spec.toml'))
engine.start()
engine.create('/made.txt', 0o644)
handle = engine.open('/made.txt', os.O_WRONLY)
engine.write(handle, b'payload', 0)
engine.release(handle)
engine.flush()
engine.stop()
print(json.dumps({'package': sosfs.__file__}))
'''

OPEN_DB = r'''
import json, sys
from terrace.state.db import Database, SchemaMismatch
database = Database(sys.argv[1])
try:
    database.initialize()
    rows = database.execute(t'SELECT count(*) AS n FROM entry').fetchone()['n']
    print(json.dumps({'opened': True, 'entries': rows}))
except SchemaMismatch as error:
    print(json.dumps({'opened': False, 'error': str(error)}))
'''

S3_KEEP = r'''
import json, sys
from uuid import uuid4
from pathlib import PurePosixPath as P
try:
    import boto3
    from moto.server import ThreadedMotoServer
except ImportError as error:
    print(f'the S3 drive needs {error.name}', file=sys.stderr)
    sys.exit(3)
from obstore.store import S3Store
from terrace.tier.s3 import S3Tier, KEEP
server = ThreadedMotoServer(ip_address='127.0.0.1', port=0)
server.start()
try:
    endpoint = 'http://127.0.0.1:%d' % server.get_host_and_port()[1]
    bucket = 'gate-' + uuid4().hex[:12]
    keys = {'aws_access_key_id': 'test', 'aws_secret_access_key': 'test'}
    client = boto3.client('s3', endpoint_url=endpoint, region_name='us-east-1', **keys)
    client.create_bucket(Bucket=bucket)
    store = S3Store(bucket, prefix='space', endpoint=endpoint, region='us-east-1', access_key_id='test',
                    secret_access_key='test', client_options={'allow_http': True})
    tier = S3Tier('cloud', store)
    tier.mkdir(P('/empty'))
    found = sorted(item['Key'] for item in client.list_objects_v2(Bucket=bucket).get('Contents', []))
    print(json.dumps({'keys': found, 'KEEP': KEEP, 'children': [entry.name for entry in tier.list(P('/empty'))]}))
finally:
    server.stop()
'''

XATTR = r'''
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
try:
    from mfusepy import ENOATTR
    from terrace.fuse.adapter import OverlayOperations
except OSError as error:
    print(f'libfuse cannot be loaded: {error}', file=sys.stderr)
    sys.exit(3)
from terrace.tier.spec import OverlaySpec
from terrace.engine.overlay import OverlayEngine
(root / 'project' / 'top.txt').write_bytes(b'top-level')
engine = OverlayEngine(OverlaySpec.load(root / 'spec.toml'))
engine.start()
operations = OverlayOperations(engine)
def read(path, name):
    try:
        return operations.getxattr(path, name).decode()
    except OSError as error:
        return 'errno %d' % error.errno
seen = {'ENOATTR': ENOATTR, 'listed': sorted(operations.listxattr('/top.txt')),
        'clean': read('/top.txt', 'user.terrace.dirty')}
handle = operations.create('/dirty.txt', 0o644)
operations.write('/dirty.txt', b'x', 0, handle)
seen['dirty'] = read('/dirty.txt', 'user.terrace.dirty')
seen['tiers'] = read('/dirty.txt', 'user.terrace.tiers')
seen['holders'] = [residency.tier for residency in engine.residencies('/dirty.txt')]
seen['other'] = {}
for name in sys.argv[2:]:
    try:
        seen['other'][name] = 'served: %r' % operations.getxattr('/dirty.txt', name)
    except OSError as error:
        seen['other'][name] = error.errno
operations.release('/dirty.txt', handle)
engine.stop()
print(json.dumps(seen))
'''

CONFIG = r'''
import json
from terrace.core.config import config, blame
print(json.dumps({'level': str(config.logging.level), 'blame': blame(config, 'logging', 'level')}))
'''

PATHS = r'''
import json
from terrace.core import platform
print(json.dumps({scope: {key: platform.path[scope][key] for key in ('config', 'log', 'state')}
                  for scope in ('system', 'user', 'local')}))
'''


def strip_sql_comments(text: str) -> str:
    """SQL with `--` comments and blank lines removed; a `--` inside a quoted string is kept."""
    out = []
    for line in text.splitlines():
        kept, quote = [], ''
        for index, char in enumerate(line):
            if quote:
                quote = '' if char == quote else quote
            elif char in '\'"':
                quote = char
            elif line.startswith('--', index):
                break
            kept.append(char)
        if ''.join(kept).strip():
            out.append(''.join(kept).rstrip())
    return '\n'.join(out)


def refused_mount(python: str, root: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run `terrace mount <spec> <mnt> -f` in its own process group; a mount that serves is killed and is red."""
    argv = [*isolated(python), '-m', 'terrace', 'mount', str(root / 'spec.toml'), str(root / 'mnt'), '-f']
    print(f'  run   {" ".join(argv)[:150]}', flush=True)
    proc = subprocess.Popen(argv, env={**env, 'RENAME_GATE_CHILD': root.name}, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        for sent, wait in ((signal.SIGINT, 20), (signal.SIGKILL, 10)):
            try:
                os.killpg(proc.pid, sent)
                proc.communicate(timeout=wait)
                break
            except (subprocess.TimeoutExpired, ProcessLookupError):
                continue
        raise Red('the mount did not refuse within 60 s: it was serving, and has been stopped')
    for line in (out + err).strip().splitlines()[-3:]:
        print(f'        {line}')
    return proc.returncode, out + err


def mode_contracts() -> None:
    """R4 to R9: every name the product writes, reads or serves is the new one, and the state store did not move."""
    left = old_name_lines(*CODE_SCOPE)
    check(not left, f'no old name in {", ".join(CODE_SCOPE)} (got {len(left)} lines, first {left[:2]})')
    python = project_python()
    need_libfuse()

    # R4: markers, the partial directory and the S3 sentinel
    fresh = overlay_dir('fresh')
    seen = drive(FRESH, [str(fresh)], 'fresh', env=site_env(fresh))
    for tier in ('project', 'scratch'):
        check((fresh / tier / '.terrace' / 'marker').is_file() and not (fresh / tier / '.sosfs').exists(),
              f'the {tier} root holds .terrace/marker and nothing under the old directory')
    temps = [move for move in seen['moves'] if '/.terrace/partial/' in move]
    roots = (fresh / 'project', fresh / 'scratch')
    check(bool(temps) and all(Path(move).parent.parent.parent in roots for move in temps),
          f'a transfer temporary was written under <tier root>/.terrace/partial/ ({len(temps)} seen)')
    named = seen['temp_name'] == '.terrace/partial/3-x.bin'
    check(named and seen['new_is_temp'] is True and seen['old_is_temp'] is False,
          'a name under .terrace/partial/ is recognized as a transfer temporary and one under the old directory is not')
    check(seen['marker_dir'] == '.terrace' and seen['partial_dir'] == '.terrace/partial',
          'MARKER_DIR and PARTIAL_DIR carry the new name')
    keep = drive(S3_KEEP, [], 's3-keep', env=site_env(fresh))
    check(keep['KEEP'] == '.terrace.keep' and keep['keys'] == ['space/empty/.terrace.keep'] and keep['children'] == [],
          f'an empty S3 directory is one .terrace.keep object and lists as empty (got {keep["keys"]})')
    proc, report = pytest(sorted({file for file, _ in GUARD_TESTS}) + ['-m', 'unit'], 'guards')
    for file, name in GUARD_TESTS:
        cases = report.find(file, name)
        check(len(cases) == 1 and cases[0].outcome == 'passed',
              f'{file}::{name} passed ({[case.outcome for case in cases] or "not collected"})')
    check(report.count('failed') == 0 and report.count('error') == 0,
          'the tier and adapter modules: 0 failed, 0 errors')

    # R9: the state store
    state_db = fresh / 'state' / 'state.db'
    check(state_db.is_file() and sorted(path.name for path in (fresh / 'state').glob('*.db')) == ['state.db'],
          'the state database is state.db')
    check(sqlite_scalar(state_db, 'PRAGMA user_version') == 2 and seen['schema_version'] == 2,
          'PRAGMA user_version reads 2 and SCHEMA_VERSION is 2')
    check(strip_sql_comments(Path('src/terrace/state/schema.sql').read_text())
          == strip_sql_comments(base_file('src/sosfs/state/schema.sql')),
          'schema.sql differs from the base commit in comments only')
    old = overlay_dir('base-overlay')
    archive = scratch('base-tree')
    raw = subprocess.run(['git', 'archive', BASE, 'src'], capture_output=True)
    if raw.returncode != 0:
        raise CannotRun(f'git archive {BASE} failed: {raw.stderr.decode()[-200:]}')
    with tarfile.open(fileobj=io.BytesIO(raw.stdout)) as tree:
        tree.extractall(archive, filter='data')
    made = drive(BASE_MAKE, [str(old), str(archive / 'src')], 'base-make', env=site_env(old))
    check(made['package'].startswith(str(archive)), f'the base commit\'s own code wrote the old overlay ({BASE})')
    check(all((old / tier / '.sosfs' / 'marker').is_file() for tier in ('project', 'scratch')),
          'the old overlay\'s tier roots hold a marker under the old directory name only')
    old_db = old / 'state' / 'state.db'
    opened = drive(OPEN_DB, [str(old_db)], 'open-db', env=site_env(old))
    check(opened.get('opened') is True and opened['entries'] >= 2,
          f'a state database written by the base commit opens without SchemaMismatch ({opened})')
    bare = overlay_dir('no-state', state=False)
    code, text = refused_mount(python, bare, site_env(bare))
    check(code != 0 and '[overlay] requires a state directory' in text,
          'a spec without [overlay].state is refused by name')

    # R5: the old overlay is refused through the real CLI, and its tier roots are left as they were
    before = {tier: snapshot(old / tier) for tier in ('project', 'scratch')}
    rows = sqlite_scalar(old_db, 'SELECT count(*) FROM residency')
    tiers = sqlite_scalar(old_db, 'SELECT count(*) FROM tier')
    check(tiers == 2 and rows >= 2 and sqlite_scalar(old_db, 'SELECT shutdown_state FROM overlay') == 'clean',
          f'the old state database holds both tier rows, {rows} residency rows and a clean shutdown')
    code, text = refused_mount(python, old, site_env(old))
    check(code != 0, f'the mount exits non-zero (got {code})')
    check("Tier 'project' marker is missing — audit required" in text,
          'the mount fails with the marker-missing message')
    after = {tier: snapshot(old / tier) for tier in ('project', 'scratch')}
    check(after == before, 'both tier roots are unmodified: same names, sizes and digests, the old markers included')
    check(not [name for tier in after for name in after[tier] if '.terrace' in name],
          'no .terrace directory was created')
    check(sqlite_scalar(old_db, 'SELECT count(*) FROM residency') == rows, 'no residency row was adopted')
    settled = sqlite_scalar(old_db, 'SELECT shutdown_state FROM overlay')
    print(f'  info  after the refusal shutdown_state reads {settled!r}')

    # R6: the environment prefix
    site = scratch('config')
    local = site / 'named.toml'
    local.write_text('[logging]\nlevel = "trace"\n')
    broken = site / 'broken.toml'
    broken.write_text('this is = not [toml\n')
    inside = lambda *pairs: ['tools/temp_site.sh', 'env', *pairs]
    control = drive(CONFIG, [], 'config', wrap=inside())
    by_prefix = drive(CONFIG, [], 'config', wrap=inside('TERRACE_LOGGING_LEVEL=DEBUG'))
    check(by_prefix['level'].upper() == 'DEBUG' and by_prefix['blame'] == 'from: TERRACE_LOGGING_LEVEL',
          f'TERRACE_LOGGING_LEVEL sets the level, with provenance "from: TERRACE_LOGGING_LEVEL" (got {by_prefix})')
    check(control['level'].upper() != 'DEBUG' and control['blame'] != by_prefix['blame'],
          f'the control differs ({control})')
    by_file = drive(CONFIG, [], 'config', wrap=inside(f'TERRACE_CONFIG_FILE={local}'))
    check(by_file['level'] == 'trace' and by_file['blame'] == f'from: {local}',
          f'TERRACE_CONFIG_FILE names the local configuration file (got {by_file})')
    for pairs in (('SOSFS_LOGGING_LEVEL=DEBUG', ), (f'SOSFS_CONFIG_FILE={local}', ),
                  ('LOGGING_LEVEL=DEBUG', 'XTERRACE_LOGGING_LEVEL=DEBUG')):
        check(drive(CONFIG, [], 'config', wrap=inside(*pairs)) == control,
              f'{" ".join(pairs)[:60]} has no effect on the configuration')
    exported = child_env({'TERRACE_LOGGING_LEVEL': 'DEBUG', 'TERRACE_ANYTHING': '1'})
    leak = run(['tools/temp_site.sh', 'env'], env=exported, quiet=True)
    names = [line.split('=', 1)[0] for line in leak.stdout.splitlines()]
    leaked = [name for name in names if name.startswith('TERRACE_')]
    check(leak.returncode == 0 and 'XDG_STATE_HOME' in names and not leaked,
          'tools/temp_site.sh scrubs every TERRACE_* variable')
    poisoned = child_env({'TERRACE_CONFIG_FILE': str(broken)})
    bites = run([*isolated(python), '-m', 'terrace', '--version'], quiet=True,
                env=site_env(site, {'TERRACE_CONFIG_FILE': str(broken)}))
    check(bites.returncode != 0, 'a malformed TERRACE_CONFIG_FILE stops the CLI, so the next check can fail')
    proc, report = pytest(['tests/test_cli.py', '-m', 'unit'], 'scrub', env=poisoned)
    check(len(report.cases) >= 8 and report.count('passed') == len(report.cases),
          f'with that variable exported, tests/test_cli.py passes whole ({len(report.cases)} cases): '
          'the fixture and the CLI helper scrub TERRACE_*')
    endpoint = 'http://127.0.0.1:9'
    proc, report = pytest(['tests/tier/test_s3_integration.py', '-m', 'integration'], 'minio',
                          env=child_env({'TERRACE_TEST_MINIO_ENDPOINT': endpoint}))
    named = [case for case in report.cases
             if case.outcome == 'skipped' and case.message.endswith(f'endpoint at {endpoint}')]
    check(bool(report.cases) and len(named) == len(report.cases),
          f'the S3 integration suite reads its endpoint from TERRACE_TEST_MINIO_ENDPOINT ({len(named)} skips name it)')

    # R7: the path namespace
    place = scratch('paths')
    for name in ('cwd', 'home', 'xdg-config', 'xdg-state'):
        (place / name).mkdir()
    system = {'config': '/etc/terrace/config.toml', 'log': '/var/log/terrace', 'state': '/var/lib/terrace'}
    set_user = {'config': f'{place}/xdg-config/terrace/config.toml', 'log': f'{place}/xdg-state/terrace/log',
                'state': f'{place}/xdg-state/terrace'}
    unset_user = {'config': f'{place}/home/.config/terrace/config.toml',
                  'log': f'{place}/home/.local/state/terrace/log', 'state': f'{place}/home/.local/state/terrace'}
    no_xdg = child_env({'HOME': str(place / 'home')}, drop=('XDG_CONFIG_HOME', 'XDG_STATE_HOME'))
    for label, env, user in (('set', site_env(place), set_user), ('unset', no_xdg, unset_user)):
        paths = drive(PATHS, [], 'paths', env=env, cwd=place / 'cwd')
        local_paths = {'config': f'{place}/cwd/.terrace.toml', 'log': user['log'], 'state': user['state']}
        check(paths == {'system': system, 'user': user, 'local': local_paths},
              f'with the XDG variables {label}, the nine paths sit under terrace ({paths["user"]["config"]})')
        check(Path(user['log']).is_dir(), f'and the first import created {user["log"]}')
    first_run = 'uv run --locked terrace --version >/dev/null && find . -type d | sort'
    first = run(['tools/temp_site.sh', 'sh', '-c', first_run], quiet=True)
    made = first.stdout.split()
    check(first.returncode == 0 and made == ['.', './config', './state', './state/terrace', './state/terrace/log'],
          f'a first run under tools/temp_site.sh creates state/terrace/log and no other directory (got {made})')

    # R8: the attribute names, without a mount
    attrs = overlay_dir('xattr')
    others = ['user.sosfs.dirty', 'user.sosfs.tiers', 'user.terrace.other', 'user.terrace', 'terrace.dirty',
              'user.something.else']
    seen = drive(XATTR, [str(attrs), *others], 'xattr', env=site_env(attrs))
    check(seen['listed'] == ['user.terrace.dirty', 'user.terrace.tiers'],
          f'listxattr gives the two names (got {seen["listed"]})')
    check(seen['dirty'] == '1' and seen['clean'] == '0',
          f'user.terrace.dirty reads 1 for a dirty file, 0 for a clean one (got {seen["dirty"]!r}, {seen["clean"]!r})')
    check(bool(seen['holders']) and seen['tiers'] == ','.join(seen['holders']),
          f'user.terrace.tiers lists the tiers holding a copy ({seen["tiers"]})')
    check(all(seen['other'][name] == seen['ENOATTR'] for name in others),
          f'every other name raises ENOATTR ({seen["other"]})')
    print('  info  R8 on a real mount (tests/fuse/test_integration.py, -m integration): NOT RUN; '
          'the integration lane runs nowhere')


# -- census, docs, ledger ------------------------------------------------------------------------------------

def mode_census() -> None:
    """R1, as the GOAL writes it."""
    lines = old_name_lines(*R1_PATHSPEC)
    check(not lines, f'the census prints nothing (got {len(lines)} lines, first {lines[:3]})')
    named = [name for name in git('ls-files').splitlines() if OLD_NAME.search(name)]
    check(not named, f'git ls-files names no old-name path (got {named[:3]})')
    check(git('hash-object', 'GOAL.md').strip() == git('rev-parse', f'{BASE}:GOAL.md').strip() != '',
          'the root GOAL.md is byte-identical to the base commit')
    collapsed = git('grep', '-nI', '-E', 'TERRACE[A-Za-z]', '--', *R1_PATHSPEC).splitlines()
    check(not collapsed, f'no identifier of the collapsed form (got {collapsed[:3]})')
    check(git('ls-files', '-s', 'CLAUDE.md').startswith('120000'), 'CLAUDE.md is still a symbolic link')


def section(text: str, heading: str, level: str) -> str:
    """The body under `heading`, up to the next heading of the same or a higher level."""
    start = re.search(rf'^{re.escape(heading)}[ \t]*$', text, re.MULTILINE)
    if start is None:
        return ''
    rest = text[start.end():]
    stop = re.search(rf'^#{{1,{len(level)}}} ', rest, re.MULTILINE)
    return rest if stop is None else rest[:stop.start()]


def mode_docs() -> None:
    """R11, and the record R8 asks for."""
    changed = section(section(Path('CHANGELOG.md').read_text(), '## [Unreleased]', '##'), '### Changed', '###')
    check(bool(changed.strip()), 'CHANGELOG.md has a `### Changed` section under `[Unreleased]`')
    flat = ' '.join(changed.split())
    check(re.search(r'\bbreaking\b', flat, re.IGNORECASE) is not None and 'no migration path' in flat,
          'the entry announces a breaking change with no migration path')
    bullets = [' '.join(bullet.split()) for bullet in re.split(r'\n\s*- ', changed)]
    for what, old, new in MOVED:
        said = [bullet for bullet in bullets if what in bullet and '\u2192' in bullet
                and old in bullet.split('\u2192', 1)[0] and new in bullet.split('\u2192', 1)[1]]
        check(bool(said), f'one bullet names the {what}, {old} before the arrow and {new} after it')
    rows = [line for line in Path('PLAN.md').read_text().splitlines() if line.startswith('| Package / entry point |')]
    check(len(rows) == 1, 'PLAN.md has one technology row for the package and entry point')
    cells = [cell.strip() for cell in rows[0].strip('|').split('|')]
    check(cells[1] == '**`terrace`**', f'the row names terrace (got {cells[1]})')
    check(re.search(r'overturn', cells[2]) is not None and 'GOAL.md' in cells[2] and len(cells[2]) > 120,
          'the row states why the earlier decision was overturned')
    agents = Path('AGENTS.md').read_text()
    notice = r'pending rename|being renamed|destination name|queued early|rename-to-terrace'
    pending = re.findall(notice, agents, re.IGNORECASE)
    check(not pending, f'AGENTS.md no longer describes the rename as pending (got {pending[:3]})')
    record = ' '.join(Path(f'spec/{SLUG}/PLAN.md').read_text().split())
    check(re.search(r'real-mount check.{0,160}?\*\*not run\*\*', record) is not None,
          f'spec/{SLUG}/PLAN.md records that the real-mount check was **not run**')


def mode_ledger() -> None:
    """Every deferral phrase in a TECH.md phase body names an issues/ seed that ROADMAP.md lists."""
    tech = Path(f'spec/{SLUG}/TECH.md').read_text().split('\n---\n', 2)[-1]
    roadmap = Path('ROADMAP.md').read_text()
    found = 0
    for line in tech.splitlines():
        if line.lstrip().startswith('#') or not DEFERRAL.search(line):
            continue
        found += 1
        seeds = re.findall(r'issues/([a-z0-9.-]+)\.md', line)
        check(bool(seeds), f'a deferral names a seed: {line.strip()[:90]!r}')
        for seed in seeds:
            check(Path(f'issues/{seed}.md').is_file(), f'issues/{seed}.md exists')
            check(re.search(rf'^\*\*Seed:\*\* \[`issues/{re.escape(seed)}\.md`\]', roadmap, re.MULTILINE) is not None,
                  f'ROADMAP.md has an entry whose seed is issues/{seed}.md')
    check(found >= 1, f'the phase bodies carry at least the one deferral this cycle files ({found} lines checked)')


# -- suite, prepush ------------------------------------------------------------------------------------------

def mode_suite() -> None:
    """R10: the profile's verify, with the base commit's passed count."""
    refuse_hollowing()
    need_libfuse()
    proc = run(['tools/lint.sh'])
    check(proc.returncode == 0, f'tools/lint.sh exits 0 (got {proc.returncode})')
    proc, report = pytest(['-n', 'auto', '-m', 'unit'], 'suite')
    for outcome in ('failed', 'error', 'skipped'):
        check(report.count(outcome) == 0, f'unit lane: 0 {outcome} (got {report.count(outcome)})')
    check(proc.returncode == 0, f'pytest exits 0 (got {proc.returncode})')
    check(report.count('passed') == BASE_PASSED,
          f'unit lane: {BASE_PASSED} passed, the base commit\'s count (got {report.count("passed")})')
    model = [case for case in report.cases if 'test_model' in (case.file + case.classname)]
    check(bool(model) and all(case.outcome == 'passed' for case in model),
          'the stateful model test reports no divergence')


def mode_prepush() -> None:
    """Before the push: a clean tree, nothing naming a home directory, and the census."""
    check(git('status', '--porcelain').strip() == '', 'the working tree is clean')
    text = git('log', '--format=%B', f'{BASE}..HEAD') + git('diff', f'{BASE}..HEAD')
    hits = sorted(set(HOME_PATH.findall(text)))
    check(not hits, f'no home-directory path in the branch diff or commit messages (got {hits})')
    mode_census()


def mode_all() -> None:
    for mode in (mode_identity, mode_contracts, mode_census, mode_docs, mode_ledger, mode_suite):
        print(f'[{mode.__name__.removeprefix("mode_")}]', flush=True)
        mode()


MODES = {'identity': mode_identity, 'contracts': mode_contracts, 'census': mode_census, 'docs': mode_docs,
         'ledger': mode_ledger, 'suite': mode_suite, 'prepush': mode_prepush, 'all': mode_all}


def main(argv: list[str]) -> int:
    if not argv or any(arg not in MODES for arg in argv):
        print(__doc__)
        return 3
    root = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True).stdout.strip()
    if not root:
        print('CANNOT RUN: not inside a git worktree', file=sys.stderr)
        return 3
    os.chdir(root)
    for name in argv:
        print(f'[{name}]', flush=True)
        try:
            nothing_unstaged()
            MODES[name]()
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
