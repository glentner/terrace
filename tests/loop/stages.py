# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The bundle loop as ordered stages, each a test. Two modules collect them: the unit lane over the
helper's own store, and the integration lane over MinIO.

A stage the tree cannot pass is a strict expected failure. Its marker names the seed that owns the
stage. What the marker accepts is narrower than "the test failed": it accepts `Blocked`, and
`Blocked` is raised in one place, by the step this tree is known to stop at, when that step raises
the exact exception recorded beside it. So:

* a stage that fails anywhere else, or at that step with any other error, fails the run;
* a stage that passes fails the run until its marker is removed;
* when a cycle moves the block, every stage behind it runs further and fails the run at its next
  stop. That cycle pins the new stop with `blocked(...)`, or removes the marker of a stage that
  now passes. The file is the loop's progress record, and it cannot go stale quietly.
"""

# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Iterator, List, Optional, Set

# Standard libs
import os
import sys
import shutil
import tomllib
import subprocess
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass

# External libs
from pytest import mark

# Internal libs
from terrace.core.exceptions import TerraceError
from terrace.tier.spec import OverlaySpec, SpecError
from terrace.engine.overlay import OverlayEngine
from terrace.engine.evict import EvictionManager
from terrace.engine.rescue import rescue
from terrace.engine.writeback import WritebackScheduler
from tests.engine.conftest import Clock
from tests.loop import oracle
from tests.loop.tap import Tap

# Public interface
__all__ = ['Blocked', 'blocked', 'Scene', 'STAGES', 'PARSER', 'REFUSAL',
           'test_stage_1_reach', 'test_stage_2_read', 'test_stage_3_pressure', 'test_stage_4_change',
           'test_stage_5_reaggregate', 'test_stage_6_read_back', 'test_stage_7_release',
           'test_stage_8_offline_commit', 'test_stage_9_several_bundles']


class Blocked(Exception):
    """A stage stopped at the step this tree is known not to pass, with the error recorded for it."""


@contextmanager
def blocked(seed: str, error: type, message: str) -> Iterator[None]:
    """
    Turn one exact failure of the enclosed step into `Blocked`.

    The type must be `error` itself and the text must equal `message`. A subclass, another text, or
    any other exception passes through untouched and fails the stage.

    Only a refusal can be pinned: one of the package's own errors, an `OSError`, or a stage's own
    failed assertion. A slip in a stage (`AttributeError`, `TypeError`, `NameError`) is never one.
    """
    if not issubclass(error, (TerraceError, OSError, AssertionError)):
        raise TypeError(f'{error.__name__} is not a refusal a stage may be blocked by')
    try:
        yield
    except Exception as caught:
        if type(caught) is error and str(caught) == message:
            raise Blocked(f'{seed}: {message}') from caught
        raise


# Where this tree stops: the spec parser refuses the archive tier's keys before it reads the type.
PARSER = 'issues/archive-spec-plumbing.md'
REFUSAL = {'single': "Unknown key(s) in tier 'bundles': location, staging, sync",
           'sharded': "Unknown key(s) in tier 'bundles': location, sharded, sync"}

# Stage name -> the seed that owns it. The marker audit in the support tests reads this table.
STAGES: Dict[str, str] = {
    'test_stage_1_reach': 'issues/archive-spec-plumbing.md',
    'test_stage_2_read': 'issues/archive-spec-plumbing.md',
    'test_stage_3_pressure': 'issues/eviction-one-hop.md',
    'test_stage_4_change': 'issues/namespace-sync-redesign.md',
    'test_stage_5_reaggregate': 'issues/archive-finalization-integration.md',
    'test_stage_6_read_back': 'issues/archive-finalization-integration.md',
    'test_stage_7_release': 'issues/shutdown-rescue-gaps.md',
    'test_stage_8_offline_commit': 'issues/offline-commit-command.md',
    'test_stage_9_several_bundles': 'issues/sharded-archive-sources.md',
}


def owned(name: str, what: str) -> Callable:
    """The strict expected-failure marker of a stage: accepts `Blocked` only, and names the owner."""
    return mark.xfail(raises=Blocked, strict=True, reason=f'{STAGES[name]}: {what}')


@dataclass
class Scene:
    """What a stage works on: the helper's loop, the tap its spec points at, and the oracle's view."""

    loop: object                     # tools/bundle_loop.py Loop, built through the tap
    tap: Tap
    fetch: Callable[[str], bytes]    # an object's bytes by key, read from the store itself, not through the tap

    def __post_init__(self) -> None:
        """A count means nothing unless the spec sends the engine through the tap."""
        source = tomllib.loads(Path(self.loop.spec).read_text())['tier'][0]
        assert source['location']['s3']['endpoint'] == self.tap.endpoint, 'the spec does not name the tap'

    def expected(self) -> oracle.Tree:
        """The oracle's tree: one top-level directory per bundle."""
        return oracle.tree(self.loop.content)

    def assert_object(self, stem: str) -> None:
        """The bundle's object unpacks to the oracle's directory for that bundle."""
        want = {name: data for name, data in self.expected().items()
                if name == f'/{stem}' or name.startswith(f'/{stem}/')}
        assert len(want) > 1, f'the oracle holds nothing for {stem}'
        assert not (report := oracle.difference(want, oracle.unpacked(self.fetch(self.loop.keys[stem])))), report


