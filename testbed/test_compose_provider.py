"""Check the service environment with docker compose config (no running daemon needed)."""
import json
import os
from pathlib import Path
import subprocess
import unittest


class ProviderSettingsTest(unittest.TestCase):
    def test_provider_settings_and_mock_defaults(self):
        settings = {
            "OPENAI_API_KEY": "fixture-only",
            "OPENAI_BASE_URL": "http://provider:9000/v1",
            "TESTBED_MODEL": "fixture-model",
            "TESTBED_VENICE_MAX_TOKENS": "7",
            "TESTBED_VENICE_CHAR_BUDGET": "350",
        }
        compose = Path(__file__).with_name("docker-compose.yml")
        env = {k: v for k, v in os.environ.items() if k not in settings and k != "TESTBED_BACKEND"}

        def render(overrides):
            result = subprocess.check_output(
                ["docker", "compose", "--env-file", os.devnull, "-f", str(compose), "config", "--format", "json"],
                env={**env, **overrides},
            )
            return json.loads(result)["services"]["llm-app"]["environment"]

        defaults = render({})
        self.assertEqual(defaults["TESTBED_BACKEND"], "mock")
        self.assertTrue(all(defaults.get(key) is None for key in settings))
        configured = render({**settings, "TESTBED_BACKEND": "venice"})
        self.assertEqual(configured["TESTBED_BACKEND"], "venice")
        for key in settings:
            self.assertTrue(configured.get(key) == settings[key], key)
