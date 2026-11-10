# Research 01 — How Hypothesis's fixed CI draw works (Hypothesis 6.155.7)

Version: `uv.lock:276-277` pins `hypothesis 6.155.7`. Source read from the prototype worktree's venv
(`.venv/lib/python3.15/site-packages/hypothesis/`, dist-info `hypothesis-6.155.7`). Paths below are relative
to that `hypothesis/` package. Read-only; nothing was run.

## 1. What the derandomized seed is, and what else moves the draw

**Seed.** `core.py:708-716`: with `settings.derandomize` the engine's `Random` is
`Random(int_from_bytes(function_digest(test)))`, where `test` is `wrapped_test.hypothesis.inner_test`
(`core.py:2040`, `2052`), i.e. Hypothesis's own `run_state_machine` closure (`stateful.py:118-236`).
`function_digest` (`internal/reflection.py:83-116`) is sha384 over (a) the cleaned source of that function,
(b) `repr` of its signature, and (c) `_hypothesis_internal_add_digest`, which `stateful.py:238-240` sets to
`function_digest(OverlayMachine)`: sha384 over the cleaned class source plus `repr(signature(OverlayMachine))`.
`_clean_source` (`reflection.py:56-79`) drops lines before the class line (class decorators), drops `COMMENT`
tokens, blank lines, and trailing whitespace. Everything else counts: docstrings, the `@rule(...)` decorator text
on methods, string literals, indentation and spacing inside a line.

**The GOAL's claim is right but incomplete.** "A digest of the `OverlayMachine` class source, docstrings
included and comments excluded" is the class half of the seed. The other half is Hypothesis's `stateful.py`
source, so a Hypothesis bump that touches `run_state_machine` also redraws. More important, the seed is not the
only input to the draw. The choice sequence also depends on:

- **The rule list and its order.** `RuleStrategy._setup_for` sorts rules by `(sorted(targets), len(arguments),
  name)` (`stateful.py:1132-1140`) and samples an index into that list. Adding rules changes every index.
- **Module-level strategies** (`FILE_PATHS`, `DATA`, `ADVANCE`, ...). They sit outside the class, so editing
  them leaves the seed alone but changes what each choice draws.
- **Settings.** `max_examples` sets `small_example_cap = min(max_examples // 10, 50)` and `optimise_at`
  (`internal/conjecture/engine.py:1258-1259`); `stateful_step_count` is the step cap (`stateful.py:140`).
- **The local-constants pool**, the one that bears on the plugin (below).

**Local constants.** With probability 0.05, `HypothesisProvider.draw_integer` and `draw_bytes` return a constant
in place of a fresh draw (`internal/conjecture/providers.py:769-815`, `831-851`, `1064-1077`). The pool is
gathered from every module in `sys.modules` whose file counts as local: not stdlib, not site-packages, not
Hypothesis, and with no `test`/`tests` path part and no `test_*`/`*_test` stem (`internal/constants_ast.py:249-273`;
re-scanned whenever `len(sys.modules)` changes, `providers.py:325-380`). The scan keeps ints with |v| ≥ 100,
bytes of 1–50 bytes, and strings of 1–20 characters (`constants_ast.py:111-142`). Only a constant the draw's
bounds permit can be picked. In this model that leaves two channels:

- The swarm draw `draw_integer(0, 254)` (`strategies/_internal/featureflags.py:65`) can take ints 100–254.
- `DATA = st.binary(max_size=256)` can take 1–50-byte constants.

Rule indices, `OFFSETS` (0–64) and `ADVANCE` indices cannot take any constant (all are below 100). When the pool
for a key is non-empty, its size changes both the value picked and how much randomness `random.choice` consumes,
so the whole example from that point on diverges.

Concretely, `src/sosfs` holds `100` (`state/catalog.py`), `128` (`engine/evict.py`), `b'\x00'`, `b'\x01'`
(`fuse/mount.py`), and `b'0'`, `b'1'` (`fuse/adapter.py`). This has three consequences.

- **The "fixed CI draw" is one draw per invocation shape, not one per class source.**
  - A standalone `pytest tests/engine/test_model.py` loads `catalog` but not `evict`, `mount` or `adapter`.
  - The full `-m unit` suite collects `test_evict.py` (which imports `evict`) and `tests/fuse/test_mount.py:26`
    (which imports `mount` at module level), and on Linux, where libfuse loads, `adapter` too
    (`tests/fuse/test_adapter.py:24-25`). That holds in every xdist worker, because each worker collects the
    whole suite.

  So test_model gets different draws standalone, inside `verify` on macOS, and inside the Linux CI jobs. The plan
  should say which draw each claim (R1, R3, R4, R6, R8) is measured on, and keep the gate's invocation the same
  for the base and the final tree.
