# Research 02 — one bundle rebuilt from recorded state (GOAL R8)

## What was run

A throwaway script, `proto.py`, on the local loop's helper at commit `ae420d1` of branch
`test/local-bundle-loop` (`tools/bundle_loop.py`, blob `e6d18b4`; `tests/loop/tap.py`, blob
`e1c91b3`). It imports nothing of the project and uses no FUSE. A moto store holds one generated
`tar.gz`; a `boto3` client reaches it only through the helper's tap, which counts requests on the
wire. Three plain directories stand for the mount: `durable/` (the first shared stage tier),
`state/` (a SQLite database, outside every tier) and `oracle/` (the bundle's unpacked tree).

Run again, from the cycle worktree: `uv run python spec/plan-the-bundle-loop/prototype/rebuild.py`,
the kept copy of `proto.py` (see `prototype/README.md` for where it finds the helper). 18 full runs
and 3 kill cases, about 10 s. The plan step's own output stayed in its scratch directory.

The flow. **Pull:** one GET, decompressed once to `durable/bundle-000.tar` (temp + rename), members
indexed (name, data offset, size, mode, mtime) into the database. **Changes:** change one member,
create one, rename one, remove one, rename the directory `sub` (half the members); each is rows
plus, for new bytes, a file on the durable tier. **Rebuild:** unchanged members are copied from the
staged `.tar` at their offsets, changed and created ones from the files beside it, removed ones are
left out, renamed ones take their new names; the stream is gzipped into a spool file and sent as one
PUT. **Verify:** the object is fetched, unpacked and compared with the oracle.

## Result

| Run (6 and 300 members each unless noted) | Names equal | Bytes equal |
|---|---|---|
| Core: rename (A), removal in the database only, altered files by name | yes | yes |
| Rename (B), copy and remove | yes | yes |
| Core plus an altered file under the renamed directory, by name and by id | yes | yes |
| Removal in a per-bundle record file, state database deleted before the rebuild (by id: 6 only) | yes | yes |
| Staged `.tar` rebuilt first (option X) | yes | yes |
| The durable tier's files alone, no record of any kind (local, not written back) | **no** | no |

Requests to the source, identical in all 18 runs:

| Window | read | stat | list | write |
|---|---|---|---|---|
| Pull | 1 | 0 | 0 | 0 |
| The five changes | 0 | 0 | 0 | 0 |
| Rebuild and write-back | 0 | 0 | 0 | 1 |
| Verifying fetch (the check, not the design) | 1 | 0 | 0 | 0 |

Modes and mtimes are recorded per member and written to a PAX tar, which keeps sub-second mtimes.
Compared after unpacking: file and directory modes, 0 differing; file mtimes, 0 differing (1 µs
tolerance); directory mtimes, 1 differing in every run (the top directory: the prototype does not
touch a directory's mtime when its entries change). Ownership was not compared.

Replay, computed and not built: the current adapter re-packs the whole working archive once per
call, so the same five changes are 5 whole rewrites, 102,400 bytes at 6 members and 30,566,400 at
300. The rebuild wrote one compressed object: 9,178 and 5,932,471 bytes.

## Rename alternatives

(A) changes the recorded name; a directory rename rewrites the recorded prefix of every row under
it. (B) copies each member to the new name as an altered file and marks the old one removed.

| Directory rename, 150 members under it | Bytes to the durable tier | Files created | Files moved | Rows changed |
|---|---|---|---|---|
| (A) | 0 | 0 | 0 | 151 |
| (B) | 2,956,800 | 150 | 0 | 452 |
| (A), one altered file under it, kept by name | 0 | 0 | 1 (the directory) | 152 |
| (A), one altered file under it, kept by id | 0 | 0 | 0 | 151 |

A file rename: (A) 1 row and nothing on the tier; (B) 3 rows and a 768-byte copy. Bytes to the source
do not differ: one object either way. After (B) the tier held 154 files and 9,383,048 bytes against
3 files and 6,425,480 under (A).

By name, a person sees `bundle-000.tar` and a tree `bundle-000/created.txt`,
`bundle-000/member-000.txt`, `bundle-000/moved/member-001.txt`. By id, `bundle-000.tar` and
`bundle-000.d/2`, `/6`, `/9`. By name, the POSIX rename and the database commit are two steps. A kill
injected between them left a row naming `sub/member-001.txt` and the file at
`moved/member-001.txt`: a read of that member failed with `ENOENT`. By id there is no such step.

## Removal alternatives

| | Written per namespace change | Rebuild equals oracle | With the state database deleted |
|---|---|---|---|
| (i) database row only | 1 row; nothing on the tier | yes | no: the removed member returns and every renamed member has its old name (5 names missing, 6 extra, at 6 members) |
| (ii) whiteout file at the removed name | not run | not run | not run |
| (iii) per-bundle record file, temp + rename | 1 file: 914 B at 6 members, 33,604 B at 300 (about 110 B a member) | yes | yes, by name and by id |

(ii), argued: a whiteout repairs the removal, but under (A) a renamed unchanged member has no bytes
on the tier, so the tier's files alone show it at its old name. Whiteouts then need a second marker
that redirects a new name to an old place, one marker per removed name inside the by-name tree, an
opaque mark for a directory removed and made again, and every marker moved with a directory rename.
Only (iii) let the tier describe itself here. Its cost grows with the member count on every change.

## Sizes, and what the limits would count

| Bytes | 6 members | 300 members |
|---|---|---|
| Source object | 5,595 | 5,930,309 |
| Staged `.tar` | 20,480 | 6,420,480 |
| Altered files (2) | 5,000 | 5,000 |
| Spool: the new compressed object, on the tier until the PUT returns | 9,178 | 5,932,471 |
| Next staged `.tar`, option X only | 30,720 | 6,420,480 |
| State database file (9 and 303 rows) | 20,480 | 57,344 |
| Record file, (iii) only | 914 | 33,604 |

The helper's bytes are random, so compression shows nothing here. One PUT needs its length first, so
the compressed object is assembled whole before it is sent; the prototype spools it on the durable
tier. In this layout a size limit counts the staged `.tar`, the altered files, the record file, and
the spool while it exists; a count limit counts one file per staged bundle, one per altered file,
and one record file per bundle. An altered file not yet in a written-back bundle is the only copy of
its bytes and cannot be evicted. After a write-back that leaves the old staged `.tar` in place, that
`.tar` and its altered files are clean but only correct together, so they leave as one unit.

## Atomic steps and kill points

| Step | All-or-nothing by | A kill before its database commit leaves |
|---|---|---|
| Stage the pulled bundle | temp + rename | a staged `.tar` no row names (not injected) |
| Write an altered file | temp + rename | injected: the view equals the tree before the change; a file no row names, by name at a live path |
| Record a removal | one transaction; (iii) temp + rename first | the record one change ahead of the database (not injected) |
| Rename (A) | one transaction; by name, plus one POSIX rename | injected: a row naming a missing file (above) |
| Replace the source object | one PUT of a complete object | injected: the bundle still recorded as changed; a second rebuild sent 1 PUT and gave the same tree |
| Refresh the staged `.tar`, retire altered files | see below | see below |

After the PUT, three options were run. **Lazy:** compress straight from state, mark the bundle
clean, change nothing on the tier. **Generation:** write `bundle-000.g2.tar`, compress from it, PUT,
switch offsets and the staged name in one transaction, then delete the old `.tar` and the altered
files. Kills after the PUT, before the switch and after it all left a view equal to the oracle; the
last left the old `.tar` and two altered files that no row names. Peak extra space was the new
`.tar` plus the spool, 12.4 MB against 5.9 MB lazy at 300 members. **Rename over the old name:** a
kill before the commit left old offsets pointing into the new file, and the view was wrong.

## What the results recommend

- **R3 (a):** keep the staged `.tar` inside the first shared stage tier beside its altered files,
  counted by that tier's limits, and retire the `staging` key. Turned down: a path of its own
  outside every tier. The rebuild read nothing but the state database and that tier.
- **R3 (b):** rebuild from recorded state and write one object. Turned down: replay, at five whole
  rewrites for five changes.
- **R3 (c):** a rename is a change of recorded state, a directory rename a change of prefix. Turned
  down: copy and remove. Altered files kept by id have no kill window; kept by name they need the
  record written before the move and a mount that completes it.
- **R3 (d):** a per-bundle record file on the durable tier, with the database row. Turned down: the
  database alone, which the tier cannot stand in for, and whiteouts. Its per-change cost argues for
  writing it when the tier is brought up to date, not on every change.
- After a write-back, either the lazy option or a generation-named `.tar`; never a rename over the
  name the recorded offsets refer to.

## Not tested

Whiteouts; a multipart or streamed upload; a source object changed by another writer between pull
and write-back (the flow makes no stat request and sets no precondition); kills during the pull,
the spool or a record-file write; writes during a rebuild; a second round of changes after a
refresh; symlinks, hard links, empty directories, `rmdir`; ownership; members larger than memory;
any network filesystem; any store but moto.
