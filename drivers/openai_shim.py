"""Universal OpenAI-compatible façade for garak (SEPP stack).

    POST /v1/chat/completions
    {messages:[{role,content},...], model?, ...}

TARGET-BLIND by design: the target is chosen by env
    CAIDO_TARGET=<slug>     (default: testbed)
and everything target-specific comes from the slug's adapter
(data/targets/<slug>/adapter.py, contract: drivers/ADAPTER_CONTRACT.md).

Behaviour (probe-hygiene policy, applies to any target):
- concatenates message contents into one prompt (all garak needs)
- conversation policy via SEPP_SHIM_CONV:
    fresh (default) EVERY request starts a new conversation (probe
      isolation: one poisoned probe must never cascade into the next,
      burned-thread lesson)
    reuse turns sharing the same first message continue one thread
      (multi-turn probes: dra/tap/goat); a declined turn drops the thread;
      SEPP_SHIM_CONV_MAX_TURNS (default 10) caps a thread's life
- declined/error turns are returned as plain assistant text with
  finish_reason="stop"/"error" so garak SCORES them instead of crashing
- pacing handled by drivers.transport (SEPP_RATE_PAUSE, default 10s)

Run:   CAIDO_TARGET=testbed .venv-garak/bin/uvicorn drivers.openai_shim:app --port 8399
garak: OPENAI_BASE_URL=http://127.0.0.1:8399/v1 OPENAI_API_KEY=x
       .venv-garak/bin/garak --model_type openai -n <slug> -p dan ...
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from transport import chat_turn, Target, ConvPool   # noqa: E402
from adapter_loader import load_adapter          # noqa: E402
from fastapi import FastAPI                      # noqa: E402
from pydantic import BaseModel                   # noqa: E402

SLUG = os.environ.get("CAIDO_TARGET", "testbed")


class Msg(BaseModel):
    role: str = "user"
    content: str = ""


class ChatReq(BaseModel):
    messages: list[Msg]
    model: str = ""
    temperature: float | None = None


def create_app(slug: str) -> FastAPI:
    adapter = load_adapter(slug)     # fail FAST at boot, not mid-scan
    target = Target(slug)
    pool = ConvPool(os.environ.get("SEPP_SHIM_CONV", "fresh"),
                    int(os.environ.get("SEPP_SHIM_CONV_MAX_TURNS", "10")))
    app = FastAPI(title=f"sepp-garak-shim[{slug}]")

    @app.post("/v1/chat/completions")
    def completions(req: ChatReq):
        prompt = "\n".join(m.content for m in req.messages if m.content)
        t0 = time.time()
        fresh, conv, turn, key = pool.plan(req.messages)
        status, text, conv_id, kind = chat_turn(
            adapter, target, prompt,
            fresh_conversation=fresh,
            conversation_id=conv,
            model=req.model or None,
            want_kind=True,
        )
        pool.observe(key, kind, conv_id, turn)
        body = {
            "id": f"chatcmpl-{slug}-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model or adapter.default_model(),
            "choices": [{
                "index": 0,
                # finish "stop" also for declined text: garak must score it
                "finish_reason": "stop" if text else "error",
                "message": {"role": "assistant", "content": text},
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "_sepp": {
                "target": slug,
                "status": status,
                "kind_hint": kind,
                "conv_mode": pool.mode,
                "conv_id": conv_id,
                "turn": turn,
                "elapsed_s": round(time.time() - t0, 1),
            },
        }
        return body

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "target": slug, "model": adapter.default_model(), "pid": os.getpid(),
                "adapter": "loaded", "last_conv": getattr(adapter, "LAST_CONV", None)}

    return app


app = create_app(SLUG)
