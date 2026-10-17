"""Platform-owned request contract mirroring simulator transaction payloads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Truth(BaseModel):
    """Optional simulator ground truth retained for offline evaluation only."""

    model_config = ConfigDict(extra="ignore")
    is_scam: bool
    typology: Literal["mule_fan_in", "rapid_forwarding", "circular_layering", "account_takeover", "none"] = "none"


class Transaction(BaseModel):
    """Validated transaction request; truth labels never enter the scoring features."""

    txn_id: str = Field(..., description="Unique UUID4. Replaying the same ID is idempotent.", examples=["12345678-1234-4234-8234-123456789abc"])
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp.", examples=["2026-10-17T07:30:00Z"])
    sender_token: str = Field(..., min_length=16, max_length=16, description="16-character pseudonymous account token.", examples=["0123456789abcdef"])
    receiver_token: str = Field(..., min_length=16, max_length=16, description="16-character pseudonymous account token.", examples=["fedcba9876543210"])
    amount: float = Field(..., gt=0, description="Positive transfer amount in INR.", examples=[1250.0])
    channel: Literal["UPI", "IMPS", "NEFT"] = Field(..., description="Supported payment rail.")
    device_token: str = Field(..., min_length=16, max_length=16, description="16-character pseudonymous device token.", examples=["a1b2c3d4e5f60718"])
    memo: str | None = Field(default=None, description="Optional simulator metadata; not used for scoring or persisted.")
    truth: Truth | None = Field(default=None, alias="_truth", description="Simulator ground truth for offline evaluation only; never used as a scoring feature.")

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        json_schema_extra={
            "examples": [{
                "txn_id": "12345678-1234-4234-8234-123456789abc",
                "timestamp": "2026-10-17T07:30:00Z",
                "sender_token": "0123456789abcdef",
                "receiver_token": "fedcba9876543210",
                "amount": 1250.0,
                "channel": "UPI",
                "device_token": "a1b2c3d4e5f60718",
            }]
        },
    )


RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
AlertStatus = Literal["OPEN", "APPROVED", "DISMISSED"]


class Reason(BaseModel):
    factor: str
    contribution: float
    description: str = ""


class TransactionResponse(BaseModel):
    txn_id: str
    risk_score: float
    risk_level: RiskLevel
    reasons: list[Reason] = Field(default_factory=list)
    alert_id: int | None


class AlertSummary(BaseModel):
    alert_id: int
    txn_id: str
    created_at: str
    status: AlertStatus
    risk_score: float
    risk_level: RiskLevel
    reasons: list[Reason] = Field(default_factory=list)
    sender_token: str
    receiver_token: str
    amount: float


class DecisionRecord(BaseModel):
    id: int
    alert_id: int
    decision: Literal["APPROVE", "DISMISS"]
    analyst: str
    note: str = ""
    decided_at: str


class AuditRecord(BaseModel):
    id: int
    ts: str
    actor: str
    role: str
    action: str
    target: str
    detail: Any = None


class AlertDetail(AlertSummary):
    features: dict[str, Any] = Field(default_factory=dict)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    audit_trail: list[AuditRecord] = Field(default_factory=list)


class DecisionResponse(BaseModel):
    status: Literal["ok"]
    alert_id: int
    decision: Literal["APPROVE", "DISMISS"]
    new_status: Literal["APPROVED", "DISMISSED"]
    analyst: str
    decided_at: str


class GraphNode(BaseModel):
    id: str
    in_degree: int
    out_degree: int
    role: Literal["sender", "receiver", "other"] | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    amount: float
    timestamp: str
    txn_id: str


class GraphResponse(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class StatsResponse(BaseModel):
    total_txns: int
    alerts_open: int
    alerts_total: int
    avg_latency_ms: float


class HealthResponse(BaseModel):
    status: Literal["ok"]


class RootResponse(BaseModel):
    name: str
    status: Literal["ok"]
    health: str
    docs: str
