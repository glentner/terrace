#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# The static gate: the lint CI runs, plus the structural properties no test asserts.
# Cheap enough (seconds, one network fetch) that a factory `verify:` command can depend
# on it, so it must stay green — a gate with a known failure is not a gate.
#
# Usage:
#   tools/lint.sh            # all checks
#   tools/lint.sh --no-net   # skip ruff if uvx would have to download it
#
# Every check runs before anything is reported, so one drive surfaces every problem
# rather than the first.
#
# Exit codes: 0 all checks passed · 1 a check failed · 3 a check could not be run
# (missing tool). 3 is distinct from 1 on purpose: "could not run" must never be
# mistaken for "passed".

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
repo=$(CDPATH='' cd -- "$here/.." && pwd -P)
cd "$repo"

no_net=''
[ "${1:-}" = "--no-net" ] && no_net=1

failed=0
note() { printf 'lint: %s\n' "$*"; }
fail() { printf 'lint: FAIL  %s\n' "$*"; failed=1; }

# ---- 1. ruff ---------------------------------------------------------------
#
# The exact selection .github/workflows/ci.yml runs, so a green gate here means a
# green `checks` job. Ruff writes a "Python 3.15 support is under development"
# warning to stderr; do not gate on empty stderr.

if command -v uvx >/dev/null 2>&1 && [ -z "$no_net" ]; then
    if uvx ruff check --select E9,F63,F7,F82 src tests; then
        note "OK    ruff E9,F63,F7,F82 over src tests"
    else
        fail "ruff reported errors"
    fi
elif [ -n "$no_net" ]; then
    note "SKIP  ruff (--no-net)"
else
    note "CANNOT RUN  ruff: uvx is not available"
    exit 3
fi

# ---- 2. import layering ----------------------------------------------------
#
# `fuse/` and the CLI call `engine/`; `engine/` calls `tier/` and `state/`; nothing
# below `engine/` imports above itself. Losing this costs the engine-first test
# discipline and the macOS CI job, and it degrades quietly — the first upward import
# compiles fine on a developer machine that has libfuse.
#
# `^[[:space:]]*` is required, not decoration: the imports most likely to violate the
# rule are the function-local deferred ones, which are indented (tier/archive.py,
# engine/overlay.py both defer obstore that way).
#
# `tier/` and `state/` are mutual peers, not a stack — state/registry.py imports
# tier/protocol and tier/posix.py imports state/journal, both by design. A naive
# core < state < tier < engine rank check fails on day one; this one permits the cycle.

below=$(grep -rEn '^[[:space:]]*(from|import)[[:space:]]+sosfs\.(engine|fuse|control)' \
        src/sosfs/core src/sosfs/state src/sosfs/tier --include='*.py' || true)
[ -z "$below" ] || fail "a below-engine module imports upward:
$below"

up=$(grep -rEn '^[[:space:]]*(from|import)[[:space:]]+sosfs\.(fuse|control)' \
     src/sosfs/engine --include='*.py' || true)
[ -z "$up" ] || fail "engine imports the FUSE/control layer:
$up"

core=$(grep -rEn '^[[:space:]]*(from|import)[[:space:]]+sosfs\.(state|tier|engine|fuse|control)' \
       src/sosfs/core --include='*.py' || true)
[ -z "$core" ] || fail "core imports a higher layer:
$core"

[ -n "$below$up$core" ] || note "OK    import layering (nothing below engine/ imports above itself)"

# ---- 3. SPDX headers -------------------------------------------------------
#
# `head -2` asserts the pair is the first two lines, which is the house template. A
# header buried under a docstring passes a whole-file grep and fails this one, which
# is the point.

missing=''
for f in $(find src tests -name '*.py' ! -path '*/__pycache__/*' | sort); do
    head -2 "$f" | grep -q '^# SPDX-FileCopyrightText: ' || missing="$missing $f"
    head -2 "$f" | grep -q '^# SPDX-License-Identifier: Apache-2.0$' || missing="$missing $f"
done
if [ -z "$missing" ]; then
    n_py=$(find src tests -name '*.py' ! -path '*/__pycache__/*' | wc -l | tr -d ' ')
    note "OK    SPDX header pair on all $n_py .py files under src/ and tests/"
else
    fail "missing or misplaced SPDX header:$missing"
fi

# ---- 4. version single-source ----------------------------------------------
#
# pyproject.toml is the only source; sosfs.__version__ reads it through
# importlib.metadata. Report it so a release drive can assert against it, and catch
# the line going missing under a refactor. A literal __version__ assignment in src/
# would be a second source.

version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)
if [ -n "$version" ]; then
    note "OK    version single-source reads $version"
else
    fail "cannot find a top-level 'version = ' in pyproject.toml"
fi

hardcoded=$(grep -rn "__version__[[:space:]]*=[[:space:]]*['\"]" src || true)
[ -z "$hardcoded" ] || fail "a hardcoded version string shadows the single source:
$hardcoded"

# ---- 5. repository shape ---------------------------------------------------
#
# CLAUDE.md -> AGENTS.md must stay a symlink in the index (git mode 120000).
# Replaced by a copy it still works, and then the two drift apart silently, which is
# the failure this catches.

links_ok=1
check_link() {
    mode=$(git ls-files -s "$1" | awk '{print $1}')
    target=$(git cat-file -p ":$1" 2>/dev/null || echo '?')
    if [ "$mode" != "120000" ]; then
        fail "$1 is not a symlink in the index (mode ${mode:-absent})"
        links_ok=0
    elif [ "$target" != "$2" ]; then
        fail "$1 points at '$target', expected '$2'"
        links_ok=0
    fi
}
check_link CLAUDE.md AGENTS.md
[ "$links_ok" -eq 1 ] && note "OK    CLAUDE.md -> AGENTS.md is a symlink"

# ---- report ----------------------------------------------------------------

if [ "$failed" -eq 0 ]; then
    note "all checks passed"
    exit 0
fi
note "one or more checks failed"
exit 1
