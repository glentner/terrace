# GOAL — Drive the bundle loop end to end on a local object store

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** local-bundle-loop
- **kind:** test
- **appetite:** small

## Problem

The loop that sets the project apart has never run. In the maintainer's statement of 2026-11-11, what
makes this different from other virtual archive filesystems is write-back with delayed re-aggregation
into the compressed bundles at the source. End to end that is: bundles at an object-store source; a
bundle staged and indexed below it; members read through the overlay; members written, created,
renamed and removed; the bundle rebuilt on a delay and pushed back as one object; and a fresh engine
reading the result from the bucket. Around it sit two more behaviours of the same story: pressure in
a tier moves a copy one tier up and no further, and a job that must release its node commits what
changed to the durable tier, after which a second process elsewhere rebuilds the bundles that
changed. The seed quotes the story and the maintainer's clarifications in full.

Every piece exists alone and none can be joined. This is **pre-existing**: archives were built
bottom-up and the work stopped below the engine.

- `ArchiveTier` has 36 unit tests, some over an S3 inner location (`tests/tier/test_archive.py`), and
  the engine runs over an S3 prefix source in 8 (`tests/tier/test_s3_engine.py`), both against a
  moto server the suite starts itself (`tests/tier/conftest.py:35`).
- The spec parser refuses every archive type: `SUPPORTED_TYPES` is `posix` and `s3`
  (`src/terrace/tier/spec.py:25`, enforced at `:209`).
- The engine's tier construction has an S3 branch and a POSIX branch and no archive branch
  (`src/terrace/engine/overlay.py:127-147`).
- Nothing under `src/terrace/engine/` calls `ArchiveTier.resync` (`src/terrace/tier/archive.py:427`),
  the one call that pushes a bundle to its source.

So nobody can drive the loop: not the maintainer at a terminal, not an agent in a cycle, not CI.
Every design decision about it is made by reading, and the cycles since the factory was adopted show
the cost: they hardened a mechanism for a source type the loop does not use. The cycles queued behind
this one (`ROADMAP.md` § *Queued: the bundle loop, driven locally*) have no shared statement of what
"the loop works" means, and no way to see how far the tree gets through it.

A developer machine already has what a local run needs: the moto server is pure Python and needs no
container, and `docker-compose.yml` describes a MinIO service for the integration marker. The build
host has no container runtime, so MinIO does not run there.

## Outcome / vision

One command stands the loop up on a laptop: a local object store holding generated bundles, a spec
over it, and stage tiers in a temporary directory, all gone again when the command is told to stop.
One scenario says how far the tree gets through the loop. Its stages are ordered; the ones the tree
cannot pass are strict expected failures that each name the seed owning them, so the scenario goes
green stage by stage as those cycles land and the file is the loop's progress record. What the bucket
should hold is decided by an oracle that is a directory tree and `tarfile`, with none of the
project's code in it.

After this cycle the profile's `verify` reports no failure: the helper's own tests pass, and every
stage is an expected failure, the first of them at the spec parser's refusal of the archive type.

## Acceptance criteria (the contract)

- **R1** — WHEN the helper under `tools/` is started for N bundles, it SHALL start an S3-compatible
  object store on the local machine with no container runtime; create a bucket holding N generated
  `tar.gz` bundles of a few kilobytes each; write an overlay spec whose source is that bucket in the
  archive-tier form `PLAN.md` § *Configuration* documents (the single-archive form for one bundle,
  the sharded form for several) and whose stage tiers and state directory are POSIX directories under
  one temporary directory, one stage of shared scope standing for the durable tier and two of node
  scope below it; and print the spec's path on standard output. One implementation SHALL serve a
  person at a terminal and the scenario's tests, which build their bucket, spec and directories
  through it. Whether the unit-marked tests use the helper's own store or hand it the moto server the
  suite already starts is the plan's to settle.
- **R2** — WHEN teardown is requested, and on every exit path of the helper (a normal exit, an error,
  an interrupt and a termination signal), the helper SHALL stop the object store and remove the
  temporary directory. Afterwards the store's endpoint SHALL refuse a connection and the directory
  SHALL NOT exist.
- **R3** — THE scenario's oracle SHALL be a directory tree and the standard library's `tarfile`: the
  expected content of each bundle is a directory the test changes with ordinary file operations, and
  an object taken from the bucket is compared with it by unpacking the object with `tarfile`. The
  oracle SHALL import nothing from the `terrace` package.
