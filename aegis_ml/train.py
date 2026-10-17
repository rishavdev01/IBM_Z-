"""Model training and evaluation pipeline for Aegis ML.

Trains LightGBM and Scikit-Learn HistGradientBoosting models with explainability metadata.
Supports time-ordered train/val/test splits and per-typology recall evaluation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
import warnings
warnings.filterwarnings("ignore")

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score

from aegis_ml.dataset import build_training_frame
from aegis_ml.features import FEATURE_NAMES
from aegis_ml.synthetic_features import generate_synthetic_features

MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")
TYPOLOGIES = ["mule_fan_in", "rapid_forwarding", "circular_layering", "account_takeover"]


def evaluate_model(
    model: Any,
    X_test: np.ndarray,
    y_test: np.ndarray,
    typologies_test: np.ndarray,
) -> dict[str, Any]:
    """Evaluate a trained model or rule scorer on test data across key metrics."""
    if hasattr(model, "predict_proba"):
        y_prob = model.predict_proba(X_test)[:, 1]
    else:
        # Custom rule callable
        y_prob = model(X_test)

    roc_auc = float(roc_auc_score(y_test, y_prob)) if len(np.unique(y_test)) > 1 else 1.0
    pr_auc = float(average_precision_score(y_test, y_prob)) if len(np.unique(y_test)) > 1 else 1.0

    preds_40 = (y_prob >= 0.40).astype(int)
    preds_70 = (y_prob >= 0.70).astype(int)

    p_40 = float(precision_score(y_test, preds_40, zero_division=0))
    r_40 = float(recall_score(y_test, preds_40, zero_division=0))
    p_70 = float(precision_score(y_test, preds_70, zero_division=0))
    r_70 = float(recall_score(y_test, preds_70, zero_division=0))

    typ_recall: dict[str, float] = {}
    for typ in TYPOLOGIES:
        mask = typologies_test == typ
        if np.sum(mask) > 0:
            typ_recall[typ] = float(np.mean(preds_40[mask]))
        else:
            typ_recall[typ] = 0.0

    return {
        "roc_auc": round(roc_auc, 4),
        "pr_auc": round(pr_auc, 4),
        "precision_40": round(p_40, 4),
        "recall_40": round(r_40, 4),
        "precision_70": round(p_70, 4),
        "recall_70": round(r_70, 4),
        "typology_recall": {k: round(v, 4) for k, v in typ_recall.items()},
    }


def rule_baseline_predict(X: np.ndarray) -> np.ndarray:
    """Simple rule baseline for benchmark comparison."""
    # Column indices from FEATURE_NAMES:
    # 5: receiver_in_degree_24h, 10: in_cycle, 12: sender_seconds_since_last_inbound
    # 13: sender_forward_ratio, 14: sender_new_device, 15: amount_vs_sender_avg
    scores = np.full(len(X), 0.05, dtype=np.float64)
    for i in range(len(X)):
        s = 0.05
        # Mule Fan-In
        if X[i, 5] >= 8:
            s = max(s, 0.85)
        # Circular Layering
        if X[i, 10] == 1:
            s = max(s, 0.90)
        # Rapid Forwarding
        if X[i, 13] >= 0.85 and 0 <= X[i, 12] <= 200:
            s = max(s, 0.88)
        # Account Takeover
        if X[i, 14] == 1 and X[i, 15] >= 4.0:
            s = max(s, 0.85)
        scores[i] = s
    return scores


def train_pipeline(source: str = "synthetic", seed: int = 42) -> None:
    """Execute training pipeline for LightGBM, Scikit-Learn HistGradientBoosting, and rule baseline."""
    print("=" * 75)
    print(f"Aegis ML Training Pipeline (Source: {source}, Seed: {seed})")
    print("=" * 75)

    os.makedirs(MODEL_DIR, exist_ok=True)

    # 1. Load data
    if source == "csv":
        csv_path = "data/transactions_labeled.csv"
        print(f"Loading and extracting features from '{csv_path}'...")
        df = build_training_frame(csv_path)
    else:
        print("Generating synthetic feature dataset (20,000 rows)...")
        df = generate_synthetic_features(n_rows=20000, scam_rate=0.04, seed=seed)

    print(f"Dataset shape: {df.shape} | Scams: {df['is_scam'].sum()} ({df['is_scam'].mean() * 100:.2f}%)")

    # 2. Time-ordered split (70% train, 15% val, 15% test) without shuffling to avoid leakage
    n_total = len(df)
    n_train = int(n_total * 0.70)
    n_val = int(n_total * 0.15)

    df_train = df.iloc[:n_train]
    df_val = df.iloc[n_train : n_train + n_val]
    df_test = df.iloc[n_train + n_val :]

    X_train = df_train[FEATURE_NAMES].values.astype(np.float64)
    y_train = df_train["is_scam"].values.astype(int)

    X_val = df_val[FEATURE_NAMES].values.astype(np.float64)
    y_val = df_val["is_scam"].values.astype(int)

    X_test = df_test[FEATURE_NAMES].values.astype(np.float64)
    y_test = df_test["is_scam"].values.astype(int)
    typ_test = df_test["typology"].values

    # Compute benign medians for explainability
    benign_df = df_train[df_train["is_scam"] == 0]
    benign_medians = {feat: float(benign_df[feat].median()) for feat in FEATURE_NAMES}

    # 3. Train LightGBM model
    print("\n[1/3] Training LightGBM Backend...")
    neg_count = np.sum(y_train == 0)
    pos_count = max(1, np.sum(y_train == 1))
    scale_pos = neg_count / pos_count

    lgb_model = lgb.LGBMClassifier(
        random_state=seed,
        n_estimators=300,
        learning_rate=0.05,
        max_depth=6,
        scale_pos_weight=scale_pos,
        verbose=-1,
    )
    lgb_model.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(stopping_rounds=15, verbose=False)],
    )

    lgb_path = os.path.join(MODEL_DIR, "model_lightgbm.joblib")
    joblib.dump(lgb_model, lgb_path)
    print(f"Saved LightGBM model to: {lgb_path}")

    # 4. Train Scikit-Learn HistGradientBoosting Backend
    print("\n[2/3] Training Scikit-Learn Backend (HistGradientBoosting)...")
    sk_model = HistGradientBoostingClassifier(
        random_state=seed,
        class_weight="balanced",
        max_iter=300,
        early_stopping=True,
        n_iter_no_change=15,
    )
    sk_model.fit(X_train, y_train)

    sk_path = os.path.join(MODEL_DIR, "model_sklearn.joblib")
    joblib.dump(sk_model, sk_path)
    print(f"Saved Scikit-Learn model to: {sk_path}")

    # 5. Evaluate models on Test split
    print("\n[3/3] Evaluating on Test Split...")
    metrics_lgb = evaluate_model(lgb_model, X_test, y_test, typ_test)
    metrics_sk = evaluate_model(sk_model, X_test, y_test, typ_test)
    metrics_rule = evaluate_model(rule_baseline_predict, X_test, y_test, typ_test)

    # 6. Save metadata.json
    metadata = {
        "feature_order": FEATURE_NAMES,
        "benign_medians": benign_medians,
        "metrics": {
            "lightgbm": metrics_lgb,
            "sklearn": metrics_sk,
            "rule_baseline": metrics_rule,
        },
        "thresholds": {
            "alert": 0.40,
            "high": 0.70,
        },
    }
    meta_path = os.path.join(MODEL_DIR, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved metadata to: {meta_path}")

    # 7. Print Comparison Table
    print("\n" + "=" * 85)
    print("Test Set Evaluation Comparison Table:")
    print("=" * 85)
    print(f"{'Model':<16} | {'ROC-AUC':<8} | {'PR-AUC':<8} | {'P@0.40':<8} | {'R@0.40':<8} | {'P@0.70':<8} | {'R@0.70':<8}")
    print("-" * 85)
    for name, m in [("LightGBM", metrics_lgb), ("Scikit-Learn", metrics_sk), ("Rule Baseline", metrics_rule)]:
        print(
            f"{name:<16} | {m['roc_auc']:<8.4f} | {m['pr_auc']:<8.4f} | "
            f"{m['precision_40']:<8.4f} | {m['recall_40']:<8.4f} | "
            f"{m['precision_70']:<8.4f} | {m['recall_70']:<8.4f}"
        )
    print("-" * 85)

    print("\nRecall per Typology (at Alert Threshold 0.40):")
    print(f"{'Typology':<22} | {'LightGBM':<12} | {'Scikit-Learn':<12} | {'Rule Baseline':<12}")
    print("-" * 65)
    for typ in TYPOLOGIES:
        print(
            f"{typ:<22} | {metrics_lgb['typology_recall'].get(typ, 0.0):<12.4f} | "
            f"{metrics_sk['typology_recall'].get(typ, 0.0):<12.4f} | "
            f"{metrics_rule['typology_recall'].get(typ, 0.0):<12.4f}"
        )
    print("=" * 85)

    # Check PR-AUC target (>= 0.80)
    if metrics_lgb["pr_auc"] < 0.80:
        worst_typ = min(metrics_lgb["typology_recall"], key=metrics_lgb["typology_recall"].get)
        print(f"\n[!] WARNING: LightGBM PR-AUC is below 0.80 ({metrics_lgb['pr_auc']:.4f}). Weakest typology: '{worst_typ}'")
    else:
        print(f"\n[+] Success: Model achieves excellent PR-AUC of {metrics_lgb['pr_auc']:.4f} (>= 0.80 target).")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Train Aegis ML Models")
    parser.add_argument(
        "--source",
        choices=["synthetic", "csv"],
        default="synthetic",
        help="Training data source: 'synthetic' features or 'csv' transactions (default: synthetic)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    args = parser.parse_args()

    train_pipeline(source=args.source, seed=args.seed)


if __name__ == "__main__":
    main()