# -- the steps stages share ------------------------------------------------------------------------------------------

def reach(scene: Scene, clock: Clock, spec: Optional[Path] = None) -> OverlayEngine:
    """Load the spec the helper wrote and start an engine over it."""
    with blocked(PARSER, SpecError, REFUSAL[scene.loop.form]):
        loaded = OverlaySpec.load(spec or scene.loop.spec)
    engine = OverlayEngine(loaded, clock=clock)
    engine.start()
    return engine


def listing(engine: OverlayEngine) -> oracle.Tree:
    """Everything the engine lists from the root, every file read in full."""
    found: oracle.Tree = {}
    pending = ['/']
    while pending:
        for entry in engine.readdir(pending.pop()):
            if entry.kind == 'dir':
                found[entry.path] = None
                pending.append(entry.path)
            else:
                handle = engine.open(entry.path, os.O_RDONLY)
                try:
                    found[entry.path] = engine.read(handle, entry.size + 1, 0)
                finally:
                    engine.release(handle)
    return found


def assert_tree(engine: OverlayEngine, scene: Scene) -> None:
    """The engine lists and reads exactly the oracle's tree, which is never empty."""
    want = scene.expected()
    assert any(data is not None for data in want.values()), 'the oracle holds no file'
    assert not (report := oracle.difference(want, listing(engine))), report


def change(engine: OverlayEngine, scene: Scene, stem: str) -> None:
    """Modify, create, rename and remove one member each inside one bundle, in the engine and in the oracle."""
    top = scene.loop.content / stem
    files = sorted(path for path in top.rglob('*') if path.is_file())
    assert len(files) >= 4, 'a bundle needs four members for the change stage'
    modified, renamed, removed = files[0], files[1], files[2]

    def name(path: Path) -> str:
        return '/' + path.relative_to(scene.loop.content).as_posix()

    handle = engine.open(name(modified), os.O_RDWR)
    try:
        engine.write(handle, b'modified in place\n', 0)
    finally:
        engine.release(handle)
    data = bytearray(modified.read_bytes())
    data[:18] = b'modified in place\n'
    modified.write_bytes(bytes(data))

    created = top / 'created.txt'
    engine.create(name(created))
    handle = engine.open(name(created), os.O_WRONLY)
    try:
        engine.write(handle, b'created through the overlay\n', 0)
    finally:
        engine.release(handle)
    created.write_bytes(b'created through the overlay\n')

    moved = renamed.with_name('renamed.txt')
    engine.rename(name(renamed), name(moved))
    renamed.rename(moved)

    engine.unlink(name(removed))
    removed.unlink()


def expire_delays(scheduler: WritebackScheduler, clock: Clock, scene: Scene) -> None:
    """
    Let every tier's delay expire, a hop at a time and well inside one re-aggregation interval, so
    everything that changed reaches the durable tier while the bundle is not yet due.
    """
    hops = len(scene.loop.tiers) + 1
    assert hops * (scene.loop.delay + 1) < scene.loop.resync_interval
    for _ in range(hops):
        clock.now += scene.loop.delay + 1
        scheduler.poll()


def cross_interval(scheduler: WritebackScheduler, clock: Clock, scene: Scene) -> None:
    """
    Cross the bundle's re-aggregation interval once and poll until the queue is idle. Crossing it
    round after round would let a conforming engine push the bundle once per round.
    """
    clock.now += scene.loop.resync_interval + 1
    for _ in range(len(scene.loop.tiers) + 2):
        if not scheduler.poll():
            return
        clock.now += 1
    raise AssertionError('the write-back queue did not go idle after the interval')


