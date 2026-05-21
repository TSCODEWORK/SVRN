"""
SVRN test suite.
Run with: PYTHONPATH=src pytest tests/ -v
"""

import html
import json
import re
import socket
import sys
import tempfile
from pathlib import Path

import pytest

# Make sure the src/ tree is importable
SRC = Path(__file__).parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ── Config module ────────────────────────────────────────────────────────────


def test_config_imports():
    """Config module must import without errors and expose key symbols."""
    from config import (
        HOME, SVRN_CONFIG, DEFAULT_PORTS,
        get_storage_root, find_ollama, bind_port, get_port,
        get_config, set_config,
    )
    assert HOME.exists()
    assert DEFAULT_PORTS["dashboard"] == 3333
    assert DEFAULT_PORTS["kiwix"] == 8888


def test_config_load_missing(tmp_path, monkeypatch):
    """_load_config returns {} when the config file doesn't exist."""
    import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "nonexistent.json")
    result = config._load_config()
    assert result == {}


def test_config_load_corrupt(tmp_path, monkeypatch):
    """_load_config returns {} on corrupt JSON, not an exception."""
    import config
    bad_file = tmp_path / "config.json"
    bad_file.write_text("{bad json[[[")
    monkeypatch.setattr(config, "CONFIG_FILE", bad_file)
    result = config._load_config()
    assert result == {}


