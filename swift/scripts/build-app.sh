#!/bin/bash
# Builds diarize (CLI) and DiarizeApp.app, then embeds the CLI binary into
# the app bundle's Resources so installing/copying the .app alone gives you
# both - the "Install 'diarize' Command in Terminal" app menu item then
# symlinks it onto PATH.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Building diarize CLI and DiarizeApp (release)"
swift build -c release

APP_BUNDLE=".build/release/DiarizeApp.app"
CLI_BIN=".build/release/diarize"

if [ ! -d "$APP_BUNDLE" ]; then
    echo "!! Expected app bundle not found at $APP_BUNDLE" >&2
    exit 1
fi
if [ ! -f "$CLI_BIN" ]; then
    echo "!! Expected CLI binary not found at $CLI_BIN" >&2
    exit 1
fi

echo "==> Embedding diarize CLI into DiarizeApp.app/Contents/Resources"
mkdir -p "$APP_BUNDLE/Contents/Resources"
cp "$CLI_BIN" "$APP_BUNDLE/Contents/Resources/diarize"

echo "==> Done: $APP_BUNDLE now bundles the diarize CLI"
