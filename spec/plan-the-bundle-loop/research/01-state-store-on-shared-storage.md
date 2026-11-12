# Research 01 — The state store on shared storage

Serves R3 (f) and R4; evidence for R3 (e) and R2 (d). Each statement is tagged **documented** (SQLite's
own pages, read 2026-11-12), **measured** (the local run below), or **unverified**.

## What SQLite documents

| Mode | Needs from the filesystem | Said about network filesystems |
|---|---|---|
| WAL, normal locking | POSIX advisory locks on the database file; a `-shm` file beside it, memory-mapped and locked, shared by every connection | "All processes using a database must be on the same host computer; WAL does not work over a network filesystem. This is because WAL requires all processes to share a small amount of memory and processes on separate host machines obviously cannot share memory with each other." (`wal.html` § 1) |
| WAL, `locking_mode=EXCLUSIVE` set before first access | Locks on the database file only; no `-shm` | "a process can interact with a WAL database without using shared memory if that process is guaranteed to be the only process accessing the database." (`wal.html` § 8). "the database connection never releases file-locks." (`pragma.html`, locking_mode) |
| Rollback journal (DELETE, TRUNCATE, PERSIST) | Locks on the database file only; a working `fsync` | "These network unreliability issues can be mitigated, completely or to an acceptable degree, by using SQLite in rollback mode. However, the SQLite library is not tested in across-a-network scenarios" (`useovernet.html`) |

- **Every mode leans on the filesystem's locks, and the failure is corruption, not a refusal.** "SQLite
  relies on exclusive locks for write operations, and those have been known to operate incorrectly for
  some network filesystems. This has led to database corruption." (`useovernet.html`). The condition is
  concurrency: "if two or more threads or processes try to access the same database at the same time,
  then database corruption might result" (`howtocorrupt.html` § 2.1); "avoid putting SQLite database
  files on NFS if multiple processes might try to access the file at the same time" (`faq.html` q5).
- **One host at a time.** `useovernet.html` lists it as an acceptable use: "Unless one of the above
  choices has been effected, or one at a time, exclusive access is used, a programmer should consider
  blocking that election". The seed's reading is right about the reason given for the WAL sentence,
  shared memory between hosts. No page says WAL with a `-shm` works on a network filesystem from one
  host; that case still needs the filesystem to map `-shm` and lock it.
- **An unclean end.** In WAL the `-wal` "might be retained on disk" (`wal.html` § 4) and "the first new
  connection to open the database will start a recovery process" (§ 9); "Recovery is run by the first
  thread to connect… Recovery restores the WAL-index so that it accurately describes the WAL file"
  (`walformat.html`). So a stale `-shm` from another host is rebuilt; that the opener learns it is
  first from locks on `-shm` is an inference (unverified). In rollback mode the opener rolls back a hot
  journal (`lockingv3.html`).
- **`synchronous` and a process kill.** "Transactions are durable across application crashes regardless
  of the synchronous setting or journal mode." A power loss or system crash is different: "A transaction
  committed in WAL mode with synchronous=NORMAL might roll back" (`pragma.html`, synchronous).
- **WAL-reset bug** (`wal.html` § 11): corruption with two or more connections writing or checkpointing
  at once, fixed in 3.51.3. The project's interpreter carries 3.53.1 (measured).

## What the project configures today

Read at `7e36368`, `src/terrace/state/db.py`:

- `sqlite3.connect(path, timeout, check_same_thread=False, autocommit=True)` (`:92`); `journal_mode = WAL`
  (`:94`); `synchronous = NORMAL` (`:95`); `foreign_keys = ON` (`:96`); `busy_timeout` of 5000 ms from
  `DEFAULT_TIMEOUT = 5.0` (`:35`, `:97`). No `locking_mode`, checkpoint or `fullfsync` pragma anywhere.
- One connection per thread, made on first use (`:70`, `:108-117`). Writes open with `BEGIN IMMEDIATE`
  (`:133`). `SCHEMA_VERSION = 2` (`:34`).
- `ExclusiveLock` (`:157-210`) is `flock(LOCK_EX | LOCK_NB)` on `<state>/lock` (`:184`), taken by the
  mount before the engine starts (`src/terrace/fuse/mount.py:163`, `:213`). Its docstring scopes it to
  one host: "Cross-host exclusion is governed by `overlay.shutdown_state` and the recorded mounting
  host, not by this lock" (`:162-164`). `Lifecycle.begin_mount` (`src/terrace/state/lifecycle.py:94-108`)
  checks and sets that row inside one `BEGIN IMMEDIATE` transaction. A design that wants the `flock` to
  stop a login-node commit while a compute node is mounted needs the filesystem to honour it across hosts.
- A change of journal mode touches `AGENTS.md` § 2 ("WAL is what makes committed transactions survive
  `SIGKILL`") and § 10 ("a WAL reader does not wait on a writer's open transaction (measured: 0.000 s
  against one)").

