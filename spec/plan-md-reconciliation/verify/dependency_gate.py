# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Phase gate for the plan-md-reconciliation cycle.

Usage (from the cycle worktree root, one mode or several in order; the first red stops the run):

    uv run --no-project --python 3.15 python -I spec/plan-md-reconciliation/verify/dependency_gate.py MODE [MODE ...]

Modes:
    lock     R1      pyproject.toml and uv.lock name no `rich`; pyproject.toml differs from the base only by
                     the `rich` line; uv.lock's diff from the base only deletes lines; every package left in
                     the lock keeps its base version and source, and the removed set is exactly what only
                     `rich` reached in the base lock's graph
    text     R3      no live file outside spec/, issues/ and ROADMAP.md names `rich`; AGENTS.md's Code style
                     still says formatting goes through `cmdkit.ansi`; every commit that touches
                     pyproject.toml or uv.lock also touches AGENTS.md, and the staged change keeps them
                     together; the cycle changes only AGENTS.md (its Code style section), pyproject.toml,
                     uv.lock, its own spec/ directory, issues/*.md and ROADMAP.md (so PLAN.md, src/, tests/
                     and ci.yml stay as the base has them)
    guards   R4-R7   the PLAN.md guards, each as the GOAL words it: R4 no fsck, the CLI line and the CLI
                     surface table each name the seven subcommands `terrace --help` prints (driven through
                     tools/temp_site.sh), the umount row takes the spec and names the pidfile and the
                     cross-host guard; R5 the Python-baseline row marks frozendict as intent, no frozendict
                     in src/ or pyproject.toml, no rich in PLAN.md; R6 the policy rate-limit sentence and
                     the FUSE, crash-recovery and CI-matrix bullets each name a test path or seed, a seed
                     where they mark coverage unbuilt, and every one named exists; R7 every `Phase N` line
                     names only phases the outline dates, the outline calls ROADMAP.md the ordered index of
                     seeded cycles, and PLAN.md names no wip branch
    ledger           every deferral phrase in TECH.md's phase bodies names an issues/ seed that exists and
                     has a ROADMAP.md entry
    build    R2      `uv sync --locked`, `rich` not importable, `uv build --out-dir` into the scratch
                     directory with wheel and sdist metadata requiring exactly the base's runtime
                     dependencies less `rich`, 851 unit tests collected, then the profile's verify
                     (`tools/lint.sh && uv run pytest -n auto -m unit`) with 851 test cases in its JUnit
                     report, none failed or erroring; the tracked tree is unchanged by the run
    prepush          a clean tree, then lock, text and guards

Committed state: lock, text, guards and ledger read the index (`git show :path`, `git grep --cached`), so a phase
stages before it runs them; build runs on the working tree and first requires it to equal the index.

Base: `$GATE_BASE`, else `git merge-base HEAD origin/main`. Never a branch commit, so the gate survives the
landing squash (publish sets GATE_BASE=HEAD^).

Scratch: `$GATE_SCRATCH`, else `.local/plan-md-reconciliation/` beside the main checkout's `.git` (git-ignored).
The run log, the JUnit report and the built distributions land there, one stamped name per run, and are never
deleted. Nothing is written to the worktree.

Footprint: one subprocess at a time. build's verify runs pytest with `-n auto` (one worker per core, about
1.5 GB peak on a 10-core machine); every other step is a single process under 300 MB. build may download the
locked set and hatchling into uv's cache. The gate never pushes and never commits.

Exit status: 0 green, 1 red, 3 could not run (a precondition missing; never read as green).
"""

from __future__ import annotations

import os
import re
import sys
import time
import tomllib
import zipfile
import tarfile
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


SLUG = 'plan-md-reconciliation'
BASE_BRANCH = 'origin/main'
UNIT_COUNT = 851              # AGENTS.md § Verification; `pytest -m unit --collect-only` at the base (GOAL R2)
SUBCOMMANDS = ('mount', 'umount', 'status', 'flush', 'commit', 'audit', 'config')
REQUIRED = ('AGENTS.md', 'pyproject.toml', 'uv.lock')
HOLLOWING = ('UV_FROZEN', 'UV_NO_SYNC')     # each makes `uv sync --locked` or `uv run`'s sync vacuous
DEFERRAL = re.compile(r'do not fix here|known limitation|follow-up|left for later|deferred', re.IGNORECASE)
UMOUNT_PHRASES = (
    "the daemon's pidfile in the spec's state directory records the mounting host",
    'that is what makes the cross-host guard possible',
)
CODE_STYLE_PHRASE = 'formatting goes through `cmdkit.ansi`'


class Red(Exception):
    """A check failed."""


class CannotRun(Exception):
    """A precondition is missing; the gate says nothing about the tree."""


class Log:
    """Print to stdout and to the run log."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.file = path.open('a')

    def __call__(self, text: str) -> None:
        print(text, flush=True)
        self.file.write(text + '\n')
        self.file.flush()


def run(*args: str, check: bool = False, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise CannotRun(f'{" ".join(args)} exited {proc.returncode}: {proc.stderr.strip()[-400:]}')
    return proc


def git(*args: str) -> str:
    return run('git', *args, check=True).stdout


def grep_cached(*args: str) -> list[str]:
    """`git grep --cached`; returns the matching lines (exit 1 is no match, anything else cannot run)."""
    proc = run('git', 'grep', '-n', '--cached', *args)
    if proc.returncode not in (0, 1):
        raise CannotRun(f'git grep {" ".join(args)} exited {proc.returncode}: {proc.stderr.strip()}')
    return proc.stdout.splitlines()


def show(rev: str, file: str) -> str:
    """A file at a commit, or in the index when `rev` is empty."""
    return git('show', f'{rev}:{file}')


def norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text)


def md_section(text: str, title: str, level: str = '') -> str:
    """The body of a Markdown section: an ATX `## title` (level '##') or a setext `title` / `---` heading."""
    lines = text.splitlines()
    if level:
        starts = [i for i, line in enumerate(lines) if line == f'{level} {title}']
        if len(starts) != 1:
            raise CannotRun(f'expected one "{level} {title}" heading, found {len(starts)}')
        start = starts[0] + 1
        # only a heading of the same level ends it: a `# comment` line inside a fenced block is not a heading
        end = next((i for i in range(start, len(lines)) if lines[i].startswith(f'{level} ')), len(lines))
        return '\n'.join(lines[start:end])
    starts = [i for i in range(len(lines) - 1)
              if lines[i] == title and re.fullmatch(r'-{3,}', lines[i + 1])]
    if len(starts) != 1:
        raise CannotRun(f'expected one setext "{title}" heading, found {len(starts)}')
    start = starts[0] + 2
    end = next((i for i in range(start, len(lines) - 1)
                if lines[i] and re.fullmatch(r'[-=]{3,}', lines[i + 1]) and len(lines[i + 1]) >= 3
                and not lines[i].startswith(('|', '*', '-', ' '))), len(lines))
    return '\n'.join(lines[start:end])


def bullets(section: str) -> list[str]:
    """Top-level `* ` bullets of a section, continuation lines joined."""
    out: list[str] = []
    for line in section.splitlines():
        if line.startswith('* '):
            out.append(line[2:])
        elif out and line.startswith('  ') and line.strip():
            out[-1] += ' ' + line.strip()
        elif not line.strip():
            continue
        elif out and not line.startswith('  '):
            out.append('')    # a paragraph ends the last bullet
    return [b for b in out if b]


def dep_name(spec: str) -> str:
    return re.split(r'[\s\[<>=!~;@]', spec, maxsplit=1)[0].lower().replace('_', '-').replace('.', '-')


def gate_base() -> str:
    base = os.environ.get('GATE_BASE') or git('merge-base', 'HEAD', BASE_BRANCH).strip()
    return git('rev-parse', '--verify', f'{base}^{{commit}}').strip()


# --------------------------------------------------------------------------------------------------- lock (R1)

def lock_graph_reach(lock: dict, root: str, drop: tuple[str, str] | None) -> set[str]:
    """Package names reachable from `root` through dependencies, extras and dependency groups.

    `drop` removes one edge (parent, child) before the walk. Every extra and group of every package is followed,
    which over-approximates what uv keeps: an over-approximation can only make the expected removed set smaller,
    so a lock that removes more than `rich` reached alone goes red rather than green.
    """
    packages = {p['name']: p for p in lock['package']}
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        pkg = packages[name]
        edges = list(pkg.get('dependencies', []))
        for group in (pkg.get('optional-dependencies') or {}).values():
            edges += group
        for group in (pkg.get('dev-dependencies') or {}).values():
            edges += group
        for edge in edges:
            if drop and (name, edge['name']) == drop:
                continue
            todo.append(edge['name'])
    return seen


def mode_lock(log: Log, base: str) -> None:
    hits = grep_cached('-w', 'rich', '--', 'pyproject.toml', 'uv.lock')
    if hits:
        raise Red('R1: `rich` still named: ' + '; '.join(hits[:5]))
    log('ok   R1 git grep -w rich -- pyproject.toml uv.lock: nothing')

    old_py, new_py = tomllib.loads(show(base, 'pyproject.toml')), tomllib.loads(show('', 'pyproject.toml'))
    old_deps = old_py['project']['dependencies']
    if not any(dep_name(d) == 'rich' for d in old_deps):
        raise CannotRun('precondition: the base declares no rich; wrong base?')
    want = [d for d in old_deps if dep_name(d) != 'rich']
    log(f'     precondition: base declares {old_deps}')
    if new_py['project']['dependencies'] != want:
        raise Red(f'R1: [project] dependencies are {new_py["project"]["dependencies"]}, want {want}')
    old_py['project']['dependencies'] = want
    if old_py != new_py:
        changed = sorted(k for k in set(old_py) | set(new_py) if old_py.get(k) != new_py.get(k))
        raise Red(f'R1: pyproject.toml changes more than the rich line, in {changed}')
    log('ok   R1 pyproject.toml: the base less `rich`, every other key and group unchanged')

    numstat = git('diff', '--cached', '--numstat', base, '--', 'pyproject.toml', 'uv.lock').splitlines()
    stat = {line.split('\t')[2]: (int(line.split('\t')[0]), int(line.split('\t')[1])) for line in numstat}
    for file in ('pyproject.toml', 'uv.lock'):
        added, deleted = stat.get(file, (0, 0))
        if added or not deleted:
            raise Red(f'R1: {file} diff from the base adds {added} and deletes {deleted} lines; want 0 and > 0')
    log(f'ok   R1 diff from the base only deletes: pyproject.toml -{stat["pyproject.toml"][1]}, '
        f'uv.lock -{stat["uv.lock"][1]}')

    old_lock, new_lock = tomllib.loads(show(base, 'uv.lock')), tomllib.loads(show('', 'uv.lock'))
    for key in sorted((set(old_lock) | set(new_lock)) - {'package'}):
        if old_lock.get(key) != new_lock.get(key):
            raise Red(f'R1: uv.lock header `{key}` changed: {old_lock.get(key)!r} -> {new_lock.get(key)!r}')
    old_pk = {p['name']: (p['version'], str(p.get('source'))) for p in old_lock['package']}
    new_pk = {p['name']: (p['version'], str(p.get('source'))) for p in new_lock['package']}
    moved = {n: (old_pk.get(n), v) for n, v in new_pk.items() if old_pk.get(n) != v}
    if moved:
        raise Red(f'R1: packages not at their base version and source: {moved}')
    project = new_py['project']['name']
    expected = lock_graph_reach(old_lock, project, drop=(project, 'rich'))
    removed = sorted(set(old_pk) - set(new_pk))
    log(f'     precondition: base lock {len(old_pk)} packages, rich {old_pk.get("rich")}; '
        f'removed {removed}')
    if set(new_pk) != expected:
        raise Red(f'R1: lock holds {sorted(set(new_pk) - expected)} beyond, and lacks '
                  f'{sorted(expected - set(new_pk))} of, what the base graph reaches without rich')
    log(f'ok   R1 uv.lock: {len(new_pk)} packages, each at its base version; removed exactly what only rich '
        f'reached ({len(removed)})')

    old_root = next(p for p in old_lock['package'] if p['name'] == project)
    new_root = next(p for p in new_lock['package'] if p['name'] == project)
    old_root['dependencies'] = [d for d in old_root['dependencies'] if d['name'] != 'rich']
    old_root['metadata']['requires-dist'] = [d for d in old_root['metadata']['requires-dist'] if d['name'] != 'rich']
    if old_root != new_root:
        raise Red(f'R1: the {project} lock entry differs from the base by more than rich')
    log(f'ok   R1 uv.lock `{project}` entry: the base less rich, groups unchanged')


# ---------------------------------------------------------------------------------------------------- text (R3)

def touched(commit: str) -> set[str]:
    return set(git('diff-tree', '--no-commit-id', '--name-only', '-r', commit).split())


def help_subcommands(log: Log) -> list[str]:
    proc = run('tools/temp_site.sh', 'uv', 'run', '--locked', 'terrace', '--help')
    if proc.returncode != 0:
        raise CannotRun(f'terrace --help exited {proc.returncode}: {proc.stderr.strip()[-400:]}')
    lines = proc.stdout.splitlines()
    try:
        start = lines.index('Commands:') + 1
    except ValueError:
        raise CannotRun('terrace --help printed no "Commands:" block') from None
    names = []
    for line in lines[start:]:
        if not line.strip():
            break
        names.append(line.split()[0])
    log(f'     precondition: terrace --help lists {names}')
    return names


def mode_text(log: Log, base: str) -> None:
    # R3
    hits = grep_cached('-w', 'rich', '--', ':!spec', ':!issues', ':!ROADMAP.md')
    if hits:
        raise Red('R3: live files still name `rich`: ' + '; '.join(hits[:5]))
    log('ok   R3 git grep -w rich outside spec/, issues/, ROADMAP.md: nothing')
    agents = show('', 'AGENTS.md')
    if CODE_STYLE_PHRASE not in norm(md_section(agents, 'Code style', '##')):
        raise Red(f'R3: AGENTS.md § Code style no longer says "{CODE_STYLE_PHRASE}"')
    log(f'ok   R3 AGENTS.md § Code style says "{CODE_STYLE_PHRASE}"')

    changed = set(git('diff', '--cached', '--name-only', base).split())
    missing = [f for f in REQUIRED if f not in changed]
    if missing:
        raise Red(f'R1/R3: the cycle does not change {missing}')
    commits = git('log', '--format=%H', f'{base}..HEAD', '--', 'pyproject.toml', 'uv.lock').split()
    for commit in commits:
        lacking = [f for f in REQUIRED if f not in touched(commit)]
        if lacking:
            raise Red(f'R3: commit {commit[:9]} changes the dependency without {lacking} (same-commit rule)')
    staged = set(git('diff', '--cached', '--name-only', 'HEAD').split())
    if staged & {'pyproject.toml', 'uv.lock'} and not set(REQUIRED) <= staged:
        raise Red(f'R3: the staged change holds {sorted(staged & set(REQUIRED))} of {list(REQUIRED)}')
    log(f'ok   R3 same commit: {len(commits)} commit(s) and the staged change keep {list(REQUIRED)} together')

    def allowed(file: str) -> bool:
        return (file in REQUIRED or file.startswith(f'spec/{SLUG}/') or file == 'ROADMAP.md'
                or re.fullmatch(r'issues/[^/]+\.md', file) is not None)
    stray = sorted(f for f in changed if not allowed(f))
    if stray:
        raise Red(f'scope: the cycle changes files outside its list: {stray}')
    old_agents = show(base, 'AGENTS.md')
    cut = md_section(old_agents, 'Code style', '##'), md_section(agents, 'Code style', '##')
    if old_agents.replace(cut[0], '') != agents.replace(cut[1], ''):
        raise Red('scope: AGENTS.md changes outside § Code style')
    log(f'ok   scope: {len(changed)} changed files, all on the list; AGENTS.md changes only in § Code style')


# ----------------------------------------------------------------------------------------------- guards (R4-R7)

def mode_guards(log: Log, base: str) -> None:
    plan = show('', 'PLAN.md')

    # R4
    if grep_cached('fsck', '--', 'PLAN.md'):
        raise Red('R4: PLAN.md names fsck')
    names = help_subcommands(log)
    if tuple(sorted(names)) != tuple(sorted(SUBCOMMANDS)) or len(names) != len(SUBCOMMANDS):
        raise CannotRun(f'precondition: terrace --help lists {names}, the GOAL names {list(SUBCOMMANDS)}')
    vision = md_section(plan, 'Vision and scope')
    cli = re.findall(r'^\* CLI: `terrace ([a-z|]+)`', vision, re.MULTILINE)
    if len(cli) != 1 or sorted(cli[0].split('|')) != sorted(names) or len(cli[0].split('|')) != len(names):
        raise Red(f'R4: § Vision and scope CLI line names {cli}, --help prints {names}')
    surface = md_section(plan, 'CLI surface')
    rows = re.findall(r'^\| `terrace ([a-z]+)[^`]*` \|(.*)$', surface, re.MULTILINE)
    table = [r[0] for r in rows]
    if sorted(table) != sorted(names) or len(table) != len(names):
        raise Red(f'R4: § CLI surface table names {table}, --help prints {names}')
    umount = [line for line in surface.splitlines() if line.startswith('| `terrace umount ')]
    if len(umount) != 1 or not umount[0].startswith('| `terrace umount <spec.toml>`'):
        raise Red(f'R4: the umount row does not take a spec file: {umount}')
    for phrase in UMOUNT_PHRASES:
        if phrase not in norm(umount[0]):
            raise Red(f'R4: the umount row lacks "{phrase}"')
    log('ok   R4 no fsck; the CLI line and the CLI surface table each name the seven subcommands --help prints; '
        'the umount row takes the spec and names the pidfile and the cross-host guard')

    # R5
    tech = md_section(plan, 'Technology decisions')
    baseline = [line for line in tech.splitlines() if line.startswith('| Python baseline |')]
    if len(baseline) != 1 or 'frozendict' not in baseline[0] \
            or 'These four are intent, with no code behind them' not in baseline[0]:
        raise Red('R5: the Python-baseline row no longer marks frozendict and its peers as intent')
    if grep_cached('frozendict', '--', 'src', 'pyproject.toml'):
        raise Red('R5: frozendict appears in src/ or pyproject.toml')
    if grep_cached('-w', 'rich', '--', 'PLAN.md'):
        raise Red('R5: PLAN.md names rich')
    log('ok   R5 the Python-baseline row marks frozendict as intent; no frozendict in src/ or pyproject.toml; '
        'no rich in PLAN.md')

    # R6
    testing = bullets(md_section(plan, 'Testing strategy'))
    picks = {
        'policy rate-limit sentence': [b for b in testing if b.startswith('**Policy tests**')],
        'FUSE integration': [b for b in testing if b.startswith('**FUSE integration**')],
        'crash recovery': [b for b in testing if b.startswith('**Crash recovery**')],
        'CI matrix': [b for b in testing if b.startswith('**CI matrix**')],
    }
    for label, found in picks.items():
        if len(found) != 1:
            raise Red(f'R6: expected one {label} bullet, found {len(found)}')
        text = found[0]
        if label.startswith('policy'):
            at = text.find('Rate-limit')
            if at < 0:
                raise Red('R6: the policy bullet has no rate-limit sentence')
            text = text[at:]
        named = re.findall(r'`((?:tests|issues)/[^`\s]+)`', text)
        if not named:
            raise Red(f'R6: the {label} text names no test path or seed')
        unbuilt = re.search(r'unbuilt|does not run|run on no automated machine', text, re.IGNORECASE)
        if unbuilt and not any(n.startswith('issues/') for n in named):
            raise Red(f'R6: the {label} text marks coverage unbuilt and names no seed')
        for name in named:
            if run('git', 'cat-file', '-e', f':{name}').returncode != 0:
                raise Red(f'R6: the {label} text names {name}, which is not in the tree')
        log(f'     precondition: {label} names {named}')
    log('ok   R6 the four testing texts each name a test path or seed, and every one named exists')

    # R7
    outline = md_section(plan, 'Phase outline')
    dated = set(re.findall(r'^(\d+)\. \*\*[^*]+\*\* \(\d{4}-\d\d-\d\d', outline, re.MULTILINE))
    dated |= set(re.findall(r'Phase (\d+), [^.]*?\(\d{4}-\d\d-\d\d', outline))
    matches = grep_cached('Phase [0-9]', '--', 'PLAN.md')
    log(f'     precondition: outline dates phases {sorted(dated, key=int)}; {len(matches)} `Phase N` lines')
    for line in matches:
        for number in re.findall(r'Phase (\d+)', line):
            if number not in dated:
                raise Red(f'R7: "{line[:120]}" names Phase {number}, which the outline does not date')
    if 'the ordered index of seeded cycles' not in norm(outline) or '`ROADMAP.md`' not in outline:
        raise Red('R7: § Phase outline no longer describes ROADMAP.md as the ordered index of seeded cycles')
    if grep_cached('-i', '-w', 'wip', '--', 'PLAN.md'):
        raise Red('R7: PLAN.md names a wip branch')
    log('ok   R7 every Phase N in PLAN.md is dated by the outline; ROADMAP.md is the ordered index; no wip')


# ----------------------------------------------------------------------------------------------------- ledger

def mode_ledger(log: Log, base: str) -> None:
    tech = show('', f'spec/{SLUG}/TECH.md')
    body = tech[tech.index('\n## Phase '):]
    roadmap = show('', 'ROADMAP.md')
    entries: list[str] = []
    for line in body.splitlines():
        if line.startswith('#') or not line.strip():
            entries.append('')
        elif line.startswith(('- ', '* ')) or not entries or not entries[-1]:
            entries.append(line.strip())
        else:
            entries[-1] += ' ' + line.strip()
    count = 0
    for entry in entries:
        if not entry or not DEFERRAL.search(entry) or 'ledger' in entry.lower() or entry.startswith('Amended'):
            continue
        count += 1
        seeds = re.findall(r'issues/[A-Za-z0-9_.-]+\.md', entry)
        if not seeds:
            raise Red(f'ledger: a deferral names no issues/ seed: "{entry[:120]}"')
        for seed in seeds:
            if run('git', 'cat-file', '-e', f':{seed}').returncode != 0:
                raise Red(f'ledger: {seed} is not in the tree')
            if seed not in roadmap:
                raise Red(f'ledger: {seed} has no ROADMAP.md entry')
    log(f'ok   ledger: {count} deferral entr{"y" if count == 1 else "ies"} in TECH.md, each with a seed and an entry')


# ------------------------------------------------------------------------------------------------- build (R2)

def mode_build(log: Log, base: str, scratch: Path, stamp: str) -> None:
    hollow = [v for v in HOLLOWING if os.environ.get(v)]
    if hollow:
        raise CannotRun(f'{hollow} set; each makes the sync or the verify vacuous')
    if run('git', 'diff', '--quiet').returncode != 0:
        raise CannotRun('the working tree differs from the index; stage the phase first')
    before = git('status', '--porcelain')

    proc = run('uv', 'sync', '--locked')
    if proc.returncode != 0:
        raise Red(f'R2: uv sync --locked exited {proc.returncode}: {proc.stderr.strip()[-600:]}')
    log('ok   R2 uv sync --locked')
    probe = "import importlib.util, sys; sys.exit(importlib.util.find_spec('rich') is not None)"
    proc = run('uv', 'run', '--locked', 'python', '-I', '-c', probe)
    if proc.returncode != 0:
        raise Red(f'R2: rich is importable in the synced environment (exit {proc.returncode})')
    log('ok   R2 rich is not installed in the synced environment')

    dist = scratch / f'dist-{stamp}'
    proc = run('uv', 'build', '--out-dir', str(dist))
    if proc.returncode != 0:
        raise Red(f'R2: uv build exited {proc.returncode}: {proc.stderr.strip()[-600:]}')
    wheels, sdists = sorted(dist.glob('*.whl')), sorted(dist.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sdists) != 1:
        raise Red(f'R2: uv build left {wheels + sdists} in {dist}')
    old_deps = tomllib.loads(show(base, 'pyproject.toml'))['project']['dependencies']
    want = sorted(dep_name(d) for d in old_deps if dep_name(d) != 'rich')
    with zipfile.ZipFile(wheels[0]) as wheel:
        meta = wheel.read(next(n for n in wheel.namelist() if n.endswith('.dist-info/METADATA'))).decode()
    with tarfile.open(sdists[0]) as sdist:
        member = next(m for m in sdist.getmembers() if m.name.count('/') == 1 and m.name.endswith('/PKG-INFO'))
        pkg_info = sdist.extractfile(member).read().decode()
    for label, text in (('wheel METADATA', meta), ('sdist PKG-INFO', pkg_info)):
        got = sorted({dep_name(line.split(':', 1)[1].strip()) for line in text.splitlines()
                      if line.startswith('Requires-Dist:') and 'extra ==' not in line})
        if got != want:
            raise Red(f'R2: {label} requires {got}, want {want}')
    log(f'ok   R2 uv build --out-dir {dist}: {wheels[0].name} and {sdists[0].name} require {want}')

    agents = show('', 'AGENTS.md')
    if f'{UNIT_COUNT} unit tests' not in md_section(agents, 'Verification', '##'):
        raise Red(f'R2: AGENTS.md § Verification no longer records {UNIT_COUNT} unit tests')
    proc = run('uv', 'run', 'pytest', '-m', 'unit', '--collect-only', '-q')
    found = re.findall(r'^(\d+)/(\d+) tests collected', proc.stdout, re.MULTILINE)
    if proc.returncode != 0 or len(found) != 1:
        raise Red(f'R2: collection exited {proc.returncode}: {proc.stdout.strip()[-300:]}')
    selected, total = map(int, found[0])
    if selected != UNIT_COUNT:
        raise Red(f'R2: {selected} unit tests collected (of {total}), want {UNIT_COUNT}')
    log(f'ok   R2 {selected}/{total} tests collected under -m unit')

    proc = run('tools/lint.sh')
    (scratch / f'{stamp}-lint.log').write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        raise Red(f'R2: tools/lint.sh exited {proc.returncode}: {(proc.stdout + proc.stderr).strip()[-600:]}')
    log('ok   R2 tools/lint.sh')
    junit = scratch / f'{stamp}-junit.xml'
    started = time.monotonic()
    proc = run('uv', 'run', 'pytest', '-n', 'auto', '-m', 'unit', f'--junitxml={junit}')
    (scratch / f'{stamp}-pytest.log').write_text(proc.stdout + proc.stderr)
    summary = [line for line in proc.stdout.splitlines() if re.search(r'\d+ passed', line)]
    if proc.returncode != 0:
        raise Red(f'R2: pytest exited {proc.returncode}: {proc.stdout.strip()[-600:]}')
    cases = ET.parse(junit).getroot().iter('testcase')
    counts = {'cases': 0, 'failure': 0, 'error': 0, 'skipped': 0}
    for case in cases:
        counts['cases'] += 1
        for kind in ('failure', 'error', 'skipped'):
            counts[kind] += case.find(kind) is not None
    if counts['cases'] != UNIT_COUNT or counts['failure'] or counts['error']:
        raise Red(f'R2: JUnit report {counts}, want {UNIT_COUNT} cases and none failed or erroring')
    log(f'ok   R2 profile verify: {summary[-1].strip("= ") if summary else "?"}; JUnit {counts} '
        f'in {time.monotonic() - started:.0f} s')

    if run('git', 'diff', '--quiet').returncode != 0 or git('status', '--porcelain') != before:
        raise Red('R2: the run changed the tracked tree (uv.lock rewritten, or a file left behind)')
    log('ok   R2 the tracked tree is unchanged by the run')


# ------------------------------------------------------------------------------------------------------- main

def mode_clean(log: Log, base: str) -> None:
    status = git('status', '--porcelain')
    if status.strip():
        raise Red(f'prepush: the tree is not clean:\n{status}')
    log('ok   prepush: clean tree')


def main(argv: list[str]) -> int:
    modes = argv[1:]
    known = ('lock', 'text', 'guards', 'ledger', 'build', 'prepush')
    if not modes or any(m not in known for m in modes):
        print(f'usage: {argv[0]} MODE [MODE ...]; modes: {", ".join(known)}', file=sys.stderr)
        return 3
    try:
        root = git('rev-parse', '--show-toplevel').strip()
        os.chdir(root)
        common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').strip())
        scratch = Path(os.environ.get('GATE_SCRATCH') or common.parent / '.local' / SLUG)
        scratch.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%dT%H%M%S')
        log = Log(scratch / f'{stamp}-gate.log')
        base = gate_base()
    except CannotRun as error:
        print(f'cannot run: {error}', file=sys.stderr)
        return 3
    log(f'gate {SLUG} {" ".join(modes)} in {root}; base {base[:9]}; log {log.path}')
    plan = []
    for mode in modes:
        plan += ['clean', 'lock', 'text', 'guards'] if mode == 'prepush' else [mode]
    try:
        for mode in plan:
            if mode == 'build':
                mode_build(log, base, scratch, stamp)
            else:
                modes_by_name = {'clean': mode_clean, 'lock': mode_lock, 'text': mode_text, 'guards': mode_guards,
                                 'ledger': mode_ledger}
                modes_by_name[mode](log, base)
    except Red as error:
        log(f'FAIL {error}')
        return 1
    except CannotRun as error:
        log(f'CANNOT RUN {error}')
        return 3
    log(f'ok   all: {" ".join(modes)}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
