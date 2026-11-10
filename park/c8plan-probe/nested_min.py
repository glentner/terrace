# Minimal: two nested removed directories and a moved-out, removed grandchild; flush alone recurses.
import sys, tempfile
from pathlib import Path
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed
root = Path(tempfile.mkdtemp(prefix='m-', dir=sys.argv[1]))
seed(root / 'project', 'd2/s/a.dat', b'payload')          # adopted from the source, no commit needed
e = OverlayEngine(build_spec(root, tiers=1), clock=Clock()); e.start()
e.readdir('/'); e.readdir('/d2'); e.readdir('/d2/s')
e.rename('/d2/s/a.dat', '/a.dat'); e.rmdir('/d2/s'); e.rmdir('/d2'); e.unlink('/a.dat')
print('queued', [(r.id, r.entry, r.op) for r in e.journal.queued()])
try:
    e.flush(); print('flush OK')
except RecursionError:
    print('flush RecursionError')
