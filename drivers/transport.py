"""Target-blind transport for the SEPP hunting stack.

Sends one prepared HTTP request to a target THROUGH the Caido proxy
(proxytunnel + CA pinning), serial and paced. Knows NOTHING about the
target's dialect - the caller supplies exact body bytes (usually built by
the target's adapter, see drivers/ADAPTER_CONTRACT.md).

Owns:
- baseline request-header template from data/targets/<slug>/baseline.req
  (headers harvested from the captured browser request)
- curl via Caido (--proxytunnel --cacert), SSE-safe (-N, generous timeout)
- one silent CA regeneration on curl exit 60 (proxy rotated its CA)
- rate limiting: SEPP_RATE_PAUSE sleep after each OUTBOUND request
- no blind retries (a timed-out turn was already processed server-side)
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
DATA_ROOT = pathlib.Path(os.environ.get("CAIDO_DATA_DIR", REPO / "data"))

RATE_PAUSE_S = float(os.environ.get("SEPP_RATE_PAUSE", "10"))

# Headers worth replaying from the captured browser request.
# authorization: required for JWT-bearing targets (e.g. the testbed bearer).
KEEP_PREFIXES = ("user-agent:", "accept:", "accept-language:", "referer:",
                 "origin:", "content-type:", "cookie:", "authorization:")
DROP_PREFIXES = ("host:", "content-length:", "connection:", "accept-encoding:",
                 "post ", "get ")


def _regen_ca():
    subprocess.run([sys.executable, str(REPO / "scripts" / "export_ca.py")],
                   capture_output=True, timeout=30)


class Target:
    """Config + header template for one target slug."""

    def __init__(self, slug: str):
        self.slug = slug
        self.dir = DATA_ROOT / "targets" / slug
        self.cfg = json.loads((self.dir / "target.json").read_text())
        baseline = (self.dir / "baseline.req").read_bytes().decode()
        self.header_args = []
        for line in baseline.split("\r\n\r\n")[0].split("\r\n"):
            low = line.lower()
            if low.startswith(DROP_PREFIXES):
                continue
            if low.startswith(KEEP_PREFIXES):
                self.header_args += ["-H", line]

    @property
    def url(self) -> str:
        return self.cfg["url"]

    @property
    def default_conversation_id(self):
        return self.cfg.get("conversation_id")


class Transport:
    """Serial, paced, Caido-proxied HTTP POST sender."""

    def __init__(self, target: Target, body_tmp: pathlib.Path | None = None):
        self.target = target
        tmp = body_tmp or (DATA_ROOT / "tmp" / "transport.body.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        self.body_tmp = tmp
        self.ca = DATA_ROOT / "caido-ca.pem"
        if not self.ca.exists():
            _regen_ca()

    def send(self, body: bytes, timeout_s: int = 170) -> tuple[str, str]:
        """Return (http_code, raw_response_text). Never retries."""
        self.body_tmp.write_bytes(body)
        proc = subprocess.run(
            ["curl", "-s", "-N", "-m", str(timeout_s),
             "-x", os.environ.get("CAIDO_PROXY", "http://127.0.0.1:8080"),
             # --noproxy '': an operator NO_PROXY covering loopback would
             # otherwise SILENTLY bypass the -x proxy for 127.0.0.1 targets
             # (ISSUES.md row 42) - transports must force proxy usage.
             "--noproxy", "",
             "--proxytunnel", "--cacert", str(self.ca), "-X", "POST",
             *self.target.header_args,
             "--data-binary", "@" + str(self.body_tmp),
             "-w", "\n__HTTP_CODE__:%{http_code}",
             self.target.url],
            capture_output=True, timeout=timeout_s + 10)
        raw = proc.stdout.decode(errors="replace")
        import re
        m = re.search(r"__HTTP_CODE__:(\d+)\s*$", raw)
        code = m.group(1) if m else "???"
        # strip the sentinel trailer from the payload handed to adapters
        raw = raw[:m.start()] if m else raw
        return code, raw.strip()


def chat_turn(adapter, target: Target, prompt: str, *,
              fresh_conversation: bool, conversation_id=None,
              model: str | None = None, timeout_s: int = 170,
              want_kind: bool = False):
    """One paced chat turn: adapter.build_body -> transport -> adapter.parse.

    Returns (http_code, text, conversation_id_or_None) — or, with
    want_kind=True, a 4-tuple with the adapter's parse kind appended
    ("ok" | "declined" | "error"). Callers needing decline semantics
    (multi-turn conversation reuse) use the 4-tuple; existing callers are
    untouched. Sleeps RATE_PAUSE_S AFTER the request (every request hits the
    server; pacing is not skipped on failures). Callers wanting retries must
    opt in ABOVE this layer.
    """
    mdl = model or adapter.default_model()
    effective_conv = conversation_id if conversation_id is not None else target.default_conversation_id
    body = adapter.build_body(prompt, mdl,
                              fresh_conversation=fresh_conversation,
                              conversation_id=effective_conv)
    code, raw = Transport(target).send(body, timeout_s=timeout_s)
    text, kind, conv_id = adapter.parse(raw)
    status = code if code != "???" else "ERR"
    if code == "200" and kind == "error":
        status = "200-empty"
    time.sleep(RATE_PAUSE_S)
    return (status, text, conv_id, kind) if want_kind else (status, text, conv_id)


class ConvPool:
    """Conversation continuity for multi-turn probes (decision 3).

    fresh (default): every turn starts a new conversation - probe isolation.
    reuse: turns sharing the SAME FIRST NON-EMPTY MESSAGE continue one thread:
      miss  -> fresh_conversation=True, remember the returned conv id
      hit   -> fresh_conversation=False, continue the stored conv id
      declined (or error) thread -> key cleared; the next turn starts clean
      one key serves at most SEPP_SHIM_CONV_MAX_TURNS turns, then it is
      dropped (bounds memory and runaway threads)
    SERIAL RUNS ONLY: state lives in this process; parallel garak workers
    would interleave unrelated probes into shared threads.
    """

    def __init__(self, mode: str = "fresh", max_turns: int = 10):
        if mode not in ("fresh", "reuse"):
            raise ValueError(f"conversation mode must be fresh|reuse, got {mode!r}")
        self.mode = mode
        self.max_turns = max(1, int(max_turns))
        self.threads: dict[str, dict] = {}

    @staticmethod
    def key(messages) -> str:
        first = next((m.content for m in messages if m.content), "")
        return hashlib.sha1(first.encode("utf-8")).hexdigest()

    def plan(self, messages) -> tuple[bool, str | None, int, str]:
        """Returns (fresh_conversation, conversation_id, turn_number, key)."""
        key = self.key(messages)
        if self.mode != "reuse":
            return True, None, 1, key
        thread = self.threads.get(key)
        if not thread or not thread.get("conv_id"):
            return True, None, 1, key
        turn = thread["turns"] + 1
        if turn > self.max_turns:
            self.threads.pop(key, None)
            return True, None, 1, key
        return False, thread["conv_id"], turn, key

    def observe(self, key: str, kind: str, conv_id: str | None, turn: int) -> None:
        if self.mode != "reuse":
            return
        if kind in ("declined", "error"):
            self.threads.pop(key, None)
            return
        if not conv_id:
            return
        self.threads[key] = {"conv_id": conv_id, "turns": turn}
        if turn >= self.max_turns:
            self.threads.pop(key, None)
