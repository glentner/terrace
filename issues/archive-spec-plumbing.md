---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Make archive tiers reachable from a spec file

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs are negotiated with a human.

## Problem

`src/terrace/tier/archive.py` is 678 lines implementing the entire `Tier` protocol over ZIP, TAR and
tar.gz in both of the operating modes `PLAN.md` describes, and it is reachable from nothing.
`uv run pytest -m unit tests/tier/test_archive.py` reports 36 passed in 0.85 s;
`tests/tier/test_spec.py:313-356` adds six more over `LocationSpec`. Every one of those tests
constructs the adapter by hand. No overlay spec can name it.

Three gates stand in the way, in the order a spec file hits them.

`TIER_KEYS` (`src/terrace/tier/spec.py:28`) is `{name, type, path, url, s3, role, scope, quota,
writeback}` — no `location`, no `staging`, no `sync`, no `sharded`. `_reject_unknown` runs at
`spec.py:204`, before the type is examined, so the first archive-shaped key kills the parse.
`SUPPORTED_TYPES` (`spec.py:25`) is `{'posix', 's3'}` while `TIER_TYPES` (`spec.py:24`) admits
`zip`, `tar` and `tar.gz`; `spec.py:209-210` raises "not yet supported" for the three it names but
does not accept. And `OverlayEngine.start` (`src/terrace/engine/overlay.py:109-126` (holds at `5e0d997`)) has exactly two
construction branches, `if tier_spec.type == 's3'` and an `else` that builds a `PosixTier` rooted at
`tier_spec.path` — an archive spec that got past the parser would be constructed as a POSIX tier
rooted at the archive *file*.

Reproduced against the direct-update ZIP example at `PLAN.md:424-429`, copied verbatim into the
`[overlay]`-plus-`[[tier]]` shape the parser requires:

```
$ uv run python -c "from terrace.tier.spec import OverlaySpec; OverlaySpec.load('/tmp/arc-spec.toml')"
SpecError: Unknown key(s) in tier 'archive': location
```

Delete `location` and give the tier a plain `path` instead, and the next gate fires:

```
SpecError: Tier type 'zip' is not yet supported (tier 'archive')
```

All three archive examples in `PLAN.md:421-447` — direct-update ZIP, staged tar.gz on S3, and the
sharded source — fail one of these two ways. Meanwhile `LocationSpec` (`spec.py:150-180`) parses
exactly the `location` table those examples use, correctly, including the S3 url-versus-path
crossing rules, and `TierSpec.from_data` never calls it. `inner_location()` (`archive.py:162-166`)
selects the `InnerLocation` a `LocationSpec` names and has no caller under `src/` outside its own
module. Both are dead except from tests.

The consequence for a reader is worse than the missing capability. Three Phase 9 checkboxes were
true at the unit level and misleading at the product level: the adapter shipped, the location
selector shipped, the index cache shipped, and the feature does not exist. A shipped-but-unreachable
adapter reads as done, and `PLAN.md` documents it as a configurable v1 tier type.

Widening the gate raises validation questions that the current stack rules do not answer, and each
one has a cost paid at the wrong time.

**An archive tier has nowhere to put its managed-location marker.** The engine registers every tier
and refuses to mount on any `MarkerStatus` but `OK` (`overlay.py:127-128` (holds at `141aa86`)). `TierRegistry.attach`
(`state/registry.py:106-110`) writes `.terrace/marker` under a POSIX root; `attach_tier`
(`registry.py:112-117`) delegates to `tier.read_marker` / `tier.write_marker`, which only `S3Tier`
implements (`s3.py:286`, `:294`). `ArchiveTier` implements neither, and it cannot host the marker
inside itself: `_guard` (`archive.py:271-274`) raises `PermissionError` for any path under
`.terrace`. An unmarked managed location is what the audit reads as tier evaporation.

**An archive source with no POSIX staging tier is caught at first write, not at parse.**
`eligible_tier` falls through to `overlay.py:200-201` (holds at `141aa86`) and raises `EngineError: No
passthrough-capable tier to stage a N-byte file from a zip source`. The mount comes up clean and
then every `open` fails. `_validate_stack` (`spec.py:304-324`) enforces every other stack rule
`PLAN.md` states — one source, source first, source not node-scoped, non-overlapping roots, state
directory outside the stack — and says nothing about this one. Staged mode has the same problem one
layer down: `ArchiveTier.__init__` raises `ValueError('A tar.gz (staged re-sync) archive requires a
staging working path')` (`archive.py:239-241`) at construction rather than at parse.

