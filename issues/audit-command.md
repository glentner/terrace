---
status: unshaped
kind: feature
appetite: big
lane: public
---

# `sosfs audit` — reconcile the state database against the tiers

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

The recovery half of a sacred invariant is unimplemented. `AGENTS.md` and `PLAN.md` § *State store*
both promise *intent → act → confirm* journaling for every managed-location mutation, and both halves
are needed for the promise to mean anything: the write side records intents, and the audit resolves
them. Only the write side exists.

`Journal.pending()` (`src/sosfs/state/journal.py:275`) carries the docstring "the audit's worklist".
`grep -rn 'pending(' src/` returns exactly one hit — the definition. Every other reference is a test
assertion that the list is empty. Intents written by a crashed process accumulate in the `intent`
table with nothing that reads them, and `Journal.confirm` and `Journal.rollback` are the same
`DELETE` (`journal.py:268-273`), so nothing in the row records how the act was meant to end. Resolving
one by hand means deciding, per row, whether the bytes moved.
- 2026-11-06: the `journal.py` line numbers above hold at `141aa86`; `deferred-rename-source-orphan`
  moved the code. On its tree `Journal.pending` is at `journal.py:306` and the shared `DELETE`
  (`_resolve`) at `:299-304`.

The visible consequence is worse than accumulation. `OverlayEngine.start()` calls
`lifecycle.begin_mount` at `src/sosfs/engine/overlay.py:108`; `begin_mount` raises `CrashDetected`
when `shutdown_state == 'mounted'` (`src/sosfs/state/lifecycle.py:100-102`), and `Mount.start`
(`src/sosfs/fuse/mount.py:213-215`) does not catch it. **After any SIGKILL, OOM kill or node loss the
overlay is permanently unmountable and unfinalizable.** There is no recovery path at all: `sosfs
mount` fails, and `sosfs commit` — the other way to reach the data — is itself a stub. The state
database is intact and authoritative, and no shipped command will act on it. `PLAN.md` § *Audit* says
the opposite: "`sosfs mount` runs the same code automatically whenever `shutdown_state != clean`".
- 2026-11-06: `overlay.py:108` above holds at `141aa86`; on the `deferred-rename-source-orphan` tree
  the `begin_mount` call is at `src/sosfs/engine/overlay.py:117`.

