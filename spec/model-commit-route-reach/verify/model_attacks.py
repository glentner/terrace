# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Self-test of route_gate.py's `model` mode: the edits the adversarial reviews named must each go red.

Usage, from the root of a checkout of the branch (reads HEAD's and the base's test_model.py through git, writes
nothing, starts no pytest):

    uv run --no-project --python 3.15 python -I spec/model-commit-route-reach/verify/model_attacks.py

HEAD's text is the control and must be green; each attack is a textual edit of it and must be red. An attack whose
edit no longer applies (its anchor text moved) fails the run, so the self-test cannot pass by testing nothing.
Exit status: 0 when the control is green and every attack red, 1 otherwise.
"""

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route_gate as gate                                      # noqa: E402


def text_at(rev: str) -> str:
    return subprocess.run(['git', 'show', f'{rev}:{gate.MODEL}'], capture_output=True, text=True, check=True).stdout


BASE, HEAD = text_at(gate.BASELINE), text_at('HEAD')
POLL = '            self.scheduler.poll(self.clock.now)\n'
ENGINE = ('            self.engine.commit_upstream(path)\n'
          '            handle = self.engine.open(path, os.O_RDWR | os.O_TRUNC)\n')
ORACLE = '            fd = os.open(self.real(path), os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o644)\n'
NESTED = '        def engine_side():\n            if fresh:\n                self.engine.create(path)\n'
PARITY = '        self.parity(engine_side, oracle_side)\n\n    @rule(path=FILE_PATHS, new=FILE_PATHS)'
CLOCK = '            self.clock.now = max(self.clock.now, min('
BASE_RULE = '        self.engine.commit_upstream(path)\n\n    @rule(advance=ADVANCE)'
ATTACKS = {
    'comprehension rebinds the oracle': (POLL, POLL + '        [None for self.oracle in [self.root]]\n'),
    'a builtin aliased to __init__': (POLL, POLL + '        sorted = self.__init__\n        sorted()\n'),
    'a decorated nested def': (NESTED, '        @self.scanned.append\n' + NESTED),
    'the oracle reads the engine': (ORACLE, ORACLE + '            os.pwrite(fd, self.engine.read(0, 9, 0), 0)\n'),
    'the engine side flushes': (ENGINE, ENGINE + '            self.engine.flush()\n'),
    'a with that can swallow': (PARITY, PARITY.replace('        self.parity', '        with self.root:\n'
                                                                           '            self.parity')),
    'parity given the oracle twice': (PARITY, PARITY.replace('(engine_side,', '(oracle_side,')),
    'the clock moved back': (CLOCK, CLOCK.replace('max(', 'min(')),
    'a base rule emptied': (BASE_RULE, BASE_RULE.replace('self.engine.commit_upstream(path)', 'pass')),
    'the settings lowered': ('max_examples=1000', 'max_examples=999'),
    'a subscript store': (POLL, POLL + "        self.__dict__['oracle'] = self.root\n"),
}


def grade(text: str) -> str:
    gate.show = lambda rev, path: BASE if rev == gate.BASELINE else text
    try:
        gate.mode_model()
    except gate.Red as error:
        return f'RED {str(error)[:90]}'
    return 'GREEN'


def main() -> int:
    gate.say = lambda line: None
    bad = 0
    verdict = grade(HEAD)
    bad += verdict != 'GREEN'
    print(f'{"ok " if verdict == "GREEN" else "BAD"} control (HEAD)                     {verdict}')
    for name, (old, new) in ATTACKS.items():
        applied = HEAD.count(old) >= 1
        verdict = grade(HEAD.replace(old, new, 1)) if applied else 'edit not applied'
        ok = applied and verdict.startswith('RED')
        bad += not ok
        print(f'{"ok " if ok else "BAD"} {name:35s} {verdict}')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
