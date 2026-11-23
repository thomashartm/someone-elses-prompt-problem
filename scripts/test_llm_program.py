import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from llm_gate import decide

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from helpers.saved_assertions import assess

EXAMPLE = ROOT / "testbed/controls/saved-example"


class EvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((EXAMPLE / "policy.json").read_text())
        self.index = json.loads((EXAMPLE / "index.json").read_text())

    def test_complete_and_missing_evidence(self):
        report = assess(self.policy, self.index, EXAMPLE)
        self.assertEqual(decide(self.policy, report, "smoke")["verdict"], "pass")
        report["observations"].pop()
        outcome = decide(self.policy, report, "smoke")
        self.assertEqual(outcome["verdict"], "blocked")
        self.assertEqual(outcome["cases"][-1]["observed"], "inconclusive")

    def test_duplicate_id_and_source_cannot_support_two_cases(self):
        for key in ("file", "request_id"):
            with self.subTest(key=key):
                idx = copy.deepcopy(self.index)
                idx["records"]["unsupported"][key] = idx["records"]["supported"][key]
                with self.assertRaises(ValueError):
                    assess(self.policy, idx, EXAMPLE)

    def test_absence_requires_complete_evidence_and_handles_reference_after_2kb(self):
        with TemporaryDirectory() as directory:
            p = Path(directory).resolve()
            rule = self.policy["cases"]["supported"]
            rule["expect"] = {"status": [200, 201], "contains": ["LATE-REFERENCE"], "absent": ["PRIVATE-FIXTURE"]}
            body = b"x" * 2048 + b"LATE-REFERENCE"
            target = p / "supported.txt"
            target.write_bytes(f"201 text/plain {len(body)}B\n---\n".encode() + body + b"\n")
            complete = assess(self.policy, self.index, p)["observations"][0]
            self.assertEqual(complete["result"], "pass")
            target.write_bytes(b"200 text/plain 4000B\n---\nsample\n[...truncated, 4000B total]\n")
            incomplete = assess(self.policy, self.index, p)["observations"][0]
            self.assertEqual(incomplete["result"], "inconclusive")
            self.assertNotIn("checks", incomplete)

    def test_async_capture_must_identify_the_completed_job(self):
        self.policy["cases"]["supported"]["completion_fields"] = {"job": "one", "state": "complete"}
        with TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            for job, state, expected in [("one", "failed", "inconclusive"), ("two", "complete", "inconclusive"),
                                         ("one", "complete", "pass")]:
                body = json.dumps({"job": job, "state": state, "verified": True, "citation": "DEMO-24"}).encode()
                (folder / "supported.txt").write_bytes(f"200 application/json {len(body)}B\n---\n".encode() + body + b"\n")
                report = assess(self.policy, self.index, folder)
                self.assertEqual(report["observations"][0]["result"], expected)

    def test_inapplicable_case_requires_a_recorded_reason(self):
        report = assess(self.policy, self.index, EXAMPLE)
        self.policy["cases"]["unsupported"]["allow_inapplicable"] = True
        row = report["observations"][1]
        row.update(result="inapplicable", reason="review state absent from schema")
        self.assertEqual(decide(self.policy, report, "smoke")["verdict"], "pass")
        del row["reason"]
        self.assertEqual(decide(self.policy, report, "smoke")["verdict"], "blocked")

    def test_business_claim_and_calibration_are_distinct(self):
        self.policy["cases"]["unsupported"]["expect"]["json_fields"]["verified"] = True
        report = assess(self.policy, self.index, EXAMPLE)
        self.assertEqual(report["observations"][1]["result"], "fail")
        self.assertEqual(decide(self.policy, report, "smoke")["verdict"], "blocked")
        self.policy["cases"]["unsupported"]["calibration_result"] = "fail"
        self.policy["cases"]["unsupported"]["calibration_failed_checks"] = ["json_fields"]
        self.assertEqual(decide(self.policy, report, "smoke")["verdict"], "pass")
        report["observations"][1]["checks"]["status"] = False
        self.assertEqual(decide(self.policy, report, "smoke")["verdict"], "blocked")
        self.policy["mode"] = "application"
        with self.assertRaises(ValueError):
            decide(self.policy, report, "smoke")

    def test_budget_and_independent_identity(self):
        report = assess(self.policy, self.index, EXAMPLE)
        report["requests_used"] = 13
        self.assertEqual(decide(self.policy, report, "smoke")["verdict"], "blocked")
        self.policy["environment"] = "shared-dev"
        with self.assertRaises(ValueError):
            decide(self.policy, report, "core")

    def test_unknown_expectation_and_escaping_path_fail_closed(self):
        for bad in ({}, {"unknown": "x"}, {"absent": []}):
            self.policy["cases"]["supported"]["expect"] = bad
            with self.assertRaises(ValueError):
                assess(self.policy, self.index, EXAMPLE)
        self.policy["cases"]["supported"]["expect"] = {"status": 200}
        self.index["records"]["supported"]["file"] = "../other"
        with self.assertRaises(ValueError):
            assess(self.policy, self.index, EXAMPLE)
