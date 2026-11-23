import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "testbed/controls/conversation_targets/notes-fixture"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


onboard = load("onboard", ROOT / "scripts/onboard_target.py")
conversation = load("conversation", ROOT / "testbed/controls/conversation_fixture.py")


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / FIXTURE.name
        shutil.copytree(FIXTURE, self.folder)
        self.path = self.folder / "readiness.json"
        self.data = json.loads(self.path.read_text())

    def verify(self):
        self.path.write_text(json.dumps(self.data))
        return onboard.check(self.folder)

    def test_snapshots_match_mock_workflow_and_adapter(self):
        chats = conversation.NoteConversations()
        key = chats.fresh("Planning notes")
        chats.add_note(key, "Monday delivery")
        for kind in ("ok", "declined"):
            snapshot = self.folder / "fixtures" / self.data["fixtures"][kind]["file"]
            self.assertEqual(chats.summarize(key), json.loads(snapshot.read_text()))
            if kind == "ok":
                key = chats.reset(key)
        result = self.verify()
        self.assertEqual(result["status"], "fixture-verified")
        self.assertEqual(result["requests_used"], 0)
        self.assertEqual({o["kind"] for o in result["observations"]}, {"ok", "declined"})

    def test_missing_corrupt_placeholder_and_mismatched_fixtures_fail_closed(self):
        declined = self.data["fixtures"]["declined"]
        for change in ({"file": "absent.json"}, {"file": "ok.json"}, {"file": "../readiness.json"},
                       {"evidence_id": "TBD"}, {"evidence_id": "replace_me"}, {"evidence_id": "local-notes-ok"},
                       {"completion": "truncated"}, {"delivery": "http", "http_status": 403},
                       {"expected_text": "unrelated output"}):
            with self.subTest(change=change):
                self.data["fixtures"]["declined"] = {**declined, **change}
                self.assertEqual(self.verify()["status"], "unverified")
        self.data["fixtures"]["declined"] = declined
        self.data["fixtures"]["ok"]["expected_text"] = "[REDACTED]"
        (self.folder / "fixtures/ok.json").write_text('{"answer":"[REDACTED]"}')
        self.assertEqual(self.verify()["status"], "unverified")
        (self.folder / "fixtures/ok.json").write_text("{")
        self.assertEqual(self.verify()["status"], "unverified")
        del self.data["fixtures"]["declined"]
        self.assertEqual(self.verify()["status"], "unverified")

    def test_http_delivery_and_target_identity(self):
        for item in self.data["fixtures"].values():
            item.update(delivery="http", http_status=200)
        result = self.verify()
        self.assertEqual(result["status"], "fixture-verified")
        for item in result["observations"]:
            self.assertEqual(item["http_status"], 200)
        self.data["target"] = "another-target"
        self.assertEqual(self.verify()["status"], "unverified")

    def test_inventory_does_not_execute_adapter_and_check_exits_nonzero(self):
        (self.folder / "adapter.py").write_text('raise RuntimeError("sensitive detail")')
        cmd = [sys.executable, str(ROOT / "scripts/onboard_target.py"),
               "--targets-dir", str(self.folder.parent)]
        rows = json.loads(subprocess.check_output(cmd + ["list"]))
        self.assertEqual(rows, [{"target": "notes-fixture", "adapter_present": True, "backend": "mock",
                                 "workflow": "note-summary", "profile": "present"}])
        result = subprocess.run(cmd + ["check", "notes-fixture"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "unverified")
        self.assertNotIn("sensitive detail", result.stdout + result.stderr)

    def test_register_only_copies_data_and_preserves_existing_target(self):
        marker = self.folder.parent / "executed"
        (self.folder / "adapter.py").write_text(f"open({str(marker)!r}, 'w').close()")
        (self.folder / "baseline.req").write_text("excluded")
        self.data["unused_field"] = "excluded"
        self.path.write_text(json.dumps(self.data))
        target = self.folder.parent / "registry" / "notes-fixture"
        cmd = [sys.executable, str(ROOT / "scripts/onboard_target.py"), "--targets-dir", str(target.parent)]
        register = cmd + ["register", target.name, "--fixtures", str(self.folder)]
        self.assertTrue(json.loads(subprocess.check_output(register))["registered"])
        self.assertEqual(json.loads(subprocess.check_output(cmd + ["check", target.name]))["status"], "fixture-verified")
        self.assertFalse(marker.exists())
        self.assertEqual(sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()),
                         ["fixtures/declined.json", "fixtures/ok.json", "readiness.json"])
        manifest = (target / "readiness.json").read_bytes()
        self.assertNotIn(b"excluded", manifest)
        self.assertNotEqual(subprocess.run(register, capture_output=True).returncode, 0)
        self.assertEqual((target / "readiness.json").read_bytes(), manifest)

    def test_invalid_bundles_publish_nothing(self):
        target = self.folder.parent / "registry" / "notes-fixture"
        capture = self.folder / "fixtures/ok.json"
        for body in ('{"answer":"a","answer":"b"}', '{"answer":"works","header":"extra"}', '[]'):
            with self.subTest(body=body):
                capture.write_text(body)
                self.assertEqual(onboard.register(target, self.folder)["status"], "unverified")
                self.assertFalse(target.exists())
        capture.unlink()
        capture.symlink_to(FIXTURE / "fixtures/ok.json")
        self.assertEqual(onboard.register(target, self.folder)["status"], "unverified")
        self.assertFalse(target.exists())


class ConversationTest(unittest.TestCase):
    def test_identical_openings_reuse_and_reset(self):
        report = conversation.exercise()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["requests_used"], 0)
        self.assertEqual(len(report["observations"]), 10)
        self.assertTrue(all(o["status"] == "passed" for o in report["observations"]))

    def test_reset_accepts_new_notes_and_unknown_session_is_rejected(self):
        chats = conversation.NoteConversations()
        key = chats.fresh("Original")
        replacement = chats.reset(key)
        with self.assertRaises(KeyError):
            chats.add_note(key, "expired")
        chats.add_note(replacement, "Replacement")
        text = chats.summarize(replacement)["answer"]
        self.assertIn("Replacement", text)
        self.assertNotIn("Original", text)
        with self.assertRaises(KeyError):
            chats.add_note("absent", "unassigned note")

    def test_final_write_at_cap_and_decline_both_retire_session(self):
        chats = conversation.NoteConversations()
        key = chats.fresh()
        for turn in range(chats.TURN_CAP):
            chats.add_note(key, f"Note {turn}")
        with self.assertRaises(KeyError):
            chats.summarize(key)
        empty = chats.fresh()
        self.assertIn("decline", chats.summarize(empty))
        with self.assertRaises(KeyError):
            chats.add_note(empty, "expired")


if __name__ == "__main__":
    unittest.main()
