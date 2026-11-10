"""Plant one named mutant into the lab tree (working copy only); anchored on the decision it tests."""
import sys
from pathlib import Path
lab, name = Path(sys.argv[1]), sys.argv[2]
cat, ov = lab / 'src/sosfs/state/catalog.py', lab / 'src/sosfs/engine/overlay.py'
LOOP = ("                for path in self.catalog.collisions(old, new):\n"
        "                    self._vacate(path)")
TOP = "                self._vacate(new, mover=entry.id)\n"
RANGE = "t'WHERE moved.path >= {old + \"/\"} AND moved.path < {old + \"0\"} ORDER BY dest.path')"
JOIN = "t'JOIN entry AS dest ON dest.path = {new} || substr(moved.path, {offset}) '"
M = {
    # the rename fix
    'M1-no-descendant-vacate': (ov, LOOP, "                pass"),
    'M2-direct-children-only': (cat, RANGE, "t'WHERE moved.parent = {old} ORDER BY dest.path')"),
    'M3-tombstoned-holders-ignored': (cat, JOIN, JOIN[:-2] + " AND dest.deleted = 0 '"),
    'M4-tombstoned-movers-ignored': (cat, RANGE, RANGE.replace(' ORDER BY', ' AND moved.deleted = 0 ORDER BY')),
    'M5-offset-off-by-one': (cat, "        offset = len(old) + 1\n        rows = self.db.execute(\n            t'SELECT dest.path",
                             "        offset = len(old)\n        rows = self.db.execute(\n            t'SELECT dest.path"),
    'M6-purge-without-replay': (ov, "                    self._vacate(path)",
                                "                    with self.db.transaction():\n                        self._purge(path)"),
    'M7-descendants-before-top': (ov, TOP + LOOP, LOOP + "\n" + TOP.rstrip('\n')),
    # the replay-recursion fix
    'N1-no-deferral': (ov, "            if rows & self._running:\n                return", "            if False:\n                return"),
    'N2-nothing-registered': (ov, "            self._running.add(row.id)\n", ""),
    'N3-defer-on-any-holder': (ov, "            if rows & self._running:\n", "            if rows:\n"),
    'N4-registration-leaks': (ov, "                self._running.discard(row.id)\n", "                pass\n"),
}
f, a, b = M[name]
s = f.read_text()
assert s.count(a) == 1, (name, s.count(a))
f.write_text(s.replace(a, b))
print('planted', name)
