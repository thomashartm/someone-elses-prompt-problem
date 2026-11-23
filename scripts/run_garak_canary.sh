#!/usr/bin/env bash
# garak canary against a target chat via the universal SEPP shim.
#   TARGET=<slug> scripts/run_garak_canary.sh   (default: testbed)
# Small by design: 1 probe, 1 generation, shim paces 10s/req.
# Each target needs a checked adapter and a provisioned baseline.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -n "${TARGET:-}" && -n "${CAIDO_TARGET:-}" && "$TARGET" != "$CAIDO_TARGET" ]]; then
  echo "TARGET and CAIDO_TARGET disagree; canary stopped" >&2
  exit 1
fi
TARGET="${TARGET:-${CAIDO_TARGET:-testbed}}"
if [[ ! "$TARGET" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
  echo "Invalid canary target slug" >&2
  exit 1
fi
cd "$ROOT"
if [[ -n "${CAIDO_DATA_DIR:-}" && ! "$CAIDO_DATA_DIR" -ef "$ROOT/data" ]]; then
  echo "Canary and adapter checks must use the same data directory" >&2
  exit 1
fi
export CAIDO_TARGET="$TARGET"

echo "== precondition: adapter offline check =="
python3 "$ROOT/scripts/check_adapters.py" "$TARGET"

echo "== precondition: token freshness =="
MODEL="$(python3 - "$TARGET" <<'PYMODEL'
import sys
from drivers.adapter_loader import load_adapter
model = load_adapter(sys.argv[1]).default_model()
if not isinstance(model, str) or not model.strip() or model != model.strip() or not model.isprintable():
    sys.exit("Selected adapter has no valid default model")
print(model)
PYMODEL
)"
echo "Canary target=$TARGET model=$MODEL"
if [[ "$TARGET" == testbed ]]; then
  "$ROOT/scripts/harvest_target.py" --slug "$TARGET"
else
  python3 - "$ROOT/data/targets/$TARGET" <<'PYCREDENTIAL'
import json
import math
from pathlib import Path
import sys
import time
directory = Path(sys.argv[1])
config = json.loads((directory / "target.json").read_text())
expiry = config.get("token_exp")
if not (directory / "baseline.req").is_file():
    sys.exit("Selected target is missing its provisioned baseline")
if expiry is not None and (type(expiry) not in (float, int) or not math.isfinite(expiry) or expiry < time.time() + 300):
    sys.exit("Selected target credentials are stale or invalid")
PYCREDENTIAL
fi

PORT=8399
export OPENAI_API_KEY="local-shim-no-key-needed"
export OPENAI_BASE_URL="http://127.0.0.1:${PORT}/v1"

echo "== starting shim on :${PORT} (target: $TARGET) =="
(exec .venv-garak/bin/uvicorn drivers.openai_shim:app --port "$PORT" \
    --app-dir "$ROOT" >/tmp/sepp-shim.log 2>&1) &
SHIM_PID=$!
trap 'kill $SHIM_PID 2>/dev/null || true' EXIT

for i in $(seq 1 20); do
  curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:${PORT}/healthz" | python3 -c '
import json, sys
health = json.load(sys.stdin)
target, model, pid = sys.argv[1:]
if not (health.get("ok") is True and health.get("target") == target
        and health.get("model") == model and health.get("pid") == int(pid)):
    sys.exit("Shim identity does not match the selected canary")
' "$TARGET" "$MODEL" "$SHIM_PID"

mkdir -p "$ROOT/data/reports/garak"
echo "== garak canary (1 generation) =="
# GNU timeout is absent on stock macOS; gtimeout (coreutils) when present,
# otherwise run unguarded (canary is 1 probe; curl/garak enforce their own)
RUN_GARAK=("$ROOT/.venv-garak/bin/garak" --model_type openai -n "$MODEL"
           --generations 1)
if [[ "$TARGET" == testbed ]]; then
  RUN_GARAK+=(-p dan.DAN_Jailbreak)
else
  RUN_GARAK+=(-p test.Test --probe_options '{"test":{"Test":{"prompts":["Reply with OK."]}}}')
fi
if command -v timeout >/dev/null 2>&1; then
  timeout 900 "${RUN_GARAK[@]}"
elif command -v gtimeout >/dev/null 2>&1; then
  gtimeout 900 "${RUN_GARAK[@]}"
else
  "${RUN_GARAK[@]}"
fi 2>&1 | tail -40

echo "== canary done; reports in data/reports/garak/ =="