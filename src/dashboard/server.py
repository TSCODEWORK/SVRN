#!/usr/bin/env python3
"""
SVRN Dashboard Server
Serves the dashboard UI and provides system/service status APIs,
vault info, offline maps, and a library download manager.

Port is auto-assigned (primary: 3333) with SO_REUSEADDR fallback.
Actual port written to ~/.config/svrn/ports.json.
"""

import base64
import html as _html
import logging
import mimetypes
import os
import re
import signal
import sys
import json
import tempfile
import time
import socket
import shutil
import subprocess
import threading
import uuid
import urllib.parse
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

_log = logging.getLogger(__name__)

# Maximum POST body size — guards against memory exhaustion from large uploads.
_MAX_POST_BYTES = 50 * 1024 * 1024  # 50 MB

# Allow running directly or imported from the app bundle
try:
    from config import (
        HOME, SVRN_CONFIG, NOTES_FILE, SETTINGS_FILE,
        get_storage_root, set_storage_root,
        zim_dirs, maps_dir, chat_sessions_dir, notes_dir,
        find_ollama, bind_port, get_port,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config import (
        HOME, SVRN_CONFIG, NOTES_FILE, SETTINGS_FILE,
        get_storage_root, set_storage_root,
        zim_dirs, maps_dir, chat_sessions_dir, notes_dir,
        find_ollama, bind_port, get_port,
    )

DASHBOARD_DIR = Path(__file__).parent

# ── ZIM library sub-directory names (searched in order, relative to storage root)
ZIM_SUBDIRS = ["zim", "Wikipedia", "Medical", "Education", "Books", "kiwix", "Survival"]

# ── Services checked for liveness ──
def _get_services():
    kiwix_port = get_port("kiwix")
    dash_port  = get_port("dashboard")
    return [
        {"id": "kiwix",   "name": "Library",  "port": kiwix_port, "path": "/health"},
        {"id": "maps",    "name": "Maps",      "port": dash_port,  "path": "/health"},
        {"id": "ollama",  "name": "Ollama AI", "port": 11434,      "path": "/api/tags"},
    ]

# ── Map download presets (protomaps extracts) ──
PROTOMAPS_SOURCE = "https://build.protomaps.com/20251201.pmtiles"

MAP_PRESETS = {
    "world_mini": {
        "name": "World (low zoom)",
        "desc": "Zooms 0–7 only — great for overview maps",
        "bbox": "-180,-85,180,85",
        "est_mb": 400,
    },
    "patagonia": {
        "name": "Patagonia (Chile)",
        "desc": "Aysén, Magallanes, Chilean Patagonia",
        "bbox": "-78.0,-57.5,-64.5,-37.0",
        "est_mb": 1900,
    },
    "chile": {
        "name": "Chile (Full)",
        "desc": "Complete territory of Chile",
        "bbox": "-77.0,-57.0,-64.0,-16.0",
        "est_mb": 4400,
    },
    "massachusetts": {
        "name": "Massachusetts",
        "desc": "MA state + immediate surrounds",
        "bbox": "-74.1,40.7,-69.3,43.4",
        "est_mb": 240,
    },
    "new_england": {
        "name": "New England",
        "desc": "MA, CT, RI, VT, NH, ME",
        "bbox": "-74.2,40.7,-66.4,48.0",
        "est_mb": 1150,
    },
    "los_lagos": {
        "name": "Los Lagos, Chile",
        "desc": "Región de Los Lagos — Puerto Montt, Osorno, Chiloé",
        "bbox": "-76.2,-44.5,-70.7,-40.0",
        "est_mb": 700,
    },
    "south_america": {
        "name": "South America",
        "desc": "Full South American continent",
        "bbox": "-83.0,-57.0,-33.0,14.0",
        "est_mb": 13500,
    },
}

# ── Map download state (single concurrent download) ──
_dl_lock  = threading.Lock()
_dl_state: dict = {"status": "idle"}


# ═══════════════════════════════════════════════════
# System Info
# ═══════════════════════════════════════════════════

def get_system_info():
    info = {}
    try:
        out = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3
        ).stdout
        info["power_source"] = (
            "AC" if "AC Power" in out else
            "Battery" if "Battery Power" in out else "Unknown"
        )
        m = re.search(r"(\d+)%", out)
        info["battery_pct"] = int(m.group(1)) if m else None
        info["charging"] = any(w in out.lower() for w in ("charging", "finishing charge"))
    except Exception:
        info.update(power_source="Unknown", battery_pct=None, charging=False)

    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        stats = {
            m.group(1).strip(): int(m.group(2))
            for m in (re.match(r"^(.+?):\s+(\d+)", ln) for ln in vm.split("\n")) if m
        }
        page = 16384
        total_raw = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True
        ).stdout.strip()
        total = int(total_raw) if total_raw else 1
        used = (stats.get("Pages active", 0) + stats.get("Pages wired down", 0)) * page
        info["ram_total_gb"] = round(total / 1e9, 1)
        info["ram_used_gb"]  = round(used  / 1e9, 1)
        info["ram_pct"]      = round(used / total * 100, 1)
    except Exception:
        info.update(ram_total_gb=None, ram_used_gb=None, ram_pct=None)

    try:
        load = os.getloadavg()
        ncpu = int(
            subprocess.run(
                ["sysctl", "-n", "hw.logicalcpu"], capture_output=True, text=True
            ).stdout.strip()
        )
        info["cpu_load_1m"] = round(load[0], 2)
        info["cpu_pct"]     = round(min(load[0] / ncpu * 100, 100), 1)
    except Exception:
        info.update(cpu_load_1m=None, cpu_pct=None)

    info["python_path"] = sys.executable

    return info


# ═══════════════════════════════════════════════════
# Vault / Storage Info
# ═══════════════════════════════════════════════════

def get_vault_info():
    storage = get_storage_root()
    if not storage:
        return {"connected": False, "reason": "No storage configured — run Setup Wizard"}

    info: dict = {"connected": True, "path": str(storage)}

    try:
        u = shutil.disk_usage(storage)
        info.update(
            total_gb=round(u.total / 1e9, 1),
            used_gb=round(u.used / 1e9, 1),
            free_gb=round(u.free / 1e9, 1),
            used_pct=round(u.used / u.total * 100, 1),
        )
    except Exception:
        pass

    # ZIM libraries — proxy from the kiwix server
    zim_by_dir: dict = {}
    all_zims: list = []
    try:
        kp = get_port("kiwix")
        req = urllib.request.Request(
            f"http://127.0.0.1:{kp}/api/archives",
            headers={"User-Agent": "SVRNDashboard"},
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            archives = json.loads(r.read())
        for arch in archives:
            name   = arch.get("name", "")
            subdir = arch.get("dir", "unknown")
            path   = arch.get("path", "")
            try:
                size = round(Path(path).stat().st_size / 1e9, 2) if path else 0
            except Exception:
                size = 0
            entry = {"name": name, "filename": name + ".zim", "size_gb": size, "dir": subdir}
            all_zims.append(entry)
            zim_by_dir.setdefault(subdir, []).append(entry)
    except Exception:
        pass

    info["zims"]      = zim_by_dir
    info["zims_all"]  = all_zims
    info["zim_count"] = len(all_zims)

    info["maps"] = [
        {"name": p["id"], "filename": p["id"] + ".pmtiles", "size_gb": p["size_on_disk_gb"] or 0}
        for p in get_maps_presets()
        if p["installed"]
    ]

    return info


# ═══════════════════════════════════════════════════
# Services
# ═══════════════════════════════════════════════════

def _port_open(port, timeout=0.8):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def _http_ok(port, path, timeout=2.0):
    if not _port_open(port, 0.5):
        return False
    try:
        url = f"http://127.0.0.1:{port}{path}"
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "SVRNDashboard"}),
            timeout=timeout,
        ) as r:
            return r.status < 500
    except Exception:
        return _port_open(port, 0.5)


def get_services_status():
    out = []
    for svc in _get_services():
        up = _http_ok(svc["port"], svc["path"]) if svc.get("path") else _port_open(svc["port"])
        out.append({**svc, "up": up, "url": f"http://localhost:{svc['port']}"})
    # Add Ollama status with binary detection
    ollama_bin = find_ollama()
    out.append({
        "id": "ollama_binary", "name": "Ollama Binary",
        "installed": ollama_bin is not None,
        "path": str(ollama_bin) if ollama_bin else None,
    })
    return out


# ═══════════════════════════════════════════════════
# Location
# ═══════════════════════════════════════════════════

