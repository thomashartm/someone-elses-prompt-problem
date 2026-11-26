#!/usr/bin/env python3
"""Register, inventory and verify local target profiles using sanitized responses.

Wire a LIVE endpoint as a new target (concept P1, target-agnostic onboarding):

    onboard_target.py wire --slug <slug> --url <url> [--dialect json|sse]
                           [--baseline FILE] [--from-request CAIDO_ID]
                           [--header 'Name: value']... [--force]

Creates data/targets/<slug>/{adapter.py,target.json,baseline.req,fixtures/}
from a dialect template and appends host→slug to data/targets/index.json.
No source file outside data/, testbed/ and docs learns the host.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import tempfile
from pathlib import Path

TARGETS = Path(__file__).resolve().parents[1] / "data" / "targets"
INDEX = TARGETS / "index.json"
SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*")
PLACEHOLDERS = {"", "...", "…", "todo", "tbd", "unknown", "n/a", "placeholder", "redacted", "replace me"}

# dialect templates honouring drivers/ADAPTER_CONTRACT.md. No host is baked in.
ADAPTER_TEMPLATES = {
    "json": '''"""Auto-generated adapter (dialect: json, single answer field).
Reviewed-by: <human glance before first live run>."""
from __future__ import annotations
import json


def build_body(prompt: str, model: str, *, fresh_conversation: bool,
               conversation_id: str | None) -> bytes:
    body = {"message": prompt}
    if not fresh_conversation and conversation_id:
        body["conversation_id"] = conversation_id
    return json.dumps(body).encode()


def parse(raw: str) -> tuple[str, str, str | None]:
    """Extract (text, kind, conversation_id) from a JSON body."""
    try:
        data = json.loads(raw)
    except ValueError:
        return "", "error", None
    if not isinstance(data, dict):
        return "", "error", None
    if "answer" in data and isinstance(data["answer"], str):
        return data["answer"], "ok", data.get("conversation_id")
    if "decline" in data and isinstance(data["decline"], str):
        return data["decline"], "declined", data.get("conversation_id")
    return "", "error", None


def default_model() -> str:
    return "default"
''',
    "sse": '''"""Auto-generated adapter (dialect: sse, data: {...} lines).
