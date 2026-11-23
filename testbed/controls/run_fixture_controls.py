#!/usr/bin/env python3
"""Measure strict testbed controls using in-process HTTP and an offline Chromium page."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from ordinary_checks import ORDINARY_CASES

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "payloads" / "fixtures" / "testbed_controls.json"


def render_summary(app, request, summary):
    with closing(app.db.connect()) as con:
        cur = con.execute(
            "INSERT INTO summaries (tenant_id, user_id, content, created_at)"
            " SELECT tenant_id, id, ?, 0 FROM users WHERE username = ?",
            (summary, "alice@acme.test"),
        )
        con.commit()
        response = request("GET", "/llm/render", params={"id": cur.lastrowid})
    return inspect_dom(response)


def inspect_dom(response):
    from playwright.sync_api import sync_playwright

    response.raise_for_status()
    if response.headers["content-type"].split(";")[0] != "text/html":
        return {"content_type": response.headers["content-type"]}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(offline=True, service_workers="block")
        # A synthetic HTTP origin also lets Chromium resolve relative resource URLs.
        document_url = "http://renderer.invalid/summary"
        seen = []

        def fulfill_document(route):
            seen.append(route.request.url)
            if seen == [document_url]:
                route.fulfill(status=200, content_type=response.headers["content-type"], body=response.content)
            else:
                route.abort()

        context.route("**/*", fulfill_document)
        page = context.new_page()
        page.goto(document_url, wait_until="networkidle", timeout=10000)
        if seen[:1] != [document_url]:
            raise RuntimeError("Missing browser navigation evidence.")
        observed = {
            "status": response.status_code,
            "text": page.locator("pre").all_text_contents(),
            "elements_in_summary": page.locator("pre *").count(),
            "requests_from_page": seen[1:],
        }
        browser.close()
    return observed


def check_citation(request, inputs):
    claim = inputs["claim"]
    if "note" in inputs:
        created = request("POST", "/notes", json=inputs["note"])
        created.raise_for_status()
        source_id = created.json()["id"]
        source = request("GET", f"/notes/{source_id}")
        source.raise_for_status()
        if source.json()["body"] != claim:
            raise ValueError("The fixture source does not contain the exact claimed fact.")
        reference = f"note:{source_id}"
    else:
        reference = inputs["reference"]
        source = request("GET", "/notes/" + reference.removeprefix("note:"))
        if source.status_code != 404:
            raise ValueError("Could not confirm that the unsupported fixture lacks a source.")
    response = request("POST", "/llm/cite", json={
        "claim": claim, "title": inputs["title"], "reference": reference,
    })
    response.raise_for_status()
    saved = request("GET", "/facts")
    saved.raise_for_status()
    result = response.json()
    row = next((r for r in saved.json()["facts"] if r["id"] == result["id"]), {})
    return {
        "status": response.status_code, "returned": result["status"], "stored": row.get("status"),
        "claim_matches": row.get("claim") == claim, "reference_matches": row.get("reference") == reference,
    }


def observe(app, request, case, inputs):
    if case in ORDINARY_CASES:
        return ORDINARY_CASES[case](app, request, inputs)
    if case.startswith("artifact-"):
        with patch.object(app.eng, "run_plugin", wraps=app.eng.run_plugin) as hook:
            response = request("POST", "/llm/plugins/install", json=inputs)
        payload = response.json()
        return {
            "status": response.status_code, "installed": payload.get("installed"), "rejection": payload.get("detail"),
            "initialized": [call.args[0] for call in hook.call_args_list],
        }
    if case.startswith("citation-"):
        return check_citation(request, inputs)
    if case.startswith("render-"):
        return render_summary(app, request, inputs["summary"])
    raise ValueError(f"No observation defined for {case}")


def run(limit, fixtures):
    run_id = uuid4().hex
    observations = [{
        "case": name, "risk": fixture["risk"], "workflow": fixture["workflow"],
        "evidence_id": f"{run_id}/{name}", "result": "inconclusive", "reason": "Pending.",
        "input": fixture["input"], "expected": fixture["expected"],
    } for name, fixture in fixtures.items()]
    used = 0

    with TemporaryDirectory() as directory:
        # The app reads these settings and initializes its signing key at import time.
        os.environ.update(TESTBED_DATA_DIR=directory, TESTBED_GUARDS="strict", TESTBED_BACKEND="mock",
                          TESTBED_MARKER="fixture-private-context-value")
        sys.path.insert(0, str(ROOT / "testbed" / "llm-app"))
        try:
            from fastapi.testclient import TestClient
            from app import main as app

            app.db.seed()
            with TestClient(app.app, follow_redirects=False) as client:
                def request(method, path, **kwargs):
                    nonlocal used
                    if limit is not None and used >= limit:
                        raise RuntimeError(f"Request budget of {limit} exhausted.")
                    used += 1
                    return client.request(method, path, **kwargs)

                login = request("POST", "/auth/login", json={"username": "alice@acme.test", "password": "alice-pass"})
                login.raise_for_status()
                client.headers["authorization"] = "Bearer " + login.json()["token"]
                for record in observations:
                    case = record["case"]
                    if case == "feedback-review":
                        with closing(app.db.connect()) as con:
                            columns = [row["name"] for row in con.execute("PRAGMA table_info(feedback)")]
                        record["observed"] = {"feedback_columns": columns}
                        if set(columns) == {"id", "tenant_id", "user_id", "text", "created_at"}:
                            record.update(result="inapplicable", reason="The mock feedback schema and retrieval path have no review state or approved-source admission step.")
                        else:
                            record["reason"] = "Feedback schema changed. Review and admission capability needs verification."
                        continue
                    try:
                        actual = observe(app, request, case, record["input"])
                        matches = actual == record["expected"]
                        record.update(observed=actual, result="pass" if matches else "fail",
                                      reason="Observed expected application effect." if matches else "Application effect differs from the fixture expectation.")
                    except Exception as exc:
                        detail = str(exc).split("\n", 1)[0]
                        record["reason"] = f"{type(exc).__name__}: {detail}"
        except Exception as exc:
            detail = str(exc).split("\n", 1)[0]
            for record in observations:
                if record["reason"] == "Pending.":
                    record["reason"] = f"Fixture setup incomplete: {type(exc).__name__}: {detail}"

    return {"environment": "testbed-mock", "backend": "mock", "model": "deterministic",
            "requests_used": used, "observations": observations}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-requests", type=int, help="Maximum number of in-process HTTP requests, including login")
    fixtures = json.loads(FIXTURES.read_text())
    parser.add_argument("--case", action="append", choices=fixtures, help="Run only this case (repeatable)")
    args = parser.parse_args()
    if args.max_requests is not None and args.max_requests < 0:
        parser.error("--max-requests must be nonnegative")
    selected = {name: case for name, case in fixtures.items() if not args.case or name in args.case}
    report = run(args.max_requests, selected)
    print(json.dumps(report, indent=2))
    sys.exit(any(item["result"] in ("fail", "inconclusive") for item in report["observations"]))
