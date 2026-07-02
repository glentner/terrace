# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for overlay-spec parsing and stack validation."""

# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path

# External libs
from pytest import mark, raises

# Internal libs
from sosfs.tier.spec import OverlaySpec, QuotaSpec, WritebackSpec, SpecError


pytestmark = mark.unit


FULL_SPEC = '''
[overlay]
name  = "myspace"
state = "/project/state/myspace"
drain_timeout = "5m"
checksum = false

[[tier]]
name = "project"
type = "posix"
path = "/project/space"
role = "source"

[[tier]]
name  = "scratch"
type  = "posix"
path  = "/scratch/space"
quota = { size = "10TB", files = 1_000_000 }
writeback = { delay = "60s", max_interval = "15m" }

[[tier]]
name  = "shm"
type  = "posix"
path  = "/dev/shm/space"
scope = "node"
quota = { size = "64GB", files = 1_000_000, bypass_size = "8GB" }
writeback = { mode = "passthrough" }
'''


MINIMAL_SPEC = '''
[overlay]
name  = "tiny"
state = "/data/state"

[[tier]]
name = "src"
type = "posix"
path = "/data/space"
role = "source"

[[tier]]
name = "fast"
type = "posix"
path = "/fast/space"
'''


def spec_text(**overrides: str) -> str:
    """MINIMAL_SPEC with whole lines replaced by keyed fragments (crude but readable)."""
    text = MINIMAL_SPEC
    for old, new in overrides.items():
        text = text.replace(old, new)
    return text


def test_parse_full_spec() -> None:
    """The PLAN.md example (posix tiers) parses to typed configs."""
    spec = OverlaySpec.parse(FULL_SPEC)
    assert spec.name == 'myspace'
    assert spec.state == Path('/project/state/myspace')
    assert spec.drain_timeout == 300.0
    assert spec.checksum is False
    assert [tier.name for tier in spec.tiers] == ['project', 'scratch', 'shm']
    assert spec.source is spec.tiers[0]

    project, scratch, shm = spec.tiers
    assert (project.role, project.scope, project.writeback) == ('source', 'shared', None)
    assert project.quota == QuotaSpec()

    assert scratch.role == 'stage'
    assert scratch.quota == QuotaSpec(size=10 * 10 ** 12, files=1_000_000)
    assert scratch.writeback == WritebackSpec(mode='delayed', delay=60.0, max_interval=900.0)

    assert shm.scope == 'node'
    assert shm.quota.bypass_size == 8 * 10 ** 9
    assert shm.writeback == WritebackSpec(mode='passthrough', delay=0.0)


def test_defaults() -> None:
    """Omitted knobs default: 5m drain, no checksum, unbounded quota, 60s delayed writeback."""
    spec = OverlaySpec.parse(MINIMAL_SPEC)
    assert spec.drain_timeout == 300.0 and spec.checksum is False
    fast = spec.tiers[1]
    assert fast.quota == QuotaSpec(size=None, files=None, bypass_size=None)
    assert fast.writeback == WritebackSpec(mode='delayed', delay=60.0, max_interval=None)


def test_paths_expand_and_absolutize() -> None:
    """Tier paths and the state dir expand '~' and normalize to absolute."""
    spec = OverlaySpec.parse(spec_text(**{'state = "/data/state"': 'state = "~/state/tiny"'}))
    assert spec.state.is_absolute() and '~' not in str(spec.state)


def test_invalid_toml() -> None:
    """Broken TOML reports as SpecError, not a raw parse exception."""
    with raises(SpecError, match='Invalid spec TOML'):
        OverlaySpec.parse('[overlay\nname = ')


def test_missing_tables() -> None:
    """The [overlay] table and at least one [[tier]] are required."""
    with raises(SpecError, match=r'requires an \[overlay\]'):
        OverlaySpec.parse('[[tier]]\nname = "x"\ntype = "posix"\npath = "/x"\nrole = "source"')
    with raises(SpecError, match=r'at least one \[\[tier\]\]'):
        OverlaySpec.parse('[overlay]\nname = "x"\nstate = "/s"')


