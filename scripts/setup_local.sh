#!/usr/bin/env bash
# setup_local.sh — ONE command to set up this workstation locally:
#
#   make setup          (or: scripts/setup_local.sh)
#
# Does, in order:
#   1. prerequisite check (node/npm, python3, uv) + best-effort install of uv
#   2. pi agent        — npm global package if missing (config lands via sepp.sh)
#   3. caido-cli + caido-mcp-server — community installer if missing
#   4. SEPP plugin      — typecheck, build, package (plugins/sepp/dist)
#   5. global `sepp`    — symlink into ~/.local/bin (Caido -> local spool bridge)
#   6. provisioning    — sepp.sh --setup-only: .env template, Caido checks, CA
#                        export, garak venv, pi models/theme/settings
#   7. smoke test      — scripts/smoke.sh
#   8. summary         — the SEPP plugin install URL (file://) + checklist
#
# Safe to rerun (idempotent). Data (.env, data/, venvs) is never overwritten.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STEP=0
OK=0; PARTIAL=0; FAILED=0

banner() { printf '\n\033[1m[%d/7] %s\033[0m\n' "$1" "$2"; }
good()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
miss()   { printf '  \033[33m⚠\033[0m %s\n' "$*"; ((PARTIAL++)) || true; }
bad()    { printf '  \033[31m✘\033[0m %s\n' "$*"; ((FAILED++)) || true; }

banner 1 "prerequisites (node/npm, python3, uv)"
for t in node npm python3; do
  command -v "$t" >/dev/null 2>&1 && good "$t $("$t" --version 2>&1 | head -1)" \
    || { miss "$t missing — install (brew install $t / nvm / python.org) and rerun"; }
done
NODE_MAJOR="$(node --version 2>/dev/null | sed 's/v\([0-9]*\).*/\1/' || echo 0)"
[ "${NODE_MAJOR:-0}" -ge 20 ] && good "node >= 20 for the plugin build" \
  || miss "node >= 20 required for plugins/sepp build (found: $(node --version 2>/dev/null || echo none))"
if ! command -v uv >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "  installing uv via brew..."
    brew install uv >/dev/null 2>&1 && good "uv installed" || bad "brew install uv failed"
  else
    miss "uv missing and no brew — needed for the garak venv (https://docs.astral.sh/uv/)"
  fi
else
  good "uv $(uv --version 2>&1 | cut -d' ' -f2)"
fi

banner 2 "pi coding agent + pi-subagents"
if command -v pi >/dev/null 2>&1; then
  good "pi on PATH: $(command -v pi)"
else
  echo "  installing @earendil-works/pi-coding-agent (npm global)..."
  npm install -g --no-fund --no-audit @earendil-works/pi-coding-agent >/dev/null 2>&1 \
    && good "pi installed — models/theme/config are provisioned in step 6" \
    || bad "npm install of pi failed (check node/npm above)"
fi
# Detect pi packages by their installed package markers — NEVER by invoking pi
# (any stray args become a chat message and block the setup; ISSUES row 28).
PKG_BASE="$HOME/.pi/agent/npm/node_modules"
PKGS=(
  "pi-subagents:controller -> hunter subagent delegation"
  "pi-opencode-go-cache:prompt caching for OpenCode Go models (dormant for venice)"
  "pi-cache-optimizer:provider-generic prompt/cache-hit optimization + stats"
)
for entry in "${PKGS[@]}"; do
  PKG="${entry%%:*}"; DESC="${entry#*:}"
  MARKER="$PKG_BASE/$PKG/package.json"
  if [ -f "$MARKER" ]; then
    good "$PKG installed ($DESC)"
  elif command -v pi >/dev/null 2>&1; then
    echo "  installing $PKG..."
    if pi install "npm:$PKG" </dev/null >>/tmp/sepp-piinstall.log 2>&1 &&
       [ -f "$MARKER" ]; then
      good "$PKG installed ($DESC)"
    else
      bad "$PKG install failed — see /tmp/sepp-piinstall.log (or run: pi install npm:$PKG)"
    fi
  else
    miss "$PKG missing and pi not installed — rerun after pi is on PATH"
  fi
done

banner 3 "caido-cli + caido-mcp-server"
INSTALLER="https://raw.githubusercontent.com/c0tton-fluff/caido-mcp-server/main/install.sh"
for pair in "caido-cli:cli" "caido-mcp-server:mcp"; do
  BIN="${pair%%:*}"; TOOL="${pair##*:}"
  if command -v "$BIN" >/dev/null 2>&1; then
    good "$BIN ($($BIN --version 2>&1 | head -1 | awk '{print $NF}'))"
  elif curl -fsSL "$INSTALLER" -o /tmp/caido-mcp-install.sh 2>/dev/null; then
    echo "  installing $BIN via community installer..."
    TOOL="$TOOL" bash /tmp/caido-mcp-install.sh >/dev/null 2>&1 \
      && command -v "$BIN" >/dev/null 2>&1 && good "$BIN installed" \
      || bad "$BIN installer ran but binary still missing — install manually: $INSTALLER"
    rm -f /tmp/caido-mcp-install.sh
  else
    bad "$BIN missing and installer unreachable — see $INSTALLER"
  fi
