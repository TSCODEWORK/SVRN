# SVRN — Project Specification
> Portable, self-contained offline knowledge & AI app for macOS

---

## Vision

SVRN is a completely offline Mac application — double-click to install, zero terminal knowledge required. It runs a local AI, a full offline library (Wikipedia, medical, cooking, books, maps, and more), and a suite of productivity tools. No internet connection is needed during use. Internet is only used for the initial download of content (ZIM files, maps, AI models).

**The dream:** Store SVRN on an external drive. Plug it into any Mac. Click open. It works.

---

## Origin

SVRN is a clean, portable, distributable fork of the `sovereign` project (`/Users/svrnplanet/Claude/sovereign/`). The sovereign project is a working daily driver — this repo exists to rebuild the architecture so it can ship to anyone.

The sovereign project audit (May 2026) identified the following blockers for distribution:
- Hardcoded `/Users/svrnplanet/` paths throughout all server files and launch plists
- Hardcoded `/opt/homebrew/bin/ollama` (breaks Intel Macs and non-Homebrew installs)
- Wrong PYTHONPATH in menubar launchd plist (Python 3.9 vs 3.14)
- Vault name `SVRNVAULT.` hardcoded in 6+ files
- No port conflict handling — servers crash silently
- Dead tile_server component still running (port 8085, unused)
- No requirements.txt
- Shell script installer requires Terminal knowledge
- `project-nomad` ZIM path hardcoded (breaks all other machines)

---

## Target Users

Any Mac user. No terminal. No Homebrew. No Python knowledge.

---

## Platform

- **macOS only** (Intel + Apple Silicon)
- Minimum target: macOS 13 Ventura
- No Linux, no Windows in v1

---

## Feature Set (Full Parity with sovereign)

All features from the sovereign project are included in v1:

| Feature | Notes |
|---------|-------|
| **AI Chat** | Streaming markdown, session history, file/image drag-drop, ZIM library RAG |
| **Offline Library** | ZIM reader (Wikipedia, medical, cooking, education, books, 59+ ZIMs) |
| **Split Reader** | 50/50 article + AI chat, breadcrumb navigation, article-aware AI |
| **Offline Maps** | MapLibre GL + PMTiles, fully local fonts, offline rendering |
| **Notes** | Local markdown notes |
| **Code Assistant** | Syntax highlighting, AI-powered |
| **Data Tools** | CyberChef (bundled), local data processing |
| **OSE Wiki** | Open Source Ecology wiki mirror |
| **System Dashboard** | Service status, vault info, system stats |
| **Settings** | Configurable vault path, location, preferences |
| **Menubar App** | macOS status bar with service indicators |
| **Library Manager** | Download ZIM files, Ollama models, maps |

---

## Architecture Decisions

### Python
**Bundled.** SVRN ships with its own Python runtime (python-build-standalone or similar). Users install zero Python dependencies. The app knows exactly which Python it's using.

- Bundled at: `SVRN.app/Contents/Resources/python/`
- All pip packages pre-installed in the bundle
- Required packages: `libzim`, `rumps`, (others TBD)

### Ollama
**Not bundled — guided install.** Ollama is a ~1GB binary + models. Bundling is impractical.
- Setup wizard detects if Ollama is installed
- If not: friendly UI walkthrough to ollama.ai download
- If installed: auto-detects path (checks `/usr/local/bin/ollama`, `/opt/homebrew/bin/ollama`, `~/.ollama/bin/ollama`)
- If running: connects immediately

### Storage / Vault
**User-chosen drive, configurable.** On first launch:
1. Setup wizard asks: "Where do you want to store your library? (ZIM files, maps, AI content)"
2. User picks a folder via native macOS folder picker (can be external drive, home folder, anywhere)
3. Path saved to `~/.config/svrn/config.json`
4. All content (ZIMs, maps, models) stored relative to that chosen root

No hardcoded drive name. No `SVRNVAULT.` anywhere in the codebase.

### Paths
**Zero hardcoded user paths.** All paths are:
- Resolved relative to the app bundle at runtime: `APP_ROOT = Path(sys.argv[0]).resolve().parent.parent`
- Or resolved relative to the user's chosen storage root (from config)
- `HOME = Path.home()` used for anything in the user's home directory

### Ollama Binary Path
**Auto-detect at runtime:**
```python
OLLAMA_CANDIDATES = [
    Path("/usr/local/bin/ollama"),       # Intel Homebrew
    Path("/opt/homebrew/bin/ollama"),    # Apple Silicon Homebrew  
    Path.home() / ".ollama/bin/ollama",  # Direct install
    Path("/Applications/Ollama.app/Contents/MacOS/Ollama"),
]
def find_ollama() -> Path | None:
    return next((p for p in OLLAMA_CANDIDATES if p.exists()), None)
```

### Port Conflict Handling
All servers bind with `SO_REUSEADDR` and try fallback ports on failure:
```python
for port in [PRIMARY, PRIMARY+1, PRIMARY+2]:
    try: server.bind(("127.0.0.1", port)); break
    except OSError: continue
```
Chosen port written to `~/.config/svrn/ports.json` so all components know where to find each other.

### Servers
| Service | Primary Port | Role |
|---------|-------------|------|
| Dashboard | 3333 | Main UI + all APIs |
| ZIM/Kiwix | 8888 | ZIM library serving |
| ~~Tile Server~~ | ~~8085~~ | **REMOVED** — dashboard serves PMTiles directly |

### Distribution Format
**.pkg installer inside a .dmg**
- `.dmg` is what Mac users expect for app installs
- `.pkg` inside runs a guided install wizard
- Wizard handles: path setup, Ollama detection, first content download
- No Terminal required at any point