def settle(engine: OverlayEngine, clock: Clock, scene: Scene) -> None:
    """Let every tier's delay expire, then cross the bundle's re-aggregation interval once."""
    scheduler = WritebackScheduler(engine)
    expire_delays(scheduler, clock, scene)
    cross_interval(scheduler, clock, scene)


def holders(engine: OverlayEngine, name: str) -> Set[str]:
    """The tiers the catalog records a copy of `name` on."""
    return {copy.tier for copy in engine.residencies(name)}


def read(engine: OverlayEngine, name: str, size: int) -> bytes:
    """One file, read in full through a handle."""
    handle = engine.open(name, os.O_RDONLY)
    try:
        return engine.read(handle, size + 1, 0)
    finally:
        engine.release(handle)


def detach(engine: OverlayEngine) -> None:
    """Tell the engine to detach: commit what changed to the durable tier, then stop."""
    result = rescue(engine)
    assert result.complete and not result.stranded
    engine.stop('detached')


def release_node(scene: Scene) -> None:
    """The node is gone: nothing of the node-scope tiers remains."""
    for name in ('tmp', 'shm'):
        shutil.rmtree(scene.loop.tiers[name].parent)


def node_copies(engine: OverlayEngine, tree: oracle.Tree) -> List[str]:
    """Every copy the catalog records on a node-scope tier, as 'tier:path'."""
    return [f'{copy.tier}:{name}' for name in tree
            for copy in engine.residencies(name) if copy.tier in ('tmp', 'shm')]


