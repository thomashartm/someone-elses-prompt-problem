#!/usr/bin/env python3
"""Offline JWT analysis - never sends the token anywhere; decodes locally.

Commands:
  decode <jwt>                 pretty-print header/payload (+ sanity warnings)
  delta <jwtA> <jwtB>          structural diff between two tokens (aud? role? exp?)
  forge-none <jwt> [--out F]   unsigned HS256-styled token ('alg:none') for replay tests
"""
import base64
import argparse
import json
import sys


def b64d(seg):
    pad = "=" * (-len(seg) % 4)
    raw = base64.urlsafe_b64decode(seg + pad)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"_raw_bytes_len": len(raw)}


def split(jwt):
    parts = jwt.strip().split(".")
    if len(parts) < 2:
        raise SystemExit("not a JWT (need >=2 dot-separated segments)")
    return parts


def sanity(header, payload):
    warns = []
    alg = str(header.get("alg", ""))
    if alg.lower() in ("none", ""):
        warns.append("alg=none-style token")
    if "HS256" in alg:
        warns.append("symmetric HMAC - consider key brute-force (weak secrets) / alg confusion RS<->HS")
    if header.get("kid", "").startswith(("../", "/", "~")):
        warns.append("kid looks path-like - traversal/injection candidate")
    if "jku" in header or "x5u" in header:
        warns.append("jku/x5u present - attacker-controllable key URL candidate")
    exp = payload.get("exp")
    if not exp:
        warns.append("no exp claim")
    return warns


def cmd_decode(args):
    parts = split(args.jwt)
    header, payload = b64d(parts[0]), b64d(parts[1])
    print(json.dumps({"header": header, "payload": payload}, indent=2))
    warns = sanity(header, payload)
    if warns:
        print("\nWARNINGS:")
        for w in warns:
            print("  - " + w)


def cmd_delta(args):
    _, pa = split(args.jwt_a), split(args.jwt_b)
    ha, hb = b64d(pa[0][0]), b64d(pa[1][0])  # noqa: F841 - header kept for future
    ua, ub = b64d(pa[0][1]), b64d(pa[1][1])
    keys = sorted(set(ua) | set(ub))
    print(json.dumps({k: [ua.get(k, "<absent>"), ub.get(k, "<absent>")] for k in keys}, indent=2))


def cmd_forge(args):
    parts = split(args.jwt)
    header = b64d(parts[0])
    header.pop("kid", None)
    header["alg"] = "none"
    seg = lambda obj: base64.urlsafe_b64encode(  # noqa: E731
        json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()
    token = "%s.%s." % (seg(header), seg(b64d(parts[1])))
    if args.out:
        import pathlib
        pathlib.Path(args.out).write_text(token + "\n")
        print("written -> " + args.out)
    else:
        print(token)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("decode"); s.add_argument("jwt"); s.set_defaults(func=cmd_decode)
    s = sub.add_parser("delta"); s.add_argument("jwt_a"); s.add_argument("jwt_b"); s.set_defaults(func=cmd_delta)
    s = sub.add_parser("forge-none"); s.add_argument("jwt"); s.add_argument("--out", default=""); s.set_defaults(func=cmd_forge)
    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
