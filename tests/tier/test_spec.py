# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for overlay-spec parsing and stack validation."""

# Type annotations
from __future__ import annotations

# Standard libs
import os
from pathlib import Path

# External libs
from pytest import mark, raises

# Internal libs
from terrace.core.units import Rate
from terrace.tier.spec import OverlaySpec, TierSpec, QuotaSpec, WritebackSpec, S3Spec, LocationSpec, SpecError


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
    """Unknown types are invalid; an archive type at a stage position is refused, naming the tier."""
    with raises(SpecError, match='Invalid type'):
        OverlaySpec.parse(spec_text(**{'type = "posix"\npath = "/fast/space"':
                                       'type = "nfs"\npath = "/fast/space"'}))
    with raises(SpecError, match="Archive tier 'fast' can only be the source"):
        OverlaySpec.parse(spec_text(**{'type = "posix"\npath = "/fast/space"':
                                       'type = "tar"\npath = "/fast/space"'}))


S3_SPEC = '''
[overlay]
name  = "cloud"
state = "/data/state"

[[tier]]
name = "bucket"
type = "s3"
role = "source"
url  = "s3://my-bucket/datasets/run01"
s3   = { region = "us-east-1", endpoint = "http://minio:9000", allow_http = true }

[[tier]]
name  = "scratch"
type  = "posix"
path  = "/scratch/space"
writeback = { delay = "60s" }
'''


def test_parse_s3_source() -> None:
    """An S3 tier parses its url into bucket + prefix and carries the connection knobs."""
    spec = OverlaySpec.parse(S3_SPEC)
    bucket, scratch = spec.tiers
    assert (bucket.type, bucket.role) == ('s3', 'source')
    assert bucket.url == 's3://my-bucket/datasets/run01'
    assert bucket.path == bucket.url                        # identity/overlap key mirrors the url
    assert bucket.s3 == S3Spec(bucket='my-bucket', prefix='datasets/run01',
                               region='us-east-1', endpoint='http://minio:9000', allow_http=True)
    assert scratch.type == 'posix' and scratch.s3 is None


def test_s3_url_without_prefix() -> None:
    """A bucket-only url yields an empty key prefix."""
    spec = OverlaySpec.parse(S3_SPEC.replace('s3://my-bucket/datasets/run01', 's3://my-bucket'))
    assert spec.source.s3 == S3Spec(bucket='my-bucket', prefix='', region='us-east-1',
                                    endpoint='http://minio:9000', allow_http=True)


def test_s3_requires_url() -> None:
    """An S3 tier without a url is malformed."""
    with raises(SpecError, match='requires a url'):
        OverlaySpec.parse(S3_SPEC.replace('url  = "s3://my-bucket/datasets/run01"\n', ''))


def test_s3_rejects_path_key() -> None:
    """An S3 tier configured with a filesystem path is a mix-up."""
    with raises(SpecError, match='uses url'):
        OverlaySpec.parse(S3_SPEC.replace('url  = "s3://my-bucket/datasets/run01"',
                                          'path = "/local/space"'))


def test_s3_url_scheme_enforced() -> None:
    """The url must carry the s3:// scheme."""
    with raises(SpecError, match='must be s3://'):
        OverlaySpec.parse(S3_SPEC.replace('s3://my-bucket/datasets/run01', 'https://my-bucket/x'))


def test_s3_unknown_connection_key_rejected() -> None:
    """Typos in the s3 = {...} table never pass silently."""
    with raises(SpecError, match='Unknown key'):
        OverlaySpec.parse(S3_SPEC.replace('region = "us-east-1"', 'reigon = "us-east-1"'))


def test_s3_settings_only_for_s3_tiers() -> None:
    """url/s3 settings on a posix tier are rejected."""
    with raises(SpecError, match='only valid for s3'):
        OverlaySpec.parse(spec_text(**{'path = "/fast/space"':
                                       'path = "/fast/space"\nurl = "s3://b/p"'}))


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


# -- LocationSpec: the inner location an archive tier layers over ------------------------------------------------