def test_config_round_trip(tmp_path, monkeypatch):
    """set_config / get_config persist values across calls."""
    import config
    monkeypatch.setattr(config, "SVRN_CONFIG", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config.set_config("test_key", "hello")
    assert config.get_config("test_key") == "hello"
    assert config.get_config("missing_key", "default") == "default"


def test_get_port_falls_back_to_default(tmp_path, monkeypatch):
    """get_port returns the default when ports.json is missing."""
    import config
    monkeypatch.setattr(config, "PORTS_FILE", tmp_path / "ports.json")
    assert config.get_port("dashboard") == 3333
    assert config.get_port("kiwix") == 8888


def test_get_port_reads_from_file(tmp_path, monkeypatch):
    """get_port reads the correct value from ports.json."""
    import config
    ports_file = tmp_path / "ports.json"
    ports_file.write_text(json.dumps({"dashboard": 4444, "kiwix": 9999}))
    monkeypatch.setattr(config, "PORTS_FILE", ports_file)
    assert config.get_port("dashboard") == 4444
    assert config.get_port("kiwix") == 9999


def test_set_storage_root_rejects_missing_path(tmp_path):
    """set_storage_root raises ValueError if the path doesn't exist."""
    import config
    with pytest.raises(ValueError, match="does not exist"):
        config.set_storage_root(tmp_path / "nonexistent")


def test_set_storage_root_accepts_existing_path(tmp_path, monkeypatch):
    """set_storage_root accepts a valid existing directory."""
    import config
    monkeypatch.setattr(config, "SVRN_CONFIG", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    storage = tmp_path / "storage"
    storage.mkdir()
    config.set_storage_root(storage)
    assert config.get_storage_root() == storage


def test_bind_port_returns_socket_and_port(tmp_path, monkeypatch):
    """bind_port returns a bound socket and an integer port."""
    import config
    monkeypatch.setattr(config, "SVRN_CONFIG", tmp_path)
    monkeypatch.setattr(config, "PORTS_FILE", tmp_path / "ports.json")
    sock, port = config.bind_port("dashboard", preferred=0)  # 0 = OS picks free port
    assert isinstance(port, int)
    assert port > 0
    sock.close()


def test_bind_port_writes_ports_file(tmp_path, monkeypatch):
    """bind_port persists the chosen port to ports.json."""
    import config
    monkeypatch.setattr(config, "SVRN_CONFIG", tmp_path)
    monkeypatch.setattr(config, "PORTS_FILE", tmp_path / "ports.json")
    sock, port = config.bind_port("test_svc", preferred=0)
    sock.close()
    data = json.loads((tmp_path / "ports.json").read_text())
    assert data["test_svc"] == port


def test_bind_port_sock_never_unbound_on_socket_failure(monkeypatch):
    """
    If socket.socket() itself raises, bind_port should propagate a RuntimeError
    cleanly — not NameError from an unbound 'sock' variable.
    """
    import socket as _socket
    import config

    original_socket = _socket.socket

    call_count = [0]

    def bad_socket(*args, **kwargs):
        call_count[0] += 1
        raise OSError("simulated failure")

    monkeypatch.setattr(_socket, "socket", bad_socket)
    with pytest.raises(RuntimeError, match="Could not bind"):
        config.bind_port("test", preferred=19999)


# ── Dashboard — file path safety ─────────────────────────────────────────────


def test_file_path_safe_allows_home(monkeypatch):
    """_file_path_safe permits paths inside HOME."""
    import dashboard.server as srv
    p = srv._file_path_safe(str(srv.HOME / "Documents" / "test.txt"))
    assert p is not None
    assert p == (srv.HOME / "Documents" / "test.txt").resolve()


def test_file_path_safe_blocks_traversal(monkeypatch):
    """_file_path_safe blocks obvious traversal outside HOME."""
    import dashboard.server as srv
    # /etc/passwd is outside HOME on any normal system
    result = srv._file_path_safe("/etc/passwd")
    home = srv.HOME
    if Path("/etc/passwd").resolve().is_relative_to(home):
        pytest.skip("/etc is inside HOME on this system")
    assert result is None


def test_file_path_safe_blocks_dotdot():
    """_file_path_safe resolves ../ before checking roots."""
    import dashboard.server as srv
    # Build a path that starts in HOME but escapes via ../
    attempt = str(srv.HOME / ".." / ".." / "etc" / "passwd")
    result = srv._file_path_safe(attempt)
    # After resolution it's outside HOME, so should be None
    resolved = Path(attempt).expanduser().resolve()
    if resolved.is_relative_to(srv.HOME):
        pytest.skip("resolved path is still inside HOME")
    assert result is None


# ── Dashboard — HTML entity handling ────────────────────────────────────────


def test_html_unescape_coverage():
    """html.unescape must handle all common entities correctly."""
    cases = {
        "&amp;":  "&",
        "&lt;":   "<",
        "&gt;":   ">",
        "&nbsp;": "\xa0",
        "&quot;": '"',
        "&#39;":  "'",
        "&#169;": "©",
    }
    for entity, expected in cases.items():
        assert html.unescape(entity) == expected, f"Failed for {entity}"


def test_fetch_zim_article_text_strips_tags():
    """_fetch_zim_article_text strips HTML tags from raw HTML."""
    import dashboard.server as srv

    raw = "<html><body><h1>Hello &amp; World</h1><p>Test &#169; content.</p></body></html>"
    # Patch urlopen to return our raw HTML
    class FakeResp:
        def read(self): return raw.encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    import urllib.request
    original = urllib.request.urlopen

    def fake_urlopen(req, timeout=None):
        return FakeResp()

    srv_module = sys.modules.get("dashboard.server") or __import__("dashboard.server")
    import unittest.mock as mock
    with mock.patch("urllib.request.urlopen", fake_urlopen):
        result = srv._fetch_zim_article_text("test_zim", "A/Test")

    assert "&amp;" not in result
    assert "<h1>" not in result
    assert "Hello" in result
    assert "World" in result


# ── Kiwix — HTML rewriting ───────────────────────────────────────────────────


def test_rewrite_html_double_quotes():
    """rewrite_html rewrites double-quoted href attributes."""
    from kiwix.server import rewrite_html
    html_bytes = b'<html><body><a href="A/Article">link</a></body></html>'
    result = rewrite_html(html_bytes, "myzim", "").decode()
    assert 'href="/zim/myzim/A/Article"' in result


def test_rewrite_html_single_quotes():
    """rewrite_html rewrites single-quoted href attributes."""
    from kiwix.server import rewrite_html
    html_bytes = b"<html><body><a href='A/Article'>link</a></body></html>"
    result = rewrite_html(html_bytes, "myzim", "").decode()
    assert "href='/zim/myzim/A/Article'" in result


def test_rewrite_html_leaves_external_links():
    """rewrite_html passes through http:// and https:// URLs unchanged."""
    from kiwix.server import rewrite_html
    html_bytes = b'<html><body><a href="https://example.com">ext</a></body></html>'
    result = rewrite_html(html_bytes, "myzim", "").decode()
    assert 'href="https://example.com"' in result


def test_rewrite_html_embedded_appends_e1():
    """rewrite_html appends ?e=1 to internal HTML links in embedded mode."""
    from kiwix.server import rewrite_html
    html_bytes = b'<html><body><a href="A/Page">link</a></body></html>'
    result = rewrite_html(html_bytes, "myzim", "A/Current", embedded=True).decode()
    assert "?e=1" in result


def test_rewrite_html_relative_path_resolution():
    """rewrite_html correctly resolves ../ relative links."""
    from kiwix.server import rewrite_html
    html_bytes = b'<html><body><a href="../Other">link</a></body></html>'
    result = rewrite_html(html_bytes, "myzim", "A/Sub/Page").decode()
    # "../Other" from "A/Sub/" resolves to "A/Other"
    assert "/zim/myzim/A/Other" in result


def test_rewrite_html_src_double_quotes():
    """rewrite_html rewrites double-quoted src attributes."""
    from kiwix.server import rewrite_html
    html_bytes = b'<html><body><img src="images/photo.png"></body></html>'
    result = rewrite_html(html_bytes, "myzim", "A/Page").decode()
    assert 'src="/zim/myzim/' in result


def test_rewrite_html_src_single_quotes():
    """rewrite_html rewrites single-quoted src attributes."""
    from kiwix.server import rewrite_html
    html_bytes = b"<html><body><img src='images/photo.png'></body></html>"
    result = rewrite_html(html_bytes, "myzim", "A/Page").decode()
    assert "src='/zim/myzim/" in result


# ── Dashboard — content-length protection ────────────────────────────────────


def test_max_post_bytes_constant():
    """_MAX_POST_BYTES must be a positive integer (50 MB)."""
    import dashboard.server as srv
    assert srv._MAX_POST_BYTES == 50 * 1024 * 1024
    assert isinstance(srv._MAX_POST_BYTES, int)


# ── Dashboard — import cleanliness ───────────────────────────────────────────


def test_no_bare_local_re_imports():
    """dashboard/server.py must not have local 'import re as' statements."""
    server_src = (SRC / "dashboard" / "server.py").read_text()
    # No function-scoped re aliases — all re usage goes through the module-level import
    assert "import re as _re" not in server_src
    assert "import re as _rer" not in server_src


def test_no_uuid_alias():
    """dashboard/server.py must not import uuid under an alias."""
    server_src = (SRC / "dashboard" / "server.py").read_text()
    assert "import uuid as _uuid_mod" not in server_src


def test_no_port_constant():
    """Module-level PORT=3333 constant was removed from dashboard/server.py."""
    server_src = (SRC / "dashboard" / "server.py").read_text()
    assert "PORT = 3333" not in server_src


def test_open_with_context_manager():
    """extract_file_text must use 'with open(...)' not bare open()."""
    server_src = (SRC / "dashboard" / "server.py").read_text()
    # The old bare open() was: 'text = open(out_path).read()'
    assert "= open(out_path).read()" not in server_src


# ── Launcher — restart backoff ───────────────────────────────────────────────


def test_launcher_has_restart_counts():
    """launcher/launch.py must declare _restart_counts for backoff tracking."""
    launcher_src = (Path(__file__).parent.parent / "launcher" / "launch.py").read_text()
    assert "_restart_counts" in launcher_src
    assert "delay" in launcher_src


# ── Menubar — Ollama port ────────────────────────────────────────────────────


def test_menubar_uses_get_config_for_ollama_port():
    """menubar/app.py must use get_config for the Ollama port, not hardcode 11434."""
    menubar_src = (SRC / "menubar" / "app.py").read_text()
    assert 'get_config("ollama_port"' in menubar_src
    # The hardcoded port check in _update must be gone
    assert "_port_open(11434)" not in menubar_src


# ── Requirements — version bounds ────────────────────────────────────────────


def test_requirements_have_upper_bounds():
    """requirements.txt pins upper version bounds on all deps."""
    req_text = (Path(__file__).parent.parent / "requirements.txt").read_text()
    assert "<4.0" in req_text   # libzim upper bound
    assert "<1.0" in req_text   # rumps upper bound
