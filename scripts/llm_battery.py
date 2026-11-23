#!/usr/bin/env python3
"""Run bounded local control fixtures and retain the evidence for each check."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from garak_summarize import summarize
from llm_gate import PROFILES, decide

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ("artifact-approved", "artifact-unapproved")
CORE = SMOKE + (
    "feedback-review", "citation-supported", "citation-unsupported", "render-plain", "render-markup",
    "summary-baseline", "customer-redaction", "calculator-dispatch", "quota-rejection",
    "chat-context", "tenant-retrieval",
)
EXAMPLE = ROOT / "testbed/controls/saved-example"


def execute(argv, destination, env=None):
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, env=env, timeout=180)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(proc.stdout)
    if proc.stderr:
        with destination.with_suffix(".log").open("x", encoding="utf-8") as stream:
            stream.write(proc.stderr)
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise ValueError(f"{destination.name}: invalid or missing output (exit {proc.returncode})") from exc
    return data, proc.returncode


def save(path, obj):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, indent=2)
        stream.write("\n")


def run(args):
    out = args.output.resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    limit = min(args.max_requests, PROFILES[args.profile]) if args.max_requests is not None else PROFILES[args.profile]
    sections = []
    report = {"profile": args.profile, "request_limit": limit, "requests_used": None,
              "scope": "local verification", "request_unit": "in-process application HTTP requests issued by this run",
              "sections": sections, "verdict": "blocked"}

    def section(name, operation):
        try:
            evidence, ok = operation()
            sections.append({"check": name, "evidence": evidence, "verdict": "pass" if ok else "blocked"})
        except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            sections.append({"check": name, "verdict": "blocked", "reason": str(exc)})

    def readiness():
        name = "readiness.json"
        data, rc = execute([sys.executable, "scripts/onboard_target.py", "--targets-dir",
                           "testbed/controls/conversation_targets", "check", "notes-fixture"], out / name)
        return name, rc == 0 and data["status"] == "fixture-verified" and len(data["observations"]) == 2

    def controls():
        fixtures = json.loads((ROOT / "payloads/fixtures/testbed_controls.json").read_text())
        required = SMOKE if args.profile == "smoke" else CORE
        if args.profile != "smoke" and {fixtures[k]["risk"] for k in required} != {f"LLM{i:02}:2026" for i in range(1, 11)}:
            raise ValueError("core control fixtures must account for each OWASP 2026 risk")
        argv = [sys.executable, "testbed/controls/run_fixture_controls.py", "--max-requests", str(limit)]
        if args.profile == "smoke":
            for case in required:
                argv.extend(["--case", case])
        observed, rc = execute(argv, out / "controls.json")
        cases = {case: {"risk": fixtures[case]["risk"], "workflow": fixtures[case]["workflow"],
                        "allow_inapplicable": case == "feedback-review"} for case in required}
        policy = {"environment": "testbed-mock", "mode": "calibration", "backend": "mock",
                  "model": "deterministic", "cases": cases}
        gate = decide(policy, observed, args.profile)
        save(out / "control-policy.json", policy)
        save(out / "control-gate.json", gate)
        report["requests_used"] = gate["requests_used"]
        return "control-gate.json", not rc and gate["verdict"] == "pass" and 0 < gate["requests_used"] <= limit

    def conversations():
        name = "conversations.json"
        data, rc = execute([sys.executable, "testbed/controls/conversation_fixture.py"], out / name)
        return name, not rc and data["status"] == "passed" and bool(data["observations"])

    def saved():
        policy_path = EXAMPLE / "policy.json"
        observation, rc = execute([sys.executable, "helpers/llm_job_harness.py", "assess", policy_path, EXAMPLE / "index.json"],
                                  out / "saved-assertions.json")
        gate = decide(json.loads(policy_path.read_text()), observation, args.profile)
        save(out / "saved-gate.json", gate)
        return "saved-gate.json", not rc and gate["verdict"] == "pass"

    def garak():
        prefix = out / "local-plumbing"
        argv = [args.garak_python, "-m", "garak", "--model_type", "test.Single", "--generations", "1",
                "-p", "test.Test", "--probe_options", '{"test":{"Test":{"prompts":["Reply with OK."]}}}',
                "--report_prefix", str(prefix)]
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        with (out / "garak.log").open("x") as log:
            subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=180, check=True)
        summary = summarize(prefix.with_suffix(".report.jsonl"))
        save(out / "garak-summary.json", summary)
        return "garak-summary.json", summary["distinct_attempts"] == 1 and all(r["hits"] == 0 for r in summary["evaluations"])

    def local_model():
        if args.local_summary is None:
            raise ValueError("full profile requires --local-summary with an authenticated local-model receipt")
        receipt = json.loads(args.local_summary.read_text())
        provider = receipt["provider"]
        ok = (provider["backend"] == "ollama" and provider["model"] == "qwen2.5:7b" and
              provider["finish_reason"] == "stop" and bool(provider["completion_id"]) and
              receipt["workflow"] == "authenticated note summary" and bool(receipt["summary"].strip()) and
              receipt["target"] == "http://127.0.0.1:8766" and receipt["user"] == "alice@acme.test" and receipt["note_id"] == 1 and
              all(term in receipt["summary"].lower() for term in ("reporting module", "sso")))
        save(out / "local-model.json", {key: receipt[key] for key in ("target", "recorded_at", "user", "workflow", "note_id", "id", "summary", "provider") if key in receipt})
        return "local-model.json", ok

    section("offline target readiness", readiness)
    section("strict application fixtures", controls)
    if args.profile != "smoke":
        section("isolated mock conversations", conversations)
        section("saved evidence assertions", saved)
    if args.profile == "full":
        section("offline garak plumbing", garak)
        section("recorded local-model summary", local_model)
    if all(s["verdict"] == "pass" for s in sections):
        report["verdict"] = "pass"
    save(out / "report.json", report)
    print(out / "report.json")
    return int(report["verdict"] != "pass")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="core")
    parser.add_argument("--output", type=Path, default=ROOT / "data/reports" / f"controls-{uuid4().hex}")
    parser.add_argument("--max-requests", type=int, help="Lower the profile's local HTTP request ceiling")
    parser.add_argument("--garak-python", default=os.getenv("GARAK_PYTHON", sys.executable))
    parser.add_argument("--local-summary", type=Path)
    args = parser.parse_args()
    if args.max_requests is not None and args.max_requests < 0:
        parser.error("--max-requests must be nonnegative")
    try:
        sys.exit(run(args))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Cannot run battery: {exc}\n")
