# Research digest — archive-spec-plumbing

Seven read-only briefs, fanned out 2026-11-28 on `62225d2`. Where two briefs disagree, this file
records the single recommendation the plan takes. Baseline unit lane on this tree: **842 passed, 9
xfailed** (`uv run pytest -n 4 -m unit`, 70 s).

| Brief | Topic |
|---|---|
| [01](01-parser-and-stack.md) | the parser, `_validate_stack`, R1–R5 |
| [02](02-engine-attach.md) | engine construction, registration with no marker, R6 |
| [03](03-staged-bundle.md) | the staged bundle in the durable tier, R7 |
| [04](04-read-only.md) | EROFS across the mutating engine API, R9 |
| [05](05-bundle-loop.md) | the loop's stages, helper and audit, R8, the stage-9 pin |
| [06](06-read-path.md) | the engine's read path over an archive source, R8 |
| [07](07-fuse-integration.md) | the FUSE mount test, R10 |

## Consolidated decisions

1. **Parser (01).** The keys `location`, `sync` and `sharded` join `TIER_KEYS` once, and the unknown-key
   check stays first. `staging` stays out of `TIER_KEYS` and is found by a scan of the raw tables (the
   adversarial review's L2; brief 01 had admitted it). `TierSpec` gains `location` and `max_frequency`, both defaulting to
   `None`. An archive tier's `path` is the location's file path, or the location's url for S3.
   Tier-local refusals sit in `TierSpec.from_data`, in this order: `sharded`, then R3 (with `role`
   read before the location), then the location rules, then `sync`. `staging` (R2) and the stack rule
   (R4) sit at stack level, with `staging` checked first. The `sharded = true` text is a contract,
   because stage 9 pins it exactly.
2. **R5 (01).** Comparison keys are `os.path.realpath` for POSIX roots, POSIX archive files and the
   state directory. S3 urls are never resolved: `realpath('s3://b/k')` returns a cwd path. Stored paths
   stay unresolved, and the message prefixes stay as they are.
3. **Registration (02).** **Option B**: one new registry method beside `_attach` registers the tier,
   reads `OK` by type and does no marker I/O. `_attach`, `attach` and `attach_tier` are untouched, and
   a fake `read_marker`/`write_marker` on the adapter is rejected. The location's identity and signature
   go in `tier.meta`, a column `schema.sql:42` already describes for this, and a mismatch on re-attach
   is refused. This is the archive's stand-in for a marker's swap detection. It is an open question at
   sign-off (PLAN § 5), designed so that it can be cut.
4. **When the bundle is pulled: 02 against 03.** 02 wants it lazy, with `ArchiveTier.stat(ROOT)`
   answering before `index()`. 03 wants an explicit, journaled, counted stage at every start.
   **Taken: 03.** The engine owns the journal and the counters (§ 1, § 9), and a pull made lazily
   inside the adapter cannot be bracketed or counted without a callback into the engine. The tree
   already pulls at a first start (the root stat). R7 needs the bundle in place and counted when
   members are first read, and it is. A pull that `index()` makes during a mount, after an
   out-of-band deletion, stays unbracketed: a residual, owned by `issues/eviction-one-hop.md`.
5. **Where the bundle lives (03, 06).** It lives at `<durable root>/.terrace/bundles/<source tier>.tar`.
   `.terrace` is the only root name no overlay entry can take. A root-level name could be taken by a
   member, and a promotion would then replace the bundle. A constant sits beside `PARTIAL_DIR`, and one
   `bundle_path()` function in `tier/archive.py` is shared by the engine and stage 8. The adapter's
   `staging` keyword becomes `bundle`. The populate temp stays a sibling of the bundle, inside
   `.terrace/bundles/`. `atomic_write` is unchanged, and the orphan gap is already recorded in
   `issues/invariant-audit-gaps.md`.
6. **Counting (03).** One `adjust_usage(durable, bytes=size, files=1)` runs in the same transaction as
   the pull's `confirm`, and only when the pull created the file. The bundle gets no residency row,
   because the evictor walks residency rows. `PosixTier.usage()` counts regular `*.tar` files directly
   in `.terrace/bundles/`, so that counter and oracle agree (§ 4). That is a high-blast-radius edit and
   an invariant text change, recorded as a deviation row.
7. **Read path (02, 06).** The read path works as it stands. One blocker: a directory with no mode
   (synthesized, or a ZIP entry with no unix bits) is cataloged `0o644`, so a promotion below it fails
   EACCES. **Fix in the adapter** (`_stat_of` reports `0o755` for a directory with no mode), not in
   `_adopt`, which is on the high-blast-radius list. 06 preferred `_adopt` because it also fixes S3. The
   S3 copy of the defect goes to a new seed, `issues/source-directory-mode.md`. Also: duplicate member
   names resolve last-wins in the scans, removing an `IntegrityError` escape. A tar hard link reads its
   target's bytes, and a sparse member reads through `tarfile`. Device, FIFO and character members stay
   out, under v1's "special files" exclusion. That last one is a deviation row.
8. **Read-only (04).** An engine `read_only` flag is derived from the source type. One guard statement
   sits at the top of `mkdir`, `create`, `unlink`, `rmdir`, `rename`, `write`, `truncate` and
   `_set_meta`, before any lock or lookup, and `open` refuses a write access mode or `O_TRUNC` before
   `_serve`. The refusal is `OSError(errno.EROFS, os.strerror(errno.EROFS), path)`. `fsync`, `release`,
   `commit_*`, `flush` and `evict_copy` stay unguarded, since they are inert with nothing admitted. A
   second read-only switch inside the adapter is rejected: it is a second authority for one rule. A
   `write` on an `O_RDONLY` handle marks the copy dirty before `pwrite` fails, so the `write` guard is
   load-bearing.
9. **Loop (05).** The helper drops `staging`. Stages 1 and 2 lose their markers. Their `STAGES` entries
   move to `spec/archive-spec-plumbing/GOAL.md`, because the audit checks each entry's file exists and
   `issues/archive-spec-plumbing.md` is deleted when the cycle lands. The stops are pinned as follows:
   - Stage 3: the eviction assertion.
   - Stages 4–8: the EROFS refusal inside `change()`, seed `issues/archive-journaling-and-locking.md`,
     the seed on which lifting R9 waits.
   - Stage 9: R2's text, seed `issues/sharded-archive-sources.md`.
   
   Stages 3, 7 and 8 stop their engine in a `finally`. Stage 8's bundle check reads `bundle_path()`.
10. **R10 (07).** The test lives in `tests/fuse/test_integration.py` and inherits its `pytestmark`.
    `start_mount` takes an optional `spec` and **fails, rather than skips**, when the engine's start
    raises on the worker thread; otherwise an R10 regression would read SKIPPED on a Linux host. A
    mount-free companion in `tests/fuse/test_adapter.py` runs on every host. A grader on Linux greps
    the `PASSED …::<name>` line.

## Contradictions resolved

- Lazy pull (02) against eager pull (03): eager, journaled and counted (decision 4).
- Mode fix in `_adopt` (06) against the adapter (02): the adapter, with S3 seeded (decision 7).
- Bundle temp through `.terrace/partial/` (03) against leaving `atomic_write` alone (02): leave it
  alone (decision 5). `atomic_write` is high-blast, the temp is inside the tier root and hidden, and
  the gap is already recorded.
- 03 recommends amending `usage()`; its alternative was a deviation owned by the audit. Taken: amend it
  (decision 6). The other way builds in a counter-oracle disagreement that § 4 forbids.
