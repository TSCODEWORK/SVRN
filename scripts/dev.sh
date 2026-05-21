#!/bin/bash
# SVRN development launcher
# Usage: ./scripts/dev.sh [--no-browser]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."

# Use venv if present, otherwise fall back to system Python
if [ -f "$ROOT/.venv/bin/python3" ]; then
    PYTHON="$ROOT/.venv/bin/python3"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    echo "ERROR: No Python found."
    echo "  Create a virtualenv first: python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

echo "Starting SVRN in development mode…"
echo "Python: $PYTHON"
echo "Root:   $ROOT"

PYTHONPATH="$ROOT/src" exec "$PYTHON" "$ROOT/launcher/launch.py" "$@"
