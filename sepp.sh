#!/usr/bin/env bash
# Bootstrap + launch the hunting agent (pi).
#
#   sepp.sh              # GLM 5.3 flash uncensored (default)
#   sepp.sh deepseek     # DeepSeek v4.1 flash uncensored
#   sepp.sh go           # OpenCode Go model (cheap long sessions; prompt-cache
#                       #   extensions kick in) - model via SEPP_GO_MODEL env,
#                       #   default: deepseek-v4-flash
#   sepp.sh <model-id>   # any model registered under the venice provider
#   sepp.sh provider/model  # explicit provider + model
#   sepp.sh --check      # run smoke checks only
#   sepp.sh --setup-only # bootstrap everything, don't start pi
#
# Idempotent: provisions everything that is missing, touches nothing that
# exists (except backing up + merging pi config), then execs pi.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PI_AGENT_DIR="${HOME}/.pi/agent"
THEME_NAME="opencode-dark"

log()  { printf '\033[2m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[bootstrap]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[bootstrap]\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. .env -----------------------------------------------------------------
if [ ! -f "$ROOT/.env" ]; then
  cat > "$ROOT/.env" <<'TPL'
# Venice API key (https://venice.ai → settings → API keys)
VENICE_API_KEY=
# Optional overrides
# VENICE_BASE_URL=https://api.venice.ai/api/v1
# CAIDO_URL=http://127.0.0.1:8080
# CAIDO_PROXY=http://127.0.0.1:8080
# SEPP_RATE_PAUSE=10
TPL
  die "created $ROOT/.env — put your VENICE_API_KEY in it and rerun"
fi
set -a; source "$ROOT/.env"; set +a
: "${VENICE_API_KEY:?VENICE_API_KEY empty — edit $ROOT/.env}"
export VENICE_API_KEY

# --- 2. repo data dirs (never committed, contents never touched) -------------
mkdir -p "$ROOT/data/tmp" "$ROOT/data/reports" "$ROOT/data/targets" "$ROOT/data/workspaces" "$ROOT/data/spool"

# --- 3. Caido running + accessible? -------------------------------------------
CAIDO_URL_EFFECTIVE="${CAIDO_URL:-http://127.0.0.1:8080}"
if ! curl -sf -m 3 -X POST "$CAIDO_URL_EFFECTIVE/graphql" \
     -H 'Content-Type: application/json' -d '{"query":"{ __typename }"}' \
     | grep -q QueryRoot; then
  die "Caido not reachable at $CAIDO_URL_EFFECTIVE - start the Caido desktop app (proxy + GraphQL) and rerun"
fi
log "caido answering at $CAIDO_URL_EFFECTIVE"

CAIDO_STATUS="$(CAIDO_URL="$CAIDO_URL_EFFECTIVE" caido-cli status 2>&1 || true)"
if grep -q "Auth: OK" <<< "$CAIDO_STATUS"; then
  log "caido auth OK ($(grep -o 'expires[^,]*' <<< "$CAIDO_STATUS" | head -1))"
else
  warn "caido auth not usable (${CAIDO_STATUS//$'\n'/; })"
  warn "run: CAIDO_URL=$CAIDO_URL_EFFECTIVE caido-mcp-server login"
fi

python3 "$ROOT/scripts/export_ca.py" >/dev/null 2>&1 \
  || warn "could not export Caido CA - proxied-TLS curl calls will fail until fixed"

# --- 4. garak venv (only if missing) ------------------------------------------
if [ ! -x "$ROOT/.venv-garak/bin/garak" ]; then
  log "creating .venv-garak (garak + shim deps) - one-time"
  command -v uv >/dev/null || die "uv not found (brew install uv)"
  uv venv --python 3.12 "$ROOT/.venv-garak" >/dev/null
  uv pip install --quiet --python "$ROOT/.venv-garak/bin/python" garak fastapi "uvicorn[standard]"
fi

# --- 5. pi provider config (merge; backup only on actual change) ---------------
mkdir -p "$PI_AGENT_DIR"
MODELS_JSON="$PI_AGENT_DIR/models.json"
python3 - "$MODELS_JSON" <<'PYEOF'
import json, pathlib, shutil, sys, time
p = pathlib.Path(sys.argv[1])
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg.setdefault("$schema", "https://pi.dev/schemas/models.schema.json")
prov = cfg.setdefault("providers", {}).setdefault("venice", {})
prov.setdefault("name", "Venice AI")
prov["baseUrl"] = "https://api.venice.ai/api/v1"
prov["api"] = "openai-completions"
prov["apiKey"] = "$VENICE_API_KEY"
prov.setdefault("compat", {})["supportsDeveloperRole"] = False
models = {m["id"]: m for m in prov.setdefault("models", [])}
for mid in ("glm-5-3-flash-uncensored", "deepseek-v4-1-flash-uncensored"):
    models.setdefault(mid, {"id": mid})
prov["models"] = list(models.values())
new = json.dumps(cfg, indent=2) + "\n"
old = p.read_text() if p.exists() else ""
if new != old:
    if old:
        shutil.copy2(p, p.with_suffix(".json.bak." + str(time.time())))
    p.write_text(new)
    print("models.json updated")
else:
    print("models.json unchanged")
PYEOF

# --- 6. theme (project-local; loaded via project trust - NO agent-dir copy,
#         a second copy with the same name makes pi skip it as a collision) ----
[ -f "$ROOT/.pi/themes/$THEME_NAME.json" ] || die "theme file missing: $ROOT/.pi/themes/$THEME_NAME.json"
SETTINGS="$PI_AGENT_DIR/settings.json"
python3 - "$SETTINGS" "$THEME_NAME" <<'PYEOF'
import json, pathlib, shutil, sys, time
p, theme = pathlib.Path(sys.argv[1]), sys.argv[2]
s = json.loads(p.read_text()) if p.exists() else {}
old = p.read_text() if p.exists() else ""
changed = False
# theme: set unless user chose a light/dark pair
if s.get("theme", "") != theme and "/" not in s.get("theme", ""):
    s["theme"] = theme
    changed = True
# input + output padding: keep EVERYTHING at the same 1-col inset so prompt,
# history and below-editor widget align (measured via tmux capture; editor pad
# >1 makes the prompt stick out vs outputPad/widget which cap at 1 in v1.5.2)
if s.get("editorPaddingX", 0) != 1:
    s["editorPaddingX"] = 1
    changed = True
if s.get("outputPad") is None:
    s["outputPad"] = 1
    changed = True
new = json.dumps(s, indent=2) + "\n"
if changed and new != old:
    if old:
        shutil.copy2(p, p.with_suffix(".json.bak." + str(time.time())))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new)