**Nothing describes a node-scoped archive.** `_validate_stack:315-316` refuses a node-scoped
*source*; an archive at a stage position with `scope = "node"` would be admitted, and neither
`PLAN.md` nor the engine says what it means.

**The tier-root overlap check is symlink-blind.** `spec.py:317-321` compares `PurePath` values built
from `os.path.abspath`, which normalizes `..` but does not resolve symlinks, so a staging root that
is a symlink into the source passes validation — and eviction of a copy on that "stage" then deletes
source data. Archives widen the exposure: an archive tier's overlap key is the archive file's path,
and the staged mode's `staging` working `.tar` is a second on-disk path no overlap rule sees at all.

## Why it was deferred

Found by the invariant audit run while porting the software factory (2026-08-22). That pass was a
harness change and not the place to add a tier type to the product.

This is **unbuilt work, not a live defect**. Nothing regresses today, because nothing can reach the
adapter. What is wrong today is the documentation: `PLAN.md` describes a capability with three
worked configuration examples, none of which load. It is listed in
[`AGENTS.md`](../AGENTS.md) § *Invariants* § *Known open violations*, row
§ 4/12 — "`ArchiveTier` is unreachable from a spec file".

## Outcome / vision

The three archive examples in `PLAN.md` load verbatim. `OverlaySpec.source.type` reads `zip`, `tar`
or `tar.gz`; the engine constructs an `ArchiveTier` over the `InnerLocation` the spec names;
the tier is registered and marked like every other managed location; and the stack combinations the
engine cannot serve are refused at parse time, with a message naming what is missing, rather than
surfacing as an `EngineError` at the first `open` of a mount that already came up.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a spec declares `type = "zip"` with `location = { type = "posix", path = ... }`, the
  spec parser SHALL load it without `SpecError`, and `OverlaySpec.source.type` SHALL read `"zip"`.
- **R2** — WHEN a spec declares `type = "tar.gz"` with an S3 `location`, a `staging` path and
  `sync = { max_frequency = "4/day" }`, the spec parser SHALL load it and `OverlaySpec.source` SHALL
  carry the parsed `LocationSpec`, the staging path and the parsed frequency. Each archive example in
  `PLAN.md` SHALL load verbatim.
- **R3** — WHEN an overlay with an archive source starts, the overlay engine SHALL construct an
  `ArchiveTier` over the inner location the spec names, and `engine.source` SHALL NOT be a
  `PosixTier`.
- **R4** — WHEN an archive tier is attached for the first time, the tier registry SHALL record a
  managed-location marker for it and report `MarkerStatus.OK`; a second start over the same state
  directory SHALL also report `OK`, not `MISSING`.
- **R5** — IF a spec names an archive source with no POSIX staging tier in the stack, THEN the spec
  parser SHALL refuse it at load, naming the missing staging tier; no spec that loaded SHALL be able
  to reach the `EngineError` at `overlay.py:200-201` (holds at `141aa86`).
- **R6** — IF a spec names a staged archive type without a `staging` path, THEN the spec parser
  SHALL refuse it at load rather than deferring to `ValueError` from `ArchiveTier.__init__`.
- **R7** — WHEN two tier roots overlap after symlink resolution — including a `staging` working
  `.tar` that resolves inside another tier's root — the spec parser SHALL refuse the stack, naming
  both tiers.
- **R8** — WHEN a mount is served over a ZIP source, a `readdir` of the mount root SHALL list the
  archive's top-level members. Observable only with a real mount on Linux with `/dev/fuse`; CI runs
  no integration job, so this is taken on trust anywhere else.

## Notes

- First of four archive cycles, and the prerequisite for the rest.
  [`archive-journaling-and-locking.md`](archive-journaling-and-locking.md) and
  [`archive-finalization-integration.md`](archive-finalization-integration.md) both add callers to
  the code this makes reachable, and
  [`archive-tier-test-coverage.md`](archive-tier-test-coverage.md) cannot be written until all three
  land.
- `PLAN.md` → ArchiveTier (:125-147), the archive spec examples (:421-447), Tier protocol
  (:109-121), Managed locations and markers (:250-256).
  [`plan-md-reconciliation.md`](plan-md-reconciliation.md) holds only the `PLAN.md` inaccuracies no
  other cycle repairs by doing its own work; the archive ones belong in this cycle's own diff, which
  is what the same-commit rule requires.
- [`invariant-audit-gaps.md`](invariant-audit-gaps.md) carries the `tier.generation` finding, which
  R4 touches, and the missing-symlink-resolution question overlaps R7. `_validate_stack` is
  high-blast-radius; changing it twice is the worse outcome, so promotion should decide which cycle
  owns it.
