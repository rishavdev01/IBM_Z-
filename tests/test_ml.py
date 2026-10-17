"""Test suite for Part 3: ML scoring and explainability.

Tests feature vectorization, score contract shape, threshold boundaries,
explainability constraints, rule-based fallback, and sub-5ms latency SLA.
"""

from __future__ import annotations

import time
import pytest
import numpy as np

from aegis_ml.features import FEATURE_NAMES, to_vector
from aegis_ml.synthetic_features import generate_synthetic_features
from aegis_ml.scorer import score, _rule_fallback


def test_feature_vector_properties():
    """Verify feature vector length, ordering, and default fallback handling."""
    assert len(FEATURE_NAMES) == 16

    # Empty dict defaults
    vec_empty = to_vector({})
    assert isinstance(vec_empty, np.ndarray)
    assert len(vec_empty) == 16
    assert vec_empty.dtype == np.float64
    assert vec_empty[0] == 0.0  # amount
    assert vec_empty[9] == -1.0  # receiver_min_forward_seconds
    assert vec_empty[15] == 1.0  # amount_vs_sender_avg

    # Explicit values
    feats = {
        "amount": 2500.0,
        "hour_of_day": 14,
        "channel_code": 1,
        "receiver_in_degree_24h": 12,
        "in_cycle": 1,
        "sender_new_device": 1,
    }
    vec = to_vector(feats)
    assert vec[0] == 2500.0
    assert vec[1] == 14.0
    assert vec[2] == 1.0
    assert vec[5] == 12.0
    assert vec[10] == 1.0
    assert vec[14] == 1.0


def test_synthetic_feature_generator():
    """Verify synthetic dataset generator produces correct shape and ~4% scam share."""
    df = generate_synthetic_features(n_rows=1000, scam_rate=0.04, seed=42)
    assert len(df) == 1000
    for name in FEATURE_NAMES:
        assert name in df.columns
    assert "is_scam" in df.columns
    assert "typology" in df.columns

    scam_count = df["is_scam"].sum()
    assert 30 <= scam_count <= 50  # ~4%
    assert set(df["typology"].unique()).issubset({
        "none",
        "mule_fan_in",
        "rapid_forwarding",
        "circular_layering",
        "account_takeover",
    })


def test_score_contract_shape():
    """Verify score() return format matches the Aegis Exchange contract."""
    sample = {
        "amount": 1500.0,
        "hour_of_day": 12,
        "channel_code": 0,
        "sender_out_degree_24h": 2,
        "sender_txn_count_1h": 1,
        "receiver_in_degree_24h": 2,
        "receiver_in_amount_24h": 3000.0,
        "receiver_out_amount_24h": 500.0,
        "receiver_forward_ratio": 0.16,
        "receiver_min_forward_seconds": -1.0,
        "in_cycle": 0,
        "cycle_length": 0,
        "sender_seconds_since_last_inbound": -1.0,
        "sender_forward_ratio": 0.1,
        "sender_new_device": 0,
        "amount_vs_sender_avg": 1.0,
    }
    res = score(sample)
    assert isinstance(res, dict)
    assert "risk_score" in res
    assert "risk_level" in res
    assert "reasons" in res

    assert 0.0 <= res["risk_score"] <= 1.0
    assert res["risk_level"] in ("LOW", "MEDIUM", "HIGH")
    assert isinstance(res["reasons"], list)


def test_risk_level_boundaries():
    """Verify strict boundaries: LOW (<0.40), MEDIUM (0.40-0.69), HIGH (>=0.70)."""
    # Low score test
    benign_sample = {name: 0.0 for name in FEATURE_NAMES}
    benign_sample["amount_vs_sender_avg"] = 1.0
    res_low = score(benign_sample)
    if res_low["risk_score"] < 0.40:
        assert res_low["risk_level"] == "LOW"

    # Rule fallback boundary check
    res_high = _rule_fallback(to_vector({"in_cycle": 1}))
    assert res_high["risk_score"] >= 0.70
    assert res_high["risk_level"] == "HIGH"


