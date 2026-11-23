# SEPP testbed

Local, deliberately vulnerable target app for validating the SEPP pipeline
(Caido capture, hunters, adapters, garak shim) against KNOWN flaws. One
FastAPI service; CRUD core (phase B1) plus one route per OWASP Top 10 for
LLM Applications item (phases B2/B3, see `../PLAN.md`).

**Scope: 127.0.0.1 only.** Everything here is intentionally insecure.

## Quickstart

```
make testbed-up      # build + start (container sepp-testbed, port 8765)
make testbed-seed    # seed check + roundtrip assertions, traffic via Caido
make testbed-down
```

Scope: run `/hunt-scope` in pi (or `python3 scripts/manage_scope.py ensure`)
to put `127.0.0.1` + `localhost` into the dedicated Caido scope `sepp`
(including a default noise-host denylist). Activate that scope in the Caido
UI if another one is active.

Verify: `make testbed-verify` runs the whole off/naive/strict matrix
against `testbed/EXPECTATIONS.md` ground truth (MATRIX GREEN expected;
nonzero exit = harness or app regression). `make testbed-canary` fires the
garak smoke through the shim + proxy. The LLM route map is complete
(LLM01-LLM10); real-model profile: `TESTBED_BACKEND=venice` (+
`OPENAI_API_KEY`; budget knobs in `EXPECTATIONS.md` -> Backend profile).

## Authenticated local-model summary

Start Ollama on the Docker host. Docker Desktop must be able to reach its
listener through `host.docker.internal:11434`. The profile uses Compose
`!override` (verified with 2.40.3). Run these commands from the repository root.

```bash
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull qwen2.5:7b
docker compose -f testbed/docker-compose.yml -f testbed/ollama.yml up -d --build --wait
python3 testbed/local_summary.py
docker compose -f testbed/docker-compose.yml -f testbed/ollama.yml down
```

The local profile serves `127.0.0.1:8766` from its own Compose project and
SQLite volume. It pins `http://host.docker.internal:11434/v1`, selects
`qwen2.5:7b`, and clears the hosted API key. The app checks the model name
in each Ollama completion. The script signs in with the seeded Alice account
and summarizes roadmap note 1. A successful receipt includes the summary ID,
text, provider model, completion ID and finish reason. The command exits with
an error if the call fails or the completion is truncated.

Both provider dispatch paths use `TESTBED_VENICE_MAX_TOKENS` (default 256) and
`TESTBED_VENICE_CHAR_BUDGET` (default 20000 per 24 hours). Each call reserves
prompt characters plus four characters per allowed output token before sending.
To skip character accounting explicitly for this local profile, prefix the
Compose startup command with `TESTBED_VENICE_CHAR_BUDGET=0`. The token cap
still applies. A zero budget on Venice blocks provider calls. Provider
unavailability produces HTTP 502 and exhausted budgets produce HTTP 429.

On 2026-11-23, Alice summarized the seeded text “Ship the reporting module;
revisit SSO audit findings.” Ollama 0.32.3 returned “Summarize: Ship the
reporting module and address SSO audit findings.” The stored summary ID was
`2`, with model `qwen2.5:7b`, completion `chatcmpl-124` and finish reason
`stop`. The installed model digest was
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
An isolated mock run of the same authenticated workflow returned the template
prefix `[summarizer] Here is what I have for you.` followed by the quoted note.
These observations cover the SEPP seeded note-summary workflow.

## Seeded accounts

| User | Password | Tenant | Role |
|---|---|---|---|
| alice@acme.test | alice-pass | acme | poweruser |
| bob@acme.test | bob-pass | acme | user |
| carol@globex.test | carol-pass | globex | user |

## Knobs (compose environment)

| Var | Values | Effect |
|---|---|---|
| `TESTBED_GUARDS` | `off` / `naive` (default) / `strict` | strength of every built-in mitigation |
| `TESTBED_BACKEND` | `mock` (default) / `venice` / `ollama` | model behind the LLM routes (B2+) |
| `TESTBED_MARKER` | `SEC-xxxxxx` random per boot | planted secret for extraction proofs; read via `docker compose exec llm-app printenv TESTBED_MARKER` |

Per-route guard semantics: docstrings in `llm-app/app/main.py`; the
strict-block / naive-bypass recipe matrix lands in `EXPECTATIONS.md` (B2).

## Debug affordances (by design)

- 500 responses carry the exception text (`internal: ...`) so error-based
  differentials are teachable.
- Login also sets an `HttpOnly` `sepp-token` cookie so history-harvest
  exercises see a realistic session shape.

## Data

Volume `sepp-testbed_testbed-data` holds SQLite + (strict tier) the JWT
secret. Wipe with `make testbed-down && docker volume rm sepp-testbed_testbed-data`.
