#!/usr/bin/env python3
"""Harvest the freshest session cookie for a target from Caido history.

Why this exists: caido-cli redacts Cookie header values; the GraphQL raw blob
does not. This also VALIDATES the JWT lifetime (many targets issue ~30 min
tokens) so a hunt never starts on a dead session.

Target-agnostic: the slug selects data/targets/<slug>/target.json (default:
$CAIDO_TARGET, else "testbed"); the host is taken from that config's "url".
The auth cookie is auto-detected as the first cookie whose value decodes as a
JWT carrying an "exp" claim (override with --cookie-name).

Usage: harvest_target.py [--slug SLUG] [--cookie-name NAME] [--path SUBSTR]
                         [--min-exp-minutes 5]
Exit 0 = target.json updated and token valid; exit 2 = cookie found but stale;
exit 1 = nothing usable found.
"""
import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def target_cfg(slug):
    path = ROOT / "data" / "targets" / slug / "target.json"
    if not path.exists():
        raise SystemExit(
            "FAIL: no target config %s (register it first, see README)" % path)
    return json.loads(path.read_text()), path


def jwt_exp(value):
    """Return the 'exp' claim if value looks like a JWT, else None."""
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        pl = parts[1]
        payload = json.loads(base64.urlsafe_b64decode(pl + "=" * (-len(pl) % 4)))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


def gql(query, variables):
    sys.path.insert(0, str(ROOT / "helpers"))
    from findings import gql as _gql
    return _gql(query, variables)


def newest_post_id(host, path_substr):
    f = 'req.host.eq:"%s"' % host
    if path_substr:
        f += ' and req.path.cont:"%s"' % path_substr
    out = subprocess.run(
        ["caido-cli", "history", "-f", f, "-n", "2000"],
        capture_output=True, env=dict(os.environ, CAIDO_URL="http://127.0.0.1:8080"))
    ids = [int(m.group(1)) for line in out.stdout.decode().splitlines()
           if (m := re.match(r"\s*(\d+)\s+POST\s+\d*\s*", line))]
    return max(ids) if ids else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slug", default=os.environ.get("CAIDO_TARGET", "testbed"),
                    help="target slug under data/targets/ (default: $CAIDO_TARGET)")
    ap.add_argument("--cookie-name", default=None,
                    help="pick this cookie explicitly instead of auto-detecting the JWT one")
    ap.add_argument("--path", default="", dest="path_substr",
                    help="only consider POSTs whose path contains this substring")
    ap.add_argument("--min-exp-minutes", type=float, default=5.0)
    args = ap.parse_args()

    cfg, cfg_path = target_cfg(args.slug)
    host = urlparse(cfg["url"]).hostname
    if not host:
        print("FAIL: cannot derive host from %s" % cfg["url"], file=sys.stderr)
        return 1

    nid = newest_post_id(host, args.path_substr)
    if not nid:
        print("FAIL: no POST traffic for %s in history" % host, file=sys.stderr)
        return 1
    out = gql('query($id:ID!){ request(id:$id){ raw } }', {"id": str(nid)})
    raw = base64.b64decode(out["data"]["request"]["raw"]).decode(errors="replace")

    cookie = None
    auth_line = None
    for line in raw.split("\r\n"):
        low = line.lower()
        if low.startswith("cookie:") and cookie is None:
            cookie = line.split(":", 1)[1].strip()
        elif low.startswith("authorization:") and auth_line is None:
            auth_line = line.split(":", 1)[1].strip()

    def _mins_check(exp, what):
        mins_left = (exp - time.time()) / 60
        if mins_left < args.min_exp_minutes:
            print("STALE: %s has %.0f min left (<%.0f). Refresh the session "
                  "(browse %s through the proxy or re-login), then rerun."
                  % (what, mins_left, args.min_exp_minutes, host), file=sys.stderr)
            return False
        print("OK request %s | %.0f min left on %s" % (nid, mins_left, what))
        return True

    if not cookie and auth_line and auth_line.lower().startswith("bearer "):
        # BEARER targets (e.g. the testbed): validate the JWT, then refresh
        # the Authorization line in baseline.req (the artifact Target() ships).
        token = auth_line.split(None, 1)[1].strip()
        exp = jwt_exp(token)
        if exp is None:
            print("FAIL: bearer on request %s is not a JWT with exp; cannot "
                  "validate lifetime" % nid, file=sys.stderr)
            return 1
        baseline = ROOT / "data" / "targets" / args.slug / "baseline.req"
        text = baseline.read_text()
        new_text, nsubs = re.subn(
            r"(?im)^authorization:.*$", "Authorization: Bearer " + token, text)
        if nsubs == 0:
            print("FAIL: no Authorization line in %s to refresh" % baseline,
                  file=sys.stderr)
            return 1
        baseline.write_text(new_text)
        cfg["token_exp"] = exp
        cfg["credential"] = "bearer"
        cfg_path.write_text(json.dumps(cfg, indent=2))
        return 0 if _mins_check(exp, "bearer JWT") else 2

    if not cookie:
        print("FAIL: request %s has neither Cookie nor Bearer credential"
              % nid, file=sys.stderr)
        return 1

    pairs = [p.strip() for p in cookie.split(";") if "=" in p]
    if args.cookie_name:
        chosen = [p for p in pairs if p.split("=", 1)[0] == args.cookie_name]
        if not chosen:
            print("FAIL: cookie %r not in request %s (have: %s)"
                  % (args.cookie_name, nid, ", ".join(p.split("=", 1)[0] for p in pairs)),
                  file=sys.stderr)
            return 1
        name, _, value = chosen[0].partition("=")
        exp = jwt_exp(value)
        if exp is None:
            print("OK (opaque cookie %r, no exp to validate) request %s" % (name, nid))
            cfg["cookie"] = cookie
            cfg_path.write_text(json.dumps(cfg, indent=2))
            return 0
    else:
        chosen = [(p.split("=", 1)[0], p.split("=", 1)[1], jwt_exp(p.split("=", 1)[1]))
                  for p in pairs]
        chosen = [(n, v, e) for n, v, e in chosen if e is not None]
        if not chosen:
            print("FAIL: no JWT-shaped cookie in request %s (have: %s); "
                  "pass --cookie-name for opaque session cookies"
                  % (nid, ", ".join(p.split("=", 1)[0] for p in pairs)), file=sys.stderr)
            return 1
        name, value, exp = chosen[0]

    mins_left = (exp - time.time()) / 60
    cfg["cookie"] = cookie
    cfg["token_cookie"] = name
    cfg["token_exp"] = exp
    cfg_path.write_text(json.dumps(cfg, indent=2))

    if mins_left < args.min_exp_minutes:
        print("STALE: %s has %.0f min left (<%.0f). Browse %s through the proxy"
              " to refresh, then rerun." % (name, mins_left, args.min_exp_minutes, host),
              file=sys.stderr)
        return 2
    print("OK request %s | %.0f min left on %s" % (nid, mins_left, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
