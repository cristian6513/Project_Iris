"""Check/package a community app with the real Store reader. Never installs it."""
from __future__ import annotations

import argparse
import base64
import io
from pathlib import Path
import sys
import zipfile

from core.app_packages import check_package, read_folder, read_zip, MAX_REQUEST_BYTES
from core.widget_catalog import APP_IDS, ICON_NAMES


def source_files(folder):
    if folder.absolute() != folder.resolve(strict=True):
        raise ValueError('Elige una carpeta real, sin enlaces.')
    folder = folder.resolve(strict=True)
    result = {}
    for path in sorted(folder.rglob('*')):
        relative = path.relative_to(folder)
        if any(part.startswith('.') or part in ('__pycache__', 'node_modules', '__MACOSX') for part in relative.parts):
            continue
        if path.is_symlink() or path.resolve(strict=True) != path.absolute() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('La carpeta contiene enlaces.')
        if path.is_file():
            if path.stat().st_nlink != 1 or path.stat().st_size > 512 * 1024:
                raise ValueError('La carpeta contiene un archivo compartido o demasiado grande.')
            result[relative.as_posix()] = path.read_bytes()
            if len(result) > 400 or sum(map(len, result.values())) > 4 * 1024 * 1024:
                raise ValueError('La carpeta supera 400 archivos o 4 MiB.')
    return result


def inspect_package(path: Path):
    if path.is_dir():
        files = source_files(path)
        payload = {'files': [{'path': key, 'data': base64.b64encode(data).decode()} for key, data in files.items()]}
        package = check_package(*read_folder(payload), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for key, data in files.items():
                archive.writestr(key, data)
        zipped = buffer.getvalue()
    else:
        if path.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError('El archivo supera 6 MiB.')
        zipped = path.read_bytes()
        package = check_package(*read_zip(zipped), bundled_ids=APP_IDS, icon_names=ICON_NAMES)
    if len(zipped) > MAX_REQUEST_BYTES:
        raise ValueError('El paquete supera 6 MiB.')
    assert check_package(*read_zip(zipped), bundled_ids=APP_IDS, icon_names=ICON_NAMES)['digest'] == package['digest']
    return package, zipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'pack'))
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args(argv)
    package, zipped = inspect_package(args.input)
    if args.command == 'pack':
        if args.output is None:
            raise ValueError('Indica el archivo .zip de salida.')
        with args.output.open('xb') as handle:
            handle.write(zipped)
        print(f'Paquete creado: {args.output}')
    else:
        if args.output is not None:
            raise ValueError('check solo recibe un archivo o carpeta.')
        manifest = package['manifest']
        print(f"{manifest['name']} {manifest['version']}: compatible; {len(package['files'])} archivos, SDK {manifest['sdkVersion']}.")
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
