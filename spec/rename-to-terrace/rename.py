# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The substitution that renames the project, as one ordered rule set per phase.

A record of how the rename was made, not part of the product: PLAN.md section 2 states the rules,
and this file is their executable form. Run it from the cycle worktree root, on a clean tree:

    python3 -I spec/rename-to-terrace/rename.py p1      # package, identifiers, imports, tooling
    python3 -I spec/rename-to-terrace/rename.py p2      # on-disk names, prefix, paths, attributes
    python3 -I spec/rename-to-terrace/rename.py p3      # documents, seeds, roadmap, changelog
    python3 -I spec/rename-to-terrace/rename.py p1 --dry-run

Each phase applies, in this order: the directory move (p1 only), its hand-written sites, the two
class rules, the upper-case rule, the lower-case rule, the prose-phrase rules, the sites that
correct what the rules produced (AFTER), and the dated notes two seeds gain (APPEND). p1 holds the
contract forms back (HOLD below) and p2 releases them. A site is an exact string that must occur
exactly once in its file; a site already applied is skipped, so a phase can be run again. The
substitution never adds or removes a line in a file under `src/`, `tests/` or `tools/`.

p1 leaves `uv.lock` to `uv lock`, which the builder runs next. Needs only the standard library and
`git`. Exit status: 0 done, 1 a site did not match or a tree check failed, 2 usage.
"""

from __future__ import annotations

import re
import sys
import subprocess
from pathlib import Path


CODE_DIRS = ('src', 'tests', 'tools')
CODE_FILES = ('pyproject.toml', 'docker-compose.yml', '.github/workflows/ci.yml')
DOC_FILES = ('AGENTS.md', 'PLAN.md', 'README.md', 'ROADMAP.md')
SEED_DIR = 'issues'
OWN_SEED = 'issues/rename-to-terrace.md'

CLASS_RULES = (('SOSFSApp', 'TerraceApp'), ('SOSFSError', 'TerraceError'))
UPPER_RULE = ('SOSFS', 'TERRACE')
LOWER_RULE = ('sosfs', 'terrace')
PHRASE_RULES = ((re.compile(r'simple-overlay-service'), 'terrace'),
                (re.compile(r'Simple [Oo]verlay [Ss]ervice'), 'Terrace'))

# The lower-case forms p1 leaves for p2: names written to a tier root, to a configuration path or
# into an attribute namespace. `None` applies to every file in the phase's scope.
HOLD = (
    (None, re.compile(r'(?<![A-Za-z0-9_])\.sosfs')),                  # marker and partial dirs, sentinel, local file
    (None, re.compile(r'user\.sosfs\.')),                              # the extended-attribute namespace
    ('src/terrace/core/platform.py', re.compile(r"'sosfs'")),          # the system and per-user path components
    ('tools/temp_site.sh', re.compile(r'<state>/sosfs/log')),          # the comment naming the per-user log path
    ('src/terrace/state/lifecycle.py', re.compile(r'# sosfs managed-location marker')),
)

PLAN_ROW_OLD = '| Package / entry point | **`sosfs`** | Per GOAL.md; repo name is a placeholder and unimportant. |'
PLAN_ROW_NEW = (
    '| Package / entry point | **`terrace`** | The project is Terrace, and its distribution, import package and '
    'console script are `terrace`. This overturns the earlier row, which recorded the placeholder name as the '
    'package and called the name unimportant: GOAL.md kept that name only "until a better name is chosen", and '
    'one was chosen on 2026-11-11, before any release and before any overlay was deployed. Renamed in one '
    'cycle with no compatibility code; CHANGELOG.md lists what moved. |')

AGENTS_NOTICE = '''\
> **Pending rename.** This project is being renamed `sosfs` → **Terrace**. The factory already
> addresses this repository by the destination name (`code terrace feature` and the other verbs in the
> operator's KB); the code does not. Everything in this file describes the tree as it is — the package
> is `src/sosfs/`, the entry point is `sosfs`, the environment prefix is `SOSFS_`. The rename is
> queued early (`issues/rename-to-terrace.md`); the namespace-replay fix it waited on has landed.
> Because `PLAN.md` records `sosfs` as a decision rather than an accident, that cycle reconciles
> `PLAN.md` before it touches code.

'''

CONFIG_SEED_OLD = '''\
- Related seeds: `issues/rename-to-terrace.md` moves the `SOSFS_*` environment prefix, the
  `SOSFS_CONFIG_FILE` variable and the `~/.config/sosfs/` and `/etc/sosfs/` paths — every one of
  which this command prints — so landing this cycle after the rename avoids writing the old names
  into a new help surface. `issues/documentation-and-v0.1.0.md` consumes the finished CLI reference
'''
CONFIG_SEED_NEW = '''\
- Related seeds: the rename to Terrace moved the `TERRACE_*` environment prefix, the
  `TERRACE_CONFIG_FILE` variable and the `~/.config/terrace/` and `/etc/terrace/` paths, every one
  of which this command prints, so this cycle writes the names a new help surface should carry.
  `issues/documentation-and-v0.1.0.md` consumes the finished CLI reference
'''

HPC_SEED_OLD = '''\
(the entry point under its current name; [`issues/rename-to-terrace.md`](rename-to-terrace.md)
changes it) and works the first time. If the job hits its time limit instead of reaching the last line, the
'''
HPC_SEED_NEW = '''\
and works the first time. If the job hits its time limit instead of reaching the last line, the
'''

ROADMAP_HEAD_OLD = '### Rename `sosfs` to Terrace\n'
ROADMAP_HEAD_NEW = '### Rename the project to Terrace\n'
ROADMAP_BODY_OLD = '''\
rename. The other third is not: the `SOSFS_*` environment prefix, the `user.sosfs.*` xattr namespace, the
`.sosfs/marker` and `.sosfs/partial` directories in every managed tier root, and the default state path
are **on-disk and user-visible contracts**, and changing the marker directory without a decision about
'''
ROADMAP_BODY_NEW = '''\
rename. The other third is not: the environment prefix, the xattr namespace, the marker and partial
directories in every managed tier root, and the default state path are **on-disk and user-visible
contracts**, and changing the marker directory without a decision about
'''
ROADMAP_TAIL_OLD = 'format break. `PLAN.md` records `sosfs` as a technology decision\n'
ROADMAP_TAIL_NEW = 'format break. `PLAN.md` records the old name as a technology decision\n'

CHANGELOG_ANCHOR = '- Project scaffolding: packaging, license, documentation stubs.\n'
CHANGELOG_ENTRY = '''\

### Changed

- **Breaking: the project is renamed to Terrace, with no migration path.** Nothing reads an old
  name: there is no fallback, no dual read and no warning that recognizes one. An overlay written
  under the old names is not adopted. Where its state database survives, the mount is refused with
  `Tier '<name>' marker is missing — audit required` and the tier root is left as it was. What
  moved, old name then new:
  - distribution and import package: `sosfs` → `terrace` (classes `SOSFSApp`, `SOSFSError` →
    `TerraceApp`, `TerraceError`)
  - console script: `sosfs` → `terrace`
  - environment prefix: `SOSFS_` → `TERRACE_` (`SOSFS_CONFIG_FILE` → `TERRACE_CONFIG_FILE`)
  - marker directory in every managed tier root: `.sosfs/` → `.terrace/`
  - partial directory for transfer temporaries: `.sosfs/partial/` → `.terrace/partial/`
  - S3 empty-directory sentinel: `.sosfs.keep` → `.terrace.keep`
  - extended attributes: `user.sosfs.tiers`, `user.sosfs.dirty` → `user.terrace.tiers`,
    `user.terrace.dirty`
  - local configuration file: `./.sosfs.toml` → `./.terrace.toml`
  - per-user configuration: `$XDG_CONFIG_HOME/sosfs/config.toml` (default
    `~/.config/sosfs/config.toml`) → `$XDG_CONFIG_HOME/terrace/config.toml` (default
    `~/.config/terrace/config.toml`)
  - per-user state and logs: `$XDG_STATE_HOME/sosfs` and `$XDG_STATE_HOME/sosfs/log` (defaults
    `~/.local/state/sosfs` and `~/.local/state/sosfs/log`) → `$XDG_STATE_HOME/terrace` and
    `$XDG_STATE_HOME/terrace/log` (defaults `~/.local/state/terrace` and
    `~/.local/state/terrace/log`)
  - system-wide configuration, logs and state: `/etc/sosfs/config.toml`, `/var/log/sosfs`,
    `/var/lib/sosfs` → `/etc/terrace/config.toml`, `/var/log/terrace`, `/var/lib/terrace`

  The state database is unchanged: it is still `state.db`, at schema version 2, where the overlay
  spec's `[overlay].state` puts it.
'''

# (file, old, new) per phase. A site names its file as the tree holds it when the phase runs.
SITES = {
    'p1': (
        ('PLAN.md', PLAN_ROW_OLD, PLAN_ROW_NEW),
        ('docker-compose.yml', 'MinIO for the sosfs integration suite', 'MinIO for the Terrace integration suite'),
        ('src/terrace/core/__init__.py', 'Core infrastructure for sosfs:', 'Core infrastructure for Terrace:'),
        ('src/terrace/core/config.py', 'Runtime configuration for sosfs (', 'Runtime configuration for Terrace ('),
        ('src/terrace/core/exceptions.py', 'Base class for sosfs domain', 'Base class for Terrace domain'),
        ('src/terrace/core/logging.py', 'every instance of sosfs\n', 'every instance of Terrace\n'),
        ('src/terrace/core/logging.py', 'append-only in sosfs;', 'append-only in Terrace;'),
        ('src/terrace/engine/__init__.py', 'Overlay engine for sosfs:', 'Overlay engine for Terrace:'),
        ('src/terrace/fuse/__init__.py', 'FUSE layer for sosfs:', 'FUSE layer for Terrace:'),
        ('src/terrace/state/__init__.py', 'State store for sosfs:', 'State store for Terrace:'),
        ('src/terrace/state/db.py', 'Database layer for the sosfs state', 'Database layer for the Terrace state'),
        ('src/terrace/state/db.py', 'manager for the sosfs state', 'manager for the Terrace state'),
        ('src/terrace/state/schema.sql', '-- sosfs state store schema', '-- Terrace state store schema'),
        ('src/terrace/tier/__init__.py', 'Tier adapters for sosfs:', 'Tier adapters for Terrace:'),
        ('tests/__init__.py', 'Test helpers for sosfs.', 'Test helpers for Terrace.'),
        ('tests/__init__.py', 'Run the sosfs CLI', 'Run the Terrace CLI'),
        ('tests/conftest.py', 'fixtures for the sosfs test suite', 'fixtures for the Terrace test suite'),
        ('tests/test_cli.py', 'Smoke tests for the sosfs CLI', 'Smoke tests for the Terrace CLI'),
        ('tools/temp_site.sh', 'a throwaway sosfs site', 'a throwaway Terrace site'),
        ('tools/temp_site.sh', '(sosfs reads ', '(Terrace reads '),
    ),
    'p2': (
        ('src/terrace/state/lifecycle.py', '# sosfs managed-location marker', '# Terrace managed-location marker'),
    ),
    'p3': (
        ('AGENTS.md', AGENTS_NOTICE, ''),
        ('AGENTS.md', '`sosfs` is a clean reference implementation', 'Terrace is a clean reference implementation'),
        ('PLAN.md', 'Plan — Simple Overlay Service (`sosfs`)\n' + '=' * 39 + '\n',
                    'Plan — Terrace (`terrace`)\n' + '=' * 26 + '\n'),
        ('PLAN.md', '`sosfs` replaces that manual choreography', 'Terrace replaces that manual choreography'),
        ('README.md', 'sosfs — Simple Overlay Service\n' + '=' * 30 + '\n', 'Terrace\n' + '=' * 7 + '\n'),
        ('ROADMAP.md', ROADMAP_HEAD_OLD, ROADMAP_HEAD_NEW),
        ('ROADMAP.md', ROADMAP_BODY_OLD, ROADMAP_BODY_NEW),
        ('ROADMAP.md', ROADMAP_TAIL_OLD, ROADMAP_TAIL_NEW),
        ('issues/config-command.md', CONFIG_SEED_OLD, CONFIG_SEED_NEW),
        ('issues/hpc-deployment.md', HPC_SEED_OLD, HPC_SEED_NEW),
        ('issues/invariant-audit-gaps.md', 're-initialization sosfs performed', 're-initialization Terrace performed'),
        ('issues/invariant-audit-gaps.md', 'a case sosfs itself produces', 'a case Terrace itself produces'),
        ('CHANGELOG.md', CHANGELOG_ANCHOR, CHANGELOG_ANCHOR + CHANGELOG_ENTRY),
    ),
}

# Applied after the rules, to text the rules produced: the one comment column the block rule cannot
# see, because a blank line and a comment line sit between the two commands it aligns.
AFTER = {
    'p3': (
        ('PLAN.md', 'commit spec.toml' + ' ' * 10 + '# finalize', 'commit spec.toml' + ' ' * 11 + '# finalize'),
    ),
}

AUDIT_NOTE = '''\
- 2026-11-12: a refused attach leaves the crash flag set. `OverlayEngine.start` calls `begin_mount`
  before it attaches the stack, and a marker status other than OK raises with no `end_mount`, so
  `shutdown_state` stays `mounted` and the next mount stops at `CrashDetected` before it reaches the
  marker. Seen by `spec/rename-to-terrace/verify/rename_gate.py contracts`, which mounts an overlay
  written before the rename to Terrace: the first attempt reports the missing marker, and the state
  database then reads `mounted`. The walk should expect a missing marker together with a crash flag
  that no crash set.
'''
CONFIG_NOTE = '''\
- 2026-11-12: the environment prefix is matched without its underscore. `core/config.py` builds the
  environment layer with `Environ(prefix='TERRACE')`, and cmdkit keeps every variable whose name
  starts with those seven letters, then drops one more character as the separator. Measured:
  `TERRACE0LOGGING_LEVEL=DEBUG` sets `logging.level`, and the provenance string names
  `TERRACE_LOGGING_LEVEL`, a variable that is not set. The base commit of the rename to Terrace did
  the same under the old prefix, and that cycle moved the prefix and nothing else
  (`spec/rename-to-terrace/`). `which` prints this string, so shaping should decide whether the
  layer keeps only names that begin `TERRACE_`.
'''

# Appended to the end of a seed, whose last section is its notes. Skipped when already there.
APPEND = {
    'p3': (
        ('issues/audit-command.md', AUDIT_NOTE),
        ('issues/config-command.md', CONFIG_NOTE),
    ),
}

TRAILING_COMMENT = re.compile(r'(?<=\S) {2,}#')
RULED = re.compile(r'-{4,}$')


def git(*args: str) -> str:
    """Run git in the worktree root and return its output."""
    return subprocess.run(('git', *args), check=True, capture_output=True, text=True).stdout


def tracked(*pathspecs: str) -> list[str]:
    """Tracked regular files under the pathspecs, symbolic links left out."""
    names = []
    for line in git('ls-files', '-s', '--', *pathspecs).splitlines():
        mode, _, rest = line.partition(' ')
        if mode != '120000':
            names.append(rest.split('\t', 1)[1])
    return names


def scope(phase: str) -> list[str]:
    """The files a phase rewrites."""
    if phase in ('p1', 'p2'):
        return tracked(*CODE_DIRS, *CODE_FILES)
    return [name for name in tracked(*DOC_FILES, SEED_DIR) if name != OWN_SEED]


def substitute(text: str, phase: str, name: str) -> tuple[str, dict[str, int]]:
    """Apply the phase's rules to one stretch of text; return it with a count per rule."""
    counts: dict[str, int] = {}
    held: list[str] = []

    def hold(match: re.Match) -> str:
        held.append(match.group(0))
        return f'\x00{len(held) - 1}\x00'

    if phase == 'p1':
        for where, pattern in HOLD:
            if where is None or where == name:
                text = pattern.sub(hold, text)
    if phase in ('p1', 'p3'):
        for old, new in CLASS_RULES:
            counts[old] = text.count(old)
            text = text.replace(old, new)
    if phase in ('p2', 'p3'):
        counts[UPPER_RULE[0]] = text.count(UPPER_RULE[0])
        text = text.replace(*UPPER_RULE)
    counts[LOWER_RULE[0]] = text.count(LOWER_RULE[0])
    text = text.replace(*LOWER_RULE)
    if phase in ('p1', 'p3'):
        for pattern, new in PHRASE_RULES:
            text, n = pattern.subn(new, text)
            counts[pattern.pattern] = n
    counts['held'] = len(held)
    text = re.sub('\x00(\\d+)\x00', lambda match: held[int(match.group(1))], text)
    return text, counts


