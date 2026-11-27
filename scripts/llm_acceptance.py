#!/usr/bin/env python3
"""Step-4 wiring: turn issue #50-#59 acceptance criteria into evidence records.

Consumes configs/llm_acceptance.json (the committed issue-to-check mapping)
plus two battery reports:
  - the exploit battery (naive+strict tiers, verdicts pre-graded against
    testbed/EXPECTATIONS.md)
  - one garak battery report, judged through scripts/llm_gate.py with a
    calibration policy (per-detector verdicts, evidence IDs)

Emits docs/llm-acceptance-records.json: one record per issue check with
{expected, observed, accepted, evidence_id, environment} - the exact fields
the 2026 issues demand ("record expected and observed", "evidence per case",
"inapplicable with a reason"). Other environments stay explicit placeholders
until real runs exist (Ollama = pending, Aderis = inapplicable with reasons).

Exit 0 iff every issue is ACCEPTED in the wired environment; 1 otherwise
(any check rejected or missing) - suitable as the regression gate #61 reads.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_CONFIG = ROOT / "configs" / "llm_acceptance.json"
DEFAULT_OUT = ROOT / "docs" / "llm-acceptance-records.json"
TIERS = ("naive", "strict")

sys.path.insert(0, str(HERE))
import llm_gate  # noqa: E402


def index_exploit_rows(report: dict) -> dict[str, dict[str, dict]]:
    """(item -> tier -> row) from an exploit-battery report."""
    index: dict[str, dict[str, dict]] = {}
    for row in report["results"]:
        index.setdefault(row["item"], {})[row["tier"]] = row
    return index


def build_issue_records(registry: dict, exploit_index: dict,
                        garak_decisions: dict[str, dict]) -> tuple[list[dict], bool]:
    """Pure core: per-issue records. Returns (issues, all_accepted)."""
    environment = registry["environment"]
    issues, all_accepted = [], True
    for spec in registry["issues"]:
        records = []
        for item in spec["exploit_items"]:
            for tier in TIERS:
                row = exploit_index.get(item, {}).get(tier)
                if row is None:
                    records.append({
                        "kind": "exploit", "check": item, "tier": tier,
                        "expected": "graded row", "observed": "missing",
                        "accepted": False, "evidence_id": None,
                        "environment": environment})
                    continue
                records.append({
                    "kind": "exploit", "check": item, "tier": tier,
                    "expected": row["expected"], "observed": row["observed"],
                    "accepted": row["verdict"] == "pass",
                    "evidence_id": f"{row['item']}:{tier}",
                    "environment": environment})
        for case in spec["garak_cases"]:
            decision = garak_decisions.get(case)
            if decision is None:
                records.append({
                    "kind": "garak", "check": case, "tier": None,
                    "expected": "policy verdict", "observed": "missing",
                    "accepted": False, "evidence_id": None,
                    "environment": environment})
                continue
            records.append({
                "kind": "garak", "check": case, "tier": None,
                "expected": decision["expected"], "observed": decision["observed"],
                "accepted": bool(decision["accepted"]),
                "evidence_id": decision.get("evidence_id"),
                "environment": environment})
        issue_accepted = bool(records) and all(r["accepted"] for r in records)
        all_accepted = all_accepted and issue_accepted
        issues.append({
            "issue": spec["id"], "risk": spec["risk"],
            "requirement": spec["requirement"],
            "status": "accepted" if issue_accepted else "rejected",
            "records": records})
    return issues, all_accepted


def evaluate(config_path: pathlib.Path, out_path: pathlib.Path) -> tuple[dict, bool]:
    raw = json.loads(config_path.read_text())
    root = config_path.parent.parent  # configs/ -> repo root

    def load(rel: str) -> dict:
        return json.loads((root / rel).read_text())

    exploit_report = load(raw["exploit_report"])
    garak_cfg = raw["garak"]
    gate = llm_gate.decide(load(garak_cfg["policy"]), load(garak_cfg["report"]),
                           garak_cfg["profile"])
    decisions = {d["case"]: d for d in gate["cases"]}

    issues, all_accepted = build_issue_records(
        raw, index_exploit_rows(exploit_report), decisions)

    document = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "environment": raw["environment"],
        "garak_run": {
            "report": garak_cfg["report"],
            "profile": garak_cfg["profile"],
            "gate_verdict": gate["verdict"],
            "within_budget": gate["within_budget"],
            "requests_used": gate["requests_used"],
        },
        "exploit_report": raw["exploit_report"],
        "other_environments": raw["other_environments"],
        "issues": issues,
        "verdict": "accepted" if all_accepted else "rejected",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2) + "\n")
    return document, all_accepted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    try:
        document, ok = evaluate(args.config, args.out)
    except (KeyError, ValueError, TypeError, OSError) as exc:
        print(f"acceptance wiring failed: {exc}", file=sys.stderr)
        return 2
    print(f"acceptance records: {args.out}  ({len(document['issues'])} issues)")
    for issue in document["issues"]:
        flag = "ACCEPT" if issue["status"] == "accepted" else "REJECT"
        print(f"  [{flag}] #{issue['issue']} {issue['risk']}: "
              f"{sum(r['accepted'] for r in issue['records'])}/{len(issue['records'])} checks "
              f"- {issue['requirement']}")
    print(f"gate: {document['garak_run']['gate_verdict']} "
          f"({document['garak_run']['requests_used']} requests, within budget: "
          f"{document['garak_run']['within_budget']})")
    print(f"VERDICT: {document['verdict'].upper()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
