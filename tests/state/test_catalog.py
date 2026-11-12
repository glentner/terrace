# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the catalog layer."""

# Type annotations
from __future__ import annotations

# Standard libs
import sqlite3
from pathlib import Path

# External libs
from pytest import fixture, mark, raises

# Internal libs
from terrace.state.db import Database
from terrace.state.catalog import Catalog, normalize, parent_of


pytestmark = mark.unit


@fixture
def catalog(tmp_path: Path) -> Catalog:
    """A catalog over an initialized database with a three-tier stack."""
    db = Database(tmp_path / 'state.db')
    db.initialize()
    with db.transaction() as conn:
        conn.execute("INSERT INTO tier (name, position, type, role) VALUES ('project', 0, 'posix', 'source')")
        conn.execute("INSERT INTO tier (name, position, type) VALUES ('scratch', 1, 'posix')")
        conn.execute("INSERT INTO tier (name, position, type, scope) VALUES ('shm', 2, 'posix', 'node')")
    yield Catalog(db)
    db.close()


def new_file(catalog: Catalog, path: str, size: int = 0) -> None:
    """Shorthand for adding a file entry with boilerplate metadata."""
    catalog.add(path, 'file', mode=0o644, uid=1000, gid=1000, size=size, mtime=1.0, ctime=1.0, atime=1.0)


def test_normalize_and_parent() -> None:
    """Path normalization and parent derivation."""
    assert normalize('a/b') == '/a/b'
    assert normalize('/a/b/') == '/a/b'
    assert normalize('/') == '/'
    assert parent_of('/') is None
    assert parent_of('/a') == '/'
    assert parent_of('/a/b') == '/a'


def test_add_and_get(catalog: Catalog) -> None:
    """Entries round-trip with derived parents."""
    new_file(catalog, '/data/x.bin', size=100)
    entry = catalog.get('/data/x.bin')
    assert entry is not None
    assert entry.parent == '/data'
    assert entry.kind == 'file'
    assert entry.size == 100
    assert catalog.get('/missing') is None


def test_duplicate_path_rejected(catalog: Catalog) -> None:
    """Paths are unique."""
    new_file(catalog, '/x')
    with raises(sqlite3.IntegrityError):
        new_file(catalog, '/x')


def test_children_and_tombstones(catalog: Catalog) -> None:
    """Children lists direct descendants; tombstones are masked until purge."""
    catalog.add('/d', 'dir', mode=0o755, uid=0, gid=0, mtime=1.0, ctime=1.0, atime=1.0)
    new_file(catalog, '/d/a')
    new_file(catalog, '/d/b')
    new_file(catalog, '/other')
    assert [entry.path for entry in catalog.children('/d')] == ['/d/a', '/d/b']

    catalog.mark_deleted('/d/a')
    assert [entry.path for entry in catalog.children('/d')] == ['/d/b']
    assert catalog.get('/d/a') is None
    assert catalog.get('/d/a', include_deleted=True) is not None

    catalog.purge('/d/a')
    assert catalog.get('/d/a', include_deleted=True) is None


def test_update_meta(catalog: Catalog) -> None:
    """Whitelisted metadata updates apply; unknown fields are rejected."""
    new_file(catalog, '/x')
    catalog.update_meta('/x', mode=0o600, size=42, mtime=9.0)
    entry = catalog.get('/x')
    assert (entry.mode, entry.size, entry.mtime) == (0o600, 42, 9.0)
    with raises(ValueError):
        catalog.update_meta('/x', path='/evil')


def test_set_listed(catalog: Catalog) -> None:
    """The listed flag marks lazy population."""
    catalog.add('/d', 'dir', mode=0o755, uid=0, gid=0, mtime=1.0, ctime=1.0, atime=1.0)
    assert not catalog.get('/d').listed
    catalog.set_listed('/d')
    assert catalog.get('/d').listed


def test_rename_file(catalog: Catalog) -> None:
    """Renaming a file rewrites path and parent."""
    new_file(catalog, '/a/x')
    catalog.rename('/a/x', '/b/y')
    assert catalog.get('/a/x') is None
    entry = catalog.get('/b/y')
    assert entry is not None and entry.parent == '/b'


