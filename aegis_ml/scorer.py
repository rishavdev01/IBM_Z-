"""Production ML scorer for Aegis Exchange.

Evaluates transactions in under 5 ms using trained gradient boosted decision trees.
Provides model-agnostic feature contribution explanations and a guaranteed rule-based fallback.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any
import joblib
import numpy as np

from aegis_ml.features import FEATURE_NAMES, to_vector

_MODEL_LOCK = threading.RLock()
_CACHED_BACKEND: str | None = None
_CACHED_MODEL: Any = None
_CACHED_METADATA: dict[str, Any] | None = None
_CACHED_MEDIANS: np.ndarray | None = None

MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")


def make_description(factor: str, val: float) -> str:
    """Generate clear, human-readable explanatory text for any of the 16 features."""
    if factor == "amount":
        return f"Transaction amount of {val:,.2f} INR is unusually high"
    elif factor == "hour_of_day":
        return f"Transaction occurred during unusual hour ({int(val):02d}:00 UTC)"
    elif factor == "channel_code":
        ch_name = {0: "UPI", 1: "IMPS", 2: "NEFT"}.get(int(val), str(int(val)))
        return f"Payment initiated via {ch_name} channel"
    elif factor == "sender_out_degree_24h":
        return f"Sender sent payments to {int(val)} distinct receivers in 24 hours"
    elif factor == "sender_txn_count_1h":
        return f"Sender initiated {int(val)} transactions in the past hour"
    elif factor == "receiver_in_degree_24h":
        return f"Receiver got money from {int(val)} different senders in 24 hours"
    elif factor == "receiver_in_amount_24h":
        return f"Receiver accumulated {val:,.2f} INR inbound funds in 24 hours"
    elif factor == "receiver_out_amount_24h":
        return f"Receiver transferred out {val:,.2f} INR in 24 hours"
    elif factor == "receiver_forward_ratio":
        return f"Receiver forwarded {val * 100:.1f}% of total inbound funds"
    elif factor == "receiver_min_forward_seconds":
        return f"Receiver forwarded incoming funds within {int(val)} seconds"
    elif factor == "in_cycle":
        return "Transaction closes a circular money loop among accounts"
    elif factor == "cycle_length":
        return f"Transaction completes a closed circular laundering loop of length {int(val)}"
    elif factor == "sender_seconds_since_last_inbound":
        return f"Sender forwarded money only {int(val)} seconds after receiving inbound funds"
    elif factor == "sender_forward_ratio":
        return f"Sender forwarded {val * 100:.1f}% of incoming funds"
    elif factor == "sender_new_device":
        return "Transaction was executed from a new, previously unseen device token"
    elif factor == "amount_vs_sender_avg":
        return f"Amount is {val:.1f}x higher than sender's historical average"
    return f"Feature {factor} exhibited an anomalous value of {val:.2f}"


def _rule_fallback(vec: np.ndarray) -> dict[str, Any]:
    """Guaranteed rule-based fallback when model files are missing or unreadable."""
    reasons: list[dict[str, Any]] = []
    score = 0.05

    # 1. Fan-in check (idx 5: receiver_in_degree_24h)
    in_deg = vec[5]
    if in_deg >= 8:
        contrib = min(0.65, 0.35 + (in_deg - 8) * 0.02)
        score = max(score, contrib + 0.25)
        reasons.append({
            "factor": "receiver_in_degree_24h",
            "contribution": round(contrib, 4),
            "description": make_description("receiver_in_degree_24h", in_deg),
        })

    # 2. Circular layering check (idx 10: in_cycle)
    in_cyc = vec[10]
    if in_cyc == 1:
        contrib = 0.50
        score = max(score, 0.88)
        reasons.append({
            "factor": "in_cycle",
            "contribution": contrib,
            "description": make_description("in_cycle", in_cyc),
        })

    # 3. Rapid forwarding check (idx 13: sender_forward_ratio, idx 12: sender_seconds_since_last_inbound)
    fwd_ratio = vec[13]
    sec_gap = vec[12]
    if fwd_ratio >= 0.85 and 0 <= sec_gap <= 240:
        contrib = 0.45
        score = max(score, 0.85)
        reasons.append({
            "factor": "sender_forward_ratio",
            "contribution": contrib,
            "description": make_description("sender_forward_ratio", fwd_ratio),
        })

    # 4. Account takeover check (idx 14: sender_new_device, idx 15: amount_vs_sender_avg)
    new_dev = vec[14]
    amt_mult = vec[15]
    if new_dev == 1 and amt_mult >= 3.5:
        contrib = 0.42
        score = max(score, 0.82)
        reasons.append({
            "factor": "sender_new_device",
            "contribution": contrib,
            "description": make_description("sender_new_device", new_dev),
        })

    # Sort by contribution descending, keep top 3
    reasons = sorted(reasons, key=lambda r: r["contribution"], reverse=True)[:3]
    risk_score = round(float(np.clip(score, 0.01, 0.99)), 4)

    if risk_score >= 0.70:
        risk_level = "HIGH"
    elif risk_score >= 0.40:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "reasons": reasons,
    }


def _load_model_cached() -> tuple[Any, dict[str, Any], np.ndarray] | None:
    """Thread-safe lazy loader for the model and metadata."""
    global _CACHED_BACKEND, _CACHED_MODEL, _CACHED_METADATA, _CACHED_MEDIANS

    backend = os.getenv("AEGIS_MODEL_BACKEND", "lightgbm").strip().lower()

    with _MODEL_LOCK:
        if _CACHED_MODEL is not None and _CACHED_BACKEND == backend and _CACHED_METADATA is not None and _CACHED_MEDIANS is not None:
            return _CACHED_MODEL, _CACHED_METADATA, _CACHED_MEDIANS

        model_file = "model_lightgbm.joblib" if backend == "lightgbm" else "model_sklearn.joblib"
        model_path = os.path.join(MODEL_DIR, model_file)
        meta_path = os.path.join(MODEL_DIR, "metadata.json")

        if not os.path.exists(model_path) or not os.path.exists(meta_path):
            return None

        try:
            model = joblib.load(model_path)
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            medians_dict = meta.get("benign_medians", {})
            medians_vec = np.array([float(medians_dict.get(feat, 0.0)) for feat in FEATURE_NAMES], dtype=np.float64)

            _CACHED_BACKEND = backend
            _CACHED_MODEL = model
            _CACHED_METADATA = meta
            _CACHED_MEDIANS = medians_vec
            return _CACHED_MODEL, _CACHED_METADATA, _CACHED_MEDIANS
        except Exception:
            return None


def score(features: dict[str, Any]) -> dict[str, Any]:
    """Score transaction features and compute top explainable reason codes.

    Contract:
        features: Exactly the 16 numeric keys from the graph engine.
        Returns:
            {"risk_score": float 0..1,
             "risk_level": "LOW" | "MEDIUM" | "HIGH",
             "reasons": [{"factor": str, "contribution": float, "description": str}]}

    Guarantees:
        - Must NEVER raise on any failure (returns rule-based fallback).
        - Must NEVER read _truth.
        - Execution finishes under 5 ms via batched counterfactual inference.
    """
    try:
        # Convert dictionary to ordered feature vector
        vec = to_vector(features)

        loaded = _load_model_cached()
        if loaded is None:
            return _rule_fallback(vec)

        model, _, medians = loaded

        # Fast batched prediction for model-agnostic explanations (1 original + 16 counterfactuals = 17 rows)
        n_feats = len(FEATURE_NAMES)
        batch = np.tile(vec, (n_feats + 1, 1))
        for i in range(n_feats):
            batch[i + 1, i] = medians[i]

        probs = model.predict_proba(batch)[:, 1]
        p_x = float(probs[0])
        p_counterfactuals = probs[1:]

        # Calculate positive feature contributions
        contributions = p_x - p_counterfactuals
        positive_items: list[tuple[float, str, float]] = []

        for i, factor in enumerate(FEATURE_NAMES):
            c_i = float(contributions[i])
            if c_i > 0.0001:  # Positive contributor
                positive_items.append((c_i, factor, float(vec[i])))

        # Sort top 3 positive contributors descending
        positive_items.sort(key=lambda item: item[0], reverse=True)
        top_reasons: list[dict[str, Any]] = []

        for contrib, factor, val in positive_items[:3]:
            top_reasons.append({
                "factor": factor,
                "contribution": round(contrib, 4),
                "description": make_description(factor, val),
            })

        risk_score = round(float(np.clip(p_x, 0.0, 1.0)), 4)
        if risk_score >= 0.70:
            risk_level = "HIGH"
        elif risk_score >= 0.40:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        return {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "reasons": top_reasons,
        }

    except Exception:
        # Absolute guarantee: score() must never raise
        try:
            vec = to_vector(features)
            return _rule_fallback(vec)
        except Exception:
            return {
                "risk_score": 0.05,
                "risk_level": "LOW",
                "reasons": [],
            }
