#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

corepack pnpm run build >/tmp/mva-v2-build.log 2>&1
python3 -m http.server 4173 -d dist >/tmp/mva-v2-http.log 2>&1 &
server_pid=$!
trap 'kill "$server_pid" >/dev/null 2>&1 || true' EXIT
sleep 2

html=$(curl -fsSL http://127.0.0.1:4173)
printf '%s' "$html" | grep -q '<div id="root"></div>'
asset_path=$(printf '%s' "$html" | sed -n 's/.*src="\(\/assets\/[^\"]*\)".*/\1/p' | head -n 1)
[ -n "$asset_path" ]
asset_js=$(curl -fsSL "http://127.0.0.1:4173${asset_path}")
printf '%s' "$asset_js" | grep -q 'StreamWeaver'
