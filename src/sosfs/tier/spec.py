# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Overlay specification: parse and validate the tier-stack TOML (PLAN.md -> Configuration)."""

# Type annotations
from __future__ import annotations
from typing import Optional, Tuple, Final

# Standard libs
import os
import tomllib
from pathlib import Path, PurePath
from dataclasses import dataclass, field

# Internal libs
from sosfs.core.exceptions import SOSFSError
from sosfs.core.units import parse_bytes, parse_duration

# Public interface
__all__ = ['OverlaySpec', 'TierSpec', 'QuotaSpec', 'WritebackSpec', 'SpecError',
           'TIER_TYPES', 'SUPPORTED_TYPES']

TIER_TYPES: Final[frozenset] = frozenset({'posix', 's3', 'zip', 'tar', 'tar.gz'})
SUPPORTED_TYPES: Final[frozenset] = frozenset({'posix'})

OVERLAY_KEYS: Final[frozenset] = frozenset({'name', 'state', 'drain_timeout', 'checksum', 'fsync_policy'})
TIER_KEYS: Final[frozenset] = frozenset({'name', 'type', 'path', 'role', 'scope', 'quota', 'writeback'})
QUOTA_KEYS: Final[frozenset] = frozenset({'size', 'files', 'bypass_size'})
WRITEBACK_KEYS: Final[frozenset] = frozenset({'mode', 'delay', 'max_interval'})

DEFAULT_DRAIN_TIMEOUT: Final[str] = '5m'
DEFAULT_DELAY: Final[str] = '60s'


class SpecError(SOSFSError):
    """The overlay spec is malformed or the tier stack is not valid."""


def _reject_unknown(table: dict, allowed: frozenset, where: str) -> None:
    """Unknown keys are always a typo — refuse rather than ignore."""
    if unknown := set(table) - allowed:
        raise SpecError(f'Unknown key(s) in {where}: {", ".join(sorted(unknown))}')


def _bytes(value, where: str) -> int:
    try:
        return parse_bytes(value)
    except (TypeError, ValueError):
        raise SpecError(f'Invalid size for {where}: {value!r}') from None


def _duration(value, where: str) -> float:
    try:
        return parse_duration(value)
    except (TypeError, ValueError):
        raise SpecError(f'Invalid duration for {where}: {value!r}') from None


@dataclass(frozen=True)
class QuotaSpec:
    """Per-tier capacity limits; None means unbounded (or no bypass threshold)."""

    size: Optional[int] = None
    files: Optional[int] = None
    bypass_size: Optional[int] = None

    @classmethod
    def from_data(cls, data: dict, where: str) -> QuotaSpec:
        """Build from a `quota = {...}` table."""
        _reject_unknown(data, QUOTA_KEYS, f'{where}.quota')
        files = data.get('files')
        if files is not None and (not isinstance(files, int) or files <= 0):
            raise SpecError(f'Invalid file count for {where}.quota.files: {files!r}')
        return cls(size=None if 'size' not in data else _bytes(data['size'], f'{where}.quota.size'),
                   files=files,
                   bypass_size=None if 'bypass_size' not in data
                   else _bytes(data['bypass_size'], f'{where}.quota.bypass_size'))


@dataclass(frozen=True)
class WritebackSpec:
    """Policy for one tier's upstream boundary (PLAN.md -> Write-back)."""

    mode: str = 'delayed'                # 'delayed' | 'passthrough'
    delay: float = 60.0                  # seconds of write quiescence before commit
    max_interval: Optional[float] = None  # upper bound between commits under constant writes

    @classmethod
    def from_data(cls, data: dict, where: str) -> WritebackSpec:
        """Build from a `writeback = {...}` table."""
        _reject_unknown(data, WRITEBACK_KEYS, f'{where}.writeback')
        mode = data.get('mode', 'delayed')
        if mode not in ('delayed', 'passthrough'):
            raise SpecError(f'Invalid mode for {where}.writeback: {mode!r}')
        if mode == 'passthrough':
            if 'delay' in data or 'max_interval' in data:
                raise SpecError(f'Passthrough writeback takes no clocks ({where}.writeback)')
            return cls(mode=mode, delay=0.0)
        delay = _duration(data.get('delay', DEFAULT_DELAY), f'{where}.writeback.delay')
        max_interval = None if 'max_interval' not in data \
            else _duration(data['max_interval'], f'{where}.writeback.max_interval')
        if max_interval is not None and max_interval <= 0:
            raise SpecError(f'max_interval must be positive ({where}.writeback)')
        return cls(mode=mode, delay=delay, max_interval=max_interval)


