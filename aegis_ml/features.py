"""Feature definitions and transformation utilities for Aegis ML.

Contains the exact 16 numeric features specified in the Aegis Exchange contract.
"""

from __future__ import annotations

from typing import Any
import numpy as np

FEATURE_NAMES: list[str] = [
    "amount",
    "hour_of_day",
    "channel_code",
    "sender_out_degree_24h",
    "sender_txn_count_1h",
    "receiver_in_degree_24h",
    "receiver_in_amount_24h",
    "receiver_out_amount_24h",
    "receiver_forward_ratio",
    "receiver_min_forward_seconds",
    "in_cycle",
    "cycle_length",
    "sender_seconds_since_last_inbound",
    "sender_forward_ratio",
    "sender_new_device",
    "amount_vs_sender_avg",
]

# Specific fallback defaults matching domain expectations
FEATURE_DEFAULTS: dict[str, float] = {
    "amount": 0.0,
    "hour_of_day": 0.0,
    "channel_code": 0.0,
    "sender_out_degree_24h": 0.0,
    "sender_txn_count_1h": 0.0,
    "receiver_in_degree_24h": 0.0,
    "receiver_in_amount_24h": 0.0,
    "receiver_out_amount_24h": 0.0,
    "receiver_forward_ratio": 0.0,
    "receiver_min_forward_seconds": -1.0,
    "in_cycle": 0.0,
    "cycle_length": 0.0,
    "sender_seconds_since_last_inbound": -1.0,
    "sender_forward_ratio": 0.0,
    "sender_new_device": 0.0,
    "amount_vs_sender_avg": 1.0,
}


def to_vector(features: dict[str, Any]) -> np.ndarray:
    """Convert a dictionary of features into a 1D numpy array of float64.

    Features are ordered strictly according to FEATURE_NAMES.
    Missing or invalid keys default gracefully to domain-safe fallback values.
    Never raises an exception.

    Args:
        features: Dictionary containing transaction and graph features.

    Returns:
        1D numpy array of shape (16,) and dtype float64.
    """
    vec = np.zeros(len(FEATURE_NAMES), dtype=np.float64)
    for idx, name in enumerate(FEATURE_NAMES):
        raw_val = features.get(name)
        if raw_val is None:
            vec[idx] = FEATURE_DEFAULTS.get(name, 0.0)
        else:
            try:
                vec[idx] = float(raw_val)
            except (ValueError, TypeError):
                vec[idx] = FEATURE_DEFAULTS.get(name, 0.0)
    return vec
