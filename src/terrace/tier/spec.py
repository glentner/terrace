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
from terrace.core.exceptions import TerraceError
from terrace.core.units import Rate, parse_bytes, parse_duration, parse_rate

# Public interface
__all__ = ['OverlaySpec', 'TierSpec', 'QuotaSpec', 'WritebackSpec', 'S3Spec', 'LocationSpec', 'SpecError',
           'TIER_TYPES', 'SUPPORTED_TYPES', 'ARCHIVE_TYPES']

TIER_TYPES: Final[frozenset] = frozenset({'posix', 's3', 'zip', 'tar', 'tar.gz'})
SUPPORTED_TYPES: Final[frozenset] = TIER_TYPES
ARCHIVE_TYPES: Final[frozenset] = frozenset({'zip', 'tar', 'tar.gz'})

OVERLAY_KEYS: Final[frozenset] = frozenset({'name', 'state', 'drain_timeout', 'checksum', 'fsync_policy'})
TIER_KEYS: Final[frozenset] = frozenset({'name', 'type', 'path', 'url', 's3', 'role', 'scope', 'quota', 'writeback',
                                         'location', 'sync', 'sharded'})
QUOTA_KEYS: Final[frozenset] = frozenset({'size', 'files', 'bypass_size'})
WRITEBACK_KEYS: Final[frozenset] = frozenset({'mode', 'delay', 'max_interval'})
S3_KEYS: Final[frozenset] = frozenset({'region', 'endpoint', 'access_key_id', 'secret_access_key',
                                       'session_token', 'allow_http', 'anonymous', 'virtual_hosted'})
LOCATION_KEYS: Final[frozenset] = frozenset({'type', 'path', 'url', 's3'})
SYNC_KEYS: Final[frozenset] = frozenset({'max_frequency'})

# The retired key a staged bundle's working path was once given by. It is not a tier key, so a tier
# built alone refuses it as unknown; a whole spec refuses it by name (`OverlaySpec._refuse_staging`).
RETIRED_STAGING: Final[str] = 'staging'

DEFAULT_DRAIN_TIMEOUT: Final[str] = '5m'
DEFAULT_DELAY: Final[str] = '60s'


class SpecError(TerraceError):
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


def _rate(value, where: str) -> Rate:
    # parse_rate calls str methods first, so a number or a table would escape as AttributeError.
    if not isinstance(value, str):
        raise SpecError(f'Invalid rate for {where}: {value!r}')
    try:
        return parse_rate(value)
    except ValueError:
        raise SpecError(f'Invalid rate for {where}: {value!r}') from None


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
class S3Spec:
    """Connection settings for an S3 tier: bucket and key prefix from the url, plus obstore knobs."""

    bucket: str
    prefix: str = ''
    region: Optional[str] = None
    endpoint: Optional[str] = None                # explicit endpoint for MinIO / moto / on-prem
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    allow_http: bool = False                      # required for http:// endpoints
    anonymous: bool = False                       # skip request signing (public buckets)
    virtual_hosted: bool = False                  # virtual-hosted-style addressing

    @classmethod
    def from_data(cls, url: str, data: dict, where: str) -> S3Spec:
        """Build from the tier `url` (s3://bucket[/prefix]) and an optional `s3 = {...}` table."""
        if not url.startswith('s3://'):
            raise SpecError(f'S3 {where} url must be s3://bucket[/prefix] (found {url!r})')
        bucket, _, prefix = url[len('s3://'):].partition('/')
        if not bucket:
            raise SpecError(f'S3 {where} url has no bucket (found {url!r})')
        _reject_unknown(data, S3_KEYS, f'{where}.s3')
        flags = {}
        for flag in ('allow_http', 'anonymous', 'virtual_hosted'):
            value = data.get(flag, False)
            if not isinstance(value, bool):
                raise SpecError(f'Invalid boolean for {where}.s3.{flag}: {value!r}')
            flags[flag] = value
        strings = {}
        for name in ('region', 'endpoint', 'access_key_id', 'secret_access_key', 'session_token'):
            value = data.get(name)
            if value is not None and not isinstance(value, str):
                raise SpecError(f'Invalid value for {where}.s3.{name}: {value!r}')
            strings[name] = value
        return cls(bucket=bucket, prefix=prefix.strip('/'), **flags, **strings)


