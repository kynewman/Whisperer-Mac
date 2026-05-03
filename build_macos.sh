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

echo "Built dist/Whisperer.app"
