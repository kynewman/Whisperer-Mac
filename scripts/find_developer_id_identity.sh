#!/bin/zsh
set -euo pipefail

IDENTITY="${WHISPERER_CODESIGN_IDENTITY:-}"
if [[ -z "$IDENTITY" ]]; then
  IDENTITY="$(/usr/bin/security find-identity -v -p codesigning 2>/dev/null \
    | /usr/bin/sed -n 's/.*"\(Developer ID Application:.*\)".*/\1/p' \
    | /usr/bin/head -n 1)"
fi

if [[ -z "$IDENTITY" ]]; then
  exit 1
fi

if [[ "$IDENTITY" != Developer\ ID\ Application:* ]]; then
  echo "Refusing non-Developer ID identity: $IDENTITY" >&2
  exit 2
fi

echo "$IDENTITY"