The behavior is pinned as intended by `tests/engine/test_namespace.py:46`, which fakes a death and
asserts the raise. It is also pinned in a way that shows exactly what is missing:
`tests/state/test_survival.py:108-116` kills a real child process with `SIGKILL`, reopens the
database, and then performs the audit's job by hand under the comment `# what the audit does: resolve
intents, then settle the flag` — `journal.rollback(intent)`, `lifecycle.end_mount('detached')`, and
`begin_mount` now succeeds. That five-line sequence is the entire recovery procedure available to an
operator today, and it lives in a test.

`sosfs audit` is `AuditApp(StubApp)` (`src/sosfs/__init__.py:167-171`) and raises
`RuntimeError('Not implemented: sosfs audit (see ROADMAP.md, Phase 9)')`. The phase number is stale
twice over — the operations work was Phase 10, and the flat phase checklist no longer exists.

Some of the substrate is already built and documented for this consumer. Markers and six
`MarkerStatus` members exist (`lifecycle.py:129-138`, `Marker.write` atomic at `:174-183`);
`TierRegistry.set_usage` (`registry.py:177`) is documented as "the audit's rebuild step";
`Journal.is_temp_name` (`journal.py:42`) recognizes the `.sosfs/partial/{intent}-{name}` convention;
`ExclusiveLock` (`db.py:157`) already writes the holder's `host:pid` into the lockfile (`db.py:192`)
so a refusal can name it.

What is missing is more than a command:

* **An offline engine construction path.** `OverlayEngine.start` unconditionally calls `begin_mount`,
  which bumps the generation and sets `shutdown_state = 'mounted'`. An audit that used it would
  overwrite the very flag it is diagnosing, and a crash *during* an offline audit would be
  indistinguishable from a crashed mount. The audit needs the tier attach and the catalog without the
  lifecycle transition, and `sosfs commit` and offline `sosfs status` need the same path.
* **A whole-tier residency iterator.** Step 2 needs every residency row for tier X. The closest thing
  in `Catalog` is `least_recent` (`catalog.py:249`), which is LRU-ordered, defaults to
  `state='clean'`, and takes `limit=100`. This cycle adds catalog surface.
  - 2026-11-06: `catalog.py:249` holds at `141aa86`; on the `deferred-rename-source-orphan` tree
    `least_recent` is at `src/sosfs/state/catalog.py:254`.
* **Checksum recording.** `--full` verifies clean copies against blake2b digests. `residency.checksum`
  exists (`schema.sql:95`), `Catalog.add_residency` accepts it (`catalog.py:195`), `spec.py:287`
  parses `checksum = true`, and **no caller ever passes a digest**. `--full` has nothing to verify
  against until something records one. See `issues/invariant-audit-gaps.md`.
  - 2026-11-06: `catalog.py:195` holds at `141aa86`; on the `deferred-rename-source-orphan` tree
    `add_residency` is at `src/sosfs/state/catalog.py:200`.
* **Orphan-versus-temp triage that covers every temp this system actually writes.**
  `is_temp_name` recognizes the journal convention and, by directory, `posix.py:135`'s
  `put-{uuid4().hex}-{name}`. It does not recognize `archive.py:101`'s
  `.{archive}.{hex}.tmp`, which is a *sibling of the archive file* rather than a member of
  `.sosfs/partial/`. An archive temp left by a crash is therefore quarantined as a true orphan — a
  reported managed-location violation for a file the system created itself. Also
  `issues/invariant-audit-gaps.md`.
* **The loss report, the quarantine mechanism, the exit codes, and the auto-audit wiring.** `MountApp`
  (`src/sosfs/__init__.py:81-101`) has neither `--no-audit` nor `--audit=full`.

## Why it was deferred

Not deferred in the usual sense: it was scheduled as Phase 10 and the phases ran out. It surfaces
here because the invariant audit run while porting the software factory measured the consequence and
found it larger than a queue position implies — this is the one **live operational defect** among the
queued cycles, and everything else on the list after it is unbuilt feature work. Fixing it inside a
harness port was out of the question: it is a new subsystem, not a patch.

Recorded in [`AGENTS.md`](../AGENTS.md) § *Invariants* § *Known open
violations*, the row reading "The **audit does not exist**: `journal.pending()` has no consumer,
`begin_mount` raises rather than recovering, and `sosfs audit` is a stub" against §§ 2 and 8.

## Outcome / vision

A crashed overlay is recoverable by one command, and by the same code the next mount runs on its own.
`sosfs audit spec.toml` takes the exclusive lock, walks the stack fastest-tier-first, repairs what it
can, reports what it could not, and exits 0 / 1 / 2 for consistent / repaired / losses. An operator
who lost a node reads a per-tier summary and a loss report naming each rolled-back file and the age
of the version it was rolled back to, rather than a `CrashDetected` traceback. `sosfs mount` on an
unclean overlay audits and then mounts. And "converged after a crash" acquires an operational
definition, which is what `issues/crash-fault-injection-suite.md` needs before it can assert
anything.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a state database is left with `shutdown_state = 'mounted'` and every tier root
  intact, `sosfs audit spec.toml` SHALL exit 1, print a per-tier verified/recovered/lost/orphaned
  summary, leave zero rows in `intent`, and leave `shutdown_state` at a value a subsequent
  `begin_mount` accepts. Gradeable mount-free: the state directory the crash harness in
  `tests/state/test_survival.py` produces is the fixture, driven under
  `tools/temp_site.sh`.
- **R2** — WHEN a staging tier root is wiped wholesale between mounts, the audit SHALL attribute the
  loss to `MarkerStatus.MISSING` on one line naming the tier, invalidate every residency row for that
  tier in a single transaction, and exit 2 — never one mystery loss line per file.
- **R3** — IF the only holder of a file's dirty bytes is missing, THEN the audit SHALL roll the entry
  back to its last committed upstream version and emit one loss-report line carrying the path and the
  age of the restored version, and SHALL exit 2.
- **R4** — WHEN an unrecorded file under a tier root matches a recognized transfer-temp convention,
  the audit SHALL discard it; WHEN it does not, the audit SHALL quarantine it and name it in the
  report. Recognizing the archive adapter's sibling `.tmp` is a dependency on
  `issues/invariant-audit-gaps.md`; without that, this criterion is knowingly incomplete for archive
  tiers.
- **R5** — WHEN `--dry-run` is passed, the audit SHALL perform no write to the state database and no
  mutation of any tier root, and SHALL still exit with the code it would otherwise have used.
- **R6** — WHEN `sosfs mount` starts an overlay whose `shutdown_state` is neither `clean` nor
  `detached`, it SHALL run the audit and then mount; `--no-audit` SHALL instead fail with
  `CrashDetected` naming the recorded host. The engine half is gradeable against tmpdir tiers; the
  end-to-end mount is Linux-only with `/dev/fuse` and CI runs no integration job, so name the pass
  count rather than the exit status.
- **R7** — WHEN the audit runs twice in succession against the same state, the second run SHALL exit
  0 and report zero repairs.
- **R8** — WHILE a mount daemon holds the state lock, `sosfs audit` SHALL exit non-zero with
  `DatabaseLocked` naming the holder as `host:pid`, and SHALL make no modification to the database.

## Notes

- **Blast radius.** This cycle is high-blast-radius as a whole rather than at one line: the audit is
  the only component licensed to delete residency rows, roll entries back to older versions, and move
  files out of a managed location on its own reading of the world. An audit that is wrong about what
  it found destroys data that was recoverable, and it runs precisely when the operator has no other
  option. A CONFIRMED review finding anywhere in it should force the human sign-off gate.
- Related seeds: `issues/invariant-audit-gaps.md` funds two of the audit's dependencies (checksum
  recording for `--full`; the archive temp-name convention for step 2's triage).
  `issues/offline-commit-command.md` is directly downstream — commit auto-audits after a crash and
  reuses the offline construction path this cycle introduces. `issues/control-channel-and-status.md`
  reuses that same path for offline `status`. `issues/crash-fault-injection-suite.md` is the proof
  obligation: its convergence assertion *is* audit + remount. `issues/archive-spec-plumbing.md` and
  `issues/archive-finalization-integration.md` change what a tier walk means once an archive tier can
  be constructed, so shaping should decide whether the audit covers archive sources in this cycle or
  in a follow-up. `issues/rename-to-terrace.md` moves `MARKER_DIR` and `PARTIAL_DIR`, which are the
  two on-disk conventions this command reads.
- `PLAN.md` § *Audit* is the specification and is unusually complete: the five-step walk (marker
  check, reconcile, resolve intents, rebuild, report), the `--fast` / `--full` modes, `--dry-run`, the
  exit codes, and the rule that the audit never moves data upstream because finalizing the source is
  `sosfs commit`'s job. `PLAN.md` § *Lifecycle and recovery* defines the `shutdown_state` three-valued
  flag the audit settles. Under the plan-drift rule, anything this cycle decides differently goes back
  into `PLAN.md` first.
- **CLI wiring.** Adding a real `audit` touches four places: the `Application` subclass, `APP_HELP`
  (`src/sosfs/__init__.py:191`), the `commands` registry (`:219`), and `tests/test_cli.py`. Missing
  one is silent in exactly one direction — a command in `commands` but absent from `APP_HELP` fails
  `tests/test_cli.py:31`, while a command added to `APP_HELP` and not to `commands` passes and fails
  only at invocation. `tests/test_cli.py:42` parametrizes the stub list `['status', 'flush', 'commit',
  'audit', 'config']`; this cycle removes `audit` from it in the same commit or the suite goes red on
  landing. It also deletes one instance of the stale "Phase 9" pointer in `StubApp.run`.
- Open questions for shaping, each with its real trade-off:
  - Does checksum recording land here or in `issues/offline-commit-command.md`? It is a write-path
    change to `TransferPool.copy` (`src/sosfs/engine/transfer.py:78-108`), the single place where the
    intent bracket wraps a real byte move, so it is high-blast-radius wherever it goes and inflates
    whichever big cycle adopts it. The third option is to ship `--full` in a later cycle and say so in
    the help text rather than shipping a flag that silently verifies nothing.
    - 2026-11-06: `transfer.py:78-108` holds at `141aa86`; on the `deferred-rename-source-orphan`
      tree the intent bracket in `TransferPool.copy` spans `src/sosfs/engine/transfer.py:85-118`.
  - Where do quarantined orphans go? A `.sosfs/quarantine` directory per tier keeps the bytes
    recoverable but consumes the tier's quota and adds a path the eviction scan must learn to ignore.
    A report-only listing is cheap and loses the file on the next sweep, which for a true orphan may
    be correct and for a misclassified file is not.
  - Does the audit refuse to run when a `scope = "node"` tier belongs to another host, or report it
    unreachable and continue? `PLAN.md` § *Deferred commit* implies the latter, with `--forfeit` at
    commit time for the rollback. Refusing is safer and makes the command useless from a login node,
    which is where an operator will run it.
  - Does anything the audit needs force a `SCHEMA_VERSION` bump (`db.py:34`, currently 2)? There is
    no migration machinery — `db.py:86` and `:105` refuse a mismatch outright — so a bump makes every
    existing state database unopenable. A new residency state for "verified this pass" costs exactly
    that.
  - The `ON DELETE CASCADE` from `entry` eats *unresolved* intent rows (`schema.sql:126`). The engine
    guards this at `overlay.py:949` by confirming before any purge; the audit does the same class of
    work and needs the same ordering, which is not expressible in the schema.
    - 2026-11-06: `overlay.py:949` holds at `141aa86`; on the `deferred-rename-source-orphan` tree
      the confirm before the purge is at `src/sosfs/engine/overlay.py:1038`.
    - 2026-11-08: `source-path-truncate-and-stat` moved it again; at `6425a21` it is at
      `src/sosfs/engine/overlay.py:1061` (holds at `5e0d997`).
- 2026-11-11 (whole-tree review at `64faf27`): the mount-time half is on the deployment path ahead of
  everything else here. A batch job killed at its time limit is the ordinary ending, and today that
  leaves an overlay no later job can mount. [`issues/read-only-mount.md`](read-only-mount.md) R4 is the
  smallest slice of the walk (markers, intents, a size check, counters) and needs no loss report, because
  a read-only overlay has nothing to lose. Shaping should consider building that slice first and the
  reconcile, quarantine, loss report and exit codes on top of it.
- 2026-11-11 (the maintainer's ruling): everything lives in the one database, which stays the authority
  for every tier. The walk specified here stands. Its most common caller is now known: a mount on another
  node after a soft unmount, which today is refused
  ([`issues/shutdown-rescue-gaps.md`](shutdown-rescue-gaps.md)).
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the state-store
  invariants topic (the intent journal has no consumer) and the remaining-work topic (dossier 4).
- *Added 2026-11-06 by `deferred-rename-source-orphan` (review cycle 1):* **catalog and residency
  sizes already wrong.** Until that cycle, `getattr`'s live stat and `_record` read a file served off
  the source at its logical path while its rename, or an ancestor's, was queued, found nothing, and
  kept the old size: the seed's probe recorded 1000 bytes in `entry.size` and the source residency's
  `size` for a 4-byte object, for good. The engine no longer records new wrong sizes there, but state
  databases written before it can still hold them, and the quota counters eviction trusts are built
  from residency sizes. The reconcile step is their natural owner: compare each residency's size with
  the tier's object (on the source, at `entry.source_path`) and correct the entry, the residency and
  the counter delta together. The cycle's GOAL sent these sizes here and to the
  `source-path-truncate-and-stat` seed, whose cycle (landed 2026-11-08,
  `spec/source-path-truncate-and-stat/`) handed them to this seed under its R4; the next note records
  what it found.
- *Added 2026-11-06 by `source-path-truncate-and-stat`, which hands these sizes here (its GOAL Q3):*
  **the already-wrong sizes, as a probe found them.** Only a copy held on the source could be
  mis-sized: the old `_record` and live stat looked at the logical path, and a staged tier holds its
  copy there. The fields are `entry.size` and the source residency's `size`, in state databases
  written before `fe26962`. Probed at `0e6d14c` with both set to 1000 for a 4-byte object:
  - the source residency's `size` heals on the next `release` of any handle on that copy, even a
    read-only one;
  - `entry.size` does not heal on a read-only open, an `fsync` with no write, `flush()`, a remount,
    or a rename plus `flush()`. Only a write through a handle, or a `truncate`, corrects it. Until
    then `getattr` reports the stale figure and placement reads it, so the 4-byte file stays served
    off the source where its true size would stage it;
  - the staged-tier quota counters are unaffected: `_record` never adjusts a source counter, and no
    staged copy was mis-sized. The note above overstates that risk. The reconcile step still corrects
    `entry.size` and the source residency, but has no counter delta to apply for these.
- *Added 2026-11-06, retiring `deferred-rename-source-orphan`:* **orphans already on a source.**
  Before that cycle, a data commit to the source during a queued rename left the pre-rename object
  behind, and the next mount adopted it as live data. The fix prevents new orphans; its GOAL and
  `PLAN.md` § Risks leave recovering the ones already on a source to this cycle. A mount adopts such
  an object as an ordinary entry (the GOAL's reproduction), so shaping decides whether the audit can
  tell one apart from user data at all, or records it as out of reach.
