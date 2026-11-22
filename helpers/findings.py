#!/usr/bin/env python3
"""Findings ledger for the Caido testing agent.

Tries to persist findings into Caido (GraphQL `createFinding`, requires a request ID). If Caido auth
is unavailable or the mutation shape differs, falls back to a local JSON
ledger at data/reports/findings.json. Either way `create` returns the finding ID.

Usage:
  findings.py list [--host HOST]
  findings.py create --title T --severity crit|high|med|low --status proven|probable|hunch \
                     (--request-id ID | --note TEXT) [--impact TEXT] [--poc-file FILE]
  findings.py show <finding-id>
  findings.py introspect            # dump what Caido actually accepts
"""
import argparse
import json
import os
import pathlib
import uuid
import sys
import time
import urllib.request

CAIDO_URL = os.environ.get("CAIDO_URL", "http://127.0.0.1:8080").rstrip("/")
ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "reports" / "findings.json"
TOKEN_DIR = pathlib.Path.home() / ".caido-mcp" / "tokens"


def _load_token():
    env = os.environ.get("CAIDO_ACCESS_TOKEN")
    if env:
        return env
    if TOKEN_DIR.is_dir():
        candidates = sorted(TOKEN_DIR.glob("*.json"))
        if CAIDO_URL.startswith("http://") or CAIDO_URL.startswith("https://"):
            want = CAIDO_URL.split("//", 1)[1].rstrip("/")
            prefer = [p for p in candidates if want in p.name]
            candidates = prefer or candidates
        for path in candidates:
            try:
                tok = json.loads(path.read_text()).get("accessToken")
                if tok:
                    return tok
            except Exception:
                continue
    # legacy fallback
    legacy = pathlib.Path.home() / ".caido-mcp" / "token.json"
    if legacy.exists():
        try:
            return json.loads(legacy.read_text()).get("access_token")
        except Exception:
            return None
    return None


def gql(query, variables=None):
    """POST to Caido GraphQL. Returns parsed JSON or raises RuntimeError."""
    token = _load_token()
    body = {"query": query, "variables": variables or {}}
    req = urllib.request.Request(
        CAIDO_URL + "/graphql",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except Exception as exc:  # noqa: BLE001 - degraded mode is a feature here
        raise RuntimeError("caido unreachable/unauthorized: %s" % exc)


def _read_ledger():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return []


def _write_ledger(items):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(items, indent=2))


SEV_RANK = {"crit": 0, "critical": 0, "high": 1, "med": 2, "medium": 2, "low": 3}


def cmd_introspect(_args):
    """Dump what finding mutations Caido currently accepts."""
    out = gql("query { __schema { mutationType { fields { name } } } }")
    fields = [f["name"] for f in out["data"]["__schema"]["mutationType"]["fields"]]
    hits = [f for f in fields if "finding" in f.lower()]
    print(json.dumps({"mutations_matching_finding": hits}, indent=2))


def cmd_create(args):
    fid = "L-%s-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), args.severity.lower(),
                          uuid.uuid4().hex[:4])  # second-resolution stamp collided when batch-recording (row 35 fix)
    finding = {
        "id": fid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "title": args.title,
        "severity": SEV_RANK.get(args.severity.lower(), 99),
        "status": args.status,
        "request_id": args.request_id or "",
        "impact": args.impact or "",
        "note": args.note or "",
        "poc_file": args.poc_file or "",
        "pushed_to_caido": False,
    }

    # Push into Caido; degrade to the local ledger if the push fails.
    try:
        poc_text = ""
        if args.poc_file and pathlib.Path(args.poc_file).exists():
            poc_text = pathlib.Path(args.poc_file).read_text()[:8000]
        description = "\n\n".join(x for x in [
            "Impact: " + (args.impact or ""),
            "Note: " + (args.note or ""),
            "Local id: " + fid,
            poc_text,
        ] if x.strip())
        inp = {
            "title": "[{}] {}".format(args.severity.upper(), args.title),
            "description": description,
            "reporter": "pi-agent",
            "dedupeKey": fid,
        }
        mut = (
            "mutation($requestId:ID!,$input:CreateFindingInput!){ "
            "createFinding(requestId:$requestId,input:$input){ "
            "finding{ id } error{ __typename } } }"
        )
        result = gql(mut, {"requestId": args.request_id, "input": inp})
        if result.get("errors"):
            raise RuntimeError(json.dumps(result["errors"])[:300])
        payload_res = result["data"]["createFinding"]
        if payload_res.get("error"):
            raise RuntimeError(str(payload_res["error"])[:300])
        finding["pushed_to_caido"] = True
        finding["caido_id"] = payload_res["finding"]["id"]
    except Exception as exc:  # noqa: BLE001
        finding["push_error"] = str(exc)[:300]

    items = _read_ledger()
    items.append(finding)
    _write_ledger(items)
    dest = "caido+local" if finding["pushed_to_caido"] else "LOCAL ONLY (push failed)"
    print("recorded %s -> %s [%s]" % (fid, dest, args.status))
    return 0


def cmd_list(args):
    items = sorted(_read_ledger(), key=lambda f: (f["severity"], f["created_at"]))
    if args.host:
        items = [f for f in items if args.host in (f.get("title", "") + f.get("impact", ""))]
    if not items:
        print("(empty ledger)")
        return 0
    for f in items:
        print("[%s|%s] %-38s %s" % (
            {0: "crit", 1: "high", 2: "med", 3: "low"}.get(f["severity"], "?"),
            f["status"], f["id"], f["title"]))
    return 0


def cmd_show(args):
    for f in _read_ledger():
        if f["id"] == args.id:
            print(json.dumps(f, indent=2))
            return 0
    print("no finding %s" % args.id, file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--host", default=""); s.set_defaults(func=cmd_list)
    s = sub.add_parser("introspect"); s.set_defaults(func=cmd_introspect)
    s = sub.add_parser("create")
    s.add_argument("--title", required=True)
    s.add_argument("--severity", required=True, choices=["crit", "high", "med", "low"])
    s.add_argument("--status", required=True, choices=["proven", "probable", "hunch"])
    s.add_argument("--request-id", required=True,
                   help="Caido request ID the finding is anchored to (mandatory)")
    s.add_argument("--note", default="", help="proof narrative / diff explanation")
    s.add_argument("--impact", default="")
    s.add_argument("--poc-file", default="")
    s.set_defaults(func=cmd_create)
    s = sub.add_parser("show"); s.add_argument("id"); s.set_defaults(func=cmd_show)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
