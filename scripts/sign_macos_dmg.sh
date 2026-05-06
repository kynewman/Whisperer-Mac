#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

DMG_PATH="${1:-dist/Whisperer-macOS-arm64.dmg}"
ALLOW_ADHOC="${WHISPERER_ALLOW_ADHOC_SIGNING:-0}"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "Missing $DMG_PATH. Run ./build_macos.sh first." >&2
  exit 1
fi

if ! IDENTITY="$(scripts/find_developer_id_identity.sh 2>/tmp/whisperer-find-identity.err)"; then
  if [[ "$ALLOW_ADHOC" == "1" ]]; then
    echo "Skipping DMG signing for explicit ad-hoc local build."
    exit 0
  fi
  cat /tmp/whisperer-find-identity.err >&2 || true
  echo "No Developer ID Application identity is available for DMG signing." >&2
  exit 1
fi

echo "Signing $DMG_PATH with identity '$IDENTITY'"
/usr/bin/codesign --force --sign "$IDENTITY" --timestamp "$DMG_PATH"
/usr/bin/codesign --verify --strict "$DMG_PATH"
echo "Signed $DMG_PATH"
