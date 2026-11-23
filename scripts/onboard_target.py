#!/usr/bin/env python3
"""Register, inventory and verify local target profiles using sanitized responses."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import tempfile
from pathlib import Path

TARGETS = Path(__file__).resolve().parents[1] / "data" / "targets"
SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*")
PLACEHOLDERS = {"", "...", "…", "todo", "tbd", "unknown", "n/a", "placeholder", "redacted", "replace me"}


def supplied(value):
    if not isinstance(value, str):
        return False
    normalized = value.lower().strip().strip("<>[]{}").strip().replace("_", " ").replace("-", " ")
    return normalized not in PLACEHOLDERS


def profile(folder, target=None):
    data = json.loads((folder / "readiness.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("target") != (target or folder.name):
        raise ValueError("profile target differs from the selected directory")
    if not all(supplied(data.get(key)) for key in ("backend", "workflow")):
        raise ValueError("profile requires a backend and workflow")
    return data


def inventory(folder):
    row = {"target": folder.name, "adapter_present": (folder / "adapter.py").is_file()}
    try:
        data = profile(folder)
        row.update(backend=data["backend"], workflow=data["workflow"], profile="present")
        if "parser" in data:
            row["parser"] = data["parser"]
    except (OSError, ValueError):
        row["profile"] = "missing-or-invalid"
    return row


def json_reply(raw):
    # Preserve duplicate keys so a second field cannot hide inside an accepted body.
    pairs = json.loads(raw, object_pairs_hook=lambda p: p)
    if not raw.lstrip().startswith("{") or len(pairs) != 1:
        raise ValueError("expected one response text field")
    name, text = pairs[0]
    kind = {"answer": "ok", "decline": "declined"}.get(name)
    if not kind or not isinstance(text, str):
        raise ValueError("unknown response field or type")
    return text, kind, None


def response_parser(folder, data):
    if "parser" in data:
        if data["parser"] == "json-answer-decline":
            return json_reply
        raise ValueError("unknown response parser")
    # Existing local adapters are trusted code. Registration always uses json_reply.
    spec = importlib.util.spec_from_file_location("readiness_adapter", folder / "adapter.py")
    adapter = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(adapter)
    except Exception:
        raise ValueError("reviewed local adapter could not be loaded") from None
    if not callable(getattr(adapter, "parse", None)):
        raise ValueError("adapter must implement parse")
    return adapter.parse


def receipt(folder):
    return {"target": folder.name, "backend": None, "workflow": None,
            "source": "offline-adapter-fixtures", "status": "unverified",
            "requests_used": 0, "observations": []}


def check(folder):
    result = receipt(folder)
    try:
        data = profile(folder)
        result.update(backend=data["backend"], workflow=data["workflow"])
        fixtures = data.get("fixtures")
        if not isinstance(fixtures, dict):
            raise ValueError("success and declined fixtures required")
        parse = response_parser(folder, data)
        ids, digests = set(), set()
        for kind in ("ok", "declined"):
            entry = fixtures.get(kind)
            if not isinstance(entry, dict):
                raise ValueError(f"{kind} fixture is missing")
            eid = entry.get("evidence_id")
            if not supplied(eid) or not SLUG.fullmatch(eid) or eid in ids:
                raise ValueError(f"{kind} requires a distinct evidence ID")
            ids.add(eid)
            code = entry.get("http_status")
            delivery = entry.get("delivery")
            if entry.get("completion") != "complete":
                raise ValueError(f"{kind} fixture is incomplete")
            if delivery == "http":
                if type(code) is not int or not 200 <= code < 300:
                    raise ValueError(f"{kind} requires a successful HTTP exchange")
            elif delivery != "in-process" or code is not None:
                raise ValueError(f"{kind} requires a valid delivery method")
            filename = entry.get("file")
            if not supplied(filename) or not SLUG.fullmatch(filename):
                raise ValueError(f"{kind} requires a fixture filename")
            path = (folder / "fixtures" / filename).resolve()
            if path.parent != (folder / "fixtures").resolve():
                raise ValueError(f"{kind} file must be inside fixtures")
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if not raw.strip() or digest in digests:
                raise ValueError(f"{kind} requires a distinct non-empty response")
            digests.add(digest)
            try:
                text, parsed_kind, _ = parse(raw.decode("utf-8"))
            except Exception:
                raise ValueError(f"{kind} adapter could not parse the fixture") from None
            if parsed_kind != kind or not supplied(text) or text != entry.get("expected_text"):
                raise ValueError(f"{kind} parsed outcome or text differs from expectation")
            result["observations"].append({"kind": kind, "evidence_id": eid, "sha256": digest, "status": "passed",
                                           "delivery": delivery, "http_status": code, "completion": "complete"})
        result["status"] = "fixture-verified"
    except (FileNotFoundError, PermissionError):
        result["reason"] = "required profile, adapter or fixture is missing or unreadable"
    except (UnicodeError, json.JSONDecodeError):
        result["reason"] = "invalid profile encoding or JSON"
    except ValueError as err:
        result["reason"] = str(err)
    except Exception:
        result["reason"] = "adapter or fixture could not be loaded"
    return result


def register(folder, bundle):
    result = receipt(folder)
    if folder.exists() or folder.is_symlink():
        return dict(result, reason="target already exists")
    try:
        profile_path = bundle / "readiness.json"
        if profile_path.is_symlink() or not profile_path.is_file() or (bundle / "fixtures").is_symlink():
            raise ValueError("bundle profile and fixtures must be regular local files")
        data = profile(bundle, folder.name)
        manifest = {key: data[key] for key in ("target", "backend", "workflow")}
        manifest.update(parser="json-answer-decline", fixtures={})
        folder.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder.parent, prefix=".register-") as temp:
            stage = Path(temp) / folder.name
            (stage / "fixtures").mkdir(parents=True)
            for kind in ("ok", "declined"):
                entry = data["fixtures"][kind]
                name = kind + ".json"
                if entry.get("file") != name:
                    raise ValueError(f"{kind} must reference fixtures/{name}")
                source = bundle / "fixtures" / name
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"{kind} fixture must be a regular local file")
                (stage / "fixtures" / name).write_bytes(source.read_bytes())
                allowed = ("file", "evidence_id", "delivery", "completion", "http_status", "expected_text")
                manifest["fixtures"][kind] = {key: entry[key] for key in allowed if key in entry}
            (stage / "readiness.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            result = check(stage)
            if result["status"] == "fixture-verified":
                stage.rename(folder)
                result["registered"] = True
    except (KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError):
        result.update(status="unverified", reason="invalid fixture bundle")
    except ValueError as err:
        result.update(status="unverified", reason=str(err))
    except OSError:
        result.update(status="unverified", reason="could not read the bundle or publish the registration")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets-dir", type=Path, default=TARGETS)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="list target/profile presence without executing adapters")
    actions.add_parser("check", help="check a reviewed local adapter").add_argument("target")
    add = actions.add_parser("register", help="register a sanitized pair of JSON response fixtures")
    add.add_argument("target")
    add.add_argument("--fixtures", required=True, type=Path, help="bundle containing readiness.json and fixtures/")
    args = parser.parse_args()
    if args.action == "list":
        folders = args.targets_dir.iterdir() if args.targets_dir.is_dir() else []
        print(json.dumps([inventory(p) for p in sorted(folders) if p.is_dir() and SLUG.fullmatch(p.name)], indent=2))
        return 0
    if not SLUG.fullmatch(args.target):
        parser.error("target must be a directory name beginning with a letter or digit")
    folder = args.targets_dir / args.target
    result = register(folder, args.fixtures) if args.action == "register" else check(folder)
    print(json.dumps(result, indent=2))
    return int(result["status"] != "fixture-verified")


if __name__ == "__main__":
    raise SystemExit(main())
