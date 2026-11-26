#!/usr/bin/env python3
"""Hunt-session workspace manager.

One workspace per host per day by default (decision #4); passing --hunt
isolates ONE hunt in its own workspace (<date>-<host>--<hunt>) so parallel
hunts against the same host never share evidence. Idempotent - /sepp can
call `ensure` repeatedly.

Lifecycle (operator-ordered, agent-executed):
    ensure --host H --url U [--hunt NAME] [--potency LEVEL]  -> create/update
    finish SID                                               -> mark closed
    prune --days N [--finished] [--id SID] [--host HOST] [--apply] -> cleanup
                                                                  (dry-run by default)

Layout:
    <root>/<YYYYMMDD>-<hostslug>[-  -<huntslug>]/
        session.json      # targets[], potency, credentials pointer, status
        scripts/README.md # agent-maintained index of hunt scripts
        tools/  evidence/  reports/  tmp/

Root: $SEPP_WORKSPACES_DIR (default <repo>/data/workspaces). Never committed.
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent / "data" / "workspaces"


def root() -> pathlib.Path:
    r = pathlib.Path(os.environ.get("SEPP_WORKSPACES_DIR", str(DEFAULT_ROOT)))
    r.mkdir(parents=True, exist_ok=True)
    return r


def hostslug(host: str) -> str:
    slug = re.sub(r"[^a-z0-9.-]+", "-", host.lower()).strip("-")
    return slug or "unknown-host"


def session_id_for(host: str, hunt: str | None = None) -> str:
    base = "%s-%s" % (time.strftime("%Y%m%d"), hostslug(host))
    if hunt:
        # decision #4 amendment: each hunt gets its OWN workspace under the
        # same host+day namespace - parallel hunts never share evidence dirs
        return "%s--%s" % (base, huntslug(hunt))
    return base


def huntslug(hunt: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", hunt.lower()).strip("-")[:40]
    return slug or "unnamed-hunt"


# Potency ladder (normative in AGENTS.md): bounds probe aggressiveness and
# pacing. Stored per workspace so every hunter leg inherits the same bond.
POTENCY_LEVELS = ("stealth", "standard", "aggressive", "full")


def new_session(sid: str, url: str, context: str, cred_pointer: str) -> dict:
    return {
        "id": sid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "active",
        "credentials_pointer": cred_pointer,
        "targets": [{
            "url": url,
            "context": context,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }],
    }


SCRIPTS_README = """# Hunt scripts - {sid}

Scripts the agent (or operator) wrote FOR THIS SESSION. Every script must:
- be executable (chmod +x) and support --help,
- be listed here with a one-liner on what it does and how to run it,
- write outputs to ../evidence/ (captures/diffs) or ../reports/.

