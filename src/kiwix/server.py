#!/usr/bin/env python3
"""
SVRN ZIM Server — replaces kiwix-serve
Serves ZIM offline libraries over HTTP.
Requires: pip install libzim

Port is auto-assigned (primary: 8888) with SO_REUSEADDR fallback.
Actual port written to ~/.config/svrn/ports.json.
"""

import logging
import os
import sys
import re
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

_log = logging.getLogger(__name__)

# Allow running directly or imported from the app bundle
# sys.path is adjusted by the launcher before this script starts.
try:
    from config import get_storage_root, zim_dirs, maps_dir, bind_port, get_port
except ImportError:
    # Running standalone — add parent to path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config import get_storage_root, zim_dirs, maps_dir, bind_port, get_port

# ── Load ZIM archives ────────────────────────────────────────────────────────

_archives: dict = {}   # stem -> {"path": Path, "archive": Archive, "dir": str}


def _scan_zim_paths() -> list:
    """Scan user storage directories for .zim files."""
    results = []
    for base in zim_dirs():
        try:
            for zim_path in sorted(base.glob("*.zim")):
                results.append({"path": str(zim_path), "dir": base.name})
        except Exception:
            _log.warning("Failed to scan ZIM directory %s", base, exc_info=True)
    return results


def load_archives():
    try:
        from libzim.reader import Archive
    except ImportError:
        print("ERROR: libzim not installed. Run: pip install libzim", file=sys.stderr)
        return

    for entry in _scan_zim_paths():
        zim_path = Path(entry["path"])
        subdir   = entry["dir"]
        name     = zim_path.stem
        if name in _archives:
            continue
        try:
            arch = Archive(str(zim_path))
            _archives[name] = {"path": zim_path, "archive": arch, "dir": subdir}
            print(f"  Loaded: {subdir}/{zim_path.name}")
        except Exception as e:
            print(f"  Warning: could not open {zim_path.name}: {e}", file=sys.stderr)


# ── Asset extension detection (for embedded link rewriting) ──────────────────

_ASSET_EXTS = frozenset([
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".otf", ".ico", ".json", ".xml", ".txt", ".pdf",
])


def _is_asset_url(url: str) -> bool:
    path = url.split("?")[0].split("#")[0]
    return any(path.lower().endswith(ext) for ext in _ASSET_EXTS)


# ── HTML helpers ─────────────────────────────────────────────────────────────

NAV_CSS = """
body{font-family:system-ui,sans-serif;background:#09090b;color:#c4c4d0;margin:0;}
nav{background:#111113;border-bottom:1px solid #27272f;padding:10px 24px;display:flex;align-items:center;gap:16px;}
nav a{color:#4f8fff;text-decoration:none;font-size:13px;}
nav .brand{font-weight:700;letter-spacing:.2em;font-size:15px;color:#ededf0;}
.content{padding:24px;}
h1,h2{color:#ededf0;}
a{color:#4f8fff;}
ul{list-style:none;padding:0;}
li{margin:10px 0;padding:12px 16px;background:#111113;border:1px solid #27272f;border-radius:6px;}
li a{font-size:15px;font-weight:600;}
li .desc{font-size:12px;color:#71717a;margin-top:4px;}
input[type=search]{background:#18181c;border:1px solid #27272f;border-radius:6px;color:#ededf0;
  font-size:14px;padding:8px 14px;width:100%;max-width:500px;margin-bottom:16px;outline:none;}
"""


def nav_html(extra=""):
    return f'<nav><span class="brand">SVRN</span><a href="/">Library Home</a>{extra}</nav>'


# ── ZIM content rewriting ────────────────────────────────────────────────────

def _resolve_url(url: str, prefix: str, article_base_dir: str) -> str:
    """
    Resolve a URL (possibly relative) against the current article's directory
    and return an absolute path rooted at prefix.
    """
    PASSTHROUGH = ("http://", "https://", "//", "data:", "#", "javascript:", "mailto:")
    if any(url.startswith(p) for p in PASSTHROUGH):
        return url

    # Absolute path within the ZIM (starts with /)
    if url.startswith("/"):
        return prefix + url.lstrip("/")

    # Strip leading ./ if present
    if url.startswith("./"):
        url = url[2:]

    # Resolve ../ sequences relative to the current article's directory
    parts = (article_base_dir + url).split("/")
    resolved = []
    for part in parts:
        if part == "..":
            if resolved:
                resolved.pop()
        elif part and part != ".":
            resolved.append(part)

    return prefix + "/".join(resolved)