def offline_commit(scene: Scene, site: Path) -> None:
    """Run the full commit in a second process that has the spec, the state directory and the durable tier."""
    env = {name: value for name, value in os.environ.items() if not name.startswith('TERRACE_')}
    env.update(XDG_CONFIG_HOME=str(site / 'config'), XDG_STATE_HOME=str(site / 'state'))
    done = subprocess.run([sys.executable, '-m', 'terrace', 'commit', str(scene.loop.spec)],
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


# -- the stages ------------------------------------------------------------------------------------------------------

@owned('test_stage_1_reach', 'the spec loads and an engine starts over it')
def test_stage_1_reach(scene: Callable[..., Scene], clock: Clock) -> None:
    """The spec loads through the spec parser and an engine starts over it."""
    stage = scene(bundles=1)
    engine = reach(stage, clock)
    try:
        assert engine.started
        assert engine.spec.source.type == 'tar.gz'
    finally:
        engine.stop()


@owned('test_stage_2_read', 'members list and read through the overlay')
def test_stage_2_read(scene: Callable[..., Scene], clock: Clock) -> None:
    """A listing gives the oracle's names, and every member read matches the oracle's bytes."""
    stage = scene(bundles=1)
    engine = reach(stage, clock)
    try:
        assert_tree(engine, stage)
    finally:
        engine.stop()


@owned('test_stage_3_pressure', 'pressure moves a copy one tier up and no further')
def test_stage_3_pressure(scene: Callable[..., Scene], clock: Clock) -> None:
    """With the fastest tier's limits below the bytes read, every read succeeds and each copy pushed out is one up."""
    stage = scene(bundles=1, fastest_size=2048)
    files = {name: data for name, data in stage.expected().items() if data is not None}
    assert max(map(len, files.values())) <= 2048 < sum(map(len, files.values()))
    engine = reach(stage, clock)
    with stage.tap.window() as seen:
        landed = []                                  # files a read left on the fastest tier
        for name, data in files.items():
            assert read(engine, name, len(data)) == data
            if 'shm' in holders(engine, name):
                landed.append(name)
        EvictionManager(engine).poll()
        pushed = [name for name in landed if 'shm' not in holders(engine, name)]
        assert pushed, 'nothing was pushed out of the fastest tier'
        assert [name for name in pushed if 'tmp' not in holders(engine, name)] == []
        assert_tree(engine, stage)
        engine.stop()
    assert seen.of('read'), 'the engine read nothing from the source through the tap'
    assert seen.of('write') == []


@owned('test_stage_4_change', 'changes inside a bundle list and read back')
def test_stage_4_change(scene: Callable[..., Scene], clock: Clock) -> None:
    """One member modified, one created, one renamed, one removed; the engine lists and reads the oracle's tree."""
    stage = scene(bundles=1)
    engine = reach(stage, clock)
    try:
        change(engine, stage, 'bundle-000')
        assert_tree(engine, stage)
    finally:
        engine.stop()


@owned('test_stage_5_reaggregate', 'a changed bundle is rebuilt on its delay and pushed as one object')
def test_stage_5_reaggregate(scene: Callable[..., Scene], clock: Clock) -> None:
    """
    After the clock passes the bundle's interval and the engine polls, the object unpacks to the
    oracle's tree. Nothing is pushed before the interval expires: a write-back that repacks on every
    change is what the delay exists to prevent.
    """
    stage = scene(bundles=1)
    engine = reach(stage, clock)
    try:
        scheduler = WritebackScheduler(engine)
        with stage.tap.window() as early:
            change(engine, stage, 'bundle-000')
            expire_delays(scheduler, clock, stage)
        with stage.tap.window() as due:
            cross_interval(scheduler, clock, stage)
        assert early.of('write') == []
        assert due.of('write') == [('PUT', stage.loop.keys['bundle-000'])]
        stage.assert_object('bundle-000')
    finally:
        engine.stop()


@owned('test_stage_6_read_back', 'a fresh engine reads the rebuilt bundle from the bucket')
def test_stage_6_read_back(scene: Callable[..., Scene], clock: Clock) -> None:
    """A second engine on fresh state over the same bucket lists and reads the same tree."""
    stage = scene(bundles=1)
    engine = reach(stage, clock)
    try:
        change(engine, stage, 'bundle-000')
        settle(engine, clock, stage)
    finally:
        engine.stop()
    second = reach(stage, Clock(), spec=stage.loop.respec('second'))
    try:
        assert_tree(second, stage)
    finally:
        second.stop()


@owned('test_stage_7_release', 'a detach leaves every change on the durable tier and nothing on the node')
def test_stage_7_release(scene: Callable[..., Scene], clock: Clock) -> None:
    """After a detach every change is on the durable tier, no copy is recorded on a node-scope tier."""
    stage = scene(bundles=1)
    engine = reach(stage, clock)
    with stage.tap.window() as seen:
        change(engine, stage, 'bundle-000')
        detach(engine)
    assert seen.of('write') == []
    release_node(stage)
    after = reach(stage, Clock())
    try:
        assert_tree(after, stage)
        assert node_copies(after, stage.expected()) == []
    finally:
        after.stop()


@owned('test_stage_8_offline_commit', 'a second process commits the changed bundle and keeps it staged')
def test_stage_8_offline_commit(scene: Callable[..., Scene], clock: Clock, temp_site: Path) -> None:
    """A second process runs the full commit; the object matches the oracle and the staged bundle stays."""
    stage = scene(bundles=1)
    engine = reach(stage, clock)
    change(engine, stage, 'bundle-000')
    detach(engine)
    release_node(stage)
    offline_commit(stage, temp_site)
    stage.assert_object('bundle-000')
    assert stage.loop.staging.is_file()
    bundles = set(stage.loop.keys.values())
    with stage.tap.window() as seen:
        after = reach(stage, Clock())
        try:
            assert_tree(after, stage)
        finally:
            after.stop()
    assert seen.requests, 'the engine made no request through the tap'
    assert [key for _, key in seen.of('read') if key in bundles] == []


@owned('test_stage_9_several_bundles', 'a change to one bundle leaves every other object untouched')
def test_stage_9_several_bundles(scene: Callable[..., Scene], clock: Clock) -> None:
    """Over several bundles, after a change to one and its re-aggregation, every other object is byte-identical."""
    stage = scene(bundles=3)
    before = {stem: stage.fetch(key) for stem, key in stage.loop.keys.items()}
    engine = reach(stage, clock)
    try:
        assert_tree(engine, stage)
        with stage.tap.window() as seen:
            change(engine, stage, 'bundle-000')
            settle(engine, clock, stage)
        assert seen.of('write') == [('PUT', stage.loop.keys['bundle-000'])]
        stage.assert_object('bundle-000')
        others = [stem for stem in before if stem != 'bundle-000']
        assert len(others) == 2
        assert [stem for stem in others if stage.fetch(stage.loop.keys[stem]) != before[stem]] == []
    finally:
        engine.stop()
