# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The helper's own tests: what it builds, and that every exit path leaves nothing behind."""

# Type annotations
from __future__ import annotations
from typing import Any, Tuple

# Standard libs
import io
import os
import re
import ast
import sys
import time
import signal
import socket
import tarfile
import tomllib
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

# External libs
from pytest import fail, mark, raises, MonkeyPatch

# Internal libs
from tests.loop import bundle_loop, HELPER
from tests.loop.conftest import S3_UNAVAILABLE

pytestmark = [mark.unit, mark.skipif(bool(S3_UNAVAILABLE), reason=str(S3_UNAVAILABLE))]


def refuses(endpoint: str) -> bool:
    """Whether nothing listens at the endpoint any more."""
    address = urlsplit(endpoint)
    try:
        probe = socket.create_connection((address.hostname, address.port), timeout=2)
    except ConnectionRefusedError:
        return True
    with probe:
        # A free loopback port can be handed to the probe itself as its source: TCP then connects the
        # socket to itself, and nothing listens there.
        return probe.getsockname() == probe.getpeername()


def client(endpoint: str) -> Any:
    import boto3
    return boto3.client('s3', endpoint_url=endpoint, region_name='us-east-1',
                        aws_access_key_id='test', aws_secret_access_key='test')


def buckets(endpoint: str) -> set[str]:
    return {bucket['Name'] for bucket in client(endpoint).list_buckets()['Buckets']}


def test_store_answers_then_refuses_after_stop() -> None:
    running = bundle_loop.Store().start()
    try:
        assert client(running.endpoint).list_buckets()['Buckets'] == []
    finally:
        running.stop()
    assert refuses(running.endpoint)


def test_single_bundle_spec_and_layout(store: str, tmp_path: Path) -> None:
    with bundle_loop.Loop(store, parent=tmp_path) as loop:
        assert loop.form == 'single'
        data = tomllib.loads(loop.spec.read_text())
        source, *stages = data['tier']
        assert (source['type'], source['role']) == ('tar.gz', 'source')
        assert source['location']['url'] == f's3://{loop.bucket}/bundles/bundle-000.tar.gz'
        assert source['location']['s3']['endpoint'] == store
        assert source['sync'] == {'max_frequency': '24/day'} and loop.resync_interval == 3600
        assert 'sharded' not in source and 'staging' not in source and not hasattr(loop, 'staging')
        assert [(tier['name'], tier['scope']) for tier in stages] == [('scratch', 'shared'), ('tmp', 'node'),
                                                                      ('shm', 'node')]
        for tier in stages:
            assert set(tier['quota']) == {'size', 'files'} and tier['writeback'] == {'delay': '60s'}
        assert loop.delay == 60
        roots = [Path(tier['path']) for tier in stages]
        assert roots == [loop.tiers[name] for name in ('scratch', 'tmp', 'shm')]
        state = Path(data['overlay']['state'])
        assert state == loop.state
        for root in roots + [state]:
            assert root.is_relative_to(loop.root)
        for i, root in enumerate(roots):
            assert not state.is_relative_to(root) and not root.is_relative_to(state)
            for other in roots[i + 1:]:
                assert not root.is_relative_to(other) and not other.is_relative_to(root)


def test_several_bundles_use_the_sharded_form(store: str, tmp_path: Path) -> None:
    with bundle_loop.Loop(store, bundles=3, parent=tmp_path) as loop:
        assert loop.form == 'sharded' and not hasattr(loop, 'staging')
        source = tomllib.loads(loop.spec.read_text())['tier'][0]
        assert source['sharded'] is True and 'staging' not in source
        assert source['location']['url'] == f's3://{loop.bucket}/bundles/*.tar.gz'
        listed = client(store).list_objects_v2(Bucket=loop.bucket)['Contents']
        assert sorted(item['Key'] for item in listed) == [f'bundles/bundle-00{n}.tar.gz' for n in range(3)]


