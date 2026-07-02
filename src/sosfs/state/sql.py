# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Render PEP 750 template strings into parameterized SQL."""

# Type annotations
from __future__ import annotations
from typing import Any, List, Tuple, Final

# Standard libs
import re
from string.templatelib import Template, Interpolation

# Internal libs
from sosfs.core.exceptions import SOSFSError

# Public interface
__all__ = ['render', 'SQLTemplateError']


IDENTIFIER: Final[re.Pattern] = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


class SQLTemplateError(SOSFSError):
    """A SQL template could not be rendered safely."""


def render(template: Template) -> Tuple[str, Tuple[Any, ...]]:
    """
    Render a t-string into (sql, params) for the sqlite3 driver.

    Interpolations become bound `?` parameters, with three exceptions:
      * `{name:id}` splices a validated identifier (for whitelisted column names),
      * a `Template` value (or list of them, joined by ', ') splices as a sub-fragment,
      * a list/tuple of scalars expands to a placeholder list (for IN clauses).

    Never hand-write `?` placeholders or quote values inside the template text.
    """
    if not isinstance(template, Template):
        raise SQLTemplateError(f'Expected Template, given {type(template).__name__} (write SQL as t-strings)')
    sql: List[str] = [template.strings[0]]
    params: List[Any] = []
    for interp, static in zip(template.interpolations, template.strings[1:]):
        _render_part(interp, sql, params)
        sql.append(static)
    return ''.join(sql), tuple(params)


def _render_part(interp: Interpolation, sql: List[str], params: List[Any]) -> None:
    """Append one interpolation to the accumulating query."""
    if interp.conversion is not None:
        raise SQLTemplateError(f'Conversions are not supported in SQL templates ({interp.expression!r})')
    value = interp.value
    if interp.format_spec == 'id':
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise SQLTemplateError(f'Invalid SQL identifier: {value!r}')
        sql.append(value)
    elif interp.format_spec:
        raise SQLTemplateError(f'Unsupported format spec {interp.format_spec!r} ({interp.expression!r})')
    elif isinstance(value, Template):
        inner_sql, inner_params = render(value)
        sql.append(inner_sql)
        params.extend(inner_params)
    elif isinstance(value, (list, tuple)):
        if not value:
            raise SQLTemplateError(f'Empty sequence in SQL template ({interp.expression!r})')
        if all(isinstance(member, Template) for member in value):
            rendered = [render(member) for member in value]
            sql.append(', '.join(fragment for fragment, _ in rendered))
            for _, inner_params in rendered:
                params.extend(inner_params)
        else:
            sql.append(', '.join('?' * len(value)))
            params.extend(value)
    else:
        sql.append('?')
        params.append(value)
