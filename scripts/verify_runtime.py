"""Verify an A08.4 private Windows runtime, without executing its binaries."""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .runtime_inventory import read_json, verify_runtime
else:
    from runtime_inventory import read_json, verify_runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument(
        "--pin", type=Path, default=Path(__file__).with_name("runtime_sources.json")
    )
    args = parser.parse_args(argv)
    document = verify_runtime(args.root, args.inventory, read_json(args.pin, maximum=64 * 1024))
    print(f"private runtime verified: {args.root} ({len(document['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
