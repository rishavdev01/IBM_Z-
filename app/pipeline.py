"""Idempotent transaction-to-score-to-persistence integration pipeline."""

from __future__ import annotations

from time import perf_counter
from typing import Any

import os

from app.alerts_store import get_alert_for_transaction, get_transaction, persist_transaction_result
from app.graph_engine import GraphEngine
from app.scoring import rule_based_score, score_features

def process_transaction(txn: dict[str, Any], engine: GraphEngine) -> dict[str, Any]:
    """Process a transaction idempotently with atomic transaction/alert persistence."""
    txn_id = str(txn["txn_id"])
    threshold = float(os.getenv("ALERT_THRESHOLD", "0.40"))
    prior = get_transaction(txn_id)
    prior_alert = get_alert_for_transaction(txn_id)
    if prior is not None and (prior_alert is not None or float(prior["risk_score"]) < threshold):
        return {**prior, "alert_id": prior_alert["alert_id"] if prior_alert else None}

    started = perf_counter()
    features = engine.extract_features(txn)
    try:
        result = score_features(features)
    except Exception:
        result = rule_based_score(features)
    processing_ms = (perf_counter() - started) * 1000
    response, alert_id = persist_transaction_result(txn, features, result, threshold, processing_ms)
    return {**response, "alert_id": alert_id}