def rewrite_html(content_bytes: bytes, zim_name: str, article_path: str = "",
                 embedded: bool = False) -> bytes:
    """Rewrite relative links so they route through this server.

    embedded=True: skip SVRN nav bar; inject postMessage script so the
    parent reader page can track navigation; propagate ?e=1 on all HTML links.
    """
    try:
        text = content_bytes.decode("utf-8", errors="replace")
    except Exception:
        return content_bytes

    prefix = f"/zim/{zim_name}/"

    # Compute the directory of the current article for resolving relative paths
    # e.g. article_path="App/IntroPage" → article_base_dir="App/"
    if "/" in article_path:
        article_base_dir = article_path.rsplit("/", 1)[0] + "/"
    else:
        article_base_dir = ""

    def fix_href(m):
        quote = m.group(1)  # ' or "
        url   = m.group(2)
        new   = _resolve_url(url, prefix, article_base_dir)
        # In embedded mode propagate ?e=1 so every ZIM page stays embedded
        if embedded and new != url and new.startswith(prefix) and not _is_asset_url(new):
            new = new + ("&e=1" if "?" in new else "?e=1")
        if new == url:
            return m.group(0)
        return f'href={quote}{new}{quote}'

    def fix_src(m):
        quote = m.group(1)  # ' or "
        url   = m.group(2)
        new   = _resolve_url(url, prefix, article_base_dir)
        if new == url:
            return m.group(0)
        return f'src={quote}{new}{quote}'

    # Match both single- and double-quoted href/src attributes
    text = re.sub(r'href=(["\'])([^"\']*)\1', fix_href, text)
    text = re.sub(r'src=(["\'])([^"\']*)\1',  fix_src,  text)

    if embedded:
        # Inject a lightweight postMessage script so the parent reader can track
        # the current article path and title without cross-origin access.
        zs = zim_name.replace("'", "\\'")
        ps = article_path.replace("'", "\\'").replace("\\", "\\\\")
        inject = (
            f"<script>(function(){{"
            f"function _sn(){{var t=document.title||'{zs}';"
            f"window.parent.postMessage({{type:'zim-nav',path:'{ps}',title:t,zim:'{zs}'}},'*');}}"
            f"if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',_sn);"
            f"else _sn();}})();</script>"
        )
        text = re.sub(r'(<body[^>]*>)', r'\1' + inject, text, count=1, flags=re.IGNORECASE)
    else:
        # Inject the SVRN nav bar (standalone browsing)
        nav = nav_html(
            f' <span style="color:#71717a">›</span> '
            f'<a href="/zim/{zim_name}/">{zim_name}</a>'
        )
        text = re.sub(r'(<body[^>]*>)', r'\1' + nav, text, count=1, flags=re.IGNORECASE)

    return text.encode("utf-8")


# ── HTTP Handler ─────────────────────────────────────────────────────────────

class ZIMHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = urllib.parse.unquote(parsed.path)
        parts  = [p for p in path.split("/") if p]
        # Preserve trailing slash — it is significant in ZIM paths
        _had_trailing_slash = path.endswith("/")

        # / — library index
        if not parts:
            self._serve_index()
            return

        # /health
        if parts == ["health"]:
            self._send(200, b'{"status":"ok","service":"svrn-zim"}', "application/json")
            return

        # /reload — rescan storage for new ZIM files
        if parts == ["reload"]:
            load_archives()
            data = json.dumps({"loaded": len(_archives)}).encode()
            self._send(200, data, "application/json")
            return

        # /api/archives — JSON list for dashboard
        if parts == ["api", "archives"]:
            data = [
                {"name": k, "dir": v["dir"], "path": str(v["path"])}
                for k, v in _archives.items()
            ]
            self._send(200, json.dumps(data).encode(), "application/json")
            return

        # /zim/<name>/<...path...>
        if parts[0] == "zim" and len(parts) >= 2:
            zim_name     = parts[1]
            article_path = "/".join(parts[2:]) if len(parts) > 2 else ""
            # Re-add trailing slash if the original URL had one — ZIM paths like
            # "based.cooking/" are distinct from "based.cooking" and matter for
            # relative-URL resolution (article_base_dir calculation).
            if _had_trailing_slash and article_path and not article_path.endswith("/"):
                article_path += "/"
            self._serve_article(zim_name, article_path, parsed.query)
            return

        self._send(404, b"Not found", "text/plain")

    # ── Index page ──

    def _serve_index(self):
        if not _archives:
            storage = get_storage_root()
            hint = str(storage / "zim") if storage else "your chosen storage folder → zim/"
            body = (
                f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>SVRN Library</title>
                <style>{NAV_CSS}</style></head><body>{nav_html()}
                <div class="content">
                <h1>No ZIM Libraries Loaded</h1>
                <p>Put .zim files in <strong>{hint}</strong>.<br>
                Download ZIM files from the Library Manager when online.</p>
                </div></body></html>"""
            )
        else:
            items = []
            for name, info in sorted(_archives.items()):
                try:
                    title = info["archive"].get_metadata("Title").decode("utf-8", errors="replace")
                except Exception:
                    title = name
                try:
                    desc = info["archive"].get_metadata("Description").decode("utf-8", errors="replace")[:120]
                except Exception:
                    desc = ""
                items.append(
                    f'<li><a href="/zim/{name}/">{title}</a>'
                    f'<div class="desc">{info["dir"]} · {desc}</div></li>'
                )
            body = (
                f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>SVRN Library</title>
                <style>{NAV_CSS}</style></head><body>{nav_html()}
                <div class="content">
                <h1>Offline Library</h1>
                <p style="color:#71717a;margin-bottom:16px">{len(_archives)} collection(s) loaded</p>
                <ul>{''.join(items)}</ul>
                </div></body></html>"""
            )
        self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    # ── Article serving ──

    def _serve_article(self, zim_name: str, article_path: str, query: str):
        # Parse ?e=1 (embedded reader mode — skip nav bar, inject postMessage)
        qp       = urllib.parse.parse_qs(query)
        embedded = qp.get("e", [""])[0] == "1"
        e_sfx    = "?e=1" if embedded else ""

        if zim_name not in _archives:
            self._send(404, f"ZIM '{zim_name}' not found".encode(), "text/plain")
            return

        arch = _archives[zim_name]["archive"]

        # No path → redirect to main entry
        if not article_path:
            try:
                main = arch.main_entry
                # Follow redirect chain to final target
                hops = 0
                while main.is_redirect and hops < 5:
                    main = main.get_redirect_entry()
                    hops += 1
                self._redirect(f"/zim/{zim_name}/{main.path}{e_sfx}")
            except Exception:
                for fallback in ["A/Main_Page", "Main_Page", "index", "home", "mainPage"]:
                    try:
                        arch.get_entry_by_path(fallback)
                        self._redirect(f"/zim/{zim_name}/{fallback}{e_sfx}")
                        return
                    except KeyError:
                        continue
                self._redirect("/")
            return

        # Try to get entry — check multiple path variants
        entry = None
        candidates = [article_path]
        # A/ prefix variants
        if article_path.startswith("A/"):
            candidates.append(article_path[2:])
        else:
            candidates.append(f"A/{article_path}")
        # Trailing-slash variants
        candidates.append(article_path + "/")
        if article_path.startswith("A/"):
            candidates.append(article_path[2:] + "/")
        else:
            candidates.append(f"A/{article_path}/")
        # Last-component fallback
        stripped = article_path.rstrip("/")
        if "/" in stripped:
            last = stripped.rsplit("/", 1)[-1]
            if last:
                candidates += [last, f"A/{last}", last + "/"]

        for candidate in candidates:
            try:
                entry = arch.get_entry_by_path(candidate)
                break
            except KeyError:
                continue

        if entry is None:
            self._send(
                404,
                (
                    f"<html><body style='background:#09090b;color:#c4c4d0;"
                    f"font-family:monospace;padding:40px'>"
                    f"<h2>Article not found</h2><p>{article_path}</p>"
                    f"<p><a href='/zim/{zim_name}/{e_sfx}' style='color:#4f8fff'>"
                    f"← Back to library</a></p>"
                    f"</body></html>"
                ).encode(),
                "text/html"
            )
            return

        # Follow redirect chain internally; track final path for postMessage accuracy
        final_path = article_path
        hops = 0
        while entry.is_redirect and hops < 10:
            redir = entry.get_redirect_entry()
            final_path = redir.path
            entry = redir
            hops += 1

        item     = entry.get_item()
        content  = bytes(item.content)
        mimetype = item.mimetype or "application/octet-stream"

        if "text/html" in mimetype:
            content = rewrite_html(content, zim_name, final_path, embedded=embedded)

        self._send(200, content, mimetype)

    # ── Helpers ──

    def _redirect(self, url: str):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type",   content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def _rescan_loop():
    """Background thread: rescan for new ZIM files every 60s."""
    import time
    while True:
        time.sleep(60)
        prev_count = len(_archives)
        load_archives()
        if len(_archives) > prev_count:
            print(f"  Rescanned — now {len(_archives)} ZIM archive(s)")


if __name__ == "__main__":
    import socketserver as _ss
    import threading

    # Find an available port with SO_REUSEADDR + fallback.
    _sock, port = bind_port("kiwix")

    class _SVRNZIMServer(HTTPServer):
        """HTTPServer pre-initialised with an externally bound socket."""
        def __init__(self, addr, handler, sock):
            _ss.BaseServer.__init__(self, addr, handler)
            self.socket = sock  # already bound + listening

    print(f"SVRN ZIM Server  →  http://localhost:{port}")
    storage = get_storage_root()
    print(f"Storage: {storage or 'NOT CONFIGURED (use Setup Wizard)'}")
    load_archives()
    print(f"Loaded {len(_archives)} ZIM archive(s)")

    threading.Thread(target=_rescan_loop, daemon=True).start()

    try:
        server = _SVRNZIMServer(("127.0.0.1", port), ZIMHandler, _sock)
        server.serve_forever()
    except KeyboardInterrupt:
        print("ZIM server stopped")
