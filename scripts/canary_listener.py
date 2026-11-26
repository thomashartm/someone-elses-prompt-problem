#!/usr/bin/env python3
"""Loopback canary listener: journals every request that reaches it.

Piece 1 of the exfiltration proof rig (docs/exploits, LLM05). Anything that
makes the victim's renderer load an external resource shows up here as one
JSONL line - method, path, query, headers. Replies 204 and serves nothing.

Bind loopback by default; a remote-binding flag is a deliberate extension,
not the default (see AGENTS.md scope rules).

Usage:
    python3 scripts/canary_listener.py --port 0 --log data/reports/canary.jsonl
Exit: runs until SIGINT; prints the bound address on startup and a summary on
Ctrl+C. Port 0 picks a free ephemeral port.
"""
from __future__ import annotations

import argparse
import datetime
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

LOG_FLUSH_LOCK = threading.Lock()


class Journal:
    """Append-only JSONL journal; tolerant readers treat a torn last line as
    absent (writer crash mid-line loses one hit, never corrupts the file)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = LOG_FLUSH_LOCK

    def append(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

    def lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return [
            line
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class CanaryHandler(http.server.BaseHTTPRequestHandler):
    journal: Journal = None  # type: ignore[assignment]

    def _record(self):
        path = self.path
        rec = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "method": self.command,
            "path": path.split("?", 1)[0],
            "query": path.split("?", 1)[1] if "?" in path else "",
            "user_agent": self.headers.get("User-Agent", ""),
            "referer": self.headers.get("Referer", ""),
        }
        self.journal.append(rec)
        return rec

    def do_GET(self):
        self._record()
        self.send_response(204)
        self.end_headers()

    do_POST = do_HEAD = do_GET

    def log_message(self, fmt, *args):  # silence stderr chatter; the journal is the log
        pass


def serve(host: str, port: int, journal: Journal) -> tuple[socketserver.TCPServer, int]:
    handler = type("BoundHandler", (CanaryHandler,), {"journal": journal})
    server = socketserver.ThreadingTCPServer((host, port), handler)
    server.daemon_threads = True
    server.timeout = 0.25  # handle_request() polls this often (no kwarg before Py 3.12)
    bound = server.server_address[1]
    return server, bound


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    ap.add_argument("--log", default="data/reports/canary.jsonl")
    ap.add_argument("--max-hits", type=int, default=0, help="stop after N hits (0 = run forever)")
    args = ap.parse_args()

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    journal = Journal(log_path)
    server, bound = serve(args.host, args.port, journal)
    print(f"canary listening on {args.host}:{bound} -> {log_path}", flush=True)

    hits_at_start = len(journal.lines())
    try:
        while True:
            server.handle_request()
            if args.max_hits and len(journal.lines()) - hits_at_start >= args.max_hits:
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        total = len(journal.lines()) - hits_at_start
        print(f"canary stopped; journaled {total} hits this run", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
