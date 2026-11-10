# Second stranding route: a rename tombstone carried by its directory's rename is missed by the row's purge.
import dataclasses, sys, tempfile
from pathlib import Path
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed
root = Path(tempfile.mkdtemp(prefix='r2-', dir=sys.argv[1]))
seed(root / 'project', 'seeded.txt', b'x')
e = OverlayEngine(dataclasses.replace(build_spec(root, tiers=3), fsync_policy='commit'), clock=Clock()); e.start()
e.mkdir('/d1'); e.create('/d1/a.dat'); e.commit_upstream('/d1/a.dat')
e.rename('/d1/a.dat', '/d1/c.dat'); e.rename('/d1', '/d3'); e.flush()
print('route2 after flush:', [(r['path'], r['deleted'], r['source_path']) for r in e.db.execute(t'SELECT * FROM entry ORDER BY path') if r['path'] not in ('/', '/seeded.txt')], 'queued', e.journal.queued())
