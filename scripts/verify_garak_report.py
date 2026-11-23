#!/usr/bin/env python3
"""Verify that a garak JSONL report contains evaluated output and completed."""
import argparse
import json
from pathlib import Path


def verify(path):
    run = None
    evaluated = False
    complete = False
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("unexpected content in report")
            kind = record.get("entry_type")
            if complete and kind != "digest":
                raise ValueError("unexpected record after completion")
            if kind == "init":
                if run is not None or not isinstance(record.get("run"), str) or not record["run"]:
                    raise ValueError("invalid run identity")
                run = record["run"]
            elif kind == "eval":
                count = record.get("total_evaluated")
                if run and type(count) is int and count > 0:
                    evaluated = True
            elif kind == "completion":
                if not run or record.get("run") != run:
                    raise ValueError("completion does not match run")
                complete = True
    if not complete or not evaluated:
        raise ValueError("missing completion or evaluated output")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        verify(args.report)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Canary report invalid: {args.report}: {exc}\n")
    print(f"Canary report: {args.report}")
