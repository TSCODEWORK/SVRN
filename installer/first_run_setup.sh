#!/bin/bash
# SVRN First-Run Setup Wizard
#
# This script runs inside a Terminal window on first launch.
# Variables SVRN_RESOURCES, SVRN_PYTHON, and SVRN_LAUNCHER are
# injected by SVRN_launcher.sh when it writes this script to /tmp/.
#
# NOT meant to be run directly — always invoked via SVRN.app.

set -euo pipefail

# ── Terminal appearance ───────────────────────────────────────────────────────

BOLD=$'\e[1m'
DIM=$'\e[2m'
RESET=$'\e[0m'
GREEN=$'\e[32m'
YELLOW=$'\e[33m'
RED=$'\e[31m'
CYAN=$'\e[36m'
WHITE=$'\e[97m'

_ok()   { echo "  ${GREEN}✓${RESET}  $*"; }
_warn() { echo "  ${YELLOW}⚠${RESET}  $*"; }
_err()  { echo "  ${RED}✗${RESET}  $*"; }
_hdr()  { echo; echo "${BOLD}${CYAN}$*${RESET}"; echo; }

clear

echo "${BOLD}${WHITE}"
cat << 'BANNER'
  ╔══════════════════════════════════════════════════════╗
  ║                                                      ║
  ║   ◉  SVRN — Offline Knowledge & AI                  ║
  ║      First-Run Setup                                 ║
  ║                                                      ║
  ╚══════════════════════════════════════════════════════╝
BANNER
echo "${RESET}"

echo "  Welcome! This wizard takes about 30 seconds and runs once."
echo "  After setup, SVRN opens automatically every time you launch the app."
echo

# ── Pre-flight checks ─────────────────────────────────────────────────────────

_hdr "Checking your system…"

# macOS version
MACOS_VER="$(sw_vers -productVersion)"
MACOS_MAJOR="${MACOS_VER%%.*}"
if [ "$MACOS_MAJOR" -lt 13 ] 2>/dev/null; then
    _err "macOS 13 Ventura or later is required (you have $MACOS_VER)"
    echo
    echo "  Please update macOS and try again."
    read -rp "  Press Return to close…" _
    exit 1
fi
_ok "macOS $MACOS_VER"

# Architecture
ARCH="$(uname -m)"
case "$ARCH" in
    arm64)  _ok "Apple Silicon (M-series)" ;;
    x86_64) _ok "Intel Mac" ;;
    *)      _warn "Unknown architecture: $ARCH — proceeding anyway" ;;
esac

# Bundled Python
if [ -f "$SVRN_PYTHON" ] && "$SVRN_PYTHON" --version &>/dev/null; then
    PY_VER="$("$SVRN_PYTHON" --version 2>&1)"
    _ok "Bundled Python ($PY_VER)"
else
    _err "Bundled Python not found at: $SVRN_PYTHON"
    echo
    echo "  SVRN.app may be corrupted. Please re-download and try again."
    read -rp "  Press Return to close…" _
    exit 1
fi

# ── Ollama detection ──────────────────────────────────────────────────────────

_hdr "Checking for Ollama (AI engine)…"

OLLAMA_PATHS=(
    "/usr/local/bin/ollama"
    "/opt/homebrew/bin/ollama"
    "$HOME/.ollama/bin/ollama"
    "/Applications/Ollama.app/Contents/Resources/ollama"
)

OLLAMA_BIN=""
for p in "${OLLAMA_PATHS[@]}"; do
    if [ -x "$p" ]; then
        OLLAMA_BIN="$p"
        break
    fi
done

if [ -n "$OLLAMA_BIN" ]; then
    _ok "Ollama found at $OLLAMA_BIN"
    echo "  ${DIM}AI chat will be available in SVRN.${RESET}"
else
    _warn "Ollama not found"
    echo
    echo "  ${DIM}SVRN works fully without Ollama — you can use the offline library,"
    echo "  maps, and notes right away. To add AI chat, install Ollama later at:${RESET}"
    echo "  ${CYAN}https://ollama.ai${RESET}"
    echo
    echo -n "  Open ollama.ai now? [y/N] "
    read -r _open_ollama
    if [[ "$_open_ollama" =~ ^[Yy]$ ]]; then
        open "https://ollama.ai"
    fi
fi

# ── Storage directory selection ───────────────────────────────────────────────

_hdr "Choose a storage location…"

