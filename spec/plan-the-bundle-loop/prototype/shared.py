"""
Throwaway prototype (plan step, research 03): two read-only "mounts" as real processes over one
shared durable-tier directory and one bundle on the local loop's helper. No FUSE, no project import.

    python shared.py drive [--runs N]      the driver: store, tap, scenarios, summary on stdout
    python shared.py mount <config.json>   one mount process (started by the driver)
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import signal
import sqlite3
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from collections import Counter
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
# The helper and its tap, as landed: tools/bundle_loop.py and tests/loop/tap.py of this repository.
# PROTOTYPE_HELPER names another directory holding tools/ and tests/loop/ (the plan step ran a copy).
REPO = Path(__file__).resolve().parents[3]
HELPER = Path(os.environ.get('PROTOTYPE_HELPER') or REPO)
SCRATCH = os.environ.get('PROTOTYPE_SCRATCH') or None      # labs go to the default temporary directory
ORACLE = []                                                # every mount's verdict, for the RESULT line
REFUSED = 3


# ------------------------------------------------------------------------------------------------
# The mount process
# ------------------------------------------------------------------------------------------------

def scan_tar(path: Path) -> list[tuple[str, int, int]]:
    """Name, data offset and size of every regular member of an uncompressed tar."""
    found = []
    with tarfile.open(path, mode='r:') as archive:
        for member in archive:
            if member.isfile():
                found.append((member.name, member.offset_data, member.size))
    return sorted(found)


def marker_text(source: str, nonce: bool) -> str:
    text = ('# shared staged-bundle tier: read-only to every mount\n'
            'kind = "shared-staged"\n'
            'format = 1\n'
            f'source = "{source}"\n')
    if nonce:
        text += f'written_by = {os.getpid()}\nwritten = {time.time()}\n'
    return text


class Refused(Exception):
    pass


def ensure_marker(shared: Path, source: str, nonce: bool, method: str, wrote: list[str]) -> dict:
    """Write the marker if absent (temp + rename, or temp + exclusive link), then read and check it."""
    directory = shared / '.terrace'
    directory.mkdir(exist_ok=True)
    final = directory / 'marker'
    made = False
    if not final.exists():
        temp = directory / f'marker.{os.getpid()}.{uuid4().hex[:8]}.partial'
        with open(temp, 'w') as stream:
            stream.write(marker_text(source, nonce))
            stream.flush()
            os.fsync(stream.fileno())
        wrote.append(str(temp))
        if method == 'link':
            try:
                os.link(temp, final)
                made = True
            except FileExistsError:
                pass
            os.unlink(temp)
        else:
            os.replace(temp, final)
            made = True
        if made:
            wrote.append(str(final))
    text = final.read_text()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise Refused('marker: corrupt') from None
    if data.get('kind') != 'shared-staged':
        raise Refused('marker: another kind')
    if data.get('source') != source:
        raise Refused('marker: another source')
    return {'made': made, 'sha': hashlib.sha256(text.encode()).hexdigest()[:16]}


def mount(config: Path) -> int:
    import boto3
    from botocore.config import Config
    cfg = json.loads(config.read_text())
    state, lower, shared = Path(cfg['state']), Path(cfg['lower']), Path(cfg['shared'])
    lower.mkdir(parents=True, exist_ok=True)
    wrote: list[str] = []                    # every path this process created or renamed into place
    out: dict = {'name': cfg['name'], 'pid': os.getpid(), 'pulled': 0, 'refused': None, 'lock': None,
                 'requests': Counter()}
    status = 0
    try:
        db = sqlite3.connect(state / 'state.db')
        wrote.append(str(state / 'state.db'))
        db.executescript('''
            CREATE TABLE IF NOT EXISTS staged (key TEXT PRIMARY KEY, source TEXT, signature TEXT, name TEXT);
            CREATE TABLE IF NOT EXISTS copy (member TEXT PRIMARY KEY, tier TEXT, path TEXT, size INTEGER);
            CREATE TABLE IF NOT EXISTS member_index (bundle TEXT, member TEXT, offset INTEGER, size INTEGER,
                                                     PRIMARY KEY (bundle, member));''')
        client = boto3.client('s3', endpoint_url=cfg['endpoint'], region_name='us-east-1',
                              aws_access_key_id='test', aws_secret_access_key='test',
                              config=Config(retries={'max_attempts': 1, 'mode': 'standard'}))
        bucket, key, stem = cfg['bucket'], cfg['key'], cfg['stem']

        (state / 'ready').touch()
        if cfg.get('go'):
            while not os.path.exists(cfg['go']):
                time.sleep(0.0005)
        if cfg.get('stagger'):
            time.sleep(cfg['stagger'])
        t_go = time.monotonic()

        # 1. The marker.
        out['marker'] = ensure_marker(shared, cfg['source'], cfg.get('marker_nonce', False),
                                      cfg.get('marker_method', 'rename'), wrote)
        if cfg.get('only_marker'):
            return 0

        # 2. The signature, and with it the staged name.
        mode, signature = cfg.get('signature', 'head'), None
        if mode == 'trust':
            staged = sorted(shared.glob(f'{stem}.*.tar'), key=lambda found: found.stat().st_mtime_ns)
            if staged:
                signature = staged[-1].name[len(stem) + 1:-len('.tar')]
        if signature is None and mode == 'list':
            entry = client.list_objects_v2(Bucket=bucket, Prefix=key)['Contents'][0]
            out['requests']['list'] += 1
            signature = f'{entry["ETag"].strip(chr(34))[:16]}-{entry["Size"]}'
        if signature is None:
            head = client.head_object(Bucket=bucket, Key=key)
            out['requests']['stat'] += 1
            signature = f'{head["ETag"].strip(chr(34))[:16]}-{head["ContentLength"]}'
        name = f'{stem}.{signature}.tar'
        final, sidecar, lock = shared / name, shared / f'{name}.index', shared / f'{name}.lock'
        tag = f'{os.getpid()}.{uuid4().hex[:8]}'

        def pull() -> None:
            started = time.monotonic()
            reply = client.get_object(Bucket=bucket, Key=key)
            body = reply['Body'].read()
            out['requests']['read'] += 1
            got = f'{reply["ETag"].strip(chr(34))[:16]}-{len(body)}'
            if mode != 'trust' and got != signature:
                raise RuntimeError(f'the object changed between the signature and the fetch: {got}')
            if cfg.get('delay'):
                time.sleep(cfg['delay'])
            temp = shared / f'{name}.{tag}.partial'
            with open(temp, 'wb') as stream:
                stream.write(gzip.decompress(body))
                stream.flush()
                os.fsync(stream.fileno())
            wrote.append(str(temp))
            index_temp = None
            if cfg.get('index', 'sidecar') == 'sidecar':
                rows = scan_tar(temp)
                index_temp = shared / f'{name}.index.{tag}.partial'
                with open(index_temp, 'w') as stream:
                    stream.write(json.dumps({'bundle': key, 'signature': signature, 'members': len(rows)}) + '\n')
                    for member, offset, size in rows:
                        stream.write(json.dumps({'n': member, 'o': offset, 's': size}) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                wrote.append(str(index_temp))
            if cfg.get('kill_point'):
                (state / 'at-kill-point').touch()
                time.sleep(3600)
            # The index goes first: a bundle under its final name always has its index beside it.
            if index_temp is not None:
                os.replace(index_temp, sidecar)
                wrote.append(str(sidecar))
            os.replace(temp, final)
            wrote.append(str(final))
            out['pulled'] += 1
            out['pull_s'] = time.monotonic() - started

        # 3. The pull, when nothing is staged under that name.
        if not final.exists():
            if cfg.get('lock'):
                try:
                    handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                    os.write(handle, f'{os.uname().nodename} {os.getpid()} {time.time()}\n'.encode())
                    os.close(handle)
                    mine = True
                    wrote.append(str(lock))
                except FileExistsError:
                    mine = False
                if mine:
                    out['lock'] = 'held'
                    if not final.exists():           # a holder may have finished since the first look
                        pull()
                    os.unlink(lock)                  # only the maker removes a lock
                else:
                    waited = time.monotonic()
                    deadline = waited + cfg['lock_timeout']
                    while time.monotonic() < deadline and not final.exists():
                        time.sleep(0.005)
                    out['lock_wait_s'] = time.monotonic() - waited
                    if final.exists():
                        out['lock'] = 'waited'
                    else:
                        out['lock'] = 'timed-out'
                        pull()                       # the lock only saves work; past the timeout, do the work
            else:
                pull()
        out['stage_s'] = time.monotonic() - t_go
        out['staged'] = name
        db.execute('INSERT OR REPLACE INTO staged VALUES (?, ?, ?, ?)', (key, cfg['source'], signature, name))
        db.commit()

        # 4. The member index.
        started = time.monotonic()
        if cfg.get('index', 'sidecar') == 'sidecar':
            index = {}
            with open(sidecar) as stream:
                header = json.loads(stream.readline())
                assert header['signature'] == signature
                for line in stream:
                    row = json.loads(line)
                    index[row['n']] = (row['o'], row['s'])
        else:
            rows = scan_tar(final)
            db.executemany('INSERT OR REPLACE INTO member_index VALUES (?, ?, ?, ?)',
                           [(name, member, offset, size) for member, offset, size in rows])
            db.commit()
            index = {member: (offset, size) for member, offset, size in rows}
        out['index_s'] = time.monotonic() - started
        out['index_mode'] = cfg.get('index', 'sidecar')
        out['members'] = len(index)

        # 5. Read members from the staged bundle into the mount's own lower tier.
        handle = os.open(final, os.O_RDONLY)
        names = sorted(index)

        def read(member: str, descriptor: int) -> None:
            offset, size = index[member]
            data = os.pread(descriptor, size, offset)
            target = lower / member
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + '.partial')
            temp.write_bytes(data)
            os.replace(temp, target)
            wrote.append(str(target))
            db.execute('INSERT OR REPLACE INTO copy VALUES (?, ?, ?, ?)', (member, 'lower', str(target), size))

        if cfg.get('hold'):
            half = len(names) // 2
            for member in names[:half]:
                read(member, handle)
            db.commit()
            (state / 'half').touch()
            while not os.path.exists(cfg['hold']):
                time.sleep(0.005)
            for member in names[half:-1]:
                read(member, handle)                 # the descriptor opened before the source changed
            again = os.open(shared / db.execute('SELECT name FROM staged WHERE key = ?', (key,)).fetchone()[0],
                            os.O_RDONLY)             # and by the name this mount's own database records
            read(names[-1], again)
            os.close(again)
        else:
            for member in names:
                read(member, handle)
        os.close(handle)
        db.commit()
        out['copies'] = db.execute('SELECT count(*) FROM copy').fetchone()[0]
        out['total_s'] = time.monotonic() - t_go
    except Refused as error:
        out['refused'] = str(error)
        status = REFUSED
    finally:
        out['writes'] = wrote
        out['requests'] = dict(out['requests'])
        (state / 'result.json').write_text(json.dumps(out))
    return status


# ------------------------------------------------------------------------------------------------
# The driver
# ------------------------------------------------------------------------------------------------

def config(run: Path, name: str, loop, endpoint: str, **over) -> dict:
    stem = loop.stems[0]
    cfg = {'name': name, 'endpoint': endpoint, 'bucket': loop.bucket, 'key': loop.keys[stem], 'stem': stem,
           'source': f's3://{loop.bucket}/{loop.keys[stem]}', 'shared': str(run / 'shared'),
           'state': str(run / name / 'state'), 'lower': str(run / name / 'lower'), 'lock': False,
           'lock_timeout': 5.0, 'delay': 0.0, 'signature': 'head', 'index': 'sidecar'}
    cfg.update(over)
    return cfg


def spawn(cfg: dict) -> subprocess.Popen:
    state = Path(cfg['state'])
    state.mkdir(parents=True)
    Path(cfg['shared']).mkdir(parents=True, exist_ok=True)
    (state / 'config.json').write_text(json.dumps(cfg))
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'mount', str(state / 'config.json')],
                            stdout=subprocess.DEVNULL, stderr=open(state / 'stderr.log', 'w'))


def await_file(path: Path, process: subprocess.Popen, limit: float = 60.0) -> None:
    deadline = time.monotonic() + limit
    while not path.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError(f'{path} never appeared (exit {process.poll()}): '
                               f'{(path.parent / "stderr.log").read_text()[-2000:]}')
        time.sleep(0.002)


def finish(process: subprocess.Popen, cfg: dict) -> dict:
    code = process.wait(timeout=120)
    result = json.loads((Path(cfg['state']) / 'result.json').read_text())
    result['exit'] = code
    if code not in (0, REFUSED):
        result['stderr'] = (Path(cfg['state']) / 'stderr.log').read_text()[-2000:]
    return result


def together(cfgs: list[dict], run: Path) -> list[dict]:
    go = run / 'go'
    for cfg in cfgs:
        cfg['go'] = str(go)
    processes = [spawn(cfg) for cfg in cfgs]
    for process, cfg in zip(processes, cfgs):
        await_file(Path(cfg['state']) / 'ready', process)
    go.touch()
    return [finish(process, cfg) for process, cfg in zip(processes, cfgs)]


def verify(cfg: dict, content: Path, stem: str) -> dict:
    """Compare a mount's lower tier with the oracle tree, member by member."""
    lower, ok, bad, missing = Path(cfg['lower']), 0, 0, 0
    for found in sorted((content / stem).rglob('*')):
        if found.is_file():
            mine = lower / found.relative_to(content)
            if not mine.exists():
                missing += 1
            elif mine.read_bytes() == found.read_bytes():
                ok += 1
            else:
                bad += 1
    return {'ok': ok, 'bad': bad, 'missing': missing, 'oracle': bad == 0 and missing == 0 and ok > 0}