def test_bundles_are_kilobyte_tarballs_of_their_trees(store: str, tmp_path: Path) -> None:
    with bundle_loop.Loop(store, bundles=2, parent=tmp_path) as loop:
        seen = set()
        for stem, key in loop.keys.items():
            blob = client(store).get_object(Bucket=loop.bucket, Key=key)['Body'].read()
            assert 2 * 1024 <= len(blob) <= 16 * 1024
            with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as archive:
                found = {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}
                assert {member.name.split('/')[0] for member in archive} == {stem}
            want = {item.relative_to(loop.content).as_posix(): item.read_bytes()
                    for item in (loop.content / stem).rglob('*') if item.is_file()}
            assert len(want) == 6 and found == want
            assert {Path(name).parent.as_posix() for name in want} == {stem, f'{stem}/sub'}
            seen |= set(want.values())
        assert len(seen) == 12


def test_respec_gives_fresh_directories_over_the_same_bucket(store: str, tmp_path: Path) -> None:
    with bundle_loop.Loop(store, parent=tmp_path) as loop:
        first = tomllib.loads(loop.spec.read_text())
        second = tomllib.loads(loop.respec('second').read_text())
        assert second['tier'][0]['location'] == first['tier'][0]['location']
        assert second['overlay']['state'] != first['overlay']['state']
        assert {tier['path'] for tier in second['tier'][1:]}.isdisjoint(tier['path'] for tier in first['tier'][1:])


def test_close_removes_bucket_and_directory(store: str, tmp_path: Path) -> None:
    loop = bundle_loop.Loop(store, parent=tmp_path)
    assert loop.root.is_dir() and loop.bucket in buckets(store)
    loop.close()
    assert not loop.root.exists()
    assert loop.bucket not in buckets(store)
    loop.close()


