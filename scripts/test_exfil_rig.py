import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for entry in (HERE, ROOT / "drivers"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from canary_listener import Journal, serve  # noqa: E402
from prove_exfil import hit_matches  # noqa: E402


class JournalTests(unittest.TestCase):
    def test_append_then_read_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / "j.jsonl")
            journal.append({"method": "GET", "path": "/a"})
            journal.append({"method": "POST", "path": "/b"})
            lines = journal.lines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["path"], "/b")

    def test_read_on_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(Journal(Path(tmp) / "absent.jsonl").lines(), [])

    def test_appends_from_two_threads_all_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / "j.jsonl")
            def spam(i):
                for n in range(20):
                    journal.append({"n": n, "t": i})
            threads = [threading.Thread(target=spam, args=(i,)) for i in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(journal.lines()), 80)


class ListenerTests(unittest.TestCase):
    def test_hit_is_journalled_with_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / "j.jsonl")
            server, port = serve("127.0.0.1", 0, journal)
            server.daemon_threads = True
            server.timeout = 0.05
            def hit():
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/exfil?d=SEC-abc123", timeout=5).read()
            thread = threading.Thread(target=hit)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not journal.lines():
                server.handle_request()
            thread.join(5)
            server.server_close()
            lines = journal.lines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["path"], "/exfil")
            self.assertIn("SEC-abc123", record["query"])
            self.assertEqual(record["method"], "GET")


class HitMatchTests(unittest.TestCase):
    def test_match_is_confined_to_path_plus_query(self):
        lines = [
            json.dumps({"path": "/exfil", "query": "d=SEC-abc123"}),
            json.dumps({"path": "/other", "query": "x=1"}),
            json.dumps({"user_agent": "SEC-abc123"}),  # body/header fields must not match
        ]
        self.assertEqual(len(hit_matches(lines, "SEC-abc123")), 1)

    def test_invalid_lines_are_skipped(self):
        lines = ["not-json", json.dumps({"path": "/x", "query": "d=SEC-1"})]
        self.assertEqual(len(hit_matches(lines, "SEC-1")), 1)


class ServeBindingTests(unittest.TestCase):
    def test_ephemeral_and_fixed_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_a = Journal(Path(tmp) / "a.jsonl")
            server_pinned, port = serve("127.0.0.1", 0, journal_a)
            server_free = serve("127.0.0.1", 0, Journal(Path(tmp) / "b.jsonl"))[0]
            try:
                self.assertTrue(1024 <= port <= 65535)
                self.assertNotEqual(port, server_free.server_address[1])
            finally:
                server_pinned.server_close()
                server_free.server_close()


if __name__ == "__main__":
    unittest.main()
