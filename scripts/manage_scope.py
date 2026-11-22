#!/usr/bin/env python3
"""manage_scope.py - SEPP scope manager for Caido (via GraphQL).

Manages ONE dedicated scope (default name "sepp", get-or-created) so the
agent never mutates operator-made scopes. Idempotent merges only.

Commands:
  ensure [SITE...]   merge sites into the scope allowlist; default SITE is
                     "local" (= 127.0.0.1 + localhost, our testbed).
                     Also merges the default denylist (noise hosts) unless
                     --no-default-denylist.
  add [SITE...]      alias of ensure
  show               print the scope and exit

Sites: bare host (api.test), glob (*.example.com), or the magic "local".
Schemes/paths are stripped, output lowercased.
Exit 0 on success, 1 on GraphQL/HTTP problems. READ scope + UPDATE scope
are the only mutations performed (createScope when the scope is absent).
"""
import argparse
import json
import os
import pathlib
import sys
from urllib.parse import urlparse

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "helpers"))

from findings import gql  # noqa: E402

SCOPE_NAME = os.environ.get("SEPP_SCOPE_NAME", "sepp")

# Noise hosts excluded from history/views by default (operator-approved set).
DEFAULT_DENYLIST = [
    "*.google.com",
    "*.gstatic.com",
    "*.googleapis.com",
    "*.apple.com",
    "*.mozilla.org",
    "*.ubuntu.com",
    "*.docker.io",
    "docker.com",
]


def normalize(site: str) -> str:
    site = site.strip().lower()
    if "//" in site:
        parsed = urlparse(site)
        site = parsed.netloc or parsed.path.split("/", 1)[0]
    return site.rstrip("/")


def expand_sites(tokens):
    hosts = []
    for token in tokens:
        if token == "local":
            hosts += ["127.0.0.1", "localhost"]
        else:
            hosts.append(normalize(token))
    return hosts


def get_or_create_scope():
    out = gql("{ scopes { id name allowlist denylist } }")
    if out.get("errors"):
        raise RuntimeError("scopes query failed: %s" % json.dumps(out["errors"])[:300])
    for s in out["data"]["scopes"]:
        if s["name"] == SCOPE_NAME:
            return s, False
    out = gql(
        "mutation($inp: CreateScopeInput!){ createScope(input: $inp){ scope { id name } } }",
        {"inp": {"name": SCOPE_NAME, "allowlist": [], "denylist": []}},
    )
    if out.get("errors"):
        raise RuntimeError("createScope failed: %s" % json.dumps(out["errors"])[:300])
    scope = out["data"]["createScope"]["scope"]
    return {"id": scope["id"], "name": scope["name"],
            "allowlist": [], "denylist": []}, True


def merge(scope, allow_add, deny_add):
    allow = list(dict.fromkeys((scope.get("allowlist") or []) + allow_add))
    deny = list(dict.fromkeys((scope.get("denylist") or []) + deny_add))
    changed = allow != scope.get("allowlist") or deny != scope.get("denylist")
    return allow, deny, changed


def write_back(scope_id, allow, deny):
    out = gql(
        "mutation($id: ID!, $inp: UpdateScopeInput!){ updateScope(id: $id, input: $inp)"
        "{ scope { id name } } }",
        {"id": scope_id, "inp": {"name": SCOPE_NAME, "allowlist": allow, "denylist": deny}},
    )
    if out.get("errors"):
        raise RuntimeError("updateScope failed: %s" % json.dumps(out["errors"])[:300])


def render(scope, created, changed):
    lines = ["scope %r (id %s)%s%s" % (
        scope["name"], scope["id"],
        " CREATED" if created else "",
        " UPDATED" if changed else ""),
        "allowlist:"]
    lines += ["  + %s" % h for h in scope.get("allowlist") or []] or ["  (empty)"]
    lines.append("denylist:")
    lines += ["  - %s" % h for h in scope.get("denylist") or []] or ["  (empty)"]
    lines.append("note: if the Caido UI has a different scope ACTIVE, switch it"
                 " to %r there (activation is a UI setting)" % SCOPE_NAME)
    return "\n".join(lines)


def main():
    global SCOPE_NAME
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", default="ensure",
                    choices=("ensure", "add", "show"))
    ap.add_argument("sites", nargs="*",
                    help="hosts/globs to merge; bare 'local' expands to the testbed")
    ap.add_argument("--scope-name", default=SCOPE_NAME)
    ap.add_argument("--no-default-denylist", action="store_true",
                    help="skip merging the built-in noise-host denylist")
    args = ap.parse_args()
    SCOPE_NAME = args.scope_name

    try:
        scope, created = get_or_create_scope()
    except RuntimeError as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        return 1

    if args.command == "show":
        print(render(scope, created, False))
        return 0

    want_allow = expand_sites(args.sites) if args.sites else expand_sites(["local"])
    want_deny = [] if args.no_default_denylist else DEFAULT_DENYLIST
    allow, deny, changed = merge(scope, want_allow, want_deny)

    if changed:
        try:
            write_back(scope["id"], allow, deny)
        except RuntimeError as exc:
            print("FAIL: %s" % exc, file=sys.stderr)
            return 1
        scope.update(allowlist=allow, denylist=deny)

    print(render(scope, created, changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
