# SEPP exploitation suite — execution roadmap

Tracks the plan in [`docs/llm-test-program-implementation.md`](docs/llm-test-program-implementation.md) ([program ticket #61](https://github.com/thomashartm/someone-elses-prompt-problem/issues/61)). Exploit playbooks, exfil rig, multi-turn shim, harness assertions and live battery shipped in PR #79; what follows closes the remaining capability gaps.

## Recommended sequence

| # | Step | Size | Why now |
|---|---|---|---|
| 1 | Land #78 properly: reconcile the dirty main-checkout transport.py/harvest_target.py with our merged transport.py, add the round-trip regression test the issue already implies, commit, close #78 | ~½ hr | Hunters harvesting fresh targets today get silent 401s (headers dropped). Cheapest blocker to clear. |
| 2 | Fix #60: rebuild .venv-garak (python3 -m venv + pinned garak), fix entry-script shebangs, verify garak --version + canary dry-run | ~½ hr | Nothing garak-based can run until this works. |
| 3 | Build P6 — the profiled battery runner (#47, plan Phase 6): config-driven probe selection by name (never owasp:* tags), smoke/core/full profiles with ceilings 12/414/628, request accounting independent of detector scores, structured JSON feeding scripts/llm_gate.py — driven through the shim, which finally supports multi-turn (SEPP_SHIM_CONV=reuse) | ~1–2 days | This is the single biggest exploitation-capability gap: today a hunter can only run an ad-hoc canary; after this, repeatable model-layer attack batteries with gate-ready evidence. |
| 4 | Wire per-risk acceptance (#50–#59, plan Phase 7): map 2025 battery rows + garak evidence to each 2026 issue; add the missing fixture pairs (artifact admission, unreviewed knowledge, unsupported citations) | ~½ day | Turns the suite from "demos" into the shared-dev regression gate #61 asks for. |
| 5 | Re-verify the plan's First Deliverable evidence (authenticated summary via Ollama qwen2.5:7b, model identity recorded) — likely cheap since transport/provider plumbing exists | ~1 hr | It gates everything else's evidence validity in the plan. |

Backlog runner-ups, not urgent: #49 (target index map/pagination nits), #11 (CA rotation staleness), #12 (throwaway-probe hygiene).
