#!/bin/bash
# SVRN build script — assembles SVRN.app, installs Python, builds .pkg + .dmg
#
# Usage:
#   ./scripts/build_app.sh              # full build
#   ./scripts/build_app.sh --no-python  # skip Python download (use cache)
#   ./scripts/build_app.sh --app-only   # skip pkg/dmg, just build .app
#
# Requirements (install once):
#   brew install create-dmg   # for the .dmg step

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SVRN_VERSION="1.0.0"
PYTHON_VERSION="3.12.10"
PBS_DATE="20250529"   # python-build-standalone release date — update when bumping Python

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$ROOT/build"
APP_DIR="$BUILD_DIR/SVRN.app"
CONTENTS="$APP_DIR/Contents"
RESOURCES="$CONTENTS/Resources"
PYTHON_CACHE="$BUILD_DIR/.python_cache"

SKIP_PYTHON=false
APP_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --no-python) SKIP_PYTHON=true ;;
        --app-only)  APP_ONLY=true ;;
    esac
done

# ── Detect architecture ────────────────────────────────────────────────────────
ARCH="$(uname -m)"
case "$ARCH" in
    arm64)   PBS_ARCH="aarch64-apple-darwin" ;;
    x86_64)  PBS_ARCH="x86_64-apple-darwin" ;;
    *)       echo "ERROR: Unsupported architecture: $ARCH"; exit 1 ;;
esac

PBS_FILENAME="cpython-${PYTHON_VERSION}+${PBS_DATE}-${PBS_ARCH}-install_only.tar.gz"
PBS_URL="https://github.com/indygreg/python-build-standalone/releases/download/${PBS_DATE}/${PBS_FILENAME}"

echo "╔══════════════════════════════════════╗"
echo "║  SVRN v${SVRN_VERSION} — App Builder"
echo "║  Python ${PYTHON_VERSION} · ${ARCH}"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Step 1: Clean previous build ──────────────────────────────────────────────
echo "▶ Cleaning build directory…"
rm -rf "$APP_DIR"
mkdir -p "$BUILD_DIR" "$PYTHON_CACHE"

# ── Step 2: Create .app bundle structure ──────────────────────────────────────
echo "▶ Creating app bundle structure…"
mkdir -p \
    "$CONTENTS/MacOS" \
    "$RESOURCES/src/config" \
    "$RESOURCES/src/dashboard/static" \
    "$RESOURCES/src/kiwix" \
    "$RESOURCES/launcher"

# ── Step 3: Copy Info.plist ────────────────────────────────────────────────────
echo "▶ Copying Info.plist…"
cp "$ROOT/installer/Info.plist" "$CONTENTS/Info.plist"

# ── Step 4: Build Swift launcher binary ───────────────────────────────────────
echo "▶ Building Swift launcher…"
(cd "$ROOT/swift" && swift build -c release 2>&1 | grep -v "^Build complete" || true)
SWIFT_BINARY="$ROOT/swift/.build/release/SVRN"
if [ ! -f "$SWIFT_BINARY" ]; then
    echo "ERROR: Swift build failed — binary not found at $SWIFT_BINARY"
    exit 1
fi
cp "$SWIFT_BINARY" "$CONTENTS/MacOS/SVRN"
chmod +x "$CONTENTS/MacOS/SVRN"
echo "  Swift binary: $(du -sh "$CONTENTS/MacOS/SVRN" | awk '{print $1}')"

