"""Prepare three clean publication trees from Iris source. Never commits or pushes.

Default: print a plan. --apply copies declared files with backups and drift checks.
--verify compares a prepared tree with the current source. Git metadata is never copied.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys
from urllib.parse import unquote
import uuid


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'tools/repositories/exports.json'
TEXT = {'.py', '.js', '.mjs', '.json', '.toml', '.yaml', '.yml', '.md', '.txt', '.html', '.css', '.sh', '.ini', '.cfg', '.lock'}
SECRET_RULES = {
    'api_key': re.compile(r'\b(?:sk-(?:proj-|or-v1-|fish-)?[A-Za-z0-9_-]{20,}|sk_[A-Za-z0-9_-]{24,}|oc_sk_[A-Za-z0-9_-]{20,})\b'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'oauth_client_secret': re.compile(r'\bGOCSPX-[A-Za-z0-9_-]{20,}\b'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}
LINK = re.compile(r'(?<!!)\[([^\]\n]+)\]\((<[^>\n]+>|[^)\n]+)\)')


class ExportError(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value:
        raise ExportError(f'Ruta no permitida: {value}')
    return path


def private_name(name: str) -> bool:
    base = PurePosixPath(name).name.lower()
    return (base.startswith(('client_secret', 'credentials', 'secrets'))
            or base in {'auth.json', 'token.json', 'tokens.json', '.env'}
            or base.startswith('.env.') and base != '.env.example'
            or PurePosixPath(base).suffix in {'.pem', '.key', '.p12', '.pfx', '.sqlite', '.sqlite3', '.db'})


def scan_secrets(name: str, data: bytes, exceptions=()) -> list[dict]:
    if PurePosixPath(name).suffix not in TEXT and PurePosixPath(name).name != '.env.example':
        return []
    text = data.decode('utf-8', errors='replace')
    findings = []
    for rule, pattern in SECRET_RULES.items():
        for match in pattern.finditer(text):
            line_number = text.count('\n', 0, match.start()) + 1
            line_hash = sha(text.splitlines()[line_number - 1].encode())
            if any(item['path'] == name and item['rule'] == rule and item['line_sha256'] == line_hash for item in exceptions):
                continue
            # Values never enter the report or terminal output.
            findings.append({'path': name, 'line': line_number,
                             'rule': rule, 'fingerprint': sha(match.group().encode())})
    return findings


def read_plain(root: Path, relative: str) -> bytes:
    path = root.joinpath(*safe_relative(relative).parts)
    if path.is_symlink() or path.resolve() != path.absolute() or path.stat().st_nlink != 1:
        raise ExportError(f'El origen contiene un enlace o archivo compartido: {relative}')
    if path.stat().st_size > 90 * 1024 * 1024:
        raise ExportError(f'Archivo demasiado grande para el repositorio: {relative}')
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ExportError(f'El origen cambió mientras se leía: {relative}')
    return data


def rewrite_markdown(data: bytes, source_name: str, included: set[str], *, component: str) -> tuple[bytes, int]:
    """Keep included links local; describe references to another repo without inventing URLs."""
    changed = 0
    fenced = False
    output = []
    for line in data.decode('utf-8').splitlines(keepends=True):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        if fenced:
            output.append(line)
            continue
        def replace(match):
            nonlocal changed
            label, target = match.groups()
            target = target[1:-1] if target.startswith('<') else target
            if not target or target.startswith('#') or re.match(r'^[a-z][a-z0-9+.-]*://', target, re.I):
                return match.group()
            path = target.partition('#')[0]
            decoded = unquote(path)
            if re.match(r'^[a-zA-Z]:', decoded) or decoded.startswith('/'):
                changed += 1
                return f'{label} (referencia local no distribuida)'
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_name), decoded))
            if resolved in included or any(name.startswith(resolved.rstrip('/') + '/') for name in included):
                return match.group()
            changed += 1
            if resolved.startswith(('artifacts/', 'docs/audits/')) or resolved.startswith('../'):
                return f'{label} (informe interno no distribuido)'
            if component != 'main':
                return f'{label} (repositorio principal: `{resolved}`)'
            return f'{label} (referencia no distribuida: `{resolved}`)'
        output.append(LINK.sub(replace, line))
    return ''.join(output).encode('utf-8'), changed


@dataclass
class Plan:
    directories: dict[str, str]
    files: dict[str, dict[str, bytes]]
    summary: dict


def make_plan(source: Path, policy: dict) -> Plan:
    candidates = {name.decode('utf-8') for name in git(source, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0') if name}
    roots = set(policy['main_roots'])
    root_files = set(policy['main_files'])
    selected = {}
    exclusions = []
    secrets = []
    for name in sorted(candidates):
        safe_relative(name)
        if not (source / name).is_file():
            continue  # deleted source files are retired through the ownership record
        if name.split('/')[0] not in roots and name not in root_files:
            continue
        if private_name(name) or any(name.startswith(prefix) for prefix in policy['excluded_prefixes']):
            exclusions.append(name)
            continue
        data = read_plain(source, name)
        secrets.extend(scan_secrets(name, data, policy.get('secret_exceptions', ())))
        selected[name] = data
    if secrets:
        raise ExportError('Se detectaron posibles secretos; no se copió nada:\n' + json.dumps(secrets, indent=2))
    components = policy['components']
    files = {'main': dict(selected)}
    for component in ('apps', 'transitions'):
        spec = components[component]
        required = set(spec['files'])
        missing = required - selected.keys()
        if missing:
            raise ExportError(f'Faltan fuentes de {component}: {sorted(missing)}')
        files[component] = {name: data for name, data in selected.items()
                            if name in required or any(name.startswith(prefix) for prefix in spec['prefixes'])}
    templates = 'tools/repositories/templates/'
    for component, template in [('apps', 'apps-readme.md'), ('transitions', 'transitions-readme.md')]:
        files[component]['README.md'] = read_plain(source, templates + template)
        files[component]['.gitignore'] = read_plain(source, templates + 'kit-gitignore')
    files['apps']['herramientas/validar_app.py'] = read_plain(source, templates + 'validar_app.py')
    files['apps']['core/README.md'] = ('# Referencias del contrato\n\nEstas copias son para consulta. '
        'No forman un runtime autónomo de Core. Usa herramientas/validar_app.py con el repositorio principal '
        'para el chequeo oficial.\n').encode()
    base = git(source, 'rev-parse', 'HEAD').decode().strip()
    dirty = bool(git(source, 'status', '--porcelain', '--untracked-files=normal'))
    source_hash = sha(b''.join(name.encode() + b'\0' + sha(data).encode() + b'\n' for name, data in sorted(selected.items())))
    summaries = {}
    for component, payload in files.items():
        included = set(payload)
        link_changes = 0
        for name in list(payload):
            public_doc = (name in {'README.md', 'CONTRIBUTING.md'} or name.startswith(('docs/', 'tools/skills/'))
                          or name.startswith(('apps/', 'examples/')) and name.endswith('/README.md'))
            if name.endswith('.md') and public_doc and (name != 'README.md' or component == 'main'):
                payload[name], count = rewrite_markdown(payload[name], name, included, component=component)
                link_changes += count
        tree_hash = sha(b''.join(name.encode() + b'\0' + sha(data).encode() + b'\n' for name, data in sorted(payload.items())))
        metadata = {'schema': 1, 'component': component, 'source_base_commit': base,
                    'source_has_uncommitted_changes': dirty, 'source_tree_sha256': source_hash,
                    'content_tree_sha256': tree_hash, 'payload_files': len(payload),
                    'documentation_references_adapted': link_changes,
                    'exact_source_commit': not dirty, 'installer_acceptance': False}
        payload['SOURCE.json'] = (json.dumps(metadata, indent=2) + '\n').encode()
        summaries[component] = {'directory': components[component]['directory'], 'files': len(payload),
                                'bytes': sum(map(len, payload.values())), 'content_tree_sha256': tree_hash,
                                'documentation_references_adapted': link_changes}
    files['overview'] = {'README.md': read_plain(source, templates + 'overview.md')}
    directories = {key: spec['directory'] for key, spec in components.items()}
    directories['overview'] = '.'
    return Plan(directories, files, {'source_base_commit': base, 'source_has_uncommitted_changes': dirty,
                                    'components': summaries, 'excluded_sensitive_or_internal_files': len(exclusions)})


def target_path(destination: Path, directory: str, name: str) -> Path:
    root = destination if directory == '.' else destination.joinpath(*safe_relative(directory).parts)
    path = root.joinpath(*safe_relative(name).parts)
    if not path.resolve().is_relative_to(destination.resolve()):
        raise ExportError(f'El destino sale de la carpeta de publicación: {name}')
    for parent in (root, *path.parents):
        if parent == destination.parent:
            break
        if parent.exists() and (parent.is_symlink() or parent.resolve() != parent.absolute()):
            raise ExportError(f'El destino contiene un enlace: {parent.name}')
    if path.exists() and (not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1):
        raise ExportError(f'El destino no es un archivo ordinario: {name}')
    return path


def state_path(destination: Path) -> Path:
    return target_path(destination, '.', '.iris-publication/state.json')


def changes_for(destination: Path, plan: Plan) -> tuple[list[tuple[Path, bytes | None]], list[str]]:
    state_file = state_path(destination)
    state = json.loads(state_file.read_text('utf-8')) if state_file.exists() else {'files': {}}
    previous = state.get('files', {})
    changes = []
    conflicts = []
    desired = {}
    for component, payload in plan.files.items():
        for name, data in payload.items():
            path = target_path(destination, plan.directories[component], name)
            relative = path.relative_to(destination).as_posix()
            desired[relative] = data
            current = path.read_bytes() if path.exists() else None
            if current == data:
                continue
            old_hash = previous.get(relative)
            if current is not None and (old_hash is None or sha(current) != old_hash):
                conflicts.append(relative)
            else:
                changes.append((path, data))
    for relative, old_hash in previous.items():
        if relative in desired:
            continue
        path = target_path(destination, '.', relative)
        if path.exists():
            if sha(path.read_bytes()) != old_hash:
                conflicts.append(relative)
            else:
                changes.append((path, None))
    for component, directory in plan.directories.items():
        if directory == '.':
            continue
        root = destination / directory
        if not root.exists():
            continue
        for parent, directories, filenames in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if name not in {
                '.git', '__pycache__', '.venv', 'node_modules', '.pytest_cache', '.mypy_cache', '.ruff_cache',
                'target', 'artifacts', 'build', 'dist'}]
            for filename in filenames:
                path = Path(parent) / filename
                relative = path.relative_to(destination).as_posix()
                if relative not in desired and relative not in previous:
                    conflicts.append('Archivo no administrado: ' + relative)
    return changes, conflicts


def apply_plan(destination: Path, plan: Plan) -> dict:
    changes, conflicts = changes_for(destination, plan)
    if conflicts:
        raise ExportError('Hay cambios manuales en la copia; intégralos primero en Alice. No se copió nada:\n' + '\n'.join(conflicts))
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    backup = destination / '.iris-publication/backups' / stamp
    originals = []
    for path, data in changes:
        current = path.read_bytes() if path.exists() else None
        originals.append((path, current))
        if current is not None:
            saved = backup / path.relative_to(destination)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(current)
    try:
        for path, data in changes:
            path.parent.mkdir(parents=True, exist_ok=True)
            if data is None:
                path.unlink()  # only a hash-verified owned file, already backed up
            else:
                path.write_bytes(data)
        # Verify before updating the ownership record.
        for component, payload in plan.files.items():
            for name, data in payload.items():
                path = target_path(destination, plan.directories[component], name)
                if path.read_bytes() != data:
                    raise ExportError(f'La verificación falló: {name}')
        owned = {}
        for component, payload in plan.files.items():
            for name, data in payload.items():
                path = target_path(destination, plan.directories[component], name)
                owned[path.relative_to(destination).as_posix()] = sha(data)
        state = state_path(destination)
        state.parent.mkdir(parents=True, exist_ok=True)
        temporary = state.with_suffix('.pending')
        temporary.write_text(json.dumps({'schema': 1, 'files': owned}, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, state)
    except BaseException:
        for path, data in reversed(originals):
            if data is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(data)
        raise
    return {'copied_or_updated': sum(data is not None for _, data in changes),
            'retired_with_backup': sum(data is None for _, data in changes),
            'verified_files': len(owned), 'backup_created': backup.exists()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--verify', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    destination = args.destination.absolute()
    if destination.resolve().is_relative_to(ROOT.resolve()) or ROOT.resolve().is_relative_to(destination.resolve()):
        raise ExportError('El destino debe estar fuera del proyecto de trabajo y no contenerlo.')
    policy = json.loads(POLICY.read_text('utf-8'))
    plan = make_plan(ROOT, policy)
    changes, conflicts = changes_for(destination, plan)
    summary = {**plan.summary, 'files_to_copy_or_update': sum(data is not None for _, data in changes),
               'files_to_retire': sum(data is None for _, data in changes), 'conflicts': conflicts,
               'mode': 'apply' if args.apply else 'verify' if args.verify else 'plan'}
    if args.apply:
        summary['result'] = apply_plan(destination, plan)
    if args.verify and (changes or conflicts):
        raise ExportError('La copia no coincide con el origen:\n' + json.dumps(summary, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ExportError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
