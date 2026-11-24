# Strict mock fixture controls

Run with Python 3.10+ from the repository root. Install the testbed and browser dependencies once.

```sh
python3 -m venv .venv-controls
.venv-controls/bin/python -m pip install -r testbed/controls/requirements.txt
.venv-controls/bin/python -m playwright install chromium
.venv-controls/bin/python testbed/controls/run_fixture_controls.py --max-requests 21 > controls.json
```

The run uses a freshly seeded temporary SQLite database and FastAPI’s in-process HTTP client. The full run makes 21 application requests, including login and source readback. A lower limit stops further application requests before dispatch. Repeat `--case` to run a subset. For example, `--case artifact-approved --case artifact-unapproved` uses three requests including login. The CLI writes JSON to stdout and exits nonzero on failure or inconclusive evidence. Each observation includes its fixture input, expected outcome and evidence ID. Completed checks include the observed outcome.

| Mock fixture (2025) | Program issue (2026) | Evidence |
|---|---|---|
| LLM01 | LLM01 / #50 | The ordinary note summary includes the fixture text, and raw mock output, visible summary and persisted content match. This is the note-summary baseline. |
| LLM02 | LLM02 / #51 | An authorized synthetic customer read exposes the masked contact in the provider context and the complete response. This route has no streamed or saved answer. [Redaction results and Aderis capture checks](../redaction.md) |
| LLM06 | LLM03 / #52 | The authorized calculator receives the requested addition exactly once and the fetch tool receives zero calls. |
| LLM03 | LLM04 / #53 | Approved plugin ID reaches the real initialization hook. The unapproved ID receives the registry rejection and zero hook calls. |
| LLM04 | LLM05 / #54 | Two synthetic sources give different inventory weekdays and carry distinct review states. The mock has no review state or review-based admission step, so this case is inapplicable. |
| LLM10 | LLM06 / #55 | A one-job quota admits one ordinary request. The second response is 429 and the counting generator receives zero additional calls. |
| LLM09 | LLM07 / #56 | A synthetic note contains the exact supported claim. A second claim cites an absent note. Both the returned and persisted verification status are checked. The mock verifies reference existence. Semantic validation of an unrelated, existing reference is outside this pair. |
| LLM07 | LLM08 / #57 | One ordinary chat question reaches the mock with masked context. The provider arguments and final answer exclude the private synthetic fixture value. |
| LLM08 | LLM09 / #58 | An ordinary search returns the caller’s known note. Each returned note ID is independently checked against database tenant ownership. |
| LLM05 | LLM10 / #59 | A plain summary and a summary containing an inert span reach the actual strict renderer. Headless Chromium checks the resulting text, child element count and resource requests during page load. |

Chromium receives the application’s response at a fixed synthetic HTTP origin. Playwright fulfills the document navigation in memory and records and aborts all subsequent page requests. Chromium runs offline with service workers blocked. The two synthetic document loads never reach the application or a network server. The renderer controls cover page load through network idle. Verify the request observer itself with `.venv-controls/bin/python -m unittest discover -s testbed/controls -v`. An ordinary stylesheet fixture must produce one intercepted request. This mock suite covers no asynchronous job consumer.

Input and expected outcomes are in `payloads/fixtures/testbed_controls.json`. The structured capture describes the strict mock environment only.
