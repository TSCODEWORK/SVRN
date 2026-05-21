# SVRN — Claude Code Context

Portable, self-contained offline knowledge & AI app for macOS. Double-click to install. No terminal, no Homebrew, no internet required during use.

**Origin:** Clean, distributable fork of `sovereign` (`/Users/svrnplanet/Claude/sovereign/`). Sovereign was a working daily driver with hardcoded paths, Homebrew assumptions, and no installer — SVRN fixes all of that.

---

## Key File Map

```
src/
  config/__init__.py        ← SINGLE SOURCE OF TRUTH for all paths and ports
  dashboard/server.py       ← Main HTTP server (Flask-like, stdlib only)
  dashboard/*.html          ← All UI pages (chat, library, maps, notes, reader…)
  dashboard/static/         ← Bundled JS/CSS — zero CDN dependencies
  kiwix/server.py           ← ZIM file server (libzim)
  menubar/app.py            ← macOS status bar app (rumps)

scripts/
  build_app.sh              ← Produces SVRN.app, .pkg, .dmg
  dev.sh                    ← Local dev: starts dashboard + kiwix, opens browser
  launch.py                 ← Entry point inside the .app bundle

installer/                  ← DMG/PKG build assets
launcher/                   ← App bundle shell launcher

assets/                     ← Icons, Info.plist
requirements.txt            ← libzim, rumps (only external deps)
PROJECT_SPEC.md             ← Full architecture decisions and rationale
```

---

## Architecture — The Rules

**Zero hardcoded paths.** Everything user-specific flows through `src/config/__init__.py`:
- `HOME = Path.home()` — never a literal username
- Storage root chosen by user at first launch via Setup Wizard, saved to `~/.config/svrn/config.json`
- All ZIM, maps, chat, notes paths derived from `get_storage_root()`

**Bundled Python.** `python-build-standalone 3.12` ships inside `SVRN.app/Contents/Resources/python/`. The app never touches system Python.

**Dynamic ports.** `bind_port(service)` in config tries preferred → preferred+1 → preferred+2 with `SO_REUSEADDR`. Chosen ports written to `~/.config/svrn/ports.json`. Defaults: dashboard=3333, kiwix=8888.

**Ollama auto-detect.** Not bundled. Setup Wizard detects it; `find_ollama()` checks four candidate paths (Intel Homebrew, Apple Silicon Homebrew, direct install, App bundle).

**No CDN.** All JS/CSS/fonts (MapLibre, Leaflet, PMTiles, marked, highlight.js, CyberChef) bundled in `src/dashboard/static/`.

---

## Dev Workflow

```bash
pip install libzim rumps          # one-time
bash scripts/dev.sh               # starts dashboard + kiwix, opens browser
```

## Build

```bash
bash scripts/build_app.sh         # → build/SVRN.app, .pkg, .dmg
bash scripts/build_app.sh --no-python   # skip Python download if already cached
```

---

## What NOT to do

- Never hardcode `/Users/svrnplanet/` or any username anywhere
- Never hardcode `SVRNVAULT.` or any drive name
- Never hardcode `/opt/homebrew/bin/ollama` — always use `find_ollama()`
- Never import from system Python in app code — always use bundled runtime path
- Never add CDN URLs to HTML files
