"""DEPRECATED — moved to drivers/openai_shim.py (universal, CAIDO_TARGET-driven).

Kept so muscle-memory invocations (`uvicorn drivers.shim:app`) keep working;
prints a one-time pointer. Use:

    CAIDO_TARGET=<slug> .venv-garak/bin/uvicorn drivers.openai_shim:app --port 8399
"""
from __future__ import annotations

import sys
from pathlib import Path

print("drivers.shim is deprecated — use drivers.openai_shim (CAIDO_TARGET env)", file=sys.stderr)

from .openai_shim import app  # noqa: E402,F401