## The local check

`lab.py` (throwaway), one run, on a laptop, APFS on SSD; Python 3.15.2, SQLite 3.53.1. Connections
configured as `db.py` does. Kill: a child commits and acknowledges each row on a pipe, gets `SIGKILL`
at the 300th; another process opens the files. Nothing was run on a network filesystem.

| | WAL + NORMAL (today) | WAL + EXCLUSIVE | DELETE + FULL |
|---|---|---|---|
| Kill: acknowledged rows found by the next process | 301 of 301 | 301 of 301 | 300 of 300 |
| Files after the kill | db, `-shm`, `-wal` | db, `-wal` | db, `-journal` |
| Files after that process closes | db | db | db |
| Hand-off: files between a clean close and the next open | db | db | db |
| Second process while a write transaction is open: read | ok | "database is locked" | ok |
| Same: `BEGIN IMMEDIATE`, 0.5 s timeout | locked after 0.59 s | locked after 0.58 s | locked after 0.58 s |
| Second connection in the same process | opens | refused after its 2 s timeout | opens |
| Reader thread against a writer's open transaction (1.0 s) | 0.000 s | no second connection | 0.000 s |
| Writer's `COMMIT` against a reader's open transaction (1.0 s) | 0.000 s | — | 1.010 s |
| One write transaction, median / p95 of 500 | 0.022 / 0.026 ms | 0.030 / 0.062 ms | 0.285 / 0.568 ms |

Also run, all acknowledged rows present after the kill in each: WAL + FULL 0.092 / 0.152 ms;
DELETE + NORMAL 0.205 / 0.380 ms; TRUNCATE + FULL 0.196 / 0.468 ms (it leaves an empty `-journal`
after a clean close). The sync costs are a floor: `fullfsync` is off by default and "Only Mac OS X
supports F_FULLFSYNC" (`pragma.html`), so these runs did not force data to the device.

What it decides. WAL + EXCLUSIVE forces the engine to one connection for the whole process, and no
second process can read during a mount. DELETE keeps a connection per thread and a reader still does
not wait on an open write transaction, but a commit waits for every open read on another connection,
which WAL never does. The engine's reads that walk a cursor (`state/journal.py:309`, `:318`) were not
audited for how long they hold one.

## The check for the maintainer

Run on the filesystem that will hold the state directory; host A is a compute node, host B a login
node. Python 3.6 or later, standard library only; each line prints the SQLite version, so prefer the
interpreter the product runs on. `wal` as a last argument to every step runs today's mode instead; its
step B1 opens a WAL database from a second host while the first is live, which SQLite documents as
unsupported, so only ever in this throwaway directory.

```
D=$HOME/terrace-sqlite-check        # both hosts; must not exist
mkdir "$D" && cat > "$D/check.py" <<'EOF'
import fcntl, os, sqlite3, sys, time
d, role, mode, N = os.path.dirname(os.path.abspath(__file__)), sys.argv[1], (sys.argv[2:] or ['delete'])[0], 200
def db():
    c = sqlite3.connect(os.path.join(d, 'state.db'), timeout=1, isolation_level=None)
    c.execute('PRAGMA journal_mode=' + mode); c.execute('PRAGMA synchronous=FULL')
    return c
def flock():
    f = open(os.path.join(d, 'lock'), 'a+')
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return f
    except OSError: return None
def put(c, i): c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO t VALUES (?)', (i,))
who = '%s sqlite %s %s' % (os.uname().nodename.split('.')[0], sqlite3.sqlite_version, mode)
if role == 'a':        # host A: commit N rows, then hold an open write transaction and the flock
    held, c = flock(), db()
    assert held, 'flock not granted on host A'
    c.execute('CREATE TABLE t (i INTEGER PRIMARY KEY)')
    for i in range(1, N + 1): put(c, i); c.execute('COMMIT')
    put(c, N + 1)
    print('A  %s: acknowledged %d, holding write lock and flock' % (who, N), flush=True)
    time.sleep(3600)
elif role == 'b1':     # host B, while A holds
    free = flock() is not None
    try: put(db(), N + 2); wrote = 'ACCEPTED'
    except sqlite3.OperationalError as e: wrote = 'refused (%s)' % e
    print('B1 %s: flock %s, write %s' % (who, 'FREE' if free else 'held elsewhere', wrote))
    print('B1', 'HOLDS' if not free and wrote == 'refused (database is locked)' else 'FAILS')
elif role == 'b2':     # host B, after kill -9 of A
    free, c = flock() is not None, db()
    n, top = c.execute('SELECT count(*), max(i) FROM t').fetchone()
    put(c, N + 3); c.execute('COMMIT')
    ok = c.execute('PRAGMA integrity_check').fetchone()[0]; c.close()
    print('B2 %s: flock %s, rows %d of %d acknowledged (max %d), write ok, integrity %s'
          % (who, 'free' if free else 'STILL HELD', n, N, top, ok))
    good = free and n == N and top == N and ok == 'ok'
    if good:
        for f in os.listdir(d): os.remove(os.path.join(d, f))
        os.rmdir(d)
    print('B2', 'HOLDS, nothing left behind' if good else 'FAILS, files kept in ' + d)
EOF
python3 "$D/check.py" a &           # host A; wait for its line
python3 "$D/check.py" b1            # host B
kill -9 %1                          # host A
python3 "$D/check.py" b2            # host B
```

