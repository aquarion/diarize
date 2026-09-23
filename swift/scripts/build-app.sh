#!/bin/bash
# Builds diarize (CLI) and DiarizeApp, then assembles a proper DiarizeApp.app
# bundle around the DiarizeApp executable and embeds the CLI binary into its
# Resources - installing/copying the .app alone gives you both, and the
# "Install 'diarize' Command in Terminal" app menu item then symlinks it onto
# PATH.
#
# SwiftPM's `swift build` never produces a macOS .app bundle on its own (with
# either the native or `swiftbuild` build system) - it only emits a plain
# executable. So this script builds the raw executable and hand-assembles the
# bundle structure (Info.plist, Contents/MacOS, Contents/Resources) itself.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Building diarize CLI and DiarizeApp (release)"
swift build -c release

BIN_DIR=".build/release"
APP_BUNDLE="$BIN_DIR/DiarizeApp.app"
APP_BIN="$BIN_DIR/DiarizeApp"
CLI_BIN="$BIN_DIR/diarize"
BUNDLE_VERSION="${DIARIZE_APP_VERSION:-1.0}"
ICON_DIR="../docs/branding/diarize.icon"
ICON_GLYPH="$ICON_DIR/Assets/noun_transcript_8458812_FFFFFF.svg"

# CFBundleVersion/CFBundleShortVersionString both go straight into the
# Info.plist XML below, and CFBundleVersion is specifically required by
# Launch Services to be 1-3 period-separated integers with a non-zero first
# component - reject anything else up front instead of writing a malformed
# or invalid plist and exiting 0 anyway.
if ! [[ "$BUNDLE_VERSION" =~ ^[1-9][0-9]*(\.[0-9]+){0,2}$ ]]; then
    echo "!! Invalid DIARIZE_APP_VERSION '$BUNDLE_VERSION': must be 1-3 period-separated integers with a non-zero first component (e.g. 1.0 or 1.2.3)" >&2
    exit 1
fi

if [ ! -f "$APP_BIN" ]; then
    echo "!! Expected DiarizeApp executable not found at $APP_BIN" >&2
    exit 1
fi
if [ ! -f "$CLI_BIN" ]; then
    echo "!! Expected CLI binary not found at $CLI_BIN" >&2
    exit 1
fi
if [ ! -f "$ICON_GLYPH" ]; then
    echo "!! Expected app icon glyph not found at $ICON_GLYPH" >&2
    exit 1
fi

echo "==> Assembling DiarizeApp.app bundle"
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"
cp "$APP_BIN" "$APP_BUNDLE/Contents/MacOS/DiarizeApp"

cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>DiarizeApp</string>
    <key>CFBundleIdentifier</key>
    <string>net.istic.diarize</string>
    <key>CFBundleName</key>
    <string>Diarize</string>
    <key>CFBundleDisplayName</key>
    <string>Diarize</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleShortVersionString</key>
    <string>$BUNDLE_VERSION</string>
    <key>CFBundleVersion</key>
    <string>$BUNDLE_VERSION</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

echo "==> Embedding diarize CLI into DiarizeApp.app/Contents/Resources"
cp "$CLI_BIN" "$APP_BUNDLE/Contents/Resources/diarize"

# Icon Composer's Liquid Glass .icon format has no public CLI compiler
# (xcrun actool silently ignores it outside an Xcode-project asset-catalog
# build - see #45) - annealer (https://github.com/istic/annealer) is a
# from-scratch reimplementation that renders it to a flat PNG in plain
# Node, which sips/iconutil (both ship with the Xcode CLT) then turn into a
# classic .icns, same as a flat source image would.
#
# Pinned to a commit, not a semver tag: this icon's fill is a designer-authored
# multi-stop "linear-gradient" (see docs/branding/diarize.icon/icon.json),
# and annealer's multi-stop-gradient support hasn't shipped in a numbered
# release yet (only on main). Switch ANNEALER_REF to e.g.
# "@istic-co/annealer@^1.1.0" once it has.
ANNEALER_REF="github:istic/annealer#0fa06bb2c156ecbb5199616cfe0c04fb5e868077"
if ! command -v npx >/dev/null 2>&1; then
    echo "!! npx not found - this script renders the app icon via annealer (https://github.com/istic/annealer), which needs Node.js. Install Node (e.g. 'brew install node') and re-run." >&2
    exit 1
fi
ICON_RENDER_DIR="$(mktemp -d)"
echo "==> Rendering AppIcon source from $ICON_DIR via annealer"
# --background-color is required by annealer's CLI but unused for this
# icon: it's only consulted for "automatic-gradient"/"flat-color" fills,
# and this icon's fill is an explicit "linear-gradient".
npx --yes "$ANNEALER_REF" \
    --icon-path "$ICON_DIR" \
    --glyph "$ICON_GLYPH" \
    --background-color "#0AC1DB" \
    --target apple \
    --output-dir "$ICON_RENDER_DIR"
ICON_SRC="$ICON_RENDER_DIR/apple-touch-icon.png"
if [ ! -f "$ICON_SRC" ]; then
    echo "!! annealer did not produce $ICON_SRC" >&2
    exit 1
fi

echo "==> Generating AppIcon.icns from $ICON_SRC"
ICONSET_DIR="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET_DIR"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$ICON_SRC" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$ICON_SRC" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET_DIR" -o "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$ICONSET_DIR")"

echo "==> Done: $APP_BUNDLE now bundles the diarize CLI and app icon"
