#!/usr/bin/env bash
# sepp.sh — receiver for the Caido "SEPP" plugin (Somebody Else's Problem).
# Right-click a request in Caido, declare it SEPP: the Hunt agent inherits it.
# Installed globally as `sepp` (make install-cli); works via symlink from any
# cwd — the repo is located through the symlink's real path.
#
# Called as:
#   sepp <url> <method> [raw-request-file] [cookies] [host] [port] [scheme] [path] [query]
#
# Env overrides:
#   SEPP_REPO        repo root (default: derived from this script's real path)
#   CAIDO_SPOOL_DIR  spool dir (default: $SEPP_REPO/data/spool)
#
# Atomically drops a job pair into the spool directory:
#   <id>.json  metadata        <id>.raw  exact request bytes
# The pi hunt extension watches the spool and injects the drop into the
# live TUI. Never blocks Caido: copies the temp file before the caller cleans it.
set -euo pipefail

# Resolve own real path through any symlinks (portable, no readlink -f).
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
  SELF_DIR="$(cd -P "$(dirname "$SELF")" && pwd)"
  SELF="$(readlink "$SELF")"
  case "$SELF" in
    /*) ;;
    *) SELF="$SELF_DIR/$SELF" ;;
  esac
done
REPO="${SEPP_REPO:-$(cd -P "$(dirname "$SELF")/.." && pwd)}"
SPOOL="${CAIDO_SPOOL_DIR:-$REPO/data/spool}"

URL="${1:-}"; METHOD="${2:-GET}"; RAWFILE="${3:-}"; COOKIES="${4:-}"
HOST="${5:-}"; PORT="${6:-}"; SCHEME="${7:-}"; PATHPART="${8:-}"; QUERY="${9:-}"

if [ -z "$URL" ]; then
  echo "usage: sepp <url> <method> [raw-request-file] [cookies] [host] [port] [scheme] [path] [query]" >&2
  echo "install with: make install-cli (repo caido-integration)" >&2
  exit 1
fi

mkdir -p "$SPOOL"

STAMP="$(date +%Y%m%d-%H%M%S)"
ID="${STAMP}-$$-${RANDOM}"
JSON_TMP="$SPOOL/.${ID}.json.tmp"
RAW_TMP="$SPOOL/.${ID}.raw.tmp"
JSON_DST="$SPOOL/${ID}.json"
RAW_DST="$SPOOL/${ID}.raw"

# Copy raw request bytes FIRST (dispatch deletes temp files after we exit).
HAS_RAW=0
if [ -n "$RAWFILE" ] && [ -f "$RAWFILE" ]; then
  cp "$RAWFILE" "$RAW_TMP"
  HAS_RAW=1
fi

CAIDO_TS="$(date '+%Y-%m-%dT%H:%M:%S%z')" python3 - "$JSON_TMP" \
  "$ID" "$URL" "$METHOD" "$COOKIES" "$HOST" "$PORT" "$SCHEME" "$PATHPART" "$QUERY" "$HAS_RAW" <<'PYEOF'
import json, os, sys
_, dst, sid, url, method, cookies, host, port, scheme, pathpart, query, has_raw = sys.argv
meta = {
    "id": sid,
    "ts": os.environ["CAIDO_TS"],
    "url": url,
    "method": method,
    "host": host,
    "port": port,
    "scheme": scheme,
    "path": pathpart,
    "query": query,
    "cookies": cookies,
    "has_raw": bool(int(has_raw)),
}
with open(dst, "w", encoding="utf-8") as fh:
    json.dump(meta, fh, indent=1)
PYEOF

# Atomic publication: watchers only ever see complete pairs (raw before json).
if [ "$HAS_RAW" = "1" ]; then
  mv "$RAW_TMP" "$RAW_DST"
else
  rm -f "$RAW_TMP"
fi
mv "$JSON_TMP" "$JSON_DST"

echo "SEPP declared: $URL (spool: ${ID})"
