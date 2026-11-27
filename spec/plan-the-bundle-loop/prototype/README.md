# Prototype record — plan-the-bundle-loop

Two throwaway prototypes, kept as a record of what the plan step ran (GOAL R8, R9). They are not a
deliverable and nothing of them belongs under `tools/`, `src/` or `tests/`. Each imports the local
loop's helper and its request-counting tap and nothing of the project; neither uses FUSE.

| File | What it is | Run | Ends with |
|---|---|---|---|
| `rebuild.py` | One bundle pulled, changed (one member changed, one created, one renamed, one removed, one directory renamed), rebuilt from recorded state and the durable tier, written back with one object write, and compared with a plain-directory oracle. Alternatives for a rename, for recording a removal and for the staged bundle after a write-back, and injected kills | `uv run python spec/plan-the-bundle-loop/prototype/rebuild.py` (about 10 s) | `RESULT trees_equal=yes|no runs=N …` |
| `shared.py` | Two processes, each a read-only mount with its own state database and lower directory, over one shared durable-tier directory and one bundle; each reads every member and is compared with the oracle; fetches of the bundle's object are counted on the tap. Sequential, overlapping, locked, killed and stale cases | `uv run python spec/plan-the-bundle-loop/prototype/shared.py` (about 2.5 min; `drive --runs N` for another count) | `RESULT oracle_bytes=yes|no processes=N` |

**Where they find the helper.** By default `tools/bundle_loop.py` and `tests/loop/tap.py` of this
repository, imported through the `tests.loop` package as the landed tests import them.
`PROTOTYPE_HELPER` names another checkout of the repository, which must hold `tools/`, `tests/` and
`tests/loop/` laid out the same way.
`PROTOTYPE_SCRATCH` names a directory for their temporary files; without it they use the default
temporary directory.

**What the plan step ran.** The helper was not on this branch, so both ran on a copy of it taken at
commit `ae420d1` of branch `test/local-bundle-loop` (`tools/bundle_loop.py` blob `e6d18b4`,
`tests/loop/tap.py` blob `e1c91b3`), on a laptop, local filesystem, Python 3.15.2. The results are in
`../research/02-bundle-rebuild-prototype.md` and `../research/03-shared-read-only-tier-prototype.md`.
After the copies here were made, each was run once more on the same helper copy: `rebuild.py` printed
`RESULT trees_equal=yes runs=18`, and `shared.py` printed `RESULT oracle_bytes=yes processes=504`.

**`RECORD.md`** is the build's run on the helper as landed (`a0c88f1`), set against the plan step's.

The copies differ from what the research briefs ran in four places: where the helper is found, where
temporary files go, the `RESULT` line and exit status, and `shared.py` with no argument running the
driver. The build changed one more, for the landed helper: the imports, which `RECORD.md` describes.