# ── Step 5: Copy source files ──────────────────────────────────────────────────
echo "▶ Copying source files…"
cp "$ROOT/src/config/__init__.py"    "$RESOURCES/src/config/__init__.py"
cp "$ROOT/src/kiwix/server.py"       "$RESOURCES/src/kiwix/server.py"
cp "$ROOT/src/dashboard/server.py"   "$RESOURCES/src/dashboard/server.py"
cp "$ROOT/src/dashboard"/*.html      "$RESOURCES/src/dashboard/"
cp -r "$ROOT/src/dashboard/static/"  "$RESOURCES/src/dashboard/static/"
cp "$ROOT/launcher/launch.py"        "$RESOURCES/launcher/launch.py"

# ── Step 6: App icon ────────────────────────────────────────────────────────────
echo "▶ Installing app icon…"
if [ -f "$ROOT/assets/AppIcon.icns" ]; then
    cp "$ROOT/assets/AppIcon.icns" "$RESOURCES/AppIcon.icns"
else
    echo "  (No AppIcon.icns found — app will use default icon)"
    touch "$RESOURCES/AppIcon.icns"
fi

# ── Step 7: Download / restore Python runtime ─────────────────────────────────
if [ "$SKIP_PYTHON" = true ] && [ -d "$PYTHON_CACHE/python" ]; then
    echo "▶ Restoring Python from cache (--no-python)…"
    cp -r "$PYTHON_CACHE/python" "$RESOURCES/python"
else
    CACHED_TGZ="$PYTHON_CACHE/$PBS_FILENAME"
    if [ -f "$CACHED_TGZ" ]; then
        echo "▶ Using cached Python archive…"
    else
        echo "▶ Downloading Python ${PYTHON_VERSION} (${ARCH})…"
        echo "  URL: $PBS_URL"
        curl -L --progress-bar -o "$CACHED_TGZ" "$PBS_URL"
    fi

    echo "▶ Extracting Python runtime…"
    rm -rf "$PYTHON_CACHE/extracted"
    mkdir -p "$PYTHON_CACHE/extracted"
    tar -xzf "$CACHED_TGZ" -C "$PYTHON_CACHE/extracted"

    EXTRACTED_PYTHON="$(ls -d "$PYTHON_CACHE/extracted"/python* 2>/dev/null | head -1)"
    if [ -z "$EXTRACTED_PYTHON" ]; then
        EXTRACTED_PYTHON="$PYTHON_CACHE/extracted/python"
    fi

    cp -r "$EXTRACTED_PYTHON" "$PYTHON_CACHE/python"
    cp -r "$PYTHON_CACHE/python" "$RESOURCES/python"

    BUNDLED_PY="$RESOURCES/python/bin/python3"
    if ! "$BUNDLED_PY" --version > /dev/null 2>&1; then
        echo "ERROR: Bundled Python failed to run"
        exit 1
    fi
    echo "  Python OK: $("$BUNDLED_PY" --version)"
fi

# ── Step 8: Install pip packages into bundled Python ──────────────────────────
echo "▶ Installing pip packages into bundled Python…"
BUNDLED_PY="$RESOURCES/python/bin/python3"
BUNDLED_PIP="$RESOURCES/python/bin/pip3"

"$BUNDLED_PIP" install --quiet --upgrade pip
"$BUNDLED_PIP" install --quiet libzim
echo "  Installed: libzim"

"$BUNDLED_PY" -c "from libzim.reader import Archive; print('  libzim: OK')"

# ── Step 9: Smoke-test the bundle ─────────────────────────────────────────────
echo "▶ Bundle smoke test…"
PYTHONPATH="$RESOURCES/src" "$BUNDLED_PY" -c "
from config import HOME, SVRN_CONFIG, find_ollama, DEFAULT_PORTS
print(f'  config: OK — HOME={HOME}')
"

# ── Step 10: Ad-hoc code sign ─────────────────────────────────────────────────
echo "▶ Ad-hoc code signing…"
codesign --sign - --force --deep "$APP_DIR" 2>&1 | grep -v "replacing existing signature" || true
echo "  Signed (ad-hoc)"

# ── Step 11: Fix permissions ───────────────────────────────────────────────────
echo "▶ Fixing permissions…"
find "$RESOURCES/python/bin" -type f -exec chmod +x {} \;
chmod +x "$CONTENTS/MacOS/SVRN"

echo ""
echo "✅ SVRN.app built successfully"
echo "   Location: $APP_DIR"
du -sh "$APP_DIR" | awk '{print "   Size: " $1}'
echo ""

if [ "$APP_ONLY" = true ]; then
    echo "Skipping pkg/dmg (--app-only)"
    exit 0
fi

# ── Step 12: Build .pkg ────────────────────────────────────────────────────────
echo "▶ Building installer package (.pkg)…"
PKG_PATH="$BUILD_DIR/SVRN-${SVRN_VERSION}.pkg"

pkgbuild \
    --root "$APP_DIR" \
    --install-location "/Applications/SVRN.app" \
    --identifier "com.tscodework.svrn" \
    --version "$SVRN_VERSION" \
    "$PKG_PATH"

echo "  pkg: $PKG_PATH"
du -sh "$PKG_PATH" | awk '{print "  Size: " $1}'

# ── Step 13: Build .dmg ────────────────────────────────────────────────────────
echo "▶ Building disk image (.dmg)…"
DMG_PATH="$BUILD_DIR/SVRN-${SVRN_VERSION}.dmg"
DMG_STAGING="$BUILD_DIR/dmg_staging"
rm -rf "$DMG_STAGING" "$DMG_PATH"
mkdir -p "$DMG_STAGING"
cp -r "$APP_DIR" "$DMG_STAGING/"

if command -v create-dmg &>/dev/null; then
    create-dmg \
        --volname "SVRN ${SVRN_VERSION}" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "SVRN.app" 150 185 \
        --hide-extension "SVRN.app" \
        --app-drop-link 450 185 \
        --no-internet-enable \
        "$DMG_PATH" \
        "$DMG_STAGING/"
else
    hdiutil create \
        -volname "SVRN ${SVRN_VERSION}" \
        -srcfolder "$DMG_STAGING" \
        -ov -format UDZO \
        "$DMG_PATH"
fi

rm -rf "$DMG_STAGING"
echo "  dmg: $DMG_PATH"
du -sh "$DMG_PATH" | awk '{print "  Size: " $1}'

echo ""
echo "╔══════════════════════════════════════╗"
echo "║  Build complete!"
echo "║"
echo "║  .app → build/SVRN.app"
echo "║  .pkg → build/SVRN-${SVRN_VERSION}.pkg"
echo "║  .dmg → build/SVRN-${SVRN_VERSION}.dmg"
echo "╚══════════════════════════════════════╝"