def test_location_posix() -> None:
    """A posix location parses and absolutizes its path."""
    loc = LocationSpec.from_data({'type': 'posix', 'path': '~/some.zip'}, 'tier "arc"')
    assert loc.type == 'posix' and loc.s3 is None
    assert loc.path == str(Path('~/some.zip').expanduser())


def test_location_s3() -> None:
    """An s3 location parses its url into a bucket + object key (the S3Spec prefix) with connection knobs."""
    loc = LocationSpec.from_data(
        {'type': 's3', 'url': 's3://cold/space.tar.gz', 's3': {'region': 'us-east-1', 'allow_http': True}},
        'tier "cold"')
    assert loc.type == 's3' and loc.path is None
    assert loc.url == 's3://cold/space.tar.gz'
    assert loc.s3 == S3Spec(bucket='cold', prefix='space.tar.gz', region='us-east-1', allow_http=True)


def test_location_type_required() -> None:
    """The location type must be posix or s3."""
    with raises(SpecError, match='Invalid location type'):
        LocationSpec.from_data({'path': '/x.zip'}, 'tier "arc"')
    with raises(SpecError, match='Invalid location type'):
        LocationSpec.from_data({'type': 'nfs', 'path': '/x.zip'}, 'tier "arc"')


def test_location_posix_requires_path() -> None:
    """A posix location without a path is malformed."""
    with raises(SpecError, match='requires a path'):
        LocationSpec.from_data({'type': 'posix'}, 'tier "arc"')


def test_location_s3_requires_url() -> None:
    """An s3 location without a url is malformed."""
    with raises(SpecError, match='requires a url'):
        LocationSpec.from_data({'type': 's3'}, 'tier "cold"')


def test_location_rejects_crossed_settings() -> None:
    """path on s3 and url/s3 on posix are mix-ups; unknown keys never pass silently."""
    with raises(SpecError, match='uses url'):
        LocationSpec.from_data({'type': 's3', 'path': '/x.zip'}, 'tier "cold"')
    with raises(SpecError, match='only valid for s3'):
        LocationSpec.from_data({'type': 'posix', 'path': '/x.zip', 'url': 's3://b/k'}, 'tier "arc"')
    with raises(SpecError, match='Unknown key'):
        LocationSpec.from_data({'type': 'posix', 'path': '/x.zip', 'staging': '/w'}, 'tier "arc"')


# -- archive sources: the types, their keys, and the stacks the engine can serve ---------------------------------

# The two single-bundle archive examples of PLAN.md § Configuration, their [[tier]] tables as written there.
ARCHIVE_EXAMPLES = {
    'direct-update-zip': '''
[[tier]]                                  # direct-update ZIP as source
name = "archive"
type = "zip"
role = "source"
location = { type = "posix", path = "/project/some.zip" }
''',
    'staged-tar-gz': '''
[[tier]]                                  # staged re-sync tar.gz on S3 as source
name = "cold"
type = "tar.gz"
role = "source"
location = { type = "s3", url = "s3://bucket/space.tar.gz" }
sync     = { max_frequency = "4/day" }

[[tier]]                                  # the durable tier: the one shared stage tier, below the source
name  = "scratch"
type  = "posix"
path  = "/scratch/space"
quota = { size = "10TB", files = 1_000_000 }    # counts the staged bundle at its uncompressed size
writeback = { delay = "60s", max_interval = "15m" }
''',
}

OVERLAY = '[overlay]\nname = "archived"\nstate = "{state}"\n'


def stack(*tiers: str, state: str = '/data/state') -> str:
    """A spec of the given [[tier]] bodies, in order, under one [overlay] table."""
    return OVERLAY.format(state=state) + ''.join(f'\n[[tier]]\n{body.strip()}\n' for body in tiers)


def archive(kind: str = 'zip', name: str = 'archive', role: str = 'source', extra: str = '',
            location: str = '{ type = "posix", path = "/data/some.zip" }') -> str:
    """An archive tier's body."""
    return f'name = "{name}"\ntype = "{kind}"\nrole = "{role}"\nlocation = {location}\n{extra}'


def posix(name: str, path: str | Path, *, role: str = 'stage', scope: str = 'shared', extra: str = '') -> str:
    """A POSIX tier's body."""
    return f'name = "{name}"\ntype = "posix"\npath = "{path}"\nrole = "{role}"\nscope = "{scope}"\n{extra}'