def shared_files(shared: Path) -> dict:
    kinds: dict = {}
    for found in sorted(shared.rglob('*')):
        if not found.is_file():
            continue
        name = found.name
        if name == 'marker':
            kind = 'marker'
        elif name.endswith('.partial'):
            kind = 'partial'
        elif name.endswith('.lock'):
            kind = 'lock'
        elif name.endswith('.tar.index'):
            kind = 'index'
        elif name.endswith('.tar'):
            kind = 'staged'
        else:
            kind = 'other'
        kinds.setdefault(kind, []).append(str(found.relative_to(shared)))
    return kinds


def outside(result: dict, cfg: dict) -> int:
    """How many of a mount's writes fell outside its own state, its own lower tier and the shared tier."""
    roots = (cfg['state'], cfg['lower'], cfg['shared'])
    return sum(1 for path in result.get('writes', []) if not path.startswith(roots))


def counts(window, key: str) -> dict:
    requests = window.requests
    return {'get_bundle': sum(1 for r in requests if r.kind == 'read' and r.key == key),
            'head': sum(1 for r in requests if r.kind == 'stat'),
            'list': sum(1 for r in requests if r.kind == 'list'),
            'write': sum(1 for r in requests if r.kind == 'write'),
            'other_get': sum(1 for r in requests if r.kind == 'read' and r.key != key)}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def emit(record: dict) -> None:
    if isinstance(record.get('oracle'), list):
        ORACLE.extend(record['oracle'])
    print(json.dumps(record, sort_keys=True), flush=True)


