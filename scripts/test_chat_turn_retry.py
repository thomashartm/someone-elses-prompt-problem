"""Hot session-refresh retry in chat_turn (issue #78 family).

A 401/sessionExpired turn is retried exactly once, and only when a fresh
Target built from disk carries different credentials than the boot-time one
(harvest_target.py may have rotated baseline.req meanwhile). Pins all three
behaviours. Run: python3 -m unittest scripts.test_chat_turn_retry
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT / "drivers") not in sys.path:
    sys.path.insert(0, str(ROOT / "drivers"))

import transport  # noqa: E402
from transport import chat_turn  # noqa: E402

BASELINE = (
    "POST /chat HTTP/1.1\r\n"
    "Host: fixture.internal\r\n"
    "Cookie: sepp-session=boot-token\r\n"
    "Content-Type: application/json\r\n"
    "\r\n"
    '{"message": "x"}'
).encode()
ROTATED = BASELINE.replace(b"boot-token", b"fresh-token")


class FakeAdapter:
    def build_body(self, prompt, model, *, fresh_conversation, conversation_id):
        return json.dumps({"message": prompt}).encode()

    def parse(self, raw):
        try:
            return json.loads(raw)["answer"], "ok", "conv-1"
        except (ValueError, KeyError, TypeError):
            return "", "error", None

    def default_model(self):
        return "stub"


class FakeTransport:
    """Returns a scripted (code, body) sequence shared across instances (a
    retry must see the NEXT response, not a replay). A 401 simulates
    concurrent credential rotation by rewriting baseline.req on disk, so the
    Target chat_turn rebuilds sees fresh credentials - exactly what
    harvest_target.py running in the background produces."""

    queue = iter([])

    def __init__(self, target):
        self.target = target

    def send(self, body, timeout_s=170):
        code, raw = next(FakeTransport.queue)
        if code == "401":
            (self.target.dir / "baseline.req").write_bytes(ROTATED)
        return code, raw


class ChatTurnRetryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        tdir = root / "targets" / "fx"
        tdir.mkdir(parents=True)
        (tdir / "target.json").write_text("{}")
        (tdir / "baseline.req").write_bytes(BASELINE)
        self.root = root
        self.patches = [
            patch.object(transport, "DATA_ROOT", root),
            patch.object(transport, "Transport", FakeTransport),
            patch.object(transport.time, "sleep", lambda *_: None),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(self._teardown)

    def _teardown(self):
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()

    def target(self):
        with patch.object(transport, "DATA_ROOT", self.root):
            return transport.Target("fx")

    def test_rotated_credentials_are_picked_up_on_401(self):
        FakeTransport.queue = iter([("401", b"expired"), ("200", b'{"answer": "fresh"}')])
        status, text, conv_id, kind = chat_turn(
            FakeAdapter(), self.target(), "hi",
            fresh_conversation=True, want_kind=True)
        self.assertEqual(status, "200")
        self.assertEqual(text, "fresh")
        self.assertEqual(kind, "ok")

    def test_unchanged_credentials_mean_no_retry(self):
        FakeTransport.queue = iter([("401", b"expired")])  # no rotation this time
        with patch.object(FakeTransport, "send",
                          lambda self, body, timeout_s=170: next(FakeTransport.queue)):
            status, text, _, kind = chat_turn(
                FakeAdapter(), self.target(), "hi",
                fresh_conversation=True, want_kind=True)
        self.assertEqual((status, kind), ("401", "error"))
        # unchanged cookie jar on disk: the rotated-write side effect never ran
        self.assertIn(b"boot-token", self.root.joinpath(
            "targets/fx/baseline.req").read_bytes())

    def test_non_auth_errors_never_retry(self):
        FakeTransport.queue = iter([("500", b"boom")])
        status, _, _, kind = chat_turn(
            FakeAdapter(), self.target(), "hi",
            fresh_conversation=True, want_kind=True)
        self.assertEqual((status, kind), ("500", "error"))

    def test_plain_caller_tuple_unchanged(self):
        FakeTransport.queue = iter([("200", b'{"answer": "ok"}')])
        result = chat_turn(FakeAdapter(), self.target(), "hi",
                           fresh_conversation=True)
        self.assertEqual(len(result), 3)  # legacy 3-tuple preserved


if __name__ == "__main__":
    unittest.main()
