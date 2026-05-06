#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

DMG_PATH="${1:-dist/Whisperer-macOS-arm64.dmg}"
ALLOW_ADHOC="${WHISPERER_ALLOW_ADHOC_SIGNING:-0}"
REQUIRE_NOTARIZATION="${WHISPERER_REQUIRE_NOTARIZATION:-0}"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "Missing $DMG_PATH. Run ./build_macos.sh first." >&2
  exit 1
fi

MOUNT_DIR="$(/usr/bin/mktemp -d /tmp/whisperer-dmg-verify.XXXXXX)"
cleanup() {
  /usr/bin/hdiutil detach "$MOUNT_DIR" >/dev/null 2>&1 || true
  /bin/rm -rf "$MOUNT_DIR"
}
trap cleanup EXIT

/usr/bin/hdiutil attach "$DMG_PATH" -nobrowse -readonly -mountpoint "$MOUNT_DIR" >/dev/null
APP_PATH="$MOUNT_DIR/Whisperer.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "No Whisperer.app found inside $DMG_PATH" >&2
  exit 1
fi

DR="$(/usr/bin/codesign -dr - "$APP_PATH" 2>&1)"
INFO="$(/usr/bin/codesign -dv --verbose=4 "$APP_PATH" 2>&1)"
echo "$DR"

if [[ "$ALLOW_ADHOC" != "1" ]] && echo "$DR" | /usr/bin/grep -q 'designated => cdhash'; then
  echo "Release DMG contains a cdhash-only app signature." >&2
  exit 1
fi

if [[ "$ALLOW_ADHOC" != "1" ]]; then
  if ! echo "$DR" | /usr/bin/grep -q 'anchor apple generic'; then
    echo "Release DMG app is not signed with a Developer ID Apple-anchored requirement." >&2
    exit 1
  fi
  if echo "$INFO" | /usr/bin/grep -q 'Signature=adhoc'; then
    echo "Release DMG app is ad-hoc signed." >&2
    exit 1
  fi
  if echo "$INFO" | /usr/bin/grep -q 'TeamIdentifier=not set'; then
    echo "Release DMG app is missing a TeamIdentifier." >&2
    exit 1
  fi
fi

/usr/bin/codesign --verify --deep --strict "$APP_PATH"
if [[ "$REQUIRE_NOTARIZATION" == "1" ]]; then
  /usr/bin/xcrun stapler validate "$DMG_PATH"
  /usr/sbin/spctl -a -vvv -t open --context context:primary-signature "$DMG_PATH"
fi

echo "Verified $DMG_PATH"
