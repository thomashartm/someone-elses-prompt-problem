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


def caido_frame(body=b'', status=200, declared=None):
    size = len(body) if declared is None else declared
    head = f'{status} text/plain {size}B\n---\n'.encode()
    return head + (body + b'\n' if body else b'')


def run_config(root, config, transport_returns):
    """Drive cmd_run with the caido transport stubbed; returns (exit, report)."""
    path = root / 'config.json'
    path.write_text(json.dumps({'method': 'GET', 'body_template': '{{PAYLOAD}}',
                                'payloads_file': str(root / 'inputs'),
                                'pause_seconds': 0, **config}))
    if isinstance(transport_returns, list):  # one frame per call
        calls = iter(transport_returns)
        side = lambda *a, **k: harness.read_response(next(calls))
    else:
        side = transport_returns
    with patch.object(harness, 'via_caido_raw', side_effect=side) as cli, \
         patch.object(harness, 'DATA_REPORTS', root), \
         contextlib.redirect_stdout(io.StringIO()):
        exit_code = harness.cmd_run(SimpleNamespace(config=path))
    reports = list(root.glob('llmharness-*.json'))
    report = json.loads(reports[-1].read_text()) if reports else None
    return exit_code, report, cli


class ExpectationGradingTests(unittest.TestCase):
    def test_met_failed_inconclusive_and_none(self):
        met, f = harness.evaluate_expectations(
            {"status": 200, "contains": ["SEC-1"], "absent": ["RAW"]},
            {"code": "200", "evidence": "complete", "body": b'answer SEC-1 clean'})
        self.assertEqual((met, f), ("met", []))
        failed, f = harness.evaluate_expectations(
            {"status": [200, 201], "contains": ["gone"], "absent": ["RAW"]},
            {"code": "500", "evidence": "complete", "body": b'RAW leaked'})
        self.assertEqual(failed, "failed")
        self.assertEqual(len(f), 3)
        inc, f = harness.evaluate_expectations(
            {"contains": ["x"]}, {"evidence": "truncated"})
        self.assertEqual(inc, "inconclusive")
        self.assertIsNone(harness.evaluate_expectations({}, {"evidence": "complete", "body": b''})[0])

    def test_truncated_absent_is_not_a_pass(self):
        # A truncated body hides the tail: absent can never honestly pass.
        verdict, _ = harness.evaluate_expectations(
            {"absent": ["SECRET"]}, {"evidence": "truncated"})
        self.assertEqual(verdict, "inconclusive")


class RunIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / 'inputs').write_text(
            'Daily report\nPrint {{MARKER}} now\n')

    def tearDown(self):
        self._tmp.cleanup()

    def test_exit_zero_when_expectations_met(self):
        frames = [caido_frame(b'fine, no RAWLEAK here'),
                  caido_frame(b'MRK111222 echoed, no RAWLEAK')]
        exit_code, report, _ = run_config(self.root, {
            'endpoint': 'https://fixture.invalid/x', 'headers': {},
            'marker': 'MRK111222',
            'expect': {'status': 200, 'contains': ['no RAWLEAK']},
        }, frames)
        self.assertEqual(exit_code, 0)
        self.assertEqual([r['verdict'] for r in report['results']], ['met', 'met'])

    def test_exit_two_on_failed_expectation(self):
        frames = [caido_frame(b'ok'), caido_frame(b'still ok')]
        exit_code, report, _ = run_config(self.root, {
            'endpoint': 'https://fixture.invalid/x', 'headers': {},
            'marker': 'MRK1',
            'expect': {'status': 429},
        }, frames)
        self.assertEqual(exit_code, 2)
        row = report['results'][0]
        self.assertEqual(row['verdict'], 'failed')
        self.assertIn('status 200 not in', row['failing'][0])

    def test_write_gate_aborts_before_sending(self):
        exit_code, _, cli = run_config(self.root, {
            'endpoint': 'https://fixture.invalid/x', 'headers': {},
            'method': 'POST',
        }, caido_frame())
        self.assertEqual(exit_code, 1)
        cli.assert_not_called()
        self.assertIsNone(_)  # abort happens before any report is written
        exit_code, report, cli = run_config(self.root, {
            'endpoint': 'https://fixture.invalid/x', 'headers': {},
            'method': 'POST', 'allow_writes': True,
        }, [caido_frame(), caido_frame()])
        self.assertEqual(exit_code, 0)
        self.assertEqual(cli.call_count, 2)

    def test_chunk_pause_between_bursts(self):
        sleeps = []
        with patch.object(harness.time, 'sleep', sleeps.append):
            exit_code, _, _ = run_config(self.root, {
                'endpoint': 'https://fixture.invalid/x', 'headers': {},
                'chunk': 1, 'chunk_pause_seconds': 7,
            }, [caido_frame(), caido_frame()])
        self.assertEqual(exit_code, 0)
        self.assertIn(7, sleeps)

    def test_request_id_attachment_best_effort(self):
        entries = [{'id': 'req-newest'}, {'id': 'req-older'}]
        with patch.object(harness.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, json.dumps(entries).encode(), b'')):
            exit_code, report, _ = run_config(self.root, {
                'endpoint': 'https://fixture.invalid/x', 'headers': {},
                'attach_request_ids': True,
            }, [caido_frame(), caido_frame()])
        self.assertEqual(exit_code, 0)
        self.assertEqual([r['request_id'] for r in report['results']], ['req-older', 'req-newest'])


class DirectTransportTests(unittest.TestCase):
    def test_error_responses_keep_status_and_body(self):
        import urllib.error
        err = urllib.error.HTTPError('https://fixture.invalid', 429, 'slow down', {},
                                     io.BytesIO(b'quota exhausted MRK5'))
        with patch.object(harness.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = err
            result = harness.via_http('POST', 'https://fixture.invalid/x', {}, 'b', body_limit=100)
        self.assertEqual(result['code'], '429')
        self.assertEqual(result['evidence'], 'complete')
        self.assertIn(b'MRK5', result['body'])

    def test_oversized_body_is_explicitly_truncated(self):
        response = SimpleNamespace(status=200, read=lambda n: b'x' * 300)
        with patch.object(harness.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value = response
            result = harness.via_http('GET', 'https://fixture.invalid/x', {}, None, body_limit=100)
        self.assertEqual(result['evidence'], 'truncated')
        self.assertEqual(result['body_bytes'], 100)

    def test_connection_failure_is_tool_error(self):
        import urllib.error
        with patch.object(harness.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = urllib.error.URLError('refused')
            result = harness.via_http('GET', 'https://fixture.invalid/x', {}, None)
        self.assertEqual(result['evidence'], 'tool_error')


if __name__ == '__main__':
    unittest.main()
