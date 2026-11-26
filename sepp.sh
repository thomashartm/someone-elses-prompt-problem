#!/usr/bin/env bash
# Bootstrap + launch the hunting agent (pi).
#
#   sepp.sh              # GLM 5.3 flash uncensored (default)
#   sepp.sh deepseek     # DeepSeek v4.1 flash uncensored
#   sepp.sh inkling      # Local Ollama: inkling-2-mini:26b-a5b-nvfp4
#   sepp.sh ollama/hf.co/mradermacher/DeepHat-V1-7B-GGUF:Q8_0
#   sepp.sh qwen         # Local Ollama: qwen2.5:7b
#   sepp.sh go           # OpenCode Go model (cheap long sessions; prompt-cache
#                       #   extensions kick in) - model via SEPP_GO_MODEL env,
#                       #   default: deepseek-v4-flash
#   sepp.sh <model-id>   # any model registered under the venice provider
#   sepp.sh provider/model  # explicit provider + model (e.g. ollama/<tag>)
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
export VENICE_API_KEY="${VENICE_API_KEY:-}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434/v1}"

# --- 2. repo data dirs (never committed, contents never touched) -------------
mkdir -p "$ROOT/data/tmp" "$ROOT/data/reports" "$ROOT/data/targets" "$ROOT/data/workspaces" "$ROOT/data/spool"

# --- 3. Caido running + accessible? -------------------------------------------
python3 "$ROOT/scripts/check_caido.py" || die "Caido CLI preflight failed"
log "Caido CLI authentication and instance OK"

python3 "$ROOT/scripts/export_ca.py" >/dev/null 2>&1 \
  || warn "could not export Caido CA - proxied-TLS curl calls will fail until fixed"

# --- 4. garak venv (create once, repair stale entry-script shebangs) ----------
if [ ! -x "$ROOT/.venv-garak/bin/garak" ]; then
  log "creating .venv-garak (garak + shim deps) - one-time"
  command -v uv >/dev/null || die "uv not found (brew install uv)"
  uv venv --python 3.12 "$ROOT/.venv-garak" >/dev/null
  uv pip install --quiet --python "$ROOT/.venv-garak/bin/python" garak fastapi "uvicorn[standard]"
fi
# uv bakes an ABSOLUTE shebang into every console script at install time, so a
# repo move/rename leaves them pointing at a path that no longer exists
# ("bad interpreter: .../sep/.venv-garak/bin/python"). Repair in place: far
# cheaper than recreating the venv, and idempotent when nothing is stale.
python3 - "$ROOT/.venv-garak" <<'PYEOF'
import os, pathlib, sys

bindir = pathlib.Path(sys.argv[1]) / "bin"
want = f"#!{bindir / 'python'}"
fixed = []
for entry in bindir.iterdir():
    if entry.is_symlink() or not entry.is_file():
        continue
    try:
        with entry.open("rb") as fh:
            if fh.read(2) != b"#!":
                continue
        text = entry.read_text(errors="replace")
    except OSError:
        continue
    first, _, rest = text.partition("\n")
    if "/bin/python" not in first or first == want:
        continue
    entry.write_text(want + "\n" + rest)
    os.chmod(entry, 0o755)
    fixed.append(entry.name)
if fixed:
    print(f"repaired {len(fixed)} stale venv shebang(s) in .venv-garak/bin")
PYEOF

# --- 5. pi provider config (merge; backup only on actual change) ---------------
mkdir -p "$PI_AGENT_DIR"
MODELS_JSON="$PI_AGENT_DIR/models.json"
python3 - "$MODELS_JSON" "$OLLAMA_BASE_URL" <<'PYEOF'
import json, pathlib, shutil, sys, time, urllib.request

p = pathlib.Path(sys.argv[1])
ollama_base = sys.argv[2].rstrip("/")
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg.setdefault("$schema", "https://pi.dev/schemas/models.schema.json")

# Venice AI
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

