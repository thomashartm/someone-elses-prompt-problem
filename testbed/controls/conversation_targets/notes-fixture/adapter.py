"""Read the local notes fixture's summary or empty-notebook response."""
import json


def parse(raw):
    response = json.loads(raw)
    if isinstance(response, dict):
        for field, kind in (("answer", "ok"), ("decline", "declined")):
            text = response.get(field)
            if isinstance(text, str) and text.strip():
                return text, kind, None
    return "", "error", None
