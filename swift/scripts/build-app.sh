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

echo "==> Done: $APP_BUNDLE now bundles the diarize CLI"