def summarize(label: str, records: list[dict]) -> None:
    def spread(values) -> str:
        return ' '.join(f'{value}:x{count}' for value, count in sorted(Counter(values).items(), key=str))
    line = {'summary': label, 'runs': len(records),
            'get_bundle': spread(r['requests']['get_bundle'] for r in records),
            'head': spread(r['requests']['head'] for r in records),
            'list': spread(r['requests']['list'] for r in records),
            'write': spread(r['requests']['write'] for r in records),
            'oracle_A': spread(r['oracle'][0] for r in records),
            'oracle_B': spread(r['oracle'][1] for r in records if len(r['oracle']) > 1),
            'staged_tars': spread(len(r['shared'].get('staged', [])) for r in records),
            'staged_is_gunzip_of_object': spread(r.get('staged_ok') for r in records),
            'one_marker_same_for_both': spread(r.get('marker_same') for r in records),
            'leftover_partial': spread(len(r['shared'].get('partial', [])) for r in records),
            'leftover_lock': spread(len(r['shared'].get('lock', [])) for r in records),
            'other_files_on_shared': spread(len(r['shared'].get('other', [])) for r in records),
            'writes_outside': spread(r['outside'] for r in records)}
    stage = [m['stage_s'] for r in records for m in r['mounts'] if 'stage_s' in m]
    if stage:
        line['stage_s_median_max'] = [round(statistics.median(stage), 4), round(max(stage), 4)]
    print(json.dumps(line), flush=True)


