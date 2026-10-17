"""In-memory alert store stub (Part 4 replaces this with SQLite)."""

from __future__ import annotations

from threading import Lock

_lock = Lock()
_next_id = 1
_alerts: dict[int, dict] = {}
_txns: list[dict] = []


def create_alert(txn: dict, features: dict, result: dict) -> int:
    """Store an alert and return an incrementing alert_id."""
    global _next_id
    with _lock:
        alert_id = _next_id
        _next_id += 1
        _alerts[alert_id] = {
            "alert_id": alert_id,
            "txn": txn,
            "features": features,
            "result": result,
            "status": "OPEN",
        }
        return alert_id


def record_txn(txn: dict, result: dict) -> None:
    """Record a scored transaction (no-op storage in the stub)."""
    with _lock:
        _txns.append({"txn": txn, "result": result})