@mark.parametrize('example', list(ARCHIVE_EXAMPLES))
def test_archive_examples_load(example: str) -> None:
    """Each example loads inside an [overlay] table; the ZIP, which shows no stage tier, above one shared POSIX one."""
    text = OVERLAY.format(state='/project/state') + ARCHIVE_EXAMPLES[example]
    if example == 'direct-update-zip':
        text += '\n[[tier]]\nname = "stage"\ntype = "posix"\npath = "/scratch/space"\n'
    spec = OverlaySpec.parse(text)
    source, durable = spec.tiers
    assert (durable.type, durable.role, durable.scope) == ('posix', 'stage', 'shared')
    if example == 'direct-update-zip':
        assert (source.type, source.role) == ('zip', 'source')
        assert source.location == LocationSpec(type='posix', path='/project/some.zip')
        assert source.path == '/project/some.zip' and source.max_frequency is None
    else:
        assert (source.type, source.role) == ('tar.gz', 'source')
        assert source.location == LocationSpec(type='s3', url='s3://bucket/space.tar.gz',
                                               s3=S3Spec(bucket='bucket', prefix='space.tar.gz'))
        assert source.path == 's3://bucket/space.tar.gz' and source.max_frequency == Rate(4, 86400.0)
    assert (source.url, source.s3, source.writeback) == (None, None, None)


def test_archive_tier_carries_location_and_rate() -> None:
    """A tar.gz source carries its parsed location and re-sync rate; a malformed rate is refused by name."""
    sync = 'sync = { max_frequency = "2/hour" }'
    spec = OverlaySpec.parse(stack(archive('tar.gz', extra=sync,
                                           location='{ type = "posix", path = "/data/space.tar.gz" }'),
                                   posix('scratch', '/scratch/space')))
    assert spec.source.location == LocationSpec(type='posix', path='/data/space.tar.gz')
    assert spec.source.path == '/data/space.tar.gz'
    assert spec.source.max_frequency == Rate(2, 3600.0)
    unlimited = OverlaySpec.parse(stack(archive('tar.gz', extra='sync = {}'), posix('scratch', '/scratch/space')))
    assert unlimited.source.max_frequency is None
    for rate in ('4', '0/day', '4/fortnight'):
        with raises(SpecError, match=r"Invalid rate for tier 'archive'\.sync\.max_frequency"):
            OverlaySpec.parse(stack(archive('tar.gz', extra=f'sync = {{ max_frequency = "{rate}" }}'),
                                    posix('scratch', '/scratch/space')))
    with raises(SpecError, match='Invalid rate'):
        OverlaySpec.parse(stack(archive('tar.gz', extra='sync = { max_frequency = 4 }'),
                                posix('scratch', '/scratch/space')))
    with raises(SpecError, match='Unknown key'):
        OverlaySpec.parse(stack(archive('tar.gz', extra='sync = { max_frequncy = "4/day" }'),
                                posix('scratch', '/scratch/space')))
    with raises(SpecError, match="requires a location"):
        OverlaySpec.parse(stack('name = "archive"\ntype = "zip"\nrole = "source"', posix('scratch', '/scratch')))
    with raises(SpecError, match="names its file with location"):
        OverlaySpec.parse(stack(archive(extra='path = "/data/some.zip"'), posix('scratch', '/scratch/space')))
    with raises(SpecError, match="location setting is only valid for archive tiers"):
        OverlaySpec.parse(stack(posix('src', '/data/space', role='source',
                                      extra='location = { type = "posix", path = "/data/some.zip" }'),
                                posix('scratch', '/scratch/space')))


@mark.parametrize('kind', ['zip', 'tar'])
def test_sync_refused_on_direct_update(kind: str) -> None:
    """A direct-update archive has no re-sync to rate-limit, so `sync` on it is refused, naming the tier."""
    with raises(SpecError, match=r"sync is only valid for tar\.gz tiers \(tier 'archive'\)") as caught:
        OverlaySpec.parse(stack(archive(kind, extra='sync = { max_frequency = "4/day" }'),
                                posix('scratch', '/scratch/space')))
    assert f'A {kind} archive is updated in place' in str(caught.value)


