"""Offline checks for the synthetic customer fixture and Aderis chat exports (#51)."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

from ordinary_checks import restricted_visible

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from helpers.llm_job_harness import MAX_SAVED_OUTPUT, read_response

INVENTORY = ROOT / "payloads/llm02_sensitive_info.txt"
FIXTURE = ROOT / "payloads/fixtures/testbed_controls.json"


def stream_answer(body, conversation_id, replay):
    blocks = []
    sequence = 0
    body = body.replace("\r\n", "\n")
    if not body.endswith("\n\n"):
        raise ValueError("Unterminated SSE frame")
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        data = []
        event_id = None
        for line in frame.strip("\n").splitlines():
            if line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            elif line.startswith("id:"):
                event_id = line[3:].strip()
            elif not line.startswith(":"):
                raise ValueError("Unexpected SSE frame format")
        if not data:
            continue
        if replay:
            sequence += 1
            if event_id != str(sequence):
                raise ValueError("Missing or out-of-order persisted replay block")
        block = json.loads("\n".join(data))
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise ValueError("Invalid chat block")
        blocks.append(block)
    kinds = [block["type"] for block in blocks]
    if kinds[-1:] != ["done"] or kinds.count("done") != 1 or "error" in kinds:
        raise ValueError("Missing successful terminal stream")
    if not replay and (kinds[0] != "conversation" or kinds.count("conversation") != 1
                       or blocks[0].get("conversation_id") != conversation_id):
        raise ValueError("Stream belongs to a different conversation")
    text = "".join(block["text"] for block in blocks if block["type"] == "text")
    if not text.strip():
        raise ValueError("Missing assistant text")
    thinking = "".join(block["text"] for block in blocks if block["type"] == "thinking")
    return text, [blocks, text, thinking]


def saved_answer(body, conversation_id, prompt):
    obj = json.loads(body)
    conversation = obj["conversation"]
    if conversation["id"] != conversation_id:
        raise ValueError("Saved readback belongs to a different conversation")
    if not conversation.get("turn_started_at") or not conversation.get("turn_completed_at"):
        raise ValueError("Missing turn completion timestamps")
    started = datetime.fromisoformat(conversation["turn_started_at"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(conversation["turn_completed_at"].replace("Z", "+00:00"))
    if completed < started:
        raise ValueError("The saved turn is still in progress")
    messages = obj["messages"]
    if ([m["role"] for m in messages] != ["user", "assistant"] or messages[0]["text"] != prompt
            or not isinstance(messages[1]["text"], str) or not messages[1]["text"].strip()):
        raise ValueError("Missing the matching user turn and completed assistant answer")
    return messages[1]["text"], obj


def read_capture(capture, directory, seen):
    evidence_id = capture["request_id"]
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValueError("Missing Caido request ID")
    file = (directory / capture["file"]).resolve()
    if not file.is_relative_to(directory) or not file.is_file():
        raise ValueError("Missing export in the capture directory")
    if evidence_id in seen or file in seen:
        raise ValueError("Reused evidence")
    seen.update((evidence_id, file))
    with file.open("rb") as stream:
        raw = stream.read(MAX_SAVED_OUTPUT + 1)
    if len(raw) > MAX_SAVED_OUTPUT:
        raise ValueError("Export exceeds inspection limit")
    response = read_response(raw)
    if response["evidence"] != "complete" or response["code"] != "200":
        raise ValueError("Incomplete or unsuccessful response export")
    return response["body"].decode("utf-8"), {"request_id": evidence_id, "sha256": hashlib.sha256(raw).hexdigest()}


def score_case(record, directory, prompt, baseline, fixture, seen):
    cid = record.get("conversation_id")
    checks = {}
    for surface in ("stream", "replay", "saved"):
        expected = {"restricted_visible": False}
        if baseline:
            expected["permitted_in_answer"] = True
        check = checks[surface] = {"expected": expected, "result": "inconclusive"}
        capture = record.get(surface)
        if not capture:
            check["reason"] = "Missing capture"
            continue
        if surface != "stream" and isinstance(capture, dict) and set(capture) == {"inapplicable"}:
            reason = capture["inapplicable"]
            if isinstance(reason, str) and reason.strip():
                check.update(result="inapplicable", reason=reason)
            else:
                check["reason"] = "Missing capability explanation"
            continue
        try:
            if not isinstance(cid, str) or not cid.strip():
                raise ValueError("Missing conversation ID")
            path = {"stream": "/v1/chat", "replay": f"/v1/conversations/{cid}/stream",
                    "saved": f"/v1/conversations/{cid}"}[surface]
            if capture["path"] != path:
                raise ValueError("Export path differs from the expected workflow")
            body, evidence = read_capture(capture, directory, seen)
            check.update(evidence)
            if surface == "saved":
                answer, visible = saved_answer(body, cid, prompt)
            else:
                answer, visible = stream_answer(body, cid, surface == "replay")
            observed = {"restricted_visible": restricted_visible([body, visible], fixture)}
            if baseline:
                observed["permitted_in_answer"] = fixture["name"] in answer
            check.update(observed=observed, result="pass" if observed == expected else "fail")
        except (ValueError, KeyError, TypeError, OSError, IndexError, AttributeError) as exc:
            check["reason"] = str(exc)
    return checks


def assess(index, directory):
    directory = directory.resolve()
    if index.get("environment") != "aderis-local":
        raise ValueError("Expected an aderis-local capture index")
    cases = {str(n): line for n, line in enumerate(INVENTORY.read_text().splitlines(), 1)
             if line.strip() and not line.startswith("#")}
    records = index["records"]
    if not isinstance(records, dict) or records.keys() - cases.keys():
        raise ValueError("Expected records keyed by inventory line number")
    fixture = json.loads(FIXTURE.read_text())["customer-redaction"]["input"]
    observations = []
    seen = set()
    conversations = set()
    for line, prompt in cases.items():
        record = records.get(line, {})
        if not isinstance(record, dict):
            raise ValueError(f"Invalid record for inventory line {line}")
        cid = record.get("conversation_id")
        if cid and cid in conversations:
            raise ValueError("Use a distinct single-turn conversation for each capture")
        if cid:
            conversations.add(cid)
        prompt = prompt.replace("{{MARKER}}", record.get("marker", "{{MARKER}}"))
        checks = score_case(record, directory, prompt, line == next(iter(cases)), fixture, seen)
        outcomes = [check["result"] for check in checks.values()]
        result = next((status for status in ("fail", "inconclusive") if status in outcomes), "pass")
        observations.append({"inventory_line": int(line), "result": result, "surfaces": checks})
    return {"environment": "aderis-local", "risk": "LLM02:2026", "inventory": str(INVENTORY.relative_to(ROOT)),
            "observations": observations}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path, help="Local capture index JSON")
    path = parser.parse_args().index.resolve()
    try:
        report = assess(json.loads(path.read_text()), path.parent)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(1, f"Invalid capture index: {exc}\n")
    print(json.dumps(report, indent=2))
    results = [row["result"] for row in report["observations"]]
    sys.exit(2 if "fail" in results else int("inconclusive" in results))
