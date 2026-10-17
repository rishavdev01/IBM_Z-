"""Tiny FastAPI stand-in so Part 1 can be tested without Part 2.

Run:
    uvicorn simulator.mock_api:app --reload --port 8000
"""

from __future__ import annotations

import random

from fastapi import FastAPI

from simulator.models import Transaction

app = FastAPI(title="Aegis Exchange mock API", version="0.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe matching the contract."""
    return {"status": "ok"}


@app.post("/v1/transactions")
def ingest(txn: Transaction) -> dict:
    """Validate the body and return a random low-risk contract response."""
    score = round(random.uniform(0.02, 0.25), 4)
    return {
        "txn_id": txn.txn_id,
        "risk_score": score,
        "risk_level": "LOW",
        "reasons": [],
        "alert_id": None,
    }