def get_location_info():
    loc_file   = SVRN_CONFIG / "location"
    state_file = SVRN_CONFIG / "network_state"
    manual = loc_file.read_text().strip() if loc_file.exists() else None
    active = state_file.read_text().strip() if state_file.exists() else None

    tz = "unknown"
    try:
        tz_link = str(Path("/etc/localtime").resolve())
        parts   = tz_link.split("/")
        CONTINENTS = {"Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic",
                      "Australia", "Europe", "Indian", "Pacific"}
        for i, part in enumerate(parts):
            if part in CONTINENTS:
                tz = "/".join(parts[i:])
                break
    except Exception:
        pass

    return {
        "manual_override": manual,
        "active_region":   active or manual or "unknown",
        "timezone":        tz,
        "location_file":   str(loc_file),
    }


def set_location(region: str):
    if region not in ("chile", "massachusetts", "auto"):
        raise ValueError(f"Unknown region: {region}")
    SVRN_CONFIG.mkdir(parents=True, exist_ok=True)
    loc_file = SVRN_CONFIG / "location"
    if region == "auto":
        loc_file.unlink(missing_ok=True)
    else:
        loc_file.write_text(region)


# ═══════════════════════════════════════════════════
# Map Downloads
# ═══════════════════════════════════════════════════

def _pmtiles_cli():
    for p in ["/opt/homebrew/bin/pmtiles", shutil.which("pmtiles")]:
        if p and Path(p).exists():
            return p
    return None


def get_maps_presets():
    storage  = get_storage_root()
    mdir     = storage / "maps" if storage else None
    cli      = _pmtiles_cli()
    result   = []
    for pid, preset in MAP_PRESETS.items():
        file_path = mdir / f"{pid}.pmtiles" if mdir else None
        installed = bool(file_path and file_path.exists())
        size_gb   = round(file_path.stat().st_size / 1e9, 2) if installed else None
        result.append({
            "id": pid,
            "installed": installed,
            "size_on_disk_gb": size_gb,
            "dest_dir":  str(mdir) if mdir else None,
            "dest_file": str(file_path) if file_path else None,
            "storage_connected": storage is not None,
            "pmtiles_cli": bool(cli),
            **preset,
        })
    return result


def start_map_download(preset_id: str, source_url: str = ""):
    global _dl_state
    with _dl_lock:
        if _dl_state.get("status") == "running":
            raise RuntimeError("A download is already in progress")
        if preset_id not in MAP_PRESETS:
            raise ValueError(f"Unknown preset: {preset_id}")

        storage = get_storage_root()
        if not storage:
            raise RuntimeError("No storage configured — complete Setup Wizard first")

        cli = _pmtiles_cli()
        if not cli:
            raise RuntimeError("pmtiles CLI not found — run: brew install pmtiles")

        preset   = MAP_PRESETS[preset_id]
        out_dir  = storage / "maps"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{preset_id}.pmtiles"
        src      = source_url or PROTOMAPS_SOURCE

        _dl_state = {
            "status":      "running",
            "preset_id":   preset_id,
            "preset_name": preset["name"],
            "output":      str(out_file),
            "started":     time.time(),
            "log":         [],
            "pid":         None,
        }

    def _run():
        global _dl_state
        cmd = [cli, "extract", src, str(out_file), f"--bbox={preset['bbox']}"]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            with _dl_lock:
                _dl_state["pid"] = proc.pid

            lines = []
            for line in proc.stdout:
                lines.append(line.rstrip())
                with _dl_lock:
                    _dl_state["log"] = lines[-40:]

            proc.wait()
            with _dl_lock:
                if _dl_state.get("status") == "running":
                    if proc.returncode == 0:
                        _dl_state["status"] = "done"
                    else:
                        _dl_state["status"] = "error"
                        _dl_state["error"]  = f"pmtiles exited {proc.returncode}"
        except Exception as exc:
            with _dl_lock:
                if _dl_state.get("status") == "running":
                    _dl_state["status"] = "error"
                    _dl_state["error"]  = str(exc)

    threading.Thread(target=_run, daemon=True).start()


def cancel_map_download():
    global _dl_state
    with _dl_lock:
        pid = _dl_state.get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        _dl_state = {"status": "idle"}


def reset_map_download_state():
    global _dl_state
    with _dl_lock:
        if _dl_state.get("status") != "running":
            _dl_state = {"status": "idle"}


def get_download_status():
    with _dl_lock:
        return dict(_dl_state)


# ═══════════════════════════════════════════════════
# MapLibre GL Style Generator
# ═══════════════════════════════════════════════════

def _build_combined_style(installed_ids: list, override_urls: dict = None) -> dict:
    """Build a MapLibre GL style with ALL installed maps as simultaneous sources."""
    if not installed_ids:
        return {"version": 8, "sources": {}, "layers": [
            {"id": "background", "type": "background",
             "paint": {"background-color": "#b8d8ea"}}]}

    ordered = []
    if "world_mini" in installed_ids:
        ordered.append("world_mini")
    for mid in installed_ids:
        if mid != "world_mini":
            ordered.append(mid)

    dash_port = get_port("dashboard")

    sources = {}
    for mid in ordered:
        url = (override_urls or {}).get(mid) or \
              f"pmtiles://http://localhost:{dash_port}/api/maps/file/{mid}"
        sources[f"pm_{mid}"] = {
            "type":        "vector",
            "url":         url,
            "attribution": "© OpenStreetMap · Protomaps",
        }

    def layers_for(mid: str) -> list:
        s        = f"pm_{mid}"
        is_world = (mid == "world_mini")
        lbl_max  = 8 if is_world else 24
        road_min = 4 if is_world else 6

        base_layers = []
        if is_world:
            base_layers = [
                {"id": "earth-world_mini", "type": "fill", "source": s,
                 "source-layer": "earth",
                 "paint": {"fill-color": "#eae6dc", "fill-antialias": False}},
                {"id": "water-world_mini", "type": "fill", "source": s,
                 "source-layer": "water",
                 "paint": {"fill-color": "#b8d8ea", "fill-antialias": False}},
                {"id": "water-outline-world_mini", "type": "line", "source": s,
                 "source-layer": "water",
                 "paint": {"line-color": "#8ab8d0", "line-width": 0.4, "line-opacity": 0.7}},
            ]

        detail_layers = [
            {"id": f"landcover-{mid}", "type": "fill", "source": s,
             "source-layer": "landcover",
             "filter": ["in", ["get", "kind"],
                        ["literal", ["grass","park","garden","national_park","forest","wood"]]],
             "paint": {"fill-color": "#d4e8c8", "fill-opacity": 0.75}},
            {"id": f"landuse-{mid}", "type": "fill", "source": s,
             "source-layer": "landuse",
             "filter": ["in", ["get", "kind"],
                        ["literal", ["residential","commercial","industrial"]]],
             "paint": {"fill-color": "#ebe8e0", "fill-opacity": 0.6}},
            {"id": f"buildings-{mid}", "type": "fill", "source": s,
             "source-layer": "buildings", "minzoom": 14,
             "paint": {"fill-color": "#ddd8cc", "fill-outline-color": "#ccc6ba"}},
            {"id": f"roads-highway-{mid}", "type": "line", "source": s,
             "source-layer": "roads", "minzoom": road_min,
             "filter": ["in", ["get", "kind"], ["literal", ["highway","major_road"]]],
             "paint": {"line-color": "#c8b89a",
                       "line-width": ["interpolate",["linear"],["zoom"], 6,1, 12,3.5, 16,7]}},
            {"id": f"roads-minor-{mid}", "type": "line", "source": s,
             "source-layer": "roads", "minzoom": 11,
             "filter": ["in", ["get", "kind"], ["literal", ["minor_road","path","other"]]],
             "paint": {"line-color": "#d8d2c4",
                       "line-width": ["interpolate",["linear"],["zoom"], 11,0.5, 16,2.5]}},
            {"id": f"boundaries-country-{mid}", "type": "line", "source": s,
             "source-layer": "boundaries",
             "filter": ["==", ["get", "kind"], "country"],
             "paint": {"line-color": "#a09880", "line-width": 1.5,
                       "line-dasharray": [4, 2]}},
            {"id": f"boundaries-state-{mid}", "type": "line", "source": s,
             "source-layer": "boundaries", "minzoom": 5,
             "filter": ["==", ["get", "kind"], "region"],
             "paint": {"line-color": "#bbb4a4", "line-width": 0.8,
                       "line-dasharray": [3, 3]}},
            {"id": f"labels-country-{mid}", "type": "symbol", "source": s,
             "source-layer": "places", "maxzoom": lbl_max,
             "filter": ["==", ["get", "kind"], "country"],
             "layout": {"text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
                        "text-size": 14, "text-font": ["Noto Sans Medium"],
                        "text-max-width": 8, "text-transform": "uppercase",
                        "text-letter-spacing": 0.08},
             "paint": {"text-color": "#2c2820", "text-halo-color": "#f5f0e8",
                       "text-halo-width": 2}},
            {"id": f"labels-city-{mid}", "type": "symbol", "source": s,
             "source-layer": "places", "minzoom": 4, "maxzoom": lbl_max,
             "filter": ["in", ["get", "kind"], ["literal", ["city","town"]]],
             "layout": {"text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
                        "text-size": ["interpolate",["linear"],["zoom"], 5,11, 12,15],
                        "text-font": ["Noto Sans Regular"], "text-max-width": 10},
             "paint": {"text-color": "#3a3020", "text-halo-color": "#f5f0e8",
                       "text-halo-width": 1.8}},
            {"id": f"labels-village-{mid}", "type": "symbol", "source": s,
             "source-layer": "places", "minzoom": 10,
             "filter": ["in", ["get", "kind"], ["literal", ["village","hamlet","suburb"]]],
             "layout": {"text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
                        "text-size": 11, "text-font": ["Noto Sans Regular"],
                        "text-max-width": 8},
             "paint": {"text-color": "#5a5040", "text-halo-color": "#f5f0e8",
                       "text-halo-width": 1.5}},
            {"id": f"labels-road-{mid}", "type": "symbol", "source": s,
             "source-layer": "roads", "minzoom": 13,
             "filter": ["in", ["get", "kind"], ["literal", ["highway","major_road"]]],
             "layout": {"text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
                        "text-size": 11, "symbol-placement": "line",
                        "text-font": ["Noto Sans Regular"]},
             "paint": {"text-color": "#6a6050", "text-halo-color": "#f5f0e8",
                       "text-halo-width": 1.5}},
        ]

        return base_layers + detail_layers

    all_layers = [
        {"id": "background", "type": "background",
         "paint": {"background-color": "#b8d8ea"}},
    ]
    for mid in ordered:
        all_layers.extend(layers_for(mid))

    return {
        "version": 8,
        "name":    "SVRN Combined",
        "sources": sources,
        "layers":  all_layers,
        # Fonts served locally — fully offline
        "glyphs": f"http://localhost:{dash_port}/static/map-fonts/{{fontstack}}/{{range}}.pbf",
    }


