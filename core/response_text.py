"""Read-only decoding of live and historical/background assistant responses.

Only IRIS's ``texto_hablado`` contract is decoded inside strings. Arbitrary
JSON, Markdown and code remain content. Historical presentation never replays
avatar actions. A Hermes failed-write footer becomes an honest, readable warning.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FIELDS = ('output', 'result', 'summary', 'content', 'text', 'message', 'response', 'error')
_TEXT_TYPES = {'', 'text', 'input_text', 'output_text', 'message', 'error'}
_IMAGE_TYPES = {'image', 'image_url', 'input_image'}
_MAX_DEPTH = 12
_FILE_MUTATION_HEADER = re.compile(
    r'⚠\ufe0f?\s*File-mutation verifier:\s*(?P<count>[1-9][0-9]{0,8}) '
    r'file edit\(s\) FAILED this turn\b'
)
_FILE_MUTATION_LINE = re.compile(r'^\s*' + _FILE_MUTATION_HEADER.pattern, re.MULTILINE)


def _mutation_failure(match: re.Match) -> dict:
    # The original diagnostic stays in Hermes' transcript. Do not read its paths
    # or instructions aloud, or repeat a success claim contradicted by the footer.
    return {
        'texto_hablado': (
            'Hubo un problema al guardar un archivo. No puedo dar la acción por '
            'completada; hay que comprobar su estado antes de continuar.'
        ),
        'emocion': 'pensativa',
        'animacion_virtual': 'idle',
        '_file_mutation_error': {'code': 'file_write_failed', 'failed_count': int(match['count'])},
    }


def iris_response_envelope(text: str) -> dict | None:
    """Read a complete avatar object even with prose or an appended Hermes footer.

    Decode whole objects, skipping their strings/nested data, rather than slicing
    at the last brace. Literal examples inside texto_hablado remain literal.
    """
    footer = next((match for match in _FILE_MUTATION_LINE.finditer(text)
                   if text[:match.start()].count('```') % 2 == 0), None)
    decoder = json.JSONDecoder()
    cursor = 0
    for _ in range(128):
        start = text.find('{', cursor)
        if start < 0:
            break
        if footer is not None and start >= footer.start():
            break  # JSON quoted by an error preview is not the assistant's answer.
        try:
            parsed, end = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            cursor = start + 1
            continue
        cursor = end
        if (not isinstance(parsed, dict) or not _visible(parsed)
                or not isinstance(parsed.get('texto_hablado'), str)):
            continue
        tail = text[end:].lstrip()
        if tail.startswith('```') and (len(tail) == 3 or tail[3].isspace()):
            tail = tail[3:].lstrip()
        failure = _FILE_MUTATION_HEADER.match(tail) or footer
        return _mutation_failure(failure) if failure else parsed

    # Hermes also appends the same footer to a plain-text answer. A fenced
    # example of the diagnostic is content, not a new failed-write signal.
    return _mutation_failure(footer) if footer else None


def _visible(value: dict) -> bool:
    return (str(value.get('role') or '').casefold() in {'', 'assistant', 'iris'}
            and str(value.get('type') or '').casefold() in _TEXT_TYPES | _IMAGE_TYPES)


def _contract(value: Any, depth: int) -> str | None:
    """Find a recognized contract; None differs from an intentionally empty answer."""
    if depth >= _MAX_DEPTH:
        return None
    if isinstance(value, dict):
        if not _visible(value):
            return None
        if isinstance(value.get('texto_hablado'), str):
            # The spoken field itself may deliberately contain example JSON.
            return value['texto_hablado']
        for key in _FIELDS:
            found = _contract(value.get(key), depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        found = [_contract(item, depth + 1) for item in value]
        if any(text is not None for text in found):
            return '\n'.join(text for text in found if text is not None)
    elif isinstance(value, str):
        envelope = iris_response_envelope(value)
        if envelope is not None:
            return envelope['texto_hablado']
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return None
        return _contract(parsed, depth + 1)
    return None


def _string_text(value: str, depth: int) -> str:
    text = value.replace('\x00', '').strip()
    envelope = iris_response_envelope(text)
    if envelope is not None:
        return envelope['texto_hablado']
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        pass
    else:
        found = _contract(parsed, depth)
        return found if found is not None else text

    # Mixed prose / Markdown fences may surround the response contract. Decode
    # complete objects rather than slicing at the last brace (nested values and
    # literal braces in the spoken text are valid). Bound malformed candidates.
    decoder = json.JSONDecoder()
    cursor = 0
    for _ in range(128):
        start = text.find('{', cursor)
        if start < 0:
            break
        try:
            parsed, end = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            cursor = start + 1
            continue
        cursor = end
        found = _contract(parsed, depth)
        if found is not None:
            return found
    return text


def _project(value: Any, depth: int = 0) -> str:
    if depth >= _MAX_DEPTH:
        return ''
    if isinstance(value, str):
        return _string_text(value, depth)
    if isinstance(value, list):
        return '\n'.join(text for item in value if (text := _project(item, depth + 1)))
    if not isinstance(value, dict) or not _visible(value):
        return ''
    if isinstance(value.get('texto_hablado'), str):
        return value['texto_hablado']
    if str(value.get('type') or '').casefold() in _IMAGE_TYPES:
        return '[Imagen adjunta]'
    for key in _FIELDS:
        if value.get(key) is not None:
            text = _project(value[key], depth + 1)
            if text:
                return text
    return ''


def visible_response_text(value: Any, *, maximum: int = 100_000) -> str:
    """Extract the display text first, then apply the surface's length limit."""
    return _project(value).replace('\x00', '').strip()[:maximum]
