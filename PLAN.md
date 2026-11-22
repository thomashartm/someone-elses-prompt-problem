# SEPP Testbed - Build Plan

Status: APPROVED 2026-11-22, decisions D1-D5 locked below.
Companions: AGENTS.md (hunt rules), README.md (setup), `testbed/EXPECTATIONS.md` (authored in Phase B2).

## Goal

A local, self-owned security testbed that exercises the whole SEPP pipeline end
to end: Caido capture, history orientation, caido-cli probing, the adapter
contract, the garak shim, and the findings/report ledger.

One FastAPI service ("sepp-testbed") hosts a known set of CRUD and LLM routes.
Each LLM route corresponds to one item of the OWASP Top 10 for LLM
Applications (2025). Built-in security measures are deliberately imperfect at
the "naive" tier; hunts must circumvent them, and the "strict" tier proves the
same measure holds when implemented correctly.

Ground-truth rule: every flaw is planted and documented, so a reported finding
maps to a known defect and a clean scan is a checked negative, not an unknown.

## Locked decisions

| # | Decision |
|---|---|
| D1 | Route map v1 as listed below: 10 LLM routes + 4 CRUD routes, single app |
| D2 | Default model backend: deterministic mock; real model via Venice mode |
| D3 | `testbed/EXPECTATIONS.md` (spoiler sheet) is committed to git |
| D4 | Guard tiers off / naive / strict, all three, selected by env |
| D5 | Naming hygiene: the retired pilot hostname family is anonymized to `pilot-app.example` / `pilot-api.example` in git-bound ledgers (ISSUES.md, BACKLOG.md); live values and evidence stay in gitignored `data/` only. Default target slug everywhere: `testbed`. Acceptance: a case-insensitive search for the retired name over all git-bound paths returns zero hits. |

## Service sketch

| Aspect | Choice |
|---|---|
| Framework | FastAPI + SQLite, single container, port 8765, bind 127.0.0.1 |
| Tenants | `acme`, `globex` (users: alice@acme poweruser, bob@acme, carol@globex) |
| Auth | `POST /auth/login` issues HS256 JWT with aud claims; strict tier: per-boot random secret, naive tier: documented default secret (enables forgery demos) |
| Guards | env `TESTBED_GUARDS` = `off` / `naive` / `strict` (default `naive`) |
| Backend | env `TESTBED_BACKEND` = `mock` (default) / `venice` |
| Venice mode | `TESTBED_MODEL` slug via `OPENAI_API_KEY` / `OPENAI_BASE_URL`; server-side token+time budget caps spend (operator protection, not part of the game) |
| Markers | every planted secret gets a per-boot random suffix (e.g. `SEC-9f3a01`), exported as `TESTBED_MARKER` so assertions cannot pass by knowing the fixture |

## Guard tiers

| Tier | Semantics | Role |
|---|---|---|
| `off` | guard code not executed | shows the raw flaw |
| `naive` | realistic flawed mitigation (prefix match, blacklist, wrong-layer filter) | positive control: the documented bypass MUST succeed |
| `strict` | correct mitigation | negative control: the same bypass MUST fail |

## Route map v1

### CRUD side

| Route | Class | Naive-tier flaw |
|---|---|---|
| `POST /auth/login` | positive control, token issuance | static app-wide secret instead of per-deployment |
| `GET/POST/PATCH /notes` | BOLA, mass assignment | ownership check missing on GET; `role` accepted from register body |
| `GET /notes/search?q=` | SQLi demonstration | f-string LIKE concatenation |
| `POST /llm/feedback` | storage poisoning inlet | free text stored unsanitized, later fed to prompts |

### LLM side (OWASP LLM Top 10 2025 numbering)