Reviewed-by: <human glance before first live run>."""
from __future__ import annotations
import json


def build_body(prompt: str, model: str, *, fresh_conversation: bool,
               conversation_id: str | None) -> bytes:
    body = {"message": prompt, "stream": True}
    if not fresh_conversation and conversation_id:
        body["conversation_id"] = conversation_id
    return json.dumps(body).encode()


def parse(raw: str) -> tuple[str, str, str | None]:
    """Concatenate SSE data-line deltas into one assistant text."""
    text_parts, conv = [], None
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload in ("[DONE]", ""):
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        delta = event.get("delta") or event.get("answer") or event.get("text") or ""
        if delta:
            text_parts.append(str(delta))
        if event.get("decline"):
            return str(event["decline"]), "declined", event.get("conversation_id")
        if event.get("conversation_id"):
            conv = event["conversation_id"]
    if not text_parts:
        return "", "error", conv
    return "".join(text_parts), "ok", conv


def default_model() -> str:
    return "default"
''',
}

FIXTURE_OK = {"answer": "synthetic success answer from onboarding", "conversation_id": "conv-fixtures-1"}
FIXTURE_DECLINE = {"decline": "synthetic decline: content filter engaged"}


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


def wire_index_add(host: str, slug: str) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    index = {}
    if INDEX.exists():
        try:
            index = json.loads(INDEX.read_text())
        except ValueError:
            index = {}
    index[host] = slug
    INDEX.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")


def wire(slug: str, url: str, dialect: str, baseline: Path | None,
         from_request: str | None, headers: list[str], force: bool) -> dict:
    """Concept P1: scaffold a live target under data/targets/<slug>/."""
    from urllib.parse import urlsplit

    folder = TARGETS / slug
    if folder.exists() or folder.is_symlink():
        if not force:
            return {"status": "refused", "reason": "target already exists (use --force to overwrite)"}
        import shutil
        shutil.rmtree(folder)
    if not url.startswith(("http://", "https://")):
        return {"status": "refused", "reason": "url must be http(s)"}
    parts = urlsplit(url)
    if not parts.hostname:
        return {"status": "refused", "reason": "url carries no host"}
    if dialect not in ADAPTER_TEMPLATES:
        return {"status": "refused", "reason": f"dialect must be one of {sorted(ADAPTER_TEMPLATES)}"}

    folder.mkdir(parents=True)
    (folder / "adapter.py").write_text(ADAPTER_TEMPLATES[dialect], encoding="utf-8")
    (folder / "target.json").write_text(json.dumps(
        {"url": url, "dialect": dialect, "onboarded": "wire"}, indent=2) + "\n", encoding="utf-8")

    if baseline:
        (folder / "baseline.req").write_bytes(Path(baseline).read_bytes())
    elif from_request:
        import subprocess
        proc = subprocess.run(["caido-cli", "request", from_request], capture_output=True, timeout=30)
        if proc.returncode:
            return {"status": "failed", "reason": f"caido-cli request {from_request} failed",
                    "target_dir": str(folder)}
        (folder / "baseline.req").write_bytes(proc.stdout)
    else:
        host_hdr = parts.hostname + (f":{parts.port}" if parts.port else "")
        synthesized = (f"POST {parts.path or '/'} HTTP/1.1\r\n"
                       f"Host: {host_hdr}\r\n"
                       + "".join(f"{h}\r\n" for h in headers)
                       + "Content-Type: application/json\r\n\r\n")
        (folder / "baseline.req").write_text(synthesized, encoding="utf-8")

    fixtures = folder / "fixtures"
    fixtures.mkdir(exist_ok=True)
    ok_body = json.dumps(FIXTURE_OK) if dialect == "json" else "data: " + json.dumps(FIXTURE_OK) + "\n\n"
    decline_body = json.dumps(FIXTURE_DECLINE) if dialect == "json" else "data: " + json.dumps(FIXTURE_DECLINE) + "\n\n"
    (fixtures / "answer_ok.txt").write_text(ok_body, encoding="utf-8")
    (fixtures / "decline_notice.txt").write_text(decline_body, encoding="utf-8")

    wire_index_add(parts.netloc, slug)
    return {
        "status": "wired",
        "target_dir": str(folder),
        "next": [
            f"python3 scripts/check_adapters.py {slug}",
            "human review of adapter.py (executable code in data/)",
            f"python3 scripts/harvest_target.py --slug {slug}   # credentials expire in hours",
            f"TARGET={slug} scripts/run_garak_canary.sh",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets-dir", type=Path, default=TARGETS)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="list target/profile presence without executing adapters")
    actions.add_parser("check", help="check a reviewed local adapter").add_argument("target")
    add = actions.add_parser("register", help="register a sanitized pair of JSON response fixtures")
    add.add_argument("target")
    add.add_argument("--fixtures", required=True, type=Path, help="bundle containing readiness.json and fixtures/")
    wire_cmd = actions.add_parser("wire", help="onboard a LIVE endpoint as a new target (concept P1)")
    wire_cmd.add_argument("--slug", required=True)
    wire_cmd.add_argument("--url", required=True)
    wire_cmd.add_argument("--dialect", default="json", choices=("json", "sse"))
    wire_cmd.add_argument("--baseline", type=Path, help="copy this raw request as baseline.req")
    wire_cmd.add_argument("--from-request", help="extract baseline.req from this Caido request id")
    wire_cmd.add_argument("--header", action="append", default=[], dest="headers",
                          help="extra baseline header, repeatable ('Name: value')")
    wire_cmd.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.action == "list":
        folders = args.targets_dir.iterdir() if args.targets_dir.is_dir() else []
        print(json.dumps([inventory(p) for p in sorted(folders) if p.is_dir() and SLUG.fullmatch(p.name)], indent=2))
        return 0
    if args.action == "wire":
        if not SLUG.fullmatch(args.slug):
            parser.error("slug must begin with a letter or digit")
        result = wire(args.slug, args.url, args.dialect, args.baseline,
                      args.from_request, args.headers, args.force)
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") == "wired" else 1
    if not SLUG.fullmatch(args.target):
        parser.error("target must be a directory name beginning with a letter or digit")
    folder = args.targets_dir / args.target
    result = register(folder, args.fixtures) if args.action == "register" else check(folder)
    print(json.dumps(result, indent=2))
    return int(result["status"] != "fixture-verified")


if __name__ == "__main__":
    raise SystemExit(main())
