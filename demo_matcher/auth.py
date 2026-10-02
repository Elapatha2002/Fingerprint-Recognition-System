"""Small password gate for the hosted viva demonstration."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets


ALGORITHM = "sha256"
ITERATIONS = 250_000


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Access password must contain at least 12 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(ALGORITHM, password.encode(), salt, ITERATIONS)
    return f"pbkdf2_{ALGORITHM}${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, expected_hex = stored.split("$", 3)
        if scheme != f"pbkdf2_{ALGORITHM}":
            return False
        actual = hashlib.pbkdf2_hmac(
            ALGORITHM, password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def configured_hash() -> str:
    return os.environ.get("FRS_ACCESS_PASSWORD_HASH", "").strip()
