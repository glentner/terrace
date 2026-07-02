# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for PEP 750 SQL template rendering."""

# Type annotations
from __future__ import annotations

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.state.sql import render, SQLTemplateError


pytestmark = mark.unit


def test_parameters_bind_in_order() -> None:
    """Interpolations become ? placeholders with params in order."""
    path, size = '/data.bin', 42
    sql, params = render(t'UPDATE entry SET size = {size} WHERE path = {path}')
    assert sql == 'UPDATE entry SET size = ? WHERE path = ?'
    assert params == (42, '/data.bin')


def test_no_interpolations() -> None:
    """A template with no interpolations renders as plain SQL."""
    sql, params = render(t'SELECT COUNT(*) FROM entry')
    assert sql == 'SELECT COUNT(*) FROM entry'
    assert params == ()


def test_identifier_splice() -> None:
    """The :id spec splices a validated identifier instead of binding."""
    column = 'mtime'
    sql, params = render(t'UPDATE entry SET {column:id} = {1.5}')
    assert sql == 'UPDATE entry SET mtime = ?'
    assert params == (1.5,)


@mark.parametrize('bad', ['mtime; DROP TABLE entry', 'a-b', '1col', '', 'sp ace'])
def test_identifier_rejected(bad: str) -> None:
    """Invalid identifiers are refused."""
    with raises(SQLTemplateError):
        render(t'SELECT {bad:id} FROM entry')


def test_identifier_requires_string() -> None:
    """Non-string identifier values are refused."""
    with raises(SQLTemplateError):
        render(t'SELECT {42:id} FROM entry')


def test_conversion_rejected() -> None:
    """Conversions (!r etc.) have no SQL meaning and are refused."""
    value = 'x'
    with raises(SQLTemplateError):
        render(t'SELECT {value!r}')


def test_unknown_spec_rejected() -> None:
    """Format specs other than :id are refused."""
    value = 3
    with raises(SQLTemplateError):
        render(t'SELECT {value:>10}')


def test_nested_template() -> None:
    """A Template value splices as a sub-fragment with its params."""
    mask = t' AND deleted = {0}'
    path = '/x'
    sql, params = render(t'SELECT * FROM entry WHERE path = {path}{mask}')
    assert sql == 'SELECT * FROM entry WHERE path = ? AND deleted = ?'
    assert params == ('/x', 0)


def test_empty_nested_template() -> None:
    """An empty template splices as nothing."""
    mask = t''
    sql, params = render(t'SELECT 1{mask}')
    assert sql == 'SELECT 1'
    assert params == ()


def test_template_list_joins() -> None:
    """A list of templates joins with ', ' — the dynamic SET clause."""
    fields = {'size': 10, 'mtime': 2.0}
    sets = [t'{name:id} = {value}' for name, value in fields.items()]
    sql, params = render(t'UPDATE entry SET {sets}')
    assert sql == 'UPDATE entry SET size = ?, mtime = ?'
    assert params == (10, 2.0)


def test_scalar_sequence_expands() -> None:
    """A list of scalars expands to a placeholder list (IN clauses)."""
    tiers = ['shm', 'tmp', 'scratch']
    sql, params = render(t'SELECT * FROM residency WHERE tier IN ({tiers})')
    assert sql == 'SELECT * FROM residency WHERE tier IN (?, ?, ?)'
    assert params == ('shm', 'tmp', 'scratch')


def test_empty_sequence_rejected() -> None:
    """An empty sequence would render invalid SQL and is refused."""
    empty: list = []
    with raises(SQLTemplateError):
        render(t'SELECT * FROM entry WHERE path IN ({empty})')


def test_plain_string_rejected() -> None:
    """Plain strings are not accepted — SQL must be written as t-strings."""
    with raises(SQLTemplateError):
        render('SELECT 1')  # type: ignore[arg-type]
