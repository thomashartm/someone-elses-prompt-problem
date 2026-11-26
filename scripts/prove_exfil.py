#!/usr/bin/env python3
"""Orchestrate an exfiltration proof: listener + headless render + verdict.

Piece 3 of the exfiltration proof rig (docs/exploits, LLM05). Starts a canary
listener on an ephemeral loopback port, renders the target page in headless
Chrome (optionally through Caido), then checks the journal:

- default: PASS when at least one request arrived and a hit matches --match
- --assert-absent: inverted control - PASS when ZERO matching hits arrive
  (the strict-tier negative control; any hit fails the run)

The evidence JSON sits beside the DOM capture and records the listener URL to
embed in a planted payload: `http://<host>:<port>/<hit-path>?d=<marker>`.

Usage (naive tier - exfil must land):
    python3 scripts/prove_exfil.py --url http://127.0.0.1:8765/llm/render?id=1 \
        --match SEC-ab12cd --evidence-out evidence/exfil.json

Strict tier - exfil must NOT land:
    ... --assert-absent

Exit: 0 proof satisfied, 1 proof failed, 2 environment error (no browser, no DOM).
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for entry in (HERE, ROOT / "drivers"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from canary_listener import Journal, serve  # noqa: E402
from headless_render import render  # noqa: E402


def hit_matches(entries: list[str], needle: str) -> list[dict]:
    matches = []
    for line in entries:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        haystack = f"{record.get('path', '')}{record.get('query', '')}"
        if needle in haystack:
            matches.append(record)
    return matches


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="page to render (the sink under test)")
    ap.add_argument("--match", required=True, help="substring that must appear in a hit (e.g. the marker)")
    ap.add_argument("--proxy", default=None, help="route the render through Caido, e.g. http://127.0.0.1:8080")
    ap.add_argument("--listen-host", default="127.0.0.1")
    ap.add_argument("--listen-port", type=int, default=8899,
                    help="fixed by default so planted payload URLs can bake it in; 0 = ephemeral")
    ap.add_argument("--wait", type=int, default=10, help="seconds to keep the listener open after render")
    ap.add_argument("--dom-out", default=None, help="keep the rendered DOM at this path")
    ap.add_argument("--evidence-out", default=None, help="write the evidence JSON here")
    ap.add_argument("--assert-absent", action="store_true",
                    help="invert the verdict: PASS only when no matching hit arrives")
    args = ap.parse_args()

    log_tmp = Path("data/reports")
    log_tmp.mkdir(parents=True, exist_ok=True)
    journal_path = log_tmp / f"canary-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}.jsonl"
    journal = Journal(journal_path)
    try:
        server, port = serve(args.listen_host, args.listen_port, journal)
    except OSError as exc:
        print(f"cannot bind {args.listen_host}:{args.listen_port} ({exc}); "
              f"is another canary run active? kill it or pass --listen-port 0", file=sys.stderr)
        return 2
    server.daemon_threads = True
    listener_url = f"http://{args.listen_host}:{port}/exfil"
    print(f"listener: {listener_url} (journal {journal_path})", flush=True)

    code, dom, diagnostics = render(args.url, args.proxy, timeout=15)
    if code == 2:
        print(diagnostics, file=sys.stderr)
        return 2
    dom_ok = code == 0

    deadline = time.monotonic() + args.wait
    matched = False
    while time.monotonic() < deadline:
        server.handle_request()
        matched = bool(hit_matches(journal.lines(), args.match))
        if matched:
            time.sleep(0.75)  # brief grace period for sibling hits, then finish
            break
    server.server_close()

    all_entries = journal.lines()
    matches = hit_matches(all_entries, args.match)

    if args.assert_absent:
        verdict = "pass" if not matches else "fail"
        reason = "no matching hit arrived" if not matches else f"{len(matches)} matching hit(s) arrived"
    else:
        verdict = "pass" if matches else "fail"
        reason = f"{len(matches)} matching hit(s)" if matches else "no matching hit arrived"
    if not dom_ok:
        reason += "; DOM capture failed (render stdout may still be informative)"
        if args.assert_absent and verdict == "pass":
            verdict = "inconclusive"

    evidence = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "url": args.url,
        "match": args.match,
        "listener_url": listener_url,
        "assert": "absent" if args.assert_absent else "present",
        "verdict": verdict,
        "reason": reason,
        "dom_capture": "ok" if dom_ok else "failed",
        "render_diagnostics": diagnostics,
        "journal": str(journal_path),
        "hits_total": len(all_entries),
        "matching_hits": matches,
    }
    if args.dom_out and dom_ok:
        Path(args.dom_out).write_text(dom, encoding="utf-8")
        evidence["dom_saved_to"] = args.dom_out
    out_path = Path(args.evidence_out) if args.evidence_out else journal_path.with_suffix(".evidence.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    print(f"verdict: {verdict.upper()} ({reason})")
    print(f"evidence: {out_path}")
    if not dom_ok:
        print(dom, file=sys.stderr)
    return {"pass": 0, "fail": 1, "inconclusive": 1}[verdict]


if __name__ == "__main__":
    sys.exit(main())
