"""Step-4 acceptance wiring: offline proof of the issue-to-evidence driver.

Run: python3 -m unittest scripts.test_llm_acceptance
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import llm_acceptance as acc  # noqa: E402


def registry_fixture():
    return {
        "environment": "testbed-mock",
        "exploit_report": "exp.json",
        "garak": {"report": "g.json", "policy": "p.json", "profile": "full"},
        "other_environments": {"aderis-local": "inapplicable: no target"},
        "issues": [
            {"id": 50, "risk": "LLM01", "requirement": "summaries obey",
             "exploit_items": ["LLM01"],
             "garak_cases": ["DAN_Jailbreak"]},
            {"id": 58, "risk": "LLM08", "requirement": "tenant isolation",
             "exploit_items": ["LLM08", "LLM09"],
             "garak_cases": []},
        ],
    }


def exploit_report_fixture():
    mk = lambda tier, item, verdict, obs: {"tier": tier, "item": item,
                                           "workflow": "w", "expected": "exp " + tier,
                                           "observed": obs, "verdict": verdict}
    return {"results": [
        mk("naive", "LLM01", "pass", "marker present"),
        mk("strict", "LLM01", "pass", "masked"),
        mk("naive", "LLM08", "pass", "inbound"),
        mk("strict", "LLM08", "pass", "filtered"),
        mk("naive", "LLM09", "pass", "captured"),
        mk("strict", "LLM09", "pass", "supported"),
    ]}


def exploit_report_missing_tier():
    """Same but LLM09/strict vanished - the incomplete-run case."""
    full = exploit_report_fixture()
    full["results"] = [r for r in full["results"]
                       if not (r["item"] == "LLM09" and r["tier"] == "strict")]
    return full


DECISIONS = {
    "DAN_Jailbreak": {"case": "DAN_Jailbreak", "expected": "fail", "observed": "fail",
                      "accepted": True, "evidence_id": "run-1:dan.DAN_Jailbreak"},
}


class BuildRecordsTests(unittest.TestCase):
    def test_happy_path_issue_accepted_with_expected_fields(self):
        issues, ok = acc.build_issue_records(registry_fixture(),
                                             acc.index_exploit_rows(exploit_report_fixture()),
                                             DECISIONS)
        self.assertTrue(ok)
        i50 = issues[0]
        self.assertEqual(i50["status"], "accepted")
        rec = next(r for r in i50["records"] if r["kind"] == "garak")
        self.assertEqual(rec["expected"], "fail")
        self.assertEqual(rec["observed"], "fail")
        self.assertEqual(rec["evidence_id"], "run-1:dan.DAN_Jailbreak")
        self.assertTrue(all(set(r) >= {"expected", "observed", "accepted",
                                       "evidence_id", "environment"}
                            for r in i50["records"]))
        self.assertTrue(all(r["environment"] == "testbed-mock"
                            for r in i50["records"]))

    def test_missing_strict_tier_rejects_issue(self):
        issues, ok = acc.build_issue_records(registry_fixture(),
                                             acc.index_exploit_rows(exploit_report_missing_tier()),
                                             DECISIONS)
        i58 = issues[1]
        self.assertEqual(i58["status"], "rejected")
        missing = next(r for r in i58["records"] if r["check"] == "LLM09"
                       and r["tier"] == "strict")
        self.assertFalse(missing["accepted"])
        self.assertEqual(missing["observed"], "missing")
        self.assertFalse(ok)

    def test_missing_garak_decision_rejects(self):
        issues, ok = acc.build_issue_records(registry_fixture(),
                                             acc.index_exploit_rows(exploit_report_fixture()),
                                             {})
        i50 = issues[0]
        self.assertEqual(i50["status"], "rejected")

    def test_registry_covers_all_ten_issues_once(self):
        cfg = json.loads(acc.DEFAULT_CONFIG.read_text())
        ids = [i["id"] for i in cfg["issues"]]
        self.assertEqual(sorted(ids), list(range(50, 60)))
        risks = {i["risk"] for i in cfg["issues"]}
        self.assertEqual(risks, {f"LLM{n:02d}" for n in range(1, 11)})
        for issue in cfg["issues"]:
            self.assertTrue(issue["requirement"].strip())
            self.assertTrue(issue["exploit_items"])  # every issue has app-layer evidence


class EvaluateIoTests(unittest.TestCase):
    def test_evaluate_writes_records_and_exit_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "configs").mkdir()
            (root / "docs").mkdir()
            cfg = registry_fixture()
            (root / "configs" / "acc.json").write_text(json.dumps(cfg))
            (root / "exp.json").write_text(json.dumps(exploit_report_fixture()))

            # full gate-shaped garak report + policy so llm_gate.decide runs
            cases_policy = {"DAN_Jailbreak": {"risk": "LLM01", "workflow": "jailbreak",
                                              "calibration_result": "fail",
                                              "calibration_failed_checks": ["MitigationBypass"]}}
            (root / "p.json").write_text(json.dumps({
                "mode": "calibration", "environment": "testbed-mock", "cases": cases_policy}))
            obs_dan = {"case": "DAN_Jailbreak", "risk": "LLM01", "workflow": "jailbreak",
                       "result": "fail", "evidence_id": "run-1:dan.DAN_Jailbreak",
                       "reason": "attack success", "checks": {"MitigationBypass": False}}
            (root / "g.json").write_text(json.dumps({
                "environment": "testbed-mock", "backend": "shim:testbed",
                "model": "testbed-mock", "requests_used": 1,
                "observations": [obs_dan]}))

            out = root / "docs" / "records.json"
            document, ok = acc.evaluate(root / "configs" / "acc.json", out)
            self.assertTrue(ok)
            persisted = json.loads(out.read_text())
            self.assertEqual(persisted["verdict"], "accepted")
            self.assertEqual(persisted["garak_run"]["gate_verdict"], "pass")
            self.assertEqual(persisted["other_environments"]["aderis-local"],
                             "inapplicable: no target")
            self.assertIn("generated", persisted)


if __name__ == "__main__":
    unittest.main()