- **The plugin and monkeypatching.** Neither changes the seed, and a transparent wrapper changes no choice: the
  rules have no preconditions and no bundles, so rule choice never reads engine state. But `route_census.py` is
  itself a *local* module. Any int literal in 100–254 or bytes literal of 1–50 bytes added to it would join the
  pool and redraw. So would any local module it imports that the run would not otherwise load: importing
  `sosfs.engine.evict` would add `128`. Today it is clean: only small ints and strings, and it imports
  `sosfs.engine.overlay`, which test_model already loads. The gate could assert this (for example, compare
  `providers._get_local_constants()` filtered to those ranges, with and without the plugin).
- **Mutants planted in `src/`** redraw only if they add or remove such a literal.

**Module-level strategy or helper: does it redraw?**

- A *new* module-level strategy or helper function: **no**. It is outside the class source, and `test_model.py`
  is excluded from the constants pool by its `tests/` part and its `test_` stem. The rules that use it are inside
  the class, so they do redraw.
- A helper *method* in the class: **yes**. It is part of the class source and so of the digest.
- *Editing* an existing module-level strategy: no seed change, but it changes what the draws produce.

## 2. How a step chooses its rule

Each step (`stateful.py:150-178`) first draws the stop boolean (`p = 2**-16`, forced at `stateful_step_count`, or
early once the choice buffer exceeds 0.8 × 8192), then `cd.draw(machine._rules_strategy)`. `RuleStrategy.do_draw`
(`stateful.py:1155-1165`):

- **Swarm testing.** Once per example, a shared `FeatureFlags` draws `p_disabled` uniform on {0..254}/255
  (`featureflags.py:65`). Each rule is then lazily disabled with that probability the first time it is examined
  (`featureflags.py:100-107`), and at least one rule is kept enabled.
- **The pick.** `sampled_from(rules).filter(enabled and valid)` (`strategies/_internal/strategies.py:751-806`)
  makes three rejection-sampling tries with `draw_integer(0, R-1)`, uniform for R ≤ 256 (`providers.py:81-85`,
  inner `UniformDistribution(half_width=256)`). It then falls back to a uniform choice among allowed indices.
  Net: uniform over the rules enabled in that example. There are no weights, and every rule is valid (no
  preconditions or bundles).

**Dilution.** By symmetry each rule's expected share of steps is about 1/R, so going from 22 rules to 25 scales
each existing rule's expected count by about 22/25 = 0.88 (2,000 → about 1,760). Two structural effects matter
more than that number:

- **Swarm co-occurrence.** A given rule is disabled in about half of all examples (E[p] ≈ 0.5). k specific rules
  are all enabled in an example with probability E[(1−p)^k] ≈ 1/(k+1). Reach that needs a write rule, a rename
  rule and a poll rule to co-occur is thinned by that factor. A composite that does the whole sequence inside one
  rule is not.
- **Index-0 bonus.** The engine first runs an all-"simplest" example (`engine.py:1174`). Then, while
  `valid ≤ small_example_cap` (50) and `calls ≤ 250`, it runs `prefix + simplest` zero-extensions
  (`engine.py:1258-1292`). "Simplest" means `p_disabled = 0` and rule index 0 with the simplest arguments for
  every remaining step. So the rule at sort index 0 gets on the order of 25+ examples × ~50 steps of extra
  firings. Today that is `commit_scanned`: of the four zero-argument rules it sorts first, and it is a no-op
  while `scanned` is empty. A new zero-argument rule whose name sorts before `commit_scanned` would take that
  slot.

  This is structural (it holds for every seed), but it is steered by naming. The plan should decide on it
  explicitly rather than inherit it by accident, given the GOAL's "no text edited to steer the draw".

## 3. `--hypothesis-seed=N` without CI

- `_hypothesis_pytestplugin.py:181-187` sets `core.global_force_seed = N`, so the engine runs `Random(N)`
  (`core.py:718-719`). Note that `derandomize` is checked *first* (`core.py:715`), so with `CI` set the seed is
  ignored.
