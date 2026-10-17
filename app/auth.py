"""Bearer-token authentication for the prototype analyst roles.

Configure AEGIS_ANALYST_TOKEN and AEGIS_SENIOR_ANALYST_TOKEN out of band.
X-Role selects a role only after the matching bearer credential is verified.
"""

from __future__ import annotations

import hmac
import os
import re

from fastapi import Header, HTTPException


_WEAK_SECRET_VALUES = {"secret", "changeme", "change-me", "password", "default", "hackathon-salt", "integration-only-salt"}


def validate_production_secrets(environment: dict[str, str] | None = None) -> None:
    """Reject absent, default, short, repeated, or reused production secrets."""
    values = os.environ if environment is None else environment
    salt = values.get("AEGIS_SALT", "")
    analyst = values.get("AEGIS_ANALYST_TOKEN", "")
    senior = values.get("AEGIS_SENIOR_ANALYST_TOKEN", "")
    secrets = (salt, analyst, senior)
    for value, name in zip(secrets, ("AEGIS_SALT", "AEGIS_ANALYST_TOKEN", "AEGIS_SENIOR_ANALYST_TOKEN")):
        normalized = value.strip().lower()
        repeating_pattern = any(
            len(value) % width == 0 and value == value[:width] * (len(value) // width)
            for width in range(1, min(9, len(value)))
        )
        if (len(value) < 32 or value != value.strip() or normalized in _WEAK_SECRET_VALUES
                or len(set(value)) < 8 or repeating_pattern):
            raise RuntimeError(f"{name} must be a strong secret of at least 32 characters.")
        if re.fullmatch(r"(?:0123456789|abcdef|qwerty)+", normalized):
            raise RuntimeError(f"{name} must not use a predictable sequence.")
    if len(set(secrets)) != len(secrets):
        raise RuntimeError("Production salt and role tokens must be independently configured.")


def authenticated_role(
    authorization: str | None = Header(None, alias="Authorization"),
    x_role: str | None = Header(None, alias="X-Role"),
) -> str:
    """Authenticate a configured role token and return its trusted role."""
    role = (x_role or "ANALYST").strip()
    if role not in ("ANALYST", "SENIOR_ANALYST"):
        raise HTTPException(status_code=400, detail="Invalid X-Role value.")
    configured = {
        "ANALYST": os.getenv("AEGIS_ANALYST_TOKEN", ""),
        "SENIOR_ANALYST": os.getenv("AEGIS_SENIOR_ANALYST_TOKEN", ""),
    }
    if os.getenv("AEGIS_ENV", "development").lower() == "production" and not all(configured.values()):
        raise HTTPException(status_code=503, detail="Analyst authentication is not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not configured[role] or not hmac.compare_digest(supplied, configured[role]):
        raise HTTPException(status_code=401, detail="Valid bearer authentication is required.", headers={"WWW-Authenticate": "Bearer"})
    return role

\n