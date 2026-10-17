"""Pydantic v2 transaction model matching docs/CONTRACT.md."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Channel = Literal["UPI", "IMPS", "NEFT"]
Typology = Literal[
    "mule_fan_in",
    "rapid_forwarding",
    "circular_layering",
    "account_takeover",
    "none",
]
_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")


class Truth(BaseModel):
    """Simulator-only ground truth. Scoring code must never read this."""

    is_scam: bool
    typology: Typology


class Transaction(BaseModel):
    """Payment transaction posted to POST /v1/transactions."""

    model_config = ConfigDict(populate_by_name=True)

    txn_id: str
    timestamp: str
    sender_token: str
    receiver_token: str
    amount: float = Field(gt=0)
    channel: Channel
    device_token: str
    memo: str | None = None
    truth: Truth | None = Field(default=None, alias="_truth")

    @field_validator("sender_token", "receiver_token", "device_token")
    @classmethod
    def tokens_are_16_hex(cls, value: str) -> str:
        if not _TOKEN_RE.fullmatch(value):
            raise ValueError("token must be 16 lowercase hex characters")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_iso_utc(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be ISO-8601 UTC") from exc
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a UTC timezone")
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("timestamp must be UTC")
        return value

    def to_payload(self) -> dict[str, Any]:
        """Dump by alias and omit unset/None fields (so ``_truth`` is optional)."""
        return self.model_dump(by_alias=True, exclude_none=True)
