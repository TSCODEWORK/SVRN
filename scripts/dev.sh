#!/bin/bash
# SVRN development launcher
# Usage: ./scripts/dev.sh [--no-browser]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."

# Use system Python or venv if present
if [ -f "$ROOT/.venv/bin/python3" ]; then
    PYTHON="$ROOT/.venv/bin/python3"
else
    PYTHON="python3"
fi

echo "Starting SVRN in development mode…"
echo "Python: $PYTHON"
echo "Root:   $ROOT"

PYTHONPATH="$ROOT/src" exec "$PYTHON" "$ROOT/launcher/launch.py" "$@"
