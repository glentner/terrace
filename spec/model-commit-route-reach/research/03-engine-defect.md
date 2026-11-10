# 03 — An engine defect the new reach found (GOAL Q6: stop and ask)

**Found:** 2026-11-09, planning dry run of P2 (`route_gate.py reach_seeds`) on the plan-first lab `c7plan-lab7`
(the prototype of PLAN § 2.1, both composites), `--hypothesis-seed=5`, without `CI`. The census run of
`tests/engine/test_model.py` failed; P1 (`model_attacks.py`, then `model reach detect mutants records timing suite`)
was GREEN on the same tree (177 checks, 254 s), so the fixed CI draw does not reach it.

**What:** `OverlayEngine.rename` raises `sqlite3.IntegrityError: UNIQUE constraint failed: entry.path` (not an
`OSError`) from `Catalog.rename`'s descendant prefix rewrite (`src/sosfs/state/catalog.py:180`, called from
`src/sosfs/engine/overlay.py:486`), when a directory is renamed onto a name under which a removed child's tombstone
row still sits in the catalog. `src/` is the base's (`980f998`); the defect is the base engine's.

**Falsifying example** (Hypothesis, seed 5; the shorter of the two it reported):

```
state.mkdir(path='/d2')
state.move_out_rmdir_unlink(new='/a.dat', path='/d2/a.dat')
state.mkdir(path='/d1')
state.create_with_content(data=b'', path='/d1/a.dat')
state.rename(new='/d2', old='/d1')
state.teardown()
```

**Deterministic reproduction on the base engine** (a scratch tree of `980f998`, `PYTHONPATH` at its root):

```python
import dataclasses, tempfile
from pathlib import Path
from sosfs.engine.overlay import OverlayEngine
from tests.engine.conftest import Clock, build_spec, seed

root = Path(tempfile.mkdtemp(prefix='repro-'))
seed(root / 'project', 'seeded.txt', b'initial-content')
engine = OverlayEngine(dataclasses.replace(build_spec(root, tiers=3), fsync_policy='commit'), clock=Clock())
engine.start()
engine.mkdir('/d2')
engine.create('/d2/a.dat')
engine.commit_upstream('/d2/a.dat')
engine.rename('/d2/a.dat', '/a.dat')     # queued; leaves a tombstone at /d2/a.dat
engine.rmdir('/d2')                      # queued rmdir; /d2 becomes a tombstone
engine.unlink('/a.dat')                  # cancels the queued move, queues the removal behind the rmdir
engine.mkdir('/d1')
engine.create('/d1/a.dat')
engine.rename('/d1', '/d2')              # raises sqlite3.IntegrityError
```

Output:

```
queued: [('rmdir', 2), ('unlink', 3), ('mkdir', 5), ('data', 6)]
catalog: [(1, '/', 0, '/'), (3, '/a.dat', 1, '/d2/a.dat'), (5, '/d1', 0, None), (6, '/d1/a.dat', 0, None),
          (2, '/d2', 1, '/d2'), (4, '/d2/a.dat', 1, None)]
rename /d1 -> /d2 raised IntegrityError UNIQUE constraint failed: entry.path
```

Row 4 is the tombstone the move-out left at `/d2/a.dat`. It is not purged while the `rmdir` row for `/d2` (and the
unlink behind it) are queued. The directory rename's prefix rewrite then moves `/d1/a.dat` onto `/d2/a.dat` and hits
the UNIQUE index. The plain oracle renames `/d1` to `/d2` without error, so this is a divergence with an exception
the VFS API should never raise (§ 11).

**Which rule reaches it:** `move_out_rmdir_unlink`, the composite added for R6's M7 (PLAN deviation row 6). In planning,
every draw without it passed: the route rule alone on its CI draw and on the pilot's seeds 1–20, the three-rule design on
its CI draw and seeds 1–20, and the base on its CI draw and seeds 1–20. With it, P1's CI draw and P2's seeds 1–4, 6 and 8 passed, and seed 5 did
not; the P2 dry run stopped at that first red, so seeds 7 and 9–20 under the census, and P3's plain seeds, are unmeasured.

**State of the plan when it stopped:** see `../STATE.md`.
