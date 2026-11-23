"""Provider configuration, output identity, token cap, and reservation checks."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).parent / "llm-app"))
from app import venice


class ProviderClientTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = patch.multiple(venice, BACKEND="ollama", MODEL="qwen2.5:7b",
                                  MAX_TOKENS=7, CHAR_BUDGET=30,
                                  SPEND_PATH=str(Path(tmp.name) / "spend.json"))
        settings.start()
        self.addCleanup(settings.stop)
        env = patch.dict(os.environ, {"OPENAI_BASE_URL": "http://localhost:11434/v1",
                                      "OPENAI_API_KEY": "fixture-only"})
        env.start()
        self.addCleanup(env.stop)
        self.open_local = self.start_patch("urllib.request.OpenerDirector.open")
        self.open_venice = self.start_patch("urllib.request.urlopen")
        self.messages = [{"role": "user", "content": "hi"}]
        self.response = {"model": "qwen2.5:7b", "id": "unit-completion",
                         "choices": [{"message": {"content": "Hi."}, "finish_reason": "stop"}]}

    def start_patch(self, name):
        target = patch(name)
        mock = target.start()
        self.addCleanup(target.stop)
        return mock

    def test_clamp_and_budget_boundaries(self):
        self.open_local.return_value = io.BytesIO(json.dumps(self.response).encode())
        provider = {}
        self.assertEqual(venice.chat_completion(self.messages, max_tokens=500, provider=provider), "Hi.")
        self.assertEqual(provider, {"backend": "ollama", "model": "qwen2.5:7b",
                                    "completion_id": "unit-completion", "finish_reason": "stop"})
        request = self.open_local.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(json.loads(request.data)["max_tokens"], 7)
        self.assertFalse(request.has_header("Authorization"))
        self.assertEqual(venice._spend()["chars"], 30)
        with self.assertRaises(venice.BudgetExhausted):
            venice.chat_completion(self.messages)
        self.open_local.assert_called_once()

    def test_only_local_zero_budget_skips_reservation(self):
        with patch.object(venice, "CHAR_BUDGET", 0):
            self.open_local.return_value = io.BytesIO(json.dumps(self.response).encode())
            self.assertEqual(venice.chat_completion(self.messages), "Hi.")
            with patch.object(venice, "BACKEND", "venice"), self.assertRaises(venice.BudgetExhausted):
                venice.chat_completion(self.messages)
        self.open_venice.assert_not_called()

    def test_unavailable_or_misconfigured_local_provider(self):
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "https://provider.invalid/v1"}):
            with self.assertRaisesRegex(venice.BackendError, "local OPENAI_BASE_URL"):
                venice.chat_completion(self.messages)
        self.open_local.assert_not_called()
        self.open_local.side_effect = urllib.error.URLError("fixture offline")
        with self.assertRaisesRegex(venice.BackendError, "ollama unreachable"):
            venice.chat_completion(self.messages)
        self.open_venice.assert_not_called()

    def test_malformed_response_or_model_mismatch(self):
        with patch.object(venice, "CHAR_BUDGET", 0):
            for body in [b"bad json", b"{}", json.dumps({**self.response, "model": "wrong"}).encode()]:
                with self.subTest(body=body):
                    self.open_local.return_value = io.BytesIO(body)
                    with self.assertRaises(venice.BackendError):
                        venice.chat_completion(self.messages)

    def test_venice_uses_configured_key_and_model(self):
        self.open_venice.return_value = io.BytesIO(json.dumps(self.response).encode())
        with patch.object(venice, "BACKEND", "venice"), patch.object(venice, "MODEL", "venice-test"):
            self.assertEqual(venice.chat_completion(self.messages), "Hi.")
        request = self.open_venice.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture-only")
        self.assertEqual(json.loads(request.data)["model"], "venice-test")
        self.open_local.assert_not_called()
