"""Run with the testbed app's dependencies installed."""
import importlib
import os
import sqlite3
import unittest
from unittest.mock import patch

from app import main, venice


class DispatchTest(unittest.TestCase):
    def test_normal_and_heavy_use_selected_backend(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT)")
        db.row_factory = sqlite3.Row
        for name in ["mock", "venice", "ollama"]:
            with self.subTest(backend=name), patch.dict(os.environ, TESTBED_BACKEND=name):
                importlib.reload(main)
                with patch.object(venice, "chat_completion", return_value="provider reply") as remote, \
                        patch.object(main, "current_user", return_value={"id": 1}):
                    chat = main.llm_chat(main.ChatReq(message="Hi"), db, "fixture")
                    heavy = main.llm_heavy(main.HeavyReq(prompt="Hello"), db, "fixture", "fixture")
                self.assertEqual(chat["backend"], name)
                if name == "mock":
                    self.assertEqual(remote.call_count, 0)
                    self.assertTrue(chat["answer"].startswith("[chat]"))
                    self.assertTrue(heavy["output"].startswith("[job:"))
                else:
                    self.assertEqual(remote.call_count, 2)
                    self.assertEqual(chat["answer"], "provider reply")
                    self.assertEqual(heavy["output"], "provider reply")


if __name__ == "__main__":
    unittest.main()
