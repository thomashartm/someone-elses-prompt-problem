"""COMPATIBILITY FACADE — the pre-adapter target driver API.

Historical name: `drivers/chatdriver.py` (Pilot-only). Target logic now
lives in data/targets/<slug>/adapter.py (drivers/ADAPTER_CONTRACT.md) and
the engine in drivers/transport.py. This module re-exports the old surface
(chat, LAST_CONV) backed by the new stack so session scripts and guides
written against it keep working:

    from chatdriver import chat
    status, text = chat("Say READY", conv=False)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from transport import RATE_PAUSE_S  # noqa: E402,F401  (re-exported)
from transport import Target, chat_turn  # noqa: E402
from adapter_loader import load_adapter  # noqa: E402

SLUG = os.environ.get("CAIDO_TARGET", "testbed")
_ADAPTER = load_adapter(SLUG)
_TARGET = Target(SLUG)

# module-level mirror of the adapter's last seen conversation id
LAST_CONV = None


def chat(message, tries=1, model=None, timeout_s=170, conv=None, pause_after=True):
    """Old signature, new engine. conv semantics preserved:
    False -> fresh server-side thread (probe isolation);
    None  -> target default; "id" -> explicit thread.
    Returns (status, text). Statuses: "200", "200-empty", other HTTP codes, "ERR".
    NOTE: pacing is intrinsic to transport now (always-on); `pause_after`
    and `tries` are accepted for compatibility and effectively advisory.
    """
    global LAST_CONV
    fresh = conv is False
    status, text, conv_id = chat_turn(
        _ADAPTER, _TARGET, message,
        fresh_conversation=fresh,
        conversation_id=(conv if isinstance(conv, str) else None),
        model=model, timeout_s=timeout_s,
    )
    LAST_CONV = conv_id or LAST_CONV
    return status, text


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "Say READY and nothing else."
    s, t = chat(msg)
    print("STATUS:", s)
    print(t[:2000])
