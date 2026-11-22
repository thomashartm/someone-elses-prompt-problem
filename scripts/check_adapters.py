#!/usr/bin/env python3
"""Offline adapter checker — zero network, zero tokens.

For every data/targets/<slug>/adapter.py:
  1. contract check: build_body / parse / default_model present + callable
  2. build_body: returns bytes, deterministic, embeds/omits conversation id
     as the contract demands (fresh vs continued)
  3. parse against fixtures/: *_ok* fixture -> kind "ok" with non-empty text;
     *decline* -> kind "declined"; *incomplete*/others -> any parse outcome
     but no crash (weak fixtures are allowed, crashes are not)

Run: python3 scripts/check_adapters.py [slug ...]     (default: all targets)
Exit 0 = every adapter healthy. Used by smoke.sh-worthy pipelines and safe
to run anywhere (pure offline).
"""
import importlib.util
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = REPO / "data" / "targets"
REQUIRED = ("build_body", "parse", "default_model")


def load(slug: str):
    p = TARGETS / slug / "adapter.py"
    spec = importlib.util.spec_from_file_location(f"{slug}_adapter_check", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(slug: str) -> list[str]:
    errs = []
    p = TARGETS / slug / "adapter.py"
    if not p.exists():
        return [f"{slug}: adapter.py missing"]
    mod = load(slug)
    for fn in REQUIRED:
        if not callable(getattr(mod, fn, None)):
            errs.append(f"{slug}: contract violation — {fn}() missing (drivers/ADAPTER_CONTRACT.md)")

    bb = getattr(mod, "build_body", None)
    pr = getattr(mod, "parse", None)
    if bb:
        try:
            fresh = bb("PING", "m1", fresh_conversation=True, conversation_id=None)
            cont = bb("PING", "m1", fresh_conversation=False, conversation_id="c-123")
            if not isinstance(fresh, bytes) or not isinstance(cont, bytes):
                errs.append(f"{slug}: build_body must return bytes")
            elif fresh == cont:
                errs.append(f"{slug}: build_body identical for fresh vs continued conversation "
                            f"— probe isolation lever looks broken")
            # determinism
            if bb("PING", "m1", fresh_conversation=True, conversation_id=None) != fresh:
                errs.append(f"{slug}: build_body not deterministic")
        except Exception as e:
            errs.append(f"{slug}: build_body crashed: {e}")

    fx_dir = TARGETS / slug / "fixtures"
    if pr and fx_dir.is_dir():
        for fx in sorted(fx_dir.iterdir()):
            if fx.suffix not in (".txt", ".sse"):
                continue
            try:
                text, kind, conv = pr(fx.read_text())
            except Exception as e:
                errs.append(f"{slug}: parse crashed on {fx.name}: {e}")
                continue
            name = fx.stem.lower()
            if "ok" in name:
                if kind != "ok" or not text:
                    errs.append(f"{slug}: {fx.name} expected kind=ok with text, got kind={kind} text={text[:40]!r}")
            if "decline" in name:
                if kind != "declined":
                    errs.append(f"{slug}: {fx.name} expected kind=declined, got kind={kind}")
            if "conversation" in name and not conv:
                errs.append(f"{slug}: {fx.name} carries a conversation id but parse returned None")
    return errs


def main() -> int:
    slugs = sys.argv[1:]
    if not slugs:
        slugs = sorted(d.name for d in TARGETS.iterdir() if d.is_dir())
    failures = 0
    for slug in slugs:
        errs = check(slug)
        if errs:
            failures += len(errs)
            for e in errs:
                print(f"FAIL {e}")
        else:
            print(f"OK   {slug}: contract + fixtures green")
    print(f"\n{failures} problem(s)" if failures else "\nall adapters healthy")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
