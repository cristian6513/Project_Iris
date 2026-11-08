"""Python twin of face/js/os_app_manifest.js, for Core's app package installer (F17).

Same rules, messages and normalized result as the JS validator that the build and the shell
use. tests/test_app_manifest_parity.py runs both on the same manifests so they cannot drift.
JS details that matter for parity: `trim()` and its whitespace set, lengths in UTF-16 code
units, `Number.isInteger` (1.0 is an integer, true is not) and `undefined` versus `null`.
"""
from __future__ import annotations

import re

from core.app_extension_contract import validate_extension

APP_MANIFEST_SCHEMA = 'iris.app-manifest/v1'
APP_SDK_VERSION = 2
APP_ACCENTS = ('cyan', 'magenta', 'amber')
APP_PRESENTATIONS = ('window', 'tab')
SHELL_DESTINATIONS = ('desktop', 'apps')
APP_UI_ADAPTERS = ('legacy',)
APP_UI_MODULE = re.compile(r'[a-z][a-z0-9_]{0,63}\.js')
APP_UI_ENTRY = re.compile(r'[a-z][a-z0-9_-]{0,63}\.js')
APP_UI_STYLE = re.compile(r'[a-z][a-z0-9_-]{0,63}\.css')

ID = re.compile(r'[a-z][a-z0-9-]{1,31}')
VERSION = re.compile(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[0-9A-Za-z.-]{1,32})?', re.ASCII)  # JS \d is ASCII
NEED = re.compile(r'[a-z][a-z0-9_.-]{1,63}')
WIDGET_NAME = re.compile(r'[a-z][a-z0-9-]{0,31}')
CONTROL = re.compile(r'[\x00-\x1f\x7f]')
# String.prototype.trim(): ECMAScript WhiteSpace and LineTerminator.
_JS_SPACE = '\t\n\v\f\r                  　﻿'
WINDOW_LIMITS = {'width': (360, 1600), 'height': (300, 1200)}
FIELDS = {
    'manifest': ('schema', 'sdkVersion', 'id', 'name', 'version', 'description', 'icon', 'accent',
                 'presentation', 'window', 'launcher', 'ui', 'requestedCapabilities', 'services', 'widgets', 'extension'),
    'window': ('width', 'height'),
    'launcher': ('visible', 'order', 'label'),
    'ui': ('adapter', 'module', 'entry', 'style'),
    'widget': ('id', 'name', 'entry', 'style', 'size', 'configVersion', 'preview'),
}
MISSING = object()  # JS `undefined`; JSON null is None and, as in JS, is not undefined.


def _js_length(value: str) -> int:
    return len(value.encode('utf-16-le')) // 2


def _is_integer(value) -> bool:
    return not isinstance(value, bool) and (isinstance(value, int) or isinstance(value, float) and value.is_integer())


def _get(container, key):
    return container.get(key, MISSING) if isinstance(container, dict) else MISSING


def _unknown(value: dict, allowed, path: str, errors: list) -> None:
    for key in value:
        if key not in allowed:
            errors.append(f'{path}{key}: campo desconocido')


def _text(value, path: str, limit: int, errors: list, *, required: bool = True):
    if value is MISSING and not required:
        return None
    if not isinstance(value, str) or not value.strip(_JS_SPACE):
        errors.append(f'{path}: debe ser un texto no vacío')
        return None
    clean = value.strip(_JS_SPACE)
    if _js_length(clean) > limit:
        errors.append(f'{path}: máximo {limit} caracteres')
    if CONTROL.search(clean):
        errors.append(f'{path}: no admite caracteres de control')
    return clean


def _integer(value, path: str, bounds, errors: list):
    low, high = bounds
    if not _is_integer(value) or value < low or value > high:
        errors.append(f'{path}: debe ser un entero entre {low} y {high}')
        return None
    return int(value)


def _needs(value, path: str, errors: list) -> list:
    if value is MISSING:
        return []
    if not isinstance(value, list) or len(value) > 16:
        errors.append(f'{path}: debe ser una lista de hasta 16 identificadores')
        return []
    seen: list = []
    for item in value:
        if not isinstance(item, str) or not NEED.fullmatch(item):
            errors.append(f'{path}: identificador inválido')
        elif item in seen:
            errors.append(f'{path}: «{item}» repetido')
        if item not in seen:
            seen.append(item)
    return seen


