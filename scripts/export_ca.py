#!/usr/bin/env python3
"""Export Caido's intercept CA certificate so curl/python trust proxied TLS.

Uses `openssl s_client -proxy ... -showcerts` through Caido, parses ALL
presented PEMs (leaf + Caido root), caches as traffic/caido-ca.pem.

Usage: export_ca.py [hostname]     (default: pilot-app.example)
Exit 0 on success, nonzero on failure. Idempotent, fast.
"""
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CA_OUT = HERE.parent / "data" / "caido-ca.pem"
PROXY = "127.0.0.1:8080"

PEM_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "pilot-app.example"
    cmd = ["openssl", "s_client", "-proxy", PROXY,
           "-connect", "%s:443" % host, "-servername", host, "-showcerts"]
    try:
        out = subprocess.run(cmd, input=b"", capture_output=True,
                             timeout=20).stdout.decode(errors="replace")
    except FileNotFoundError:
        print("FAIL: openssl not on PATH", file=sys.stderr)
        return 1
    pems = PEM_RE.findall(out)
    if not pems:
        print("FAIL: no certificates in s_client output", file=sys.stderr)
        return 1
    CA_OUT.parent.mkdir(parents=True, exist_ok=True)
    CA_OUT.write_text("".join(p + "\n" for p in pems))
    # verify usability
    chk = subprocess.run(
        ["openssl", "verify", "-CAfile", str(CA_OUT), str(CA_OUT)],
        capture_output=True)
    print("OK wrote %s (%d cert(s)); self-verify: %s"
          % (CA_OUT, len(pems), "ok" if chk.returncode == 0 else chk.stderr.decode()[:120]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
