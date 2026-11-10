"""Plant one named mutant of the rename fix into the lab tree (working copy only)."""
import sys
from pathlib import Path
lab, name = Path(sys.argv[1]), sys.argv[2]
cat, ov = lab / 'src/sosfs/state/catalog.py', lab / 'src/sosfs/engine/overlay.py'
LOOP = ("                for path, mover in self.catalog.collisions(old, new):\n"
        "                    self._vacate(path, mover=mover)")
TOP = "                self._vacate(new, mover=entry.id)\n"
RANGE = "t'WHERE src.path >= {old + \"/\"} AND src.path < {old + \"0\"} ORDER BY dest.path')"
JOIN = "t'JOIN entry AS dest ON dest.path = {new} || substr(src.path, {offset}) '"
M = {
    'M1-no-descendant-vacate': (ov, LOOP, "                pass"),
    'M2-direct-children-only': (cat, RANGE, "t'WHERE src.parent = {old} ORDER BY dest.path')"),
    'M3-live-destinations-only': (cat, JOIN, "t'JOIN entry AS dest ON dest.path = {new} || substr(src.path, {offset}) AND dest.deleted = 0 '"),
    'M4-live-movers-only': (cat, RANGE, "t'WHERE src.path >= {old + \"/\"} AND src.path < {old + \"0\"} AND src.deleted = 0 ORDER BY dest.path')"),
    'M5-offset-off-by-one': (cat, "        offset = len(old) + 1\n        rows = self.db.execute(\n            t'SELECT dest.path",
                             "        offset = len(old)\n        rows = self.db.execute(\n            t'SELECT dest.path"),
    'M6-no-mover': (ov, "                    self._vacate(path, mover=mover)", "                    self._vacate(path)"),
    'M7-purge-without-replay': (ov, "                    self._vacate(path, mover=mover)",
                                "                    with self.db.transaction():\n                        self._purge(path)"),
    'M8-descendants-before-top': (ov, TOP + LOOP, LOOP + "\n" + TOP.rstrip('\n')),
}
f, a, b = M[name]
s = f.read_text()
assert s.count(a) == 1, (name, s.count(a))
f.write_text(s.replace(a, b))
print('planted', name)
