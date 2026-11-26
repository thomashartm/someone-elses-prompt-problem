# scripts index

One line per script: what + invocation.

check_adapters.py — offline adapter contract check: python3 scripts/check_adapters.py [slug]
dev_smoke_sepp_menu.mjs — sepp-menu extension load-path smoke: node --experimental-strip-types scripts/dev_smoke_sepp_menu.mjs
dev_smoke_sepp_redteam.mjs — sepp-redteam extension runtime smoke (stubbed pi): node --experimental-strip-types scripts/dev_smoke_sepp_redteam.mjs happy|restore|corrupt|pin
engagement_status.py — controller brief from board+ledger+reports: python3 scripts/engagement_status.py [workspace-id-substring]
export_ca.py — export Caido intercept CA via openssl s_client: python3 scripts/export_ca.py [hostname]
harvest_target.py — harvest freshest target JWT cookie from Caido history: python3 scripts/harvest_target.py [--slug testbed] [--cookie-name N] [--path SUBSTR]
hunter_board.py — hunter heartbeat/roster/stop/abort: python3 scripts/hunter_board.py heartbeat|list|get|stop|abort
manage_scope.py — Caido scope manager (sepp scope, local testbed + noise denylist): python3 scripts/manage_scope.py ensure|add|show [SITE...]
run_garak_canary.sh — garak canary via universal shim: TARGET=<slug> scripts/run_garak_canary.sh; prints the verified JSONL path for this invocation
garak_battery.py — profiled probe battery (smoke|core|full) via shim, budget-enforced preflight, writes llm_gate-ready report: python3 scripts/garak_battery.py --profile smoke [--dry-run] (garak venv rooted at $SEPP_GARAK_HOME, default .venv-garak)
configs/garak_profiles.json — committed probe definitions for the battery (select by name, ceilings 12/414/628)
verify_garak_report.py — validate a completed report: python3 scripts/verify_garak_report.py <report.jsonl>
sepp.sh — Caido→Hunt bridge receiver (global `sepp` via make install-cli): sepp <url> <method> [raw] [cookies] ...
setup_local.sh — one-command workstation install (behind `make setup`): bash scripts/setup_local.sh
smoke.sh — plumbing smoke (caido, CLI auth, helpers --help): bash scripts/smoke.sh
workspace.py — hunt session workspaces: python3 scripts/workspace.py list|ensure|path|prune

onboard_target.py — register sanitized fixture bundles, list targets, or verify a local response parser. See [local readiness](../docs/local-readiness.md).
llm_battery.py — run a bounded local fixture profile and print its report path. Run `python3 scripts/llm_battery.py --help`.
llm_gate.py — check observed results against an environment policy. Run `python3 scripts/llm_gate.py --help`.
garak_summarize.py — summarize evaluated output in a completed JSONL report. Run `python3 scripts/garak_summarize.py report.jsonl`.
canary_listener.py — loopback JSONL request journal for exfil proofs: python3 scripts/canary_listener.py --port 8899 --log data/reports/canary.jsonl
prove_exfil.py — listener + headless render + verdict for LLM05 exfil proofs (renderer: drivers/headless_render.py; see docs/exploits/llm05-*): python3 scripts/prove_exfil.py --url <render-url> --match SEC-xxxxxx [--assert-absent]

[Document summary fixtures](../testbed/summary-format.md) — generate six document pairs and score saved testbed or Aderis attachment-job results with garak. Run `.venv-garak/bin/python scripts/summary_fixtures.py --help` for usage.