def _preview(raw, errors: list):
    if raw is MISSING:
        return None
    if not isinstance(raw, dict):
        errors.append('widgets.preview: debe ser un objeto')
        return None
    _unknown(raw, ('layout', 'title', 'subtitle', 'items'), 'widgets.preview.', errors)
    layout = _get(raw, 'layout')
    if layout not in ('agenda', 'player', 'summary'):
        errors.append('widgets.preview.layout: agenda, player o summary')
    title = _text(_get(raw, 'title'), 'widgets.preview.title', 60, errors)
    subtitle = _text(_get(raw, 'subtitle'), 'widgets.preview.subtitle', 80, errors, required=False)
    items = []
    raw_items = _get(raw, 'items')
    if not isinstance(raw_items, list) or len(raw_items) > 3:
        errors.append('widgets.preview.items: lista de hasta tres ejemplos')
    else:
        for item in raw_items:
            if not isinstance(item, dict):
                errors.append('widgets.preview.items: cada ejemplo debe ser un objeto')
                continue
            _unknown(item, ('label', 'detail'), 'widgets.preview.items.', errors)
            label = _text(_get(item, 'label'), 'widgets.preview.items.label', 60, errors)
            detail = _text(_get(item, 'detail'), 'widgets.preview.items.detail', 60, errors, required=False)
            items.append({'label': label, **({'detail': detail} if detail else {})})
    return {'layout': layout, 'title': title, **({'subtitle': subtitle} if subtitle else {}), 'items': items}


