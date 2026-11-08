"""Data-only contract for external app workers. No package code is imported by Core."""
from __future__ import annotations

import json
import math
import re

EXTENSION_SCHEMA = 'iris.app-extension/v1'
ARGUMENT_TYPES = ('string', 'integer', 'number', 'boolean', 'object', 'array')
# Who may call an operation: both, only the app's own window and widgets, or only Iris.
# Core assigns the caller from the route a call arrives by; the app never states it.
AUDIENCES = ('all', 'ui', 'iris')
CALLERS = ('ui', 'iris')
_NAME = re.compile(r'[a-z][a-z0-9_]{0,47}')
_ENTRY = re.compile(r'[a-z][a-z0-9_]{0,63}\.py')
_SPACE = '\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'


def validate_extension(raw, sdk, errors: list) -> dict | None:
    if not isinstance(raw, dict):
        errors.append('extension: debe ser un objeto')
        return None
    if sdk != 2:
        errors.append('extension: requiere sdkVersion 2')
    for key in raw:
        if key not in ('schema', 'python', 'skill', 'operations'):
            errors.append(f'extension.{key}: campo desconocido')
    if raw.get('schema') != EXTENSION_SCHEMA:
        errors.append(f'extension.schema: debe ser «{EXTENSION_SCHEMA}»')
    python = raw.get('python')
    if 'python' in raw and (not isinstance(python, str) or not _ENTRY.fullmatch(python)):
        errors.append('extension.python: archivo .py de backend/, sin carpetas')
    skill = raw.get('skill', False)
    if not isinstance(skill, bool):
        errors.append('extension.skill: true o false')
    if not python and not skill:
        errors.append('extension: declara Python o una skill')
    operations = raw.get('operations', [])
    normalized = []
    if not isinstance(operations, list) or len(operations) > 16:
        errors.append('extension.operations: lista de hasta 16 operaciones')
    else:
        seen = set()
        for op in operations:
            if not isinstance(op, dict):
                errors.append('extension.operations: cada operación debe ser un objeto')
                continue
            for key in op:
                if key not in ('action', 'label', 'effect', 'arguments', 'audience'):
                    errors.append(f'extension.operations.{key}: campo desconocido')
            action = op.get('action')
            if not isinstance(action, str) or not _NAME.fullmatch(action):
                errors.append('extension.operations.action: identificador en minúsculas, hasta 48 caracteres')
            elif action in seen:
                errors.append('extension.operations.action: operación repetida')
            else:
                seen.add(action)
            label = op.get('label')
            if (not isinstance(label, str) or not label.strip(_SPACE) or len(label.encode('utf-16-le')) // 2 > 80
                    or re.search(r'[\x00-\x1f\x7f]', label)):
                errors.append('extension.operations.label: texto de 1 a 80 caracteres, sin controles')
            if op.get('effect') not in ('read', 'local', 'external'):
                errors.append('extension.operations.effect: read, local o external')
            args = op.get('arguments', {})
            if (not isinstance(args, dict) or len(args) > 16
                    or any(not _NAME.fullmatch(key) or value not in ARGUMENT_TYPES for key, value in args.items())):
                errors.append('extension.operations.arguments: hasta 16 nombres con tipos string, integer, number, boolean, object o array')
            audience = op.get('audience', 'all')
            if audience not in AUDIENCES:
                errors.append('extension.operations.audience: all, ui o iris')
            normalized.append({'action': action, 'label': label.strip(_SPACE) if isinstance(label, str) else label,
                               'effect': op.get('effect'), 'arguments': args,
                               # Omitted when it is the default, so earlier manifests normalize as before.
                               **({'audience': audience} if audience != 'all' else {})})
    if normalized and not python:
        errors.append('extension.operations: requieren Python')
    return {'schema': EXTENSION_SCHEMA, **({'python': python} if python is not None else {}),
            'skill': skill, 'operations': normalized}


def callable_by(operation: dict, caller: str) -> bool:
    """Whether ``caller`` (``ui`` or ``iris``, as Core assigned it) may run ``operation``."""
    audience = operation.get('audience', 'all')
    return caller in CALLERS and audience in ('all', caller)


def bounded_json(value, *, object_only=False):
    """Plain JSON, finite numbers and bounded shape/bytes on both sides of the pipe."""
    def visit(item, depth=0):
        if depth > 8:
            raise ValueError('Los datos tienen demasiados niveles.')
        if item is None or isinstance(item, (str, bool, int, float)):
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if abs(item) > 1e15 or not math.isfinite(item):
                    raise ValueError('Número fuera de rango.')
            if isinstance(item, str) and len(item) > 8192:
                raise ValueError('Texto demasiado largo.')
        elif isinstance(item, dict) and len(item) <= 64:
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 120:
                    raise ValueError('Clave de datos no válida.')
                visit(child, depth + 1)
        elif isinstance(item, list) and len(item) <= 128:
            for child in item:
                visit(child, depth + 1)
        else:
            raise ValueError('Los datos no son un JSON admitido.')
    if object_only and not isinstance(value, dict):
        raise ValueError('Los datos deben ser un objeto.')
    visit(value)
    if len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) > 32768:
        raise ValueError('Los datos superan 32 KiB.')
    return value


def validate_arguments(spec: dict, args) -> dict:
    bounded_json(args, object_only=True)
    if set(args) != set(spec):
        raise ValueError('Los argumentos no coinciden con la operación.')
    for key, kind in spec.items():
        item = args[key]
        valid = {'string': isinstance(item, str), 'integer': isinstance(item, int) and not isinstance(item, bool),
                 'number': isinstance(item, (int, float)) and not isinstance(item, bool),
                 'boolean': isinstance(item, bool), 'object': isinstance(item, dict), 'array': isinstance(item, list)}
        if not valid[kind]:
            raise ValueError(f'{key}: se esperaba {kind}.')
    return dict(args)