def rewrite(text: str, phase: str, name: str, totals: dict[str, int]) -> str:
    """
    One file through the rules, line for line.

    Consecutive lines whose trailing comments start at one column keep them at one column: the old
    one where every line still fits with two spaces before the comment, else the least that does.
    A ruled comment (one ending in a run of dashes) keeps its width.
    """
    rows = []
    for line in text.splitlines(keepends=True):
        body, eol = (line[:-1], '\n') if line.endswith('\n') else (line, '')
        split = TRAILING_COMMENT.search(body)
        head, pad, tail = (body, 0, '') if split is None else (
            body[:split.start()], split.end() - 1 - split.start(), body[split.end() - 1:])
        new_head, head_counts = substitute(head, phase, name)
        new_tail, tail_counts = substitute(tail, phase, name)
        for counts in (head_counts, tail_counts):
            for rule, n in counts.items():
                totals[rule] = totals.get(rule, 0) + n
        rows.append((head, pad, new_head, new_tail, eol))
    out = []
    at = 0
    while at < len(rows):
        head, pad, new_head, new_tail, eol = rows[at]
        if not pad:
            grown = len(new_head) - len(head)
            ruled = RULED.search(head)
            if grown > 0 and ruled is not None and len(ruled.group(0)) - grown >= 4:
                new_head = new_head[:-grown]
            out.append(new_head + eol)
            at += 1
            continue
        column = len(head) + pad
        end = at
        while end + 1 < len(rows) and rows[end + 1][1] and len(rows[end + 1][0]) + rows[end + 1][1] == column:
            end += 1
        block = rows[at:end + 1]
        if len(block) == 1 and pad == 2:
            target = len(new_head) + 2
        else:
            target = max(column, max(len(row[2]) for row in block) + 2)
        for _, _, row_head, row_tail, row_eol in block:
            out.append(row_head + ' ' * (target - len(row_head)) + row_tail + row_eol)
        at = end + 1
    return ''.join(out)


