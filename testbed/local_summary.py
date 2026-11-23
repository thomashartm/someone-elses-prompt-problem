"""Summarize the ordinary seeded roadmap note in the isolated Ollama testbed."""
from datetime import datetime, timezone
import http.client
import json
import sys

HOST = "127.0.0.1:8766"
MODEL = "qwen2.5:7b"


def main():
    headers = {"Content-Type": "application/json"}

    def call(path, data=None):
        body = None if data is None else json.dumps(data).encode()
        con = http.client.HTTPConnection(HOST, timeout=70)
        try:
            con.request("GET" if body is None else "POST", path, body=body, headers=headers)
            response = con.getresponse()
            if response.status != 200:
                raise ValueError(f"{path} returned HTTP {response.status}: {response.read(300).decode()}")
            return json.load(response)
        finally:
            con.close()

    if call("/healthz").get("backend") != "ollama":
        raise ValueError("expected Ollama testbed on port 8766")
    login = call("/auth/login", {"username": "alice@acme.test", "password": "alice-pass"})
    headers["Authorization"] = "Bearer " + login["token"]
    result = call("/llm/summarize", {"note_id": 1})
    provider = result["provider"]
    if (provider["backend"] != "ollama" or provider["model"] != MODEL
            or not provider["completion_id"] or provider["finish_reason"] != "stop"
            or not result["summary"].strip()):
        raise ValueError("expected a completed, identified Ollama summary")
    print(json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(),
                      "target": "http://" + HOST, "workflow": "authenticated note summary",
                      "user": login["user"]["username"], "note_id": 1, "id": result["id"],
                      "summary": result["summary"], "provider": provider}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (http.client.HTTPException, KeyError, ValueError, OSError) as exc:
        sys.exit(f"summary unavailable: {exc}")
