"""«Avisar a Iris»: the shell (face/js/app_ask_iris.js) accepts exactly what Core accepts
(core/app_messages.py). Otherwise the person approves a message in Iris' dialog that Core then
refuses. Same cases on both sides, with the edges of Core's measure: 4096 bytes of
``json.dumps(ensure_ascii=True)``, its separators, escaped non-ASCII and Python's float exponents.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from core.app_messages import MessageRefused, checked_message

ROOT = Path(__file__).resolve().parents[1]


def sized(total, *, char='x', extra=None):
    """``{"t": ...}`` (plus ``extra``) whose Core size is ``total`` bytes, filled with ``char``."""
    base = {'t': '', **(extra or {})}
    room = total - len(json.dumps(base, ensure_ascii=True).encode())
    unit = len(json.dumps(char, ensure_ascii=True)) - 2
    value = char * (room // unit) + 'x' * (room % unit)
    return {'t': value, **(extra or {})}


def nested(levels):
    value = 1
    for _ in range(levels):
        value = [value]
    return value


TEXTS = [' hola ', 'Te toca', '/reset', '  /reset', '', '   ', 'x' * 300, 'x' * 301, '😀' * 300, '😀' * 301,
         'a\tb', 'a\nb', 'a\rb', 'a\x7fb', 'ñ' * 300]
DATA = [
    None, True, 0, -0.5, 'texto', [], {},
    sized(4096), sized(4097), sized(4096, char='ñ'), sized(4097, char='ñ'), sized(4096, char='😀'),
    sized(4097, char='😀'), sized(4096, char='"'), sized(4097, char='\\'), sized(4096, char='\x01'),
    sized(4097, char='\n'), sized(4096, char='\x7f'),
    sized(4096, extra={'n': 1e-07}), sized(4097, extra={'n': 1e-07}), sized(4096, extra={'n': 1.5e-10}),
    sized(4097, extra={'n': 2.5e-300}), sized(4096, extra={'n': 123.25, 'b': False, 'z': None}),
    # JavaScript writes these as decimals, Python as 1e-06 (Codex P3): both edges of each.
    *[sized(total, extra={'n': number}) for number in (0.000001, 0.00001, 1.234e-05, -0.00005, 0.0001, 0.00012,
                                                        1e-07, 9.999e-05, 0.000123456789)
      for total in (4095, 4096, 4097)],
    sized(4097, extra={'l': [1, 2, [3, {}]]}),
    [0] * 128, [0] * 129, {f'k{i}': 0 for i in range(64)}, {f'k{i}': 0 for i in range(65)},
    {'k' * 120: 1}, {'k' * 121: 1}, {'ñ' * 120: 1}, nested(8), nested(9), {'a': nested(7)}, {'a': nested(8)},
    1e15, 1e15 + 2, -1e15, [1e15, -1e15 - 2],
]

SCRIPT = """
import { askIrisRequest } from './face/js/app_ask_iris.js';
let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) input += chunk;
const answer = (request) => { try { askIrisRequest(request); return true; } catch { return false; } };
// What the websocket carries to Core is the shell's own JSON (1e15 arrives as an integer).
process.stdout.write(JSON.stringify(JSON.parse(input).map((request) => [answer(request), JSON.stringify(request)])));
"""
CASES = ([{'text': text} for text in TEXTS] + [{'text': 'Hola', 'data': data} for data in DATA])


def core_accepts(wire):
    try:
        checked_message({'request_id': 'a' * 32, 'app_id': 'iris-chess', **json.loads(wire)})
    except MessageRefused:
        return False
    return True


@pytest.fixture(scope='module')
def js_results():
    env = {key: value for key, value in os.environ.items() if not key.startswith('NODE_TEST')}
    reply = subprocess.run(['node', '--input-type=module', '-e', SCRIPT], cwd=ROOT, env=env, capture_output=True,
                           input=json.dumps(CASES, ensure_ascii=False).encode('utf-8'), timeout=60, check=True)
    return json.loads(reply.stdout.decode('utf-8'))


def test_the_shell_and_core_accept_the_same_messages(js_results):
    assert len(js_results) == len(CASES)
    for (shell, wire) in js_results:
        assert shell == core_accepts(wire), (shell, wire[:120])


def test_the_cases_sit_on_both_sides_of_each_limit(js_results):
    """Control: every size edge is present on both sides, so the parity is not vacuous."""
    sizes = [len(json.dumps(json.loads(wire)['data'], ensure_ascii=True).encode()) for _, wire in js_results
             if '"t":' in wire]
    assert sizes.count(4096) >= 6 and sizes.count(4097) >= 6
    accepted = sum(shell for shell, _ in js_results)
    assert accepted >= 20 and len(js_results) - accepted >= 20
