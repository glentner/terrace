"""
Throwaway prototype for GOAL R8 (plan-the-bundle-loop): one bundle pulled, changed, rebuilt from
recorded state plus the durable tier, and written back with one object write. Imports nothing of
the project. Run from the cycle worktree:  uv run python <this file>
"""
from __future__ import annotations
import gzip, hashlib, io, json, os, random, shutil, sqlite3, stat, sys, tarfile, tempfile
from collections import Counter
from pathlib import Path

# The helper and its tap, as landed: tools/bundle_loop.py and tests/loop/tap.py of this repository.
# PROTOTYPE_HELPER names another directory holding tools/ and tests/loop/ (the plan step ran a copy).
REPO = Path(__file__).resolve().parents[3]
HELPER = Path(os.environ.get('PROTOTYPE_HELPER') or REPO)
SCRATCH = Path(os.environ.get('PROTOTYPE_SCRATCH') or tempfile.mkdtemp(prefix='proto-rebuild.'))   # labs and results.json
sys.path[:0] = [str(HELPER / 'tools'), str(HELPER / 'tests' / 'loop')]
from bundle_loop import Store, Loop          # noqa: E402
from tap import Tap                          # noqa: E402
import boto3                                 # noqa: E402
from botocore.config import Config           # noqa: E402

MT0, MT1, MT2 = 1_800_000_000.0, 1_800_000_100.0, 1_800_000_200.0
PRE = random.Random('pre').randbytes(1500)
NEW = random.Random('new').randbytes(2000)
CRE = random.Random('cre').randbytes(3000)


def say(*parts):
    print(*parts, flush=True)


class Kill(Exception):
    """An injected kill at a named step."""


class Durable:
    """The directory standing for the first shared stage tier. Every write to it is metered."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.reset()

    def reset(self):
        self.n = dict(bytes=0, created=0, moved=0, removed=0)

    def snap(self):
        out = dict(self.n)
        self.reset()
        return out

    def write_atomic(self, rel: str, chunks, mode=None, mtime=None) -> int:
        dst = self.root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name('.tmp-' + dst.name)
        size = 0
        with open(tmp, 'wb') as handle:
            for chunk in chunks:
                handle.write(chunk)
                size += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        if mtime is not None:
            os.utime(tmp, (mtime, mtime))
        os.replace(tmp, dst)                       # the all-or-nothing step
        self.n['bytes'] += size
        self.n['created'] += 1
        return size

    def move(self, old: str, new: str):
        (self.root / new).parent.mkdir(parents=True, exist_ok=True)
        os.rename(self.root / old, self.root / new)
        self.n['moved'] += 1

    def remove(self, rel: str):
        os.unlink(self.root / rel)
        self.n['removed'] += 1

    def listing(self):
        return sorted(str(p.relative_to(self.root)) + ('/' if p.is_dir() else f'  [{p.stat().st_size}]')
                      for p in self.root.rglob('*'))

    def usage(self):
        files = [p for p in self.root.rglob('*') if p.is_file()]
        return len(files), sum(p.stat().st_size for p in files)


SCHEMA = """
CREATE TABLE IF NOT EXISTS bundle(id INTEGER PRIMARY KEY, key TEXT, staged TEXT, generation INT, dirty INT, etag TEXT);
CREATE TABLE IF NOT EXISTS member(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, kind TEXT,
    offset INT, size INT, mode INT, mtime REAL, state TEXT, alt TEXT);
