#!/usr/bin/env python3
"""Hunt-session workspace manager.

One workspace per host per day (decision #4): multiple targets on the same
host accumulate in the same day's workspace. Idempotent - /sepp can call
`ensure` repeatedly.

Layout:
    <root>/<YYYYMMDD>-<hostslug>/
        session.json      # targets[], credentials pointer, timestamps, status
        scripts/README.md # agent-maintained index of hunt scripts
        tools/  evidence/  reports/  tmp/

Root: $SEPP_WORKSPACES_DIR (default <repo>/data/workspaces). Never committed.

Subcommands:
  ensure --host H --url U [--context TXT] [--cred-pointer P]   -> JSON {id,path,created}
  list                                                          -> JSON [...]
  prune --days N [--apply]                                      -> JSON {candidates,removed}
  path <session-id>                                             -> abs path
"""
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


def session_id_for(host: str) -> str:
    return "%s-%s" % (time.strftime("%Y%m%d"), hostslug(host))


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
    sid = session_id_for(ap_args.host)
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
    sj.write_text(json.dumps(sess, indent=2) + "\n")
    print(json.dumps({"id": sid, "path": str(ws), "created": created, "targets": len(sess["targets"])}))
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


def cmd_prune(ap_args) -> int:
    cutoff = time.time() - ap_args.days * 86400
    candidates = []
    for d in sorted(root().iterdir()):
        if not d.is_dir():
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
    print(json.dumps({"days": ap_args.days, "candidates": [c.name for c in candidates],
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
    s.set_defaults(func=cmd_ensure)
    s = sub.add_parser("list"); s.set_defaults(func=cmd_list)
    s = sub.add_parser("prune")
    s.add_argument("--days", type=int, required=True)
    s.add_argument("--apply", action="store_true", help="actually delete (default: report only)")
    s.set_defaults(func=cmd_prune)
    s = sub.add_parser("path"); s.add_argument("sid"); s.set_defaults(func=cmd_path)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
