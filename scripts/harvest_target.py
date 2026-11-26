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


def candidate_ids(host, path_substr, max_keep=40):
    """Recent request ids for host, newest first: POSTs first, then others.

    Rationale: a fresh auth cookie often arrives via Set-Cookie on a POST
    (e.g. /auth/refresh) but is only carried on subsequent GETs — so the
    freshest USABLE request may not be a POST.
    """
    f = 'req.host.eq:"%s"' % host
    if path_substr:
        f += ' and req.path.cont:"%s"' % path_substr
    posts, others, cursor, prev_cursor = [], [], "", None
    for _page in range(300):  # safety bound; each page covers <=100 requests
        cmd = ["caido-cli", "history", "-f", f, "-n", "100"]
        if cursor:
            cmd += ["--after", cursor]
        out = subprocess.run(cmd, capture_output=True,
                             env=dict(os.environ, CAIDO_URL="http://127.0.0.1:8080"))
        text = out.stdout.decode()
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)\s+(POST)\s+\d*\s*", line)
            if m:
                posts.append(int(m.group(1)))
                continue
            m = re.match(r"\s*(\d+)\s+([A-Z]+)\s+\d*\s*", line)
            if m:
                others.append(int(m.group(1)))
            c = re.match(r"--after (\S+)", line.strip())
            if c:
                cursor = c.group(1)
        if not cursor or "--after" not in text:
            break  # no cursor line => last page reached
        if cursor == prev_cursor:
            break  # page didn't advance => last page reached
        prev_cursor = cursor
    return sorted(set(posts), reverse=True)[:max_keep], \
        sorted(set(others), reverse=True)[:max_keep]


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

    posts, others = candidate_ids(host, args.path_substr)
    if not posts and not others:
        print("FAIL: no traffic for %s in history" % host, file=sys.stderr)
        return 1
    # Preference order: with --cookie-name, any request bearing that cookie;
    # otherwise newest POST, falling back to newest requests of any method.
    if args.cookie_name:
        order = sorted(set(posts) | set(others), reverse=True)
    else:
        order = posts + sorted(set(others) - set(posts), reverse=True)
    order = order[:25]  # Caido metadata fetches only, well below rate budget

    def _jar_of(raw_text):
        """Extract (cookie_header, auth_line) from a raw request."""
        ck = au = None
        for line in raw_text.split("\r\n"):
            low = line.lower()
            if low.startswith("cookie:") and ck is None:
                ck = line.split(":", 1)[1].strip()
            elif low.startswith("authorization:") and au is None:
                au = line.split(":", 1)[1].strip()
        return ck, au

    def _fresh_jwt_count(cookie_header):
        if not cookie_header:
            return 0
        return sum(1 for p in cookie_header.split(";")
                   if "=" in p and jwt_exp(p.strip().split("=", 1)[1] or "")
                   and jwt_exp(p.strip().split("=", 1)[1]) > time.time())

    # Walk candidates newest-first; when no --cookie-name is forced, prefer
    # the jar holding the MOST fresh JWT cookies (ties -> newest id): a jar
    # with fewer fresh JWTs may be missing rotated tokens (e.g. a chat-
    # gateway cookie that rides along only on richer requests).
    best = None  # (score_tuple, id, raw, cookie, auth)
    for cid in order:
        out = gql('query($id:ID!){ request(id:$id){ raw } }', {"id": str(cid)})
        cand_raw = base64.b64decode(out["data"]["request"]["raw"]).decode(errors="replace")
        cand_cookie, cand_auth = _jar_of(cand_raw)
        if args.cookie_name:
            if not (cand_cookie and ("%s=" % args.cookie_name) in cand_cookie):
                continue
            score = (1, cid)
        else:
            score = (_fresh_jwt_count(cand_cookie), cid)
        if best is None or score > best[0]:
            best = (score, cid, cand_raw, cand_cookie, cand_auth)
        if best[0][0] >= 3:
            break  # rich enough jar; stop fetching
    if best is None:
        print("FAIL: no recent request for %s carries cookie %r"
              % (host, args.cookie_name), file=sys.stderr)
        return 1
    _, nid, raw, cookie, auth_line = best

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

    # Propagate the fresh jar into baseline.req — Transport replays THAT
    # header template, so a stale Cookie line there means a dead session
    # regardless of what target.json holds.
    # GitHub issue #78: operate on RAW BYTES. read_text()/write_text() apply
    # universal-newline translation and silently rewrite CRLF baselines to
    # LF-only, which broke Transport's header parsing (targets lost their
    # Cookie/Referer headers -> 401). Bytes in, bytes out.
    baseline = ROOT / "data" / "targets" / args.slug / "baseline.req"
    if baseline.exists():
        raw = baseline.read_bytes()
        cookie_line = ("Cookie: " + cookie).encode()
        raw, nsubs = re.subn(br"(?im)^cookie:[^\r\n]*",
                             lambda _m: cookie_line, raw)
        if nsubs:
            baseline.write_bytes(raw)
            print("OK baseline.req Cookie line refreshed (%d jar(s))." % nsubs)
        else:
            print("WARN: no Cookie line in %s to refresh" % baseline, file=sys.stderr)

    if mins_left < args.min_exp_minutes:
        print("STALE: %s has %.0f min left (<%.0f). Browse %s through the proxy"
              " to refresh, then rerun." % (name, mins_left, args.min_exp_minutes, host),
              file=sys.stderr)
        return 2
    print("OK request %s | %.0f min left on %s" % (nid, mins_left, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
