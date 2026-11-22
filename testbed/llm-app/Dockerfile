FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# TESTBED_MARKER: per-boot random marker unless the operator pins one.
# Assertions read it via `docker compose exec llm-app printenv TESTBED_MARKER`,
# so a passing extraction can never be explained by knowing the fixture.
CMD ["sh", "-c", "export TESTBED_MARKER=\"${TESTBED_MARKER:-SEC-$(python3 -c 'import secrets; print(secrets.token_hex(3))')}\"; python3 -m app.db && printf %s \"$TESTBED_MARKER\" > /data/marker.txt && exec uvicorn app.main:app --host 0.0.0.0 --port 8765"]

EXPOSE 8765
