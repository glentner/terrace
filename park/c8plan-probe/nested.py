import dataclasses, sys, tempfile
from pathlib import Path
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed
root = Path(tempfile.mkdtemp(prefix='n-', dir=sys.argv[1]))
seed(root / 'project', 'seeded.txt', b'x')
e = OverlayEngine(dataclasses.replace(build_spec(root, tiers=int(sys.argv[2])), fsync_policy='commit'), clock=Clock()); e.start()
def dump(label):
    ents = [(r['id'], r['path'], r['deleted'], r['source_path']) for r in e.db.execute(t'SELECT * FROM entry ORDER BY id') if r['path'] not in ('/', '/seeded.txt')]
    print(label, ents, [(r.id, r.entry, r.op) for r in e.journal.queued()])
e.mkdir('/d2'); e.mkdir('/d2/s'); e.create('/d2/s/a.dat'); e.commit_upstream('/d2/s/a.dat'); dump('commit')
e.rename('/d2/s/a.dat', '/a.dat'); dump('rename')
e.rmdir('/d2/s'); e.rmdir('/d2'); dump('rmdirs')
if sys.argv[3] == 'unlink':
    e.unlink('/a.dat'); dump('unlink')
try:
    e.flush(); print('flush OK'); dump('after flush')
except RecursionError as x:
    print('flush RecursionError')
