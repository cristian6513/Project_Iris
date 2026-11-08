"""Delegate to the official checker in an Iris source checkout; never install an app."""
import argparse
from pathlib import Path
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'pack'))
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    parser.add_argument('--iris-source', type=Path)
    parser.add_argument('--python', type=Path)
    args = parser.parse_args()
    if (args.command == 'pack') != (args.output is not None):
        parser.error('pack necesita una salida .zip; check no la recibe.')
    source = (args.iris_source or Path(__file__).resolve().parents[2] / 'Project iris').resolve()
    checker = source / 'scripts/app_package_tool.py'
    if not checker.is_file() or not (source / 'core/app_packages.py').is_file():
        parser.error('No encuentro el código principal de Iris. Indica --iris-source RUTA.')
    python = str(args.python.resolve()) if args.python else sys.executable
    command = [python, '-B', str(checker), args.command, str(args.input.resolve())]
    if args.output:
        command.append(str(args.output.resolve()))
    return subprocess.run(command, cwd=source).returncode

if __name__ == '__main__':
    raise SystemExit(main())
