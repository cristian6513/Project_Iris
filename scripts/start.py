"""
IRIS OS — Cross-platform Python Launcher

Works on both Windows and Linux.
Usage: python scripts/start.py
"""

import os
import sys
from pathlib import Path


def main() -> None:
    """Launch IRIS from the checkout without side effects when imported."""
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)
    # Keep the launcher usable in legacy Windows terminals using cp1252.
    print("Starting IRIS...")
    from core.agent import main as iris_main

    iris_main()


if __name__ == "__main__":
    main()