print("settings ok: theme=%s editorPaddingX=%s outputPad=%s"
      % (s.get("theme"), s.get("editorPaddingX"), s.get("outputPad")),
      "(updated)" if changed else "(unchanged)")
PYEOF

# --- 6b. explicit workspace pruning (decision: NEVER automatic) ---------------
# flag: --prune-workspaces[=DAYS]  (list only)  +--apply-confirm deletes
WS_DAYS=""; WS_APPLY=0
for arg in "$@"; do
  case "$arg" in
    --prune-workspaces*) WS_DAYS="${arg#--prune-workspaces=}"; [ "$WS_DAYS" = "$arg" ] && WS_DAYS=30 ;;
    --apply-confirm) WS_APPLY=1 ;;
  esac
done
if [ -n "$WS_DAYS" ]; then
  log "hunt workspace prune (>=$WS_DAYS days)$([ "$WS_APPLY" = 1 ] && echo ', APPLYING' || echo ', dry-run')"
  EXTRA=""; [ "$WS_APPLY" = 1 ] && EXTRA="--apply"
  python3 "$ROOT/scripts/workspace.py" prune --days "$WS_DAYS" $EXTRA
fi

# --- 7. flag exits (after provisioning, before model logic) --------------------
if [ "${1:-}" = "--check" ]; then exec "$ROOT/scripts/smoke.sh"; fi
if [ "${1:-}" = "--setup-only" ]; then log "setup complete"; exit 0; fi

# --- 8. model selection + slug check ------------------------------------------
pick_model() {
  case "${1:-glm}" in
    glm)      echo "venice|glm-5-3-flash-uncensored" ;;
    deepseek) echo "venice|deepseek-v4-1-flash-uncensored" ;;
    # OpenCode Go gateway: caching extensions (pi-opencode-go-cache) are
    # provider-scoped and activate here; model overridable via SEPP_GO_MODEL.
    go)       echo "opencode-go|${SEPP_GO_MODEL:-deepseek-v4-flash}" ;;
    */*)      echo "${1%%/*}|${1#*/}" ;;
    *)        echo "venice|$1" ;;
  esac
}
IFS='|' read -r PROVIDER MODEL_ID <<< "$(pick_model "${1:-glm}")"

SLUGS="$(curl -sf -m 10 "https://api.venice.ai/api/v1/models" \
  -H "Authorization: Bearer $VENICE_API_KEY" | jq -r '.data[].id' 2>/dev/null || true)"
if [ -n "$SLUGS" ] && [ "$PROVIDER" = "venice" ] && ! grep -qx -- "$MODEL_ID" <<< "$SLUGS"; then
  warn "model '$MODEL_ID' not in Venice /models - renamed? closest:"
  grep -i "${MODEL_ID%%-*}" <<< "$SLUGS" | head -5 | sed 's/^/  /' || true
fi

# --- 9. project extension visibility -------------------------------------------
# .pi/extensions/sepp.ts and .pi/themes/ live in the repo; pi auto-discovers
# them from cwd. Project trust may prompt on first run - that is expected.
[ -f "$ROOT/.pi/extensions/sepp.ts" ] || warn "hunt extension missing?!"
cd "$ROOT"
log "starting pi: provider=$PROVIDER model=$MODEL_ID theme=$THEME_NAME"
exec pi --provider "$PROVIDER" --model "$MODEL_ID"
