# Run the LLM control checks

The local battery exercises the strict testbed with synthetic business data. Its report shows the measured HTTP request count, the expected and observed application behavior and the evidence for each verdict. The application gate can also assess completed captures from a **separately identified environment**. A passing mock battery establishes fixture coverage only.

## 1. Prepare the local tools

Use Python 3.10+ for the control fixtures. From the repository root:

```sh
python3 -m venv .venv-controls
.venv-controls/bin/python -m pip install -r testbed/controls/requirements.txt
.venv-controls/bin/python -m playwright install chromium
export CONTROL_PYTHON="$PWD/.venv-controls/bin/python"
export GARAK_PYTHON="$PWD/.venv-garak/bin/python"
```

The full profile uses the garak environment installed by SEPP. Smoke runs the artifact cases without launching a browser. The [onboarding guide](local-readiness.md) covers registration, checking sanitized response fixtures and conversation isolation.

## 2. Run smoke or core

```sh
make llm-smoke
make llm-battery LLM_PROFILE=core
```

Each command prints the path to a new `report.json` inside `data/reports/`. Open the referenced `control-gate.json` for individual verdicts or `controls.json` for the inputs, expectations and observations. New runs use new directories. To specify a location, run `"$CONTROL_PYTHON" scripts/llm_battery.py --profile core --output <new-directory>`.

| Profile | Request ceiling | Checks |
|---|---:|---|
| Smoke | 12 | Offline parser readiness and two artifact-admission cases |
| Core | 414 | Readiness, strict fixtures across all ten OWASP 2026 categories, local conversation isolation and saved-response assertion calibration |
| Full | 628 | Core plus an offline garak plumbing run and a separately recorded authenticated Ollama summary |

The ceilings apply to HTTP requests issued by the fixture runner, including login. The current core fixtures use 21. A smaller `--max-requests` is enforced before dispatch. A required incomplete case blocks the run. Offline parser checks, direct mock calls, and replaying stored observations do not issue target requests. The local garak generator is an in-process fixture. Each evaluated detector and distinct attempt is counted separately in its report.

## 3. Obtain local-model evidence and run full

Start host Ollama with the specified model and the container-reachable local binding described in the [testbed setup](../testbed/README.md). Then run:

```sh
make testbed-local
mkdir -p data/reports
python3 testbed/local_summary.py > data/reports/local-summary.json
make llm-battery LLM_PROFILE=full LOCAL_SUMMARY=data/reports/local-summary.json
```

The full profile checks the supplied receipt for the expected workflow, model, completion ID, finish reason and ordinary roadmap facts. The receipt records an earlier model call. Its timestamp and provenance should be retained with the environment evidence. The full run does not count that earlier call as a new request. To inspect a separate completed garak report, run `python3 scripts/garak_summarize.py <report.jsonl>`.

## 4. Evaluate an application capture and gate promotion

The [example policy](../testbed/controls/saved-example/policy.json) names the expected environment, workflow, OWASP risk, backend and required cases. The [capture index](../testbed/controls/saved-example/index.json) identifies a completed Caido CLI response export and request ID for each case. For an asynchronous operation, set the policy case’s `completion_fields` to the expected top-level job identifier and terminal success state. The assessor checks these fields in the captured result before evaluating business assertions. A failed, pending or different job yields an inconclusive result. Keep the policy under separate control from the observations.

```sh
example=testbed/controls/saved-example
mkdir -p data/reports
python3 helpers/llm_job_harness.py assess "$example/policy.json" "$example/index.json" > data/reports/example-assessment.json
make llm-gate POLICY="$example/policy.json" REPORT=data/reports/example-assessment.json
```

The example should pass in calibration mode. For Aderis local or shared dev, provide that environment's own policy and completed response exports. Set `mode` to `application` and specify the expected backend and model. Each evaluated case needs its own request ID. The assessment records a digest of the supplied export. Capture origin and request counts come from the index and need independent collection at the source.

The assessor checks HTTP status, required or forbidden literal text, and expected top-level JSON fields. Status values can be a single code or a list. Absent or truncated evidence is inconclusive, including a body over the configured inspection limit (200,000 bytes by default). A failed assertion returns exit 2. Incomplete or invalid assessment input returns exit 1. The gate exits nonzero for a failed control, missing required evidence, an environment mismatch or an exceeded budget. Per-case reasons appear in JSON.

Inapplicability requires an explicit exception in the policy and a recorded reason. A policy consisting entirely of inapplicable cases cannot pass. On a mock calibration only, a case may set `calibration_result` to `fail` and name the expected failures in `calibration_failed_checks`. Unexpected failed assertions still block. [Fixture coverage and limits](../testbed/controls/README.md)

## Scope of the results

The fixture report distinguishes ten risk categories, thirteen cases and the application boundaries actually observed. Feedback review is inapplicable on the mock because its schema lacks an admission state. The summary case verifies a note baseline. Aderis document handling still requires the original attachment and its completed application result. Shared-dev runs have their own evidence and approval requirements. [Program issue #61](https://github.com/thomashartm/someone-elses-prompt-problem/issues/61)

For #50, use the [document summary verifier](../testbed/summary-format.md) to score the closing quotation and retained business fact for each original attachment.

For #51, the [customer redaction checks](../testbed/redaction.md) cover the ordinary customer response, streamed text and tool blocks, conversation replay, and the completed saved transcript.
