#!/usr/bin/env python3
"""
SVRN Launcher
Starts all background services and opens the dashboard in the default browser.

This script is the entry point for the SVRN.app bundle and also works
when run directly from the terminal for development.

Usage:
    python3 launcher/launch.py [--no-browser]
"""

import logging
import os
import sys
import time
import signal
import subprocess
import threading
import traceback
import webbrowser
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("launcher")

# ── Resolve paths relative to this launcher ──────────────────────────────────
# Works whether running from:
#   - SVRN.app/Contents/MacOS/SVRN (launcher binary calls this)
#   - python3 launcher/launch.py (dev mode)
#   - any absolute path

LAUNCHER_DIR = Path(__file__).resolve().parent

# In the .app bundle, layout is:
#   Contents/Resources/launcher/launch.py  ← this file
#   Contents/Resources/src/               ← source modules
#   Contents/Resources/python/            ← bundled Python
#
# In dev mode, layout is:
#   launcher/launch.py
#   src/
APP_ROOT = LAUNCHER_DIR.parent   # Contents/Resources/ or repo root

# Support both layouts
if (APP_ROOT / "src").exists():
    SRC_DIR = APP_ROOT / "src"
else:
    SRC_DIR = APP_ROOT   # flat dev layout fallback

PYTHON = sys.executable

# Inject src/ into path so config/dashboard/kiwix modules are importable
for p in (str(SRC_DIR), str(APP_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from config import SVRN_CONFIG, get_storage_root, get_port, find_ollama
except ImportError as e:
    print(f"FATAL: Cannot import config module: {e}")
    traceback.print_exc()
    print(f"  SRC_DIR: {SRC_DIR}")
    print(f"  sys.path: {sys.path[:3]}")
    sys.exit(1)

# ── Service definitions ───────────────────────────────────────────────────────

SERVICES = [
    {
        "name":   "dashboard",
        "script": SRC_DIR / "dashboard" / "server.py",
        "env":    {"PYTHONPATH": str(SRC_DIR)},
    },
    {
        "name":   "kiwix",
        "script": SRC_DIR / "kiwix" / "server.py",
        "env":    {"PYTHONPATH": str(SRC_DIR)},
    },
]

_procs: list = []
_restart_counts: dict = {}  # service name → consecutive crash count


def _start_service(svc: dict) -> subprocess.Popen:
    env = {**os.environ}
    env["PYTHONPATH"] = str(SRC_DIR)
    env.update(svc.get("env", {}))
    proc = subprocess.Popen(
        [PYTHON, str(svc["script"])],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(f"  Started {svc['name']} (pid {proc.pid})")
    return proc


def _log_service(name: str, proc: subprocess.Popen):
    """Stream service stdout to our own stdout with a prefix tag."""
    for line in proc.stdout:
        print(f"[{name}] {line.decode('utf-8', errors='replace').rstrip()}")


def _wait_for_dashboard(timeout: int = 15) -> bool:
    """Poll until the dashboard is accepting connections."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            port = get_port("dashboard")
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def _stop_all(signum=None, frame=None):
    print("\nSVRN: Stopping all services…")
    for proc in _procs:
        try:
            proc.terminate()
        except Exception:
            pass
    for proc in _procs:
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    sys.exit(0)


def needs_setup() -> bool:
    """Return True if the user hasn't completed first-run setup."""
    return get_storage_root() is None


def open_setup_wizard():
    """Open the setup wizard page in the browser."""
    port = get_port("dashboard")
    webbrowser.open(f"http://localhost:{port}/setup")


def main():
    global _procs
    no_browser = "--no-browser" in sys.argv

    print("=" * 50)
    print("  SVRN — Offline Knowledge & AI")
    print("=" * 50)

    SVRN_CONFIG.mkdir(parents=True, exist_ok=True)

    # Register signal handlers for clean shutdown
    signal.signal(signal.SIGTERM, _stop_all)
    signal.signal(signal.SIGINT,  _stop_all)

    # Start services
    print("\nStarting services…")
    for svc in SERVICES:
        proc = _start_service(svc)
        _procs.append(proc)
        # Log output from each service in a background thread
        t = threading.Thread(
            target=_log_service, args=(svc["name"], proc), daemon=True
        )
        t.start()

    # Wait for dashboard to come up
    print("Waiting for dashboard…", end="", flush=True)
    ready = _wait_for_dashboard(timeout=20)
    if ready:
        print(" ready")
    else:
        print(" timeout — opening anyway")

    port = get_port("dashboard")

    if not no_browser:
        if needs_setup():
            print(f"\nFirst run — opening Setup Wizard at http://localhost:{port}/setup")
            time.sleep(1)  # give the page a moment to load
            webbrowser.open(f"http://localhost:{port}/setup")
        else:
            print(f"\nOpening dashboard at http://localhost:{port}")
            webbrowser.open(f"http://localhost:{port}")

    print("\nSVRN is running. Press Ctrl+C to stop.\n")

    # Monitor services — restart on unexpected exit with exponential backoff
    while True:
        for i, proc in enumerate(_procs):
            ret = proc.poll()
            if ret is not None:
                svc   = SERVICES[i]
                count = _restart_counts.get(svc["name"], 0)
                delay = min(5 * (2 ** count), 60)  # 5 → 10 → 20 → 40 → 60s cap
                _restart_counts[svc["name"]] = count + 1
                print(f"  [WARN] {svc['name']} exited (code {ret}) — restart #{count + 1} in {delay}s…")
                time.sleep(delay)
                new_proc = _start_service(svc)
                _procs[i] = new_proc
                t = threading.Thread(
                    target=_log_service, args=(svc["name"], new_proc), daemon=True
                )
                t.start()
        time.sleep(5)


if __name__ == "__main__":
    main()
