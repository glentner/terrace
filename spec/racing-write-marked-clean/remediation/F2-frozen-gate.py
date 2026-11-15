# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Runs the frozen gate, verify/racing_gate.py, unchanged, with the test review cycle 1's F2 added to its lists.

Usage (from the cycle worktree's root; the same modes as the gate):

    uv run --no-project --python 3.15 python -I spec/racing-write-marked-clean/remediation/F2-frozen-gate.py MODE ...

Why: the gate asserts that tests/engine/test_racing.py holds exactly the tests it names, on the base (`base`) and
on the tree (`suite`). The fix for F2 adds one test to that module, so the frozen gate cannot pass as written. The
new test drives the hop record's reset, which the base lacks, so it joins MECHANISM: not run on the base, required
to pass on the tree. Nothing else in the gate changes: this file loads it from its own path, extends that one
tuple, and calls its `main`.

Exit status: the gate's.
"""

from __future__ import annotations

import sys
import importlib.util
from pathlib import Path


ADDED = ('test_writer_that_never_pauses_lets_commit_upstream_return', )
GATE = Path(__file__).resolve().parent.parent / 'verify' / 'racing_gate.py'


def main() -> int:
    spec = importlib.util.spec_from_file_location('racing_gate', GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    assert not set(ADDED) & set(gate.MECHANISM + gate.BASE_FAILS + gate.BASE_PASSES), 'already named by the gate'
    gate.MECHANISM = gate.MECHANISM + ADDED
    print(f'  wrapper: {GATE.name} unchanged; MECHANISM += {ADDED}', flush=True)
    return gate.main(sys.argv[1:])


if __name__ == '__main__':
    sys.exit(main())