# ═══════════════════════════════════════════════════
# Chat Session Management
# ═══════════════════════════════════════════════════

from datetime import datetime as _dt


def _get_sessions_dir() -> Path:
    d = chat_sessions_dir()
    if d:
        d.mkdir(parents=True, exist_ok=True)
    return d


def list_chat_sessions() -> list:
    d = _get_sessions_dir()
    if not d:
        return []
    sessions = []
    for f in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            sessions.append({
                "id":            data["id"],
                "title":         data.get("title", "Untitled"),
                "updated_at":    data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
            })
        except Exception:
            pass
    return sessions


def get_chat_session(session_id: str) -> dict:
    d = _get_sessions_dir()
    if not d:
        return {}
    f = d / f"{session_id}.json"
    return json.loads(f.read_text()) if f.exists() else {}


def save_chat_session(session: dict):
    d = _get_sessions_dir()
    if not d:
        return
    (d / f"{session['id']}.json").write_text(
        json.dumps(session, ensure_ascii=False, indent=2)
    )


def delete_chat_session(session_id: str) -> bool:
    d = _get_sessions_dir()
    if not d:
        return False
    f = d / f"{session_id}.json"
    if f.exists():
        f.unlink()
        return True
    return False


def new_chat_session() -> dict:
    session = {
        "id":         str(uuid.uuid4()),
        "title":      "New Chat",
        "created_at": _dt.utcnow().isoformat(),
        "updated_at": _dt.utcnow().isoformat(),
        "messages":   [],
    }
    save_chat_session(session)
    return session


# ═══════════════════════════════════════════════════
# Reader — article text extraction for AI context
# ═══════════════════════════════════════════════════

def _fetch_zim_article_text(zim_name: str, path: str) -> str:
    """Fetch and strip-to-text a ZIM article from the kiwix server."""
    if not zim_name:
        return ""
    kp  = get_port("kiwix")
    url = (
        f"http://127.0.0.1:{kp}/zim/"
        + urllib.parse.quote(zim_name, safe="")
        + "/"
        + urllib.parse.quote(path, safe="/")
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SVRN-reader/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<style[^>]*>.*?</style>",   " ", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = _html.unescape(raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:10000]


# ═══════════════════════════════════════════════════
# ZIM RAG Search
# ═══════════════════════════════════════════════════

def zim_rag_search(query: str, max_results: int = 4, excerpt_len: int = 600) -> list:
    """Search installed ZIM files using libzim fulltext index."""
    try:
        from libzim.reader import Archive
        from libzim.search import Query, Searcher
    except ImportError:
        return []

    kp         = get_port("kiwix")
    results    = []
    seen_titles = set()

    for base in zim_dirs():
        if len(results) >= max_results:
            break
        try:
            zim_files = list(base.glob("*.zim"))
        except Exception:
            continue
        for zim_path in zim_files:
            if len(results) >= max_results:
                break
            try:
                arch = Archive(str(zim_path))
                if not arch.has_fulltext_index:
                    continue
                searcher = Searcher(arch)
                q        = Query().set_query(query)
                search   = searcher.search(q)
                sr       = search.getResults(0, 2)
                source_name = " ".join(zim_path.stem.split("_")[:2]).title()
                for path in sr:
                    if len(results) >= max_results:
                        break
                    try:
                        entry = arch.get_entry_by_path(path)
                        while entry.is_redirect:
                            entry = entry.get_redirect_entry()
                        title = entry.title
                        if title in seen_titles:
                            continue
                        seen_titles.add(title)
                        raw     = bytes(entry.get_item().content).decode("utf-8", errors="replace")
                        text    = re.sub(r'\s+', ' ',
                                  re.sub(r'<[^>]+>', ' ', raw)).strip()
                        excerpt = text[:excerpt_len]
                        url     = f"http://localhost:{kp}/zim/{zim_path.stem}/{path}"
                        results.append({
                            "title":   title,
                            "source":  source_name,
                            "excerpt": excerpt,
                            "url":     url,
                        })
                    except Exception:
                        continue
            except Exception:
                continue

    return results


# ═══════════════════════════════════════════════════
# File Text Extraction
# ═══════════════════════════════════════════════════

def extract_file_text(data: bytes, filename: str, mime_type: str = "") -> str:
    fname = filename.lower()
    if fname.endswith((".txt",".md",".csv",".json",".xml",".html",".htm",".rst")):
        try:
            return data.decode("utf-8", errors="replace")[:8000]
        except Exception:
            return ""
    if fname.endswith(".pdf") or "pdf" in mime_type:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            out_path = tmp_path.replace(".pdf", ".txt")
            subprocess.run(
                ["textutil", "-convert", "txt", "-output", out_path, tmp_path],
                capture_output=True, timeout=15,
            )
            os.unlink(tmp_path)
            if os.path.exists(out_path):
                with open(out_path) as f:
                    text = f.read()
                os.unlink(out_path)
                return text[:8000]
        except Exception:
            _log.warning("PDF text extraction failed for %s", fname, exc_info=True)
        return "[PDF content could not be extracted]"
    if fname.endswith((".jpg",".jpeg",".png",".gif",".webp",".bmp")):
        return f"[IMAGE:{base64.b64encode(data).decode()}]"
    try:
        return data.decode("utf-8", errors="replace")[:4000]
    except Exception:
        return ""


# ═══════════════════════════════════════════════════
# Notes
# ═══════════════════════════════════════════════════

def _notes_file() -> Path:
    d = notes_dir()
    if d:
        d.mkdir(parents=True, exist_ok=True)
        return d / "notes.json"
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    return NOTES_FILE


def _load_notes_raw() -> list:
    f = _notes_file()
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except Exception:
        return []


def _save_notes_raw(notes: list):
    _notes_file().write_text(json.dumps(notes, indent=2))


def get_notes() -> list:
    notes = _load_notes_raw()
    notes.sort(key=lambda n: n.get("updated_at", n.get("created_at", 0)), reverse=True)
    return notes


def get_note(note_id: str) -> dict:
    for n in _load_notes_raw():
        if n["id"] == note_id:
            return n
    return None


def create_note(title: str, content: str) -> dict:
    notes = _load_notes_raw()
    now   = time.time()
    note  = {"id": str(uuid.uuid4()), "title": title, "content": content,
             "created_at": now, "updated_at": now}
    notes.insert(0, note)
    _save_notes_raw(notes)
    return note


def update_note(note_id: str, title: str, content: str) -> dict:
    notes = _load_notes_raw()
    for n in notes:
        if n["id"] == note_id:
            n["title"]      = title
            n["content"]    = content
            n["updated_at"] = time.time()
            _save_notes_raw(notes)
            return n
    return None


def delete_note(note_id: str) -> bool:
    notes  = _load_notes_raw()
    before = len(notes)
    notes  = [n for n in notes if n["id"] != note_id]
    if len(notes) < before:
        _save_notes_raw(notes)
        return True
    return False


def clear_notes():
    _save_notes_raw([])


# ═══════════════════════════════════════════════════
# Settings
# ═══════════════════════════════════════════════════

SETTINGS_DEFAULTS = {
    "map_source":       "https://build.protomaps.com/20251201.pmtiles",
    "map_threads":      4,
    "ollama_url":       "http://localhost:11434",
    "refresh_interval": 10,
    "show_power":       True,
}


def get_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return {**SETTINGS_DEFAULTS, **json.loads(SETTINGS_FILE.read_text())}
        except Exception:
            pass
    return dict(SETTINGS_DEFAULTS)


def save_settings(data: dict):
    SVRN_CONFIG.mkdir(parents=True, exist_ok=True)
    merged = {**SETTINGS_DEFAULTS, **data}
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2))
    return merged


