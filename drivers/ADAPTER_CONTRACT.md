"""Contract for per-target adapters (`data/targets/<slug>/adapter.py`).

The SEPP/garak stack is target-blind: transport (Caido proxy, pacing, CA) and
the OpenAI façade are shared code. Everything that varies per target — the
request-body dialect and the response grammar — lives in ONE small module
inside the target's data directory:

    data/targets/<slug>/adapter.py

Required functions (exact signatures):

    def build_body(prompt: str, model: str,
                   fresh_conversation: bool,
                   conversation_id: str | None) -> bytes
        Serialize one chat turn into the target's request-body dialect.
        fresh_conversation=True  -> omit any conversation/thread id so the
                                    server starts a new one (probe isolation)
        fresh_conversation=False -> continue `conversation_id` (or the
                                    target's stored default when None)
        Return the EXACT bytes to send as the HTTP body.

    def parse(raw: str) -> tuple[str, str, str | None]
        Extract (text, kind, conversation_id) from the raw HTTP response
        BODY (status code arrives separately; headers stripped).
        kind is one of: "ok" | "declined" | "error"
          ok       - assistant text worth scoring (garak)
          declined - a soft refusal/filter answer (still text, scored, but
                     flagged - e.g. content-filter oracles)
          error    - no usable text (transport/protocol failure)
        conversation_id: server-minted thread id if the response carries one
        (enables follow-ups), else None.

    def default_model() -> str
        Model name to advertise/use when the caller sends an empty model
        (e.g. garak without --model_name).

Conventions:
- Fixtures for OFFLINE verification live in `data/targets/<slug>/fixtures/`
  (e.g. `sse-ok.txt`, `decline.txt`). `scripts/check_adapters.py` runs every
  adapter against its fixtures with zero network access.
- Adapters are EXECUTABLE CODE in the data dir. They run with repo
  privileges: anything the agent wrote there gets a human glance before its
  first live run.
- A session experimenting with dialect variants does so in ITS workspace
  (data/workspaces/<id>/); only the proven winner is promoted here.
"""
