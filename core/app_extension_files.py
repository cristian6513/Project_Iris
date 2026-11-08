"""Bounded external components and owned skill transactions; never import app code."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import yaml

from core.profile_paths import _plain_directory, validate_profile_home, ProfilePathError

MAX_COMPONENT_BYTES = 1024 * 1024
PART = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')


def component_files(files: dict[str, bytes], manifest: dict) -> dict[str, bytes]:
    extension = manifest.get('extension', {})
    output = {}
    for name, data in files.items():
        if not name.startswith(('backend/', 'skill/')):
            continue
        parts = name.split('/')
        if len(parts) > 3 or len(data) > 256 * 1024:
            raise ValueError(f'{name}: componente demasiado grande o con demasiadas carpetas.')
        leaf, dot, suffix = parts[-1].rpartition('.')
        if (any(not PART.fullmatch(part) for part in parts[1:-1]) or not dot
                or not (PART.fullmatch(leaf) or name == 'skill/SKILL.md')):
            raise ValueError(f'{name}: nombre de componente no válido.')
        if parts[0] == 'backend':
            if not extension.get('python') or suffix not in ('py', 'json', 'txt') or leaf in ('requirements', 'setup', 'sitecustomize', 'usercustomize'):
                raise ValueError(f'{name}: solo Python y datos propios; no se instalan dependencias.')
        elif not extension.get('skill') or suffix != 'md':
            raise ValueError(f'{name}: las skills de apps contienen SKILL.md y referencias Markdown, sin scripts.')
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError(f'{name}: debe ser UTF-8.') from None
        output['__extension__/' + name] = data
    if sum(map(len, output.values())) > MAX_COMPONENT_BYTES:
        raise ValueError('Python y la skill superan 1 MiB.')
    if extension.get('python') and '__extension__/backend/' + extension['python'] not in output:
        raise ValueError('Falta el archivo declarado en extension.python.')
    if extension.get('skill'):
        raw = output.get('__extension__/skill/SKILL.md')
        if raw is None:
            raise ValueError('Falta skill/SKILL.md.')
        validate_skill(raw.decode(), manifest['id'])
    return output


def validate_skill(source: str, app_id: str):
    """A deliberately small scalar frontmatter, understood without app dependencies."""
    lines = source.splitlines()
    if not lines or lines[0] != '---' or '---' not in lines[1:]:
        raise ValueError('SKILL.md: falta el encabezado name y description entre --- .')
    end = lines.index('---', 1)
    if end > 12:
        raise ValueError('SKILL.md: encabezado demasiado largo.')
    fields = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(':')
        if not sep or key not in ('name', 'description', 'license') or key in fields:
            raise ValueError('SKILL.md: encabezado simple con name, description y license opcional.')
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise ValueError('SKILL.md: usa texto en una línea o comillas JSON.') from None
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        if not isinstance(value, str) or not value or len(value) > 300 or any(ord(c) < 32 for c in value):
            raise ValueError('SKILL.md: valores de texto de 1 a 300 caracteres en una línea.')
        fields[key] = value
    # Hermes parses YAML first. Values accepted as plain strings here must not
    # turn into booleans/lists/dates, lose comments, or use its malformed-YAML fallback.
    try:
        interpreted = yaml.safe_load('\n'.join(lines[1:end]))
    except yaml.YAMLError:
        interpreted = None
    if (not isinstance(interpreted, dict) or interpreted != fields
            or any(not isinstance(value, str) for value in interpreted.values())):
        raise ValueError('SKILL.md: usa comillas JSON para que los valores sean texto YAML sin cambios.')
    if fields.get('name') != f'iris-app-{app_id}' or not fields.get('description'):
        raise ValueError(f'SKILL.md: name debe ser iris-app-{app_id} y description es obligatoria.')
    if not '\n'.join(lines[end + 1:]).strip():
        raise ValueError('SKILL.md: faltan las instrucciones de uso.')
    return fields


def plain_bytes(path: Path) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 256 * 1024:
        raise ValueError('El componente contiene un enlace o archivo ajeno.')
    with path.open('rb') as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('El componente cambió mientras se leía.')
        data = handle.read(256 * 1024 + 1)
    if len(data) > 256 * 1024:
        raise ValueError('El componente supera el tamaño permitido.')
    return data


def read_tree(folder: Path, home: Path) -> dict[str, bytes] | None:
    _plain_directory(folder, home)
    if not folder.exists():
        return None
    files = {}
    for path in folder.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('El componente contiene enlaces.')
        if path.is_dir():
            _plain_directory(path, home)
        else:
            files[path.relative_to(folder).as_posix()] = plain_bytes(path)
            if len(files) > 400 or sum(map(len, files.values())) > MAX_COMPONENT_BYTES:
                raise ValueError('El componente supera el tamaño permitido.')
    return files


def write_tree(folder: Path, files: dict[str, bytes], home: Path):
    _plain_directory(folder, home)
    if folder.exists():
        raise ValueError('La carpeta del componente ya existe.')
    folder.mkdir(parents=True)
    for name, data in files.items():
        target = folder / name
        if not target.is_relative_to(folder) or '..' in Path(name).parts or Path(name).is_absolute():
            raise ValueError('Ruta de componente no válida.')
        _plain_directory(target.parent, home)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(data)


def remove_tree(folder: Path, expected: dict[str, bytes], home: Path):
    # Never recursively delete a computed tree. Check its exact contents and remove
    # only those files and their now-empty directories, retaining anything foreign.
    if read_tree(folder, home) != expected:
        raise ValueError('El componente fue modificado; se conservó sin cambios.')
    for name in expected:
        (folder / name).unlink()
    for path in sorted((p for p in folder.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        path.rmdir()
    folder.rmdir()


def encode_files(files):
    return None if files is None else {key: base64.b64encode(value).decode() for key, value in files.items()}


def decode_files(files):
    return None if files is None else {key: base64.b64decode(value, validate=True) for key, value in files.items()}


class SkillChange:
    """Journaled rename. DB installation identity decides recovery after a crash."""
    def __init__(self, home: Path, app_id: str, old, new, token: str):
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,31}', app_id) or not re.fullmatch(r'ins_[0-9a-f]{32}', token):
            raise ValueError('Propietario de skill no válido.')
        self.home = validate_profile_home(home)
        self.name = 'iris-app-' + app_id
        self.folder = self.home / 'skills' / self.name
        self.backup = self.home / 'skills' / ('.' + self.name + '-backup-' + token)
        self.staged = self.home / 'skills' / ('.' + self.name + '-new-' + token)
        self.old, self.new = old, new

    def preflight(self):
        actual = read_tree(self.folder, self.home)
        if actual != self.old:
            raise ValueError(f'La skill {self.name} existe o fue modificada por ti. Se conservó; resuelve el conflicto antes de actualizar.')
        for folder in (self.backup, self.staged):
            if read_tree(folder, self.home) is not None:
                raise ValueError('Hay una operación de skill pendiente de recuperar.')

    def apply(self):
        self.preflight()
        if self.new is not None:
            write_tree(self.staged, self.new, self.home)
        if self.old is not None:
            self.folder.rename(self.backup)
        if self.new is not None:
            self.staged.rename(self.folder)

    def recover(self, committed: bool):
        target = self.new if committed else self.old
        actual = read_tree(self.folder, self.home)
        backup = read_tree(self.backup, self.home)
        staged = read_tree(self.staged, self.home)
        if actual != target:
            allowed = self.old if committed else self.new
            if actual is not None:
                if actual != allowed:
                    raise ValueError('La skill cambió durante la recuperación; se conservó.')
                remove_tree(self.folder, actual, self.home)
            if target is not None:
                if not committed and backup == target:
                    self.backup.rename(self.folder)
                    backup = None
                elif committed and staged == target:
                    self.staged.rename(self.folder)
                    staged = None
                else:
                    write_tree(self.folder, target, self.home)
        if backup is not None:
            if backup != self.old:
                raise ValueError('La copia de recuperación fue modificada; se conservó.')
            remove_tree(self.backup, backup, self.home)
        if staged is not None:
            # A crash can interrupt write_tree before every staged file is
            # complete. The journal owns precisely these names and byte prefixes;
            # anything else is a conflict and is retained.
            if self.new is None or any(name not in self.new or not self.new[name].startswith(data)
                                       for name, data in staged.items()):
                raise ValueError('La copia preparada fue modificada; se conservó.')
            remove_tree(self.staged, staged, self.home)