Global/shared tooling lives in the repo (helpers/, drivers/, scripts/) and is
NOT duplicated here.
"""


def cmd_ensure(ap_args) -> int:
    if ap_args.hunt:
        sid = session_id_for(ap_args.host, ap_args.hunt)
    else:
        sid = session_id_for(ap_args.host)
    if ap_args.potency and ap_args.potency not in POTENCY_LEVELS:
        print(json.dumps({"error": "potency must be one of %s" % (POTENCY_LEVELS,)}))
        return 1
    ws = root() / sid
    for sub in ("scripts", "tools", "evidence", "reports", "tmp"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    readme = ws / "scripts" / "README.md"
    if not readme.exists():
        readme.write_text(SCRIPTS_README.format(sid=sid))

    sj = ws / "session.json"
    created = not sj.exists()
    if created:
        sess = new_session(sid, ap_args.url, ap_args.context or "", ap_args.cred_pointer or "")
        if ap_args.potency:
            sess["potency"] = ap_args.potency
    else:
        sess = json.loads(sj.read_text())
        if ap_args.url and not any(t["url"] == ap_args.url for t in sess.get("targets", [])):
            sess.setdefault("targets", []).append({
                "url": ap_args.url,
                "context": ap_args.context or "",
                "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        sess["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if ap_args.cred_pointer:
            sess["credentials_pointer"] = ap_args.cred_pointer
        if ap_args.potency:
            sess["potency"] = ap_args.potency  # mid-hunt potency change: operator order
    sj.write_text(json.dumps(sess, indent=2) + "\n")
    print(json.dumps({"id": sid, "path": str(ws), "created": created,
                      "targets": len(sess["targets"]),
                      "potency": sess.get("potency", "standard")}))
    return 0


def cmd_list(_ap_args) -> int:
    out = []
    for d in sorted(root().iterdir()):
        if not d.is_dir():
            continue
        sj = d / "session.json"
        sess = {}
        if sj.exists():
            try:
                sess = json.loads(sj.read_text())
            except Exception:
                sess = {"error": "unreadable session.json"}
        size_kb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) // 1024
        out.append({
            "id": d.name,
            "path": str(d),
            "status": sess.get("status", "?"),
            "targets": [t.get("url") for t in sess.get("targets", [])],
            "updated_at": sess.get("updated_at", ""),
            "size_kb": size_kb,
        })
    print(json.dumps(out, indent=2))
    return 0


def cmd_finish(ap_args) -> int:
    """Mark a workspace closed (operator stop order). Prune --finished then
    selects it for cleanup; nothing is deleted here."""
    ws = root() / ap_args.sid
    sj = ws / "session.json"
    if not sj.is_file():
        print(json.dumps({"error": "no workspace %s" % ap_args.sid}))
        return 1
    sess = json.loads(sj.read_text())
    sess["status"] = "closed"
    sess["closed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    sess["updated_at"] = sess["closed_at"]
    sj.write_text(json.dumps(sess, indent=2) + "\n")
    print(json.dumps({"id": ap_args.sid, "status": "closed"}))
    return 0


def cmd_prune(ap_args) -> int:
    cutoff = time.time() - ap_args.days * 86400
    candidates = []
    for d in sorted(root().iterdir()):
        if not d.is_dir():
            continue
        if ap_args.id and d.name != ap_args.id:
            continue
        if ap_args.host and not d.name.startswith("%s-" % time.strftime("%Y%m%d")) \
                and ap_args.host not in d.name:
            continue
        sess = {}
        sj = d / "session.json"
        if sj.exists():
            try:
                sess = json.loads(sj.read_text())
            except Exception:
                sess = {}
        if ap_args.finished and sess.get("status") != "closed":
            continue
        m = re.match(r"^(\d{8})-", d.name)
        stamp = time.mktime(time.strptime(m.group(1), "%Y%m%d")) if m else d.stat().st_mtime
        if stamp < cutoff:
            candidates.append(d)
    removed = []
    if ap_args.apply:
        for d in candidates:
            shutil.rmtree(d)
            removed.append(d.name)
    print(json.dumps({"days": ap_args.days, "finished_only": ap_args.finished,
                      "selector": ap_args.id or ap_args.host or "all",
                      "candidates": [c.name for c in candidates],
                      "removed": removed, "applied": ap_args.apply}))
    return 0


def cmd_path(ap_args) -> int:
    ws = root() / ap_args.sid
    if not ws.is_dir():
        print(json.dumps({"error": "no workspace %s" % ap_args.sid}))
        return 1
    print(str(ws))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ensure")
    s.add_argument("--host", required=True)
    s.add_argument("--url", required=True)
    s.add_argument("--context", default="")
    s.add_argument("--cred-pointer", default="")
    s.add_argument("--hunt", default=None,
                   help="give this hunt its OWN workspace: <date>-<host>--<hunt>")
    s.add_argument("--potency", default=None, choices=POTENCY_LEVELS,
                   help="bond this workspace to a potency level (AGENTS.md ladder); "
                        "hunters inherit it; re-issuing mid-hunt changes it")
    s.set_defaults(func=cmd_ensure)
    s = sub.add_parser("list"); s.set_defaults(func=cmd_list)
    s = sub.add_parser("finish")
    s.add_argument("sid", help="workspace id to mark closed (cleanup selector)")
    s.set_defaults(func=cmd_finish)
    s = sub.add_parser("prune")
    s.add_argument("--days", type=int, required=True)
    s.add_argument("--id", default=None, help="only this workspace id")
    s.add_argument("--host", default=None, help="only workspaces whose name contains this host slug")
    s.add_argument("--finished", action="store_true",
                   help="only workspaces marked closed via `finish`")
    s.add_argument("--apply", action="store_true", help="actually delete (default: report only)")
    s.set_defaults(func=cmd_prune)
    s = sub.add_parser("path"); s.add_argument("sid"); s.set_defaults(func=cmd_path)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
