"""Offline acceptance for the profiled garak battery runner (plan Phase 6).

Proves: config validation, preflight budget enforcement before traffic,
attempt-based request accounting independent of detector scores, and the
conservative result derivation (unevaluated -> inconclusive, never pass).
Zero network, zero garak import. Run:
    python3 -m unittest scripts.test_garak_battery
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import garak_battery as gb  # noqa: E402


class TempTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


def profile_fixture(**over):
    base = {
        "description": "fixture profile",
        "ceiling": 12,
        "generations": 1,
        "predicted_requests": 3,
        "probes": {
            "dan.DAN_Jailbreak": {"risk": "LLM01", "workflow": "jailbreak"},
            "web_injection.StringAssemblyDataExfil": {"risk": "LLM05", "workflow": "exfil"},
        },
    }
    base.update(over)
    return base


def write_config(root, profiles_spec):
    path = root / "profiles.json"
    path.write_text(json.dumps({"profiles": profiles_spec}))
    return path


class ConfigValidationTests(TempTestCase):
    def test_real_config_loads_and_respects_ceiling_arithmetic(self):
        profiles = gb.load_profiles()  # committed configs/garak_profiles.json
        for name, spec in profiles.items():
            self.assertLessEqual(spec["predicted_requests"], spec["ceiling"],
                                 f"{name} declares over-ceiling requests")
        self.assertEqual(profiles["full"]["ceiling"], 628)
        smoke = set(profiles["smoke"]["probes"])
        self.assertTrue(smoke <= set(profiles["core"]["probes"]))
        self.assertTrue(set(profiles["core"]["probes"]) <= set(profiles["full"]["probes"]))

    def test_bad_risk_rejected(self):
        path = write_config(self.root, {"smoke": {"predicted_requests": 1, "probes": {
            "dan.X": {"risk": "LLM23", "workflow": "w"}}}})
        with self.assertRaisesRegex(gb.BatteryError, "risk"):
            gb.load_profiles(path)

    def test_empty_probe_set_rejected(self):
        path = write_config(self.root, {"smoke": {"predicted_requests": 3, "probes": {}}})
        with self.assertRaisesRegex(gb.BatteryError, "empty probe set"):
            gb.load_profiles(path)

    def test_unknown_profile_name_rejected(self):
        path = write_config(self.root, {"microwave": {"predicted_requests": 1, "probes": {
            "dan.X": {"risk": "LLM01", "workflow": "w"}}}})
        with self.assertRaisesRegex(gb.BatteryError, "unknown profile"):
            gb.load_profiles(path)

    def test_malformed_probe_name_rejected(self):
        path = write_config(self.root, {"smoke": {"predicted_requests": 1, "probes": {
            "justclass": {"risk": "LLM01", "workflow": "w"}}}})
        with self.assertRaisesRegex(gb.BatteryError, "'module.Class'"):
            gb.load_profiles(path)

    def test_unknown_parent_rejected(self):
        path = write_config(self.root, {"smoke": {
            "inherits": "ghost", "predicted_requests": 1, "probes": {
                "dan.X": {"risk": "LLM01", "workflow": "w"}}}})
        with self.assertRaisesRegex(gb.BatteryError, "unknown profile 'ghost'"):
            gb.load_profiles(path)


class PreflightTests(TempTestCase):
    def setUp(self):
        super().setUp()
        self.profiles = {"smoke": {**profile_fixture(), "ceiling": 12}}

    def test_over_ceiling_refused_before_traffic(self):
        with patch.object(gb, "count_prompts",
                          return_value={"dan.DAN_Jailbreak": 20,
                                        "web_injection.StringAssemblyDataExfil": 20}):
            with self.assertRaisesRegex(gb.BatteryError, "exceed"):
                gb.preflight("smoke", self.profiles, Path("/fake"))

    def test_within_ceiling_returns_prediction(self):
        with patch.object(gb, "count_prompts",
                          return_value={"dan.DAN_Jailbreak": 1,
                                        "web_injection.StringAssemblyDataExfil": 2}):
            self.assertEqual(gb.preflight("smoke", self.profiles, Path("/fake")), 3)

    def test_generations_multiply_into_budget(self):
        self.profiles["smoke"]["generations"] = 2
        with patch.object(gb, "count_prompts",
                          return_value={"dan.DAN_Jailbreak": 4,
                                        "web_injection.StringAssemblyDataExfil": 4}):
            with self.assertRaisesRegex(gb.BatteryError, "exceed"):
                gb.preflight("smoke", self.profiles, Path("/fake"))


def report_text(eval_rows, attempts=2, run="run-uuid-1", states_per_attempt=1):
    """states_per_attempt emulates garak re-emitting an attempt per state
    transition (issued -> responded) with the SAME uuid."""
    lines = [{"entry_type": "start_run setup"},
             {"entry_type": "init", "run": run, "garak_version": "0.17.1",
              "start_time": "now"}]
    for i in range(attempts):
        for _state in range(states_per_attempt):
            lines.append({"entry_type": "attempt", "seq": i,
                          "probe_classname": "x", "status": 1,
                          "uuid": f"attempt-{i}"})
    lines += eval_rows
    lines.append({"entry_type": "completion", "run": run, "end_time": "later"})
    return "\n".join(json.dumps(l) for l in lines)


class SummarizeTests(TempTestCase):
    def setUp(self):
        super().setUp()
        self.profile = profile_fixture()
        self.identity = {"environment": "testbed-mock",
                         "backend": "shim:testbed", "model": "testbed-mock"}

    def write_report(self, rows, attempts=2, states_per_attempt=1):
        path = self.root / "r.jsonl"
        path.write_text(report_text(rows, attempts, states_per_attempt=states_per_attempt))
        return path

    @staticmethod
    def eval_row(probe, detector, passed, fails=0, nones=0):
        return {"entry_type": "eval", "probe": probe, "detector": detector,
                "passed": passed, "fails": fails, "nones": nones,
                "total_evaluated": passed + fails + nones}

    def test_all_green_derives_pass_with_detector_checks(self):
        path = self.write_report([
            self.eval_row("dan.DAN_Jailbreak", "dan.DANJailbreak", 1),
            self.eval_row("web_injection.StringAssemblyDataExfil",
                          "web_injection.StringAssemblyDataExfil", 1)],
            attempts=2, states_per_attempt=2)  # 4 records -> 2 real requests
        rep = gb.summarize_report(path, "smoke", self.profile, self.identity)
        self.assertEqual(rep["requests_used"], 2)  # distinct attempts, not state re-emits
        self.assertEqual([o["result"] for o in rep["observations"]], ["pass", "pass"])
        self.assertTrue(all(o["checks"] for o in rep["observations"]))
        ids = [o["evidence_id"] for o in rep["observations"]]
        self.assertEqual(len(set(ids)), len(ids))  # gate rejects duplicate evidence ids
        self.assertTrue(all(i and "run-uuid-1" in i for i in ids))
        self.assertEqual(rep["environment"], "testbed-mock")
        self.assertEqual(rep["model"], "testbed-mock")

    def test_attack_success_derives_fail_naming_detector(self):
        path = self.write_report([
            self.eval_row("dan.DAN_Jailbreak", "dan.DANJailbreak", 0, fails=1)])
        rep = gb.summarize_report(path, "smoke", self.profile, self.identity)
        dan = next(o for o in rep["observations"] if o["case"] == "DAN_Jailbreak")
        self.assertEqual(dan["result"], "fail")
        self.assertFalse(dan["checks"]["DANJailbreak"])
        self.assertIn("DANJailbreak", dan["reason"])

    def test_missing_eval_derives_inconclusive(self):
        path = self.write_report([
            self.eval_row("dan.DAN_Jailbreak", "dan.DANJailbreak", 1)])
        rep = gb.summarize_report(path, "smoke", self.profile, self.identity)
        other = next(o for o in rep["observations"]
                     if o["case"] == "StringAssemblyDataExfil")
        self.assertEqual(other["result"], "inconclusive")

    def test_partially_evaluated_never_passes(self):
        path = self.write_report([
            self.eval_row("dan.DAN_Jailbreak", "dan.DANJailbreak", 1),
            self.eval_row("web_injection.StringAssemblyDataExfil",
                          "web_injection.StringAssemblyDataExfil", 0, nones=1)])
        rep = gb.summarize_report(path, "smoke", self.profile, self.identity)
        other = next(o for o in rep["observations"]
                     if o["case"] == "StringAssemblyDataExfil")
        self.assertEqual(other["result"], "inconclusive")

    def test_incomplete_report_rejected(self):
        path = self.root / "bad.jsonl"
        path.write_text(json.dumps({"entry_type": "init", "run": "x"}))
        with self.assertRaises(Exception):
            gb.summarize_report(path, "smoke", self.profile, self.identity)


class DryRunCliTests(TempTestCase):
    def test_dry_run_never_spawns_shim_or_garak(self):
        profiles = {"smoke": {**profile_fixture()}}
        with patch.object(sys, "argv", ["garak_battery.py", "--profile", "smoke",
                                        "--dry-run"]), \
             patch.object(gb, "load_profiles", return_value=profiles), \
             patch.object(gb, "count_prompts",
                          return_value={"dan.DAN_Jailbreak": 1,
                                        "web_injection.StringAssemblyDataExfil": 2}):
            rc = gb.main()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