@dataclass(frozen=True)
class LocationSpec:
    """The inner location an archive tier layers over: a POSIX path or a single S3 object (PLAN.md -> ArchiveTier)."""

    type: str                                    # 'posix' | 's3'
    path: Optional[str] = None                   # absolute POSIX path to the archive file
    url: Optional[str] = None                    # s3://bucket/key for an S3-backed archive object
    s3: Optional[S3Spec] = None                  # parsed connection settings when type == 's3' (key is s3.prefix)

    @classmethod
    def from_data(cls, data: dict, where: str) -> LocationSpec:
        """Build from a `location = {...}` table; reuses `S3Spec` for the connection knobs of an S3 object."""
        if not isinstance(data, dict):
            raise SpecError(f'{where}.location must be a table')
        _reject_unknown(data, LOCATION_KEYS, f'{where}.location')
        kind = data.get('type')
        if kind not in ('posix', 's3'):
            raise SpecError(f'Invalid location type for {where}: {kind!r} (expected "posix" or "s3")')
        if kind == 's3':
            if 'path' in data:
                raise SpecError(f'S3 {where}.location uses url = "s3://bucket/key", not path')
            url = data.get('url')
            if not isinstance(url, str) or not url:
                raise SpecError(f'S3 {where}.location requires a url')
            return cls(type='s3', url=url, s3=S3Spec.from_data(url, data.get('s3', {}), f'{where}.location'))
        if 'url' in data or 's3' in data:
            raise SpecError(f'The url/s3 settings are only valid for s3 locations ({where}.location)')
        path = data.get('path')
        if not isinstance(path, str) or not path:
            raise SpecError(f'Posix {where}.location requires a path')
        return cls(type='posix', path=os.path.abspath(os.path.expanduser(path)))


