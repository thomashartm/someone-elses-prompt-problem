"""Auth for the SEPP testbed: JWT issue/verify + password check (phase B1).

Login-secret guard behaviour (route POST /auth/login):
  strict  random secret, generated once per deployment volume and persisted
          under TESTBED_DATA_DIR/jwt-secret.key -> tokens die with the volume
  naive   DOCUMENTED static secret (below) shared by every deployment ->
          anyone who reads testbed/EXPECTATIONS.md can forge valid tokens
          for ANY tenant/role (token-forgery demo relies on this)
  off     same as naive (the flaw IS the absence of a proper secret)

Verification is uniform across tiers: HS256, exp, aud and iss are always
checked; token TTL 1 h.
"""
import os
import secrets
import time

import jwt

from . import guards

STATIC_SECRET = "sepp-testbed-static-dev-secret"  # DOCUMENTED: naive-tier secret
JWT_AUD = "sepp-testbed"
JWT_ISS = "sepp-testbed"
TOKEN_TTL_S = 3600


def login_secret(data_dir: str) -> str:
    if guards.is_strict():
        path = os.path.join(data_dir, "jwt-secret.key")
        if not os.path.exists(path):
            os.makedirs(data_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                fh.write(secrets.token_hex(32))
            os.replace(tmp, path)
        with open(path) as fh:
            return fh.read().strip()
    return STATIC_SECRET


def issue_token(secret: str, username: str, tenant: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "tenant": tenant,
        "role": role,
        "iat": now,
        "exp": now + TOKEN_TTL_S,
        "aud": JWT_AUD,
        "iss": JWT_ISS,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class InvalidToken(Exception):
    pass


def verify_token(secret: str, token: str) -> dict:
    try:
        return jwt.decode(
            token, secret, algorithms=["HS256"], audience=JWT_AUD, issuer=JWT_ISS
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc


def check_password(row, password: str) -> bool:
    import hashlib
    import hmac

    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(row["pw_salt"]), 100_000
    ).hex()
    return hmac.compare_digest(digest, row["pw_hash"])