def test_failed_setup_leaves_nothing(store: str, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    def broken(self: Any, label: str) -> Path:
        raise RuntimeError('planted')
    monkeypatch.setattr(bundle_loop.Loop, 'respec', broken)
    before = buckets(store)
    with raises(RuntimeError, match='planted'):
        bundle_loop.Loop(store, parent=tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert buckets(store) == before


def test_setup_error_in_main_stops_the_store(monkeypatch: MonkeyPatch) -> None:
    started = []
    real = bundle_loop.Store.start
    monkeypatch.setattr(bundle_loop.Store, 'start', lambda self: started.append(real(self)) or self)
    def broken(self: Any, label: str) -> Path:
        raise RuntimeError('planted')
    monkeypatch.setattr(bundle_loop.Loop, 'respec', broken)
    with raises(RuntimeError, match='planted'):
        bundle_loop.main([])
    assert len(started) == 1 and refuses(started[0].endpoint)


# Each way out of `main` a test can reach in its own process, and what `main` gives back on it: the
# status, or the type of what it raises. A signal on one of these comes from the test itself.
IN_PROCESS = {'command': 0, 'error': RuntimeError, 'before-the-loop': 128 + signal.SIGTERM,
              'in-setup': 128 + signal.SIGHUP, 'in-the-command': 128 + signal.SIGINT, 'waiting': 0}
HEARD = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def down(path: str, monkeypatch: MonkeyPatch) -> object:
    """Run the helper's `main` in this process down one exit path; its status, or the type it raised."""
    argv = ['--', sys.executable, '-c', 'pass']
    if path == 'error':
        def broken(self: Any, label: str) -> Path:
            raise RuntimeError('planted')
        monkeypatch.setattr(bundle_loop.Loop, 'respec', broken)
    elif path == 'before-the-loop':
        start = bundle_loop.Store.start
        def started(self: Any) -> Any:
            start(self)
            signal.raise_signal(signal.SIGTERM)
            return self
        monkeypatch.setattr(bundle_loop.Store, 'start', started)
    elif path == 'in-setup':
        generate = bundle_loop.Loop._generate
        def generating(self: Any, *args: Any) -> None:
            signal.raise_signal(signal.SIGHUP)
            generate(self, *args)
        monkeypatch.setattr(bundle_loop.Loop, '_generate', generating)
    elif path == 'in-the-command':
        argv = ['--', sys.executable, '-c', SIGNAL_SELF, str(int(signal.SIGINT))]
    elif path == 'waiting':
        argv = []
        class Watch(io.StringIO):
            def write(self, text: str) -> int:
                if 'interrupt to tear down' in text:
                    signal.raise_signal(signal.SIGTERM)
                return len(text)
        monkeypatch.setattr(sys, 'stderr', Watch())
    try:
        return bundle_loop.main(argv)
    except Exception as error:
        return type(error)


@mark.parametrize('path', list(IN_PROCESS))
def test_main_in_process_gives_the_caller_its_signal_handling_back(path: str, monkeypatch: MonkeyPatch) -> None:
    # The caller has handlers of its own, a wakeup fd and SIGHUP blocked; after `main`, on whichever
    # path it took, all three are as they were, and a real signal of each kind reaches the caller.
    heard = []
    def caller(signum: int, frame: object) -> None:
        heard.append(signum)
    read, write = os.pipe()
    for end in (read, write):
        os.set_blocking(end, False)
    handlers = {signum: signal.getsignal(signum) for signum in HEARD}
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGHUP])
    wakeup = None
    try:
        wakeup = signal.set_wakeup_fd(write)
        for signum in HEARD:
            signal.signal(signum, caller)
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])
        assert signal.SIGHUP in blocked
        assert down(path, monkeypatch) == IN_PROCESS[path]
        assert {signum: signal.getsignal(signum) for signum in HEARD} == dict.fromkeys(HEARD, caller)
        assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == blocked
        assert signal.set_wakeup_fd(write) == write
        assert heard == []
        signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGHUP])
        for signum in HEARD:
            signal.raise_signal(signum)
        assert heard == list(HEARD)
        assert sorted(os.read(read, 16)) == sorted(HEARD)
    finally:
        if wakeup is not None:
            signal.set_wakeup_fd(wakeup)
        for signum, handler in handlers.items():
            signal.signal(signum, signal.SIG_DFL if handler is None else handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        os.close(read)
        os.close(write)


# A process that runs the helper's `main` in process, then waits to be interrupted with Python's own
# SIGINT handling, as pytest does after a helper test.
AFTER_MAIN = '''
import sys, time
from importlib.util import spec_from_file_location, module_from_spec
found = spec_from_file_location("bundle_loop", sys.argv[1])
helper = module_from_spec(found)
found.loader.exec_module(helper)
print("main returned", helper.main(["--", sys.executable, "-c", "pass"]), flush=True)
time.sleep(30)
'''


def test_an_interrupt_after_main_in_process_stops_the_caller() -> None:
    process = subprocess.Popen([sys.executable, '-c', AFTER_MAIN, str(HELPER)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        process.stdout.readline()                   # the spec's path
        assert process.stdout.readline() == 'main returned 0\n'
        process.send_signal(signal.SIGINT)
        try:
            out, err = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            fail('SIGINT did not stop a process after the helper ran in it')
    finally:
        process.kill()
        process.wait()
    assert process.returncode == -signal.SIGINT and 'KeyboardInterrupt' in err, err


PROBE = ('import os, sys, tomllib; d = tomllib.load(open(os.environ["BUNDLE_LOOP_SPEC"], "rb")); '
         'print(d["tier"][0]["location"]["s3"]["endpoint"]); sys.exit(int(sys.argv[1]))')


def run_command(status: int) -> Tuple[int, Path, str]:
    done = subprocess.run([sys.executable, str(HELPER), '--', sys.executable, '-c', PROBE, str(status)],
                          capture_output=True, text=True, timeout=60)
    spec, endpoint = done.stdout.split()
    return done.returncode, Path(spec), endpoint


def test_command_exit_tears_down() -> None:
    status, spec, endpoint = run_command(0)
    assert status == 0 and not spec.parent.exists() and refuses(endpoint)


def test_command_failure_passes_status_and_tears_down() -> None:
    status, spec, endpoint = run_command(3)
    assert status == 3 and not spec.parent.exists() and refuses(endpoint)


# A command that a signal ends as it ends `sleep`: by the signal, with no traceback of its own.
RUNNING = ('import time, signal; signal.signal(signal.SIGINT, signal.SIG_DFL); '
           'print("command running", flush=True); time.sleep(60)')


@mark.parametrize('name', ['int', 'term', 'hup'])
def test_a_signal_to_the_process_group_exits_128_plus_its_number(name: str) -> None:
    # A terminal's Ctrl-C, or its closing, signals every process of the job; the command usually
    # ends first, and the helper reports it as a shell would, as it does when it is signalled first.
    signum = SIGNALS[name]
    process = subprocess.Popen([sys.executable, str(HELPER), '--', sys.executable, '-c', RUNNING],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        spec = Path(process.stdout.readline().strip())
        endpoint = tomllib.loads(spec.read_text())['tier'][0]['location']['s3']['endpoint']
        assert process.stdout.readline() == 'command running\n'
        os.killpg(process.pid, signum)
        out, err = process.communicate(timeout=30)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    assert process.returncode == 128 + signum, err
    assert 'Traceback' not in err, err
    assert not spec.parent.exists() and refuses(endpoint)


def stop_with(signum: int, again: bool = False, **options: Any) -> None:
    """Start the helper with no command, signal it once it is up, and check it left nothing."""
    process = subprocess.Popen([sys.executable, str(HELPER)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, **options)
    try:
        spec = Path(process.stdout.readline().strip())
        endpoint = tomllib.loads(spec.read_text())['tier'][0]['location']['s3']['endpoint']
        assert spec.is_file() and not refuses(endpoint)
        process.stderr.readline()                   # the helper is waiting
        process.send_signal(signum)
        if again:
            time.sleep(0.005)
            process.send_signal(signum)
        assert process.wait(timeout=30) == 0
    finally:
        process.kill()
        process.wait()
    assert not spec.parent.exists() and refuses(endpoint)


def test_interrupt_tears_down() -> None:
    stop_with(signal.SIGINT)


def test_terminate_tears_down() -> None:
    stop_with(signal.SIGTERM)


def test_interrupt_tears_down_when_started_with_it_ignored() -> None:
    stop_with(signal.SIGINT, preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))


def test_a_second_signal_during_teardown_changes_nothing() -> None:
    stop_with(signal.SIGTERM, again=True)


def test_close_fails_loudly_when_the_directory_survives(store: str, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    loop = bundle_loop.Loop(store, parent=tmp_path)
    locked = loop.root / 'locked'
    locked.mkdir()
    (locked / 'file').write_bytes(b'x')
    locked.chmod(0o500)
    loop.close()
    assert not loop.root.exists()
    second = bundle_loop.Loop(store, parent=tmp_path)
    def refuse(target: Any, *args: Any, ignore_errors: bool = False, **kwargs: Any) -> None:
        if not ignore_errors:                       # as shutil.rmtree does: an ignored error is silent
            raise PermissionError(13, 'planted', str(target))
    monkeypatch.setattr(bundle_loop.shutil, 'rmtree', refuse)
    with raises(PermissionError):
        second.close()
    monkeypatch.undo()
    second.close()
    assert not second.root.exists()


# Starts a server with `socket.getfqdn` replaced by one that reports the call and raises, then sends
# it one request. A server that names itself by a reverse lookup of its address fails here on every
# host, where a slow resolver would fail it only on the hosts that have one. Run in a process of its
# own and bounded: a store whose serving thread dies before it is ready can leave its start waiting.
NO_LOOKUP = '''
import sys, socket
from http.client import HTTPConnection
from urllib.parse import urlsplit
def refuse(*args):
    print("getfqdn called:", *args, file=sys.stderr, flush=True)
    raise OSError("planted: no server may look its own name up")
socket.getfqdn = refuse
from tests.loop import bundle_loop
from tests.loop.tap import Tap
store = bundle_loop.Store().start()
try:
    endpoint = store.endpoint
    if sys.argv[1] == "tap":
        tap = Tap(store.endpoint).start()
        endpoint = tap.endpoint
    address = urlsplit(endpoint)
    connection = HTTPConnection(address.hostname, address.port, timeout=5)
    connection.request("GET", "/")
    print("answered", connection.getresponse().status, flush=True)
    connection.close()
    if sys.argv[1] == "tap":
        tap.stop()
finally:
    store.stop()
'''


@mark.parametrize('server', ['store', 'tap'])
def test_no_server_looks_up_its_own_name(server: str) -> None:
    try:
        done = subprocess.run([sys.executable, '-c', NO_LOOKUP, server], cwd=HELPER.parents[1],
                              capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as error:
        err = error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr
        fail(f'the {server} did not start and answer within 15 s\n--- stderr\n{err}')
    assert 'getfqdn called' not in done.stderr, done.stderr
    assert done.returncode == 0 and done.stdout.startswith('answered '), done.stderr


def test_agents_md_lists_every_file_flag_and_variable_of_the_loop() -> None:
    # Where AGENTS.md lists the commands, every option `--help` shows and every BUNDLE_LOOP_ variable
    # the helper or its tests use; anywhere in it, every module of the loop.
    text = (HELPER.parents[1] / 'AGENTS.md').read_text()
    commands = text.split('\n## Commands\n', 1)[1].split('\n## ', 1)[0]
    flags = set(re.findall(r'(?<![\w-])--[a-z][a-z-]*', bundle_loop.arguments().format_help())) - {'--help'}
    modules = sorted(Path(__file__).parent.glob('*.py'))
    names = {name for source in [HELPER, *modules] for name in re.findall(r'\bBUNDLE_LOOP_[A-Z_]+', source.read_text())}
    assert {'--bundles', '--endpoint', '--fastest-size'} <= flags
    assert {'BUNDLE_LOOP_SPEC', 'BUNDLE_LOOP_ENDPOINT', 'BUNDLE_LOOP_BUCKET', 'BUNDLE_LOOP_EVERY_SIGNAL'} <= names
    assert sorted(name for name in flags | names if name not in commands) == []
    files = [HELPER.relative_to(HELPER.parents[1]).as_posix()]
    files += [f'tests/loop/{module.name}' for module in modules if module.name != '__init__.py']
    assert [name for name in files if name not in text and Path(name).name not in text] == []


def test_helper_imports_nothing_of_the_package() -> None:
    names = set()
    for node in ast.walk(ast.parse(HELPER.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name.split('.')[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or '').split('.')[0])
    assert names and 'terrace' not in names and 'tests' not in names


# Runs the helper in a process of its own, with the client library's send patched to signal the
# process from inside the n-th request (`n:signum`, comma-separated), or, given `thread:signum`, to
# signal a thread other than the main one once the helper is waiting. Each request's number, URL and
# time since the driver started go to standard error, with the times the helper was loaded, the store
# answered and `main` returned. A process that outlives the test's wait leaves the stack of every
# thread there too: once at DUMP_AFTER seconds, and again on SIGUSR1, which the test sends before it
# kills the process. Both dumps come from faulthandler's own C handler and watchdog thread, so they
# appear whatever the main thread is blocked in and whatever the helper's handlers do.
DUMP_AFTER = 15
DRIVER = '''
import os, sys, time, signal, threading, faulthandler
began = time.monotonic()
faulthandler.register(signal.SIGUSR1, file=sys.__stderr__, all_threads=True)
faulthandler.dump_traceback_later(float(os.environ["BUNDLE_LOOP_DUMP_AFTER"]), file=sys.__stderr__)
def mark(*what):
    print(*what, f"+{time.monotonic() - began:.2f}s", file=sys.stderr, flush=True)
from importlib.util import spec_from_file_location, module_from_spec
from botocore.httpsession import URLLib3Session
found = spec_from_file_location("bundle_loop", sys.argv[1])
helper = module_from_spec(found)
found.loader.exec_module(helper)
mark("driver: helper loaded")
plan, count, real = sys.argv[2], [0], URLLib3Session._get_request_target
at = {} if plan.startswith("thread:") else {int(n): int(s) for n, s in (pair.split(":") for pair in plan.split(",") if pair)}
def target(self, url, proxy_url):
    count[0] += 1
    mark("request", count[0], url)
    if count[0] in at:
        os.kill(os.getpid(), at[count[0]])
    return real(self, url, proxy_url)
URLLib3Session._get_request_target = target
start = helper.Store.start
def started(self):
    try:
        return start(self)
    finally:
        mark("driver: store start returned")
helper.Store.start = started
class Watch:
    def __init__(self, stream):
        self.stream = stream
    def write(self, text):
        if plan.startswith("thread:") and "interrupt to tear down" in text:
            def later():
                time.sleep(0.5)
                signal.pthread_kill(threading.get_ident(), int(plan.split(":")[1]))
            threading.Thread(target=later, daemon=True).start()
        return self.stream.write(text)
    def flush(self):
        self.stream.flush()
sys.stderr = Watch(sys.stderr)
status = helper.main(sys.argv[3:])
mark("driver: main returned", status)
sys.exit(status)
'''

# With one bundle the helper makes five requests: the bucket and the bundle in setup, then the
# listing, the bundle and the bucket in teardown. The status None is 128 plus the signal's number.
WINDOWS = {
    'setup-1': ('1:{0}', False, 0), 'setup-2': ('2:{0}', False, 0),
    'teardown-3': ('3:{0}', True, 0), 'teardown-4': ('4:{0}', True, 0), 'teardown-5': ('5:{0}', True, 0),
    'second': ('1:{0},2:{0}', False, 0), 'thread': ('thread:{0}', False, 0), 'command': ('', True, None),
}
SIGNALS = {'int': signal.SIGINT, 'term': signal.SIGTERM, 'hup': signal.SIGHUP}
MODES = ('legacy', 'standard')
# The suite runs one representative per window kind; the class checker sets the variable for every one.
EVERY = [(window, name, mode) for window in WINDOWS for name in SIGNALS for mode in MODES]
SOME = [('setup-1', 'term', 'standard'), ('setup-2', 'int', 'legacy'), ('teardown-4', 'hup', 'standard'),
        ('second', 'int', 'standard'), ('thread', 'int', 'legacy'), ('command', 'term', 'legacy')]
CASES = EVERY if os.environ.get('BUNDLE_LOOP_EVERY_SIGNAL') == '1' else SOME
SIGNAL_SELF = ('import os, sys, time; os.kill(os.getppid(), int(sys.argv[1])); time.sleep(60)')


@mark.parametrize('window, name, mode', CASES, ids=['-'.join(case) for case in CASES])
def test_a_signal_in_any_window_tears_down_on_the_first(window: str, name: str, mode: str, tmp_path: Path) -> None:
    plan, command, status = WINDOWS[window]
    signum = SIGNALS[name]
    status = 128 + signum if status is None else status
    scratch = tmp_path / 'tmp'
    scratch.mkdir()
    env = dict(os.environ, TMPDIR=str(scratch), AWS_RETRY_MODE=mode, AWS_CONFIG_FILE=str(tmp_path / 'absent'),
               BUNDLE_LOOP_DUMP_AFTER=str(DUMP_AFTER))
    argv = [sys.executable, '-c', DRIVER, str(HELPER), plan.format(int(signum))]
    if window == 'command':
        argv += ['--', sys.executable, '-c', SIGNAL_SELF, str(int(signum))]
    elif command:
        argv += ['--', sys.executable, '-c', 'pass']
    began = time.monotonic()
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    hung = False
    try:
        out, err = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        # Ask for a second stack dump, then collect what the process wrote; a retried communicate
        # keeps the output the first one read.
        hung = True
        process.send_signal(signal.SIGUSR1)
        try:
            out, err = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
    finally:
        process.kill()
        process.wait()
    if hung:
        fail(f'the helper did not exit within 20 s; it ended {time.monotonic() - began:.1f} s after it '
             f'started, with status {process.returncode} ({window} {name} {mode})\n'
             f'--- stdout\n{out}\n--- stderr\n{err}')
    requests = [line.split() for line in err.splitlines() if line.startswith('request ')]
    endpoints = {urlsplit(fields[2])._replace(path='', query='').geturl() for fields in requests}
    assert len(endpoints) == 1, err
    planned = [int(pair.split(':')[0]) for pair in plan.split(',') if pair and not pair.startswith('thread')]
    assert all(n <= len(requests) for n in planned), err      # every planned signal was sent
    assert process.returncode == status, err
    assert 'Traceback' not in err, err
    assert list(scratch.iterdir()) == [] and refuses(endpoints.pop()), err
