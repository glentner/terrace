# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The scenario's own machinery: the block, the markers, the oracle and the tap, each shown to refuse."""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Optional

# Standard libs
import io
import ast
import sys
import gzip
import tarfile
import http.client
from pathlib import Path
from urllib.parse import urlsplit

# External libs
import obstore
from obstore.store import S3Store
from pytest import mark, raises, param

# Internal libs
from tests.loop import oracle, stages, test_scenario
from tests.loop.stages import Blocked, blocked, Scene, STAGES
from tests.loop.tap import Tap

pytestmark = mark.unit
REPO = Path(__file__).resolve().parents[2]


class Refusal(OSError):
    """A refusal a stage may be blocked by."""


class Narrower(Refusal):
    """A subclass of it, which `blocked` must not mistake for it."""


def test_blocked_turns_the_exact_failure_into_blocked() -> None:
    with raises(Blocked, match=r'^issues/x\.md: no$') as caught:
        with blocked('issues/x.md', Refusal, 'no'):
            raise Refusal('no')
    assert type(caught.value.__cause__) is Refusal


def test_blocked_passes_another_message_through() -> None:
    with raises(Refusal, match='^no, and more$'):
        with blocked('issues/x.md', Refusal, 'no'):
            raise Refusal('no, and more')


def test_blocked_passes_a_subclass_through() -> None:
    with raises(Narrower):
        with blocked('issues/x.md', Refusal, 'no'):
            raise Narrower('no')


def test_blocked_passes_another_type_through() -> None:
    with raises(KeyError):
        with blocked('issues/x.md', Refusal, 'no'):
            raise KeyError('no')


def test_blocked_lets_a_passing_step_pass() -> None:
    with blocked('issues/x.md', Refusal, 'no'):
        pass


def test_blocked_refuses_to_pin_a_slip() -> None:
    for slip in (AttributeError, TypeError, NameError, KeyError, Exception):
        with raises(TypeError, match='not a refusal'):
            with blocked('issues/x.md', slip, 'no'):
                pass


def enclosing(source: ast.Module, node: ast.AST) -> list[str]:
    """The names of the functions whose bodies hold `node`."""
    return [found.name for found in ast.walk(source) if isinstance(found, ast.FunctionDef) and node in ast.walk(found)]


def test_blocked_is_raised_in_one_place() -> None:
    source = ast.parse(Path(stages.__file__).read_text())
    raised = [node for node in ast.walk(source) if isinstance(node, ast.Raise) and node.exc is not None
              and 'Blocked' in ast.unparse(node.exc)]
    assert len(raised) == 1 and enclosing(source, raised[0]) == ['blocked']
    calls = [node for node in ast.walk(source) if isinstance(node, ast.Call)]
    marks = [node for node in calls if ast.unparse(node.func) == 'mark.xfail']
    assert len(marks) == 1 and enclosing(source, marks[0]) == ['owned']
    names = [ast.unparse(node.func) for node in calls]
    assert [name for name in names if 'skip' in name or (name.endswith('xfail') and name != 'mark.xfail')] == []


def test_every_stage_marker_is_strict_and_names_an_existing_seed() -> None:
    assert len(STAGES) == 9
    for name, seed in STAGES.items():
        marks = getattr(getattr(stages, name), 'pytestmark', [])
        assert [found.name for found in marks] in ([], ['xfail']), name
        assert (REPO / seed).is_file(), seed
        for found in marks:
            assert found.kwargs.get('strict') is True and found.kwargs.get('raises') is Blocked, name
            assert not found.args and sorted(found.kwargs) == ['raises', 'reason', 'strict'], name
            assert found.kwargs['reason'].startswith(f'{seed}: '), name


def test_the_unit_module_collects_exactly_the_nine_stages() -> None:
    declared = [name for name in vars(stages) if name.startswith('test_')]
    assert declared == list(STAGES)
    assert sorted(name for name in vars(test_scenario) if name.startswith('test_')) == sorted(STAGES)
    for name in STAGES:
        assert getattr(test_scenario, name) is getattr(stages, name)
    assert test_scenario.pytestmark.name == 'unit'


Files = Dict[str, Optional[bytes]]


