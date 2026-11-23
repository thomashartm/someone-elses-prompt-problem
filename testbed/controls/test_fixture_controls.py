"""Regression checks for browser observation and per-run request accounting."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

import httpx

from run_fixture_controls import inspect_dom


class BrowserObserverTest(unittest.TestCase):
    def test_records_normal_stylesheet_request(self):
        url = "http://styles.invalid/fixture.css"
        response = httpx.Response(
            200, headers={"content-type": "text/html"},
            text=f'<link rel="stylesheet" href="{url}"><pre>Fixture inventory</pre>',
            request=httpx.Request("GET", "http://testserver/llm/render"),
        )
        observed = inspect_dom(response)
        self.assertEqual(observed["requests_from_page"], [url])
        self.assertEqual(observed["text"], ["Fixture inventory"])


class RequestBudgetTest(unittest.TestCase):
    def test_quota_check_needs_both_application_responses(self):
        runner = Path(__file__).with_name("run_fixture_controls.py")
        for limit, expected_status, exit_code in [(2, "inconclusive", 1), (3, "pass", 0)]:
            with self.subTest(limit=limit):
                result = subprocess.run(
                    [sys.executable, str(runner), "--case", "quota-rejection", "--max-requests", str(limit)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, exit_code, result.stderr)
                capture = json.loads(result.stdout)
                self.assertEqual(capture["requests_used"], limit)
                self.assertEqual(capture["observations"][0]["result"], expected_status)


if __name__ == "__main__":
    unittest.main()
