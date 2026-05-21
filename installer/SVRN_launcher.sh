#!/bin/bash
# SVRN.app/Contents/MacOS/SVRN
#
# This script is the executable entry point for the macOS app bundle.
# It locates the bundled Python runtime and runs the launcher.
#
# Path layout inside the bundle:
#   Contents/MacOS/SVRN           ← this script
#   Contents/Resources/python/    ← bundled Python runtime
#   Contents/Resources/src/       ← SVRN source (config, dashboard, kiwix, menubar)
#   Contents/Resources/launcher/  ← launch.py

set -e

# Resolve the bundle root (Contents/) regardless of how this script was invoked
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
MACOS_DIR="$(dirname "$SCRIPT_PATH")"
BUNDLE_CONTENTS="$(dirname "$MACOS_DIR")"
RESOURCES="$BUNDLE_CONTENTS/Resources"

PYTHON="$RESOURCES/python/bin/python3"
LAUNCHER="$RESOURCES/launcher/launch.py"

# Verify the bundled Python exists
if [ ! -f "$PYTHON" ]; then
    osascript -e 'tell application "System Events" to display alert "SVRN Error" message "Bundled Python runtime not found. Please reinstall SVRN." as critical'
    exit 1
fi

# Set PYTHONPATH so config/dashboard/kiwix modules find each other
export PYTHONPATH="$RESOURCES/src"

# Set HOME explicitly (should already be set, but be safe)
export HOME="${HOME:-/Users/$(id -un)}"

# Launch — pass all args so --no-browser works during development
exec "$PYTHON" "$LAUNCHER" "$@"