def packed(files: Files) -> bytes:
    """A tar.gz of `files`, with None for a directory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


BASE: Files = {'b/one.txt': b'one', 'b/sub/two.txt': b'two'}


def test_oracle_accepts_the_tree_it_was_packed_from(tmp_path: Path) -> None:
    for name, data in BASE.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    assert oracle.difference(oracle.tree(tmp_path), oracle.unpacked(packed(BASE))) == ''
    assert set(oracle.tree(tmp_path)) == {'/b', '/b/one.txt', '/b/sub', '/b/sub/two.txt'}


@mark.parametrize('files, line', [
    param({'b/one.txt': b'one'}, 'missing: /b/sub\nmissing: /b/sub/two.txt', id='missing'),
    param(BASE | {'b/three.txt': b''}, 'unexpected: /b/three.txt', id='unexpected'),
    param(BASE | {'b/one.txt': b'ONE'}, 'bytes differ: /b/one.txt', id='bytes'),
    param(BASE | {'b/sub/two.txt': b'two '}, 'bytes differ: /b/sub/two.txt', id='length'),
    param({'b/one.txt': b'one', 'b/sub': b'two'}, 'kind differs: /b/sub\nmissing: /b/sub/two.txt', id='kind'),
    param(BASE | {'b/empty': None}, 'unexpected: /b/empty', id='empty-directory'),
])
def test_oracle_reports_each_kind_of_difference(files: Files, line: str) -> None:
    assert oracle.difference(oracle.unpacked(packed(BASE)), oracle.unpacked(packed(files))) == line


def test_oracle_refuses_a_member_held_twice() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for data in (b'old', b'one'):
            info = tarfile.TarInfo('b/one.txt')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    with raises(ValueError, match='more than once: b/one.txt'):
        oracle.unpacked(buffer.getvalue())


def test_oracle_reads_past_an_end_marker() -> None:
    def plain(files: Files) -> bytes:
        return gzip.decompress(packed(files))
    blob = gzip.compress(plain(BASE) + plain({'b/extra.txt': b'x'}))
    assert oracle.difference(oracle.unpacked(packed(BASE)), oracle.unpacked(blob)) == 'unexpected: /b/extra.txt'


def test_oracle_imports_nothing_but_the_standard_library() -> None:
    names = set()
    for node in ast.walk(ast.parse(Path(oracle.__file__).read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name.split('.')[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or '').split('.')[0])
    assert names and names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


# The tap's own tests go through obstore, the client the engine uses, so what they count is what a stage counts.

def through(tap: Tap, bucket: str) -> S3Store:
    return S3Store(bucket, endpoint=tap.endpoint, region='us-east-1', access_key_id='test',
                   secret_access_key='test', client_options={'allow_http': True})


def test_tap_counts_each_kind_once_and_forwards_bytes(scene: Callable[..., Scene]) -> None:
    stage = scene(bundles=1)
    remote = through(stage.tap, stage.loop.bucket)
    payload = bytes(range(256)) * 40
    with stage.tap.window() as seen:
        obstore.put(remote, 'k/object.bin', payload)
        assert bytes(obstore.get(remote, 'k/object.bin').bytes()) == payload
        assert obstore.head(remote, 'k/object.bin')['size'] == len(payload)
        assert [item['path'] for item in obstore.list(remote, prefix='k').collect()] == ['k/object.bin']
        obstore.delete(remote, 'k/object.bin')
    assert seen.of('write') == [('PUT', 'k/object.bin'), ('POST', '')]
    assert [request.query for request in seen.requests if request.method == 'POST'] == ['delete']
    assert seen.of('read') == [('GET', 'k/object.bin')]
    assert seen.of('stat') == [('HEAD', 'k/object.bin')]
    assert seen.of('list') == [('GET', '')]
    assert len(seen.requests) == 5


def test_window_holds_only_its_own_requests(scene: Callable[..., Scene]) -> None:
    stage = scene(bundles=1)
    remote = through(stage.tap, stage.loop.bucket)
    obstore.put(remote, 'before', b'x')
    with stage.tap.window() as seen:
        obstore.head(remote, 'before')
    obstore.put(remote, 'after', b'x')
    assert [(request.kind, request.key) for request in seen.requests] == [('stat', 'before')]


def test_window_cannot_be_read_before_it_closes(scene: Callable[..., Scene]) -> None:
    stage = scene(bundles=1)
    with stage.tap.window() as seen:
        with raises(RuntimeError, match='after its block ends'):
            seen.of('write')
    assert seen.requests == []


def test_scene_refuses_a_spec_that_bypasses_the_tap(scene: Callable[..., Scene], store: str) -> None:
    stage = scene(bundles=1)
    with Tap(store) as other:
        with raises(AssertionError, match='does not name the tap'):
            Scene(loop=stage.loop, tap=other, fetch=stage.fetch)


def test_tap_refuses_a_chunked_body_and_fails_the_window(scene: Callable[..., Scene]) -> None:
    stage = scene(bundles=1)
    address = urlsplit(stage.tap.endpoint)
    with raises(AssertionError, match='refused 1 request'):
        with stage.tap.window() as seen:
            connection = http.client.HTTPConnection(address.hostname, address.port)
            connection.request('PUT', f'/{stage.loop.bucket}/chunked', body=iter([b'a', b'b']),
                               headers={'Transfer-Encoding': 'chunked'})
            assert connection.getresponse().status == 501
            connection.close()
    assert seen.requests == []
