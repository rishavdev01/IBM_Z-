"""Synthetic feature dataset generator for Aegis ML.

Generates realistic labeled graph feature distributions matching the contract specifications,
reflecting distinct topological signatures for the 4 scam typologies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aegis_ml.features import FEATURE_NAMES


def generate_synthetic_features(
    n_rows: int = 20000,
    scam_rate: float = 0.04,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate a synthetic labeled DataFrame with all 16 feature columns.

    Args:
        n_rows: Total number of rows to generate.
        scam_rate: Proportion of scam instances (~0.04 = 4%).
        seed: Random seed for reproducibility.

    Returns:
        pd.DataFrame containing the 16 feature columns, 'is_scam' (0/1),
        and 'typology' (str).
    """
    rng = np.random.default_rng(seed)

    n_scam = max(4, int(n_rows * scam_rate))
    n_benign = n_rows - n_scam

    # 1. Benign records
    benign_amounts = np.clip(rng.lognormal(mean=np.log(1500.0), sigma=0.6, size=n_benign), 50.0, 5000.0)
    # 1% high spike
    spikes = rng.random(n_benign) < 0.01
    benign_amounts[spikes] = rng.uniform(10000.0, 45000.0, size=np.sum(spikes))

    benign_data = {
        "amount": benign_amounts,
        "hour_of_day": rng.integers(0, 24, size=n_benign),
        "channel_code": rng.choice([0, 1, 2], p=[0.70, 0.20, 0.10], size=n_benign),
        "sender_out_degree_24h": rng.integers(1, 5, size=n_benign),
        "sender_txn_count_1h": rng.integers(1, 4, size=n_benign),
        "receiver_in_degree_24h": rng.integers(1, 5, size=n_benign),
        "receiver_in_amount_24h": rng.uniform(100.0, 10000.0, size=n_benign),
        "receiver_out_amount_24h": rng.uniform(0.0, 5000.0, size=n_benign),
        "receiver_forward_ratio": rng.uniform(0.0, 0.40, size=n_benign),
        "receiver_min_forward_seconds": rng.choice([-1.0, 3600.0, 7200.0, 18000.0], size=n_benign),
        "in_cycle": np.zeros(n_benign, dtype=int),
        "cycle_length": np.zeros(n_benign, dtype=int),
        "sender_seconds_since_last_inbound": rng.choice([-1.0, 3600.0, 14400.0, 86400.0], size=n_benign),
        "sender_forward_ratio": rng.uniform(0.0, 0.35, size=n_benign),
        "sender_new_device": (rng.random(n_benign) < 0.02).astype(int),
        "amount_vs_sender_avg": np.clip(rng.normal(1.0, 0.25, size=n_benign), 0.5, 2.0),
        "is_scam": np.zeros(n_benign, dtype=int),
        "typology": ["none"] * n_benign,
    }
    df_benign = pd.DataFrame(benign_data)

    # 2. Scam records split equally across 4 typologies
    typologies = ["mule_fan_in", "rapid_forwarding", "circular_layering", "account_takeover"]
    n_per_typ = n_scam // len(typologies)
    scam_dfs = []

    for typ in typologies:
        # Fan-in: high receiver_in_degree_24h, high receiver_in_amount_24h
        if typ == "mule_fan_in":
            amt = rng.uniform(2000.0, 9000.0, size=n_per_typ)
            in_deg = rng.integers(8, 25, size=n_per_typ)
            df_typ = pd.DataFrame({
                "amount": amt,
                "hour_of_day": rng.integers(0, 24, size=n_per_typ),
                "channel_code": rng.choice([0, 1, 2], p=[0.5, 0.4, 0.1], size=n_per_typ),
                "sender_out_degree_24h": rng.integers(1, 4, size=n_per_typ),
                "sender_txn_count_1h": rng.integers(1, 3, size=n_per_typ),
                "receiver_in_degree_24h": in_deg,
                "receiver_in_amount_24h": amt * in_deg * rng.uniform(0.8, 1.2, size=n_per_typ),
                "receiver_out_amount_24h": rng.uniform(0.0, 2000.0, size=n_per_typ),
                "receiver_forward_ratio": rng.uniform(0.0, 0.10, size=n_per_typ),
                "receiver_min_forward_seconds": np.full(n_per_typ, -1.0),
                "in_cycle": np.zeros(n_per_typ, dtype=int),
                "cycle_length": np.zeros(n_per_typ, dtype=int),
                "sender_seconds_since_last_inbound": np.full(n_per_typ, -1.0),
                "sender_forward_ratio": rng.uniform(0.0, 0.20, size=n_per_typ),
                "sender_new_device": np.zeros(n_per_typ, dtype=int),
                "amount_vs_sender_avg": rng.uniform(0.8, 1.4, size=n_per_typ),
                "is_scam": np.ones(n_per_typ, dtype=int),
                "typology": [typ] * n_per_typ,
            })

        # Rapid forwarding: low sender_seconds_since_last_inbound (30-180s), high sender_forward_ratio (0.90-0.98)
        elif typ == "rapid_forwarding":
            amt = rng.uniform(3000.0, 15000.0, size=n_per_typ)
            gap = rng.uniform(30.0, 180.0, size=n_per_typ)
            fwd_ratio = rng.uniform(0.90, 0.98, size=n_per_typ)
            df_typ = pd.DataFrame({
                "amount": amt,
                "hour_of_day": rng.integers(0, 24, size=n_per_typ),
                "channel_code": rng.choice([0, 1], p=[0.4, 0.6], size=n_per_typ),
                "sender_out_degree_24h": rng.integers(1, 4, size=n_per_typ),
                "sender_txn_count_1h": rng.integers(2, 6, size=n_per_typ),
                "receiver_in_degree_24h": rng.integers(1, 4, size=n_per_typ),
                "receiver_in_amount_24h": amt * 1.5,
                "receiver_out_amount_24h": amt * fwd_ratio,
                "receiver_forward_ratio": fwd_ratio,
                "receiver_min_forward_seconds": gap,
                "in_cycle": np.zeros(n_per_typ, dtype=int),
                "cycle_length": np.zeros(n_per_typ, dtype=int),
                "sender_seconds_since_last_inbound": gap,
                "sender_forward_ratio": fwd_ratio,
                "sender_new_device": np.zeros(n_per_typ, dtype=int),
                "amount_vs_sender_avg": rng.uniform(1.0, 2.5, size=n_per_typ),
                "is_scam": np.ones(n_per_typ, dtype=int),
                "typology": [typ] * n_per_typ,
            })

        # Circular layering: in_cycle = 1, cycle_length 3-5, large amounts
        elif typ == "circular_layering":
            amt = rng.uniform(20000.0, 90000.0, size=n_per_typ)
            df_typ = pd.DataFrame({
                "amount": amt,
                "hour_of_day": rng.integers(0, 24, size=n_per_typ),
                "channel_code": rng.choice([1, 2], p=[0.7, 0.3], size=n_per_typ),
                "sender_out_degree_24h": rng.integers(1, 3, size=n_per_typ),
                "sender_txn_count_1h": rng.integers(1, 4, size=n_per_typ),
                "receiver_in_degree_24h": rng.integers(1, 3, size=n_per_typ),
                "receiver_in_amount_24h": amt * rng.uniform(0.9, 1.1, size=n_per_typ),
                "receiver_out_amount_24h": amt * rng.uniform(0.9, 1.0, size=n_per_typ),
                "receiver_forward_ratio": rng.uniform(0.90, 0.99, size=n_per_typ),
                "receiver_min_forward_seconds": rng.uniform(60.0, 300.0, size=n_per_typ),
                "in_cycle": np.ones(n_per_typ, dtype=int),
                "cycle_length": rng.integers(3, 6, size=n_per_typ),
                "sender_seconds_since_last_inbound": rng.uniform(60.0, 400.0, size=n_per_typ),
                "sender_forward_ratio": rng.uniform(0.90, 0.99, size=n_per_typ),
                "sender_new_device": np.zeros(n_per_typ, dtype=int),
                "amount_vs_sender_avg": rng.uniform(2.0, 6.0, size=n_per_typ),
                "is_scam": np.ones(n_per_typ, dtype=int),
                "typology": [typ] * n_per_typ,
            })

        # Account takeover: sender_new_device = 1, amount_vs_sender_avg = 5-20x
        else:
            base_amt = rng.uniform(800.0, 2500.0, size=n_per_typ)
            mult = rng.uniform(5.0, 20.0, size=n_per_typ)
            amt = base_amt * mult
            df_typ = pd.DataFrame({
                "amount": amt,
                "hour_of_day": rng.integers(0, 24, size=n_per_typ),
                "channel_code": rng.choice([0, 1], p=[0.3, 0.7], size=n_per_typ),
                "sender_out_degree_24h": rng.integers(1, 4, size=n_per_typ),
                "sender_txn_count_1h": rng.integers(3, 7, size=n_per_typ),
                "receiver_in_degree_24h": rng.integers(1, 3, size=n_per_typ),
                "receiver_in_amount_24h": amt,
                "receiver_out_amount_24h": rng.uniform(0.0, 1000.0, size=n_per_typ),
                "receiver_forward_ratio": rng.uniform(0.0, 0.20, size=n_per_typ),
                "receiver_min_forward_seconds": np.full(n_per_typ, -1.0),
                "in_cycle": np.zeros(n_per_typ, dtype=int),
                "cycle_length": np.zeros(n_per_typ, dtype=int),
                "sender_seconds_since_last_inbound": rng.choice([-1.0, 7200.0, 28800.0], size=n_per_typ),
                "sender_forward_ratio": rng.uniform(0.5, 1.5, size=n_per_typ),
                "sender_new_device": np.ones(n_per_typ, dtype=int),
                "amount_vs_sender_avg": mult,
                "is_scam": np.ones(n_per_typ, dtype=int),
                "typology": [typ] * n_per_typ,
            })

        scam_dfs.append(df_typ)

    df_all_scams = pd.concat(scam_dfs, ignore_index=True)
    df_combined = pd.concat([df_benign, df_all_scams], ignore_index=True)

    # Reorder columns to have 16 features first, then is_scam, then typology
    cols = FEATURE_NAMES + ["is_scam", "typology"]
    df_combined = df_combined[cols]

    # Deterministic shuffle to interleave benign and scam
    df_shuffled = df_combined.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    return df_shuffled