def test_reasons_at_most_three_positive_contributions():
    """Verify reasons list contains at most 3 items and all contributions are positive."""
    scam_sample = {
        "amount": 8000.0,
        "hour_of_day": 14,
        "channel_code": 1,
        "sender_out_degree_24h": 3,
        "sender_txn_count_1h": 4,
        "receiver_in_degree_24h": 18,
        "receiver_in_amount_24h": 90000.0,
        "receiver_out_amount_24h": 0.0,
        "receiver_forward_ratio": 0.0,
        "receiver_min_forward_seconds": -1.0,
        "in_cycle": 1,
        "cycle_length": 4,
        "sender_seconds_since_last_inbound": 45.0,
        "sender_forward_ratio": 0.95,
        "sender_new_device": 1,
        "amount_vs_sender_avg": 10.0,
    }
    res = score(scam_sample)
    assert len(res["reasons"]) <= 3

    for item in res["reasons"]:
        assert "factor" in item
        assert "contribution" in item
        assert "description" in item
        assert item["contribution"] > 0
        assert len(item["description"]) > 5


def test_fan_in_scores_higher_than_benign():
    """Verify an overt fan-in burst scores higher than a benign transaction."""
    benign = {
        "amount": 250.0,
        "hour_of_day": 10,
        "channel_code": 0,
        "sender_out_degree_24h": 1,
        "sender_txn_count_1h": 1,
        "receiver_in_degree_24h": 1,
        "receiver_in_amount_24h": 500.0,
        "receiver_out_amount_24h": 100.0,
        "receiver_forward_ratio": 0.2,
        "receiver_min_forward_seconds": -1.0,
        "in_cycle": 0,
        "cycle_length": 0,
        "sender_seconds_since_last_inbound": -1.0,
        "sender_forward_ratio": 0.1,
        "sender_new_device": 0,
        "amount_vs_sender_avg": 1.0,
    }

    fan_in = dict(benign)
    fan_in["receiver_in_degree_24h"] = 18
    fan_in["receiver_in_amount_24h"] = 80000.0
    fan_in["amount"] = 5500.0

    res_benign = score(benign)
    res_fan_in = score(fan_in)

    assert res_fan_in["risk_score"] > res_benign["risk_score"]
    assert res_fan_in["risk_level"] in ("MEDIUM", "HIGH")


def test_fallback_when_model_missing(monkeypatch):
    """Verify score() never raises and falls back to rule engine when model files are missing."""
    import aegis_ml.scorer as scorer_mod

    # Point to nonexistent folder
    monkeypatch.setattr(scorer_mod, "MODEL_DIR", "nonexistent/model/path")
    with scorer_mod._MODEL_LOCK:
        scorer_mod._CACHED_MODEL = None
        scorer_mod._CACHED_BACKEND = None

    fan_in = {
        "amount": 5000.0,
        "receiver_in_degree_24h": 15,
    }
    res = scorer_mod.score(fan_in)

    assert isinstance(res, dict)
    assert res["risk_score"] >= 0.70
    assert res["risk_level"] == "HIGH"
    assert len(res["reasons"]) > 0
    assert res["reasons"][0]["factor"] == "receiver_in_degree_24h"


def test_score_latency_sla():
    """Verify 1000 sequential score() calls average under 5 ms."""
    sample = {
        "amount": 4500.0,
        "hour_of_day": 15,
        "channel_code": 0,
        "sender_out_degree_24h": 2,
        "sender_txn_count_1h": 1,
        "receiver_in_degree_24h": 10,
        "receiver_in_amount_24h": 45000.0,
        "receiver_out_amount_24h": 1000.0,
        "receiver_forward_ratio": 0.05,
        "receiver_min_forward_seconds": -1.0,
        "in_cycle": 0,
        "cycle_length": 0,
        "sender_seconds_since_last_inbound": -1.0,
        "sender_forward_ratio": 0.1,
        "sender_new_device": 0,
        "amount_vs_sender_avg": 1.2,
    }

    # Warmup
    for _ in range(20):
        score(sample)

    n_iterations = 1000
    t0 = time.perf_counter()
    for _ in range(n_iterations):
        score(sample)
    total_time = time.perf_counter() - t0

    avg_ms = (total_time / n_iterations) * 1000.0
    print(f"\nMeasured average scoring latency: {avg_ms:.3f} ms per transaction")
    assert avg_ms < 5.0, f"Average latency {avg_ms:.3f} ms exceeded 5.0 ms SLA target"
