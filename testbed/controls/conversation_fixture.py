#!/usr/bin/env python3
"""Exercise ordinary note-summary conversations in the in-process testbed mock."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "llm-app"))
from app import engine


class NoteConversations:
    TURN_CAP = 4

    def __init__(self):
        self.sessions = {}

    def fresh(self, opening=None):
        conversation = uuid4().hex
        self.sessions[conversation] = {"notes": [], "turns": 0}
        if opening is not None:
            self.add_note(conversation, opening)
        return conversation

    def _turn(self, conversation):
        session = self.sessions[conversation]
        session["turns"] += 1
        if session["turns"] == self.TURN_CAP:
            del self.sessions[conversation]
        return session["notes"]

    def add_note(self, conversation, note):
        self._turn(conversation).append(note)

    def reset(self, conversation):
        del self.sessions[conversation]
        return self.fresh()

    def summarize(self, conversation):
        notes = self._turn(conversation)
        if not notes:
            self.sessions.pop(conversation, None)
            return {"decline": "Add a note before requesting a summary."}
        return {"answer": engine.respond("\n".join(notes), "Summarize my notes.", "",
                                         data_only=True, directive_enabled=False, persona="notes")}


def rejects_followup(chats, conversation):
    try:
        chats.summarize(conversation)
    except KeyError:
        return True
    return False


def exercise():
    chats = NoteConversations()
    opening = "Planning notes"
    left, right = chats.fresh(opening), chats.fresh(opening)
    chats.add_note(left, "Monday delivery")
    chats.add_note(right, "Friday review")
    first = chats.summarize(left)["answer"]
    second = chats.summarize(right)["answer"]
    replacement = chats.reset(left)
    retired_left = rejects_followup(chats, left)
    after_reset = chats.summarize(replacement)
    retired_decline = rejects_followup(chats, replacement)
    unaffected = chats.fresh("Other open notebook")
    fourth_turn = chats.summarize(right)["answer"]
    retired_at_cap = rejects_followup(chats, right)
    other_summary = chats.summarize(unaffected)["answer"]
    restarted = chats.fresh(opening)
    restarted_summary = chats.summarize(restarted)["answer"]
    checks = [
        ("distinct-fresh-conversations", left != right),
        ("reuse-first", opening in first and "Monday delivery" in first and "Friday review" not in first),
        ("reuse-second", opening in second and "Friday review" in second and "Monday delivery" not in second),
        ("reset-first", after_reset == {"decline": "Add a note before requesting a summary."}),
        ("explicit-reset-invalidates", replacement != left and retired_left),
        ("decline-invalidates", retired_decline),
        ("reset-isolation", fourth_turn == second),
        ("cap-invalidates", retired_at_cap),
        ("cap-isolation", "Other open notebook" in other_summary and "Friday review" not in other_summary),
        ("fresh-after-cap", restarted != right and opening in restarted_summary and "Friday review" not in restarted_summary),
    ]
    observations = [{"evidence_id": name, "completion": "complete", "status": "passed" if ok else "failed"}
                    for name, ok in checks]
    return {"target": "notes-fixture", "backend": "mock", "workflow": "note-summary",
            "source": "in-process-testbed-mock", "status": "passed" if all(ok for _, ok in checks) else "failed",
            "requests_used": 0, "observations": observations,
            "turn_cap": chats.TURN_CAP,
            "turns": {"first": first, "second": second, "after_reset": after_reset, "fourth_turn": fourth_turn,
                      "unaffected": other_summary, "fresh_after_cap": restarted_summary}}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    report = exercise()
    print(json.dumps(report, indent=2))
    raise SystemExit(int(report["status"] != "passed"))
