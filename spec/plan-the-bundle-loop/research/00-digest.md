# Research digest — plan-the-bundle-loop

Three briefs, each from one research child run in parallel on 2026-11-12. Evidence is **measured**
(a run in the brief), **documented** (a cited source) or **read**.

| Brief | Topic | Serves |
|---|---|---|
| [`01`](01-state-store-on-shared-storage.md) | SQLite's journal modes and locks on shared storage; a local check; the check for the maintainer | R3 (e), (f), R4 |
| [`02`](02-bundle-rebuild-prototype.md) | One bundle rebuilt from recorded state, against a plain-directory oracle | R8; R3 (a)–(d); R5 (b)–(e), (h) |
| [`03`](03-shared-read-only-tier-prototype.md) | Two read-only processes over one durable tier | R9; R5 (g) |

Both prototypes ran on the helper at `ae420d1` (in review, not yet on `main`), on a laptop's local
filesystem against a moto store, with requests counted on the wire by the helper's tap.

## Consolidated decisions

1. **A staged bundle lives inside the durable tier and is counted by its limits; `staging` goes.**
   Measured (02): the rebuild read only the database and that tier; the staged `.tar` is nearly all
   the tier holds (6,420,480 of 6,425,480 bytes at 300 members).
2. **A source catches up by a rebuild from recorded state and one object write.** Measured (02):
   trees equal in 18 of 18 runs; 1 read, then 1 write. Replay computes to 5 whole rewrites for 5
   changes. The staged bundle is never replaced in place afterwards: a rename over its name gave a
   wrong view after a kill; leaving it, or a new name switched in one transaction, did not.
3. **A rename is a change of recorded state.** Measured (02): 0 bytes and 151 rows for a directory
   of 150 members, against 2,956,800 bytes, 150 files and 452 rows for copy and remove.
4. **A removal is recorded in the database and in one record file per bundle on the durable tier.**
   Measured (02): with the database deleted, the tier's files and the record rebuilt the oracle; the
   tier's files alone did not. About 110 bytes a member per write.
5. **A mount after a kill recovers and mounts.** Measured (01): every acknowledged commit survives
   `SIGKILL` on a local filesystem. Read: the seeds. No recovery was prototyped.
6. **The design assumes a rollback journal with `synchronous = FULL`.** Documented (01): the one
   mode SQLite names as mitigating a network filesystem. Measured (01), local only: it keeps a
   connection per thread; a commit waits on open reads; 0.285 ms against 0.022 ms a transaction.
7. **The durable tier is the lowest shared tier, and over a bundle source there is one.** Read.
8. **Sharing a tier read-only needs:** a marker whose text is a function of the source; one flat
   index beside each staged bundle, renamed first; a staged name that carries the object's
   signature; fetch to a unique name and rename; an optional lock file that lapses. Measured (03):
   504 of 504 processes read the oracle's bytes; fetches 1 (sequential, or locked) or 2 (overlapping,
   unlocked).

## Contradictions between briefs, and the one recommendation for each

- **Where the member index lives.** 02 indexed members into the mount's database; 03 wrote the index
  once beside the staged bundle and kept no member rows in a mount's database. *Recommendation:* the
  index beside the bundle is the one copy that every mount reads (03); a writing mount's database
  holds what changes (names, removals, which altered file), which is what 02's rows amounted to
  after the pull. Whether a mount also caches offsets in its database is left to the owning seed.
- **How the staged bundle is named.** 02 used `<stem>.tar` and, for a refresh, `<stem>.g2.tar`; 03
  used `<stem>.<signature>.tar`. *Recommendation:* 03's. It is 02's "new name, never in place" rule
  with a name that also says which source object the bundle is; after a write-back the new name
  carries the signature the store returned for the new object.
- **Altered files by name or by id.** 02's measurements favour by id (no kill window on a directory
  rename); the namespace seed's note of 2026-11-11 favours by name (a durable set a person can
  read). 02 also showed the record file makes either layout recoverable without the database.
  *Recommendation:* not decided in this cycle; it is the namespace seed's decision 4, and the root
  `PLAN.md` records the measurement as its input.
- **Who may evict from the durable tier.** 02 has the tier's limits count the staged bundle and
  pressure drop or rebuild it; 03 has no mount evict from a shared tier. *Recommendation:* both, by
  the tier's kind: a tier owned by one overlay is counted and reclaimed by it; a tier marked shared
  for reading is reclaimed only by an explicit command.
- **Locks.** 01 assumes SQLite's POSIX locks on the state directory's filesystem; 03 uses no `flock`
  or `fcntl` lock on the shared tier, only an exclusive create and a rename. No conflict: they are
  two filesystems with two sets of assumptions, and the root `PLAN.md` states them apart.

## Questions the research raised about how an R-ID reads

None stops the plan; each is carried by a reading recorded in the cycle plan § 3.

- R4: whether "the locks the design assumes" include a `flock` seen across hosts. Carried: no.
- R3 (f) and R6: the assumed mode contradicts `state/db.py` and no seed owns the change. Carried: P4
  writes the seed.
- R3 (d): when the record file is written was not measured. Carried: stated as argued.
- R5 (g): how a mount learns a bundle's signature. Carried: stated as an open choice of the seed.
- R9: the fetch count is 1 or 2 by scenario. Carried: reported per scenario.

## Not covered by any brief

A network filesystem, FUSE, more than two mounts, a streamed or multipart upload, a source changed
by another writer between pull and write-back, SFTP signatures, whiteouts (argued only), and a
recovery walk after a kill.