@dataclass(frozen=True)
class TierSpec:
    """One configured tier; position in `OverlaySpec.tiers` is stack order, source first."""

    name: str
    type: str
    path: str
    role: str = 'stage'
    scope: str = 'shared'
    quota: QuotaSpec = field(default_factory=QuotaSpec)
    writeback: Optional[WritebackSpec] = None    # None for the source (no upstream boundary)

    @classmethod
    def from_data(cls, data: dict) -> TierSpec:
        """Build one tier from a `[[tier]]` table."""
        name = data.get('name')
        if not isinstance(name, str) or not name:
            raise SpecError(f'Every [[tier]] requires a name (found {name!r})')
        where = f'tier {name!r}'
        _reject_unknown(data, TIER_KEYS, where)
        kind = data.get('type')
        if kind not in TIER_TYPES:
            raise SpecError(f'Invalid type for {where}: {kind!r} '
                            f'(expected one of {", ".join(sorted(TIER_TYPES))})')
        if kind not in SUPPORTED_TYPES:
            raise SpecError(f'Tier type {kind!r} is not yet supported ({where})')
        path = data.get('path')
        if not isinstance(path, str) or not path:
            raise SpecError(f'Posix {where} requires a path')
        role = data.get('role', 'stage')
        if role not in ('source', 'stage'):
            raise SpecError(f'Invalid role for {where}: {role!r}')
        scope = data.get('scope', 'shared')
        if scope not in ('shared', 'node'):
            raise SpecError(f'Invalid scope for {where}: {scope!r}')
        if role == 'source' and 'writeback' in data:
            raise SpecError(f'The source has no upstream boundary — writeback is not valid ({where})')
        quota = QuotaSpec.from_data(data.get('quota', {}), where)
        writeback = None if role == 'source' \
            else WritebackSpec.from_data(data.get('writeback', {}), where)
        return cls(name=name, type=kind, path=os.path.abspath(os.path.expanduser(path)),
                   role=role, scope=scope, quota=quota, writeback=writeback)


@dataclass(frozen=True)
class OverlaySpec:
    """The validated tier-stack definition passed to mount/audit/commit."""

    name: str
    state: Path
    tiers: Tuple[TierSpec, ...]
    drain_timeout: float = 300.0
    checksum: bool = False
    fsync_policy: str = 'resident'       # fsync durability scope: 'resident' | 'commit' (to source)

    @property
    def source(self) -> TierSpec:
        """The authoritative tier (always first)."""
        return self.tiers[0]

    @classmethod
    def load(cls, path: str | Path) -> OverlaySpec:
        """Parse and validate a spec file."""
        return cls.parse(Path(path).read_text())

    @classmethod
    def parse(cls, text: str) -> OverlaySpec:
        """Parse and validate spec TOML text."""
        try:
            return cls.from_data(tomllib.loads(text))
        except tomllib.TOMLDecodeError as error:
            raise SpecError(f'Invalid spec TOML: {error}') from None

    @classmethod
    def from_data(cls, data: dict) -> OverlaySpec:
        """Build and validate from parsed TOML data."""
        _reject_unknown(data, frozenset({'overlay', 'tier'}), 'spec')
        overlay = data.get('overlay')
        if not isinstance(overlay, dict):
            raise SpecError('Spec requires an [overlay] table')
        _reject_unknown(overlay, OVERLAY_KEYS, '[overlay]')
        name = overlay.get('name')
        if not isinstance(name, str) or not name:
            raise SpecError('[overlay] requires a name')
        state = overlay.get('state')
        if not isinstance(state, str) or not state:
            raise SpecError('[overlay] requires a state directory')
        checksum = overlay.get('checksum', False)
        if not isinstance(checksum, bool):
            raise SpecError(f'Invalid checksum flag: {checksum!r}')
        fsync_policy = overlay.get('fsync_policy', 'resident')
        if fsync_policy not in ('resident', 'commit'):
            raise SpecError(f'Invalid fsync_policy: {fsync_policy!r}')
        drain_timeout = _duration(overlay.get('drain_timeout', DEFAULT_DRAIN_TIMEOUT),
                                  '[overlay].drain_timeout')
        tables = data.get('tier')
        if not isinstance(tables, list) or not tables:
            raise SpecError('Spec requires at least one [[tier]]')
        tiers = tuple(TierSpec.from_data(table) for table in tables)
        state_dir = Path(os.path.abspath(os.path.expanduser(state)))
        cls._validate_stack(tiers, state_dir=state_dir)
        return cls(name=name, state=state_dir, tiers=tiers,
                   drain_timeout=drain_timeout, checksum=checksum, fsync_policy=fsync_policy)

    @staticmethod
    def _validate_stack(tiers: Tuple[TierSpec, ...], *, state_dir: Path) -> None:
        """Stack-level invariants: one source, source first, distinct roots, durable state dir."""
        names = [tier.name for tier in tiers]
        if duplicates := sorted({name for name in names if names.count(name) > 1}):
            raise SpecError(f'Duplicate tier names: {", ".join(duplicates)}')
        sources = [tier for tier in tiers if tier.role == 'source']
        if len(sources) != 1:
            raise SpecError(f'Exactly one tier must have role = "source" (found {len(sources)})')
        if tiers[0].role != 'source':
            raise SpecError('The source tier must be first — order in the spec is stack order')
        if tiers[0].scope != 'shared':
            raise SpecError('The source tier cannot be node-scoped')
        roots = [(tier.name, PurePath(tier.path)) for tier in tiers]
        for i, (name, root) in enumerate(roots):
            for other_name, other_root in roots[i + 1:]:
                if root == other_root or root.is_relative_to(other_root) or other_root.is_relative_to(root):
                    raise SpecError(f'Tier roots overlap: {name!r} and {other_name!r}')
            if PurePath(state_dir).is_relative_to(root):
                raise SpecError(f'The state directory cannot live inside a managed tier ({name!r}); '
                                f'it must be on durable storage outside the stack')
