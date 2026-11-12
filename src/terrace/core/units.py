# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Parsing for size, duration, and rate strings used across configuration."""

# Type annotations
from __future__ import annotations
from typing import Dict, Final

# Standard libs
import re
from dataclasses import dataclass

# Public interface
__all__ = ['parse_bytes', 'parse_duration', 'parse_rate', 'Rate']


# SI suffixes are decimal (storage-vendor convention); IEC 'i' suffixes are binary
BYTES_PATTERN: Final[re.Pattern] = re.compile(r'(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTP]I?B|B)?')
BYTES_SCALES: Final[Dict[str, int]] = {
    'B': 1,
    'KB': 10 ** 3, 'MB': 10 ** 6, 'GB': 10 ** 9, 'TB': 10 ** 12, 'PB': 10 ** 15,
    'KIB': 2 ** 10, 'MIB': 2 ** 20, 'GIB': 2 ** 30, 'TIB': 2 ** 40, 'PIB': 2 ** 50,
}


def parse_bytes(value: str | int | float) -> int:
    """Parse size to integer bytes — SI ('10TB') and binary ('512GiB') suffixes; bare numbers are bytes."""
    if isinstance(value, (int, float)):
        return int(value)
    if match := BYTES_PATTERN.fullmatch(value.strip().upper()):
        return int(float(match.group('num')) * BYTES_SCALES[match.group('unit') or 'B'])
    else:
        raise ValueError(f'Size string {value!r} is not a valid size')


DURATION_PATTERN: Final[re.Pattern] = re.compile(r'(?P<num>\d+(?:\.\d+)?)(?P<unit>[SMHDW])')
DURATION_SCALES: Final[Dict[str, int]] = {
    'S': 1,
    'M': 60,
    'H': 60 * 60,
    'D': 60 * 60 * 24,
    'W': 60 * 60 * 24 * 7,
}


def parse_duration(value: str | int | float) -> float:
    """Parse duration to seconds — '60s', '15m', composite '1h30m'; bare numbers are seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r'\s+', '', value.upper())
    try:
        return float(text)
    except ValueError:
        pass
    pos, total = 0, 0.0
    while pos < len(text):
        if match := DURATION_PATTERN.match(text, pos):
            total += float(match.group('num')) * DURATION_SCALES[match.group('unit')]
            pos = match.end()
        else:
            raise ValueError(f'Duration string {value!r} is not a valid duration')
    if pos == 0:
        raise ValueError(f'Duration string {value!r} is not a valid duration')
    return total


@dataclass(frozen=True)
class Rate:
    """A frequency limit: at most `count` events per `period` seconds."""

    count: int
    period: float

    @property
    def per_second(self) -> float:
        """Events per second."""
        return self.count / self.period

    @property
    def min_interval(self) -> float:
        """Minimum seconds between events at this rate."""
        return self.period / self.count


RATE_PERIODS: Final[Dict[str, float]] = {
    'S': 1, 'SEC': 1, 'SECOND': 1, 'SECONDS': 1,
    'M': 60, 'MIN': 60, 'MINUTE': 60, 'MINUTES': 60,
    'H': 3600, 'HR': 3600, 'HOUR': 3600, 'HOURS': 3600,
    'D': 86400, 'DAY': 86400, 'DAYS': 86400,
    'W': 604800, 'WEEK': 604800, 'WEEKS': 604800,
}


def parse_rate(value: str) -> Rate:
    """Parse rate to `Rate` — '4/day', '2/hour', '1/6h'."""
    count_part, sep, period_part = value.strip().partition('/')
    count_text = count_part.strip()
    if not sep or not count_text.isdigit():
        raise ValueError(f'Rate string {value!r} is not a valid rate')
    count = int(count_text)
    period_text = period_part.strip().upper()
    if period_text in RATE_PERIODS:
        period = float(RATE_PERIODS[period_text])
    else:
        try:
            period = parse_duration(period_text)
        except ValueError:
            raise ValueError(f'Rate string {value!r} is not a valid rate') from None
    if count <= 0 or period <= 0:
        raise ValueError(f'Rate string {value!r} is not a valid rate')
    return Rate(count=count, period=period)
