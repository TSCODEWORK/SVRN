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
| **AI Chat** | Streaming markdown, session history, file/image drag-drop |
| **Offline Library** | ZIM reader (Wikipedia, medical, education, books, 50+ collections) |
| **Split Reader** | Article + AI side-by-side, article-aware AI context |
| **Offline Maps** | MapLibre GL + PMTiles — drop pins, measure distances, save places |
| **Notes** | Local markdown notes with live preview |
| **Code Assistant** | Syntax highlighting, AI-powered |
| **Data Tools** | CyberChef (bundled) |
| **OSE Wiki** | Open Source Ecology wiki mirror |
| **Inventory** | Track parts, supplies, and equipment locally |
| **Library Manager** | Download ZIM files, Ollama models, and maps |

## Install

> **macOS Gatekeeper note:** SVRN is not notarized. After opening the DMG, right-click SVRN → Open → Open to launch it the first time.

1. Download `SVRN-1.0.0.dmg` from the [latest release](https://github.com/TSCODEWORK/SVRN/releases/latest)
2. Open the DMG → drag SVRN to Applications
3. Right-click SVRN → **Open** → **Open** (first launch only, bypasses Gatekeeper)
4. The Setup Wizard opens — choose a storage folder and detect Ollama

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
pip install libzim                # one-time (rumps not needed — Swift handles menubar)
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
