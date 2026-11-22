"""Loader + validator for per-target adapters (data/targets/<slug>/adapter.py).

See drivers/ADAPTER_CONTRACT.md for the contract these modules implement.
Keeps every consumer (openai_shim, chatdriver compat, scripts) free of
target knowledge: a consumer names a slug, this module hands back a
validated adapter object.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))  # allow both `drivers.X` and direct imports

from transport import Target, DATA_ROOT  # noqa: E402

REQUIRED_FUNCS = ("build_body", "parse", "default_model")


class AdapterError(RuntimeError):
    pass


def adapter_path(slug: str) -> pathlib.Path:
    return DATA_ROOT / "targets" / slug / "adapter.py"


def load_adapter(slug: str):
    """Import + validate data/targets/<slug>/adapter.py. Raises AdapterError
    with a precise message when the target has no adapter or violates the
    contract (check drivers/ADAPTER_CONTRACT.md)."""
    p = adapter_path(slug)
    if not p.exists():
        raise AdapterError(
            f"no adapter for target '{slug}': {p} missing — write it per "
            f"drivers/ADAPTER_CONTRACT.md (orient on captured history first)")
    spec = importlib.util.spec_from_file_location(f"{slug}_adapter", p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # pragma: no cover - surface adapter bugs verbatim
        raise AdapterError(f"adapter '{slug}' crashed on import: {e}") from e
    missing = [f for f in REQUIRED_FUNCS if not callable(getattr(mod, f, None))]
    if missing:
        raise AdapterError(
            f"adapter '{slug}' violates the contract — missing/uncallable: "
            f"{', '.join(missing)} (see drivers/ADAPTER_CONTRACT.md)")
    return mod
