"""Salted tokenization utility for accounts and devices.

Uses SHA-256 with a salt to produce deterministic 16-character hex tokens.
"""

from __future__ import annotations

import hashlib
import os


def tokenize(raw_id: str, salt: str | None = None) -> str:
    """Tokenize a raw identifier (account ID or device ID).

    Calculates SHA-256 of (salt + raw_id) and returns the first 16 hex characters.
    If salt is None, defaults to the AEGIS_SALT environment variable, falling back
    to 'hackathon-salt'.

    Args:
        raw_id: The raw string ID (e.g. 'ACC000123' or 'DEV123_1').
        salt: Optional salt override.

    Returns:
        16-character hexadecimal token string.
    """
    if salt is None:
        salt = os.getenv("AEGIS_SALT", "hackathon-salt")
    payload = f"{salt}{raw_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
