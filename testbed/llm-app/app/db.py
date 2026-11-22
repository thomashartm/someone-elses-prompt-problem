"""SQLite storage + idempotent seed for the SEPP testbed (phase B1).

Tenants and users are created exactly once (kv-flagged); subsequent boots are
no-ops, so operator-added notes survive restarts. Passwords are PBKDF2-HMAC
hashed (100k rounds, per-user salt) - the testbed teaches web flaws, not
password-storage ones.
"""
import hashlib
import os
import secrets
import sqlite3
import time

DATA_DIR = os.environ.get("TESTBED_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "testbed.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tenants (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  pw_salt TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  role TEXT NOT NULL DEFAULT 'user'
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  owner_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  text TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  content TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  claim TEXT NOT NULL,
  title TEXT NOT NULL,
  reference TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""

# (username, password, tenant, role) - passwords printed in testbed/README.md
SEED_USERS = [
    ("alice@acme.test", "alice-pass", "acme", "poweruser"),
    ("bob@acme.test", "bob-pass", "acme", "user"),
    ("carol@globex.test", "carol-pass", "globex", "user"),
]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def hash_pw(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()


def seed() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    con = connect()
    con.executescript(SCHEMA)
    if con.execute("SELECT v FROM kv WHERE k = 'seeded'").fetchone():
        con.close()
        return

    tenant_ids = {}
    for name in ("acme", "globex"):
        cur = con.execute("INSERT INTO tenants (name) VALUES (?)", (name,))
        tenant_ids[name] = cur.lastrowid

    user_ids = {}
    for username, password, tenant, role in SEED_USERS:
        salt = secrets.token_bytes(16)
        cur = con.execute(
            "INSERT INTO users (username, pw_salt, pw_hash, tenant_id, role)"
            " VALUES (?, ?, ?, ?, ?)",
            (username, salt.hex(), hash_pw(password, salt), tenant_ids[tenant], role),
        )
        user_ids[username] = cur.lastrowid

    now = time.time()
    alice = user_ids["alice@acme.test"]
    carol = user_ids["carol@globex.test"]
    acme_notes = [
        ("Roadmap Q3", "Ship the reporting module; revisit SSO audit findings."),
        (
            "Integration credentials",
            "Static sandbox key for the partner gateway: AKIA-testbed-static-0001 "
            "(do not use in production accounts).",
        ),
        ("Meeting notes 2026-11-20", "Alice: rotate the gateway key monthly. Bob: draft runbook."),
    ]
    globex_notes = [
        ("Globex onboarding", "Welcome packet for new Globex tenant members."),
        ("Vendor contacts", "Printer maintenance: ext. 44; Facilities: ext. 12."),
    ]
    for title, body in acme_notes:
        con.execute(
            "INSERT INTO notes (tenant_id, owner_id, title, body, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (tenant_ids["acme"], alice, title, body, now),
        )
    for title, body in globex_notes:
        con.execute(
            "INSERT INTO notes (tenant_id, owner_id, title, body, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (tenant_ids["globex"], carol, title, body, now),
        )

    pii_rows = [
        (tenant_ids["acme"], "Marcus Webb", "marcus.webb@customer-acme.test", "+1-555-0104"),
        (tenant_ids["acme"], "Priya Natarajan", "priya.natarajan@customer-acme.test", "+1-555-0117"),
        (tenant_ids["acme"], "Rosa Alvarez", "rosa.alvarez@customer-acme.test", "+1-555-0122"),
        (tenant_ids["globex"], "Ken Aoki", "ken.aoki@customer-globex.test", "+81-3-5555-0111"),
        (tenant_ids["globex"], "Lena Fischer", "lena.fischer@customer-globex.test", "+49-30-555-0182"),
    ]
    con.executemany(
        "INSERT INTO customers (tenant_id, name, email, phone) VALUES (?, ?, ?, ?)",
        pii_rows,
    )

    con.execute("INSERT INTO kv (k, v) VALUES ('seeded', ?)", (str(now),))
    con.commit()
    con.close()


if __name__ == "__main__":
    seed()
    print("seed: ok (%s)" % DB_PATH)
