import dataclasses, sys, tempfile
from pathlib import Path
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed

root = Path(tempfile.mkdtemp(prefix='rc-', dir=sys.argv[1]))
seed(root / 'project', 'seeded.txt', b'initial-content')
e = OverlayEngine(dataclasses.replace(build_spec(root, tiers=3), fsync_policy='commit'), clock=Clock())
e.start()

def dump(label):
    ents = [(r['id'], r['path'], r['deleted'], r['source_path']) for r in
            e.db.execute(t'SELECT id, path, deleted, source_path FROM entry ORDER BY id') if r['path'] not in ('/', '/seeded.txt')]
    rows = [(r.id, r.entry, r.op, (r.detail or {}).get('path')) for r in e.journal.queued()]
    print(f'{label:34} entries={ents}\n{"":34} queued={rows}')

e.mkdir('/d2'); e.create('/d2/a.dat'); e.commit_upstream('/d2/a.dat'); dump('after commit')
e.rename('/d2/a.dat', '/a.dat'); dump('after rename /d2/a.dat->/a.dat')
e.rmdir('/d2'); dump('after rmdir /d2')
e.unlink('/a.dat'); dump('after unlink /a.dat')
e.flush(); dump('after flush')
e.mkdir('/d1'); e.create('/d1/a.dat')
try:
    e.rename('/d1', '/d2'); print('rename OK')
except Exception as x:
    print('rename RAISED', type(x).__module__, type(x).__name__, x)
