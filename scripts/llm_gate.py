#!/usr/bin/env python3
"""Decide whether all required observations satisfy an independently supplied policy."""
import argparse
import json
from pathlib import Path
import sys

PROFILES = {"smoke": 12, "core": 414, "full": 628}
RESULTS = {"pass", "fail", "inapplicable", "inconclusive"}


def decide(policy, report, profile):
    mode = policy["mode"]
    if mode not in {"calibration", "application"} or report["environment"] != policy["environment"]:
        raise ValueError("unexpected environment or gate mode")
    for key in ("environment", "backend", "model"):
        if not isinstance(report[key], str) or not report[key].strip():
            raise ValueError(f"missing {key} identity")
        if key in policy and policy[key] != report[key]:
            raise ValueError(f"unexpected {key}")
    if mode == "application" and (report["backend"] in {"mock", "recorded"} or report["environment"] in {"offline-fixture", "testbed-mock"}):
        raise ValueError("application gate requires application evidence")
    if mode == "application" and not (policy.get("backend") and policy.get("model")):
        raise ValueError("application policy must specify the expected backend and model")
    count = report["requests_used"]
    if type(count) is not int or count < 0:
        raise ValueError("missing request count")
    required = policy["cases"]
    if not isinstance(required, dict) or not required:
        raise ValueError("empty gate policy")
    by_case, ids = {}, set()
    for row in report["observations"]:
        case, eid = row["case"], row.get("evidence_id")
        if case in by_case or case not in required or row["result"] not in RESULTS:
            raise ValueError(f"unexpected, duplicate or invalid case: {case}")
        if eid is not None:
            if not isinstance(eid, str) or not eid.strip() or eid in ids:
                raise ValueError(f"{case}: ambiguous evidence ID")
            ids.add(eid)
        by_case[case] = row
    decisions = []
    passed = count <= PROFILES[profile]
    evaluated = 0
    for case, rule in required.items():
        expected = rule.get("calibration_result", "pass")
        if expected not in {"fail", "pass"} or (mode == "application" and expected != "pass"):
            raise ValueError(f"{case}: invalid expectation for {mode}")
        if expected == "fail":
            allowed = rule.get("calibration_failed_checks")
            if not isinstance(allowed, list) or not allowed or not all(isinstance(k, str) and k for k in allowed):
                raise ValueError(f"{case}: specify the expected failed assertions")
        row = by_case.get(case, {})
        actual = row.get("result", "inconclusive")
        reason = row.get("reason", "missing observation")
        eid = row.get("evidence_id")
        if row and (row.get("risk") != rule["risk"] or row.get("workflow") != rule["workflow"]):
            raise ValueError(f"{case}: observation belongs to a different risk or workflow")
        if actual == "inapplicable":
            ok = rule.get("allow_inapplicable") is True and isinstance(row.get("reason"), str) and bool(reason.strip())
        else:
            ok = bool(eid) and actual == expected
            if ok and expected == "fail":
                checks = row.get("checks", {})
                ok = (isinstance(checks, dict) and all(type(v) is bool for v in checks.values())
                      and {k for k, v in checks.items() if not v} == set(allowed))
            evaluated += int(bool(eid) and actual in {"pass", "fail"})
        decisions.append({"case": case, "risk": rule["risk"], "expected": expected, "observed": actual,
                          "evidence_id": eid, "reason": reason, "accepted": ok})
        passed = passed and ok
    return {"mode": mode, "profile": profile, "environment": report["environment"], "backend": report["backend"],
            "model": report["model"], "requests_used": count, "limit": PROFILES[profile],
            "within_budget": count <= PROFILES[profile], "cases": decisions,
            "verdict": "pass" if passed and evaluated else "blocked"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--profile", choices=PROFILES, default="smoke")
    args = parser.parse_args()
    try:
        outcome = decide(json.loads(args.policy.read_text()), json.loads(args.report.read_text()), args.profile)
    except (KeyError, ValueError, OSError, TypeError) as exc:
        parser.exit(1, f"Invalid gate input: {exc}\n")
    print(json.dumps(outcome, indent=2))
    return int(outcome["verdict"] != "pass")


if __name__ == "__main__":
    sys.exit(main())