# ═══════════════════════════════════════════════════
# File API (Code Assistant)
# ═══════════════════════════════════════════════════

_FILE_ROOTS = [HOME]


def _file_path_safe(raw: str) -> Path | None:
    try:
        p = Path(raw).expanduser().resolve()
    except Exception:
        return None
    for root in _FILE_ROOTS:
        try:
            p.relative_to(root.resolve())
            return p
        except ValueError:
            continue
    return None


def file_read(raw_path: str) -> dict:
    p = _file_path_safe(raw_path)
    if p is None:       return {"error": "path not allowed"}
    if not p.exists():  return {"error": "file not found"}
    if p.is_dir():      return {"error": "path is a directory"}
    try:
        return {"path": str(p), "content": p.read_text(errors="replace"), "size": p.stat().st_size}
    except Exception as e:
        return {"error": str(e)}


def file_write(raw_path: str, content: str) -> dict:
    p = _file_path_safe(raw_path)
    if p is None:
        return {"error": "path not allowed"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"path": str(p), "size": p.stat().st_size, "ok": True}
    except Exception as e:
        return {"error": str(e)}


def file_list(raw_path: str) -> dict:
    p = _file_path_safe(raw_path)
    if p is None:       return {"error": "path not allowed"}
    if not p.exists():  return {"error": "path not found"}
    if not p.is_dir():  return {"error": "not a directory"}
    try:
        items = [
            {"name": c.name, "path": str(c),
             "type": "dir" if c.is_dir() else "file",
             "size": c.stat().st_size if c.is_file() else None}
            for c in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        ]
        return {"path": str(p), "items": items}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════
# Content Catalog
# ═══════════════════════════════════════════════════

CONTENT_CATALOG = {
    "wikipedia_en_all_nopic": {
        "name": "Wikipedia (English, text only)",
        "desc": "Complete English Wikipedia — 6M+ articles, no images.",
        "category": "Reference", "icon": "📖", "size_gb": 52,
        "url": "https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_nopic_2026-03.zim",
        "filename": "wikipedia_en_all_nopic_2026-03.zim", "dest_subdir": "Wikipedia",
    },
    "wiktionary_en": {
        "name": "Wiktionary (English)",
        "desc": "Dictionary, thesaurus, and etymology for 6M+ words.",
        "category": "Reference", "icon": "📝", "size_gb": 3,
        "url": "https://download.kiwix.org/zim/wiktionary/wiktionary_en_all_nopic_2026-05.zim",
        "filename": "wiktionary_en_all_nopic_2026-05.zim", "dest_subdir": "Books",
    },
    "wikivoyage_en": {
        "name": "Wikivoyage (English)",
        "desc": "Comprehensive travel guide — destinations, culture, practical info.",
        "category": "Reference", "icon": "✈️", "size_gb": 0.23,
        "url": "https://download.kiwix.org/zim/wikivoyage/wikivoyage_en_all_nopic_2026-03.zim",
        "filename": "wikivoyage_en_all_nopic_2026-03.zim", "dest_subdir": "Books",
    },
    "wikiversity_en": {
        "name": "Wikiversity (English)",
        "desc": "Open learning community — courses, lectures, and study materials.",
        "category": "Reference", "icon": "🏫", "size_gb": 2,
        "url": "https://download.kiwix.org/zim/wikiversity/wikiversity_en_all_nopic_2026-05.zim",
        "filename": "wikiversity_en_all_nopic_2026-05.zim", "dest_subdir": "Education",
    },
    "wikipedia_en_medicine_maxi": {
        "name": "Wikipedia Medicine",
        "desc": "Medical-focus Wikipedia — drugs, conditions, procedures.",
        "category": "Medical", "icon": "🏥", "size_gb": 2.2,
        "url": "https://download.kiwix.org/zim/wikipedia/wikipedia_en_medicine_maxi_2026-04.zim",
        "filename": "wikipedia_en_medicine_maxi_2026-04.zim", "dest_subdir": "Wikipedia",
    },
    "medlineplus_en": {
        "name": "MedlinePlus",
        "desc": "17,000+ trusted health articles from the US National Library of Medicine.",
        "category": "Medical", "icon": "🩺", "size_gb": 2,
        "url": "https://download.kiwix.org/zim/other/medlineplus.gov_en_all_2025-01.zim",
        "filename": "medlineplus.gov_en_all_2025-01.zim", "dest_subdir": "Medical",
    },
    "ifixit_en": {
        "name": "iFixit Repair Guides",
        "desc": "Step-by-step repair guides for electronics, appliances, and vehicles.",
        "category": "Medical", "icon": "🔧", "size_gb": 3.6,
        "url": "https://download.kiwix.org/zim/ifixit/ifixit_en_all_2025-12.zim",
        "filename": "ifixit_en_all_2025-12.zim", "dest_subdir": "Medical",
    },
    "nhs_medicines": {
        "name": "NHS Medicines A–Z",
        "desc": "Dosages, side effects, and interactions from the UK NHS.",
        "category": "Medical", "icon": "💊", "size_gb": 0.02,
        "url": "https://download.kiwix.org/zim/other/nhs.uk_en_medicines_2025-12.zim",
        "filename": "nhs.uk_en_medicines_2025-12.zim", "dest_subdir": "Medical",
    },
    "zimgit_medicine": {
        "name": "Field & Emergency Medicine",
        "desc": "Field medicine, trauma, and emergency care manuals.",
        "category": "Medical", "icon": "🏕️", "size_gb": 0.07,
        "url": "https://download.kiwix.org/zim/other/zimgit-medicine_en_2024-08.zim",
        "filename": "zimgit-medicine_en_2024-08.zim", "dest_subdir": "Medical",
    },
    "stackoverflow_en": {
        "name": "Stack Overflow",
        "desc": "Top-voted programming Q&A — invaluable for coding offline.",
        "category": "Technical", "icon": "💻", "size_gb": 55,
        "url": "https://download.kiwix.org/zim/stack_exchange/stackoverflow.com_en_all_2023-11.zim",
        "filename": "stackoverflow.com_en_all_2023-11.zim", "dest_subdir": "Education",
    },
    "devdocs_python": {
        "name": "DevDocs — Python",
        "desc": "Full Python 3 language and standard library documentation.",
        "category": "Technical", "icon": "🐍", "size_gb": 0.1,
        "url": "https://download.kiwix.org/zim/devdocs/devdocs_en_python_2026-05.zim",
        "filename": "devdocs_en_python_2026-05.zim", "dest_subdir": "Education",
    },
    "devdocs_javascript": {
        "name": "DevDocs — JavaScript",
        "desc": "Full JavaScript language reference and MDN Web APIs documentation.",
        "category": "Technical", "icon": "🟨", "size_gb": 0.1,
        "url": "https://download.kiwix.org/zim/devdocs/devdocs_en_javascript_2026-04.zim",
        "filename": "devdocs_en_javascript_2026-04.zim", "dest_subdir": "Education",
    },
    "freecodecamp_en": {
        "name": "freeCodeCamp",
        "desc": "Full freeCodeCamp curriculum — web dev, Python, data science.",
        "category": "Education", "icon": "🖥️", "size_gb": 0.01,
        "url": "https://download.kiwix.org/zim/other/freecodecamp_en_all_2026-02.zim",
        "filename": "freecodecamp_en_all_2026-02.zim", "dest_subdir": "Education",
    },
    "wikibooks_en_nopic": {
        "name": "Wikibooks (text only)",
        "desc": "118,000+ open-content textbooks — cooking, languages, science, and more.",
        "category": "Reference", "icon": "📗", "size_gb": 3.1,
        "url": "https://download.kiwix.org/zim/wikibooks/wikibooks_en_all_nopic_2026-01.zim",
        "filename": "wikibooks_en_all_nopic_2026-01.zim", "dest_subdir": "Education",
    },
    "gutenberg_en": {
        "name": "Project Gutenberg (English)",
        "desc": "70,000+ free classic books — literature, philosophy, history, science.",
        "category": "Books", "icon": "📚", "size_gb": 60,
        "url": "https://download.kiwix.org/zim/gutenberg/gutenberg_en_all_2025-11.zim",
        "filename": "gutenberg_en_all_2025-11.zim", "dest_subdir": "Books",
    },
    "based_cooking": {
        "name": "based.cooking",
        "desc": "500+ no-frills recipes — simple, practical cooking.",
        "category": "Books", "icon": "🍳", "size_gb": 0.02,
        "url": "https://lbo.download.kiwix.org/zim/zimit/based.cooking_en_all_2026-02.zim",
        "filename": "based.cooking_en_all_2026-02.zim", "dest_subdir": "Books",
    },
    "stackexchange_cooking": {
        "name": "Cooking Q&A (Seasoned Advice)",
        "desc": "59,000+ Q&A for cooks — techniques, ingredients, recipes.",
        "category": "Books", "icon": "👨‍🍳", "size_gb": 0.24,
        "url": "https://download.kiwix.org/zim/stack_exchange/cooking.stackexchange.com_en_all_2026-02.zim",
        "filename": "cooking.stackexchange.com_en_all_2026-02.zim", "dest_subdir": "Books",
    },
    "stackexchange_diy": {
        "name": "Home Improvement Q&A",
        "desc": "165,000+ Q&A for home repair, renovation, and construction.",
        "category": "Technical", "icon": "🏠", "size_gb": 2,
        "url": "https://download.kiwix.org/zim/stack_exchange/diy.stackexchange.com_en_all_2026-02.zim",
        "filename": "diy.stackexchange.com_en_all_2026-02.zim", "dest_subdir": "Education",
    },
    "food_preparation": {
        "name": "Food Preparation for Preppers",
        "desc": "Long-term food storage, preservation techniques, and survival meals.",
        "category": "Survival", "icon": "🥫", "size_gb": 0.1,
        "url": "https://download.kiwix.org/zim/other/zimgit-food-preparation_en_2025-04.zim",
        "filename": "zimgit-food-preparation_en_2025-04.zim", "dest_subdir": "Survival",
    },
    "military_medicine": {
        "name": "Military Medicine Manuals",
        "desc": "US military medical manuals — field care, trauma, and triage.",
        "category": "Survival", "icon": "🏥", "size_gb": 0.08,
        "url": "https://download.kiwix.org/zim/zimit/irp.fas.org_en_military-medicine_2026-05.zim",
        "filename": "fas-military-medicine_en_2025-06.zim", "dest_subdir": "Survival",
    },
}

