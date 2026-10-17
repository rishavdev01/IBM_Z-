"""Mock FastAPI server for testing Part 1 without requiring Part 2.

Serves GET /health and POST /v1/transactions conforming to the Aegis Exchange contract.
Run with:
    python -m uvicorn simulator.mock_api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import random
from typing import Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from simulator.models import Transaction

app = FastAPI(title="Aegis Exchange - Mock API")

_alert_id_counter = 1000


class ReasonItem(BaseModel):
    factor: str
    contribution: float
    description: str


class TxnResponse(BaseModel):
    txn_id: str
    risk_score: float
    risk_level: str
    reasons: list[ReasonItem] = Field(default_factory=list)
    alert_id: int | None = None


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/v1/transactions", response_model=TxnResponse)
def post_transaction(txn: Transaction) -> TxnResponse:
    """Ingest a transaction and return a scored response.

    If _truth is attached with is_scam=True, mock returns a HIGH risk score with reasons.
    Otherwise returns a LOW risk score.
    """
    global _alert_id_counter

    is_scam = False
    typology = "none"
    if txn.truth is not None:
        is_scam = txn.truth.is_scam
        typology = txn.truth.typology

    if is_scam:
        # Mock high-risk response
        risk_score = round(random.uniform(0.75, 0.95), 4)
        risk_level = "HIGH"
        _alert_id_counter += 1
        alert_id = _alert_id_counter
        reasons = [
            ReasonItem(
                factor="typology_trigger",
                contribution=0.85,
                description=f"Detected pattern matching typology: {typology}",
            )
        ]
    else:
        # Benign low-risk response
        risk_score = round(random.uniform(0.01, 0.15), 4)
        risk_level = "LOW"
        alert_id = None
        reasons = []

    return TxnResponse(
        txn_id=txn.txn_id,
        risk_score=risk_score,
        risk_level=risk_level,
        reasons=reasons,
        alert_id=alert_id,
    )
