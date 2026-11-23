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

    def test_ollama_profile_isolated_from_default_testbed_and_host_credentials(self):
        base = Path(__file__).with_name("docker-compose.yml")
        output = subprocess.check_output(
            ["docker", "compose", "--env-file", os.devnull, "-f", str(base), "-f",
             str(base.with_name("ollama.yml")), "config", "--format", "json"],
            env={**os.environ, "OPENAI_API_KEY": "fixture-only", "TESTBED_MODEL": "other-model",
                 "OPENAI_BASE_URL": "https://provider.invalid", "TESTBED_BACKEND": "venice"},
        )
        config = json.loads(output)
        service = config["services"]["llm-app"]
        self.assertEqual(service["environment"]["TESTBED_BACKEND"], "ollama")
        self.assertEqual(service["environment"]["TESTBED_MODEL"], "qwen2.5:7b")
        self.assertEqual(service["environment"]["OPENAI_BASE_URL"], "http://host.docker.internal:11434/v1")
        self.assertEqual(service["environment"]["OPENAI_API_KEY"], "")
        self.assertEqual(config["volumes"]["testbed-data"]["name"], "sepp-testbed-local_testbed-data")
        self.assertEqual([(p["host_ip"], p["published"]) for p in service["ports"]], [("127.0.0.1", "8766")])
