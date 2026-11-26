"""Workspace control-plane: per-hunt isolation, potency bonds, close+cleanup.

Run: python3 -m unittest scripts.test_workspace_control
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import workspace as ws  # noqa: E402


def run(subprocess_args):
    """Invoke workspace.main() with argv; returns (exit, parsed-json|None)."""
    buf = io.StringIO()
    argv = ["workspace.py", *subprocess_args]
    with patch.object(sys, "argv", argv), \
         redirect_stdout(buf):
        code = 0
        try:
            sys_mod = ws.main()
            code = sys_mod if isinstance(sys_mod, int) else 0
        except SystemExit as exc:  # argparse errors
            code = exc.code or 0
    out = buf.getvalue().strip()
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = None
    return code, parsed


class WorkspaceControlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"SEPP_WORKSPACES_DIR": self._tmp.name})
        self.env.start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.env.stop)
        self.day = ws.time.strftime("%Y%m%d")

    def session(self, sid):
        return json.loads((Path(self._tmp.name) / sid / "session.json").read_text())

    # ---- per-hunt isolation ------------------------------------------
    def test_hunt_flag_creates_isolated_workspace(self):
        rc, a = run(["ensure", "--host", "Api.Example.com", "--url", "https://api.example.com",
                     "--hunt", "Perimeter Sweep"])
        self.assertEqual(rc, 0)
        legacy_rc, b = run(["ensure", "--host", "api.example.com", "--url", "https://api.example.com"])
        self.assertEqual(legacy_rc, 0)
        self.assertEqual(a["id"], f"{self.day}-api.example.com--perimeter-sweep")
        self.assertEqual(b["id"], f"{self.day}-api.example.com")
        self.assertNotEqual(a["id"], b["id"])  # parallel hunts never share dirs
        self.assertTrue(Path(a["path"]).is_dir())

    def test_same_hunt_reensures_is_idempotent(self):
        _, first = run(["ensure", "--host", "h.t", "--url", "https://h.t", "--hunt", "one"])
        _, again = run(["ensure", "--host", "h.t", "--url", "https://h.t/x", "--hunt", "one"])
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(again["targets"], 2)  # accumulated, same isolated dir

    # ---- potency bonds -------------------------------------------------
    def test_potency_recorded_and_mutatable_mid_hunt(self):
        _, made = run(["ensure", "--host", "h.t", "--url", "https://h.t",
                       "--hunt", "p1", "--potency", "stealth"])
        self.assertEqual(made["potency"], "stealth")
        _, bumped = run(["ensure", "--host", "h.t", "--url", "https://h.t",
                         "--hunt", "p1", "--potency", "full"])
        self.assertEqual(bumped["potency"], "full")
        self.assertEqual(self.session(made["id"])["potency"], "full")

    def test_invalid_potency_rejected_without_side_effects(self):
        rc, out = run(["ensure", "--host", "h.t", "--url", "https://h.t", "--potency", "rage"])
        self.assertIn(rc, (1, 2))  # 1 = JSON error path, 2 = argparse choice rejection
        self.assertFalse(list(Path(self._tmp.name).iterdir()))  # nothing created

    # ---- close + cleanup ------------------------------------------------
    def test_finish_marks_closed_but_deletes_nothing(self):
        _, made = run(["ensure", "--host", "h.t", "--url", "https://h.t", "--hunt", "d1"])
        rc, fin = run(["finish", made["id"]])
        self.assertEqual(rc, 0)
        self.assertEqual(fin["status"], "closed")
        self.assertEqual(self.session(made["id"])["status"], "closed")
        self.assertTrue(Path(made["path"]).exists())

    def test_prune_finished_selector_hits_only_closed(self):
        _, open_ws = run(["ensure", "--host", "h.t", "--url", "https://h.t", "--hunt", "open"])
        _, closed_ws = run(["ensure", "--host", "h.t", "--url", "https://h.t", "--hunt", "closed"])
        run(["finish", closed_ws["id"]])
        run(["prune", "--days", "0", "--finished"])             # dry-run default
        rc, applied = run(["prune", "--days", "0", "--finished", "--apply"])
        self.assertEqual(rc, 0)
        self.assertIn(closed_ws["id"], applied["removed"])
        self.assertNotIn(open_ws["id"], applied["removed"] + applied["candidates"])
        self.assertFalse(Path(closed_ws["path"]).exists())
        self.assertTrue(Path(open_ws["path"]).exists())

    def test_prune_by_id_and_host_selectors(self):
        _, a = run(["ensure", "--host", "aa.t", "--url", "https://aa.t", "--hunt", "x"])
        _, b = run(["ensure", "--host", "bb.t", "--url", "https://bb.t", "--hunt", "y"])
        rc, sel = run(["prune", "--days", "0", "--id", a["id"], "--apply"])
        self.assertEqual(sel["removed"], [a["id"]])
        self.assertTrue(Path(b["path"]).exists())


if __name__ == "__main__":
    unittest.main()
