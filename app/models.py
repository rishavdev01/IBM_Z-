"""Platform-owned request contract mirroring simulator transaction payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Truth(BaseModel):
    """Optional simulator-only ground truth retained for offline evaluation."""

    model_config = ConfigDict(extra="ignore")
    is_scam: bool
    typology: Literal["mule_fan_in", "rapid_forwarding", "circular_layering", "account_takeover", "none"] = "none"


class Transaction(BaseModel):
    """Validated API view of the shared simulator payload (including `_truth`)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    txn_id: str
    timestamp: str
    sender_token: str = Field(..., min_length=16, max_length=16)
    receiver_token: str = Field(..., min_length=16, max_length=16)
    amount: float = Field(..., gt=0)
    channel: Literal["UPI", "IMPS", "NEFT"]
    device_token: str = Field(..., min_length=16, max_length=16)
    memo: str | None = None
    truth: Truth | None = Field(default=None, alias="_truth")

\n