def validate_app_manifest(raw, *, icon_names=None) -> dict:
    """``{'ok', 'manifest', 'errors', 'incompatible'}`` exactly as validateAppManifest() returns."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {'ok': False, 'manifest': None, 'errors': ['El manifiesto debe ser un objeto JSON.'], 'incompatible': False}
    if _get(raw, 'schema') != APP_MANIFEST_SCHEMA:
        errors.append(f'schema: debe ser «{APP_MANIFEST_SCHEMA}»')
    sdk = _get(raw, 'sdkVersion')
    if not _is_integer(sdk) or sdk < 1:
        errors.append('sdkVersion: debe ser un entero positivo')
    incompatible = _is_integer(sdk) and sdk > APP_SDK_VERSION
    if incompatible:
        errors.append(f'sdkVersion: {int(sdk)} no es compatible con esta versión de IRIS OS (SDK {APP_SDK_VERSION})')
    _unknown(raw, FIELDS['manifest'], '', errors)

    raw_id = _get(raw, 'id')
    app_id = raw_id if isinstance(raw_id, str) and ID.fullmatch(raw_id) else None
    if not app_id:
        errors.append('id: minúsculas, números y guiones; 2 a 32 caracteres, empezando por letra')
    elif app_id in SHELL_DESTINATIONS:
        errors.append(f'id: «{app_id}» está reservado por el escritorio')
    name = _text(_get(raw, 'name'), 'name', 40, errors)
    raw_version = _get(raw, 'version')
    version = raw_version if isinstance(raw_version, str) and VERSION.fullmatch(raw_version) else None
    if not version:
        errors.append('version: formato «mayor.menor.parche»')
    description = _text(_get(raw, 'description'), 'description', 80, errors)

    raw_icon = _get(raw, 'icon')
    icon = raw_icon if isinstance(raw_icon, str) else None
    if not icon:
        errors.append('icon: nombre de un icono de os_icons.js')
    elif icon_names is not None and icon not in icon_names:
        errors.append(f'icon: «{icon}» no existe en os_icons.js')
    accent = _get(raw, 'accent')
    accent = 'cyan' if accent is MISSING or accent is None else accent
    if accent not in APP_ACCENTS:
        errors.append(f'accent: uno de {", ".join(APP_ACCENTS)}')
    presentation = _get(raw, 'presentation')
    if presentation not in APP_PRESENTATIONS:
        errors.append(f'presentation: uno de {", ".join(APP_PRESENTATIONS)}')

    window_size = None
    raw_window = _get(raw, 'window')
    if presentation == 'window':
        if not isinstance(raw_window, dict):
            errors.append('window: obligatorio para presentation «window»')
        else:
            _unknown(raw_window, FIELDS['window'], 'window.', errors)
            window_size = {'width': _integer(_get(raw_window, 'width'), 'window.width', WINDOW_LIMITS['width'], errors),
                           'height': _integer(_get(raw_window, 'height'), 'window.height', WINDOW_LIMITS['height'], errors)}
    elif raw_window is not MISSING:
        errors.append('window: solo para presentation «window»')

    raw_launcher = _get(raw, 'launcher')
    raw_launcher = {} if raw_launcher is MISSING or raw_launcher is None else raw_launcher
    launcher = None
    if not isinstance(raw_launcher, dict):
        errors.append('launcher: debe ser un objeto')
    else:
        _unknown(raw_launcher, FIELDS['launcher'], 'launcher.', errors)
        visible = _get(raw_launcher, 'visible')
        visible = True if visible is MISSING or visible is None else visible
        if not isinstance(visible, bool):
            errors.append('launcher.visible: true o false')
        order = _get(raw_launcher, 'order')
        launcher = {'visible': visible,
                    'order': _integer(1000 if order is MISSING or order is None else order, 'launcher.order', (0, 9999), errors),
                    'label': _text(_get(raw_launcher, 'label'), 'launcher.label', 32, errors, required=False)}

    ui = None
    raw_ui = _get(raw, 'ui')
    if not isinstance(raw_ui, dict):
        errors.append('ui: obligatorio')
    else:
        _unknown(raw_ui, FIELDS['ui'], 'ui.', errors)
        kinds = [key for key in ('adapter', 'module', 'entry') if key in raw_ui]
        if len(kinds) != 1:
            errors.append('ui: indica solo uno de «adapter», «module» o «entry»')
        elif kinds[0] == 'adapter':
            if raw_ui['adapter'] not in APP_UI_ADAPTERS:
                errors.append('ui.adapter: esta versión solo admite «legacy»')
            ui = {'adapter': raw_ui['adapter']}
        elif kinds[0] == 'module':
            if not isinstance(raw_ui['module'], str) or not APP_UI_MODULE.fullmatch(raw_ui['module']):
                errors.append('ui.module: nombre de un archivo .js de face/js, sin carpetas')
            ui = {'module': raw_ui['module']}
        else:
            if not isinstance(raw_ui['entry'], str) or not APP_UI_ENTRY.fullmatch(raw_ui['entry']):
                errors.append('ui.entry: nombre de un archivo .js de la carpeta ui/ de la app, sin subcarpetas')
            ui = {'entry': raw_ui['entry']}
        if 'style' in raw_ui:
            if not (ui and ui.get('entry')):
                errors.append('ui.style: solo para apps con «entry»')
            elif not isinstance(raw_ui['style'], str) or not APP_UI_STYLE.fullmatch(raw_ui['style']):
                errors.append('ui.style: nombre de un archivo .css de la carpeta ui/ de la app')
            else:
                ui['style'] = raw_ui['style']

    requested = _needs(_get(raw, 'requestedCapabilities'), 'requestedCapabilities', errors)
    services = _needs(_get(raw, 'services'), 'services', errors)
    widgets = []
    raw_widgets = _get(raw, 'widgets')
    if raw_widgets is not MISSING:
        if not isinstance(raw_widgets, list) or len(raw_widgets) > 4:
            errors.append('widgets: lista de hasta cuatro widgets')
        else:
            seen: set = set()
            for widget in raw_widgets:
                if not isinstance(widget, dict):
                    errors.append('widgets: cada widget debe ser un objeto')
                    continue
                _unknown(widget, FIELDS['widget'], 'widgets.', errors)
                prefix = f'{app_id if app_id else "undefined"}.'
                widget_id = _get(widget, 'id')
                if (not isinstance(widget_id, str) or not widget_id.startswith(prefix)
                        or not WIDGET_NAME.fullmatch(widget_id[len(prefix):])):
                    errors.append('widgets.id: id de la app seguido de un nombre, por ejemplo calendar.month')
                key = widget_id if isinstance(widget_id, (str, int, float, bool)) or widget_id is None else id(widget_id)
                if key in seen:
                    errors.append('widgets.id: widget repetido')
                seen.add(key)
                widget_name = _text(_get(widget, 'name'), 'widgets.name', 40, errors)
                entry = _get(widget, 'entry')
                if not isinstance(entry, str) or not APP_UI_ENTRY.fullmatch(entry):
                    errors.append('widgets.entry: archivo .js de ui/, sin carpetas ni URL')
                style = _get(widget, 'style')
                if style is not MISSING and (not isinstance(style, str) or not APP_UI_STYLE.fullmatch(style)):
                    errors.append('widgets.style: archivo .css de ui/, sin carpetas ni URL')
                if _get(widget, 'size') != 'compact':
                    errors.append('widgets.size: esta versión admite compact')
                config_version = _get(widget, 'configVersion')
                if not (_is_integer(config_version) and config_version == 1):
                    errors.append('widgets.configVersion: esta versión admite 1')
                preview = _preview(_get(widget, 'preview'), errors)
                widgets.append({'id': widget_id, 'name': widget_name, 'entry': entry,
                                **({'style': style} if isinstance(style, str) and style else {}),
                                'size': 'compact', 'configVersion': 1, **({'preview': preview} if preview else {})})
    extension = validate_extension(raw['extension'], sdk, errors) if 'extension' in raw else None
    if errors:
        return {'ok': False, 'manifest': None, 'errors': errors, 'incompatible': bool(incompatible)}

    manifest = {
        'schema': APP_MANIFEST_SCHEMA, 'sdkVersion': int(sdk), 'id': app_id, 'name': name, 'version': version,
        'description': description, 'icon': icon, 'accent': accent, 'presentation': presentation,
        **({'window': window_size} if window_size else {}),
        'launcher': launcher if launcher['label'] else {'visible': launcher['visible'], 'order': launcher['order']},
        'ui': ui, 'requestedCapabilities': requested, 'services': services,
        **({'widgets': widgets} if widgets else {}),
        **({'extension': extension} if extension is not None else {}),
    }
    return {'ok': True, 'manifest': manifest, 'errors': [], 'incompatible': False}
