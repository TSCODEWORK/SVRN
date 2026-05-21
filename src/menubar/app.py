#!/usr/bin/env python3
"""
SVRN Menubar App
Sits in the macOS menu bar, shows service status, opens dashboard on click.
Requires: pip install rumps

Paths: zero hardcoded — reads from ~/.config/svrn/ports.json at runtime.
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import rumps

# Allow running from app bundle or dev mode
import sys
_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from config import get_port, get_storage_root, find_ollama, get_config, SVRN_CONFIG

_log = logging.getLogger(__name__)

POLL_INTERVAL = 12  # seconds


def _dashboard_url() -> str:
    return f"http://localhost:{get_port('dashboard')}"


def _kiwix_url() -> str:
    return f"http://localhost:{get_port('kiwix')}"


def _port_open(port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def _fetch_json(url: str, timeout: float = 2.0):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SVRNMenubar"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _storage_info() -> dict:
    storage = get_storage_root()
    if not storage or not storage.exists():
        return {"connected": False}
    try:
        u = shutil.disk_usage(storage)
        return {
            "connected": True,
            "free_gb":   round(u.free  / 1e9, 1),
            "total_gb":  round(u.total / 1e9, 1),
            "path":      str(storage),
        }
    except Exception:
        return {"connected": True, "path": str(storage)}


def _power_info() -> dict:
    try:
        out = subprocess.run(
            ["pmset", "-g", "ps"], capture_output=True, text=True, timeout=2
        ).stdout
        import re
        on_ac = "AC Power" in out
        m     = re.search(r"(\d+)%", out)
        pct   = int(m.group(1)) if m else None
        return {"ac": on_ac, "pct": pct}
    except Exception:
        return {"ac": False, "pct": None}


class SVRNApp(rumps.App):
    def __init__(self):
        super().__init__("◉", quit_button=None)
        self._lock = threading.Lock()

        dash = _dashboard_url()

        self._title_item  = rumps.MenuItem("SVRN",          callback=lambda _: webbrowser.open(dash))
        self._storage_item = rumps.MenuItem("💾  Storage: checking…")
        self._power_item  = rumps.MenuItem("⚡  Power: checking…")
        self._sep1        = rumps.separator

        self._ollama_item = rumps.MenuItem("🤖  AI: checking…")
        self._library_item = rumps.MenuItem("📖  Library: checking…",
                                            callback=lambda _: webbrowser.open(dash + "/library"))
        self._maps_item   = rumps.MenuItem("🗺️  Maps",
                                           callback=lambda _: webbrowser.open(dash + "/maps"))
        self._sep2        = rumps.separator

        self._open_dash   = rumps.MenuItem("Open SVRN Dashboard",
                                           callback=lambda _: webbrowser.open(dash))
        self._open_chat   = rumps.MenuItem("Open AI Chat",
                                           callback=lambda _: webbrowser.open(dash + "/chat"))
        self._reload_zim  = rumps.MenuItem("Reload ZIM Libraries",
                                           callback=self._do_reload_zim)
        self._sep3        = rumps.separator
        self._quit_item   = rumps.MenuItem("Quit SVRN", callback=rumps.quit_application)

        self.menu = [
            self._title_item,
            self._storage_item,
            self._power_item,
            self._sep1,
            self._ollama_item,
            self._library_item,
            self._maps_item,
            self._sep2,
            self._open_dash,
            self._open_chat,
            self._reload_zim,
            self._sep3,
            self._quit_item,
        ]

        # Start background poller
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        while True:
            try:
                self._update()
            except Exception:
                _log.warning("Status poll failed", exc_info=True)
            time.sleep(POLL_INTERVAL)

    def _update(self):
        # Grab dynamic ports
        dash_port   = get_port("dashboard")
        kiwix_port  = get_port("kiwix")
        ollama_port = get_config("ollama_port", 11434)

        # Service checks
        dash_up   = _port_open(dash_port)
        kiwix_up  = _port_open(kiwix_port)
        ollama_up = _port_open(ollama_port)

        # Storage
        si = _storage_info()
        if si["connected"]:
            free = si.get("free_gb", "?")
            total = si.get("total_gb", "?")
            storage_text = f"💾  {free} GB free of {total} GB"
        else:
            storage_text = "💾  No storage configured"

        # Power
        pi = _power_info()
        if pi["ac"]:
            power_text = "⚡  AC Power"
        elif pi["pct"] is not None:
            icon = "🔋" if pi["pct"] > 20 else "🪫"
            power_text = f"{icon}  Battery {pi['pct']}%"
        else:
            power_text = "⚡  Power unknown"

        # Ollama
        if ollama_up:
            tags = _fetch_json(f"http://127.0.0.1:{ollama_port}/api/tags")
            if tags:
                model_count = len(tags.get("models", []))
                ollama_text = f"🤖  AI: {model_count} model{'s' if model_count != 1 else ''} ready"
            else:
                ollama_text = "🤖  AI: running"
        elif find_ollama():
            ollama_text = "🤖  AI: installed, not running"
        else:
            ollama_text = "🤖  AI: Ollama not installed"

        # Library
        if kiwix_up:
            archives = _fetch_json(f"http://127.0.0.1:{kiwix_port}/api/archives")
            if archives is not None:
                count = len(archives)
                library_text = f"📖  Library: {count} collection{'s' if count != 1 else ''}"
            else:
                library_text = "📖  Library: running"
        else:
            library_text = "📖  Library: offline"

        # Title bar icon
        if dash_up and kiwix_up:
            icon = "◉"
        elif dash_up or kiwix_up:
            icon = "◎"
        else:
            icon = "○"

        # Apply on main thread
        rumps.Timer(lambda _: self._apply_update(
            icon, storage_text, power_text, ollama_text, library_text
        ), 0).start()

    def _apply_update(self, icon, storage_text, power_text, ollama_text, library_text):
        self.title = icon
        self._storage_item.title  = storage_text
        self._power_item.title    = power_text
        self._ollama_item.title   = ollama_text
        self._library_item.title  = library_text

    def _do_reload_zim(self, _):
        try:
            kiwix_port = get_port("kiwix")
            urllib.request.urlopen(
                f"http://127.0.0.1:{kiwix_port}/reload", timeout=3
            )
            rumps.notification("SVRN", "Library reloaded", "ZIM archives rescanned.")
        except Exception as e:
            rumps.notification("SVRN", "Reload failed", str(e))


def main():
    SVRNApp().run()


if __name__ == "__main__":
    main()
