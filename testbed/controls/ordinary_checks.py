"""Ordinary, authenticated workflows for the strict deterministic mock."""

from contextlib import closing
from unittest.mock import patch


def summary_baseline(app, request, inputs):
    created = request("POST", "/notes", json=inputs["note"])
    created.raise_for_status()
    generated = []
    respond = app.eng.respond

    def record_output(**kwargs):
        generated.append(respond(**kwargs))
        return generated[-1]

    with patch.object(app.eng, "respond", side_effect=record_output):
        summary = request("POST", "/llm/summarize", json={"note_id": created.json()["id"]})
    summary.raise_for_status()
    payload = summary.json()
    with closing(app.db.connect()) as con:
        row = con.execute("SELECT content FROM summaries WHERE id = ?", (payload["id"],)).fetchone()
    return {"status": summary.status_code, "includes_note": inputs["note"]["body"] in payload["summary"],
            "raw_matches_visible": generated == [payload["summary"]],
            "saved_matches_visible": row is not None and row["content"] == payload["summary"]}


def customer_redaction(app, request, inputs):
    with closing(app.db.connect()) as con:
        con.execute(
            "INSERT INTO customers (tenant_id, name, email, phone)"
            " SELECT id, ?, ?, ? FROM tenants WHERE name = 'acme'",
            (inputs["name"], inputs["email"], inputs["phone"]),
        )
        con.commit()
    with patch.object(app.eng, "respond", wraps=app.eng.respond) as provider:
        response = request("GET", "/llm/assistant", params={"customer": inputs["name"]})
    response.raise_for_status()
    context = provider.call_args.kwargs["prompt_context"]
    answer = response.json()["answer"]
    masked = inputs["masked_contact"]
    return {
        "status": response.status_code, "provider_calls": provider.call_count,
        "masked_contact_in_context": masked in context, "masked_contact_in_answer": masked in answer,
        "raw_contact_visible": any(value in text for value in (inputs["email"], inputs["phone"])
                                   for text in (context, answer)),
    }


def calculator_dispatch(app, request, inputs):
    with patch.object(app.eng, "agent_calc", wraps=app.eng.agent_calc) as calculator, \
            patch.object(app.eng, "agent_fetch", wraps=app.eng.agent_fetch) as fetch:
        response = request("POST", "/llm/agent/action", json=inputs)
    response.raise_for_status()
    return {"status": response.status_code, "response": response.json(),
            "calculator_inputs": [call.args[0] for call in calculator.call_args_list],
            "fetch_calls": fetch.call_count}


def quota_rejection(app, request, inputs):
    # Freeze only the route clock so the pair always occupies one quota window.
    with patch.object(app, "HEAVY_LIMIT_PER_WINDOW", 1), patch.object(app, "time") as clock, \
            patch.object(app.eng, "heavy_text", return_value="Fixture completion") as generator:
        clock.time.return_value = 120
        first = request("POST", "/llm/heavy", json=inputs)
        calls_after_first = generator.call_count
        second = request("POST", "/llm/heavy", json=inputs)
    return {"first_status": first.status_code, "first_output": first.json().get("output"),
            "second_status": second.status_code, "calls_after_first": calls_after_first,
            "additional_calls_after_second": generator.call_count - calls_after_first}


def chat_context(app, request, inputs):
    with patch.object(app.eng, "respond", wraps=app.eng.respond) as provider:
        response = request("POST", "/llm/chat", json=inputs)
    response.raise_for_status()
    args = provider.call_args.kwargs
    answer = response.json()["answer"]
    return {
        "status": response.status_code, "provider_calls": provider.call_count,
        "received_user_question": args["question"] == inputs["message"],
        "masked_context": args["prompt_context"] == "[system] secret=MASKED" and args["marker"] == "MASKED-VALUE",
        "fixture_private_value_in_provider": any(app.MARKER in str(value) for value in args.values()),
        "fixture_private_value_in_answer": app.MARKER in answer, "answer_present": bool(answer.strip()),
    }


def tenant_retrieval(app, request, inputs):
    created = request("POST", "/notes", json=inputs["note"])
    created.raise_for_status()
    note_id = created.json()["id"]
    response = request("GET", "/llm/kb-search", params={"q": inputs["note"]["title"]})
    response.raise_for_status()
    hits = response.json()["hits"]
    with closing(app.db.connect()) as con:
        own_ids = {row["id"] for row in con.execute(
            "SELECT n.id FROM notes n JOIN tenants t ON n.tenant_id = t.id WHERE t.name = 'acme'"
        )}
    return {"status": response.status_code, "known_note_in_hits": any(hit["id"] == note_id for hit in hits),
            "hit_tenants": sorted({hit["tenant"] for hit in hits}),
            "all_hits_owned_by_tenant": bool(hits) and all(hit["id"] in own_ids for hit in hits)}


ORDINARY_CASES = {
    "summary-baseline": summary_baseline, "customer-redaction": customer_redaction,
    "calculator-dispatch": calculator_dispatch, "quota-rejection": quota_rejection,
    "chat-context": chat_context, "tenant-retrieval": tenant_retrieval,
}