done

banner 4 "SEPP plugin (build + package)"
if (cd "$ROOT" && make plugin-build >/tmp/sepp-build.log 2>&1); then
  ZIP="$ROOT/plugins/sepp/dist/plugin_package.zip"
  [ -f "$ZIP" ] && good "plugin packaged: plugins/sepp/dist/plugin_package.zip" \
    || bad "build log claims success but zip missing (see /tmp/sepp-build.log)"
else
  bad "plugin build failed — see /tmp/sepp-build.log"; tail -5 /tmp/sepp-build.log
fi

banner 5 "global sepp command (repo-independent bridge)"
if (cd "$ROOT" && make install-cli >/dev/null 2>&1) && command -v sepp >/dev/null 2>&1; then
  good "sepp on PATH: $(readlink "$(command -v sepp)" 2>/dev/null || command -v sepp)"
  [ -w "$HOME/.local/bin" ] || true
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) miss "~/.local/bin not on PATH in THIS shell — add it to use \`sepp\`" ;; esac
else
  bad "make install-cli failed"
fi

banner 6 "hunting stack provisioning (Caido checks, CA, garak venv, pi config)"
KEY_EMPTY=1
[ -f "$ROOT/.env" ] && grep -q '^VENICE_API_KEY=..*' "$ROOT/.env" && KEY_EMPTY=0
if [ "$KEY_EMPTY" = 1 ]; then
  miss "VENICE_API_KEY not set in $ROOT/.env — skipping provisioning; fill it and rerun \`make setup\`"
else
  if (cd "$ROOT" && ./sepp.sh --setup-only >/tmp/sepp-setup.log 2>&1); then
    good "provisioned (see /tmp/sepp-setup.log): Caido reachable + auth checked, CA exported,"
    good "garak venv at .venv-garak/, pi models.json + theme + padding settings in place"
    grep -E "caido auth|venv" /tmp/sepp-setup.log | sed 's/^/    /' | head -3
  else
    bad "sepp.sh provisioning failed — see /tmp/sepp-setup.log"; tail -5 /tmp/sepp-setup.log
  fi
fi

banner 7 "smoke test"
if (cd "$ROOT" && ./scripts/smoke.sh >/tmp/sepp-smoke.log 2>&1); then
  good "smoke: all green (tools, Caido GraphQL, CA bundle, helpers, jwt)"
else
  miss "smoke reports problems (often just a stale token):"
  grep -E "MISSING|UNREACHABLE|absent|FAILED" /tmp/sepp-smoke.log | sed 's/^/    /' | head -5
fi

# ---------------------------------------------------------------- summary ----
ZIP="$ROOT/plugins/sepp/dist/plugin_package.zip"
URL=""
[ -f "$ZIP" ] && URL="file://$(python3 -c 'import sys,urllib.request,pathlib; print(urllib.request.pathname2url(str(pathlib.Path(sys.argv[1]).resolve())))' "$ZIP")"

TOKEN_HINT=""
if ls "$HOME/.caido-mcp/tokens/"*.json >/dev/null 2>&1; then
  EXP="$(python3 -c "
import json,glob
f=sorted(glob.glob('$HOME/.caido-mcp/tokens/*.json'))[0]
print(json.load(open(f)).get('expiresAt','?'))" 2>/dev/null)"
  TOKEN_HINT="Caido token: present (expires $EXP — refresh: caido-mcp-server login)"
else
  TOKEN_HINT="Caido token: ABSENT — run: CAIDO_URL=http://127.0.0.1:8080 caido-mcp-server login"
fi

printf '\n\033[1m════ SETUP SUMMARY ════\033[0m\n'
if [ -n "$URL" ]; then
  printf 'SEPP plugin install URL (paste into Caido if it supports URLs,\notherwise use Plugins → Install from file):\n  %s\n' "$URL"
else
  printf 'SEPP plugin: NOT BUILT — fix step 4, then rerun.\n'
fi
printf '%s\n' "$TOKEN_HINT"
printf 'Launch a hunt afterwards:  cd %s && ./sepp.sh\n' "$ROOT"
[ "$FAILED" -gt 0 ] && printf '\n\033[31m%d step(s) failed — scroll up.\033[0m\n' "$FAILED"
[ "$PARTIAL" -gt 0 ] && printf '\n\033[33m%d item(s) need attention — scroll up (⚠).\033[0m\n' "$PARTIAL"
[ "$FAILED" -eq 0 ] && [ "$PARTIAL" -eq 0 ] && printf '\n\033[32mAll green.\033[0m\n'
exit 0
