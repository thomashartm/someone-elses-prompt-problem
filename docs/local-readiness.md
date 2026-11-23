# Offline target readiness

From the repository root, list registered targets and check the two representative responses for the bundled notes fixture.

```sh
python3 scripts/onboard_target.py --targets-dir testbed/controls/conversation_targets list
python3 scripts/onboard_target.py --targets-dir testbed/controls/conversation_targets check notes-fixture
python3 testbed/controls/conversation_fixture.py
```

The first command lists the target, backend, workflow, profile and adapter presence. The second reports `fixture-verified` when the reviewed response parser extracts the expected kind and exact text from both files. Each observation identifies its capture by evidence ID and SHA-256. A missing, placeholder, incomplete, duplicate or mismatched fixture leaves the result `unverified` (exit 1).

The third command starts two local note-summary conversations with the same opening, adds a different note to each, and summarizes both with the testbed's deterministic mock engine. A reset retires the old ID and creates an empty notebook. Summarizing an empty notebook returns a decline and retires that ID. Adding or summarizing a note counts as one turn, including the opening note. The fourth turn completes and retires that session. The receipt contains ten individual checks and observed replies, including confirmation that a second session retains its own history.

Both verification commands use zero target HTTP requests. The bundled profile describes an in-process fixture workflow. A deployed target, the production shim and provider-backed model behavior each require separate evidence.

## Register a sanitized JSON bundle

Use a temporary inventory to try registration with the bundled example.

```sh
scratch=$(mktemp -d)
python3 scripts/onboard_target.py --targets-dir "$scratch/targets" register notes-fixture --fixtures testbed/controls/conversation_targets/notes-fixture
python3 scripts/onboard_target.py --targets-dir "$scratch/targets" check notes-fixture
```

For your own bundle, use the same three data files. Set the manifest's target to the registration name. Use `fixtures/ok.json` with a single `answer` string and `fixtures/declined.json` with a single `decline` string. Registration verifies the pair with the built-in JSON parser and publishes the two capture files and selected profile fields in one directory rename. Existing registrations are preserved. A rejected bundle exits with status 1 and can be corrected and retried.

## Register an existing response parser

1. Use a local `data/targets/<slug>/` directory with an operator-reviewed `adapter.py`. Checking imports that file as Python code and calls `parse(raw_response_body)`. Use a parser whose import and parsing are side-effect free. Inventory only reads profile metadata.
2. Place one sanitized ordinary success body and one sanitized soft-decline body in `fixtures/`. Use the actual body format, including event-stream frames when the parser expects them. Remove credentials and personal information before saving. Select complete captures with known application outcomes.
3. Create `readiness.json` using the [bundled profile](../testbed/controls/conversation_targets/notes-fixture/readiness.json) as the template. Set the target to the directory name, set the backend and application workflow, and give each capture a distinct evidence ID and independently known expected assistant text. For an HTTP capture, use `"delivery": "http"`, include the integer `http_status`, and set `completion` to `complete` only after observing the terminal response. For a direct local function call use `"delivery": "in-process"` and omit the HTTP status.
4. Run `python3 scripts/onboard_target.py check <slug>`. The default inventory root is `data/targets/`. The receipt verifies response parsing against the supplied evidence. Capture origin and completion come from the operator's profile.

Run the focused local checks with `python3 -m unittest discover -s scripts -p test_local_readiness.py`.
