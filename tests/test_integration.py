"""End-to-end coverage for the platform-owned integration adapters."""

from __future__ import annotations

import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.db as db
from app.main import app
from simulator.tokenization import tokenize


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Use an isolated database and explicit test-only analyst credentials."""
    monkeypatch.setenv("AEGIS_DB_PATH", str(tmp_path / "integration.db"))
    monkeypatch.setenv("AEGIS_ENV", "test")
    monkeypatch.setenv("AEGIS_ANALYST_TOKEN", "integration-analyst")
    monkeypatch.setenv("AEGIS_SENIOR_ANALYST_TOKEN", "integration-senior")
    monkeypatch.setenv("AEGIS_SALT", "integration-only-salt")
    monkeypatch.setenv("AEGIS_SCORER_MODE", "rules")
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    with TestClient(app, headers={"Authorization": "Bearer integration-analyst", "X-Role": "ANALYST"}) as test_client:
        yield test_client
    if db._engine is not None:
        db._engine.dispose()
    db._engine = None
    db._SessionLocal = None


def txn(sender: str, receiver: str, when: datetime, *, amount: float = 100.0) -> dict:
    """Build a simulator-compatible transaction payload."""
    return {
        "txn_id": str(uuid.uuid4()),
        "timestamp": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sender_token": tokenize(sender, salt="integration-only-salt"),
        "receiver_token": tokenize(receiver, salt="integration-only-salt"),
        "amount": amount,
        "channel": "UPI",
        "device_token": tokenize("device-" + sender, salt="integration-only-salt"),
        "_truth": {"is_scam": True, "typology": "mule_fan_in"},
    }


def test_ingestion_persists_scores_alert_and_is_idempotent(client):
    """A benign item is stored; suspicious fan-in becomes an alert once only."""
    now = datetime.now(timezone.utc)
    benign = txn("benign", "shop", now)
    # Deliberately contradictory truth ensures the scoring path ignores `_truth`.
    benign["_truth"] = {"is_scam": True, "typology": "mule_fan_in"}
    response = client.post("/v1/transactions", json=benign)
    assert response.status_code == 200
    assert response.json()["risk_level"] == "LOW"
    assert response.json()["alert_id"] is None

    responses = []
    for index in range(8):
        payload = txn(f"sender-{index}", "mule", now + timedelta(seconds=index))
        responses.append(client.post("/v1/transactions", json=payload))
    assert all(result.status_code == 200 for result in responses)
    flagged = [result.json() for result in responses if result.json()["alert_id"] is not None]
    assert flagged, "the accumulating many-sender pattern should cross the alert threshold"
    original_payload = txn("duplicate", "mule", now + timedelta(seconds=30))
    first = client.post("/v1/transactions", json=original_payload).json()
    again = client.post("/v1/transactions", json=original_payload).json()
    assert again == first
    stored_alerts = client.get("/v1/alerts").json()
    assert sum(alert["txn_id"] == original_payload["txn_id"] for alert in stored_alerts) <= 1
    detail = client.get(f"/v1/alerts/{flagged[0]['alert_id']}").json()
    assert "_truth" not in detail
    assert len(detail["features"]) == 16
    decision = client.post(f"/v1/alerts/{flagged[0]['alert_id']}/decision", headers={
        "X-Role": "SENIOR_ANALYST", "Authorization": "Bearer integration-senior",
    }, json={"decision": "DISMISS", "analyst": "test-analyst", "note": "Integration flow"})
    assert decision.status_code == 200
    audit = client.get("/v1/audit", headers={
        "X-Role": "SENIOR_ANALYST", "Authorization": "Bearer integration-senior",
    })
    assert audit.status_code == 200
    assert any(entry["action"] == "DECISION" for entry in audit.json())


def test_role_header_cannot_grant_senior_and_unmask_requires_token(client):
    """A senior role header without its senior credential is rejected."""
    payload = txn("privacy-sender", "privacy-receiver", datetime.now(timezone.utc))
    alert = client.post("/v1/transactions", json=payload).json()
    # Seed a high-risk alert directly to exercise role access independently of rules.
    from app.alerts_store import create_alert
    alert_id = create_alert(payload, {}, {"risk_score": 0.9, "risk_level": "HIGH"})
    forged = client.get("/v1/alerts?unmask=true", headers={"X-Role": "SENIOR_ANALYST"})
    assert forged.status_code == 401
    senior = client.get("/v1/alerts?unmask=true", headers={
        "X-Role": "SENIOR_ANALYST", "Authorization": "Bearer integration-senior",
    })
    assert senior.status_code == 200
    item = next(row for row in senior.json() if row["alert_id"] == alert_id)
    assert item["sender_token"] == payload["sender_token"]
    masked = client.get(f"/v1/alerts/{alert_id}")
    assert masked.json()["sender_token"].endswith("...")
    nested_graph = client.get(f"/v1/alerts/{alert_id}/graph")
    assert all("..." in node["id"] for node in nested_graph.json()["nodes"])


def test_general_graph_route_and_input_validation(client):
    now = datetime.now(timezone.utc)
    payload = txn("graph-source", "graph-target", now)
    assert client.post("/v1/transactions", json=payload).status_code == 200
    token = payload["sender_token"]
    graph = client.get(f"/v1/graph/neighborhood/{token}")
    assert graph.status_code == 200
    assert graph.json()["nodes"]
    assert graph.json()["nodes"][0]["id"].endswith("...")
    invalid = dict(payload, txn_id="not-a-uuid")
    assert client.post("/v1/transactions", json=invalid).status_code == 422

    # A fresh engine restored from SQLite sees the same persisted neighborhood.
    from app.alerts_store import list_recent_transactions
    from app.graph_engine import GraphEngine
    restored = GraphEngine()
    restored.restore_transactions(list_recent_transactions("2000-01-01T00:00:00+00:00"))
    assert restored.neighborhood(token)["edges"]


def test_scoring_exception_uses_explainable_rule_fallback(client, monkeypatch):
    import app.pipeline as pipeline
    monkeypatch.setattr(pipeline, "score_features", lambda _: (_ for _ in ()).throw(RuntimeError("test scorer failure")))
    payload = txn("fallback", "receiver", datetime.now(timezone.utc))
    response = client.post("/v1/transactions", json=payload)
    assert response.status_code == 200
    assert response.json()["risk_level"] == "LOW"


def test_alert_write_failure_rolls_back_transaction_and_retry_recovers(client, monkeypatch):
    from sqlalchemy.orm import Session
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "score_features", lambda _: {"risk_score": .95, "risk_level": "HIGH", "reasons": []})
    payload = txn("rollback-sender", "rollback-receiver", datetime.now(timezone.utc))
    original_flush = Session.flush
    flush_count = 0

    def fail_second_flush(session, *args, **kwargs):
        nonlocal flush_count
        flush_count += 1
        if flush_count == 2:
            raise RuntimeError("simulated alert insert failure")
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", fail_second_flush)
    with pytest.raises(RuntimeError, match="simulated alert insert failure"):
        client.post("/v1/transactions", json=payload)
    with sqlite3.connect(db._resolve_db_path()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE txn_id=?", (payload["txn_id"],)).fetchone()[0] == 0
    monkeypatch.setattr(Session, "flush", original_flush)
    retry = client.post("/v1/transactions", json=payload)
    assert retry.status_code == 200
    assert retry.json()["alert_id"] is not None
    assert client.get("/v1/alerts?limit=1000").json()


def test_retry_recovers_legacy_partial_transaction_alert(client, monkeypatch):
    from app.alerts_store import record_txn
    import app.pipeline as pipeline

    payload = txn("partial-sender", "partial-receiver", datetime.now(timezone.utc))
    record_txn(payload, {"risk_score": .91, "risk_level": "HIGH", "reasons": []})
    monkeypatch.setattr(pipeline, "score_features", lambda _: {"risk_score": .1, "risk_level": "LOW", "reasons": []})
    recovered = client.post("/v1/transactions", json=payload)
    assert recovered.status_code == 200
    assert recovered.json()["risk_score"] == .91
    assert recovered.json()["alert_id"] is not None
    duplicate = client.post("/v1/transactions", json=payload)
    assert duplicate.json() == recovered.json()
    assert len([alert for alert in client.get("/v1/alerts?limit=1000").json() if alert["txn_id"] == payload["txn_id"]]) == 1


def test_concurrent_duplicate_ingestion_creates_one_transaction_and_alert(client, monkeypatch):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "score_features", lambda _: {"risk_score": .95, "risk_level": "HIGH", "reasons": []})
    payload = txn("concurrent-sender", "concurrent-receiver", datetime.now(timezone.utc))
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.post("/v1/transactions", json=payload), range(8)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["alert_id"] for response in responses}) == 1
    with sqlite3.connect(db._resolve_db_path()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE txn_id=?", (payload["txn_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM alerts WHERE txn_id=?", (payload["txn_id"],)).fetchone()[0] == 1


def test_authentication_and_stats_authorization(client):
    assert client.get("/v1/stats", headers={"Authorization": ""}).status_code == 401
    assert client.get("/v1/stats", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/v1/stats").status_code == 200
    assert client.get("/v1/audit", headers={"X-Role": "SENIOR_ANALYST"}).status_code == 401
    assert client.get("/v1/audit", headers={"X-Role": "SENIOR_ANALYST", "Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/v1/audit").status_code == 403
    assert client.get("/v1/audit", headers={"X-Role": "SENIOR_ANALYST", "Authorization": "Bearer integration-senior"}).status_code == 200


def test_production_secret_validation():
    from app.auth import validate_production_secrets

    valid = {
        "AEGIS_SALT": "salt-K7mQ2vN9xP4bR8wC1dF6hJ3kL5sT0yZa",
        "AEGIS_ANALYST_TOKEN": "analyst-T4cV8nB2mQ6xR0pK5dH9sW3jL1yF7aZe",
        "AEGIS_SENIOR_ANALYST_TOKEN": "senior-P9wD3kM7xB1vN5qR8cH2sF6jL0yT4aZe",
    }
    validate_production_secrets(valid)
    for bad in (
        {},
        {**valid, "AEGIS_SALT": ""},
        {**valid, "AEGIS_SALT": "hackathon-salt"},
        {**valid, "AEGIS_ANALYST_TOKEN": "short"},
        {**valid, "AEGIS_SENIOR_ANALYST_TOKEN": "a" * 32},
        {**valid, "AEGIS_SENIOR_ANALYST_TOKEN": "abcd" * 8},
        {**valid, "AEGIS_SALT": "0123456789" * 4},
    ):
        with pytest.raises(RuntimeError):
            validate_production_secrets(bad)


def test_spoofed_analyst_name_does_not_change_decision_attribution(client):
    from app.alerts_store import create_alert

    payload = txn("audit-sender", "audit-receiver", datetime.now(timezone.utc))
    alert_id = create_alert(payload, {}, {"risk_score": .9, "risk_level": "HIGH"})
    response = client.post(
        f"/v1/alerts/{alert_id}/decision",
        json={"decision": "APPROVE", "analyst": "chief-executive", "note": "reviewed"},
    )
    assert response.status_code == 200
    assert response.json()["analyst"] == "ANALYST"
    detail = client.get(f"/v1/alerts/{alert_id}").json()
    assert detail["decisions"][0]["analyst"] == "ANALYST"
    assert detail["decisions"][0]["note"] == "reviewed"


def test_concurrent_decisions_only_one_wins(client):
    from app.alerts_store import create_alert

    payload = txn("decision-sender", "decision-receiver", datetime.now(timezone.utc))
    alert_id = create_alert(payload, {}, {"risk_score": .9, "risk_level": "HIGH"})
    def decide(value):
        return client.post(f"/v1/alerts/{alert_id}/decision", json={"decision": value, "analyst": "spoof", "note": ""})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(decide, ("APPROVE", "DISMISS")))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(client.get(f"/v1/alerts/{alert_id}").json()["decisions"]) == 1


def test_additive_migration_preserves_existing_transactions(tmp_path, monkeypatch):
    """The additive reason migration retains existing transaction rows."""
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE transactions (
        txn_id VARCHAR PRIMARY KEY NOT NULL, timestamp VARCHAR NOT NULL,
        sender_token VARCHAR NOT NULL, receiver_token VARCHAR NOT NULL,
        amount FLOAT NOT NULL, channel VARCHAR NOT NULL, device_token VARCHAR NOT NULL,
        risk_score FLOAT NOT NULL, risk_level VARCHAR NOT NULL,
        truth_is_scam INTEGER, truth_typology VARCHAR)""")
    connection.execute("INSERT INTO transactions VALUES ('legacy-id','2026-01-01T00:00:00Z','s','r',10,'UPI','d',0.1,'LOW',NULL,NULL)")
    connection.commit()
    connection.close()
    monkeypatch.setenv("AEGIS_DB_PATH", str(path))
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    with sqlite3.connect(path) as migrated:
        assert migrated.execute("SELECT txn_id, reasons_json, features_json FROM transactions").fetchone() == ("legacy-id", "[]", "{}")
    db._engine.dispose()
    db._engine = None
    db._SessionLocal = None
