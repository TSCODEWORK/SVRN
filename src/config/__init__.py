"""
SVRN Config — centralised path and configuration management.

All user-specific and machine-specific paths are resolved here.
Nothing outside this module should hardcode a username, volume name,
or absolute path that doesn't start with a runtime variable.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path

_log = logging.getLogger(__name__)

# ── Well-known locations (never hardcoded usernames) ─────────────────────────

HOME         = Path.home()
SVRN_CONFIG  = HOME / ".config" / "svrn"
CONFIG_FILE  = SVRN_CONFIG / "config.json"
PORTS_FILE   = SVRN_CONFIG / "ports.json"
NOTES_FILE   = SVRN_CONFIG / "notes" / "notes.json"
SETTINGS_FILE = SVRN_CONFIG / "settings.json"

# ── Default port primaries (fallback logic handled by bind_port()) ────────────

DEFAULT_PORTS = {
    "dashboard": 3333,
    "kiwix":     8888,
}


# ── Config read/write ─────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load ~/.config/svrn/config.json — returns {} if missing or corrupt."""
    try:
        return json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        _log.warning("Failed to load config from %s", CONFIG_FILE, exc_info=True)
        return {}


def _save_config(data: dict) -> None:
    SVRN_CONFIG.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def get_storage_root() -> Path | None:
    """Return the user's chosen storage root, or None if not yet configured."""
    cfg = _load_config()
    p = cfg.get("storage_root")
    if p:
        path = Path(p)
        if path.exists():
            return path
    return None


def set_storage_root(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"Storage path does not exist: {path}")
    cfg = _load_config()
    cfg["storage_root"] = str(path)
    _save_config(cfg)


def get_config(key: str, default=None):
    return _load_config().get(key, default)


def set_config(key: str, value) -> None:
    cfg = _load_config()
    cfg[key] = value
    _save_config(cfg)


# ── Storage layout (all relative to user's chosen root) ──────────────────────

def zim_dirs(root: Path | None = None) -> list[Path]:
    """All directories to scan for .zim files."""
    r = root or get_storage_root()
    if not r:
        return []
    dirs = [r / "zim"]
    # Legacy sub-directory names carried over from sovereign
    for sub in ("Wikipedia", "Medical", "Education", "Books", "kiwix"):
        dirs.append(r / sub)
    return [d for d in dirs if d.exists()]


def maps_dir(root: Path | None = None) -> Path | None:
    r = root or get_storage_root()
    return (r / "maps") if r else None


def chat_sessions_dir(root: Path | None = None) -> Path | None:
    r = root or get_storage_root()
    if not r:
        return SVRN_CONFIG / "chat" / "sessions"
    return r / "chat" / "sessions"


def notes_dir(root: Path | None = None) -> Path | None:
    r = root or get_storage_root()
    return (r / "notes") if r else (SVRN_CONFIG / "notes")


# ── Ollama detection ──────────────────────────────────────────────────────────

_OLLAMA_CANDIDATES = [
    Path("/usr/local/bin/ollama"),                                    # Intel Homebrew
    Path("/opt/homebrew/bin/ollama"),                                  # Apple Silicon Homebrew
    HOME / ".ollama" / "bin" / "ollama",                              # Direct install
    Path("/Applications/Ollama.app/Contents/MacOS/Ollama"),           # App bundle
]


def find_ollama() -> Path | None:
    """Return the first ollama binary found on this system, or None."""
    # Check user-configured override first
    override = get_config("ollama_path")
    if override:
        p = Path(override)
        if p.exists():
            return p
    return next((p for p in _OLLAMA_CANDIDATES if p.exists()), None)


# ── Port binding with fallback ────────────────────────────────────────────────

def bind_port(service: str, preferred: int | None = None) -> tuple[socket.socket, int]:
    """
    Try to bind a TCP socket to preferred port (or DEFAULT_PORTS[service]).
    Falls back to preferred+1, preferred+2 on EADDRINUSE.

    Returns (bound_socket, port).
    Caller is responsible for closing or handing the socket to HTTPServer.
    Chosen port is written to ~/.config/svrn/ports.json.
    """
    primary = preferred or DEFAULT_PORTS.get(service, 8000)
    last_exc = None
    for port in (primary, primary + 1, primary + 2):
        sock = None  # ensure defined before try so except can safely close it
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(128)
            _record_port(service, port)
            return sock, port
        except OSError as e:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            last_exc = e
    raise RuntimeError(
        f"Could not bind {service} to any of ports "
        f"{primary}–{primary + 2}: {last_exc}"
    )


def _record_port(service: str, port: int) -> None:
    SVRN_CONFIG.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(PORTS_FILE.read_text()) if PORTS_FILE.exists() else {}
    except Exception:
        _log.warning("Failed to read ports file, starting fresh", exc_info=True)
        existing = {}
    existing[service] = port
    try:
        PORTS_FILE.write_text(json.dumps(existing, indent=2))
    except Exception:
        _log.warning("Failed to write ports file", exc_info=True)


def get_port(service: str) -> int:
    """Look up the port a service is currently running on."""
    try:
        data = json.loads(PORTS_FILE.read_text())
        return data[service]
    except FileNotFoundError:
        pass
    except Exception:
        _log.warning("Failed to read port for %s", service, exc_info=True)
    return DEFAULT_PORTS.get(service, 8000)
