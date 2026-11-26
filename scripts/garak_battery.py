#!/usr/bin/env python3
"""Profiled garak battery runner (program plan Phase 6, issue #47).

Runs a named probe battery (smoke | core | full) against one target through
the OpenAI shim, then distils the garak JSONL report into the structured
report format scripts/llm_gate.py consumes.

Design rules (from docs/llm-test-program-implementation.md Phase 6):
- probes are selected BY NAME from configs/garak_profiles.json, never by
  owasp:* tags (those follow the 2023 numbering)
- the runner enforces the request budget BEFORE any traffic: predicted
  requests (probe prompt counts x generations) must fit the profile ceiling
  - smoke 12, core 414, full 628 - or the run is refused
- requests_used counts actual attempt records (one provider call each),
  independent of how many detector scores they produced
- every observation identifies its own evidence (garak run uuid + probe);
  an unevaluated or partially-evaluated case is inconclusive, never pass

Traffic rules: the shim forwards to the target via drivers.transport
(Caido-proxied, paced). --conv-mode reuse enables shim multi-turn threads
(SEPP_SHIM_CONV=reuse) for multi-turn probes (tap/dra) - serial runs only.

Usage:
  garak_battery.py --profile smoke [--slug testbed] [--dry-run]
                   [--environment testbed-mock] [--conv-mode fresh|reuse]
                   [--port 8399] [--report-out PATH]

Exit codes: 0 battery report written (gate decides pass/fail separately),
1 preflight or run failure (nothing trustworthy recorded), 2 bad arguments.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "garak_profiles.json"
REPORTS = ROOT / "data" / "reports" / "garak"
GARAK_HOME = pathlib.Path(os.environ.get("SEPP_GARAK_HOME", ROOT / ".venv-garak"))
CEILINGS = {"smoke": 12, "core": 414, "full": 628}
# Potency ladder (AGENTS.md "Operator control plane") binds which battery
# profiles a given aggressiveness level may spend. The controller passes the
# workspace's active potency; an explicit over-bond run needs the operator
# order to raise potency FIRST (never silently).
POTENCY_PROFILES = {
    "stealth": {"smoke"},
    "standard": {"smoke", "core"},
    "aggressive": {"smoke", "core", "full"},
    "full": {"smoke", "core", "full"},
}
VALID_RISKS = {f"LLM{n:02d}" for n in range(1, 11)}
SHIM_HEALTH_TIMEOUT_S = 30


class BatteryError(RuntimeError):
    """Preflight or execution failure - nothing trustworthy was produced."""


# ------------------------------------------------------------ configuration

def load_profiles(path: pathlib.Path = CONFIG) -> dict:
    """Validate and flatten the profile config (inheritance resolved)."""
    raw = json.loads(path.read_text())
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise BatteryError(f"{path}: no profiles defined")
    flat = {}

    def resolve_probes(name: str, seen: frozenset) -> dict:
        if name in flat:
            return flat[name]["probes"]
        if name in seen:
            raise BatteryError(f"{name}: inheritance cycle")
        spec = profiles[name]
        if name not in CEILINGS:
            raise BatteryError(f"{name}: unknown profile (expected {sorted(CEILINGS)})")
        probes = dict(spec.get("probes") or {})
        parent = spec.get("inherits")
        if parent:
            if parent not in profiles:
                raise BatteryError(f"{name}: inherits unknown profile {parent!r}")
            probes = {**resolve_probes(parent, seen | {name}), **probes}
        return probes

    for name in sorted(profiles):
        spec = profiles[name]
        probes = resolve_probes(name, frozenset())
        if not probes:
            raise BatteryError(f"{name}: empty probe set")
        for fq, meta in probes.items():
            if not isinstance(meta, dict) or meta.get("risk") not in VALID_RISKS \
                    or not str(meta.get("workflow", "")).strip():
                raise BatteryError(f"{name}/{fq}: risk must be LLM01..LLM10 and workflow required")
            parts = fq.split(".")
            if len(parts) != 2 or not all(parts):
                raise BatteryError(f"{name}/{fq}: probe must be 'module.Class'")
        declared = spec.get("predicted_requests")
        if not isinstance(declared, int) or declared <= 0:
            raise BatteryError(f"{name}: predicted_requests missing")
        flat[name] = {
            "description": spec.get("description", ""),
            "generations": int(spec.get("generations", 1)),
            "predicted_requests": declared,
            "probes": probes,
            "ceiling": CEILINGS[name],
        }
    return flat


# ------------------------------------------------------------- preflight

def count_prompts(garak_home: pathlib.Path, probe_names: list[str]) -> dict[str, int]:
    """Ask the garak installation for each probe's prompt count (offline)."""
    program = (
        "import importlib, json, sys, warnings\n"
        "warnings.filterwarnings('ignore')\n"
        "out = {}\n"
        "for fq in sys.argv[1:]:\n"
        "    mod, cls = fq.rsplit('.', 1)\n"
        "    p = getattr(importlib.import_module('garak.probes.' + mod), cls)()\n"
        "    out[fq] = len(p.prompts)\n"
        "print(json.dumps(out))\n"
    )
    proc = subprocess.run(
        [str(garak_home / "bin" / "python3"), "-c", program, *probe_names],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode:
        raise BatteryError(f"prompt census failed: {proc.stderr.strip()[-400:]}")
    try:
        return {k: int(v) for k, v in json.loads(proc.stdout.strip().splitlines()[-1]).items()}
    except (ValueError, IndexError) as exc:
        raise BatteryError(f"prompt census unreadable: {exc}") from exc


def preflight(profile_name: str, profiles: dict, garak_home: pathlib.Path) -> int:
    """Budget enforcement BEFORE any traffic. Returns predicted request count."""
    spec = profiles[profile_name]
    census = count_prompts(garak_home, sorted(spec["probes"]))
    predicted = sum(n * spec["generations"] for n in census.values())
    if predicted > spec["ceiling"]:
        raise BatteryError(
            f"profile {profile_name}: predicted {predicted} requests exceed "
            f"ceiling {spec['ceiling']} (config claims {spec['predicted_requests']})")
    drift = abs(predicted - spec["predicted_requests"])
    if drift:
        print(f"# note: predicted_requests drifted by {drift} "
              f"(config {spec['predicted_requests']}, census {predicted})")
    return predicted


# ------------------------------------------------------------- shim + garak

def start_shim(slug: str, port: int, conv_mode: str) -> tuple[subprocess.Popen, dict]:
    """Spawn the OpenAI shim; returns (process, health-identity)."""
    env = {**os.environ,
           "CAIDO_TARGET": slug,
           "SEPP_SHIM_CONV": conv_mode,
           "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "local-shim-no-key-needed")}
    proc = subprocess.Popen(
        [str(GARAK_HOME / "bin" / "uvicorn"), "drivers.openai_shim:app",
         "--port", str(port), "--app-dir", str(ROOT)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + SHIM_HEALTH_TIMEOUT_S
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.time() < deadline:
        if proc.poll() is not None:
            raise BatteryError(f"shim exited early (rc={proc.returncode})")
        try:
            with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
                health = json.loads(resp.read())
            if health.get("ok") and health.get("target") == slug:
                return proc, health
        except (OSError, ValueError):
            pass
        time.sleep(0.4)
    proc.terminate()
    raise BatteryError(f"shim not healthy on :{port} within {SHIM_HEALTH_TIMEOUT_S}s")


def run_garak(profile: dict, model: str, base_url: str, port: int) -> pathlib.Path:
    """Launch garak; returns the JSONL report path it announces."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    argv = [str(GARAK_HOME / "bin" / "garak"),
            "--model_type", "openai", "-n", model,
            "--generations", str(profile["generations"]),
            "-p", ",".join(sorted(profile["probes"]))]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=7200,
                          env={**os.environ,
                               "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "local-shim-no-key-needed"),
                               "OPENAI_BASE_URL": base_url},
                          cwd=str(ROOT))
    for line in reversed(proc.stdout.splitlines()):
        if "reporting to" in line and line.rstrip().endswith(".jsonl"):
            return pathlib.Path(line.split("reporting to", 1)[1].strip().rstrip(") "))
    tail = (proc.stderr or proc.stdout or "?").strip().splitlines()
    raise BatteryError(f"garak produced no report path; tail: {tail[-3:]}")


# ------------------------------------------------------------- summarising

def summarize_report(report_path: pathlib.Path, profile_name: str, profile: dict,
                     identity: dict) -> dict:
    """Distil the garak JSONL into the llm_gate report format."""
    import verify_garak_report
    verify_garak_report.verify(report_path)  # raises when incomplete

    evals: dict[str, dict] = {}
    attempt_ids: set[str] = set()
    requests_used = 0
    run_uuid = None
    garak_version = None
    for line in report_path.read_text().splitlines():
        rec = json.loads(line)
        kind = rec.get("entry_type")
        if kind == "attempt":
            # garak re-emits an attempt per state transition (issued ->
            # responded) with the same uuid; count DISTINCT attempts = one
            # provider request each, independent of detector scores
            key = rec.get("uuid") or f"{rec.get('probe_classname')}:{rec.get('seq')}"
            if key not in attempt_ids:
                attempt_ids.add(key)
                requests_used += 1
        elif kind == "init":
            run_uuid = rec.get("run")
            garak_version = rec.get("garak_version")
        elif kind == "eval":
            evals.setdefault(rec["probe"], []).append(rec)

    observations = []
    for fq in sorted(profile["probes"]):
        meta = profile["probes"][fq]
        case = fq.split(".", 1)[1]
        evs = evals.get(fq)
        checks = {}
        reasons = []
        if not evs:
            result, reason = "inconclusive", "no detector evaluations recorded"
            reasons = [reason]
        else:
            unevaluated = []
            for ev in evs:
                detector = ev["detector"].rsplit(".", 1)[-1]
                ok = ev.get("fails", 0) == 0 and ev.get("nones", 0) == 0 \
                    and ev.get("passed", 0) == ev.get("total_evaluated", 0)
                checks[detector] = bool(ok)
                if ev.get("total_evaluated", 0) == 0 or ev.get("nones", 0) > 0:
                    unevaluated.append(detector)  # undecided: evidence incomplete
            attacked = [d for d, ok in checks.items() if not ok]
            if unevaluated:
                # conservative: a partially-evaluated case can never pass
                result = "inconclusive"
                reasons.append("unevaluated detectors: " + ", ".join(unevaluated))
            elif attacked:
                result = "fail"  # attack visible: vulnerability demonstration
                reasons.append("attack success in: " + ", ".join(attacked))
            elif checks:
                result = "pass"
            else:
                result, reasons = "inconclusive", ["no detector produced a verdict"]
        if not reasons:
            reasons.append("all detectors held")
        observations.append({
            "case": case,
            "risk": meta["risk"],
            "workflow": meta["workflow"],
            "result": result,
            "evidence_id": f"{run_uuid}:{fq}" if run_uuid else None,
            "reason": "; ".join(reasons),
            "checks": checks,
        })

    unknown = sorted(set(evals) - set(profile["probes"]))
    for fq in unknown:
        print(f"# warning: report contains unconfigured probe {fq}")

    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "environment": identity.get("environment", ""),
        "backend": identity.get("backend", ""),
        "model": identity.get("model", ""),
        "profile": profile_name,
        "garak_version": garak_version,
        "garak_run": run_uuid,
        "requests_used": requests_used,
        "requests_predicted": profile["predicted_requests"],
        "ceiling": profile["ceiling"],
        "garak_report": str(report_path),
        "observations": observations,
    }


# --------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", dest="list_profiles",
                    help="show profiles (predicted/ceiling/probes) + potency binding, exit")
    ap.add_argument("--profile", choices=sorted(CEILINGS),
                    help="battery to run (see --list; required unless --list)")
    ap.add_argument("--slug", default=os.environ.get("CAIDO_TARGET", "testbed"))
    ap.add_argument("--environment", default="testbed-mock",
                    help="environment label recorded for the gate (must match the policy)")
    ap.add_argument("--conv-mode", default="fresh", choices=("fresh", "reuse"),
                    help="shim conversation policy; reuse is SERIAL-RUNS-ONLY")
    ap.add_argument("--port", type=int, default=8399)
    ap.add_argument("--dry-run", action="store_true",
                    help="preflight only: budget census + adapter check, zero traffic")
    ap.add_argument("--report-out", type=pathlib.Path, default=None)
    ap.add_argument("--potency", default="standard", choices=sorted(POTENCY_PROFILES),
                    help="workspace potency bond (AGENTS.md ladder); restricts which "
                         "profiles may run: stealth=smoke, standard=smoke+core, "
                         "aggressive/full=all")
    args = ap.parse_args()

    if args.list_profiles:
        try:
            preds = {n: spec["predicted_requests"] for n, spec in load_profiles().items()}
        except BatteryError:
            preds = {}
        print("profiles (predicted / ceiling / allowed potency):")
        for name in sorted(CEILINGS):
            cov = ", ".join(sorted(l for l, ps in POTENCY_PROFILES.items() if name in ps))
            print(f"  {name:<7} {preds.get(name, '?'):>4} / {CEILINGS[name]:<4} ({cov})")
        print("pick with --profile; `scripts/capabilities.py` has the full sheet "
              "(probes, commands, make targets)")
        return 0

    if not args.profile:
        ap.error("--profile is required (or use --list)")
    if args.profile not in POTENCY_PROFILES[args.potency]:
        print(f"potency '{args.potency}' does not cover profile '{args.profile}' "
              f"(allowed: {sorted(POTENCY_PROFILES[args.potency])}); "
              f"raise potency first or lower the profile", file=sys.stderr)
        return 2
    profiles = load_profiles()
    profile = profiles[args.profile]
    print(f"== battery {args.profile}: {profile['description']}")
    predicted = preflight(args.profile, profiles, GARAK_HOME)
    print(f"== preflight OK: {len(profile['probes'])} probes, {predicted} requests "
          f"(ceiling {profile['ceiling']})")

    shim = None
    try:
        if args.dry_run:
            print("== dry run: stopping before traffic")
            return 0
        shim, health = start_shim(args.slug, args.port, args.conv_mode)
        model = health["model"]
        print(f"== shim healthy: target={health['target']} model={model} "
              f"conv={args.conv_mode}")
        report_path = run_garak(profile, model,
                                f"http://127.0.0.1:{args.port}/v1", args.port)
        identity = {"environment": args.environment,
                    "backend": f"shim:{args.slug}",
                    "model": model}
        report = summarize_report(report_path, args.profile, profile, identity)
        out = args.report_out or REPORTS / f"battery-{args.profile}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"== battery report: {out}")
        for obs in report["observations"]:
            print(f"  [{obs['result']:>12}] {obs['risk']} {obs['case']}: {obs['reason']}")
        within = report["requests_used"] <= report["ceiling"]
        print(f"== requests used {report['requests_used']} / ceiling {report['ceiling']} "
              f"({'within budget' if within else 'OVER BUDGET'}); "
              f"feed the report to scripts/llm_gate.py with a policy")
        return 0
    except BatteryError as exc:
        print(f"battery failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if shim is not None:
            shim.terminate()


if __name__ == "__main__":
    sys.exit(main())
