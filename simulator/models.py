"""Pydantic v2 data models for Aegis Exchange transactions.

Matches the TRANSACTION contract specification:
- txn_id: uuid4 string
- timestamp: ISO-8601 UTC string (e.g. 2026-10-17T07:30:00Z)
- sender_token: 16 hex chars
- receiver_token: 16 hex chars
- amount: float INR (must be > 0)
- channel: UPI | IMPS | NEFT
- device_token: 16 hex chars
- memo: optional string
- _truth: optional ground truth object (is_scam: bool, typology: str)
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ChannelType = Literal["UPI", "IMPS", "NEFT"]
TypologyType = Literal[
    "mule_fan_in",
    "rapid_forwarding",
    "circular_layering",
    "account_takeover",
    "none",
]


class Truth(BaseModel):
    """Ground-truth metadata for simulated transactions."""

    model_config = ConfigDict(extra="ignore")

    is_scam: bool
    typology: TypologyType = "none"


class Transaction(BaseModel):
    """Transaction model matching the Aegis Exchange contract."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    txn_id: str
    timestamp: str
    sender_token: str
    receiver_token: str
    amount: float = Field(..., gt=0.0, description="Transaction amount in INR; must be > 0")
    channel: ChannelType
    device_token: str
    memo: str | None = None
    truth: Truth | None = Field(default=None, alias="_truth")

    @field_validator("sender_token", "receiver_token", "device_token")
    @classmethod
    def validate_token_length(cls, v: str) -> str:
        """Validate token format."""
        if len(v) != 16:
            raise ValueError(f"Token must be exactly 16 hex characters, got '{v}' (len={len(v)})")
        return v

    def to_payload(self) -> dict[str, Any]:
        """Convert the transaction model to an API JSON payload dict.

        Uses aliases so 'truth' is exported as '_truth' and drops None values.
        """
        return self.model_dump(by_alias=True, exclude_none=True)