def test_unknown_keys_rejected() -> None:
    """Typos never pass silently, at any level."""
    with raises(SpecError, match='Unknown key'):
        OverlaySpec.parse(MINIMAL_SPEC + '\n[overlay.extra]\nx = 1')
    with raises(SpecError, match='Unknown key'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nqutoa = { size = "1GB" }'}))
    with raises(SpecError, match='Unknown key'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nquota = { szie = "1GB" }'}))
    with raises(SpecError, match='Unknown key'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nwriteback = { dely = "60s" }'}))


def test_tier_type_validation() -> None:
    """Unknown types are invalid; known-but-unimplemented types say so."""
    with raises(SpecError, match='Invalid type'):
        OverlaySpec.parse(spec_text(**{'type = "posix"\npath = "/fast/space"':
                                       'type = "nfs"\npath = "/fast/space"'}))
    with raises(SpecError, match='not yet supported'):
        OverlaySpec.parse(spec_text(**{'type = "posix"\npath = "/fast/space"':
                                       'type = "s3"\npath = "/fast/space"'}))


def test_posix_requires_path() -> None:
    """A posix tier without a path is malformed."""
    with raises(SpecError, match='requires a path'):
        OverlaySpec.parse(spec_text(**{'path = "/fast/space"\n': ''}))


def test_exactly_one_source() -> None:
    """No source, or two, is a stack error."""
    with raises(SpecError, match='Exactly one tier'):
        OverlaySpec.parse(spec_text(**{'role = "source"\n': ''}))
    with raises(SpecError, match='Exactly one tier'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nrole = "source"'}))


def test_source_must_be_first() -> None:
    """Order in the file is stack order — the source leads."""
    text = spec_text(**{'role = "source"\n': ''})           # drop role from 'src'
    text = text.replace('name = "fast"', 'name = "fast"\nrole = "source"')
    with raises(SpecError, match='must be first'):
        OverlaySpec.parse(text)


def test_source_cannot_be_node_scoped() -> None:
    """A node-local source breaks detach/commit semantics."""
    with raises(SpecError, match='node-scoped'):
        OverlaySpec.parse(spec_text(**{'role = "source"': 'role = "source"\nscope = "node"'}))


def test_source_takes_no_writeback() -> None:
    """The source has no upstream boundary."""
    with raises(SpecError, match='no upstream boundary'):
        OverlaySpec.parse(spec_text(**{'role = "source"': 'role = "source"\nwriteback = { delay = "60s" }'}))


def test_duplicate_names() -> None:
    """Tier names must be unique."""
    with raises(SpecError, match='Duplicate tier names'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "src"'}))


def test_overlapping_roots() -> None:
    """No tier root may equal or nest within another."""
    with raises(SpecError, match='roots overlap'):
        OverlaySpec.parse(spec_text(**{'path = "/fast/space"': 'path = "/data/space/inner"'}))
    with raises(SpecError, match='roots overlap'):
        OverlaySpec.parse(spec_text(**{'path = "/fast/space"': 'path = "/data/space"'}))


def test_state_dir_not_inside_a_tier() -> None:
    """The state directory must live outside every managed location."""
    with raises(SpecError, match='state directory'):
        OverlaySpec.parse(spec_text(**{'state = "/data/state"': 'state = "/data/space/.state"'}))


def test_passthrough_takes_no_clocks() -> None:
    """Passthrough mode contradicts delay/max_interval."""
    with raises(SpecError, match='no clocks'):
        OverlaySpec.parse(spec_text(**{'name = "fast"':
                                       'name = "fast"\nwriteback = { mode = "passthrough", delay = "60s" }'}))


def test_invalid_units_and_values() -> None:
    """Unit strings run through core.units and report as SpecError with context."""
    with raises(SpecError, match='quota.size'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nquota = { size = "ten terabytes" }'}))
    with raises(SpecError, match='writeback.delay'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nwriteback = { delay = "soon" }'}))
    with raises(SpecError, match='quota.files'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nquota = { files = -1 }'}))
    with raises(SpecError, match='drain_timeout'):
        OverlaySpec.parse(spec_text(**{'name  = "tiny"': 'name  = "tiny"\ndrain_timeout = "whenever"'}))
    with raises(SpecError, match='Invalid mode'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nwriteback = { mode = "eventually" }'}))
    with raises(SpecError, match='Invalid role'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nrole = "cache"'}))
    with raises(SpecError, match='Invalid scope'):
        OverlaySpec.parse(spec_text(**{'name = "fast"': 'name = "fast"\nscope = "rack"'}))


def test_load_from_file(tmp_path: Path) -> None:
    """OverlaySpec.load reads a spec file; a missing file raises the natural OSError."""
    spec_file = tmp_path / 'spec.toml'
    spec_file.write_text(MINIMAL_SPEC)
    assert OverlaySpec.load(spec_file).name == 'tiny'
    with raises(FileNotFoundError):
        OverlaySpec.load(tmp_path / 'absent.toml')
