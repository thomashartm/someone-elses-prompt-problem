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
| `TESTBED_BACKEND` | `mock` (default) / `venice` | model behind the LLM routes (B2+) |
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