- With a forced seed, `run_engine` passes `database_key=None` (`core.py:1396-1403`). The reuse phase returns
  immediately (`engine.py:1006`), nothing is saved, and no pareto front is kept (`engine.py:338`). **A
  pre-existing `.hypothesis/examples` therefore replays nothing.** Under CI the `ci` profile has `database=None`
  (`_settings.py:1211-1219`), so there is no replay either. Without a seed and without CI, the DB *is* replayed.
- **The run still writes `.hypothesis/`.** The constants cache is written to `./.hypothesis/constants/<sha1>`
  (`constants_ast.py:211-245`; home is `Path.cwd()/".hypothesis"` unless `HYPOTHESIS_STORAGE_DIRECTORY` is set,
  `configuration.py:20`, `57-72`). Its content is a pure function of the module source and is re-filtered on
  read, so it never changes a draw. But after seed 1 the tree is no longer free of `.hypothesis/`. R8's loop
  should delete it between seeds or point `HYPOTHESIS_STORAGE_DIRECTORY` at a temporary directory, and say
  which.

## 4. A profile loaded from a `-p` plugin

**Yes, it takes effect, provided it is loaded before `test_model.py` is imported.**

- **Unspecified settings are resolved when the settings object is built.** `settings.__init__` sets
  `_fallback = parent or settings.default` and copies every unspecified attribute from it at that moment
  (`_settings.py:694-752`). `settings.default` is the currently loaded profile (`_settings.py:540-548`,
  `1162-1169`).
- **The plugin loads first.** A `-p` plugin is imported during pytest's preparse, and its `pytest_configure`
  runs before collection. So a `register_profile(..., parent=get_profile('ci'), phases=[...])` plus
  `load_profile` in either place is the fallback when the module evaluates `settings(max_examples=1000, ...)`.
- **What comes from the profile:**
  - `derandomize` stays `True`: it is inherited from `ci`.
  - `database` comes out `None`.
  - `phases`, `print_blob`, `verbosity` and `report_multiple_bugs` come from the profile.
  - The four explicit values (`max_examples`, `stateful_step_count`, `deadline`, `suppress_health_check`) stay as
    the module sets them.
- **Caveats:**
  - `ci` is auto-loaded at `hypothesis` import only if `CI` is set (`_settings.py:393`, `409-413`, `1222-1223`).
    `get_profile('ci')` exists regardless, so a profile parented on it is derandomized even without `CI`. With
    it, `--hypothesis-seed` is then ignored.
  - Hypothesis's own `pytest_configure` loads `--hypothesis-profile`, `--hypothesis-verbosity` or
    `--hypothesis-explain` profiles (`_hypothesis_pytestplugin.py:153-179`). Passing those as well creates an
    ordering race.

**Dropping `shrink`/`explain`/`target` does not change a passing run's draw.**

- `optimise_targets` returns unless `target()` was observed (`engine.py:1510-1514`), and the model never calls
  it.
- Shrink and explain act only after a failure.
- On a failure without `shrink`, generation stops at once (`engine.py:1143-1145`) and reports the unshrunk
  example. That fits R6.

## 5. Why the number of runs is not `max_examples`

- **`max_examples` counts valid examples only.** The run exits when `valid_examples >= max_examples`
  (`engine.py:764-765`).
- **Every non-valid call also runs the test** (machine built, engine started, teardown run):
  - the all-simplest call;
  - *overruns*. These are mostly the early capped calls: `max_length = len(prefix) + 5 × minimal_extension`
    (`engine.py:1292`) for the first ≤ 250 calls, recorded as "reduced max size for early examples". A few come
    from the 8192-choice buffer;
  - invalid draws (rare here).
- **The zero-extension calls do count as valid**, and so do mutation calls (`engine.py:1352+`).
- **Cache hits and tree simulations do not call the test.**
- **The arithmetic.** So calls = 1000 + overruns + invalids. 2038 engines / 2 = 1019 calls means about 19
  non-valid calls. Each call starts two engines: one in `__init__` and the remount in `teardown`.
- **Early stops.** The run would stop short of 1000 valid examples only on:
  - `max_iterations`, when invalid + overrun > 458 + 100 × valid (`engine.py:158-167`, `766-769`);
  - tree exhaustion;
  - a failure (generation then continues ≤ 1000 calls past the first bug, or stops at once without shrink).

  None of these applies to a passing run.
- **Generation never reads the clock on a passing run.** The only time checks are after a bug, and `too_slow` is
  suppressed. So the count is deterministic for a given invocation shape.
