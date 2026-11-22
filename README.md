# SEPP (Someone Else's Prompt Problem)

<p align="center">
  <img src="assets/sepp-logo.jpg" alt="SEPP — Someone Else's Prompt Problem" width="600">
</p>

## Overview

SEPP (Someone Else's Prompt Problem) is an agentic security-testing stack for **LLM-enabled APIs**.

It targets the **OWASP Top 10 for LLM Applications** (prompt injection, sensitive data/system-prompt disclosure, insecure output handling, excessive agency, model DoS, etc.) alongside classic API weaknesses (BOLA/IDOR, JWT/OAuth flaws, injection, mass assignment). Autonomous controller and hunter agents drive **Caido** through `caido-cli` and run **NVIDIA garak** probes through a target-blind OpenAI shim — reusing your authenticated browser sessions in Caido and proving every finding with reproduced before/after request pairs.

```
operator ──delegates URL / Caido right-click──▶ pi controller ──▶ hunter subagents
                                                   │
                                                   ├── caido-cli       history, replay, batch sweeps
                                                   ├── helpers/*       findings, JWT, OAuth, LLM job harness
                                                   └── NVIDIA garak ──▶ OpenAI shim (drivers/openai_shim.py)
                                                                            ▼ (all traffic proxied)
                                                                      Caido :8080 ◀── browser session (auth)
```

## Requirements

- **Caido** running locally on `127.0.0.1:8080` ([caido.io](https://caido.io))
- **Caido CLI & MCP** (`caido-cli`, `caido-mcp-server`)
- **Node.js ≥ 20**, **Python 3**, **uv**
- **Security-capable LLM API key** — the driving agent needs a model that will execute offensive security tasks without refusing. Tested models on Venice:

  | Model | Venice model ID |
  |---|---|
  | GLM 5.3 Uncensored | `glm-5-3-flash-uncensored` |
  | DeepSeek 4.1 Uncensored | `deepseek-4_1-uncensored` |

## Setup

Run the idempotent local installer:

```bash
make setup
```

This checks prerequisites, installs `pi`, `caido-cli`, and `caido-mcp-server`, builds the SEPP Caido plugin (`plugins/sepp/dist/plugin_package.zip`), symlinks the `sepp` CLI into `~/.local/bin`, exports the Caido CA certificate, provisions the **NVIDIA garak** venv (`.venv-garak/`), and runs a smoke test.

After `make setup`:

1. **Authenticate Caido CLI/MCP** (tokens expire periodically; re-run on auth errors):
   ```bash
   CAIDO_URL=http://127.0.0.1:8080 caido-mcp-server login
   caido-cli status -u http://127.0.0.1:8080
   ```
2. **Install the Caido plugin**: in Caido, go to **Plugins → Install from file** and select `plugins/sepp/dist/plugin_package.zip`.
3. **Configure `.env`**: run `./sepp.sh` once to generate `.env`, then set `VENICE_API_KEY`.
4. **Capture auth**: log into your target application in a browser proxied through Caido. Agents harvest tokens/cookies from proxy history — you do not need to paste secrets.

## Usage

Start a controller session:

```bash
./sepp.sh                # default: GLM 5.3 flash uncensored (Venice)
./sepp.sh deepseek       # DeepSeek 4.1 uncensored (Venice)
./sepp.sh go             # OpenCode Go model (SEPP_GO_MODEL, prompt-cached)
./sepp.sh --check        # verify plumbing without launching pi
```

Send a target to the agent either in chat (`target: https://api.example.com/v2/chat`) or by right-clicking a request in Caido HTTP History → **SEPP: send to Hunt**.

The main session acts as the **controller** (orients on Caido history, plans, verifies evidence, reports) and delegates hands-on probing to background **hunter** subagents. Destructive or state-changing probes escalate to you for approval.

### Session commands

| Command | Purpose |
|---|---|
| `alt+g` / `/menu` | Capability hub: prompt templates, hunt starters, board/brief shortcuts, toolchain check |
| `/hunt` | Show current hunt status and target ledger |
| `/hunt start <url>` | Arm the findings and phase ledger for a target |
| `/hunt here <url> [notes]` | Run a hunt directly in the current session with ledger tracking |
| `/hunt phase <name>` | Set phase (`orient`, `map`, `hypothesize`, `prove`, `record`, `report`) |
| `/hunt report` | Export the ledger to `data/reports/sepp-redteam-<ts>.md` |
| `/hunters` | Live hunter board (status, phase, workspace, latest evidence) |
| `/hunt-abort [<name>\|all]` | Cooperatively stop a hunter (or force-stop all) |
| `/hunt-status [workspace]` | Generate a deterministic status report directly from disk (`/sepp-status`) |
| `/hunt-brief [show]` | Synthesize an interpreted brief with recommendations (`/sepp-brief`) |
| `/sessions` | Inspect workspaces, evidence files, and report tails (`/sepps`) |

Outside `pi`: `python3 scripts/hunter_board.py list` shows live hunter state.

## OWASP LLM Top 10 & NVIDIA garak

SEPP tests LLM-enabled APIs using two complementary paths:

1. **Automated scanning with NVIDIA garak**: custom or non-standard LLM endpoints are wrapped by a per-target adapter (`data/targets/<slug>/adapter.py`, contract in `drivers/ADAPTER_CONTRACT.md`) and exposed through the local OpenAI-compatible shim (`drivers/openai_shim.py`). All garak probes flow through the paced Caido proxy transport (`drivers/transport.py`).
   ```bash
   python3 scripts/check_adapters.py <slug>   # offline adapter validation
   TARGET=<slug> scripts/run_garak_canary.sh  # run a single-probe garak canary via the shim
   ```
   Direct garak runs use `.venv-garak/bin/garak` with narrow probe subsets (e.g. `-p dan,promptinject`) to respect rate limits.
2. **Targeted API & LLM job probing**: `helpers/llm_job_harness.py` runs config-driven payload matrices against LLM job endpoints, while `helpers/jwt_tools.py`, `helpers/oauth_flow.py`, and `caido-cli batch` cover auth, BOLA, and injection around the model.

## Local OWASP LLM Top 10 Testbed

The repo includes a local deliberately vulnerable FastAPI app (`testbed/`) with routes mapped to **OWASP LLM01–LLM10** and configurable guard tiers (`off` / `naive` / `strict`) for validating hunters, adapters, and garak probes:

```bash
make testbed-up       # start on 127.0.0.1:8765
make testbed-seed     # seed accounts & traffic through Caido
make testbed-verify   # run the off/naive/strict expectation matrix
make testbed-canary   # run the garak canary against the testbed
make testbed-down     # stop stack
```

See `testbed/README.md` for seeded credentials and environment knobs.

## Operating Rules

- **Scope**: only touch explicitly delegated hosts.
- **Rate limits**: max 50 requests per burst with pauses between batches; stop on 5xx storms or timeouts.
- **Approvals**: destructive or state-changing probes require explicit operator confirmation.
- **Evidence & hygiene**: findings require reproduced request pairs (`caido-cli request <id>`); secrets are never printed in reports or chat.

## Repository Layout

| Path | Kind | Purpose |
|---|---|---|
| `AGENTS.md` | code | Agent methodology, hard rules, test priorities, report format |
| `Makefile` | code | Setup, plugin build/pack, CLI install, and testbed targets |
| `sepp.sh` | code | Session launcher (loads `.env`, checks Caido, starts `pi`) |
| `drivers/` | code | Target-blind transport, adapter loader, and OpenAI shim for NVIDIA garak |
| `helpers/` | code | Findings ledger, JWT/OAuth analyzers, LLM job harness, ffuf wrapper |
| `plugins/sepp/` | code | First-party Caido plugin (**SEPP: send to Hunt**) |
| `scripts/` | code | Setup, smoke checks, CA export, harvester, garak canary, hunter board, workspace manager |
| `payloads/` | code | Prompt-injection and API probe payload lists |
| `testbed/` | code | Local OWASP LLM Top 10 vulnerable app and verification suite |
| `.pi/` | code | `pi` agent definitions (`hunter.md`), extensions, MCP config, and templates |
| `data/` | data | Per-target adapters/creds, CA cert, workspaces, reports, garak output (**gitignored**) |
| `.env` | data | API keys and local overrides (**gitignored**) |

Key environment variables: `CAIDO_TARGET` (target slug under `data/targets/`, default `testbed`), `CAIDO_URL`, `CAIDO_PROXY`, `CAIDO_DATA_DIR` (default `./data`), `SEPP_RATE_PAUSE`.

## Verify

```bash
./scripts/smoke.sh    # checks Caido reachability, CLI auth, and helper CLIs
```
