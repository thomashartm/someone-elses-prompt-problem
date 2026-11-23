"""Exercise the canary wrapper with an isolated adapter and stand-ins for network clients."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CanaryIdentityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("scripts", ".venv-garak/bin", "bin", "data/targets/fixture"):
            (self.root / name).mkdir(parents=True)
        shutil.copytree(ROOT / "scripts", self.root / "scripts", dirs_exist_ok=True)
        shutil.copytree(ROOT / "drivers", self.root / "drivers", ignore=shutil.ignore_patterns("__pycache__"))
        folder = self.root / "data/targets/fixture"
        (folder / "adapter.py").write_text(
            'def default_model(): return "fixture-model"\n'
            'def build_body(prompt, model, *, fresh_conversation, conversation_id): return str(conversation_id).encode()\n'
            'def parse(body): return body, "ok", None\n'
        )
        (folder / "target.json").write_text('{"token_exp": 4102444800}')
        (folder / "baseline.req").touch()
        self.stub("scripts/harvest_target.py", 'touch "$FIXTURE/harvest"; exit 1')
        self.stub(".venv-garak/bin/uvicorn", 'echo "$$" > "$FIXTURE/pid"; exec sleep 30')
        self.stub("bin/curl", '''touch "$FIXTURE/network"
test -s "$FIXTURE/pid" || exit 1
printf '{"ok":true,"target":"%s","model":"fixture-model","pid":%s}' "${HEALTH_TARGET:-$CAIDO_TARGET}" "$(cat "$FIXTURE/pid")"''')
        self.stub(".venv-garak/bin/garak", '''printf "%s\\n" "$CAIDO_TARGET" "$@" > "$FIXTURE/invocation"
while [ "$#" -gt 1 ]; do
  if [ "$1" = --report_prefix ]; then
    printf '%s\\n' '{"entry_type":"init","run":"fixture"}' '{"entry_type":"eval","total_evaluated":1}' \\
      '{"entry_type":"completion","run":"fixture"}' > "$2.report.jsonl"
    break
  fi
  shift
done''')


    def stub(self, name, body):
        path = self.root / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def invoke(self, **overrides):
        env = {key: value for key, value in os.environ.items()
               if key not in ("TARGET", "CAIDO_TARGET", "CAIDO_DATA_DIR", "HEALTH_TARGET")}
        env.update(FIXTURE=str(self.root), PATH=str(self.root / "bin") + ":" + env["PATH"], **overrides)
        return subprocess.run(["bash", str(self.root / "scripts/run_garak_canary.sh")], env=env,
                              cwd=self.root, capture_output=True, text=True, timeout=15)

    def test_conflict_stops_before_adapter_credentials_or_network(self):
        result = self.invoke(TARGET="fixture", CAIDO_TARGET="other")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disagree", result.stderr)
        for file in ("harvest", "network", "invocation"):
            self.assertFalse((self.root / file).exists())

    def test_consistent_target_and_model(self):
        result = self.invoke(CAIDO_TARGET="fixture")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / "invocation").read_text().splitlines()
        self.assertEqual(args[0], "fixture")
        self.assertEqual(args[args.index("-n") + 1], "fixture-model")
        self.assertIn("target=fixture model=fixture-model", result.stdout)

    def test_stale_credentials_stop_before_network(self):
        (self.root / "data/targets/fixture/target.json").write_text(json.dumps({"token_exp": 1}))
        result = self.invoke(TARGET="fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale", result.stderr)
        self.assertFalse((self.root / "network").exists())

    def test_existing_shim_with_wrong_identity_is_rejected(self):
        result = self.invoke(TARGET="fixture", HEALTH_TARGET="other")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "invocation").exists())