- **R4** — THE scenario SHALL drive the engine API in process, as `tests/tier/test_s3_engine.py`
  does, over the spec the helper wrote, as these ordered stages, each a test:

  | # | Stage | What it asserts | Starting owner under `issues/` |
  |---|---|---|---|
  | 1 | reach | The spec loads through the spec parser and an engine starts over it. | `archive-spec-plumbing.md` |
  | 2 | read | A listing gives the oracle's names, and every member read matches the oracle's bytes. | `archive-spec-plumbing.md` |
  | 3 | pressure | With the fastest tier's limits set below the bytes read, every read succeeds and each copy pushed out is found one tier up. | `eviction-one-hop.md` |
  | 4 | change | Inside one bundle, one member is modified, one created, one renamed and one removed; the engine then lists and reads the oracle's tree. | `namespace-sync-redesign.md` |
  | 5 | re-aggregate | After the injected clock passes that bundle's re-aggregation interval and the engine polls, the bundle's object unpacks to the oracle's tree. | `archive-finalization-integration.md` |
  | 6 | read back | A second engine on fresh state over the same bucket lists and reads the same tree. | `archive-finalization-integration.md` |
  | 7 | release | After changes in the fastest tier the engine is told to detach; every change is then on the durable tier, and no copy is recorded on any node-scope tier. | `shutdown-rescue-gaps.md` |
  | 8 | offline commit | A second process, given the spec, the state directory and the durable tier, and nothing of the first process's node-scope tiers, runs the full commit; the bundle's object then unpacks to the oracle's tree, and the staged bundle is still on the durable tier. | `offline-commit-command.md` |
  | 9 | several bundles | Over a bucket of several bundles, after a change to one and its re-aggregation, every other bundle's object is byte-identical to before. | `sharded-archive-sources.md` |

  Stages 1 to 8 SHALL need one bundle, so none of them waits on the sharded form.
- **R5** — WHILE the tree cannot pass a stage, that stage SHALL be a strict expected failure whose
  reason names the path of the seed that owns it, so a stage that starts passing fails the run until
  its marker is removed. A stage the tree can pass SHALL be a plain passing test. Every seed a marker
  names SHALL exist under `issues/` on the cycle's base. On this cycle's tree stage 1's expected
  failure SHALL be the spec parser's refusal of the archive type. The owners in R4's table are the
  starting assignment: the plan confirms each against its seed and records a change of owner as a
  deviation.
- **R6** — THE scenario SHALL count the requests each stage makes to the source, by kind, and SHALL
  assert: in stage 5, exactly one write, of the changed bundle's one object; in stages 3 and 7, no
  write to the source; and in stage 8, that an engine started after the commit pulls no bundle from
  the source.
- **R7** — THE scenario SHALL also exist as an integration-marked variant that runs the same stages
  under the same markers against the endpoint the `TERRACE_TEST_MINIO_*` variables name, with the
  defaults and the skip when no endpoint answers that `tests/tier/test_s3_integration.py` has.
  `uv run pytest -m integration --collect-only` SHALL list it. The cycle's records SHALL state that
  the variant was not run. Any gate over it SHALL assert a pass count, never an exit code.
- **R8** — THE scenario's unit-marked tests SHALL add no more than about ten seconds to
  `uv run pytest -n auto -m unit` on the build host, where the lane takes about 35 seconds and
  reports 731 passed before this cycle. The cycle's records SHALL give the lane's time and its counts
  by outcome before and after.
- **R9** — `AGENTS.md` SHALL describe the helper and the scenario where it lists the commands and the
  repository map, and every measured figure the cycle changes there (test counts, file counts, lane
  time) SHALL move in the commit that changes it.

The seed's map also names `racing-write-marked-clean.md` and `archive-journaling-and-locking.md`,
between pressure and re-aggregation. Neither has a stage in the table: one needs a race and the other
a crash or a second writer, and the scenario is single-threaded. They stand as prerequisites of
stage 5 unless the plan finds a deterministic stage for one, which it may add under R5.

## Non-goals (no-gos)

- Any change under `src/`. This cycle is the helper, the scenario and the documents that describe
  them.
- Making a stage pass by changing the engine, or by weakening what the stage asserts.
- A real FUSE mount. It belongs to `issues/ci-integration-lane.md`.
- Running the MinIO variant in this cycle, or a CI job that runs it.
- Many bundles at scale. Stage 9 uses a handful of kilobyte bundles.
- An SFTP source (`issues/sftp-inner-location.md`).
- A container in the helper's path.
- Archive coverage at the adapter, engine and mount level once the code exists
  (`issues/archive-tier-test-coverage.md`). This scenario states the target the code is built toward.

## Clarifications

- **Q:** Which mount does the scenario drive? — **A:** The engine API only, in process, as
  `tests/tier/test_s3_engine.py` does, against the moto server the suite already starts. A real FUSE
  mount is a non-goal here and belongs to `issues/ci-integration-lane.md` (the foreman's ruling,
  2026-11-11).
- **Q:** What becomes of the seed's draft R5 (MinIO, and a real mount where a machine has one)? —
  **A:** It shrinks to R7. The scenario carries an integration-marked variant against MinIO that
  takes its endpoint from the environment as `tests/tier/test_s3_integration.py` does. It is written
  and collected, and the cycle's records say it was not run: the build host has no container
  runtime. Any integration gate asserts a pass count, never an exit code (the foreman's ruling,
  2026-11-11).
