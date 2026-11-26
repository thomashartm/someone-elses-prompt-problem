"""Regression coverage for the baseline.req header pipeline (issue #78).

harvest_target.py once laundered CRLF baselines to LF; Target then dropped
ALL headers (silent 401s). These tests pin the tolerant parsing either way,
plus the per-target rate-pause precedence (env > target.json > default).
Run: python3 -m unittest scripts.test_transport_target
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
from transport import Target  # noqa: E402


def crlf_baseline():
    return (
        "POST /chat HTTP/1.1\r\n"
        "Host: fixture.internal\r\n"
        "User-Agent: Mozilla/5.0 fixture\r\n"
        "Accept: application/json\r\n"
        "Referer: https://fixture.internal/app\r\n"
        "Origin: https://fixture.internal\r\n"
        "Cookie: sepp-session=fresh-jwt\r\n"
        "Authorization: Bearer abc\r\n"
        "Content-Type: application/json\r\n"
        "Connection: keep-alive\r\n"
        "\r\n"
        '{"message": "hi"}'
    ).encode()


def lf_baseline():
    return crlf_baseline().replace(b"\r\n", b"\n")


class TargetHeaderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def target(self, baseline_bytes, cfg=None):
        tdir = self.root / "targets" / "fx"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "target.json").write_text(json.dumps(cfg or {}))
        (tdir / "baseline.req").write_bytes(baseline_bytes)
        with patch.object(transport, "DATA_ROOT", self.root):
            return Target("fx")

    def test_crlf_baseline_keeps_session_headers(self):
        headers = self.target(crlf_baseline()).header_args
        self.assertIn("Cookie: sepp-session=fresh-jwt", headers)
        self.assertIn("Authorization: Bearer abc", headers)

    def test_lf_baseline_parses_identically(self):
        # the #78 failure mode: LF-only file used to yield header_args=[]
        self.assertEqual(
            self.target(lf_baseline()).header_args,
            self.target(crlf_baseline()).header_args,
        )

    def test_mixed_line_endings_parse(self):
        mixed = lf_baseline()[:-1] + b"\r\n"
        # 7 kept headers -> 14 list entries (flag+value pairs)
        self.assertEqual(len(self.target(mixed).header_args), 14)

    def test_drop_prefixes_still_filtered(self):
        joined = "|".join(self.target(crlf_baseline()).header_args)
        self.assertNotIn("Host:", joined)
        self.assertNotIn("Connection:", joined)
        self.assertNotIn("Content-Length:", joined)


class RatePausePrecedenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        tdir = self.root / "targets" / "fx"
        tdir.mkdir(parents=True)
        (tdir / "target.json").write_text(json.dumps({"rate_pause_s": 3}))
        (tdir / "baseline.req").write_bytes(crlf_baseline())

    def tearDown(self):
        self._tmp.cleanup()

    def paused(self, env=None, cfg=None):
        if cfg is not None:
            self.root.joinpath("targets/fx/target.json").write_text(json.dumps(cfg))
        with patch.object(transport, "DATA_ROOT", self.root):
            with patch.dict("os.environ", env or {}, clear=False):
                return Target("fx").rate_pause_s

    def test_precedence_env_then_target_then_constant(self):
        import os
        saved = os.environ.pop("SEPP_RATE_PAUSE", None)
        try:
            self.assertEqual(self.paused(), 3)                        # target.json
            self.assertEqual(self.paused({"SEPP_RATE_PAUSE": "7"}), 7.0)  # env wins
            # no env, no cfg key -> conservative default
            self.assertEqual(self.paused(cfg={}), transport.DEFAULT_RATE_PAUSE_S)
        finally:
            if saved is not None:
                os.environ["SEPP_RATE_PAUSE"] = saved

    def test_legacy_reexport_matches_env(self):
        with patch.dict("os.environ", {"SEPP_RATE_PAUSE": "5"}):
            import importlib
            importlib.reload(transport)
            self.assertEqual(transport.RATE_PAUSE_S, 5.0)
        importlib.reload(transport)  # restore for later tests in the process


if __name__ == "__main__":
    unittest.main()