def test_staging_refused_naming_the_durable_tier() -> None:
    """The retired key is refused by name, naming the tier that carries it and the tier that now holds the bundle."""
    with raises(SpecError) as caught:
        OverlaySpec.parse(stack(archive('tar.gz', name='cold', extra='staging = "/scratch/space.tar"'),
                                posix('scratch', '/scratch/space'), posix('shm', '/dev/shm/space', scope='node')))
    message = str(caught.value)
    assert message.startswith("The staging key is retired (tier 'cold')")
    assert "'scratch', the shared POSIX stage tier directly below the source" in message
    with raises(SpecError, match='Unknown key'):
        TierSpec.from_data({'name': 'cold', 'type': 'tar.gz', 'role': 'source', 'staging': '/w',
                            'location': {'type': 'posix', 'path': '/data/space.tar.gz'}})


def test_staging_refused_without_a_durable_tier() -> None:
    """With no shared POSIX stage tier below the source, the refusal still names the key and says the spec has none."""
    for below in ([], [posix('shm', '/dev/shm/space', scope='node')]):
        with raises(SpecError) as caught:
            OverlaySpec.parse(stack(archive('tar.gz', name='cold', extra='staging = "/scratch/space.tar"'), *below))
        message = str(caught.value)
        assert message.startswith("The staging key is retired (tier 'cold')")
        assert 'shared POSIX stage tier directly below the source, which this spec does not have' in message


def test_sharded_refused_naming_its_seed() -> None:
    """`sharded = true` is a known key refused as not yet supported, with one fixed text, never as unknown."""
    sharded = archive('tar.gz', name='imagenet', extra='sharded = true\nsync = { max_frequency = "4/day" }',
                      location='{ type = "s3", url = "s3://bucket/imagenet/*.tar.gz" }')
    with raises(SpecError) as caught:
        OverlaySpec.parse(stack(sharded, posix('scratch', '/scratch/space')))
    assert str(caught.value) == ("Sharded archive sources are not yet supported (tier 'imagenet'); "
                                 'see issues/sharded-archive-sources.md')
    assert OverlaySpec.parse(stack(archive(extra='sharded = false'), posix('scratch', '/scratch/space')))
    with raises(SpecError, match=r"Invalid boolean for tier 'archive'\.sharded"):
        OverlaySpec.parse(stack(archive(extra='sharded = "yes"'), posix('scratch', '/scratch/space')))


@mark.parametrize('kind', ['zip', 'tar', 'tar.gz'])
def test_archive_refused_at_stage_position(kind: str) -> None:
    """Archive tiers are source-only: one at a stage position is refused, naming it."""
    with raises(SpecError, match=f"Archive tier 'packed' can only be the source: type '{kind}'"):
        OverlaySpec.parse(stack(posix('src', '/data/space', role='source'), posix('scratch', '/scratch/space'),
                                archive(kind, name='packed', role='stage')))


STACK_RULE = {
    'no-stage': ([], 'the spec has no stage tier'),
    'node-stage': ([posix('shm', '/dev/shm/space', scope='node')], "the tier below it, 'shm', is a node posix tier"),
    's3-stage': (['name = "bucket"\ntype = "s3"\nurl = "s3://bucket/stage"'],
                 "the tier below it, 'bucket', is a shared s3 tier"),
    'second-shared': ([posix('scratch', '/scratch/space'), posix('shm', '/dev/shm/space', scope='node'),
                       posix('project', '/project/space')], "'project' is a second shared stage tier"),
}


@mark.parametrize('case', list(STACK_RULE))
def test_archive_stack_rule(case: str) -> None:
    """Over an archive source, exactly one shared POSIX stage tier sits directly below it; else the fault is named."""
    below, fault = STACK_RULE[case]
    with raises(SpecError) as caught:
        OverlaySpec.parse(stack(archive(), *below))
    message = str(caught.value)
    assert message.startswith("Archive source 'archive' needs exactly one shared POSIX stage tier, directly below it")
    assert message.endswith(fault)


