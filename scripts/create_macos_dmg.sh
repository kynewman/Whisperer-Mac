#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_PATH="${APP_PATH:-dist/Whisperer.app}"
DMG_PATH="${DMG_PATH:-dist/Whisperer-macOS-arm64.dmg}"
VOLUME_NAME="${VOLUME_NAME:-Whisperer Mac}"
STAGING_DIR="build/dmg-staging"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Missing $APP_PATH. Run ./build_macos.sh first." >&2
  exit 1
fi

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

ditto "$APP_PATH" "$STAGING_DIR/Whisperer.app"
ln -s /Applications "$STAGING_DIR/Applications"

mkdir -p "$(dirname "$DMG_PATH")"
rm -f "$DMG_PATH"

hdiutil create \
  -volname "$VOLUME_NAME" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Built $DMG_PATH"
