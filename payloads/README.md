# OWASP LLM Top 10 Payloads

Payload lists for `helpers/llm_job_harness.py` and manual replay probes.

## Conventions

- Lines starting with `#` and blank lines are ignored by `helpers/llm_job_harness.py`.
- The **first non-comment line** in every file is a neutral baseline (`Summarize the daily report.`) used to diff probe responses against normal behavior.
- `{{MARKER}}` is replaced at runtime with a unique canary token (e.g., `MRK123456`) so reflected or obeyed instructions can be detected deterministically.

## Files

| File | Scope |
|---|---|
| `llm_injection.txt` | Default quick-smoke set spanning the core OWASP LLM classes |
| `llm01_prompt_injection.txt` | **LLM01** — Direct override, delimiter/role escape, framing, multilingual & base64 |
| `llm02_sensitive_info.txt` | **LLM02** — Secrets/env vars, PII, cross-tenant leakage, debug splats |
| `llm03_supply_chain.txt` | **LLM03** — Typosquat ids, registry confusion, version confusion, `compat=` marker exfil |
| `llm04_poisoning.txt` | **LLM04** — Blacklist-clean paraphrased directives, homoglyph smuggling, KB row injection |
| `llm05_output_handling.txt` | **LLM05** — XSS, Markdown image exfil, SSTI, SQL/shell/JSON sink probes |
| `llm06_excessive_agency.txt` | **LLM06** — Role escalation, unauthorized tool calls, job smuggling, tenant tampering |
| `llm07_system_prompt_leak.txt` | **LLM07** — Verbatim, JSON/YAML, translation, and completion-prefix prompt extraction |
| `llm08_rag_vector.txt` | **LLM08** — Indirect injection in documents, ACL/collection boundary & metadata leaks |
| `llm09_misinformation.txt` | **LLM09** — Fabricated citations, fake policy URLs, lookalike domains, citation laundering |
| `llm10_unbounded_consumption.txt` | **LLM10** — Bounded checks for output-length, tool-loop, and nesting caps |

## Usage

Point `payloads_file` in a `llm_job_harness.py` config to any file above:

```bash
python3 helpers/llm_job_harness.py init > config.json
# edit config.json: set endpoint, headers, and payloads_file (e.g. "payloads/llm07_system_prompt_leak.txt")
python3 helpers/llm_job_harness.py run config.json
```

The harness explicitly uses a 2,000-byte body limit for live responses. Each report row records `evidence` as `complete`, `truncated`, `incomplete`, or `tool_error`. The last three outcomes have `signals: null` and stop the run with exit 1. An empty signal list is possible only after a complete response.

For an already saved `caido-cli raw` output, run `python3 helpers/llm_job_harness.py inspect response.txt --reference SAMPLE`. This offline check accepts up to 1,000,000 bytes of CLI output, checks the declared body length in bytes, and reports reference presence only for complete bodies. Its exit status describes evidence completeness.
