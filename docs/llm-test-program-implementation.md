# LLM test program implementation plan

SEPP needs three pieces of evidence before a control can count toward a shared-dev release decision. The deterministic testbed must distinguish the expected outcomes, the selected model must have handled the request, and the application must have enforced the control through the final user-visible result. Track these observations separately for the mock testbed, the local-model testbed, Aderis local and shared dev. [Program ticket #61](https://github.com/thomashartm/someone-elses-prompt-problem/issues/61)

## First deliverable

Submit an ordinary authenticated summary to the local testbed using Ollama and `qwen2.5:7b`. Record the resolved model identity, application result and limits in effect. Exercise the budget boundary with a counting provider stub. Verify that rejection occurs before a further provider call. The existing testbed is sufficient for this work.

## Delivery and acceptance

| Phase | Work | Acceptance evidence |
|---|---|---|
| 0. Prerequisites | Verify the selected adapter, target, model and canary artifact. | A current run has evaluated output and a matching completion record. Keep fixture evidence and a live testbed run distinguishable. |
| 1. Onboarding | Register a named target with representative success and decline fixtures. | The offline check validates the adapter against both fixtures. Missing evidence leaves readiness unverified. A failed onboarding preserves an existing registration. |
| 2. Local model | Add a local provider profile using the existing compatible client and both provider dispatch paths. | An authenticated summary reaches the selected local model. A provider error is visible, the token and character limits work, and the mock remains usable on its own. |
| 3. Conversations | Exercise fresh and continued conversations using an explicit session identity. | Two independent conversations with identical opening text remain isolated. A decline, explicit reset or turn limit clears only the affected conversation. |
| 4. Rendering | Follow an inert synthetic value to the application’s renderer. | Inspect the DOM. Plain-text content remains text and rendering initiates zero external requests. Record which renderer actually ran. |
| 5. Application assertions | Check expected HTTP status, required and forbidden text, and the final business result from complete evidence. | Every observation identifies its own evidence. A failed job, truncated response, or ambiguous request association is inconclusive. A required inconclusive observation blocks promotion. |
| 6. Profiles and gate | Provide smoke, core and full profiles with request accounting. Use structured results for the gate. | Record the declared budget and actual use. Distinguish successful calibration of a weak fixture from a passing application control. Report each failed or incomplete requirement individually. |
| 7. Coverage | Map the testbed’s 2025 IDs to the 2026 application issues. Add fixture pairs for artifact admission, unreviewed knowledge and unsupported citations. | Each applicable control has an ordinary positive case and a contrasting case with a measurable expected result. Record the reason for each inapplicable feature. |
| 8. Reporting | Publish runnable instructions and a report that identifies environment, backend, model, workflow, expected outcome and observed evidence. | A reviewer can trace every verdict and distinguish a model response from the final application effect. An attachment result identifies the original file and completed ingestion workflow. |

The planned profile ceilings are 12 requests for smoke, 414 for core and 628 for full. Count actual requests independently of the number of detector scores. Apply the repository’s scope, rate, workspace and operator-approval rules for any environment run. Use synthetic accounts and documents for calibration. [Repository operating instructions](../AGENTS.md)

## Existing work to reuse

The merged harness rejects incomplete responses, the canary verifies target identity and the current report, and Compose forwards provider and budget settings. A successful provider-stub check covers configuration. The local Ollama run supplies separate evidence of model execution. [Response handling](../helpers/llm_job_harness.py) · [Canary](../scripts/run_garak_canary.sh) · [Report validation](../scripts/verify_garak_report.py) · [Compose settings](../testbed/docker-compose.yml)

Application acceptance is tracked in issues #50 through #59. The document-summary fixture work belongs to #50. Keep the original document, ingestion path, completed response and test environment associated throughout a run. For a background job, follow it to completion before recording a verdict. [Document summary issue](https://github.com/thomashartm/someone-elses-prompt-problem/issues/50) · [Testbed ground truth](../testbed/EXPECTATIONS.md)
