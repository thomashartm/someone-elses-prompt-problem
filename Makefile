# SEPP — Somebody Else's Problem
# Developer targets for the caido-integration repo.
# Day-to-day hunting runs through ./sepp.sh — this Makefile builds/install it.

PLUGIN_DIR := plugins/sepp
BIN_DIR    := $(HOME)/.local/bin

.DEFAULT_GOAL := intro
.PHONY: intro plugin-build plugin-pack plugin-clean typecheck install-cli setup testbed-up testbed-down testbed-seed testbed-reseed testbed-verify testbed-canary testbed-logs

## intro: what you are looking at
intro:
	@echo ""
	@echo "  SEPP — Someone Else's (Prompt) Problem."
	@echo "  Right-click a request in Caido, declare it SEPP:"
	@echo "  the hunting agent inherits it, cookies and all,"
	@echo "  and never bothers you with it again."
	@echo ""
	@echo "  Targets:"
	@awk 'BEGIN { FS = ":.*## " } /^## / { sub(/^## /, "", $$1); printf "    \033[36m%-13s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

## setup: ONE command — full local workstation install (plugin build + URL, pi, garak, caido)
setup:
	@bash "$(CURDIR)/scripts/setup_local.sh"

## plugin-build: install deps, typecheck, build backend+frontend, pack the zip
plugin-build:
	cd $(PLUGIN_DIR) && npm install --no-fund --no-audit && npm run typecheck && npm run build && npm run pack
	@echo ""
	@echo "plugin ready: $(PLUGIN_DIR)/dist/plugin_package.zip"
	@echo "install via Caido -> Plugins -> Install from file"

## plugin-pack: re-zip dist without rebuilding (fast manifest/icon tweaks)
plugin-pack:
	cd $(PLUGIN_DIR) && npm run pack

## plugin-clean: remove build output + node_modules
plugin-clean:
	rm -rf $(PLUGIN_DIR)/dist $(PLUGIN_DIR)/node_modules

## typecheck: TypeScript check only (no build output)
typecheck:
	cd $(PLUGIN_DIR) && npm run typecheck

## install-cli: make `sepp` globally available (symlink into ~/.local/bin)
install-cli:
	mkdir -p $(BIN_DIR)
	ln -sf $(CURDIR)/scripts/sepp.sh $(BIN_DIR)/sepp
	@echo "installed: $(BIN_DIR)/sepp -> $(CURDIR)/scripts/sepp.sh"
	@command -v sepp >/dev/null && echo "on PATH: $$($(BIN_DIR)/sepp 2>&1 | head -1 | cut -c1-60)..." || \
		echo "WARNING: $(BIN_DIR) not on PATH in this shell" >&2

## testbed-up: build + start the local testbed stack (127.0.0.1:8765)
testbed-up:
	docker compose -f testbed/docker-compose.yml up -d --build

## testbed-down: stop the testbed stack
testbed-down:
	docker compose -f testbed/docker-compose.yml down

## testbed-seed: seeded roundtrip assertions; traffic via Caido when up
testbed-seed:
	bash testbed/seed.sh

## testbed-reseed: down + wipe volume + up (fresh marker, fresh seed data)
testbed-reseed:
	docker compose -f testbed/docker-compose.yml down -v
	docker compose -f testbed/docker-compose.yml up -d --build

## testbed-verify: run the FULL guard-tier matrix (off/naive/strict)
testbed-verify:
	bash testbed/verify.sh

## testbed-canary: garak canary through the shim (needs history + adapter)
testbed-canary:
	TARGET=testbed scripts/run_garak_canary.sh

## testbed-logs: follow the testbed app log
testbed-logs:
	docker compose -f testbed/docker-compose.yml logs -f llm-app

CONTROL_PYTHON ?= python3
LLM_PROFILE ?= core
LLM_GATE_PROFILE ?= smoke

.PHONY: testbed-local llm-smoke llm-battery llm-gate llm-exploits

## testbed-local: start the isolated local Ollama testbed
testbed-local:
	docker compose -f testbed/docker-compose.yml -f testbed/ollama.yml up -d --build --wait

## llm-smoke: run local readiness and artifact admission controls
llm-smoke:
	"$(CONTROL_PYTHON)" scripts/llm_battery.py --profile smoke

## llm-battery: run a bounded local control profile
llm-battery:
	"$(CONTROL_PYTHON)" scripts/llm_battery.py --profile "$(LLM_PROFILE)" $(if $(LOCAL_SUMMARY),--local-summary "$(LOCAL_SUMMARY)")

## llm-gate: evaluate saved evidence against a required policy
llm-gate:
	"$(CONTROL_PYTHON)" scripts/llm_gate.py "$(POLICY)" "$(REPORT)" --profile "$(LLM_GATE_PROFILE)"

## llm-exploits: full OWASP LLM exploit battery vs dockerized testbed (naive+strict, incl. exfil rig)
llm-exploits:
	python3 scripts/exploit_battery.py --with-exfil-rig