OLLAMA_CATALOG = {
    "llama3.2:3b":    {"name": "Llama 3.2 (3B)",   "desc": "Fast and capable. Best for quick queries.", "size_gb": 2.0, "tags": ["fast","general"]},
    "llama3.2:8b":    {"name": "Llama 3.2 (8B)",   "desc": "Balanced quality and speed. Best all-around.", "size_gb": 4.9, "tags": ["balanced","recommended"]},
    "mistral:7b":     {"name": "Mistral 7B",        "desc": "Excellent for coding and structured tasks.", "size_gb": 4.1, "tags": ["coding"]},
    "deepseek-r1:8b": {"name": "DeepSeek R1 (8B)", "desc": "Reasoning specialist — shows thinking step by step.", "size_gb": 4.9, "tags": ["reasoning"]},
    "phi3:mini":      {"name": "Phi-3 Mini (3.8B)", "desc": "Microsoft's compact model. Very fast.", "size_gb": 2.3, "tags": ["fast","compact"]},
    "qwen2.5:7b":     {"name": "Qwen 2.5 (7B)",    "desc": "Strong multilingual model.", "size_gb": 4.4, "tags": ["multilingual"]},
}


# ═══════════════════════════════════════════════════
# Content Download Queue
# ═══════════════════════════════════════════════════

_cq_jobs:   list       = []
_cq_active: dict | None = None
_cq_lock                = threading.Lock()


def _cq_worker():
    global _cq_active
    while True:
        time.sleep(1)
        with _cq_lock:
            if _cq_active and _cq_active.get("status") in ("done", "error", "cancelled"):
                _cq_active = None
            if _cq_active is None and _cq_jobs:
                job = _cq_jobs.pop(0)
                _cq_active = job
                threading.Thread(target=_cq_run, args=(job,), daemon=True).start()


def _cq_run(job: dict):
    try:
        if job["type"] == "zim":
            _cq_download_zim(job)
        elif job["type"] == "ollama":
            _cq_pull_ollama(job)
    except Exception as e:
        job["status"] = "error"
        job["error"]  = str(e)


