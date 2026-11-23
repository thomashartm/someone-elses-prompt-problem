import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import llm_job_harness as harness


def frame(body=b'', status=200, declared=None):
    size = len(body) if declared is None else declared
    head = f'{status} text/plain {size}B\n---\n'.encode()
    return head + (body + b'\n' if body else b'')


class ResponseEvidenceTests(unittest.TestCase):
    def test_complete_capture_preserves_tail(self):
        body = b'x' * 2100 + b'SAMPLE'
        response = harness.read_response(frame(body))
        self.assertEqual(response['evidence'], 'complete')
        self.assertEqual(response['body'], body)
        self.assertEqual(response['code'], '200')

    def test_saved_response_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.txt"
            command = [sys.executable, harness.__file__, "inspect", str(path), "--reference", "SAMPLE"]
            for saved, evidence, present in [
                (frame(b'x' * 4096 + b'SAMPLE'), 'complete', True),
                (frame(b'nothing here'), 'complete', False),
                (frame(b'partial', declared=100), 'incomplete', None),
                (b'x' * (harness.MAX_SAVED_OUTPUT + 1), 'incomplete', None),
            ]:
                path.write_bytes(saved)
                proc = subprocess.run(command, capture_output=True, check=False)
                result = json.loads(proc.stdout)
                self.assertEqual(proc.returncode, int(evidence != 'complete'))
                self.assertEqual(result['evidence'], evidence)
                self.assertEqual(result.get('reference_present'), present)

    def test_size_and_framing(self):
        for body in (b'', b'ends with newline\n', 'Résumé'.encode(), b'[...truncated, 65B total]'):
            with self.subTest(body=body):
                self.assertEqual(harness.read_response(frame(body))['body'], body)
        for output in (b'', b'(no response)\n', b'200 text/plain 0B\n',
                       frame(b'short', declared=50)):
            self.assertEqual(harness.read_response(output)['evidence'], 'incomplete')

    def test_clipped_capture_is_unscorable(self):
        output = frame(b'x' * 2000 + b'\n[...truncated, 2070B total]', declared=2070)
        response = harness.read_response(output)
        self.assertEqual(response['evidence'], 'truncated')
        self.assertNotIn('body', response)

    def test_run_stops_and_records_failed_evidence(self):
        failures = [
            (subprocess.CompletedProcess([], 7, b'partial', b'private detail'), 'tool_error'),
            (subprocess.TimeoutExpired('caido-cli', 60), 'tool_error'),
            (subprocess.CompletedProcess([], 0, b'(no response)\n', b''), 'incomplete'),
            (subprocess.CompletedProcess([], 0, frame(b'x\n[...truncated, 10B total]', declared=10), b''), 'truncated'),
        ]
        for failure, expected in failures:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'inputs').write_text('Daily report\nWeekly report\n')
                config = dict(endpoint='https://fixture.invalid/', headers={}, body_template='example',
                              payloads_file=str(root / 'inputs'), pause_seconds=0)
                path = root / 'config.json'
                path.write_text(json.dumps(config))
                options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch.object(harness.subprocess, 'run', **options) as cli, patch.object(harness, 'DATA_REPORTS', root), contextlib.redirect_stdout(io.StringIO()):
                    result = harness.cmd_run(SimpleNamespace(config=path))
                self.assertNotEqual(result, 0)
                cli.assert_called_once()
                argv = cli.call_args.args[0]
                self.assertEqual(argv[argv.index('--body-limit') + 1], str(harness.CLI_BODY_LIMIT))
                report = json.loads(next(root.glob('llmharness-*.json')).read_text())
                row, = report['results']
                self.assertEqual(row['evidence'], expected)
                self.assertIsNone(row['signals'])
                self.assertNotIn('private detail', str(report))