---

## Setup Wizard Flow

First launch experience:

```
1. Welcome screen
   "Welcome to SVRN — your offline knowledge base"
   [Get Started]

2. Storage location
   "Where would you like to store your library?"
   [Choose Folder]  ← native macOS folder picker
   Shows estimated space needed

3. Ollama check
   IF installed: "✓ Ollama detected — AI is ready"
   IF not:       "Ollama powers your AI assistant"
                 [Download Ollama] → opens ollama.ai in browser
                 [I've installed it] → re-checks
                 [Skip for now]  → AI features disabled until installed

4. Choose first content to download
   Grid of options (check to add):
   ☑ Wikipedia (English, no images) — 22 GB
   ☐ Wikipedia (English, with images) — 87 GB  
   ☑ Medical / Health library — 4 GB
   ☑ World map — 2 GB
   ☐ Based Cooking — 0.5 GB
   ☐ AI Model: llama3.2 (3B) — 2 GB  ← fast, lightweight
   ☐ AI Model: phi4 (14B) — 8 GB    ← more capable
   [Start Download]

5. Downloading screen
   Progress bars per item
   "You can start using SVRN while content downloads"
   [Open SVRN]

6. Main dashboard — fully functional
```

---

## File Structure (Target)

```
SVRN.app/
├── Contents/
│   ├── Info.plist
│   ├── MacOS/
│   │   └── SVRN                    ← launcher binary (Swift or shell)
│   └── Resources/
│       ├── python/                 ← bundled Python runtime
│       │   ├── bin/python3
│       │   └── lib/
│       ├── dashboard/              ← dashboard server + HTML
│       │   ├── server.py
│       │   ├── *.html
│       │   └── static/
│       ├── kiwix/                  ← ZIM server
│       │   └── server.py
│       └── assets/                 ← app icons, splash screen
│
~/.config/svrn/
├── config.json                     ← user preferences (storage path, etc.)
└── ports.json                      ← dynamically assigned ports

<USER_STORAGE_ROOT>/               ← wherever user chose
├── zim/                           ← ZIM files
├── maps/                          ← PMTiles files
├── models/                        ← Ollama models (if user chose this location)
├── chat/
│   └── sessions/                  ← AI chat history
└── notes/                         ← user notes
```

---

## What to Pull from sovereign (Reference Implementation)

These components are solid and transfer with path-fixing only:

| Component | Quality | Action |
|-----------|---------|--------|
| `dashboard/server.py` | ✅ Solid | Port with path fixes + port fallback |
| `dashboard/*.html` | ✅ Solid | Direct copy, all assets local |
| `dashboard/static/` | ✅ Solid | Direct copy — all bundles confirmed local |
| `kiwix/server.py` | ✅ Solid | Port with path fixes + embedded mode |
| ZIM split reader | ✅ Excellent | Direct copy |
| Map rendering system | ⚠️ Fragile | Refactor — remove tile_server, solidify combined-style |
| Setup wizard | 🆕 New | Build from scratch |
| App launcher | 🆕 New | Build from scratch |
| Installer (.pkg/.dmg) | 🆕 New | Build from scratch |
| `menubar/app.py` | ⚠️ Broken PYTHONPATH | Port with fixes |

---

## Key Constraints

1. **Zero internet during normal use** — only Nominatim search in maps needs internet (will be replaced with local offline geocoder or clearly labeled)
2. **Zero hardcoded usernames or paths**
3. **Zero CDN dependencies** — all JS/CSS/fonts must be bundled (currently passing: MapLibre, PMTiles, marked.js, highlight.js all confirmed local)
4. **Graceful degradation** — if Ollama not running, AI shows helpful message; if no ZIMs, library shows empty state; if no maps, map shows empty state
5. **SO_REUSEADDR on all sockets** — no silent port crashes

---

## Build Order (Suggested)

1. **Repo scaffold** — directory structure, .gitignore, README
2. **Config system** — `~/.config/svrn/config.json`, path resolution utilities
3. **Port the servers** — dashboard + kiwix with all fixes applied
4. **Setup wizard** — first-run experience
5. **App launcher** — starts all servers, opens browser to localhost:3333
6. **Python bundling** — embed python-build-standalone
7. **Menubar app** — fixed PYTHONPATH, Ollama path detection
8. **Installer** — .pkg + .dmg
9. **Testing** — test on a fresh macOS VM as a new user

---

## Audit Findings Reference

Full audit conducted May 2026 on the `sovereign` project. Key findings:

- **Only 1 runtime internet call in the whole app**: Nominatim geocoding in maps.html (graceful failure)
- **All JS/CSS assets confirmed local**: MapLibre (784KB), PMTiles (51KB), marked (35KB), Leaflet (144KB)
- **All map fonts confirmed local**: 1,024 PBF files (Noto Sans 4 weights × 256 unicode ranges)
- **Map rendering 100% offline**: No external tile sources, no CDN style URLs
- **AI chat gracefully degrades**: Returns 503 with helpful message if Ollama not running
- **ZIM library gracefully degrades**: Shows empty state if libzim not installed or no ZIMs present
- **launchd auto-restart working**: KeepAlive=true on all services
- **Dead component**: tile_server on port 8085 — never used by dashboard, can be deleted
- **NOMAD ZIM path**: `/Users/svrnplanet/project-nomad/storage/zim` — SVRN-specific, remove entirely

---

*Last updated: May 2026*
*Reference implementation: `/Users/svrnplanet/Claude/sovereign/`*