def record_of(scenario: str, index: int, window, loop, cfgs: list[dict], results: list[dict],
              expected: str | None = None) -> dict:
    stem = loop.stems[0]
    shared = Path(cfgs[0]['shared'])
    files = shared_files(shared)
    staged_ok = None
    if expected is not None and files.get('staged'):
        staged_ok = all(sha(shared / name) == expected for name in files['staged'])
    markers = {m['marker']['sha'] for m in results if m.get('marker')}
    final_marker = sha(shared / '.terrace' / 'marker') if (shared / '.terrace' / 'marker').exists() else None
    record = {'scenario': scenario, 'run': index, 'requests': counts(window, loop.keys[stem]),
              'oracle': [verify(cfg, loop.content, stem)['oracle'] for cfg in cfgs],
              'shared': files, 'staged_ok': staged_ok, 'marker_same': len(markers) == 1,
              'outside': sum(outside(m, cfg) for m, cfg in zip(results, cfgs)),
              'mounts': [{k: v for k, v in m.items() if k != 'writes'} for m in results]}
    del final_marker
    return record


def drive(runs: int) -> int:
    # As landed, the tap is a module of the tests.loop package, which loads tools/bundle_loop.py
    # itself, so both come in through that package with the repository root on the path.
    sys.path.insert(0, str(HELPER))
    from tests.loop import bundle_loop
    from tests.loop.tap import Tap
    Store, Loop, pack, member_bytes = bundle_loop.Store, bundle_loop.Loop, bundle_loop.pack, bundle_loop.member_bytes
    randomizer = random.Random(3)
    def blob(path: Path) -> str:
        data = path.read_bytes()
        return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()[:7]
    emit({'prototype': 'shared', 'helper': str(HELPER),
          'helper_blobs': {'tools/bundle_loop.py': blob(HELPER / 'tools' / 'bundle_loop.py'),
                           'tests/loop/tap.py': blob(HELPER / 'tests' / 'loop' / 'tap.py')},
          'command': 'uv run python shared.py drive --runs %d' % runs,
          'python': sys.version.split()[0], 'runs': runs, 'platform': sys.platform})

    with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='proto-shared.') as lab_name, Store() as store, \
            Tap(store.endpoint) as tap:
        lab = Path(lab_name)
        for members in (6, 300):
            with Loop(store.endpoint, members=members, parent=lab) as loop:
                stem = loop.stems[0]
                key = loop.keys[stem]
                body = loop.client.get_object(Bucket=loop.bucket, Key=key)['Body'].read()
                expected = hashlib.sha256(gzip.decompress(body)).hexdigest()[:16]
                emit({'bundle': key, 'members': members, 'object_bytes': len(body),
                      'tar_bytes': len(gzip.decompress(body))})
                base = lab / f'm{members}'

                def one(scenario: str, index: int, plan) -> dict:
                    run = base / scenario / f'{index:02d}'
                    run.mkdir(parents=True)
                    with tap.window() as window:
                        cfgs, results = plan(run)
                    record = record_of(f'{scenario}/m{members}', index, window, loop, cfgs, results, expected)
                    emit(record)
                    return record

                def sequential(**second):
                    def plan(run):
                        a = config(run, 'A', loop, tap.endpoint)
                        ra = finish(spawn(a), a)
                        b = config(run, 'B', loop, tap.endpoint, **second)
                        rb = finish(spawn(b), b)
                        return [a, b], [ra, rb]
                    return plan

                def racing(**both):
                    stagger = both.pop('stagger_max', None)

                    def plan(run):
                        a = config(run, 'A', loop, tap.endpoint, **both)
                        b = config(run, 'B', loop, tap.endpoint, **both)
                        if stagger:
                            b['stagger'] = randomizer.uniform(0, stagger)
                        return [a, b], together([a, b], run)
                    return plan

                # (a) one after the other
                recs = [one('a-sequential-head', i, sequential()) for i in range(max(3, runs // 2))]
                summarize(f'a-sequential-head/m{members}', recs)
                pull_s = statistics.median(m['pull_s'] for r in recs for m in r['mounts'] if 'pull_s' in m)
                emit({'median_pull_s': round(pull_s, 4), 'members': members})
                recs = [one('a-sequential-trust', i, sequential(signature='trust')) for i in range(3)]
                summarize(f'a-sequential-trust/m{members}', recs)
                recs = [one('a-sequential-list', i, sequential(signature='list')) for i in range(3)]
                summarize(f'a-sequential-list/m{members}', recs)
                recs = [one('a-sequential-each-scans', i, lambda run: (lambda a, b: ([a, b], [
                    finish(spawn(a), a), finish(spawn(b), b)]))(
                        config(run, 'A', loop, tap.endpoint, index='scan'),
                        config(run, 'B', loop, tap.endpoint, index='scan'))) for i in range(5)]
                summarize(f'a-sequential-each-scans/m{members}', recs)
                emit({'index_in_mount': 'each-scans', 'members': members, 'index_s_median': round(statistics.median(
                    m['index_s'] for r in recs for m in r['mounts']), 5)})

                # (b) together, temp + rename only
                recs = [one('b-together-rename', i, racing()) for i in range(runs)]
                summarize(f'b-together-rename/m{members}', recs)
                emit({'index_in_mount': 'sidecar', 'members': members, 'index_s_median': round(statistics.median(
                    m['index_s'] for r in recs for m in r['mounts']), 5)})
                recs = [one('b-together-rename-delay0.3', i, racing(delay=0.3)) for i in range(runs)]
                summarize(f'b-together-rename-delay0.3/m{members}', recs)
                recs = [one('b-stagger-rename', i, racing(stagger_max=2 * pull_s + 0.02)) for i in range(runs)]
                summarize(f'b-stagger-0..2xpull-rename/m{members}', recs)

                # (c) together, O_CREAT|O_EXCL lock file, 5 s timeout
                recs = [one('c-together-lock', i, racing(lock=True)) for i in range(runs)]
                summarize(f'c-together-lock-timeout5/m{members}', recs)
                emit({'c_lock_outcomes': dict(Counter(m['lock'] for r in recs for m in r['mounts']))})
                recs = [one('c-together-lock-delay0.3', i, racing(lock=True, delay=0.3)) for i in range(runs)]
                summarize(f'c-together-lock-timeout5-delay0.3/m{members}', recs)
                recs = [one('c-lock-timeout-shorter-than-pull', i,
                            racing(lock=True, delay=0.5, lock_timeout=0.1)) for i in range(5)]
                summarize(f'c-lock-timeout0.1-delay0.5/m{members}', recs)

                if members != 300:
                    continue

                # (d) SIGKILL after the fetch, before the rename
                for variant, lock in (('d-kill-rename', False), ('d-kill-lock-timeout2', True)):
                    for index in range(3):
                        run = base / variant / f'{index:02d}'
                        run.mkdir(parents=True)
                        with tap.window() as window:
                            a = config(run, 'A', loop, tap.endpoint, lock=lock, lock_timeout=2.0, kill_point=True)
                            process = spawn(a)
                            await_file(Path(a['state']) / 'at-kill-point', process)
                            os.kill(process.pid, signal.SIGKILL)
                            process.wait()
                            left = shared_files(run / 'shared')
                            b = config(run, 'B', loop, tap.endpoint, lock=lock, lock_timeout=2.0)
                            rb = finish(spawn(b), b)
                            c = config(run, 'C', loop, tap.endpoint, lock=lock, lock_timeout=2.0)
                            rc = finish(spawn(c), c)
                        after = shared_files(run / 'shared')
                        emit({'scenario': variant, 'run': index, 'killed_exit': process.returncode,
                              'left_by_killed': {kind: len(names) for kind, names in left.items()},
                              'left_names': left, 'requests': counts(window, key),
                              'B': {k: rb.get(k) for k in ('pulled', 'lock', 'lock_wait_s', 'stage_s', 'exit')},
                              'C': {k: rc.get(k) for k in ('pulled', 'lock', 'lock_wait_s', 'stage_s', 'exit')},
                              'oracle_B': verify(b, loop.content, stem)['oracle'],
                              'oracle_C': verify(c, loop.content, stem)['oracle'],
                              'staged_ok': all(sha(run / 'shared' / n) == expected for n in after['staged']),
                              'after': {kind: len(names) for kind, names in after.items()}})

                # The marker: who writes it first at an empty directory
                for label, over in (('marker-deterministic-rename', {}),
                                    ('marker-nonce-rename', {'marker_nonce': True}),
                                    ('marker-nonce-link', {'marker_nonce': True, 'marker_method': 'link'})):
                    same = Counter()
                    for index in range(runs):
                        run = base / label / f'{index:02d}'
                        run.mkdir(parents=True)
                        cfgs = [config(run, n, loop, tap.endpoint, only_marker=True, **over) for n in 'AB']
                        results = together(cfgs, run)
                        final = sha(run / 'shared' / '.terrace' / 'marker')
                        seen = {m['marker']['sha'] for m in results}
                        made = sum(m['marker']['made'] for m in results)
                        same[f'both_read_final={seen == {final}} writers={made}'] += 1
                    emit({'scenario': label, 'runs': runs, 'outcomes': dict(same)})

                # What a mount refuses
                for label, text in (('refuse-overlay-marker', '# Terrace managed-location marker\nuuid = "0b1e"\n'
                                     'tier = "scratch"\ngeneration = 1\nwritten = 1.0\n'),
                                    ('refuse-other-source', marker_text('s3://another-bucket/bundles/x.tar.gz', False))):
                    run = base / label
                    (run / 'shared' / '.terrace').mkdir(parents=True)
                    (run / 'shared' / '.terrace' / 'marker').write_text(text)
                    with tap.window() as window:
                        a = config(run, 'A', loop, tap.endpoint)
                        ra = finish(spawn(a), a)
                    emit({'scenario': label, 'exit': ra['exit'], 'refused': ra['refused'],
                          'requests': counts(window, key), 'shared': shared_files(run / 'shared')})

                # The stale case: the object is replaced at the source while A holds the old one
                run = base / 'stale'
                run.mkdir(parents=True)
                new_content = run / 'new-content'
                for found in sorted((loop.content / stem).rglob('*')):
                    if found.is_file():
                        target = new_content / found.relative_to(loop.content)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        number = int(found.stem.split('-')[1])
                        target.write_bytes(member_bytes(99, number, found.stat().st_size))
                with tap.window() as window:
                    a = config(run, 'A', loop, tap.endpoint, hold=str(run / 'resume'))
                    pa = spawn(a)
                    await_file(Path(a['state']) / 'half', pa)
                    loop.client.put_object(Bucket=loop.bucket, Key=key, Body=pack(new_content, stem))  # direct, not tapped
                    b = config(run, 'B', loop, tap.endpoint)
                    rb = finish(spawn(b), b)
                    (run / 'resume').touch()
                    ra = finish(pa, a)
                    c = config(run, 'C', loop, tap.endpoint, signature='trust')
                    rc = finish(spawn(c), c)
                    d = config(run, 'D', loop, tap.endpoint)
                    rd = finish(spawn(d), d)
                emit({'scenario': 'stale', 'requests': counts(window, key),
                      'A': {'staged': ra['staged'], 'pulled': ra['pulled'], 'requests': ra['requests'],
                            'reads_old_oracle': verify(a, loop.content, stem)},
                      'B': {'staged': rb['staged'], 'pulled': rb['pulled'], 'requests': rb['requests'],
                            'reads_new_oracle': verify(b, new_content, stem),
                            'reads_old_oracle': verify(b, loop.content, stem)['oracle']},
                      'C_trust': {'staged': rc['staged'], 'pulled': rc['pulled'], 'requests': rc['requests'],
                                  'reads_new_oracle': verify(c, new_content, stem)['oracle']},
                      'D_head': {'staged': rd['staged'], 'pulled': rd['pulled'], 'requests': rd['requests'],
                                 'reads_new_oracle': verify(d, new_content, stem)['oracle']},
                      'shared': shared_files(run / 'shared')})

                # The member index, measured on this bundle's staged tar
                staged = next((run / 'shared').glob(f'{stem}.*.tar'))
                index_bench(f'helper-{members}', staged, lab)
            if members == 6:
                pass
        # The helper's default size, and a larger synthetic tar (not made by the helper)
        with Loop(store.endpoint, members=6, parent=lab) as loop:
            body = loop.client.get_object(Bucket=loop.bucket, Key=loop.keys[loop.stems[0]])['Body'].read()
            small = lab / 'helper-6.tar'
            small.write_bytes(gzip.decompress(body))
            index_bench('helper-6', small, lab)
        big = lab / 'synthetic-5000.tar'
        with tarfile.open(big, 'w:') as archive:
            import io
            for number in range(5000):
                data = random.Random(number).randbytes(1024)
                info = tarfile.TarInfo(f'synthetic/{number % 50:02d}/member-{number:05d}.bin')
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        index_bench('synthetic-5000x1KiB', big, lab)
    emit({'done': True})
    good = bool(ORACLE) and all(ORACLE)
    print(f"RESULT oracle_bytes={'yes' if good else 'no'} processes={len(ORACLE)}", flush=True)
    return 0 if good else 1


def index_bench(label: str, staged: Path, lab: Path) -> None:
    """Scan the tar; write both sidecar forms; time loading and one lookup in each. Medians of 7."""
    def timed(action) -> float:
        samples = []
        for _ in range(7):
            started = time.perf_counter()
            action()
            samples.append(time.perf_counter() - started)
        return round(statistics.median(samples) * 1000, 3)

    rows = scan_tar(staged)
    flat = lab / f'{label}.index.jsonl'
    with open(flat, 'w') as stream:
        for member, offset, size in rows:
            stream.write(json.dumps({'n': member, 'o': offset, 's': size}) + '\n')
    lite = lab / f'{label}.index.sqlite'
    db = sqlite3.connect(lite)
    db.execute('CREATE TABLE member (name TEXT PRIMARY KEY, offset INTEGER, size INTEGER) WITHOUT ROWID')
    db.executemany('INSERT INTO member VALUES (?, ?, ?)', rows)
    db.commit()
    db.close()
    probe = rows[len(rows) // 2][0]

    def load_flat() -> None:
        with open(flat) as stream:
            index = {row['n']: (row['o'], row['s']) for row in map(json.loads, stream)}
        assert index[probe]

    def one_sqlite() -> None:
        handle = sqlite3.connect(f'file:{lite}?mode=ro&immutable=1', uri=True)
        assert handle.execute('SELECT offset, size FROM member WHERE name = ?', (probe,)).fetchone()
        handle.close()

    def all_sqlite() -> None:
        handle = sqlite3.connect(f'file:{lite}?mode=ro&immutable=1', uri=True)
        assert len(handle.execute('SELECT * FROM member').fetchall()) == len(rows)
        handle.close()

    before = sorted(found.name for found in lab.iterdir() if found.name.startswith(label))
    one_sqlite()
    after = sorted(found.name for found in lab.iterdir() if found.name.startswith(label))
    emit({'index_bench': label, 'members': len(rows), 'tar_bytes': staged.stat().st_size,
          'scan_tar_ms': timed(lambda: scan_tar(staged)),
          'flat_jsonl_bytes': flat.stat().st_size, 'flat_load_all_ms': timed(load_flat),
          'sqlite_bytes': lite.stat().st_size, 'sqlite_open_one_lookup_ms': timed(one_sqlite),
          'sqlite_open_load_all_ms': timed(all_sqlite),
          'sqlite_immutable_open_made_no_file': before == after})


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'mount':
        sys.exit(mount(Path(sys.argv[2])))
    if len(sys.argv) >= 2 and sys.argv[1] == 'drive':
        count = int(sys.argv[sys.argv.index('--runs') + 1]) if '--runs' in sys.argv else 20
        sys.exit(drive(count))
    sys.exit(drive(20))
