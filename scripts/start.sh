#!/usr/bin/env bash
# IRIS OS — Linux Launch Script

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "◉  Starting IRIS..."

# Activate venv if exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Start IRIS
python -m core.agent "$@"
