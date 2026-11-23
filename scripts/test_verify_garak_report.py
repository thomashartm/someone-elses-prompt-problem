import json
from pathlib import Path
import tempfile
import unittest

from verify_garak_report import verify


class GarakReportTest(unittest.TestCase):
    def test_completion_requires_evaluated_output_from_the_same_run(self):
        start = {"entry_type": "init", "run": "fixture"}
        evaluated = {"entry_type": "eval", "total_evaluated": 1}
        end = {"entry_type": "completion", "run": "fixture"}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.jsonl"
            with self.assertRaises(FileNotFoundError):
                verify(path)
            for records in ([], [start, evaluated], [start, end],
                            [start, dict(evaluated, total_evaluated=0), end],
                            [start, evaluated, dict(end, run="stale")], [start, evaluated, end, {}]):
                with self.subTest(records=records):
                    path.write_text("".join(json.dumps(row) + "\n" for row in records))
                    with self.assertRaises(ValueError):
                        verify(path)
            path.write_text("".join(json.dumps(row) + "\n" for row in (start, evaluated, end, {"entry_type": "digest"})))
            verify(path)
