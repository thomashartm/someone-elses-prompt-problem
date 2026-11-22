#!/usr/bin/env bash
# Smoke test: verifies the full plumbing without touching any target.
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
fail=0

say() { printf '%-46s %s\n' "$1" "$2"; }

# 1. tools on PATH
for t in caido-cli caido-mcp-server pi ffuf jwt jq; do
  command -v "$t" >/dev/null 2>&1 && say "binary $t" "OK ($(command -v $t))" \
    || { say "binary $t" "MISSING"; fail=1; }
done

# 2. Caido reachable
curl -sf -m 3 -X POST "${CAIDO_URL:-http://127.0.0.1:8080}/graphql" \
  -H 'Content-Type: application/json' -d '{"query":"{ __typename }"}' \
  | grep -q QueryRoot && say "caido graphql" OK || { say "caido graphql" UNREACHABLE; fail=1; }

# 3. Caido auth usable? (non-fatal: only needed for history/findings push)
tok=""
[ -n "${CAIDO_ACCESS_TOKEN:-}" ] && tok="$CAIDO_ACCESS_TOKEN"
[ -z "$tok" ] && ls "$HOME/.caido-mcp/tokens/"*.json >/dev/null 2>&1 && tok=SET
[ -n "$tok" ] && say "caido auth token" FOUND || say "caido auth token" "absent - run: caido-mcp-server login"

# Caido CA bundle for proxied TLS (regenerate if stale/missing)
python3 "$DIR/scripts/export_ca.py" >/dev/null 2>&1 \
  && say "caido ca bundle" OK || { say "caido ca bundle" FAILED; fail=1; }

# helpers import / respond cleanly
cd "$DIR"
for h in helpers/findings.py helpers/jwt_tools.py helpers/oauth_flow.py helpers/llm_job_harness.py; do
  python3 "$h" --help >/dev/null 2>&1 && say "helper $(basename $h)" OK \
    || { say "helper $(basename $h)" BROKEN; fail=1; }
done

# 5. jwt_tools round-trip sanity
T="eyJhbGciOiJIUzI1NiIsImtpZCI6Ii9wYXRoLyJ9.eyJzdWIiOiIxIiwiZXhwIjo5OTk5OTk5OTk5fQ.AA"
python3 helpers/jwt_tools.py decode "$T" | grep -q WARNINGS && say "jwt_tools sanity" OK \
  || { say "jwt_tools sanity" FAILED; fail=1; }

exit $fail
