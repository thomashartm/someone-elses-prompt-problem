"""Evaluate application assertions on completed, individually identified Caido exports."""
import hashlib
import json
from pathlib import Path
import re
import sys

if __package__:
    from .llm_job_harness import MAX_SAVED_OUTPUT, read_response
else:
    from llm_job_harness import MAX_SAVED_OUTPUT, read_response


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def check_rule(rule):
    if not isinstance(rule, dict) or not rule or set(rule) - {"status", "contains", "absent", "json_fields"}:
        raise ValueError("expected status, contains, absent, or json_fields assertions")
    if "status" in rule:
        codes = rule["status"] if isinstance(rule["status"], list) else [rule["status"]]
        if not codes or any(type(code) is not int or not 100 <= code <= 599 for code in codes):
            raise ValueError("invalid expected HTTP status")
    for key in ("contains", "absent"):
        if key in rule and (not isinstance(rule[key], list) or not rule[key] or not all(map(nonempty, rule[key]))):
            raise ValueError(f"{key} needs a nonempty list of literal strings")
    if "json_fields" in rule and (not isinstance(rule["json_fields"], dict) or not rule["json_fields"]):
        raise ValueError("json_fields needs named expected fields")


def assert_response(response, rule):
    body = response["body"]
    checks = {}
    if "status" in rule:
        codes = rule["status"] if isinstance(rule["status"], list) else [rule["status"]]
        checks["status"] = int(response["code"]) in codes
    if "contains" in rule:
        checks["contains"] = all(s.encode() in body for s in rule["contains"])
    if "absent" in rule:
        checks["absent"] = all(s.encode() not in body for s in rule["absent"])
    if "json_fields" in rule:
        try:
            obj = json.loads(body)
        except (ValueError, UnicodeError):
            obj = None
        checks["json_fields"] = isinstance(obj, dict) and all(
            key in obj and json.dumps(obj[key], sort_keys=True) == json.dumps(value, sort_keys=True)
            for key, value in rule["json_fields"].items()
        )
    return checks


def assess(policy, index, directory):
    cases = policy["cases"]
    if not isinstance(cases, dict) or not cases:
        raise ValueError("policy must contain named cases")
    if index["environment"] != policy["environment"]:
        raise ValueError("capture environment differs from policy")
    for field in ("environment", "backend", "model"):
        if not nonempty(index[field]):
            raise ValueError(f"missing {field}")
    records = index["records"]
    if not isinstance(records, dict) or records.keys() - cases.keys():
        raise ValueError("unexpected capture cases")
    budget = policy.get("body_limit", 200_000)
    if type(budget) is not int or not 0 < budget <= MAX_SAVED_OUTPUT:
        raise ValueError("invalid body_limit")
    count = index["requests_used"]
    if type(count) is not int or count < 0:
        raise ValueError("requests_used must be a nonnegative measured count")

    seen_ids, seen_paths = set(), set()
    observations = []
    for name, spec in cases.items():
        if not re.fullmatch(r"LLM(?:0[1-9]|10):2026", spec["risk"]) or not nonempty(spec["workflow"]):
            raise ValueError(f"{name}: missing risk edition or workflow")
        check_rule(spec["expect"])
        completion = None
        if "completion_fields" in spec:
            completion = {"json_fields": spec["completion_fields"]}
            check_rule(completion)
        obs = {"case": name, "risk": spec["risk"], "workflow": spec["workflow"],
               "result": "inconclusive", "evidence_id": None}
        observations.append(obs)
        record = records.get(name)
        if record is None:
            obs["reason"] = "missing capture"
            continue
        if not isinstance(record, dict):
            raise ValueError(f"{name}: invalid capture record")
        if nonempty(record.get("inapplicable")) and set(record) == {"inapplicable"}:
            obs.update(result="inapplicable", reason=record["inapplicable"])
            continue
        if not nonempty(record.get("request_id")) or not nonempty(record.get("file")):
            obs["reason"] = "missing request ID or export"
            continue
        evidence_id = record["request_id"]
        path = (directory / record["file"]).resolve()
        if not path.is_relative_to(directory) or path == directory:
            raise ValueError(f"{name}: export must be inside capture directory")
        if evidence_id in seen_ids or path in seen_paths:
            raise ValueError(f"{name}: duplicate request ID or export path")
        seen_ids.add(evidence_id)
        seen_paths.add(path)
        obs["evidence_id"] = evidence_id
        if not path.is_file():
            obs["reason"] = "missing export file"
            continue
        with path.open("rb") as stream:
            raw = stream.read(MAX_SAVED_OUTPUT + 1)
        if len(raw) > MAX_SAVED_OUTPUT:
            obs["reason"] = "export exceeds inspection limit"
            continue
        obs["sha256"] = hashlib.sha256(raw).hexdigest()
        response = read_response(raw)
        if response["evidence"] != "complete":
            obs["reason"] = response["reason"]
            continue
        if response["body_bytes"] > budget:
            obs["reason"] = "response exceeds policy body limit"
            continue
        if completion and not assert_response(response, completion)["json_fields"]:
            obs["reason"] = "capture does not identify the expected completed job"
            continue
        checks = assert_response(response, spec["expect"])
        obs.update(result="pass" if all(checks.values()) else "fail", checks=checks,
                   http_status=int(response["code"]), reason=", ".join(k for k in checks if not checks[k]) or "all assertions met")
    if count < len(seen_ids):
        raise ValueError("request count is smaller than the number of distinct requests")
    return {k: index[k] for k in ("environment", "backend", "model", "requests_used")} | {"observations": observations}


def command(args):
    try:
        report = assess(json.loads(args.policy.read_text()), json.loads(args.index.read_text()), args.index.resolve().parent)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f"Invalid assessment input: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    results = [row["result"] for row in report["observations"]]
    if "inconclusive" in results or all(r == "inapplicable" for r in results):
        return 1
    return 2 if "fail" in results else 0