Output that means the assumption holds. This is the real output of the local dry run (two processes
on one host, the project's interpreter, default mode), with the host name replaced:

```
A  <host> sqlite 3.53.1 delete: acknowledged 200, holding write lock and flock
B1 <host> sqlite 3.53.1 delete: flock held elsewhere, write refused (database is locked)
B1 HOLDS
B2 <host> sqlite 3.53.1 delete: flock free, rows 200 of 200 acknowledged (max 200), write ok, integrity ok
B2 HOLDS, nothing left behind
```

The `wal` run printed the same five lines with `wal` for `delete`. What a different line means:

| Line | Meaning |
|---|---|
| A stops on `flock not granted`, or on an `OperationalError` | The filesystem refuses the lock or the journal outright. Nothing is assumed safely there |
| B1 `flock FREE` | `flock` is not seen across hosts. The state-directory lock guards one host only, as its docstring says; cross-host exclusion rests on the lifecycle row alone |
| B1 `write ACCEPTED` | SQLite's write lock is not seen across hosts. Two hosts at once can corrupt the database; nothing but the workflow keeps them apart |
| B1 `write refused (…)` with another message | The lock call itself fails on this filesystem. Record the message |
| B2 `flock STILL HELD`, or it stops on `database is locked` | A killed process's locks outlive it. A mount after a kill cannot open the state |
| B2 `rows` below 200, `max` not 200, or `integrity` not `ok` | A commit acknowledged on host A is not what host B reads. The database cannot stay on this filesystem while mounted |

Single-host variant: all four commands in one shell on one node, in a directory on the same
filesystem. It shows the locks are granted, a killed process's locks are released, and acknowledged
rows survive. It does not show that another host sees any of it.

## Recommendation, and the alternative turned down

**Assume a rollback journal, `journal_mode=DELETE` with `synchronous=FULL`, and assume of the
filesystem two things: POSIX advisory locks on the database file that every host sees and that die
with their process, and an `fsync` that reaches the server.** Treat `flock` across hosts as wanted
but not assumed.

- It is the one mode SQLite names as mitigating a network filesystem (documented, `useovernet.html`),
  and it asks for locks on one file and no shared memory (documented, `wal.html` § 7 and § 8).
- It keeps a connection per thread, and a reader does not wait on an open write transaction (measured).
- `FULL`, because with `NORMAL` a commit is not synced. Whether an unsynced write by a killed process
  on one host is visible at once to another host is a property of the filesystem (unverified); a
  released node is closer to SQLite's "system crash" than to an application crash.
- Cost: 13 times the median of today's mode on the laptop, still under 1 ms (measured); on a network
  filesystem, unknown (unverified). A commit waits on open reads (measured), and § 2 and § 10 of
  `AGENTS.md` and `db.py:94-95` change, so a seed must own it (R6).

**Turned down: keep WAL with normal locking.** It costs nothing to build and passed every local check
(measured), and the documented reason against it is several hosts at once. But SQLite states flatly
that it "does not work over a network filesystem" (documented), it adds a mapped and locked `-shm` to
what the filesystem must get right, and what happens when that fails there is not documented as a
refusal (unverified). If the maintainer's `wal` run holds on his filesystem, keeping WAL becomes a
defensible second decision; that is his call, on evidence this cycle does not have.

**Turned down: WAL with `locking_mode=EXCLUSIVE`.** Documented for a sole process, but a second
connection in the same process is refused (measured), so it forces the engine to one shared connection.

**If the check fails.** Locks unseen across hosts (B1): the design must not lean on them; the lifecycle
row and the workflow's one host at a time are the only guard, and the document says so. SQLite's
dot-file locking is a documented substitute for missing POSIX locks (`howtocorrupt.html` § 2.4), untested
here. Rows lost, integrity failed, or locks surviving a kill (B2): the database is kept node-local
while mounted and written to shared storage at a soft unmount, the alternative turned down on
2026-11-11, and a kill then loses the node's database, so R3 (e) must recover from the durable tier's
own files (R5 h).

## What stays unverified

- Everything on a network filesystem: locks across hosts, release of a dead process's locks, `fsync`
  reaching the server, `-shm` mapping, and the cost of a commit.
- The check uses 200 rows and one kill. It can show the assumption fails; passing once does not show
  the locks are right under every timing.
- The kill here is of a process. A node that loses power or is fenced was not simulated.
- Which engine reads hold a cursor open long enough to delay a commit in rollback mode.