@dataclass(frozen=True)
class TierSpec:
    """One configured tier; position in `OverlaySpec.tiers` is stack order, source first."""

    name: str
    type: str
    path: str                                    # POSIX root, S3 url, or an archive's inner path/url (overlap key)
    url: Optional[str] = None                    # set for s3 tiers
    s3: Optional[S3Spec] = None                  # parsed connection settings for s3 tiers
    role: str = 'stage'
    scope: str = 'shared'
    quota: QuotaSpec = field(default_factory=QuotaSpec)
    writeback: Optional[WritebackSpec] = None    # None for the source (no upstream boundary)
    location: Optional[LocationSpec] = None      # the inner location of an archive tier
    max_frequency: Optional[Rate] = None         # the re-sync rate limit of a staged archive tier

    @classmethod
    def from_data(cls, data: dict) -> TierSpec:
        """Build one tier from a `[[tier]]` table."""
        name = data.get('name')
        if not isinstance(name, str) or not name:
            raise SpecError(f'Every [[tier]] requires a name (found {name!r})')
        where = f'tier {name!r}'
        _reject_unknown(data, TIER_KEYS, where)
        kind = data.get('type')
        if kind not in SUPPORTED_TYPES:
            raise SpecError(f'Invalid type for {where}: {kind!r} '
                            f'(expected one of {", ".join(sorted(SUPPORTED_TYPES))})')
        cls._refuse_sharded(kind, data, where)
        role = data.get('role', 'stage')
        if role not in ('source', 'stage'):
            raise SpecError(f'Invalid role for {where}: {role!r}')
        if kind in ARCHIVE_TYPES and role != 'source':
            raise SpecError(f'Archive {where} can only be the source: type {kind!r} is not valid at a stage position')
        location = None
        if kind in ARCHIVE_TYPES:
            location = cls._archive_location(data, where)
            path, url, s3 = (location.path if location.type == 'posix' else location.url), None, None
        elif 'location' in data:
            raise SpecError(f'The location setting is only valid for archive tiers ({where})')
        else:
            path, url, s3 = cls._location(kind, data, where)
        max_frequency = cls._sync(kind, data, where)
        scope = data.get('scope', 'shared')
        if scope not in ('shared', 'node'):
            raise SpecError(f'Invalid scope for {where}: {scope!r}')
        if role == 'source' and 'writeback' in data:
            raise SpecError(f'The source has no upstream boundary — writeback is not valid ({where})')
        quota = QuotaSpec.from_data(data.get('quota', {}), where)
        writeback = None if role == 'source' \
            else WritebackSpec.from_data(data.get('writeback', {}), where)
        return cls(name=name, type=kind, path=path, url=url, s3=s3, role=role, scope=scope, quota=quota,
                   writeback=writeback, location=location, max_frequency=max_frequency)

    @staticmethod
    def _refuse_sharded(kind: str, data: dict, where: str) -> None:
        """
        `sharded` is a known key whose feature does not exist yet. It is read before the location,
        whose url names many objects (`*`) only in the sharded form.
        """
        if 'sharded' not in data:
            return
        sharded = data['sharded']
        if not isinstance(sharded, bool):
            raise SpecError(f'Invalid boolean for {where}.sharded: {sharded!r}')
        if sharded:
            raise SpecError(f'Sharded archive sources are not yet supported ({where}); '
                            f'see issues/sharded-archive-sources.md')
        if kind not in ARCHIVE_TYPES:
            raise SpecError(f'The sharded setting is only valid for archive tiers ({where})')

    @staticmethod
    def _archive_location(data: dict, where: str) -> LocationSpec:
        """An archive names its one file through `location`, never through a tier's own path or url."""
        for key in ('path', 'url', 's3'):
            if key in data:
                raise SpecError(f'Archive {where} names its file with location = {{...}}, not {key}')
        if 'location' not in data:
            raise SpecError(f'Archive {where} requires a location = {{...}} table')
        return LocationSpec.from_data(data['location'], where)

    @staticmethod
    def _sync(kind: str, data: dict, where: str) -> Optional[Rate]:
        """The re-sync rate limit; only a staged (tar.gz) archive is re-synced, so only it takes `sync`."""
        if 'sync' not in data:
            return None
        if kind in ('zip', 'tar'):
            raise SpecError(f'A {kind} archive is updated in place and has no re-sync to rate-limit; '
                            f'sync is only valid for tar.gz tiers ({where})')
        if kind != 'tar.gz':
            raise SpecError(f'The sync setting is only valid for tar.gz tiers ({where})')
        sync = data['sync']
        if not isinstance(sync, dict):
            raise SpecError(f'{where}.sync must be a table')
        _reject_unknown(sync, SYNC_KEYS, f'{where}.sync')
        if 'max_frequency' not in sync:
            return None
        return _rate(sync['max_frequency'], f'{where}.sync.max_frequency')

    @staticmethod
    def _location(kind: str, data: dict, where: str) -> Tuple[str, Optional[str], Optional[S3Spec]]:
        """Resolve a tier's backing location by type: a POSIX `path`, or an S3 `url` + `s3` table."""
        if kind == 's3':
            if 'path' in data:
                raise SpecError(f'S3 {where} uses url = "s3://bucket[/prefix]", not path')
            url = data.get('url')
            if not isinstance(url, str) or not url:
                raise SpecError(f'S3 {where} requires a url')
            return url, url, S3Spec.from_data(url, data.get('s3', {}), where)
        if 'url' in data or 's3' in data:
            raise SpecError(f'The url/s3 settings are only valid for s3 tiers ({where})')
        path = data.get('path')
        if not isinstance(path, str) or not path:
            raise SpecError(f'Posix {where} requires a path')
        return os.path.abspath(os.path.expanduser(path)), None, None


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
        cls._refuse_staging(tables)
        tiers = tuple(TierSpec.from_data(table) for table in tables)
        state_dir = Path(os.path.abspath(os.path.expanduser(state)))
        cls._validate_stack(tiers, state_dir=state_dir)
        return cls(name=name, state=state_dir, tiers=tiers,
                   drain_timeout=drain_timeout, checksum=checksum, fsync_policy=fsync_policy)

    @staticmethod
    def _refuse_staging(tables: list) -> None:
        """
        Refuse the retired `staging` key by name, before any tier is built and before the stack rule,
        so a spec written for the old working path is told where the staged bundle lives now: in the
        root of the shared POSIX stage tier directly below the source, counted by its limits.
        """
        for table in tables:
            if not isinstance(table, dict) or RETIRED_STAGING not in table:
                continue
            below = tables[1] if len(tables) > 1 and isinstance(tables[1], dict) else None
            holds = (below is not None and below.get('role', 'stage') == 'stage'
                     and below.get('scope', 'shared') == 'shared' and below.get('type') == 'posix')
            holder = (f'the root of {below.get("name")!r}, the shared POSIX stage tier directly below the source'
                      if holds else 'the root of the shared POSIX stage tier directly below the source, '
                                    'which this spec does not have')
            raise SpecError(f'The {RETIRED_STAGING} key is retired (tier {table.get("name")!r}): '
                            f'a staged bundle lives in {holder}, and counts against its limits')

    @staticmethod
    def _validate_stack(tiers: Tuple[TierSpec, ...], *, state_dir: Path) -> None:
        """
        Stack-level invariants: one source, source first, an archive source's one durable tier, distinct
        roots, durable state dir.

        Roots and the state directory are compared with symlinks resolved (an archive's root is its file),
        so the check reads the filesystem and its result can differ between hosts. S3 urls are never
        resolved. The stored paths stay as written.
        """
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
        if tiers[0].type in ARCHIVE_TYPES:
            # An archive source is served only through a POSIX copy, and its staged bundle lives in the one
            # shared tier directly below it.
            rule = f'Archive source {tiers[0].name!r} needs exactly one shared POSIX stage tier, directly below it'
            if len(tiers) < 2:
                raise SpecError(f'{rule}; the spec has no stage tier')
            below = tiers[1]
            if below.scope != 'shared' or below.type != 'posix':
                raise SpecError(f'{rule}; the tier below it, {below.name!r}, is a {below.scope} {below.type} tier')
            for tier in tiers[2:]:
                if tier.scope == 'shared':
                    raise SpecError(f'{rule}; {tier.name!r} is a second shared stage tier')
        # A POSIX root (an archive's root is its file) is compared with symlinks resolved; an S3 url as written.
        roots = []
        for tier in tiers:
            remote = tier.type == 's3' or (tier.location is not None and tier.location.type == 's3')
            roots.append((tier.name, PurePath(tier.path if remote else os.path.realpath(tier.path))))
        state_key = PurePath(os.path.realpath(state_dir))
        for i, (name, root) in enumerate(roots):
            for other_name, other_root in roots[i + 1:]:
                if root == other_root or root.is_relative_to(other_root) or other_root.is_relative_to(root):
                    raise SpecError(f'Tier roots overlap: {name!r} and {other_name!r} ({root} and {other_root})')
            if state_key.is_relative_to(root):
                raise SpecError(f'The state directory cannot live inside a managed tier ({name!r}); '
                                f'it must be on durable storage outside the stack ({state_key} is in {root})')
