#!/usr/bin/env python3
"""Headless-Chrome renderer: loads a URL with JS execution and optional proxy.

Piece 2 of the exfiltration proof rig (docs/exploits, LLM05). Captures the
rendered DOM so a checker can compare raw payload text against escaped markup
after real browser execution.

Important: `--proxy-bypass-list=<-loopback>` is REQUIRED when the proxied URL
points at 127.0.0.1 - Chrome otherwise bypasses the proxy for loopback hosts
and the request never reaches Caido.

Usage:
    python3 drivers/headless_render.py --url http://127.0.0.1:8765/llm/render?id=1 \
        [--proxy http://127.0.0.1:8080] [--dom-out dom.html] [--timeout 20]

Exit: 0 DOM captured, 1 navigation/render failure, 2 no browser found.
No new Python dependencies: drives the installed Chrome/Chromium binary.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

CHROME_CANDIDATES = [
    "$SEPP_CHROME",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
]


def find_chrome() -> str | None:
    import os

    for candidate in CHROME_CANDIDATES:
        value = os.path.expandvars(candidate) if "$" in candidate else candidate
        if "/" in value:
            if Path(value).exists():
                return value
        else:
            found = shutil.which(value)
            if found:
                return found
    return None


def chrome_flags(proxy: str | None, user_data_dir: str) -> list[str]:
    flags = [
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={user_data_dir}",
        "--ignore-certificate-errors",
        # virtual time fast-forwards rendering; --timeout bounds a stalled
        # budget (pending subresource) so --dump-dom still fires
        "--virtual-time-budget=5000",
        "--timeout=12000",
        "--dump-dom",
    ]
    if proxy:
        flags += [f"--proxy-server={proxy}", "--proxy-bypass-list=<-loopback>"]
    return flags


def render(url: str, proxy: str | None, timeout: int) -> tuple[int, str, str]:
    """Returns (exit_code, dom_or_empty, diagnostics).
    exit 0: DOM captured. exit 1: navigation produced no DOM. exit 2: no browser.
    Uses incremental pipe readers plus a kill watchdog: Chrome occasionally
    overshoots --virtual-time-budget on a pending subresource, and killing it
    must not discard the DOM it already printed.
    Note: Chrome exits 0 even for failed navigations; success is judged by DOM
    shape, with stderr surfaced in diagnostics."""
    chrome = find_chrome()
    if chrome is None:
        return 2, "", "no Chrome/Chromium found; set SEPP_CHROME to the binary path"
    buf: dict[str, list[str]] = {"out": [], "err": []}

    def reader(stream, key):
        try:
            for chunk in iter(lambda: stream.readline(), ""):
                buf[key].append(chunk)
        except ValueError:
            pass  # closed while we were reading

    with tempfile.TemporaryDirectory(prefix="sepp-render-") as tmp:
        proc = subprocess.Popen(
            [chrome, *chrome_flags(proxy, tmp), url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        readers = [
            threading.Thread(target=reader, args=(stream, key), daemon=True)
            for stream, key in ((proc.stdout, "out"), (proc.stderr, "err"))
        ]
        for thread in readers:
            thread.start()
        deadline = time.monotonic() + timeout
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        timed_out = proc.poll() is None
        if timed_out:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
        for thread in readers:
            thread.join(timeout=3)
    dom = "".join(buf["out"])
    stderr_tail = "".join(buf["err"]).strip()[-400:]
    looks_done = dom.lstrip().lower().startswith(("<!doctype", "<html"))
    notes = [f"render killed after {timeout}s (virtual-time stall?)" if timed_out else "chrome exited"]
    if stderr_tail:
        notes.append(stderr_tail)
    if looks_done:
        return 0, dom, " ".join(notes)
    if timed_out:
        return 1, dom, " ".join(notes)
    return 1, dom, " ".join(notes) or f"chrome exited {proc.returncode} with empty DOM"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--proxy", default=None, help="e.g. http://127.0.0.1:8080 (Caido)")
    ap.add_argument("--dom-out", default=None, help="write the DOM here (else stdout)")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()

    code, output, diagnostics = render(args.url, args.proxy, args.timeout)
    if code == 2:
        print(diagnostics, file=sys.stderr)
        return 2
    if code != 0:
        print(f"render diagnostics: {diagnostics}", file=sys.stderr)
        return 1
    if args.dom_out:
        Path(args.dom_out).write_text(output, encoding="utf-8")
        print(f"DOM ({len(output)} chars) -> {args.dom_out}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