def _cq_download_zim(job: dict):
    storage = get_storage_root()
    if not storage:
        raise RuntimeError("No storage configured — complete Setup Wizard first")

    dest_dir = storage / job["dest_subdir"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / job["filename"]
    tmp_file  = dest_dir / (job["filename"] + ".part")

    job["status"] = "running"
    job["dest"]   = str(dest_file)

    resume_from = tmp_file.stat().st_size if tmp_file.exists() else 0
    headers = {"User-Agent": "SVRN/1.0"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
        job["downloaded_bytes"] = resume_from

    try:
        req  = urllib.request.Request(job["url"], headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        total_remaining   = int(resp.headers.get("Content-Length", 0))
        job["total_bytes"] = resume_from + total_remaining

        mode = "ab" if resume_from else "wb"
        with open(tmp_file, mode) as f:
            downloaded = resume_from
            while True:
                if job.get("cancel"):
                    job["status"] = "cancelled"
                    return
                chunk = resp.read(1024 * 512)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                job["downloaded_bytes"] = downloaded
                total = job["total_bytes"]
                job["progress"] = round(downloaded / total * 100, 1) if total else 0

        tmp_file.replace(dest_file)
        job["status"]   = "done"
        job["progress"] = 100

        # Hot-reload kiwix server
        try:
            kp = get_port("kiwix")
            urllib.request.urlopen(f"http://127.0.0.1:{kp}/reload", timeout=2)
        except Exception:
            pass

    except Exception as e:
        job["status"] = "error"
        job["error"]  = str(e)


def _cq_pull_ollama(job: dict):
    """Pull an Ollama model using the detected binary."""
    ollama_bin = find_ollama()
    if not ollama_bin:
        job["status"] = "error"
        job["error"]  = "Ollama binary not found — install Ollama first"
        return

    job["status"]      = "running"
    job["progress"]    = 0
    job["status_text"] = "Pulling…"

    proc = subprocess.Popen(
        [str(ollama_bin), "pull", job["model"]],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for line in proc.stdout:
        if job.get("cancel"):
            proc.terminate()
            job["status"] = "cancelled"
            return
        line = line.strip()
        try:
            d         = json.loads(line)
            total     = d.get("total", 0)
            completed = d.get("completed", 0)
            if total and completed:
                job["progress"]    = round(completed / total * 100, 1)
                job["status_text"] = d.get("status", "")
            else:
                job["status_text"] = d.get("status", line)
        except Exception:
            if line:
                job["status_text"] = line

    proc.wait()
    job["status"]   = "done" if proc.returncode == 0 else "error"
    job["progress"] = 100
    if proc.returncode != 0:
        job["error"] = "ollama pull failed"


def cq_enqueue(job_data: dict) -> dict:
    job = {
        "id":               str(uuid.uuid4())[:8],
        "status":           "queued",
        "progress":         0,
        "downloaded_bytes": 0,
        "total_bytes":      0,
        "status_text":      "",
        "cancel":           False,
        "error":            "",
        **job_data,
    }
    with _cq_lock:
        _cq_jobs.append(job)
    return job


def cq_cancel(job_id: str):
    with _cq_lock:
        if _cq_active and _cq_active["id"] == job_id:
            _cq_active["cancel"] = True
        else:
            for j in list(_cq_jobs):
                if j["id"] == job_id:
                    _cq_jobs.remove(j)
                    break


def cq_status() -> dict:
    with _cq_lock:
        return {
            "active": dict(_cq_active) if _cq_active else None,
            "queue":  [dict(j) for j in _cq_jobs],
        }


def get_content_catalog() -> list:
    storage = get_storage_root()
    installed_paths: dict = {}

    def _scan_dir(d: Path):
        try:
            for f in d.iterdir():
                if f.suffix == ".zim":
                    installed_paths[f.name] = f
        except Exception:
            pass

    if storage:
        for sub in ZIM_SUBDIRS:
            d = storage / sub
            if d.exists():
                _scan_dir(d)

    # Also check what the live kiwix server has loaded
    try:
        kp = get_port("kiwix")
        archives = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{kp}/api/archives", timeout=1).read()
        )
        for arch in archives:
            p = arch.get("path", "")
            if p:
                fp = Path(p)
                if fp.exists() and fp.name not in installed_paths:
                    installed_paths[fp.name] = fp
    except Exception:
        pass

    qs = cq_status()
    active_filenames = set()
    if qs["active"] and qs["active"].get("type") == "zim":
        active_filenames.add(qs["active"].get("filename", ""))
    for j in qs["queue"]:
        if j.get("type") == "zim":
            active_filenames.add(j.get("filename", ""))

    result = []
    for cid, item in CONTENT_CATALOG.items():
        fname     = item["filename"]
        file_path = installed_paths.get(fname)
        installed = file_path is not None
        real_size = round(file_path.stat().st_size / 1e9, 2) if installed and file_path.exists() else item["size_gb"]
        queued    = fname in active_filenames and not installed
        result.append({
            "id":        cid,
            "installed": installed,
            "queued":    queued,
            "size_gb":   real_size,
            **{k: v for k, v in item.items() if k != "size_gb"},
        })
    return result


def get_ollama_catalog() -> list:
    installed: set = set()
    try:
        data = json.loads(
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2).read()
        )
        for m in data.get("models", []):
            installed.add(m["name"])
    except Exception:
        pass

    qs = cq_status()
    queued_models: set = set()
    if qs["active"] and qs["active"].get("type") == "ollama":
        queued_models.add(qs["active"].get("model", ""))
    for j in qs["queue"]:
        if j.get("type") == "ollama":
            queued_models.add(j.get("model", ""))

    result = []
    for mid, item in OLLAMA_CATALOG.items():
        inst = mid in installed or any(i.startswith(mid.split(":")[0]) for i in installed)
        result.append({"id": mid, "installed": inst,
                       "queued": mid in queued_models and not inst, **item})
    return result


def delete_zim(catalog_id: str) -> bool:
    if catalog_id not in CONTENT_CATALOG:
        return False
    item    = CONTENT_CATALOG[catalog_id]
    storage = get_storage_root()
    if not storage:
        return False
    target = storage / item["dest_subdir"] / item["filename"]
    if target.exists():
        target.unlink()
        return True
    return False


def delete_ollama_model(model_id: str) -> dict:
    ollama_bin = find_ollama()
    if not ollama_bin:
        return {"ok": False, "error": "Ollama binary not found"}
    r = subprocess.run(
        [str(ollama_bin), "rm", model_id],
        capture_output=True, text=True, timeout=15,
    )
    return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}


def delete_map(preset_id: str) -> dict:
    if preset_id not in MAP_PRESETS:
        return {"ok": False, "error": "Unknown preset"}
    storage = get_storage_root()
    if not storage:
        return {"ok": False, "error": "No storage configured"}
    target = storage / "maps" / f"{preset_id}.pmtiles"
    if not target.exists():
        return {"ok": False, "error": "File not found"}
    target.unlink()
    return {"ok": True}


# ═══════════════════════════════════════════════════
# OSE Wiki Explorer
# ═══════════════════════════════════════════════════

def get_ose_wiki_dir():
    storage = get_storage_root()
    if storage:
        d = storage / "ose-wiki" / "wiki.opensourceecology.org"
        if d.exists():
            return d
    return None


def ose_search(query: str, limit: int = 40) -> list:
    d = get_ose_wiki_dir()
    if not d:
        return []
    wiki_dir = d / "wiki"
    if not wiki_dir.exists():
        return []
    skip_prefixes = ("File:", "Template:", "Talk:", "Special:", "Category:", "User:", "Help:",
                     "File%3A", "Template%3A")
    q       = query.lower().replace(' ', '_')
    results = []

    def _add(name, rel_path):
        if any(name.startswith(p) for p in skip_prefixes) or ':' in name:
            return
        if q in rel_path.lower() or q in name.lower():
            title = rel_path.replace('_', ' ').replace('/', ' / ')
            results.append({"name": rel_path, "title": title})

    for f in wiki_dir.iterdir():
        if f.is_file() and f.name.endswith('.html'):
            _add(f.stem, f.stem)
    for subdir in wiki_dir.iterdir():
        if subdir.is_dir():
            parent = subdir.name
            if any(parent.startswith(p) for p in skip_prefixes) or ':' in parent:
                continue
            for f in subdir.glob("*.html"):
                _add(f.stem, f"{parent}/{f.stem}")

    results.sort(key=lambda x: (not x["name"].lower().startswith(q), len(x["name"])))
    return results[:limit]


def ose_get_article(name: str) -> dict:
    decoded = urllib.parse.unquote(name)
    if '..' in decoded or decoded.startswith('/') or decoded.startswith('.'):
        return {"error": "invalid"}

    d = get_ose_wiki_dir()
    if not d:
        return {"error": "OSE wiki not found in storage"}

    def _find_file(n):
        candidates = [d / "wiki" / f"{n}.html", d / "wiki" / f"{urllib.parse.unquote(n)}.html"]
        for c in candidates:
            if c.exists():
                return c
        return None

    html_file = _find_file(name)
    if not html_file:
        return {"error": "not found"}

    content = html_file.read_text(encoding='utf-8', errors='replace')
    title   = decoded.replace('_', ' ').split('/')[-1]

    m = re.search(r'<h1[^>]*>(?:<[^>]+>)*([^<]+)', content)
    if m:
        raw = m.group(1).strip()
        if '/' in raw:
            raw = raw.split('/')[-1].strip()
        if raw:
            title = raw
    elif '/' not in decoded:
        m = re.search(r'<title>([^<]+?)\s*[-–]', content)
        if m:
            title = m.group(1).strip()

    m    = re.search(r'<div[^>]+id="mw-content-text"[^>]*>(.*)', content, re.DOTALL)
    body = m.group(1) if m else content

    body = re.sub(r'<div class="lang">.*?</div>\s*', '', body, flags=re.DOTALL)
    body = re.sub(r'<a[^>]+class="new"[^>]*>.*?</a>', '', body, flags=re.DOTALL)
    body = re.sub(r'<li>\s*</li>', '', body)
    body = re.sub(r'<ul>\s*</ul>|<ol>\s*</ol>', '', body)
    body = re.sub(r'src="/images/', 'src="/ose-wiki/images/', body)

    def _rewrite_srcset(m):
        val = re.sub(r'(/images/)', '/ose-wiki/images/', m.group(1))
        return f'srcset="{val}"'
    body = re.sub(r'srcset="([^"]+)"', _rewrite_srcset, body)

    def _rewrite_link(mm):
        article = mm.group(1)
        if article.startswith('http') or article.startswith('//'):
            return mm.group(0)
        try:
            article = urllib.parse.unquote(article)
        except Exception:
            pass
        return f'href="javascript:void(0)" data-article="{article}"'

    body = re.sub(r'href="/wiki/([^"]+)"', _rewrite_link, body)
    body = re.sub(r'href="/index\.php[^"]*"', 'href="javascript:void(0)"', body)

    return {"title": title, "html": body, "name": name}


# ═══════════════════════════════════════════════════
# HTTP Handler
# ═══════════════════════════════════════════════════

class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        routes = {
            "/health":                   lambda: {"status": "ok", "service": "svrn-dashboard"},
            "/api/ports":                lambda: {"dashboard": get_port("dashboard"), "kiwix": get_port("kiwix")},
            "/api/system":               get_system_info,
            "/api/services":             get_services_status,
            "/api/vault":                get_vault_info,
            "/api/location":             get_location_info,
            "/api/maps/presets":         get_maps_presets,
            "/api/maps/download/status": get_download_status,
            "/api/notes":                get_notes,
            "/api/settings":             get_settings,
            "/api/library/catalog":      get_content_catalog,
            "/api/library/queue":        cq_status,
            "/api/library/ollama":       get_ollama_catalog,
            "/api/all": lambda: {
                "system":    get_system_info(),
                "services":  get_services_status(),
                "vault":     get_vault_info(),
                "location":  get_location_info(),
                "timestamp": time.time(),
            },
        }

        clean_path = urllib.parse.urlparse(self.path).path

        if clean_path in routes:
            try:
                self._json(200, routes[clean_path]())
            except Exception as e:
                self._json(500, {"error": str(e)})
            return

        # GET /api/notes/<id>
        if clean_path.startswith("/api/notes/"):
            note_id = clean_path[len("/api/notes/"):]
            if note_id:
                note = get_note(note_id)
                if note:
                    self._json(200, note)
                else:
                    self._json(404, {"error": "not found"})
                return

        # File API
        if clean_path == "/api/files/read":
            qs  = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            raw = qs.get("path", [""])[0]
            self._json(200, file_read(raw)); return
        if clean_path == "/api/files/list":
            qs  = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            raw = qs.get("path", [str(HOME)])[0]
            self._json(200, file_list(raw)); return

        # Combined map style
        if clean_path == "/api/maps/combined-style":
            presets      = get_maps_presets()
            installed_ids = [p["id"] for p in presets if p["installed"]]
            self._json(200, _build_combined_style(installed_ids)); return

        # PMTiles file serving
        if clean_path.startswith("/api/maps/file/"):
            self._serve_pmtiles(clean_path[len("/api/maps/file/"):]); return

        # CyberChef
        if clean_path in ("/cyberchef", "/cyberchef/"):
            self.path = "/static/cyberchef/index.html"
            super().do_GET(); return

        # Library cancel
        if clean_path.startswith("/api/library/cancel/"):
            cq_cancel(clean_path.split("/")[-1])
            self._json(200, {"cancelled": True}); return

        # OSE Wiki
        if clean_path == "/api/ose/status":
            d = get_ose_wiki_dir()
            self._json(200, {"available": d is not None, "path": str(d) if d else None}); return
        if clean_path.startswith("/api/ose/search"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q  = qs.get("q", [""])[0].strip()
            self._json(200, ose_search(q) if q else []); return
        if clean_path.startswith("/api/ose/article"):
            qs   = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = urllib.parse.unquote(qs.get("name", [""])[0].strip())
            self._json(200, ose_get_article(name)); return
        if clean_path.startswith("/ose-wiki/images/"):
            self._serve_ose_image(clean_path[len("/ose-wiki/images/"):]); return

        # Chat sessions
        if clean_path == "/api/chat/sessions":
            self._json(200, list_chat_sessions()); return
        if clean_path.startswith("/api/chat/session/") and \
                not clean_path.endswith(("/save", "/delete")):
            sid = clean_path[len("/api/chat/session/"):]
            self._json(200, get_chat_session(sid)); return

        # Ollama models list
        if clean_path == "/api/chat/models":
            try:
                data = json.loads(
                    urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2).read()
                )
                models = [m["name"] for m in data.get("models", [])
                          if not any(x in m["name"] for x in ("embed", "vision"))]
                preferred = ""
                for p in ("llama3.2:3b","phi3:mini","phi4:latest","llama3.1:latest",
                          "llama3.2:latest","mistral:7b","gemma3:latest"):
                    if p in models:
                        preferred = p
                        break
                if not preferred and models:
                    preferred = models[0]
                self._json(200, {"models": models, "preferred": preferred})
            except Exception as e:
                self._json(200, {"models": [], "preferred": "", "error": str(e)})
            return

        # /api/reader/article — fetch plain text of a ZIM article for AI context
        if clean_path == "/api/reader/article":
            _aq = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            zn  = _aq.get("zim",  [""])[0]
            ap  = _aq.get("path", [""])[0]
            txt = _fetch_zim_article_text(zn, ap)
            self._json(200, {"text": txt, "zim": zn, "path": ap}); return

        # Config API
        if clean_path == "/api/config/storage":
            storage = get_storage_root()
            self._json(200, {"path": str(storage) if storage else None,
                             "configured": storage is not None}); return

        # Page routes
        page_map = {
            "/chat":       "/chat.html",
            "/maps":       "/maps.html",
            "/notes":      "/notes.html",
            "/settings":   "/settings.html",
            "/docs":       "/docs.html",
            "/library":    "/library.html",
            "/datatools":  "/datatools.html",
            "/codeassist": "/codeassist.html",
            "/ose":        "/ose.html",
            "/reader":     "/reader.html",
            "/setup":      "/setup.html",
        }
        if clean_path in page_map:
            self.path = page_map[clean_path]
        elif clean_path in ("/", ""):
            self.path = "/index.html"
        else:
            self.path = clean_path

        super().do_GET()

    def _serve_ose_image(self, rel: str):
        d = get_ose_wiki_dir()
        if not d:
            self.send_response(404); self.end_headers(); return
        img_path = d / "images" / urllib.parse.unquote(rel)
        if not img_path.exists() and "thumb/" in rel:
            parts = urllib.parse.unquote(rel).split("/")
            if len(parts) >= 5:
                img_path = d / "images" / "/".join(parts[1:4])
        if not img_path.exists() or not img_path.is_file():
            flat_name = urllib.parse.unquote(rel).split("/")[-1]
            stripped  = re.sub(r'^\d+px-', '', flat_name)
            for candidate in (d / flat_name, d / stripped):
                if candidate.exists() and candidate.is_file():
                    img_path = candidate
                    break
        if not img_path.exists() or not img_path.is_file():
            self.send_response(404); self.end_headers(); return
        ctype, _ = mimetypes.guess_type(str(img_path))
        data = img_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",   ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control",  "public, max-age=604800")
        self.end_headers()
        self.wfile.write(data)

    def _serve_pmtiles(self, name: str):
        name = name.strip("/").split("?")[0]
        if not name.replace("-", "").replace("_", "").isalnum():
            self._json(400, {"error": "invalid name"}); return

        storage  = get_storage_root()
        filepath = None
        if storage:
            candidate = storage / "maps" / f"{name}.pmtiles"
            if candidate.exists():
                filepath = candidate
        if not filepath:
            local = SVRN_CONFIG / "maps" / f"{name}.pmtiles"
            if local.exists():
                filepath = local
        if not filepath:
            self._json(404, {"error": f"map '{name}' not found"}); return

        file_size = os.path.getsize(filepath)
        range_hdr = self.headers.get("Range", "")
        m = re.match(r"bytes=(\d+)-(\d*)", range_hdr)
        if m:
            start  = int(m.group(1))
            end    = int(m.group(2)) if m.group(2) else file_size - 1
            end    = min(end, file_size - 1)
            length = end - start + 1
            with open(filepath, "rb") as f:
                f.seek(start)
                data = f.read(length)
            self.send_response(206)
            self.send_header("Content-Type",   "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Range",  f"bytes {start}-{end}/{file_size}")
            self.send_header("Accept-Ranges",  "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        else:
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type",   "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges",  "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

    def do_PUT(self):
        clean_path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        if length > _MAX_POST_BYTES:
            self._json(413, {"error": "Request body too large"}); return
        body   = json.loads(self.rfile.read(length) or b"{}") if length else {}
        try:
            if clean_path.startswith("/api/notes/"):
                note_id = clean_path[len("/api/notes/"):]
                updated = update_note(note_id, body.get("title",""), body.get("content",""))
                self._json(200, updated) if updated else self._json(404, {"error": "not found"})
            else:
                self._json(404, {"error": "not found"})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def do_DELETE(self):
        clean_path = urllib.parse.urlparse(self.path).path
        try:
            if clean_path.startswith("/api/notes/"):
                note_id = clean_path[len("/api/notes/"):]
                self._json(200, {"deleted": delete_note(note_id)})
            else:
                self._json(404, {"error": "not found"})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def do_POST(self):
        clean_path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        if length > _MAX_POST_BYTES:
            self._json(413, {"error": "Request body too large"}); return
        body   = json.loads(self.rfile.read(length) or b"{}") if length else {}

        try:
            # ── Chat session management ─────────────────────────────────────
            if clean_path == "/api/chat/session/new":
                self._json(200, new_chat_session()); return

            if clean_path.startswith("/api/chat/session/") and clean_path.endswith("/save"):
                save_chat_session(body)
                self._json(200, {"saved": True}); return

            if clean_path.startswith("/api/chat/session/") and clean_path.endswith("/delete"):
                sid = clean_path[len("/api/chat/session/"):-len("/delete")]
                self._json(200, {"deleted": delete_chat_session(sid)}); return

            if clean_path == "/api/chat/search-zim":
                deep = body.get("deep", False)
                results = zim_rag_search(
                    body.get("query",""),
                    max_results=6 if deep else 3,
                    excerpt_len=1000 if deep else 600,
                )
                self._json(200, {"results": results}); return

            if clean_path == "/api/chat/upload":
                file_data = base64.b64decode(body.get("data", ""))
                filename  = body.get("filename", "file.txt")
                mime      = body.get("mime", "")
                text      = extract_file_text(file_data, filename, mime)
                is_image  = filename.lower().endswith((".jpg",".jpeg",".png",".gif",".webp",".bmp"))
                self._json(200, {
                    "filename": filename, "text": text, "is_image": is_image,
                    "image_b64": body.get("data","") if is_image else "",
                }); return

            # ── AI Chat (streaming SSE to Ollama) ───────────────────────────
            if clean_path == "/api/chat":
                messages = body.get("messages", [])
                model    = body.get("model", "")

                if not model:
                    try:
                        tags = json.loads(
                            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2).read()
                        )
                        candidates = [m["name"] for m in tags.get("models", [])
                                      if not any(x in m["name"] for x in ("embed","vision"))]
                        for p in ("llama3.2:3b","phi3:mini","phi4:latest",
                                  "llama3.1:latest","mistral:7b","gemma3:latest"):
                            if p in candidates:
                                model = p; break
                        if not model and candidates:
                            model = candidates[0]
                    except Exception:
                        pass

                if not model:
                    self._json(503, {"error": "No Ollama model available. Install one from Library → AI Models."}); return

                rag_context    = body.get("rag_context",    [])
                attached_files = body.get("attached_files", [])
                article_zim    = body.get("article_zim",    "")
                article_path   = body.get("article_path",   "")
                article_title  = body.get("article_title",  "")
                article_text   = body.get("article_text",   "")

                sys_parts = [
                    "You are SVRN AI — a helpful, knowledgeable offline assistant. "
                    "You have no internet access. Be concise, accurate, and friendly. "
                    "When you use information from library sources, cite the source name."
                ]

                if article_zim:
                    sys_parts.append(
                        f"\n\nThe user is reading the offline library: {article_zim}."
                    )
                    if article_title:
                        sys_parts.append(f' They are currently viewing: "{article_title}".')
                    if article_text:
                        sys_parts.append(
                            f"\n\n--- CURRENT ARTICLE TEXT ---\n"
                            f"{article_text[:7000]}\n"
                            f"--- END ARTICLE ---"
                        )

                if rag_context:
                    sys_parts.append("\n\n--- LIBRARY SOURCES ---")
                    for r in rag_context:
                        sys_parts.append(f"\n[{r['source']} — {r['title']}]\n{r['excerpt']}")
                    sys_parts.append("\n--- END SOURCES ---")
                if attached_files:
                    sys_parts.append("\n\n--- ATTACHED FILES ---")
                    for af in attached_files:
                        if not af.get("is_image"):
                            sys_parts.append(f"\n[File: {af['filename']}]\n{af.get('text','')[:4000]}")
                    sys_parts.append("\n--- END FILES ---")

                system_prompt    = "".join(sys_parts)
                ollama_messages  = [{"role": "system", "content": system_prompt}]

                for msg in messages:
                    m = {"role": msg["role"], "content": msg.get("content", "")}
                    if msg.get("images"):
                        m["images"] = msg["images"]
                    ollama_messages.append(m)

                image_files = [af for af in attached_files if af.get("is_image") and af.get("image_b64")]
                if image_files and ollama_messages and ollama_messages[-1]["role"] == "user":
                    ollama_messages[-1].setdefault("images", []).extend(
                        [af["image_b64"] for af in image_files]
                    )

                payload = json.dumps({
                    "model": model, "messages": ollama_messages, "stream": True
                }).encode()

                req = urllib.request.Request(
                    "http://127.0.0.1:11434/api/chat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    ollama_resp = urllib.request.urlopen(req, timeout=30)
                except Exception as e:
                    self._json(503, {"error": f"Ollama unreachable: {e}. Is Ollama running?"}); return

                self.send_response(200)
                self.send_header("Content-Type",  "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                try:
                    for raw_line in ollama_resp:
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                self.wfile.write(
                                    ("data: " + json.dumps({"text": token}) + "\n\n").encode()
                                )
                                self.wfile.flush()
                            if chunk.get("done"):
                                break
                        except Exception:
                            continue
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

            # ── Map downloads ─────────────────────────────────────────────
            if clean_path == "/api/maps/download":
                start_map_download(body.get("preset_id",""), body.get("source_url",""))
                self._json(200, {"started": True})
            elif clean_path == "/api/maps/download/cancel":
                cancel_map_download()
                self._json(200, {"cancelled": True})
            elif clean_path == "/api/maps/download/reset":
                reset_map_download_state()
                self._json(200, {"reset": True})

            # ── Location ──────────────────────────────────────────────────
            elif clean_path == "/api/location":
                set_location(body.get("region", ""))
                self._json(200, {"ok": True, "region": body.get("region")})

            # ── Library downloads ─────────────────────────────────────────
            elif clean_path == "/api/library/download":
                dtype = body.get("type")
                if dtype == "zim":
                    cid = body.get("id", "")
                    if cid not in CONTENT_CATALOG:
                        self._json(400, {"error": "unknown catalog id"}); return
                    storage = get_storage_root()
                    if not storage:
                        self._json(400, {"error": "No storage configured — complete Setup Wizard first"}); return
                    item = CONTENT_CATALOG[cid]
                    job  = cq_enqueue({
                        "type": "zim", "catalog_id": cid, "name": item["name"],
                        "url": body.get("url") or item["url"],
                        "filename": item["filename"], "dest_subdir": item["dest_subdir"],
                        "size_gb": item["size_gb"],
                    })
                    self._json(200, {"queued": True, "job": job})
                elif dtype == "ollama":
                    mid = body.get("id", "")
                    if mid not in OLLAMA_CATALOG:
                        self._json(400, {"error": "unknown model"}); return
                    item = OLLAMA_CATALOG[mid]
                    job  = cq_enqueue({"type": "ollama", "model": mid, "name": item["name"]})
                    self._json(200, {"queued": True, "job": job})
                else:
                    self._json(400, {"error": "type must be zim or ollama"})

            elif clean_path == "/api/library/delete/zim":
                self._json(200, {"deleted": delete_zim(body.get("id",""))})

            elif clean_path == "/api/library/delete/ollama":
                self._json(200, delete_ollama_model(body.get("id","")))

            elif clean_path == "/api/maps/delete":
                self._json(200, delete_map(body.get("preset_id","")))

            # ── Notes ─────────────────────────────────────────────────────
            elif clean_path == "/api/notes":
                self._json(201, create_note(body.get("title","Untitled"), body.get("content","")))
            elif clean_path == "/api/notes/clear":
                clear_notes()
                self._json(200, {"cleared": True})

            # ── Settings ──────────────────────────────────────────────────
            elif clean_path == "/api/settings":
                self._json(200, save_settings(body))

            # ── File write ────────────────────────────────────────────────
            elif clean_path == "/api/files/write":
                self._json(200, file_write(body.get("path",""), body.get("content","")))

            # ── Config: set storage root ───────────────────────────────────
            elif clean_path == "/api/config/storage":
                path = Path(body.get("path", "")).expanduser()
                if not path.exists():
                    self._json(400, {"error": "Path does not exist"}); return
                set_storage_root(path)
                self._json(200, {"ok": True, "path": str(path)})

            # ── Setup Wizard: native folder picker via AppleScript ─────────
            elif clean_path == "/api/setup/choose-folder":
                try:
                    script = (
                        'tell application "Finder"\n'
                        '  set chosen to choose folder with prompt "Choose where to store your SVRN library"\n'
                        '  return POSIX path of chosen\n'
                        'end tell'
                    )
                    result = subprocess.run(
                        ["osascript", "-e", script],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.returncode == 0:
                        chosen = result.stdout.strip().rstrip("/")
                        p = Path(chosen)
                        p.mkdir(parents=True, exist_ok=True)
                        self._json(200, {"path": str(p)})
                    else:
                        # User cancelled
                        self._json(200, {"path": None, "cancelled": True})
                except Exception as e:
                    self._json(400, {"error": str(e)})

            else:
                self._json(404, {"error": "not found"})

        except Exception as e:
            self._json(400, {"error": str(e)})

    def _json(self, code, data):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    import socketserver as _ss

    SVRN_CONFIG.mkdir(parents=True, exist_ok=True)

    # Find an available port with SO_REUSEADDR + fallback.
    # bind_port() returns a listening socket; we hand it directly to a subclassed
    # HTTPServer that skips the normal socket-creation path.
    _sock, PORT = bind_port("dashboard")

    class _SVRNDashServer(HTTPServer):
        """HTTPServer pre-initialised with an externally bound socket."""
        def __init__(self, addr, handler, sock):
            # Call BaseServer.__init__ to set up handler/address bookkeeping,
            # but skip HTTPServer.__init__ which would create and bind a new socket.
            _ss.BaseServer.__init__(self, addr, handler)
            self.socket = sock  # already bound + listening

    # Start content download queue worker
    threading.Thread(target=_cq_worker, daemon=True, name="cq-worker").start()

    print(f"SVRN Dashboard  →  http://localhost:{PORT}")
    storage = get_storage_root()
    print(f"Storage: {storage or 'NOT CONFIGURED (run Setup Wizard)'}")
    ollama_bin = find_ollama()
    print(f"Ollama:  {ollama_bin or 'NOT FOUND'}")

    try:
        server = _SVRNDashServer(("127.0.0.1", PORT), DashboardHandler, _sock)
        server.serve_forever()
    except KeyboardInterrupt:
        print("Dashboard stopped")
