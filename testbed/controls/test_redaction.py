import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from redaction import FIXTURE, INVENTORY, assess


class RedactionAssessmentTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.fixture = json.loads(FIXTURE.read_text())["customer-redaction"]["input"]
        self.prompt = INVENTORY.read_text().splitlines()[2]
        self.events = [{"type": "text", "text": self.fixture["name"]}, {"type": "done"}]
        self.record = {"conversation_id": "turn1"}
        self.index = {"environment": "aderis-local", "records": {"3": self.record}}
        self.messages = [{"role": "user", "text": self.prompt},
                         {"role": "assistant", "text": self.fixture["name"]}]
        self.stream("stream", self.events)
        self.stream("replay", self.events)
        self.saved()

    def capture(self, surface, body, path):
        data = body.encode()
        (self.directory / surface).write_bytes(f"200 OK {len(data)}B\n---\n".encode() + data + b"\n")
        self.record[surface] = {"request_id": surface, "file": surface, "path": path}

    def stream(self, surface, events):
        if surface == "stream":
            events = [{"type": "conversation", "conversation_id": "turn1"}, *events]
        frames = []
        for seq, event in enumerate(events, 1):
            prefix = f"id: {seq}\n" if surface == "replay" else ""
            frames.append(prefix + "data: " + json.dumps(event) + "\n\n")
        path = "/v1/chat" if surface == "stream" else "/v1/conversations/turn1/stream"
        self.capture(surface, "".join(frames), path)

    def saved(self, cid="turn1", **extra):
        body = {"conversation": {"id": cid, "turn_started_at": "2026-11-24T09:00:00Z",
                                 "turn_completed_at": "2026-11-24T09:00:05Z"}, "messages": self.messages, **extra}
        self.capture("saved", json.dumps(body), "/v1/conversations/turn1")

    def assess(self):
        return assess(self.index, self.directory)["observations"][0]

    def test_ordinary_request_and_inventory_coverage(self):
        report = assess(self.index, self.directory)["observations"]
        self.assertEqual(report[0]["result"], "pass")
        self.assertEqual(len(report), 10)
        self.assertTrue(all(row["result"] == "inconclusive" for row in report[1:]))

    def test_completed_nonbaseline_can_withhold_the_permitted_field(self):
        self.index["records"] = {"5": self.record}
        self.record["marker"] = "fixture-marker"
        self.messages[0]["text"] = INVENTORY.read_text().splitlines()[4].replace("{{MARKER}}", "fixture-marker")
        self.messages[1]["text"] = "That information is unavailable."
        self.saved()
        refusal = [{"type": "text", "text": self.messages[1]["text"]}, {"type": "done"}]
        for surface in ("stream", "replay"):
            self.stream(surface, refusal)
        result = assess(self.index, self.directory)["observations"][1]
        self.assertEqual(result["result"], "pass")

    def test_split_stream_and_replay_only_disclosures(self):
        for surface in ("stream", "replay"):
            with self.subTest(surface=surface):
                email = self.fixture["email"]
                self.stream(surface, [self.events[0], {"type": "text", "text": email[:4]},
                                      {"type": "text", "text": email[4:]}, self.events[-1]])
                checks = self.assess()["surfaces"]
                self.assertEqual(checks[surface]["result"], "fail")
                self.assertEqual(checks["saved"]["result"], "pass")
                self.stream(surface, self.events)

    def test_spaced_email_in_tool_metadata_and_phone_only_in_saved_output(self):
        self.stream("stream", [self.events[0], {"type": "tool_result",
                    "content": {"contact": " ".join(self.fixture["email"])}}, self.events[-1]])
        self.saved(contact=self.fixture["phone"])
        checks = self.assess()["surfaces"]
        self.assertEqual([checks[name]["result"] for name in ("stream", "replay", "saved")],
                         ["fail", "pass", "fail"])

    def test_missing_permitted_value_fails_even_if_user_or_metadata_supplies_it(self):
        self.messages[1]["text"] = "Details unavailable"
        self.saved(title=self.fixture["name"])
        self.assertEqual(self.assess()["surfaces"]["saved"]["result"], "fail")

    def test_wrong_conversation_missing_terminal_and_truncated_export(self):
        self.stream("stream", self.events[:-1])
        self.saved(cid="other")
        export = self.directory / "replay"
        export.write_bytes(export.read_bytes()[:-12])
        self.assertTrue(all(c["result"] == "inconclusive" for c in self.assess()["surfaces"].values()))

    def test_copy_of_earlier_answer_cannot_supply_another_surface_or_turn(self):
        self.record["replay"]["file"] = "stream"
        self.messages.insert(0, {"role": "assistant", "text": self.fixture["name"]})
        self.saved()
        checks = self.assess()["surfaces"]
        self.assertEqual(checks["replay"]["result"], "inconclusive")
        self.assertEqual(checks["saved"]["result"], "inconclusive")

    def test_unsupported_readback_needs_a_reason(self):
        self.record["saved"] = {"inapplicable": ""}
        self.assertEqual(self.assess()["result"], "inconclusive")
        self.record["saved"] = {"inapplicable": "Reviewed local version has no transcript route"}
        self.assertEqual(self.assess()["surfaces"]["saved"]["result"], "inapplicable")

    def test_pending_saved_turn_cannot_pass(self):
        self.saved(conversation={"id": "turn1", "turn_started_at": "2026-11-24T09:00:00Z",
                                 "turn_completed_at": None})
        self.assertEqual(self.assess()["surfaces"]["saved"]["result"], "inconclusive")

    def test_explicit_stream_error_cannot_pass(self):
        self.stream("stream", [self.events[0], {"type": "error", "message": "failed"}, self.events[-1]])
        self.assertEqual(self.assess()["surfaces"]["stream"]["result"], "inconclusive")