"""


class Proto:
    """One mount's view of one bundle: a state database outside every tier, and the durable tier."""

    def __init__(self, lab: Path, client, bucket, key, stem, *, layout='name', rename='A', removal='db'):
        self.lab, self.client, self.bucket, self.key, self.stem = lab, client, bucket, key, stem
        self.layout, self.rename_mode, self.removal = layout, rename, removal
        self.durable = Durable(lab / 'durable')
        (lab / 'state').mkdir(exist_ok=True)
        self.dbpath = lab / 'state' / 'state.db'
        self.db = sqlite3.connect(self.dbpath)
        self.db.executescript(SCHEMA)
        row = self.db.execute('SELECT staged, generation FROM bundle').fetchone()
        self.staged_rel, self.generation = row if row else (f'{stem}.tar', 1)
        self.manifest_rel = f'{stem}.manifest.json'
        self.manifest_writes = []
        self.sizes = {}
        self.kill_at = None

    # ---- helpers
    def _kill(self, point):
        if self.kill_at == point:
            raise Kill(point)

    def _alt_rel(self, mid, name):
        return name if self.layout == 'name' else f'{self.stem}.d/{mid}'

    def _commit(self):
        self.db.execute('UPDATE bundle SET dirty = 1')
        if self.removal == 'manifest':
            self.write_manifest()                  # the tier's own record first, then the database
        self.db.commit()

    def _live(self):
        rows = self.db.execute("SELECT id, name, kind, offset, size, mode, mtime, alt FROM member "
                               "WHERE state != 'removed' ORDER BY name").fetchall()
        return [dict(zip(('id', 'name', 'kind', 'offset', 'size', 'mode', 'mtime', 'alt'), row)) for row in rows]

    def write_manifest(self, members=None, staged=None):
        members = self._live() if members is None else members
        doc = {'bundle': self.key, 'staged': staged or self.staged_rel,
               'members': [{'n': m['name'], 'k': m['kind'], 'mode': m['mode'], 'mtime': m['mtime'],
                            **({'f': m['alt']} if m['alt'] else {'at': m['offset'], 'size': m['size']})}
                           for m in members]}
        size = self.durable.write_atomic(self.manifest_rel, [json.dumps(doc, separators=(',', ':')).encode()])
        self.manifest_writes.append(size)

    # ---- pull
    def pull(self):
        reply = self.client.get_object(Bucket=self.bucket, Key=self.key)
        self.sizes['source_object'] = reply['ContentLength']
        unzip = gzip.GzipFile(fileobj=reply['Body'])
        self.sizes['staged_tar'] = self.durable.write_atomic(self.staged_rel, iter(lambda: unzip.read(1 << 20), b''))
        self._kill('pull-after-stage')
        self.db.execute('INSERT INTO bundle(key, staged, generation, dirty, etag) VALUES (?,?,?,0,?)',
                        (self.key, self.staged_rel, self.generation, reply['ETag']))
        with tarfile.open(self.durable.root / self.staged_rel, 'r:') as archive:
            for info in archive:
                self.db.execute('INSERT INTO member(name, kind, offset, size, mode, mtime, state) VALUES (?,?,?,?,?,?,?)',
                                (info.name, 'dir' if info.isdir() else 'file', info.offset_data, info.size,
                                 info.mode, info.mtime, 'base'))
        if self.removal == 'manifest':
            self.write_manifest()
        self.db.commit()

    # ---- the view
    def read(self, name):
        offset, size, alt = self.db.execute("SELECT offset, size, alt FROM member WHERE name = ?", (name,)).fetchone()
        if alt:
            return (self.durable.root / alt).read_bytes()
        with open(self.durable.root / self.staged_rel, 'rb') as staged:
            staged.seek(offset)
            return staged.read(size)

    def view(self):
        return {m['name']: (m['kind'], None if m['kind'] == 'dir' else hashlib.sha256(self.read(m['name'])).hexdigest())
                for m in self._live()}

    # ---- changes: each ends as recorded state plus bytes on the durable tier
    def write(self, name, data, mode=None, mtime=None, commit=True):
        row = self.db.execute('SELECT id, mode, alt FROM member WHERE name = ?', (name,)).fetchone()
        if row is None:
            mid = self.db.execute("INSERT INTO member(name, kind, state) VALUES (?, 'file', 'altered')", (name,)).lastrowid
            mode = 0o644 if mode is None else mode
        else:
            mid, mode = row[0], (row[1] if mode is None else mode)
        rel = self._alt_rel(mid, name)
        self.durable.write_atomic(rel, [data], mode, mtime)
        self._kill('write-after-file')
        self.db.execute("UPDATE member SET state = 'altered', alt = ?, size = ?, mode = ?, mtime = ? WHERE id = ?",
                        (rel, len(data), mode, mtime, mid))
        if commit:
            self._commit()

    def remove(self, name, commit=True):
        mid, alt = self.db.execute('SELECT id, alt FROM member WHERE name = ?', (name,)).fetchone()
        # A removed entry keeps a physical place (its offset) and gives its name up at once.
        self.db.execute("UPDATE member SET state = 'removed', name = NULL, alt = NULL WHERE id = ?", (mid,))
        if commit:
            self._commit()
        if alt:
            self.durable.remove(alt)

    def rename(self, old, new):
        mid, kind, mode, mtime, alt = self.db.execute(
            'SELECT id, kind, mode, mtime, alt FROM member WHERE name = ?', (old,)).fetchone()
        if self.rename_mode == 'A':
            if kind == 'dir':
                if self.layout == 'name' and (self.durable.root / old).is_dir():
                    self.durable.move(old, new)                          # one POSIX rename of the directory
                    self._kill('rename-after-move')
                    self.db.execute("UPDATE member SET alt = ? || substr(alt, ?) WHERE substr(alt, 1, ?) = ?",
                                    (new, len(old) + 1, len(old) + 1, old + '/'))
                self.db.execute("UPDATE member SET name = ? || substr(name, ?) WHERE name = ? OR substr(name, 1, ?) = ?",
                                (new, len(old) + 1, old, len(old) + 1, old + '/'))
            else:
                if alt and self.layout == 'name':
                    self.durable.move(alt, new)
                    alt = new
                self.db.execute('UPDATE member SET name = ?, alt = ? WHERE id = ?', (new, alt, mid))
            self._commit()
            return
        # (B) copy every member to the new name and remove the old one.
        under = [(old, kind, mode, mtime)] if kind != 'dir' else self.db.execute(
            "SELECT name, kind, mode, mtime FROM member WHERE name = ? OR substr(name, 1, ?) = ? ORDER BY name",
            (old, len(old) + 1, old + '/')).fetchall()
        for name, kind_, mode_, mtime_ in under:
            target = new + name[len(old):]
            if kind_ == 'dir':
                self.db.execute("INSERT INTO member(name, kind, mode, mtime, state) VALUES (?, 'dir', ?, ?, 'base')",
                                (target, mode_, mtime_))
            else:
                self.write(target, self.read(name), mode_, mtime_, commit=False)
        for name, *_ in reversed(under):
            self.remove(name, commit=False)
        self._commit()

    # ---- rebuild
    def plan(self, source):
        if source == 'db':
            return self._live(), self.staged_rel
        if source == 'manifest':                    # the durable tier's own files, no database
            doc = json.loads((self.durable.root / self.manifest_rel).read_text())
            members = []
            for item in doc['members']:
                entry = dict(name=item['n'], kind=item['k'], mode=item['mode'], mtime=item['mtime'],
                             offset=item.get('at'), size=item.get('size'), alt=item.get('f'))
                if entry['alt']:
                    found = (self.durable.root / entry['alt']).lstat()
                    entry.update(size=found.st_size, mode=stat.S_IMODE(found.st_mode), mtime=found.st_mtime)
                members.append(entry)
            return members, doc['staged']
        # 'files': the staged tar's own index overlaid with whatever sits by name beside it.
        byname = {}
        with tarfile.open(self.durable.root / self.staged_rel, 'r:') as archive:
            for info in archive:
                byname[info.name] = dict(name=info.name, kind='dir' if info.isdir() else 'file', mode=info.mode,
                                         mtime=info.mtime, offset=info.offset_data, size=info.size, alt=None)
        top = self.durable.root / self.stem
        for found in sorted(top.rglob('*')) if top.is_dir() else []:
            rel, st = str(found.relative_to(self.durable.root)), found.lstat()
            byname[rel] = dict(name=rel, kind='dir' if found.is_dir() else 'file', mode=stat.S_IMODE(st.st_mode),
                               mtime=st.st_mtime, offset=None, size=st.st_size, alt=None if found.is_dir() else rel)
        return [byname[name] for name in sorted(byname)], self.staged_rel

    def _write_tar(self, out, members, staged_rel):
        with tarfile.open(fileobj=out, mode='w|', format=tarfile.PAX_FORMAT) as archive, \
                open(self.durable.root / staged_rel, 'rb') as staged:
            for m in members:
                info = tarfile.TarInfo(m['name'])
                info.mode, info.mtime = m['mode'], m['mtime']
                if m['kind'] == 'dir':
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                elif m['alt']:
                    info.size = m['size']
                    with open(self.durable.root / m['alt'], 'rb') as changed:
                        archive.addfile(info, changed)
                else:
                    info.size = m['size']
                    staged.seek(m['offset'])                              # unchanged bytes, never unpacked
                    archive.addfile(info, staged)

    def rebuild(self, source='db', mode='Y', put=True):
        """
        mode Y: compress straight from state into a spool file, one PUT, restage lazily.
        mode X: write the next generation's staged .tar first, compress from it, one PUT, switch.
        mode Xover: as X, but the new .tar is renamed over the old one's name before the database commit.
        """
        members, staged_rel = self.plan(source)
        root = self.durable.root
        spool = root / f'.rebuild-{self.stem}.tar.gz.tmp'
        new_rel = f'{self.stem}.g{self.generation + 1}.tar'
        if mode == 'Y':
            with open(spool, 'wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as squeeze:
                self._write_tar(squeeze, members, staged_rel)
        else:
            tmp = root / ('.tmp-' + new_rel)
            with open(tmp, 'wb') as raw:
                self._write_tar(raw, members, staged_rel)
            os.replace(tmp, root / new_rel)
            self.sizes['new_staged_tar'] = (root / new_rel).stat().st_size
            self._kill('after-newtar')
            with open(root / new_rel, 'rb') as raw, open(spool, 'wb') as out, \
                    gzip.GzipFile(fileobj=out, mode='wb', mtime=0) as squeeze:
                shutil.copyfileobj(raw, squeeze)
        self.sizes['spool'] = spool.stat().st_size
        self._kill('after-spool')
        if not put:
            return spool
        with open(spool, 'rb') as body:                                   # ONE object write
            reply = self.client.put_object(Bucket=self.bucket, Key=self.key, Body=body,
                                           ContentLength=self.sizes['spool'])
        self._kill('after-put')
        os.unlink(spool)
        if mode == 'Y':
            if self.db is not None:
                self.db.execute('UPDATE bundle SET dirty = 0, etag = ?', (reply['ETag'],))
                self.db.commit()
            return None
        old_rel, final_rel = self.staged_rel, new_rel
        if mode == 'Xover':
            os.replace(root / new_rel, root / old_rel)
            final_rel = old_rel
        self._kill('after-swap')
        with tarfile.open(root / final_rel, 'r:') as archive:
            where = {info.name: info.offset_data for info in archive}
        retire = [m['alt'] for m in members if m['alt']]
        for m in members:
            self.db.execute("UPDATE member SET offset = ?, state = 'base', alt = NULL WHERE id = ?",
                            (where[m['name']], m['id']))
        self.db.execute("DELETE FROM member WHERE state = 'removed'")
        self.db.execute('UPDATE bundle SET staged = ?, generation = generation + 1, dirty = 0, etag = ?',
                        (final_rel, reply['ETag']))
        self.db.commit()                                                  # the switch
        self.staged_rel, self.generation = final_rel, self.generation + 1
        self._kill('after-db')
        for rel in retire:
            os.unlink(root / rel)
        if final_rel != old_rel:
            os.unlink(root / old_rel)
        return None


# ---------------------------------------------------------------- oracle and comparison
def names(stem):
    return dict(pre=f'{stem}/sub/member-001.txt', change=f'{stem}/member-000.txt', create=f'{stem}/created.txt',
                rename=(f'{stem}/member-002.txt', f'{stem}/renamed-002.txt'), remove=f'{stem}/member-004.txt',
                rendir=(f'{stem}/sub', f'{stem}/moved'))


def apply_oracle(root: Path, ch, extended):
    if extended:
        (root / ch['pre']).write_bytes(PRE); os.utime(root / ch['pre'], (MT0, MT0))
    (root / ch['change']).write_bytes(NEW); os.utime(root / ch['change'], (MT1, MT1))
    (root / ch['create']).write_bytes(CRE); os.chmod(root / ch['create'], 0o640); os.utime(root / ch['create'], (MT2, MT2))
    os.rename(root / ch['rename'][0], root / ch['rename'][1])
    os.unlink(root / ch['remove'])
    os.rename(root / ch['rendir'][0], root / ch['rendir'][1])


def apply_proto(proto: Proto, ch, extended):
    ops, steps = [], []
    if extended:
        steps.append(('alter-under-dir', lambda: proto.write(ch['pre'], PRE, None, MT0)))
    steps += [('change', lambda: proto.write(ch['change'], NEW, None, MT1)),
              ('create', lambda: proto.write(ch['create'], CRE, 0o640, MT2)),
              ('rename-file', lambda: proto.rename(*ch['rename'])),
              ('remove', lambda: proto.remove(ch['remove'])),
              ('rename-dir', lambda: proto.rename(*ch['rendir']))]
    for label, step in steps:
        proto.durable.reset()
        before, written = proto.db.total_changes, len(proto.manifest_writes)
        step()
        tier = proto.durable.snap()
        record = sum(proto.manifest_writes[written:])
        ops.append(dict(op=label, rows=proto.db.total_changes - before - 1,          # less the bundle's dirty flag
                        tier_bytes=tier['bytes'], tier_created=tier['created'], tier_moved=tier['moved'],
                        tier_removed=tier['removed'], record_bytes=record))
    return ops


def tree(root: Path):
    out = {}
    for found in root.rglob('*'):
        st = found.lstat()
        digest = None if found.is_dir() else hashlib.sha256(found.read_bytes()).hexdigest()
        out[str(found.relative_to(root))] = ('dir' if found.is_dir() else 'file', stat.S_IMODE(st.st_mode), st.st_mtime, digest)
    return out


def compare(got, want):
    gn, wn = {(k, v[0]) for k, v in got.items()}, {(k, v[0]) for k, v in want.items()}
    common = [k for k in got if k in want and got[k][0] == want[k][0]]
    files = [k for k in common if got[k][0] == 'file']
    return dict(names_equal=gn == wn, missing=sorted(k for k, _ in wn - gn), extra=sorted(k for k, _ in gn - wn),
                bytes_equal=gn == wn and all(got[k][3] == want[k][3] for k in files),
                bytes_differ=sum(got[k][3] != want[k][3] for k in files), entries=len(want),
                mode_differ=sum(got[k][1] != want[k][1] for k in common),
                file_mtime_differ=sum(abs(got[k][2] - want[k][2]) > 1e-6 for k in files),
                dir_mtime_differ=sum(abs(got[k][2] - want[k][2]) > 1e-6 for k in common if got[k][0] == 'dir'),
                dir_mtime_names=[k for k in common if got[k][0] == 'dir' and abs(got[k][2] - want[k][2]) > 1e-6])


def kinds(window):
    count = Counter(request.kind for request in window.requests)
    return {kind: count.get(kind, 0) for kind in ('read', 'stat', 'list', 'write')}


def unpack(data_or_path, dest: Path):
    dest.mkdir()
    opener = dict(fileobj=io.BytesIO(data_or_path)) if isinstance(data_or_path, bytes) else dict(name=data_or_path)
    with tarfile.open(mode='r:gz', **opener) as archive:
        archive.extractall(dest, filter='fully_trusted')


def ustar_size(sizes, dirs):
    body = sum(512 + -(-size // 512) * 512 for size in sizes) + 512 * dirs + 1024
    return -(-body // 10240) * 10240


def replay_cost(file_sizes, ch_sizes):
    """Computed, not built: one whole re-pack of the working archive per operation, as the adapter does today."""
    sizes, dirs, total, steps = dict(file_sizes), 2, 0, []
    for label, edit in (('change', lambda: sizes.__setitem__('member-000', len(NEW))),
                        ('create', lambda: sizes.__setitem__('created', len(CRE))),
                        ('rename-file', lambda: None), ('remove', lambda: sizes.pop('member-004')),
                        ('rename-dir', lambda: None)):
        edit()
        steps.append(ustar_size(sizes.values(), dirs))
        total += steps[-1]
    return dict(rewrites=len(steps), bytes=total, each=steps)


# ---------------------------------------------------------------- one run
def run(store, tap, client, label, members, *, layout='name', rename='A', removal='db', extended=False,
        mode='Y', source='db', drop_db=False, kill_at=None, files_only=False):
    out = dict(label=label, members=members, layout=layout, rename=rename, removal=removal, extended=extended,
               mode=mode, source=source, drop_db=drop_db, kill_at=kill_at)
    with tempfile.TemporaryDirectory(dir=SCRATCH, prefix=f'lab-{label}-') as lab_name:
        lab = Path(lab_name)
        loop = Loop(store.endpoint, bundles=1, members=members, parent=lab)        # filled directly, not via the tap
        try:
            stem, key = loop.stems[0], loop.keys[loop.stems[0]]
            ch = names(stem)
            shutil.copytree(loop.content / stem, lab / 'oracle' / stem)
            out['replay'] = replay_cost({f'member-{i:03d}': 512 + 128 * i for i in range(members)}, None)
            proto = Proto(lab, client, loop.bucket, key, stem, layout=layout, rename=rename, removal=removal)
            with tap.window() as pulled:
                proto.pull()
            out['pull'] = kinds(pulled)
            with tap.window() as changed:
                out['ops'] = apply_proto(proto, ch, extended)
            out['changes'] = kinds(changed)
            apply_oracle(lab / 'oracle', ch, extended)
            want = tree(lab / 'oracle')
            view = proto.view()
            out['view_equals_oracle'] = view == {k: (v[0], v[3]) for k, v in want.items()}
            files, size = proto.durable.usage()
            altered = proto.db.execute("SELECT count(*), coalesce(sum(size), 0) FROM member WHERE alt IS NOT NULL").fetchone()
            out['sizes'] = dict(proto.sizes, durable_files=files, durable_bytes=size, altered_files=altered[0],
                                altered_bytes=altered[1], state_db=proto.dbpath.stat().st_size,
                                index_rows=proto.db.execute('SELECT count(*) FROM member').fetchone()[0],
                                record_file=(proto.manifest_writes[-1] if proto.manifest_writes else 0))
            out['durable_listing'] = proto.durable.listing() if members <= 6 else None
            if files_only:                       # what the tier's files alone say, with no record of removals or renames
                spool = proto.rebuild(source='files', put=False)
                unpack(str(spool), lab / 'files-only')
                os.unlink(spool)
                out['files_only'] = compare(tree(lab / 'files-only'), want)
            if drop_db:
                proto.db.close()
                proto.db = None
                for found in list((lab / 'state').iterdir()):
                    os.unlink(found)
                out['state_dir_after_drop'] = [p.name for p in (lab / 'state').iterdir()]
            proto.kill_at = kill_at
            with tap.window() as rebuilt:
                try:
                    proto.rebuild(source=source, mode=mode)
                except Kill as kill:
                    out['killed'] = str(kill)
            out['rebuild'] = kinds(rebuilt)
            out['sizes'].update(proto.sizes)
            if 'killed' in out:
                # A new process over the same state directory and durable tier.
                proto.db.close()
                again = Proto(lab, client, loop.bucket, key, stem, layout=layout, rename=rename, removal=removal)
                out['left_on_tier'] = again.durable.listing()
                out['recorded'] = dict(zip(('staged', 'generation', 'dirty'),
                                           again.db.execute('SELECT staged, generation, dirty FROM bundle').fetchone()))
                try:
                    out['view_after_kill_equals_oracle'] = again.view() == {k: (v[0], v[3]) for k, v in want.items()}
                except Exception as error:
                    out['view_after_kill_equals_oracle'] = f'error: {error!r}'
                if out['view_after_kill_equals_oracle'] is True:
                    stale = again.durable.root / f'.rebuild-{stem}.tar.gz.tmp'
                    if stale.exists():
                        os.unlink(stale)
                    with tap.window() as recovered:
                        again.rebuild(source=source, mode=mode)
                    out['recovery'] = kinds(recovered)
                proto = again
            elif proto.db is not None:
                out['view_after_rebuild_equals_oracle'] = proto.view() == {k: (v[0], v[3]) for k, v in want.items()}
            out['tier_after_rebuild'] = proto.durable.listing() if members <= 6 else None
            with tap.window() as verified:
                data = client.get_object(Bucket=loop.bucket, Key=key)['Body'].read()
            out['verify'] = kinds(verified)
            out['sizes']['written_back'] = len(data)
            unpack(data, lab / 'verify')
            out['result'] = compare(tree(lab / 'verify'), want)
        finally:
            if 'proto' in locals() and proto.db is not None:
                proto.db.close()
            loop.close()
    return out


def change_kill(store, client, point, layout):
    """A kill between a change's step on the durable tier and its database commit; then a new process looks."""
    with tempfile.TemporaryDirectory(dir=SCRATCH, prefix=f'lab-kill-{point}-') as lab_name:
        lab = Path(lab_name)
        loop = Loop(store.endpoint, bundles=1, members=6, parent=lab)
        try:
            stem, key = loop.stems[0], loop.keys[loop.stems[0]]
            ch = names(stem)
            shutil.copytree(loop.content / stem, lab / 'oracle' / stem)
            proto = Proto(lab, client, loop.bucket, key, stem, layout=layout)
            proto.pull()
            proto.write(ch['pre'], PRE, None, MT0)                      # an altered file under the directory, committed
            (lab / 'oracle' / ch['pre']).write_bytes(PRE)
            before = {k: (v[0], v[3]) for k, v in tree(lab / 'oracle').items()}
            proto.kill_at, killed = point, False
            try:
                if point == 'rename-after-move':
                    proto.rename(*ch['rendir'])
                else:
                    proto.write(ch['change'], NEW, None, MT1)
            except Kill:
                killed = True
            proto.db.close()                                            # uncommitted work is rolled back
            again = Proto(lab, client, loop.bucket, key, stem, layout=layout)
            try:
                same = again.view() == before
            except Exception as error:
                same = f'error: {type(error).__name__} on {Path(str(getattr(error, "filename", ""))).name}'
            say(f"\n=== change-kill point={point} layout={layout} killed={killed}")
            say(f"  view from state+tier equals the oracle as it was before the killed change: {same}")
            named = {row[0] for row in again.db.execute('SELECT alt FROM member WHERE alt IS NOT NULL')}
            for line in again.durable.listing():
                rel = line.split('  [')[0]
                mark = '' if line.endswith('/') or rel in named or rel == again.staged_rel else '   <- no row names this file'
                say('     left:', line + mark)
            say(f"  rows naming a file that is not there: {sorted(n for n in named if not (again.durable.root / n).exists())}")
            again.db.close()
        finally:
            loop.close()


def show(out):
    r = out['result']
    say(f"\n=== {out['label']}  members={out['members']} layout={out['layout']} rename={out['rename']} "
        f"removal={out['removal']} extended={out['extended']} mode={out['mode']} source={out['source']} "
        f"drop_db={out['drop_db']} kill_at={out['kill_at']}")
    say(f"  TREES EQUAL names={r['names_equal']} bytes={r['bytes_equal']}  entries={r['entries']} "
        f"missing={r['missing'][:4]} extra={r['extra'][:4]} bytes_differ={r['bytes_differ']}")
    say(f"  modes differing={r['mode_differ']}  file mtimes differing={r['file_mtime_differ']}  "
        f"dir mtimes differing={r['dir_mtime_differ']} {r['dir_mtime_names']}")
    say(f"  requests  pull={out['pull']}  changes={out['changes']}  rebuild+write-back={out['rebuild']}  verify={out['verify']}")
    say(f"  view from state+tier equals oracle before rebuild: {out['view_equals_oracle']}; "
        f"after: {out.get('view_after_rebuild_equals_oracle')}")
    say('  per operation:')
    for op in out['ops']:
        say(f"    {op['op']:<16} rows={op['rows']:<4} tier bytes={op['tier_bytes']:<8} created={op['tier_created']:<4} "
            f"moved={op['tier_moved']:<3} removed={op['tier_removed']:<4} record-file bytes={op['record_bytes']}")
    total = {k: sum(op[k] for op in out['ops']) for k in ('rows', 'tier_bytes', 'tier_created', 'tier_moved', 'tier_removed', 'record_bytes')}
    say(f"    TOTAL            {total}")
    say(f"  sizes {out['sizes']}")
    say(f"  replay contrast (computed): {out['replay']['rewrites']} whole re-packs, {out['replay']['bytes']} bytes "
        f"rewritten {out['replay']['each']}")
    if out.get('files_only'):
        f = out['files_only']
        say(f"  tier files alone (no record): names={f['names_equal']} bytes={f['bytes_equal']} missing={f['missing'][:6]} extra={f['extra'][:6]}")
    if out.get('durable_listing'):
        say('  durable tier after the changes:')
        for line in out['durable_listing']:
            say('     ', line)
    if out.get('killed'):
        say(f"  KILLED at {out['killed']}: recorded={out['recorded']} view equals oracle={out['view_after_kill_equals_oracle']} "
            f"recovery requests={out.get('recovery')}")
        for line in out['left_on_tier']:
            say('     left:', line)
    if out.get('tier_after_rebuild'):
        say('  durable tier at the end:', out['tier_after_rebuild'])


def main():
    wanted = sys.argv[1:] or ['core', 'rename', 'removal', 'kill']
    results = []
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)                # the store's access log, not a result
    with Store() as store, Tap(store.endpoint) as tap:
        try:
            config = Config(request_checksum_calculation='when_required', response_checksum_validation='when_required',
                            retries={'max_attempts': 1})
        except TypeError:
            config = Config(retries={'max_attempts': 1})
        client = boto3.client('s3', endpoint_url=tap.endpoint, region_name='us-east-1', aws_access_key_id='test',
                              aws_secret_access_key='test', config=config)
        plan = []
        if 'core' in wanted:
            plan += [dict(label='core-6', members=6, files_only=True), dict(label='core-300', members=300, files_only=True)]
        if 'rename' in wanted:
            plan += [dict(label='renameB-6', members=6, rename='B'), dict(label='renameB-300', members=300, rename='B'),
                     dict(label='ext-byname-6', members=6, extended=True),
                     dict(label='ext-byid-6', members=6, extended=True, layout='id'),
                     dict(label='ext-byname-300', members=300, extended=True),
                     dict(label='ext-byid-300', members=300, extended=True, layout='id')]
        if 'removal' in wanted:
            plan += [dict(label='record-6', members=6, removal='manifest', extended=True, source='manifest', drop_db=True),
                     dict(label='record-300', members=300, removal='manifest', extended=True, source='manifest', drop_db=True),
                     dict(label='record-byid-6', members=6, removal='manifest', extended=True, layout='id',
                          source='manifest', drop_db=True)]
        if 'kill' in wanted:
            plan += [dict(label='X-gen-6', members=6, mode='X'), dict(label='X-gen-300', members=300, mode='X'),
                     dict(label='kill-Y-after-put', members=6, mode='Y', kill_at='after-put'),
                     dict(label='kill-X-after-put', members=6, mode='X', kill_at='after-put'),
                     dict(label='kill-X-after-swap', members=6, mode='X', kill_at='after-swap'),
                     dict(label='kill-X-after-db', members=6, mode='X', kill_at='after-db'),
                     dict(label='kill-Xover-after-swap', members=6, mode='Xover', kill_at='after-swap')]
        for item in plan:
            try:
                out = run(store, tap, client, **item)
                show(out)
                results.append(out)
            except Exception as error:
                import traceback
                say(f"\n=== {item['label']} FAILED: {error!r}")
                traceback.print_exc(file=sys.stdout)
                results.append(dict(label=item['label'], failed=repr(error)))
        if 'kill' in wanted:
            for point, layout in (('rename-after-move', 'name'), ('write-after-file', 'name'), ('write-after-file', 'id')):
                try:
                    change_kill(store, client, point, layout)
                except Exception as error:
                    say(f"\n=== change-kill {point} {layout} FAILED: {error!r}")
    (SCRATCH / 'results.json').write_text(json.dumps(results, indent=1, default=str))
    say('\nSUMMARY')
    for out in results:
        if 'failed' in out:
            say(f"  {out['label']:<24} FAILED {out['failed']}")
        else:
            r = out['result']
            say(f"  {out['label']:<24} names={r['names_equal']} bytes={r['bytes_equal']} pull={out['pull']} "
                f"rebuild={out['rebuild']} verify={out['verify']}")
    equal = bool(results) and all('failed' not in out and out['result']['names_equal'] and out['result']['bytes_equal']
                                  for out in results)
    say(f"RESULT trees_equal={'yes' if equal else 'no'} runs={len(results)} results={SCRATCH / 'results.json'}")
    return 0 if equal else 1


if __name__ == '__main__':
    sys.exit(main())
