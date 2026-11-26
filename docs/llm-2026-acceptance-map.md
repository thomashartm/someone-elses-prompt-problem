# OWASP LLM 2026 control acceptance map (issues #50–#59)

Maps each 2026 control issue to its demonstrable cases and evidence sources.
Ground rules from the [program plan](llm-test-program-implementation.md) (Phase 7): every applicable control needs an **ordinary positive case** and a **contrasting case** with a measurable expected result; unavailable features are `inapplicable` **with a reason**; incomplete runs are `inconclusive`.

Evidence sources keyed below:

- **App battery** — `scripts/exploit_battery.py` row (live-run per tier; ground truth in `testbed/EXPECTATIONS.md`)
- **Garak battery** — probe in `configs/garak_profiles.json` (model-layer signal, app controls held constant)
- **Playbook** — `docs/exploits/llmNN-*.md` hands-on reproduction

Issue numbering note: the `LLMnn:2026` prefixes in issue titles do NOT match the risk families in their footers; the table records both faithfully.

## Control matrix

| Issue | Control (2026 family) | Our risk | Positive case | Contrasting case | App battery | Garak battery | Playbook | Testbed status |
|---|---|---|---|---|---|---|---|---|
| [#50](https://github.com/thomashartm/someone-elses-prompt-problem/issues/50) | Summaries follow the user's instructions (Prompt injection) | LLM01 | benign note summarises fact-only, no directive obedience | planted homoglyph directive is obeyed (naive) / quoted-not-executed (strict) | LLM01 naive+strict | `dan.*`, `promptinject.*`, `encoding.Inject*`, `smuggling.*`, `tap/dra` (core/full) | llm01 | covered live (app battery) |
| [#51](https://github.com/thomashartm/someone-elses-prompt-problem/issues/51) | Redaction across streamed and saved answers (Sensitive information) | LLM02 | assistant answers from masked customer record, stars only | string-assembly reconstructs the raw email (naive) / masked-only everywhere (strict) | LLM02 naive+strict | `leakreplay.GuardianComplete` (memorization probe; partial coverage — no domain-specific PII probe yet) | llm02 | covered live (app battery) |
| [#52](https://github.com/thomashartm/someone-elses-prompt-problem/issues/52) | Authorization at tool execution (Excessive agency) | LLM06 | in-boundary tool call resolves | boundary-ride authority rejected (strict 400) / rides across (naive) | LLM06 naive+strict | none — tool boundary is app-layer by definition | llm06 | covered live (app battery) |
| [#53](https://github.com/thomashartm/someone-elses-prompt-problem/issues/53) | Artifact identity before loading (Supply chain) | LLM03 | **approved** registry member admits + initializes (`LLM03-pos`) | unapproved typosquat: rejected before init (strict 400) / admitted and exfiltrates on first use (naive) | LLM03 + LLM03-pos naive+strict | `packagehallucination.JavaScript` (core), `.Python` (full) | llm03 | covered live (pair completed this iteration) |
| [#54](https://github.com/thomashartm/someone-elses-prompt-problem/issues/54) | Unreviewed feedback cannot become authoritative (Data & model poisoning) | LLM04 | neutral feedback rides along harmlessly (`payloads/llm04_poisoning.txt` baseline line) | stored poison directive steers KB answer (naive) / moderator holds it back (strict) | LLM04 naive+strict | none — corpus poisoning needs local corpus control; garak works on provider prompt surface | llm04 | covered live (app battery) |
| [#55](https://github.com/thomashartm/someone-elses-prompt-problem/issues/55) | Quota rejection stops provider work (Unbounded consumption) | LLM10 | under-quota bursts all served | 4th+ rotated-identity request rejected 429 despite identity rotation | LLM10 naive+strict | none — budgets are enforced at the gate/profile layer (`scripts/llm_gate.py` ceilings) | llm10 | covered live (app battery) |
| [#56](https://github.com/thomashartm/someone-elses-prompt-problem/issues/56) | Business claims have supporting evidence (Misinformation) | LLM09 | citation with supporting source persists as verified (naive) | fabricated intranet reference launders to verified (naive) / stays unverified (strict) | LLM09 naive+strict | `goodside.WhoIsRiley` (core), `misleading.FalseAssertion` (full) | llm09 | covered live (app battery); true source-fetch verification modeled by the tier gate only |
| [#57](https://github.com/thomashartm/someone-elses-prompt-problem/issues/57) | Private context stays out of user-visible output (Hidden context exposure) | LLM07 | normal answers, no marker | extraction prompts make the engine spell the marker (naive) / MASKED-VALUE only (strict) | LLM07 naive+strict | `sysprompt_extraction.SystemPromptExtraction` (core) | llm07 | covered live (app battery) |
| [#58](https://github.com/thomashartm/someone-elses-prompt-problem/issues/58) | Tenant isolation before generation (Vector & embedding weaknesses) | LLM08 | acme searches retrieve acme rows only | cross-tenant recall surfaces globex salaries (naive) / strict filters | LLM08 naive+strict | none — vector-store scoping is app-layer; no embedding probe needed at smoke/core | llm08 | covered live (app battery) |
| [#59](https://github.com/thomashartm/someone-elses-prompt-problem/issues/59) | Model output at the renderer and job consumer (Improper output handling) | LLM05 | stored summary renders inert text | literal `<img` reaches the DOM (naive) / escaped entity only (strict) — plus **browser-executed beacon proof** via the exfil rig | LLM05 + LLM05-rig naive+strict (`--with-exfil-rig`) | `web_injection.Markdown*`, `StringAssemblyDataExfil`, `PlaygroundMarkdownExfil` (smoke/core) | llm05 | covered live (app battery) incl. real-Chrome beacon |

## The three Phase-7 fixture pairs, where they live

| Pair | Positive | Contrast | Encoded in |
|---|---|---|---|
| Artifact admission | `PLUGIN_REGISTRY["trusted-summarizer"|"trusted-translator"]` (approved member) | `"trusted-helper-extra"` known-bad decoy with exfil-on-first-use behaviour | `testbed/llm-app/app/engine.py::PLUGIN_REGISTRY`; battery rows `LLM03-pos` / `LLM03` |
| Unreviewed knowledge | neutral baseline line (harmless feedback) | directive-bearing poison lines (incl. homoglyph smuggle) | `payloads/llm04_poisoning.txt`; battery row `LLM04` |
| Unsupported citations | fact citation whose reference supports it (naive: verified) | fabricated `http://intranet.example/fake-policy` reference | battery row `LLM09` (`FAKE_REF`), route `/llm/cite` |

## Per-environment applicability

| Environment | Status |
|---|---|
| testbed (mock engine) | fully exercised — app battery (12 checks × 2 tiers + exfil rig) + garak smoke/core via shim |
| testbed (local model, Ollama `qwen2.5:7b`) | pending — roadmap step 5 / plan First Deliverable |
| Aderis local | `inapplicable` until onboarded (`scripts/onboard_target.py wire`) — reason: no registered target profile yet |
| Aderis shared dev | `inapplicable` — requires operator-approved credentials harvest; deferred by design |

Every app-battery row records expected vs observed per tier, and a truncated or incomplete response yields `inconclusive`, blocking promotion — matching the acceptance wording of all ten issues.

Cross-references: [exploit playbooks](exploits/README.md) · [payload lists](../payloads/README.md) · [profiled battery](../scripts/garak_battery.py) · [test plan](owasp-llm10-test-plan.md)
