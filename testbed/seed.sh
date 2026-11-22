#!/usr/bin/env bash
# SEPP testbed seed + B1 roundtrip verification.
#
# Drives the seeded request flow against the local testbed; when Caido is
# reachable the same requests go THROUGH the proxy so hunt history has
# orientation material. Assertions are tier-agnostic (tier-specific
# strict/naive matrices live in testbed/verify.sh, phase B3).
#
# Usage: testbed/seed.sh          (env: TESTBED_BASE, CAIDO_PROXY)
# Exit 0 = all assertions green.
set -euo pipefail
BASE="${TESTBED_BASE:-http://127.0.0.1:8765}"
PROXY="${CAIDO_PROXY:-http://127.0.0.1:8080}"
export CAIDO_URL="${CAIDO_URL:-http://127.0.0.1:8080}"   # caido-cli requirement

say() { printf '%s\n' "$*"; }

# ---------- wait for health ----------
for _ in $(seq 1 30); do
  if curl -sf -m 2 "$BASE/healthz" >/dev/null 2>&1; then break; fi
  sleep 1
done
HEALTH="$(curl -sfS -m 5 "$BASE/healthz")"
echo "$HEALTH" | grep -q '"ok"[[:space:]]*:[[:space:]]*true' || { say "FAIL: unhealthy: $HEALTH"; exit 1; }
GUARDS="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["guards"])' <<<"$HEALTH")"
say "== testbed up: guards=$GUARDS backend=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["backend"])' <<<"$HEALTH")"

# ---------- traffic path ----------
# Primary: Caido Replay API (caido-cli send) - recorded in history by design.
# Fallback curl uses --noproxy '' EXPLICITLY: operator shells commonly export
# NO_PROXY=127.0.0.1,localhost which makes plain `curl -x` SILENTLY bypass the
# proxy for loopback targets (ISSUES.md row 42, curl -v proven). With this
# override the fallback reaches Caido and is recorded.
PROXY_MODE=cli
if ! caido-cli status >/dev/null 2>&1; then
  PROXY_MODE=direct
  say "== caido-cli not authenticated - seeding DIRECT (history will NOT populate)"
else
  say "== traffic path: Caido Replay API (history populates)"
fi

req() { # req METHOD PATH [JSON]
  local method="$1" path="$2" body="${3:-}" out rc
  if [ "$PROXY_MODE" = cli ]; then
    if [ -n "$body" ]; then
      out="$(caido-cli send "$method" "$BASE$path" -H 'Content-Type: application/json' -j "$body" 2>&1)"
    else
      out="$(caido-cli send "$method" "$BASE$path" 2>&1)"
    fi
    rc=$?
    if [ $rc -ne 0 ]; then say "FAIL: caido-cli send $method $path (rc=$rc): $out" >&2; return 1; fi
    printf '%s\n' "$out" | sed -n '/^---$/,$p' | tail -n +2
  else
    if [ -n "$body" ]; then
      curl -sfS -m 10 --noproxy '' -X "$method" -H 'Content-Type: application/json' -d "$body" "$BASE$path"
    else
      curl -sfS -m 10 --noproxy '' -X "$method" "$BASE$path"
    fi
  fi
}

authed() { # authed METHOD PATH TOKEN [JSON]
  local method="$1" path="$2" token="$3" body="${4:-}" out rc
  if [ "$PROXY_MODE" = cli ]; then
    if [ -n "$body" ]; then
      out="$(caido-cli send "$method" "$BASE$path" -H "Authorization: Bearer $token" \
        -H 'Content-Type: application/json' -j "$body" 2>&1)"
    else
      out="$(caido-cli send "$method" "$BASE$path" -H "Authorization: Bearer $token" 2>&1)"
    fi
    rc=$?
    if [ $rc -ne 0 ]; then say "FAIL: caido-cli send $method $path (rc=$rc): $out" >&2; return 1; fi
    printf '%s\n' "$out" | sed -n '/^---$/,$p' | tail -n +2
  else
    if [ -n "$body" ]; then
      curl -sfS -m 10 --noproxy '' -X "$method" -H "Authorization: Bearer $token" \
        -H 'Content-Type: application/json' -d "$body" "$BASE$path"
    else
      curl -sfS -m 10 --noproxy '' -X "$method" -H "Authorization: Bearer $token" "$BASE$path"
    fi
  fi
}

jget() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

# ---------- auth roundtrip ----------
LOGIN_JSON="$(req POST /auth/login '{"username":"alice@acme.test","password":"alice-pass"}')"
TOKEN="$(jget '["token"]' <<<"$LOGIN_JSON")"
[ -n "$TOKEN" ] || { say "FAIL: no token from login"; exit 1; }
say "ok: login alice@acme.test -> token len ${#TOKEN}"

REG_JSON="$(req POST /auth/register "{\"username\":\"seedprobe.$(date +%s)\",\"password\":\"probe-pass\",\"tenant\":\"acme\",\"role\":\"poweruser\"}")"
echo "$REG_JSON" | jget '["user"]["role"]' >/dev/null
say "ok: register probe user (role honouring is tier-dependent, asserted in verify.sh)"

# ---------- notes roundtrip ----------
NOTE_ID="$(authed POST /notes "$TOKEN" '{"title":"seed-note","body":"roundtrip canary"}' | jget '["id"]')"
LIST_JSON="$(authed GET /notes "$TOKEN")"
echo "$LIST_JSON" | grep -q '"seed-note"' || { say "FAIL: created note missing from list"; exit 1; }
say "ok: note #$NOTE_ID created + listed"

DETAIL="$(authed GET "/notes/$NOTE_ID" "$TOKEN")"
echo "$DETAIL" | grep -q 'roundtrip canary' || { say "FAIL: detail read mismatch"; exit 1; }
authed PATCH "/notes/$NOTE_ID" "$TOKEN" '{"title":"seed-note-v2"}' >/dev/null
say "ok: detail read + patch"

SEARCH_JSON="$(authed GET '/notes/search?q=canary' "$TOKEN")"
echo "$SEARCH_JSON" | grep -q 'seed-note' || { say "FAIL: search did not hit"; exit 1; }
say "ok: search q=canary hit"

# ---------- feedback inlet ----------
FB_JSON="$(authed POST /llm/feedback "$TOKEN" '{"text":"seed: please summarise roadmap notes"}')"
echo "$FB_JSON" | jget '["id"]' >/dev/null
say "ok: feedback stored ($(jget '["stored_chars"]' <<<"$FB_JSON") chars)"

# ---------- marker echo ----------
MARKER=""
if command -v docker >/dev/null 2>&1; then
  # marker travels via the data volume: `docker exec` processes do NOT inherit
  # the main process's CMD-level export (learned: empty compose env override)
  MARKER="$(docker compose -f "$(dirname "$0")/docker-compose.yml" exec -T llm-app cat /data/marker.txt 2>/dev/null || true)"
fi
if [ -n "$MARKER" ]; then
  say "ok: marker exported (value withheld from output; read via compose exec)"
else
  say "WARN: could not read TESTBED_MARKER via compose exec (docker missing?)"
fi

say "== SEED OK (guards=$GUARDS): auth/notes/search/feedback all green"