# Local Ollama (OpenAI-compatible endpoint; literal dummy apiKey so pi treats it
# as ready without extra auth and users can switch via /model at any time)
ZERO_COST = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
KNOWN_OLLAMA = [
    {
        "id": "inkling-2-mini:26b-a5b-nvfp4",
        "name": "Inkling 2 Mini 26B (local)",
        "reasoning": True,
        "input": ["text", "image"],
        "contextWindow": 1048576,
        "maxTokens": 16384,
        "cost": ZERO_COST,
    },
    {
        "id": "qwen2.5:7b",
        "name": "Qwen 2.5 7B (local)",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 32768,
        "maxTokens": 8192,
        "cost": ZERO_COST,
    },
]
oprov = cfg["providers"].setdefault("ollama", {})
oprov["name"] = "Ollama (local)"
oprov["baseUrl"] = ollama_base
oprov["api"] = "openai-completions"
oprov["apiKey"] = "ollama"
ocomp = oprov.setdefault("compat", {})
ocomp["supportsDeveloperRole"] = False
ocomp["supportsStore"] = False

omodels = {m["id"]: m for m in oprov.setdefault("models", [])}
for entry in KNOWN_OLLAMA:
    cur = omodels.get(entry["id"], {})
    merged = {**entry, **cur}
    # keep accurate metadata up to date for known local models
    for k in ("name", "reasoning", "input", "contextWindow", "maxTokens", "cost"):
        merged[k] = entry[k]
    omodels[entry["id"]] = merged

# Best-effort discovery of any other pulled Ollama models (2s timeout, non-fatal)
try:
    with urllib.request.urlopen(f"{ollama_base}/models", timeout=2) as resp:
        data = json.loads(resp.read().decode())
    for item in data.get("data", []):
        mid = item.get("id")
        if mid and mid not in omodels:
            omodels[mid] = {"id": mid, "name": f"{mid} (local)", "cost": ZERO_COST}
except Exception:
    pass

oprov["models"] = list(omodels.values())

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
    glm)                  echo "venice|glm-5-3-flash-uncensored" ;;
    deepseek)             echo "venice|deepseek-v4-1-flash-uncensored" ;;
    inkling|inkling-2-mini) echo "ollama|inkling-2-mini:26b-a5b-nvfp4" ;;
    qwen)                 echo "ollama|qwen2.5:7b" ;;
    # OpenCode Go gateway: caching extensions (pi-opencode-go-cache) are
    # provider-scoped and activate here; model overridable via SEPP_GO_MODEL.
    go)                   echo "opencode-go|${SEPP_GO_MODEL:-deepseek-v4-flash}" ;;
    */*)                  echo "${1%%/*}|${1#*/}" ;;
    *)                    echo "venice|$1" ;;
  esac
}
IFS='|' read -r PROVIDER MODEL_ID <<< "$(pick_model "${1:-glm}")"

if [ "$PROVIDER" = "venice" ]; then
  : "${VENICE_API_KEY:?VENICE_API_KEY empty — edit $ROOT/.env (or launch a local model: ./sepp.sh inkling)}"
  SLUGS="$(curl -sf -m 10 "https://api.venice.ai/api/v1/models" \
    -H "Authorization: Bearer $VENICE_API_KEY" | jq -r '.data[].id' 2>/dev/null || true)"
  if [ -n "$SLUGS" ] && ! grep -qx -- "$MODEL_ID" <<< "$SLUGS"; then
    warn "model '$MODEL_ID' not in Venice /models - renamed? closest:"
    grep -i "${MODEL_ID%%-*}" <<< "$SLUGS" | head -5 | sed 's/^/  /' || true
  fi
elif [ "$PROVIDER" = "ollama" ]; then
  OLLAMA_MODELS="$(curl -sf -m 5 "${OLLAMA_BASE_URL%/}/models" | jq -r '.data[].id' 2>/dev/null || true)"
  if [ -z "$OLLAMA_MODELS" ]; then
    die "Ollama not reachable at $OLLAMA_BASE_URL — start Ollama (ollama serve) and rerun"
  fi
  if ! grep -qx -- "$MODEL_ID" <<< "$OLLAMA_MODELS"; then
    warn "model '$MODEL_ID' not installed in Ollama ($OLLAMA_BASE_URL) — available:"
    sed 's/^/  /' <<< "$OLLAMA_MODELS"
    die "pull it first: ollama pull $MODEL_ID"
  fi
fi

# --- 9. project extension visibility -------------------------------------------
# .pi/extensions/sepp.ts and .pi/themes/ live in the repo; pi auto-discovers
# them from cwd. Project trust may prompt on first run - that is expected.
[ -f "$ROOT/.pi/extensions/sepp.ts" ] || warn "hunt extension missing?!"
cd "$ROOT"
log "starting pi: provider=$PROVIDER model=$MODEL_ID theme=$THEME_NAME"
exec pi --provider "$PROVIDER" --model "$MODEL_ID"
