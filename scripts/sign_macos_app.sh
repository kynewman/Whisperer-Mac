#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_PATH="${1:-dist/Whisperer.app}"
ENTITLEMENTS="${WHISPERER_ENTITLEMENTS:-entitlements/macos.plist}"
ALLOW_ADHOC="${WHISPERER_ALLOW_ADHOC_SIGNING:-0}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Missing $APP_PATH. Run ./build_macos.sh first." >&2
  exit 1
fi

if ! IDENTITY="$(scripts/find_developer_id_identity.sh 2>/tmp/whisperer-find-identity.err)"; then
  if [[ "$ALLOW_ADHOC" == "1" ]]; then
    IDENTITY="-"
  else
    cat /tmp/whisperer-find-identity.err >&2 || true
    cat >&2 <<'EOF'
No Developer ID Application signing identity was found.

macOS Accessibility permissions are preserved across updates by code identity.
Ad-hoc signed apps are tied to each build and will make users re-approve
Whisperer after updates. Import a Developer ID Application certificate or set:

  WHISPERER_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"

For local-only development builds, explicitly opt in to the old behavior:

  WHISPERER_ALLOW_ADHOC_SIGNING=1 ./build_macos.sh
EOF
    exit 1
  fi
fi

codesign_args=(--force --deep --sign "$IDENTITY")
if [[ "$IDENTITY" != "-" ]]; then
  codesign_args+=(--options runtime --timestamp)
  if [[ -f "$ENTITLEMENTS" ]]; then
    codesign_args+=(--entitlements "$ENTITLEMENTS")
  fi
fi

echo "Signing $APP_PATH with identity '$IDENTITY'"
/usr/bin/codesign "${codesign_args[@]}" "$APP_PATH"

DESIGNATED_REQUIREMENT="$(/usr/bin/codesign -dr - "$APP_PATH" 2>&1)"
SIGNING_INFO="$(/usr/bin/codesign -dv --verbose=4 "$APP_PATH" 2>&1)"
echo "$DESIGNATED_REQUIREMENT"

if [[ "$ALLOW_ADHOC" != "1" ]] && echo "$DESIGNATED_REQUIREMENT" | /usr/bin/grep -q 'designated => cdhash'; then
  echo "Refusing to ship a cdhash-only macOS signature; Accessibility trust would break on every update." >&2
  exit 1
fi

if [[ "$IDENTITY" != "-" ]]; then
  if ! echo "$DESIGNATED_REQUIREMENT" | /usr/bin/grep -q 'anchor apple generic'; then
    echo "Developer ID signature did not produce an Apple-anchored designated requirement." >&2
    exit 1
  fi
  if echo "$SIGNING_INFO" | /usr/bin/grep -q 'Signature=adhoc'; then
    echo "Developer ID release unexpectedly has an ad-hoc signature." >&2
    exit 1
  fi
  if echo "$SIGNING_INFO" | /usr/bin/grep -q 'TeamIdentifier=not set'; then
    echo "Developer ID release is missing a TeamIdentifier." >&2
    exit 1
  fi
  if ! echo "$SIGNING_INFO" | /usr/bin/grep -q 'Runtime Version='; then
    echo "Developer ID release is missing the hardened runtime." >&2
    exit 1
  fi
else
  echo "Warning: ad-hoc local build; do not publish this artifact."
fi

/usr/bin/codesign --verify --deep --strict "$APP_PATH"
echo "Signed $APP_PATH"