- **Q:** Is the helper a deliverable, and what does it do? — **A:** Yes, under `tools/`, usable by a
  person at a terminal and by the scenario test. It starts the local object store with no container,
  fills a bucket with N generated `tar.gz` bundles, writes an overlay spec over it with POSIX stage
  tiers in a temporary directory, prints the spec's path, and tears everything down on request and on
  every exit path. The spec uses the archive-tier form `PLAN.md` documents, which the parser refuses
  today; that refusal is the scenario's first expected failure, owned by
  `issues/archive-spec-plumbing.md`. The helper's name and interface are the plan's to design (the
  foreman's ruling, 2026-11-11).
- **Q:** How is the scenario structured while most of it cannot pass? — **A:** As ordered stages,
  each a test that is a strict expected failure naming the seed that owns it, until the tree can pass
  it. The stage-to-seed map in the seed's Notes is the starting point. The seed's R2, R2a (pressure
  moves copies one tier up with no write to the source) and R2b (detach, then a full commit from a
  second process that has only the spec, the state directory and the durable tier) all stay, as
  stages. A stage the tree can already pass is a plain passing test (the foreman's ruling,
  2026-11-11).
- **Q:** What is the oracle? — **A:** A directory tree and the standard library's `tarfile`, nothing
  of the project's own code (the foreman's ruling, 2026-11-11).
- **Q:** Does the seed's R4 stay? — **A:** Yes. The scenario counts requests to the source by kind
  per stage, and one re-aggregation of one dirty bundle costs one write of one object. How requests
  are counted is the plan's to design (the foreman's ruling, 2026-11-11).
- **Q:** What may the scenario cost the unit lane? — **A:** No more than about ten seconds on the
  build host, where the lane takes about 35 seconds today with 731 passed. Bundles are kilobytes (the
  foreman's ruling, 2026-11-11).
- **Q:** Who moves `AGENTS.md`'s measured figures? — **A:** This cycle, in the same commit that
  changes them, under the repository's same-commit rule (the foreman's ruling, 2026-11-11).
- **Q:** What is out of scope? — **A:** Any change under `src/`; making any stage pass by changing
  the engine; many bundles at scale; SFTP (the foreman's ruling, 2026-11-11).
- **Q:** R5 says stage 1 fails at the parser's refusal. For the documented archive form the parser
  stops at its unknown-key check, before the type check. Which refusal does the stage pin? — **A:**
  The refusal of the archive tier as the parser gives it for the documented form: the unknown-key
  message naming the archive keys. The type refusal already has its own test. (The foreman's ruling
  at the plan sign-off; resolved 2026-11-12.)
- **Q:** Does the re-aggregation stage also assert that nothing is pushed before the interval
  expires? — **A:** Yes. Stage 5 SHALL assert that no object is written to the source between the
  change and the interval's expiry, and one object after it. A write-back that repacks on every
  change is the behaviour the delay exists to prevent. (The foreman's ruling at the plan sign-off;
  resolved 2026-11-12.)

## Related materials

- Seed: [`issues/local-bundle-loop.md`](../../issues/local-bundle-loop.md), which quotes the
  maintainer's story and clarifications of 2026-11-11.
- Roadmap: `ROADMAP.md` § *Queued: the bundle loop, driven locally*.
- Stage owners, each confirmed present on the base: `issues/archive-spec-plumbing.md`,
  `issues/eviction-one-hop.md`, `issues/namespace-sync-redesign.md`,
  `issues/archive-finalization-integration.md`, `issues/shutdown-rescue-gaps.md`,
  `issues/offline-commit-command.md`, `issues/sharded-archive-sources.md`; and the two without a
  stage, `issues/racing-write-marked-clean.md` and `issues/archive-journaling-and-locking.md`. The
  owners of stages 7 and 8 are not in the seed's map; they come from those two seeds' own notes of
  2026-11-11 on the story.
- Related seeds: `issues/ci-integration-lane.md` (a real mount, and a machine that runs the
  integration marker), `issues/archive-tier-test-coverage.md` (coverage once the code exists),
  `issues/sftp-inner-location.md` (the same scenario over SFTP).
- Design: `PLAN.md` § *Configuration* (the archive-tier spec forms), § *Tier protocol* → ArchiveTier
  (staged re-sync), § *Testing strategy*.
- Profile: `AGENTS.md` § Factory (`verify`), § Verification, § Conventions (same-commit rule, strict
  markers, the integration gate's pass count), § Invariants § 7 and § 8.
- Sources: `tests/tier/conftest.py`, `tests/tier/test_s3_engine.py`,
  `tests/tier/test_s3_integration.py`, `tests/engine/conftest.py` (`Clock`), `docker-compose.yml`,
  `tools/`.
- Anchors were read at `bf438b4`, before the rename to Terrace landed, under the old package path
  `src/sosfs/` and the old `SOSFS_TEST_MINIO_*` variables. This document uses the new names; line
  numbers are those of `bf438b4`.
