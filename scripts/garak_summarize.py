#!/usr/bin/env python3
"""Summarize an existing, completed garak JSONL report without invoking a generator."""
import argparse
import json
from pathlib import Path

from verify_garak_report import verify


def summarize(path):
    verify(path)
    totals, attempts = {}, set()
    generator, model = None, None
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            kind = row["entry_type"]
            if kind == "start_run setup":
                generator, model = row.get("plugins.target_type"), row.get("plugins.target_name")
            elif kind == "init":
                run = row["run"]
            elif kind == "attempt" and row.get("uuid"):
                attempts.add(row["uuid"])
            elif kind == "eval":
                key = (row["probe"], row["detector"])
                total, passed = row["total_evaluated"], row["passed"]
                if type(total) is not int or type(passed) is not int or not 0 <= passed <= total:
                    raise ValueError("invalid garak evaluation counts")
                counts = totals.setdefault(key, {"probe": key[0], "detector": key[1], "evaluated": 0, "passed": 0, "hits": 0})
                counts["evaluated"] += total
                counts["passed"] += passed
                counts["hits"] += total - passed
    return {"source": "garak-jsonl", "generator": generator, "model": model, "run_id": run, "distinct_attempts": len(attempts),
            "evaluations": list(totals.values())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(summarize(args.report), indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"Incomplete garak evidence: {exc}\n")