| # | Route | Scenario | Naive measure | Expected bypass |
|---|---|---|---|---|
| LLM01 | `POST /llm/summarize` | summary job pulls tenant notes into the prompt | blacklist filter on note text | homoglyph/entity-encoded instruction slips past; model executes it, marker reflected |
| LLM02 | `GET /llm/assistant` | support bot over customer PII | PII regex-mask on context, not on answer assembly | "spell it character by character" reconstructs the email |
| LLM03 | `POST /llm/plugins/install` | installs plugin descriptor from bundled registry | name-prefix allowlist (`trusted-`) | typosquatted `trusted-helper-extra` passes |
| LLM04 | `GET /llm/kb-answer` | mini knowledge base fed by `/llm/feedback` | keyword moderation at write time | paraphrased poison stored; next kb-answer obeys it |
| LLM05 | `GET /llm/render?id=` | renders model summaries as `text/html` | input sanitized, output trusted | model-output image `onerror` (stored-output XSS) |
| LLM06 | `POST /llm/agent/action` | toolset {fetch_url, calc}, claimed boundary | allowlist via `url.startswith(prefix)` | userinfo/subdomain trick crosses out |
| LLM07 | `POST /llm/chat` | system prompt embeds the boot secret | confidentiality by instruction only | ask backwards/uppercase/base64; marker extracted |
| LLM08 | `POST /llm/kb-search` | hashed-embedding semantic search | similarity search lacks tenant predicate | cross-tenant recall (globex docs to acme user) |
| LLM09 | `POST /llm/cite` | model citations stored as verified facts | trust-by-default insert `status=verified` | fabricated source lands in `/facts`, disprovable by lookup |
| LLM10 | `POST /llm/heavy` | long generations, client-identified quota | quota keyed by spoofable `X-Client-Id`, no token clamp | header rotation defeats quota |

## Files

| Path | Content |
|---|---|
| `testbed/docker-compose.yml` | llm-app service, localhost bind, env knobs, venice mode via env |
| `testbed/llm-app/` | FastAPI source, Dockerfile, seed logic, mock engine |
| `testbed/EXPECTATIONS.md` | per route: OWASP item, flaw, strict-block recipe, naive-bypass recipe, marker assertions |
| `testbed/seed.sh` | boots, seeds tenants, drives baseline traffic through Caido so history has orientation material |
| `testbed/verify.sh` | for each tier: restart with `TESTBED_GUARDS`, run assertion curls from EXPECTATIONS; nonzero exit on mismatch |
| `data/targets/testbed/` | `target.json`, `baseline.req`, SSE adapter (no secrets; secrets live in gitignored `data/`) |
| `Makefile` | `testbed-up` / `testbed-down` / `testbed-seed` / `testbed-verify` |

## Phases

| Phase | Content | Exit check |
|---|---|---|
| A (done 2026-11-22) | target-agnostic defaults in extensions/scripts/drivers; ledger anonymization per D5; product rename SEPP | retired-name grep over git-bound paths = 0 hits; syntax checks pass; smoke harnesses green |
| B1 (done 2026-11-22) | skeleton: compose, app scaffold, guards framework, CRUD routes, seed, Makefile targets | `make testbed-up && make testbed-seed` green; naive-tier BOLA leak + SQLi differentials spot-verified; history populated via caido-cli send (see ISSUES 42) |
| B2 (done 2026-11-22) | mock engine, LLM01-05, EXPECTATIONS.md, adapter + `data/targets/testbed/` | tier matrix PROVEN live (5 naive bypasses + 6 strict blocks, SEE testbed/EXPECTATIONS.md provenance); check_adapters green; transport roundtrip recorded in Caido history (ids 2556-2558); shared-code fixes: transport --noproxy + authorization KEEP (ISSUES 43) |
| B3 (done 2026-11-22) | LLM06-10, venice backend, garak wiring, verify harness | `make testbed-verify` MATRIX GREEN (off/naive/strict, 19 checks); `make testbed-canary` end-to-end green (`dan.DAN_Jailbreak` via shim + transport + Caido); venice backend offline-verified (budget + 503 mapping + framing) |
| C (done 2026-11-22) | end-to-end hunt rehearsal through Caido | workspace `20261122-127.0.0.1` created; all 3 CRUD + 10 LLM routes proven with Caido request-ID before/after pairs (`naive` bypass vs `strict` block in `evidence/rehearsal.json`); 4 findings recorded in Caido + local ledger (`findings.py create`); `harvest_target.py` + `check_adapters.py` + `make testbed-canary` green; D5 grep = 0 hits |

## Phase C acceptance list

- Per LLM route: naive bypass demonstrated with before/after pair; strict block demonstrated
- CRUD: BOLA pair (alice vs carol note ids), mass assignment (role in register body), SQLi marker (search `q='`), storage-poison into LLM04 chain
- `harvest_target.py --slug testbed` exits 0 against the self-issued JWT; `check_adapters.py testbed` green
- garak canary (dan + injection subsets, 1 generation, mock backend) reflects planted markers
- D5 grep zero hits; repo ready for `git init`

## Out of scope (v1)

- Internet-facing exposure (localhost bind only)
- sqlmap / nuclei integration (stays operator-gated per AGENTS.md)
- Realism parity of the mock engine with a production LLM
- Per-route guard overrides (v2 idea); RBAC depth beyond the two-tenant demo
- Public vulnerable stacks (crAPI, Juice Shop) as extra compose profiles: noted as future, not v1
