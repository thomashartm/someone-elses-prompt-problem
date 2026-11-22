#!/usr/bin/env bash
# Route/parameter discovery via ffuf, ALWAYS proxied through Caido so the
# resulting traffic enriches proxy history for later analysis.
#
# Usage: discover.sh https://api.example.com [wordlist] [extra ffuf args...]
# Requires a wordlist; ships nothing - point at seclists or any text file.
set -euo pipefail

URL="${1:?usage: discover.sh <base-url> [wordlist] [ffuf args...]}"
WORDLIST="${2:?missing wordlist}"
shift 2 || true
PROXY="${CAIDO_PROXY:-http://127.0.0.1:8080}"
OUTDIR="$(cd "$(dirname "$0")/.." && pwd)/data/reports/discovery-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUTDIR"

exec ffuf -w "$WORDLIST" -u "$URL/FUZZ" -x "$PROXY" \
  -rate 50 -p 0.05 -se -ac -of md -od "$OUTDIR" "$@"