echo "  SVRN stores your ZIM libraries, maps, notes, and chat history here."
echo "  You'll need a few GB free — more if you add large library collections."
echo
echo "  ${DIM}Tip: Choose a large external drive for maximum library capacity.${RESET}"
echo

DEFAULT_STORAGE="$HOME/SVRN"

while true; do
    echo -n "  Storage path [${DEFAULT_STORAGE}]: "
    read -r USER_PATH
    STORAGE_ROOT="${USER_PATH:-$DEFAULT_STORAGE}"

    # Expand ~ manually (read doesn't expand tildes)
    STORAGE_ROOT="${STORAGE_ROOT/#\~/$HOME}"

    # Check if path exists or can be created
    if [ -d "$STORAGE_ROOT" ]; then
        # Check free space (require at least 500 MB)
        FREE_KB="$(df -k "$STORAGE_ROOT" | awk 'NR==2 {print $4}')"
        if [ "${FREE_KB:-0}" -lt 512000 ]; then
            _warn "Less than 500 MB free at $STORAGE_ROOT"
            echo -n "  Use this location anyway? [y/N] "
            read -r _proceed
            [[ "$_proceed" =~ ^[Yy]$ ]] || continue
        fi
        _ok "Using existing directory: $STORAGE_ROOT"
        break
    else
        # Try to create it
        if mkdir -p "$STORAGE_ROOT" 2>/dev/null; then
            _ok "Created directory: $STORAGE_ROOT"
            break
        else
            _err "Cannot create directory: $STORAGE_ROOT"
            echo "  Please enter a different path."
        fi
    fi
done

# ── Create subdirectory structure ─────────────────────────────────────────────

_hdr "Setting up SVRN…"

mkdir -p \
    "$STORAGE_ROOT/zims" \
    "$STORAGE_ROOT/maps" \
    "$STORAGE_ROOT/notes" \
    "$STORAGE_ROOT/chat"
_ok "Created library folders (zims/, maps/, notes/, chat/)"

# ── Write config ──────────────────────────────────────────────────────────────

CONFIG_DIR="$HOME/.config/svrn"
CONFIG_FILE="$CONFIG_DIR/config.json"
mkdir -p "$CONFIG_DIR"

# Preserve any existing config values (e.g., ollama_port) and merge
EXISTING="{}"
if [ -f "$CONFIG_FILE" ]; then
    EXISTING="$(cat "$CONFIG_FILE")"
fi

# Use Python to merge (already verified it works above)
"$SVRN_PYTHON" - << PYEOF
import json, sys, pathlib

existing = {}
try:
    existing = json.loads("""$EXISTING""")
except Exception:
    pass

existing["storage_root"] = "$STORAGE_ROOT"
pathlib.Path("$CONFIG_FILE").write_text(json.dumps(existing, indent=2))
print("  ✓  Config saved to $CONFIG_FILE")
PYEOF

# ── Final checks and launch ───────────────────────────────────────────────────

echo
echo "${BOLD}${GREEN}  Setup complete!${RESET}"
echo
echo "  SVRN is starting — your dashboard will open in your browser."
echo "  ${DIM}Next time you open SVRN.app, it launches directly without this wizard.${RESET}"
echo

# Launch the Python services (dashboard + kiwix)
export PYTHONPATH="$SVRN_RESOURCES/src"
"$SVRN_PYTHON" "$SVRN_LAUNCHER" &
SVRN_PID=$!

# Wait for dashboard to be ready (up to 20s)
PORT_FILE="$HOME/.config/svrn/ports.json"
MAX_WAIT=20
WAITED=0
echo -n "  Waiting for dashboard"
while [ $WAITED -lt $MAX_WAIT ]; do
    if [ -f "$PORT_FILE" ]; then
        DASH_PORT="$("$SVRN_PYTHON" -c "import json; d=json.loads(open('$PORT_FILE').read()); print(d.get('dashboard',3333))" 2>/dev/null || echo 3333)"
        if nc -z 127.0.0.1 "$DASH_PORT" 2>/dev/null; then
            echo " ready"
            break
        fi
    fi
    echo -n "."
    sleep 1
    WAITED=$((WAITED + 1))
done
[ $WAITED -ge $MAX_WAIT ] && echo " (opening anyway)"

# Open Setup page in browser (first run — let user add ZIM files)
DASH_PORT="${DASH_PORT:-3333}"
open "http://localhost:${DASH_PORT}/setup"

echo
echo "  ${DIM}This Terminal window will close in 5 seconds…${RESET}"
sleep 5
