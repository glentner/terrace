# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for sosfs.core.units."""

# Type annotations
from __future__ import annotations

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.core.units import parse_bytes, parse_duration, parse_rate, Rate


pytestmark = mark.unit


@mark.parametrize('value, expected', [
    ('0', 0),
    ('1024', 1024),
    ('1B', 1),
    ('1KB', 1_000),
    ('1KiB', 1_024),
    ('1kb', 1_000),
    ('1kib', 1_024),
    ('8GB', 8 * 10 ** 9),
    ('64GB', 64 * 10 ** 9),
    ('10TB', 10 * 10 ** 12),
    ('512GiB', 512 * 2 ** 30),
    ('1PB', 10 ** 15),
    ('1PiB', 2 ** 50),
    ('1.5GB', 1_500_000_000),
    ('2 MB', 2 * 10 ** 6),
])
def test_parse_bytes(value: str, expected: int) -> None:
    """Sizes parse with SI (decimal) and IEC (binary) suffixes."""
    assert parse_bytes(value) == expected


def test_parse_bytes_passthrough() -> None:
    """Numeric values pass through as bytes."""
    assert parse_bytes(4096) == 4096
    assert parse_bytes(1.5) == 1


@mark.parametrize('value', ['', 'abc', '10XB', '10KBs', '-5MB', 'KB', '1,000KB'])
def test_parse_bytes_invalid(value: str) -> None:
    """Invalid size strings raise ValueError."""
    with raises(ValueError):
        parse_bytes(value)


@mark.parametrize('value, expected', [
    ('60s', 60.0),
    ('15m', 900.0),
    ('6h', 21_600.0),
    ('1d', 86_400.0),
    ('1w', 604_800.0),
    ('1h30m', 5_400.0),
    ('1h 30m', 5_400.0),
    ('1.5h', 5_400.0),
    ('0.5s', 0.5),
    ('90', 90.0),
])
def test_parse_duration(value: str, expected: float) -> None:
    """Durations parse with unit suffixes and composite forms."""
    assert parse_duration(value) == expected


def test_parse_duration_passthrough() -> None:
    """Numeric values pass through as seconds."""
    assert parse_duration(60) == 60.0
    assert parse_duration(0.25) == 0.25


@mark.parametrize('value', ['', 'abc', '10x', '1h30', 'h', '30ss'])
def test_parse_duration_invalid(value: str) -> None:
    """Invalid duration strings raise ValueError."""
    with raises(ValueError):
        parse_duration(value)


@mark.parametrize('value, count, period', [
    ('4/day', 4, 86_400.0),
    ('2/hour', 2, 3_600.0),
    ('1/6h', 1, 21_600.0),
    ('12/minute', 12, 60.0),
    ('1/h', 1, 3_600.0),
    ('3 / day', 3, 86_400.0),
    ('10/30m', 10, 1_800.0),
])
def test_parse_rate(value: str, count: int, period: float) -> None:
    """Rates parse with word and duration denominators."""
    assert parse_rate(value) == Rate(count=count, period=period)


def test_rate_properties() -> None:
    """Derived rate properties are consistent."""
    rate = parse_rate('4/day')
    assert rate.min_interval == 21_600.0
    assert rate.per_second == 4 / 86_400


@mark.parametrize('value', ['', 'day', '4/', '/day', '0/day', 'x/day', '4/xyz', '1.5/day'])
def test_parse_rate_invalid(value: str) -> None:
    """Invalid rate strings raise ValueError."""
    with raises(ValueError):
        parse_rate(value)
