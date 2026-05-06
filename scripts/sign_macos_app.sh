#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_PATH="${1:-dist/Whisperer.app}"
BUNDLE_ID="${WHISPERER_BUNDLE_ID:-com.whisperer.app}"
IDENTITY="${WHISPERER_CODESIGN_IDENTITY:-}"
REQUIREMENT="${WHISPERER_CODESIGN_REQUIREMENT:-}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Missing $APP_PATH. Run ./build_macos.sh first." >&2
  exit 1
fi

if [[ -z "$IDENTITY" ]]; then
  IDENTITY="-"
fi

if [[ -z "$REQUIREMENT" && "$IDENTITY" == "-" ]]; then
  REQUIREMENT="=designated => identifier \"$BUNDLE_ID\""
fi

codesign_args=(--force --sign "$IDENTITY")
if [[ -n "$REQUIREMENT" ]]; then
  codesign_args+=(--requirements "$REQUIREMENT")
fi

echo "Signing $APP_PATH with identity '$IDENTITY'"
/usr/bin/codesign "${codesign_args[@]}" "$APP_PATH"

DESIGNATED_REQUIREMENT="$(/usr/bin/codesign -dr - "$APP_PATH" 2>&1)"
echo "$DESIGNATED_REQUIREMENT"
if echo "$DESIGNATED_REQUIREMENT" | /usr/bin/grep -q 'designated => cdhash'; then
  echo "Refusing to ship a cdhash-only macOS signature; Accessibility trust would break on every update." >&2
  exit 1
fi

/usr/bin/codesign --verify --deep --strict "$APP_PATH"
echo "Signed $APP_PATH"
