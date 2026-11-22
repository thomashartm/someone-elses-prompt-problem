#!/usr/bin/env python3
"""Harvest OAuth/OIDC traffic from Caido history and run a static flaw checklist.

Caido stores captured traffic behind its GraphQL API. This script queries
`requests` (paginated), extracts any request/response pairs touching known
OAuth endpoints (authorize / token / jwks / .well-known / refresh / userinfo),
prints a timeline, and emits a checklist of cheap falsifiable hypotheses.

Requires Caido auth (CAIDO_ACCESS_TOKEN env or ~/.caido-mcp/token.json from
`caido-mcp-server login`). Falls back with a clear error otherwise.

Usage:
  oauth_flow.py harvest [--host api.example.com] [--limit 500]
  oauth_flow.py check <harvest-output.json>
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.request

CAIDO_URL = os.environ.get("CAIDO_URL", "http://127.0.0.1:8080").rstrip("/")
OAUTH_HINTS = [
    "/authorize", "/oauth", "/oidc", "/token", "/connect/",
    "/.well-known/openid-configuration", "/.well-known/jwks", "/jwks",
    "/userinfo", "/introspect", "/revocation", "/refresh", "/device",
]


def _token():
    tok = os.environ.get("CAIDO_ACCESS_TOKEN")
    if tok:
        return tok
    token_dir = pathlib.Path.home() / ".caido-mcp" / "tokens"
    if token_dir.is_dir():
        candidates = sorted(token_dir.glob("*.json"))
        want = CAIDO_URL.split("//", 1)[-1].rstrip("/")
        prefer = [p for p in candidates if want in p.name]
        for path in prefer or candidates:
            try:
                tok = json.loads(path.read_text()).get("accessToken")
                if tok:
                    return tok
            except Exception:
                continue
    legacy = pathlib.Path.home() / ".caido-mcp" / "token.json"
    if legacy.exists():
        try:
            return json.loads(legacy.read_text()).get("access_token")
        except Exception:
            return None
    return None


def gql(query, variables):
    tok = _token()
    if not tok:
        raise SystemExit(
            "No Caido auth. Run: CAIDO_URL=%s caido-mcp-server login  "
            "(or export CAIDO_ACCESS_TOKEN)" % CAIDO_URL
        )
    req = urllib.request.Request(
        CAIDO_URL + "/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        out = json.load(resp)
    if out.get("errors"):
        raise SystemExit("GraphQL errors: %s" % json.dumps(out["errors"])[:600])
    return out


def cmd_harvest(args):
    flt = 'req.host.eq:"%s"' % args.host if args.host else ""
    after = None
    events = []
    for _page in range(20):  # hard cap: ~20*100 = 2000 requests max
        q = """
        query($first:Int!,$after:String,$filter:String){
          requests(first:$first,after:$after,filter:$filter){
            edges{ node{ id createdAt method host path } pageInfo{ hasNextPage endCursor } }
          }
        }"""
        data = gql(q, {"first": 100, "after": after, "filter": flt})
        conn = data["data"]["requests"]
        for edge in conn["edges"]:
            n = edge["node"]
            if any(h in n["path"] for h in OAUTH_HINTS):
                events.append(n)
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]

    print(json.dumps({"count": len(events), "events": events}, indent=2))
    print("\nNEXT: pick event ids above, then `caido-cli request <id>` for full\n"
          "headers/bodies; feed the exported JSON to: oauth_flow.py check",
          file=sys.stderr)
    return 0


CHECKLIST = [
    ("PKCE enforced?", "If authorize request lacks code_verifier/challenge, retry with"
     " stolen code twice -> code reuse."),
    ("state validated?", "Remove/modify state param on callback; observe acceptance."),
    ("redirect_uri strictly matched?", "Try sibling paths, case flips, trailing slash,"
     " added params on redirect_uri at authorize."),
    ("code single-use?", "Replay token exchange with same code twice -> two token sets."),
    ("client auth on token endpoint?", "Exchange code with wrong/no client_secret."),
    ("audience confusion?", "Decode access+id tokens (jwt_tools.py decode); swap aud"
     " values between audiences; hit API with token issued for the other one."),
    ("refresh rotation?", "Refresh twice with same refresh token; old one should die."),
    ("alg/key checks?", "jwt decode each token: kid traversal, jku/x5u, alg none"
     " (jwt_tools.py forge-none) and replay against resource server."),
    ("expired token acceptance?", "Wait out or rewind exp; replay; note any 2xx."),
    ("scopes enforced downstream?", "Token with minimized scopes on privileged route."),
]


def cmd_check(args):
    harvested = json.loads(pathlib.Path(args.file).read_text())
    ev_count = harvested.get("count", 0)
    print("Harvest shows %d OAuth-related requests. Checklist:\n" % ev_count)
    for i, (title, how) in enumerate(CHECKLIST, 1):
        print("%2d. %s\n    how: %s" % (i, title, how))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("harvest")
    s.add_argument("--host", default="")
    s.add_argument("--limit", type=int, default=500, help="(advisory) rough page cap hint")
    s.set_defaults(func=cmd_harvest)
    s = sub.add_parser("check"); s.add_argument("file"); s.set_defaults(func=cmd_check)
    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
