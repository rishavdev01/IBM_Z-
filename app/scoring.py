"""Explicit scorer adapter for the platform-owned integration boundary.

No trained model is present in the repository. The default backend is a
transparent feature-based ruleset; selecting ``ml`` invokes the Part 3
``aegis_ml.scorer.score`` interface without claiming it is a trained model.
"""

from __future__ import annotations

import os
from typing import Any

from aegis_ml import scorer as ml_scorer


def rule_based_score(features: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, explainable heuristic score from graph features only."""
    signals: list[tuple[float, str, str]] = []
    def add(value: float, factor: str, description: str) -> None:
        if value > 0:
            signals.append((value, factor, description))
    add(min(float(features.get("receiver_in_degree_24h", 0)) / 8, 0.35), "receiver_in_degree_24h", "Many distinct senders paid this receiver.")
    add(min(max(float(features.get("receiver_forward_ratio", 0)) - 0.6, 0) * 0.35, 0.25), "receiver_forward_ratio", "Receiver forwards a large share of inbound funds.")
    add(0.35 if features.get("in_cycle") else 0, "in_cycle", "This transfer closes a short transaction cycle.")
    add(0.15 if float(features.get("sender_new_device", 0)) else 0, "sender_new_device", "Sender uses a device not seen in prior transfers.")
    add(min(float(features.get("sender_txn_count_1h", 0)) / 15, 0.2), "sender_txn_count_1h", "Sender has a high recent transfer count.")
    score = min(sum(s[0] for s in signals), 1.0)
    level = "HIGH" if score >= 0.70 else "MEDIUM" if score >= 0.40 else "LOW"
    reasons = [{"factor": factor, "contribution": round(value, 4), "description": desc}
               for value, factor, desc in sorted(signals, reverse=True)[:3]]
    return {"risk_score": round(score, 4), "risk_level": level, "reasons": reasons}


def score_features(features: dict[str, Any]) -> dict[str, Any]:
    """Score with the configured backend and use the heuristic on failure."""
    mode = os.getenv("AEGIS_SCORER_MODE", "rules").strip().lower()
    if mode == "ml":
        # The Part 3 module opts in explicitly when a compatible model is available.
        if not getattr(ml_scorer, "MODEL_AVAILABLE", False):
            return rule_based_score(features)
        try:
            result = ml_scorer.score(features)
            risk = float(result["risk_score"])
            if not 0 <= risk <= 1:
                raise ValueError("risk_score must be between zero and one")
            level = "HIGH" if risk >= 0.70 else "MEDIUM" if risk >= 0.40 else "LOW"
            return {"risk_score": risk, "risk_level": level, "reasons": list(result.get("reasons", []))[:3]}
        except Exception:
            return rule_based_score(features)
    if mode != "rules":
        raise ValueError("AEGIS_SCORER_MODE must be 'rules' or 'ml'")
    return rule_based_score(features)

\n