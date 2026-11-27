#!/usr/bin/env bash
# Point the dashboard at a (new) backend tunnel URL.
#
#   hosting/scripts/set-tunnel-url.sh https://<random>.trycloudflare.com
#
# Rewrites in hosting/.env.local:
#   NEXT_PUBLIC_API_BASE_URL=<url>          (http form)
#   NEXT_PUBLIC_WS_URL=<url with wss:>      (ws form, derived)
#
# No restart needed: the /api/v1 catch-all proxy (app/api/v1/[[...path]]/route.ts)
# re-reads .env.local per request via backendBaseUrl.server.ts, and the browser
# bundle picks up NEXT_PUBLIC_WS_URL via HMR in dev.
#
# Guards: refuses to glue onto a final line without a trailing newline, and
# verifies each key still appears exactly once afterwards.

set -eu
cd "$(dirname "$0")/.."
ENV_FILE=.env.local

URL="${1:-}"
if [ -z "$URL" ]; then
  echo "usage: $0 https://<sub>.trycloudflare.com" >&2
  exit 2
fi
# normalize: strip trailing slash, force http(s) scheme
URL="${URL%/}"
case "$URL" in
  http://*|https://*) ;;
  *) URL="https://$URL" ;;
esac
WS_URL=$(echo "$URL" | sed -E 's/^http:/ws:/; s/^https:/wss:/')

[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found (run from hosting/)" >&2; exit 1; }

# Trailing-newline guard: appending to a file whose last line lacks \n glues the
# new key onto the previous value. Fix the file first if needed.
if [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
  echo "" >> "$ENV_FILE"
  echo "fixed missing trailing newline in $ENV_FILE"
fi

md5_before=$(md5sum "$ENV_FILE" | cut -d' ' -f1)

set_key() {
  local key="$1" val="$2"
  local n
  n=$(grep -c "^${key}=" "$ENV_FILE" || true)
  if [ "$n" -eq 1 ]; then
    # portable in-place replace of exactly the anchored key line
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  elif [ "$n" -eq 0 ]; then
    echo "${key}=${val}" >> "$ENV_FILE"
  else
    echo "refusing: $key appears $n times in $ENV_FILE" >&2
    exit 1
  fi
  n=$(grep -c "^${key}=" "$ENV_FILE" || true)
  [ "$n" -eq 1 ] || { echo "post-check failed: $key appears $n times" >&2; exit 1; }
}

set_key NEXT_PUBLIC_API_BASE_URL "$URL"
set_key NEXT_PUBLIC_WS_URL "$WS_URL"

md5_after=$(md5sum "$ENV_FILE" | cut -d' ' -f1)
echo "updated $ENV_FILE (md5 $md5_before -> $md5_after)"
echo "  NEXT_PUBLIC_API_BASE_URL=$URL"
echo "  NEXT_PUBLIC_WS_URL=$WS_URL"
echo "secrets (NEXT_PUBLIC_TUNNEL_AUTH_TOKEN / NEXT_PUBLIC_LOCALTUNNEL_PASSWORD) untouched"
