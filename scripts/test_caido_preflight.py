import subprocess
import unittest
from unittest.mock import patch

from check_caido import ready


class CaidoPreflightTests(unittest.TestCase):
    @patch("check_caido.subprocess.run")
    def test_status_requires_success_and_both_positive_checks(self, run):
        for status, code, expected in [
            ("Auth: OK (expires later)\nInstance: OK (v0.59.1)\n", 0, True),
            ("Auth: FAIL\n", 0, False),
            ("Auth: OK\nInstance: FAIL\n", 0, False),
            ("Auth: OK\n", 0, False),
            ("Auth: OK\nInstance: OK\n", 1, False),
        ]:
            with self.subTest(status=status, code=code):
                run.return_value = subprocess.CompletedProcess([], code, stdout=status)
                self.assertEqual(ready(), expected)
        self.assertEqual(run.call_args.args[0][0], "caido-cli")

    @patch("check_caido.subprocess.run")
    def test_cli_missing_or_hung_blocks(self, run):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("caido-cli", 5)):
            run.side_effect = error
            self.assertFalse(ready())