def apply_sites(sites: tuple, dry: bool) -> int:
    """Hand-written sites. Returns the number that failed to match."""
    failed = 0
    for name, old, new in sites:
        path = Path(name)
        text = path.read_text() if path.is_file() else ''
        found = text.count(old)
        if old in new and new in text:
            print(f'site   {name}: already applied')
        elif found == 1:
            if not dry:
                path.write_text(text.replace(old, new))
            print(f'site   {name}: {old.strip().splitlines()[0][:60]!r}')
        elif found == 0 and new and text.count(new) == 1:
            print(f'site   {name}: already applied')
        elif found == 0 and not new and path.is_file() and old.splitlines()[0] not in text:
            print(f'site   {name}: already removed')
        else:
            print(f'FAIL   {name}: site occurs {found} times, expected 1: {old.strip().splitlines()[0][:60]!r}')
            failed += 1
    return failed


def main(argv: list[str]) -> int:
    """Run one phase."""
    if not argv or argv[0] not in SITES or set(argv[1:]) - {'--dry-run'}:
        print(__doc__)
        return 2
    phase, dry = argv[0], '--dry-run' in argv
    if git('rev-parse', '--show-toplevel').strip() != str(Path.cwd().resolve()):
        print('FAIL   run from the worktree root')
        return 1
    if phase == 'p1' and Path('src/sosfs').exists():
        if Path('src/terrace').exists():
            print('FAIL   both src/sosfs and src/terrace exist')
            return 1
        print('move   src/sosfs -> src/terrace')
        if dry:
            print('dry run stops here: the rules need the moved tree')
            return 0
        git('mv', 'src/sosfs', 'src/terrace')
    if phase != 'p3' and not Path('src/terrace').is_dir():
        print('FAIL   src/terrace does not exist; run p1 first')
        return 1
    if apply_sites(SITES[phase], dry):
        return 1
    totals: dict[str, int] = {}
    changed = 0
    for name in scope(phase):
        path = Path(name)
        try:
            before = path.read_text()
        except UnicodeDecodeError:
            continue
        after = rewrite(before, phase, name, totals)
        if after != before:
            changed += 1
            if before.count('\n') != after.count('\n'):
                print(f'FAIL   {name}: line count changed')
                return 1
            if not dry:
                path.write_text(after)
    if dry:
        print('dry run: the sites after the rules and the dated notes are not evaluated')
    elif apply_sites(AFTER.get(phase, ()), dry):
        return 1
    for name, note in APPEND.get(phase, ()):
        path = Path(name)
        text = path.read_text()
        if note in text:
            print(f'note   {name}: already there')
        else:
            print(f'note   {name}: appended')
            if not dry:
                path.write_text(text + ('' if text.endswith('\n') else '\n') + note)
    for rule, n in totals.items():
        print(f'rule   {rule!r}: {n}')
    print(f'{"would change" if dry else "changed"} {changed} files in the {phase} scope')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
