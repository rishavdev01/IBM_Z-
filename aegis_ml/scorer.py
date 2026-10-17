"""Stub scorer (Part 3 replaces this with the trained model)."""

from __future__ import annotations


def score(features: dict) -> dict:
    """Return a contract-shaped score result.

    Stub always returns a low-risk result so ingestion can run before the
    trained model exists. Must never raise.
    """
    _ = features
    return {
        "risk_score": 0.1,
        "risk_level": "LOW",
        "reasons": [],
    }
