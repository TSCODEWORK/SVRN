# SVRN

**Portable, self-contained offline knowledge & AI app for macOS.**

Double-click to install. No terminal. No Homebrew. No internet required during use.

---

## What it is

SVRN bundles a local AI assistant (via Ollama), a full offline library (Wikipedia, medical references, recipes, and 50+ more ZIM collections), offline vector maps, notes, and a code assistant — all running locally on your Mac.

Store it on an external drive. Plug into any Mac. Click open. It works.

## Features

| Feature | Notes |
|---------|-------|
| **AI Chat** | Streaming markdown, session history, file/image drag-drop, library RAG |
| **Offline Library** | ZIM reader (Wikipedia, medical, cooking, books, 50+ collections) |
| **Split Reader** | Article + AI side-by-side, article-aware AI context |
| **Offline Maps** | MapLibre GL + PMTiles, fully local fonts |
| **Notes** | Local markdown notes |
| **Code Assistant** | Syntax highlighting, AI-powered |
| **Data Tools** | CyberChef (bundled) |
| **OSE Wiki** | Open Source Ecology wiki mirror |
| **System Dashboard** | Service status, storage info |
| **Library Manager** | Download ZIM files, Ollama models, maps |

## Install

1. Download `SVRN-1.0.0.dmg`
2. Open the DMG → drag SVRN to Applications
3. Double-click SVRN — the Setup Wizard opens on first launch
4. Choose a storage folder, detect Ollama, pick starter content

## Build from source

**Requirements:** macOS 13+, Xcode Command Line Tools, internet connection for first build only.

```bash
git clone https://github.com/TSCODEWORK/SVRN
cd SVRN
bash scripts/build_app.sh
# → build/SVRN.app   (100 MB)
# → build/SVRN-1.0.0.pkg
# → build/SVRN-1.0.0.dmg
```

Subsequent builds reuse the cached Python runtime:
```bash
bash scripts/build_app.sh --no-python
```

## Development

```bash
pip install libzim rumps          # one-time
bash scripts/dev.sh               # starts dashboard + kiwix, opens browser
```

## Architecture

- **Python**: Bundled (python-build-standalone 3.12) — zero system dependencies
- **Ollama**: Not bundled — Setup Wizard guides installation; binary auto-detected
- **Storage**: User-chosen folder via Setup Wizard — any drive, any path
- **Ports**: Dynamic with SO_REUSEADDR fallback — written to `~/.config/svrn/ports.json`
- **Offline**: All JS/CSS/fonts bundled locally — zero CDN dependencies

## Platform

- macOS 13 Ventura or later
- Apple Silicon (arm64) and Intel (x86_64)

---

*Reference implementation: `sovereign` (private daily driver)*
*Specification: [PROJECT_SPEC.md](PROJECT_SPEC.md)*
