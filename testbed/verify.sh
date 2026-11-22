#!/usr/bin/env bash
# verify.sh - run the FULL guard-tier matrix against the testbed
# (off, naive, strict) and assert every EXPECTATIONS.md rule.
#
#   bash testbed/verify.sh          # all three tiers
#   TIER=naive bash testbed/verify.sh   # single tier
#
# Recreates the container per tier (volume KEPT: marker + seeded data stay
# stable across tiers, so plant state written by one run is visible to the
# next). Exit nonzero on the first failing tier.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="docker compose -f $HERE/docker-compose.yml"
TIERS="${TIER:-off naive strict}"
FAILED=0

wait_health() { # wait_health TIER
  for _ in $(seq 1 30); do
    H="$(curl -sf -m 2 http://127.0.0.1:8765/healthz 2>/dev/null || true)"
    if echo "$H" | grep -q "\"guards\"[[:space:]]*:[[:space:]]*\"$1\""; then return 0; fi
    sleep 1
  done
  echo "FAIL: health never reported guards=$1 (last: ${H:-none})" >&2
  return 1
}

for T in $TIERS; do
  echo "===== TIER $T ====="
  TESTBED_GUARDS="$T" $COMPOSE up -d --force-recreate >/dev/null
  wait_health "$T"
  MARKER="$($COMPOSE exec -T llm-app cat /data/marker.txt)"
  if ! python3 "$HERE/verify_checks.py" --tier "$T" --marker "$MARKER"; then
    FAILED=1
    echo "!! tier $T FAILED"
  fi
done

echo "===== MATRIX $([ $FAILED -eq 0 ] && echo GREEN || echo RED) ====="
exit $FAILED