- [`ci-integration-lane.md`](ci-integration-lane.md) is what makes R8 observable on any machine but
  the maintainer's.
- Open questions a shaping conversation has to settle:
  1. **Where does an archive tier's marker live?** Beside the archive (`some.zip.terrace-marker`)
     pollutes the source directory and needs an inner-location write path that is not `atomic_write`
     over the whole blob. Inside the staging directory works only for staged mode and leaves
     direct-update unmarked. A fourth `InnerLocation` method is protocol surface every future
     location type must implement, against a `Tier` protocol whose small size is a stated technology
     decision.
  2. **Is `sharded` parsed now and refused, or left out of `TIER_KEYS` entirely?** Parsing it means
     `PLAN.md`'s third example loads and the mount then refuses on an unimplemented feature; leaving
     it out means the example still fails at parse and
     [`sharded-archive-sources.md`](sharded-archive-sources.md) re-opens the parser later.
  3. **Are archive tiers legal at stage positions, or source-only in v1?** Stage-position archives
     have no described semantics and no test. Source-only is one rule in `_validate_stack` and it
     closes the node-scope question at the same time.
- 2026-11-11 (whole-tree review at `64faf27`): the read side (admit the archive types and keys,
  construct the tier, index, read) is the first step of the bundle loop
  ([`spec/local-bundle-loop/GOAL.md`](../spec/local-bundle-loop/GOAL.md)) and all a read-only mount needs
  ([`issues/read-only-mount.md`](read-only-mount.md)). Shaping may split read from write here.
- 2026-11-11 (the maintainer's user story, [`spec/local-bundle-loop/GOAL.md`](../spec/local-bundle-loop/GOAL.md)): in the
  story the bundles land on the shared scratch tier, beside the altered files. In `PLAN.md`'s examples
  `staging` is a path of its own on the source tier, which no overlap rule sees and no quota counts.
  Shaping should decide whether a bundle's working copy lives inside the first shared stage tier, managed
  and counted like anything else there, and whether `staging` is then a key at all.
- 2026-11-16 (`spec/plan-the-bundle-loop/`, signed by the maintainer 2026-11-15): the question in the
  note above is answered by Decision 1 of the root `PLAN.md` (§ *Decisions*; § *The tier model* (b) and
  (d); § *Configuration*). A staged bundle lives in the durable tier directly below the source, in the
  tier's root under a name no entry of the overlay can take, and the tier's limits count it as one file
  at its uncompressed size. The `staging` key is retired: a spec that carries it is refused with a
  message naming the tier that now holds the bundle. Decision 7 adds a stack rule: over a bundle source
  a stack has exactly one shared stage tier, directly below the source, and a spec that breaks it is
  refused; this seed's parser is where that refusal lives. What changes in the draft above:
  - **R2** loads a `tar.gz` source with an S3 `location` and `sync`, and no `staging`; a spec that
    carries `staging` is refused with the message Decision 1 names. The examples it must load are the
    root `PLAN.md`'s as redrawn (§ *Configuration*), which carry no `staging`.
  - **R6** becomes: a staged archive type over a stack with no shared stage tier directly below the
    source, or with more than one shared stage tier, is refused at load, naming the tier at fault.
  - **R7** loses its `staging` clause, since no working path outside a tier remains; the symlink-blind
    overlap check between tier roots stands.
  - Open question 1 (where an archive tier's marker lives) is unchanged for a direct-update archive;
    for a staged one the staged bundle sits inside the durable tier, which carries its own marker.
  The pressure rule that reclaims a staged bundle, which Decision 1 makes evictable, is
  `issues/eviction-one-hop.md`'s; the bundle record beside it is `issues/durable-tier-record.md`'s.
- 2026-11-16 (`spec/plan-the-bundle-loop/`, review cycle 1): the cycle that builds Decision 1 changes three more places that encode
  the staging path of its own, all named in the root `PLAN.md`'s Decision 1 row: the helper's `staging` line
  and the spec it writes (`tools/bundle_loop.py:201-204`, whose comment cites the example the redraw
  removed), the unit test that asserts the staged path lies in no tier root
  (`tests/loop/test_helper.py:92`, with `:76` reading the key back), and stage 8's assertion of the bundle at
  that path (`tests/loop/stages.py:402`, owned by `issues/offline-commit-command.md`). The stages' pinned
  refusal names the `staging` key (`tests/loop/stages.py:82`) and the adapter's tests build the staged form on
  a working path of its own (`tests/tier/test_archive.py:365`, `:371`, `:384`); both move with the parser.
- Found by: the invariant audit run during the software-factory port (2026-08-22) — the
  tier-invariants and remaining-work briefs.
