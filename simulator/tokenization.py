"""Salted account/device tokenization (do not name this module tokenize.py)."""

from __future__ import annotations

import hashlib
import os

from dotenv import load_dotenv

load_dotenv()

DEFAULT_SALT = "hackathon-salt"


def tokenize(raw_id: str, salt: str | None = None) -> str:
    """Return the first 16 hex chars of sha256(salt + raw_id).

    ``salt`` defaults to env ``AEGIS_SALT``, then ``hackathon-salt``.
    """
    used = salt if salt is not None else os.getenv("AEGIS_SALT", DEFAULT_SALT)
    digest = hashlib.sha256(f"{used}{raw_id}".encode("utf-8")).hexdigest()
    return digest[:16]
