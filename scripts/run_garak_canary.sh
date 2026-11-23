#!/usr/bin/env bash
# garak canary against a target chat via the universal SEPP shim.
#   TARGET=<slug> scripts/run_garak_canary.sh   (default: testbed)
# Small by design: 1 probe, 1 generation, shim paces 10s/req.
# Precondition: scripts/harvest_target.py exits 0 (fresh cookie present)
# and the target has an adapter (scripts/check_adapters.py green).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${TARGET:-testbed}"

echo "== precondition: adapter offline check =="
python3 "$ROOT/scripts/check_adapters.py" "$TARGET"

echo "== precondition: token freshness =="
"$ROOT/scripts/harvest_target.py"

PORT=8399
export CAIDO_TARGET="$TARGET"
export OPENAI_API_KEY="local-shim-no-key-needed"
export OPENAI_BASE_URL="http://127.0.0.1:${PORT}/v1"

echo "== starting shim on :${PORT} (target: $TARGET) =="
(.venv-garak/bin/uvicorn drivers.openai_shim:app --port "$PORT" \
    --app-dir "$ROOT" >/tmp/sepp-shim.log 2>&1) &
SHIM_PID=$!
trap 'kill $SHIM_PID 2>/dev/null || true' EXIT

for i in $(seq 1 20); do
  curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null || { echo "shim not healthy"; exit 1; }

mkdir -p "$ROOT/data/reports/garak"
echo "== garak canary (1 generation) =="
# GNU timeout is absent on stock macOS; gtimeout (coreutils) when present,
# otherwise run unguarded (canary is 1 probe; curl/garak enforce their own)
RUN_GARAK=("$ROOT/.venv-garak/bin/garak" --model_type openai -n testbed-chat
           --generations 1 -p dan.DAN_Jailbreak)
if command -v timeout >/dev/null 2>&1; then
  RUN_GARAK=(timeout 900 "${RUN_GARAK[@]}")
elif command -v gtimeout >/dev/null 2>&1; then
  RUN_GARAK=(gtimeout 900 "${RUN_GARAK[@]}")
fi

RUN_DIR="$(mktemp -d "$ROOT/data/reports/garak/canary.XXXXXXXX")"
REPORT_PREFIX="$RUN_DIR/canary"
"${RUN_GARAK[@]}" --report_prefix "$REPORT_PREFIX" 2>&1 | tail -40

REPORT="${REPORT_PREFIX}.report.jsonl"
python3 "$ROOT/scripts/verify_garak_report.py" "$REPORT"
