"""Password hashing and verification primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise ValueError("Password must contain at least one letter and one digit")


def hash_password(password: str, iterations: int = 600_000) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "$".join(
        (ALGORITHM, str(iterations), base64.b64encode(salt).decode(), base64.b64encode(digest).decode())
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        salt = base64.b64decode(raw_salt, validate=True)
        expected = base64.b64decode(raw_digest, validate=True)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(raw_iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)

