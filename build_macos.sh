#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt

NODE_BIN="${NODE_BIN:-}"
if [[ -z "$NODE_BIN" ]]; then
  for candidate in \
    "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node" \
    "$(command -v node 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      NODE_BIN="$candidate"
      break
    fi
  done
fi

NPM_BIN="${NPM_BIN:-}"
if [[ -z "$NPM_BIN" && -n "$NODE_BIN" ]]; then
  adjacent_npm="$(dirname "$NODE_BIN")/npm"
  if [[ -x "$adjacent_npm" ]]; then
    NPM_BIN="$adjacent_npm"
  fi
fi
if [[ -z "$NPM_BIN" ]]; then
  NPM_BIN="$(command -v npm 2>/dev/null || true)"
fi

if [[ -n "$NPM_BIN" ]]; then
  "$NPM_BIN" --prefix whisperer-app install
  "$NPM_BIN" --prefix whisperer-app run build
elif [[ -n "$NODE_BIN" ]]; then
  "$NODE_BIN" scripts/install_node_modules_from_lock.mjs whisperer-app
  (cd whisperer-app && "$NODE_BIN" node_modules/vite/bin/vite.js build)
else
  echo "Node.js or npm is required to build the embedded dashboard." >&2
  exit 1
fi

python -m pip install pyinstaller
pyinstaller --noconfirm whisperer-macos.spec
scripts/sign_macos_app.sh dist/Whisperer.app
scripts/create_macos_dmg.sh
scripts/sign_macos_dmg.sh dist/Whisperer-macOS-arm64.dmg

REQUIRE_NOTARIZATION="${WHISPERER_REQUIRE_NOTARIZATION:-0}"
if [[ "${WHISPERER_ALLOW_ADHOC_SIGNING:-0}" == "1" ]]; then
  echo "Skipping notarization for explicit ad-hoc local build."
elif [[ "${WHISPERER_SKIP_NOTARIZATION:-0}" == "1" ]]; then
  echo "Skipping notarization by request. Do not publish this DMG."
else
  scripts/notarize_macos_dmg.sh dist/Whisperer-macOS-arm64.dmg
  REQUIRE_NOTARIZATION=1
fi
WHISPERER_REQUIRE_NOTARIZATION="$REQUIRE_NOTARIZATION" scripts/verify_macos_release.sh dist/Whisperer-macOS-arm64.dmg

echo "Built dist/Whisperer.app"
echo "Built dist/Whisperer-macOS-arm64.dmg"