def test_rename_directory_subtree(catalog: Catalog) -> None:
    """Renaming a directory rewrites every descendant's path and parent."""
    catalog.add('/old', 'dir', mode=0o755, uid=0, gid=0, mtime=1.0, ctime=1.0, atime=1.0)
    new_file(catalog, '/old/a')
    catalog.add('/old/sub', 'dir', mode=0o755, uid=0, gid=0, mtime=1.0, ctime=1.0, atime=1.0)
    new_file(catalog, '/old/sub/b')
    catalog.rename('/old', '/new')
    assert catalog.get('/new/sub/b') is not None
    assert catalog.get('/new/sub/b').parent == '/new/sub'
    assert [entry.path for entry in catalog.children('/new')] == ['/new/a', '/new/sub']
    assert catalog.get('/old/a') is None


def test_rename_with_glob_metacharacters(catalog: Catalog) -> None:
    """Subtree selection is immune to pattern metacharacters in names."""
    catalog.add('/w[e]ird*', 'dir', mode=0o755, uid=0, gid=0, mtime=1.0, ctime=1.0, atime=1.0)
    new_file(catalog, '/w[e]ird*/child')
    new_file(catalog, '/wXird-not-related')
    catalog.rename('/w[e]ird*', '/plain')
    assert catalog.get('/plain/child') is not None
    assert catalog.get('/wXird-not-related') is not None


def test_residency_roundtrip_and_order(catalog: Catalog) -> None:
    """Residencies list upstream-first; fastest_copy is the highest position."""
    new_file(catalog, '/x', size=10)
    catalog.add_residency('/x', 'shm', atime=3.0, size=10, mtime=1.0, state='dirty')
    catalog.add_residency('/x', 'project', atime=1.0, size=10, mtime=1.0)
    catalog.add_residency('/x', 'scratch', atime=2.0, size=10, mtime=1.0)
    assert [residency.tier for residency in catalog.residencies('/x')] == ['project', 'scratch', 'shm']
    assert catalog.fastest_copy('/x').tier == 'shm'
    assert catalog.get_residency('/x', 'scratch').atime == 2.0
    assert catalog.get_residency('/x', 'tmp' ) is None


def test_add_residency_requires_entry(catalog: Catalog) -> None:
    """Residency for a missing entry raises."""
    with raises(LookupError):
        catalog.add_residency('/missing', 'shm', atime=1.0, size=0, mtime=1.0)


def test_touch_and_update_residency(catalog: Catalog) -> None:
    """LRU atime advances; whitelisted updates apply; unknown fields rejected."""
    new_file(catalog, '/x')
    catalog.add_residency('/x', 'shm', atime=1.0, size=0, mtime=1.0)
    catalog.touch('/x', 'shm', atime=99.0)
    assert catalog.get_residency('/x', 'shm').atime == 99.0
    catalog.update_residency('/x', 'shm', state='dirty', size=5)
    residency = catalog.get_residency('/x', 'shm')
    assert (residency.state, residency.size) == ('dirty', 5)
    with raises(ValueError):
        catalog.update_residency('/x', 'shm', tier='project')


def test_drop_residency(catalog: Catalog) -> None:
    """Dropping forgets the copy without touching the entry."""
    new_file(catalog, '/x')
    catalog.add_residency('/x', 'shm', atime=1.0, size=0, mtime=1.0)
    catalog.drop_residency('/x', 'shm')
    assert catalog.get_residency('/x', 'shm') is None
    assert catalog.get('/x') is not None


def test_least_recent(catalog: Catalog) -> None:
    """Eviction scan returns oldest clean copies first, dirty excluded, limit honored."""
    for n, atime in enumerate([5.0, 1.0, 3.0]):
        new_file(catalog, f'/f{n}')
        catalog.add_residency(f'/f{n}', 'shm', atime=atime, size=1, mtime=1.0)
    new_file(catalog, '/dirty')
    catalog.add_residency('/dirty', 'shm', atime=0.1, size=1, mtime=1.0, state='dirty')

    paths = [path for path, _ in catalog.least_recent('shm')]
    assert paths == ['/f1', '/f2', '/f0']
    assert [path for path, _ in catalog.least_recent('shm', limit=2)] == ['/f1', '/f2']