def test_archive_stack_rule_binds_every_archive_type() -> None:
    """The rule is one rule for zip, tar and tar.gz, and a conforming stack with node tiers below it loads."""
    for kind in ('zip', 'tar', 'tar.gz'):
        with raises(SpecError, match='needs exactly one shared POSIX stage tier'):
            OverlaySpec.parse(stack(archive(kind)))
        spec = OverlaySpec.parse(stack(archive(kind), posix('scratch', '/scratch/space'),
                                       posix('shm', '/dev/shm/space', scope='node')))
        assert spec.source.type == kind


@mark.parametrize('side', ['source', 'stage', 'both'])
def test_symlinked_root_overlap_is_refused(tmp_path: Path, side: str) -> None:
    """Two roots that overlap once symlinks are resolved are refused, naming both tiers, wherever the symlink is."""
    real = tmp_path / 'real'
    (real / 'sub').mkdir(parents=True)
    if side == 'source':
        (tmp_path / 'link').symlink_to(real)
        source, stage = tmp_path / 'link', real / 'sub'
    elif side == 'stage':
        (tmp_path / 'link').symlink_to(real / 'sub')
        source, stage = real, tmp_path / 'link'
    else:
        (tmp_path / 'source-link').symlink_to(real)
        (tmp_path / 'stage-link').symlink_to(real / 'sub')
        source, stage = tmp_path / 'source-link', tmp_path / 'stage-link'
    assert not stage.is_relative_to(source) and not source.is_relative_to(stage)
    with raises(SpecError, match="Tier roots overlap: 'src' and 'stage'"):
        OverlaySpec.parse(stack(posix('src', source, role='source'), posix('stage', stage),
                                state=str(tmp_path / 'state')))


def test_archive_under_a_symlinked_directory_overlaps_its_stage(tmp_path: Path) -> None:
    """An archive reached through a symlinked directory overlaps a stage rooted at that directory's real path."""
    real = tmp_path / 'real'
    real.mkdir()
    (tmp_path / 'link').symlink_to(real)
    location = f'{{ type = "posix", path = "{tmp_path / "link" / "space.zip"}" }}'
    with raises(SpecError, match="Tier roots overlap: 'archive' and 'scratch'"):
        OverlaySpec.parse(stack(archive(location=location), posix('scratch', real), state=str(tmp_path / 'state')))


def test_symlinked_state_dir_inside_a_tier_is_refused(tmp_path: Path) -> None:
    """A state directory that is a symlink into a tier root is refused, naming the tier."""
    (tmp_path / 'src' / 'state').mkdir(parents=True)
    os.symlink(tmp_path / 'src' / 'state', tmp_path / 'state')
    with raises(SpecError, match=r"state directory cannot live inside a managed tier \('src'\)"):
        OverlaySpec.parse(stack(posix('src', tmp_path / 'src', role='source'), posix('stage', tmp_path / 'stage'),
                                state=str(tmp_path / 'state')))


def test_state_dir_under_a_symlinked_tier_is_refused(tmp_path: Path) -> None:
    """A state directory inside the real target of a symlinked tier root is refused, naming the tier."""
    (tmp_path / 'real').mkdir()
    (tmp_path / 'link').symlink_to(tmp_path / 'real')
    with raises(SpecError, match=r"state directory cannot live inside a managed tier \('src'\)"):
        OverlaySpec.parse(stack(posix('src', tmp_path / 'link', role='source'), posix('stage', tmp_path / 'stage'),
                                state=str(tmp_path / 'real' / 'state')))


def test_stage_root_containing_the_archive_overlaps(tmp_path: Path) -> None:
    """An archive's root is its file: a stage rooted at its directory overlaps it, a sibling directory does not."""
    location = f'{{ type = "posix", path = "{tmp_path / "data" / "space.zip"}" }}'
    with raises(SpecError, match="Tier roots overlap: 'archive' and 'scratch'"):
        OverlaySpec.parse(stack(archive(location=location), posix('scratch', tmp_path / 'data'),
                                state=str(tmp_path / 'state')))
    spec = OverlaySpec.parse(stack(archive(location=location), posix('scratch', tmp_path / 'data' / 'stage'),
                                   state=str(tmp_path / 'state')))
    assert spec.source.path == str(tmp_path / 'data' / 'space.zip')
