#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

DMG_PATH="${1:-dist/Whisperer-macOS-arm64.dmg}"
PROFILE="${WHISPERER_NOTARY_KEYCHAIN_PROFILE:-}"
APPLE_ID="${WHISPERER_NOTARY_APPLE_ID:-}"
TEAM_ID="${WHISPERER_NOTARY_TEAM_ID:-}"
PASSWORD="${WHISPERER_NOTARY_PASSWORD:-}"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "Missing $DMG_PATH. Run ./build_macos.sh first." >&2
  exit 1
fi

notary_args=()
if [[ -n "$PROFILE" ]]; then
  notary_args+=(--keychain-profile "$PROFILE")
elif [[ -n "$APPLE_ID" && -n "$TEAM_ID" && -n "$PASSWORD" ]]; then
  notary_args+=(--apple-id "$APPLE_ID" --team-id "$TEAM_ID" --password "$PASSWORD")
else
  cat >&2 <<'EOF'
Missing notarization credentials.

Create a notarytool keychain profile:

  xcrun notarytool store-credentials whisperer-notary \
    --apple-id you@example.com \
    --team-id TEAMID \
    --password app-specific-password

Then run:

  WHISPERER_NOTARY_KEYCHAIN_PROFILE=whisperer-notary ./build_macos.sh
EOF
  exit 1
fi

echo "Submitting $DMG_PATH for notarization"
/usr/bin/xcrun notarytool submit "$DMG_PATH" --wait "${notary_args[@]}"
/usr/bin/xcrun stapler staple "$DMG_PATH"
/usr/bin/xcrun stapler validate "$DMG_PATH"
echo "Notarized and stapled $DMG_PATH"
