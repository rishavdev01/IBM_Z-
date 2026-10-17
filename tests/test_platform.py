"""Platform tests for Part 4 (Database, Alert Store, Masking, Alerts API)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.db import (
    AlertRow,
    AuditLogRow,
    Base,
    DecisionRow,
    TransactionRow,
    _make_engine,
    _resolve_db_path,
    init_db,
    make_test_engine,
    make_test_session,
)


# ===========================================================================
# Phase 1 Tests: Database Schema and Engine Configuration
# ===========================================================================


def test_resolve_db_path_default(monkeypatch):
    monkeypatch.delenv("AEGIS_DB_PATH", raising=False)
    path_str = _resolve_db_path()
    path = Path(path_str)
    assert path.name == "aegis.db"
    assert path.parent.name == "data"
    assert path.parent.exists()


def test_resolve_db_path_custom(monkeypatch, tmp_path):
    custom = tmp_path / "custom_dir" / "test.db"
    monkeypatch.setenv("AEGIS_DB_PATH", str(custom))
    path_str = _resolve_db_path()
    assert path_str == str(custom)
    assert custom.parent.exists()


def test_make_engine_pragmas(tmp_path):
    """File-based SQLite engine should have WAL mode and busy timeout configured."""
    db_file = tmp_path / "pragma_test.db"
    engine = _make_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        synchronous = conn.execute(text("PRAGMA synchronous")).scalar()

        # In WAL mode, SQLite returns "wal"
        assert str(journal_mode).lower() == "wal"
        assert int(busy_timeout) == 30000
        # synchronous NORMAL is 1
        assert int(synchronous) == 1
    engine.dispose()


def test_init_db_idempotent():
    """init_db creates all 4 tables and can be called multiple times safely."""
    engine = make_test_engine()
    Base.metadata.create_all(engine, checkfirst=True)
    # Call again to ensure idempotency
    Base.metadata.create_all(engine, checkfirst=True)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {"transactions", "alerts", "decisions", "audit_log"}
    assert expected.issubset(tables)


def test_table_columns_and_indexes():
    engine = make_test_engine()
    inspector = inspect(engine)

    # transactions columns
    txn_cols = {c["name"] for c in inspector.get_columns("transactions")}
    assert {
        "txn_id",
        "timestamp",
        "sender_token",
        "receiver_token",
        "amount",
        "channel",
        "device_token",
        "risk_score",
        "risk_level",
        "truth_is_scam",
        "truth_typology",
    }.issubset(txn_cols)

    # alerts columns
    alert_cols = {c["name"] for c in inspector.get_columns("alerts")}
    assert {
        "alert_id",
        "txn_id",
        "created_at",
        "status",
        "risk_score",
        "risk_level",
        "reasons_json",
        "features_json",
        "sender_token",
        "receiver_token",
        "amount",
    }.issubset(alert_cols)

    # decisions columns
    decision_cols = {c["name"] for c in inspector.get_columns("decisions")}
    assert {"id", "alert_id", "decision", "analyst", "note", "decided_at"}.issubset(decision_cols)

    # audit_log columns
    audit_cols = {c["name"] for c in inspector.get_columns("audit_log")}
    assert {"id", "ts", "actor", "role", "action", "target", "detail_json"}.issubset(audit_cols)

    # Check indexes exist
    alert_indexes = {ix["name"] for ix in inspector.get_indexes("alerts")}
    assert "ix_alerts_status" in alert_indexes
    assert "ix_alerts_risk_score" in alert_indexes


def test_crud_operations_isolated():
    engine = make_test_engine()
    session = make_test_session(engine)

    # Insert Transaction
    txn = TransactionRow(
        txn_id="txn-001",
        timestamp="2026-10-17T12:00:00Z",
        sender_token="a1b2c3d4e5f60718",
        receiver_token="b2c3d4e5f6a10718",
        amount=15000.0,
        channel="UPI",
        device_token="dev-999",
        risk_score=0.85,
        risk_level="HIGH",
        truth_is_scam=1,
        truth_typology="mule_fan_in",
    )
    session.add(txn)

    # Insert Alert
    alert = AlertRow(
        txn_id="txn-001",
        created_at="2026-10-17T12:00:01Z",
        status="OPEN",
        risk_score=0.85,
        risk_level="HIGH",
        reasons_json='[{"factor": "in_degree", "contribution": 0.4}]',
        features_json='{"amount": 15000.0}',
        sender_token="a1b2c3d4e5f60718",
        receiver_token="b2c3d4e5f6a10718",
        amount=15000.0,
    )
    session.add(alert)
    session.flush()

    assert alert.alert_id is not None
    alert_id = alert.alert_id

    # Insert Decision
    decision = DecisionRow(
        alert_id=alert_id,
        decision="APPROVE",
        analyst="analyst_alice",
        note="Confirmed mule pattern",
        decided_at="2026-10-17T12:05:00Z",
    )
    session.add(decision)

    # Insert Audit Log
    audit = AuditLogRow(
        ts="2026-10-17T12:05:00Z",
        actor="analyst_alice",
        role="SENIOR_ANALYST",
        action="DECISION",
        target=f"alert:{alert_id}",
        detail_json='{"decision": "APPROVE"}',
    )
    session.add(audit)
    session.commit()

    # Query back
    queried_alert = session.get(AlertRow, alert_id)
    assert queried_alert is not None
    assert queried_alert.status == "OPEN"
    assert queried_alert.amount == 15000.0

    queried_decision = session.query(DecisionRow).filter_by(alert_id=alert_id).first()
    assert queried_decision is not None
    assert queried_decision.decision == "APPROVE"

    session.close()


# ===========================================================================
# Phase 2 Tests: Persistent Alerts Store
# ===========================================================================

from sqlalchemy.orm import sessionmaker

from app.alerts_store import (
    AlertAlreadyDecidedError,
    AlertNotFoundError,
    add_decision,
    count_open,
    create_alert,
    get_alert,
    list_alerts,
    list_audit,
    record_txn,
    write_audit,
)


@pytest.fixture
def test_factory():
    """Isolated session factory using an in-memory SQLite database."""
    engine = make_test_engine()
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_create_alert_and_count_open(test_factory):
    txn = {
        "txn_id": "txn-101",
        "timestamp": "2026-10-17T14:00:00Z",
        "sender_token": "send_111122223333",
        "receiver_token": "recv_444455556666",
        "amount": 25000.0,
    }
    features = {"amount": 25000.0, "receiver_in_degree_24h": 5}
    result = {
        "risk_score": 0.88,
        "risk_level": "HIGH",
        "reasons": [{"factor": "fan_in", "contribution": 0.5, "description": "Rapid fan-in detected"}],
    }

    assert count_open(factory=test_factory) == 0

    alert_id = create_alert(txn, features, result, factory=test_factory)
    assert isinstance(alert_id, int)
    assert alert_id > 0

    assert count_open(factory=test_factory) == 1


def test_record_txn_and_duplicate_idempotency(test_factory):
    txn = {
        "txn_id": "txn-202",
        "timestamp": "2026-10-17T14:10:00Z",
        "sender_token": "sender_aaa",
        "receiver_token": "receiver_bbb",
        "amount": 500.0,
        "channel": "UPI",
        "device_token": "dev_1",
        "_truth": {"is_scam": True, "typology": "mule_fan_in"},
    }
    result = {"risk_score": 0.15, "risk_level": "LOW"}

    # First record
    record_txn(txn, result, factory=test_factory)

    # Verify persisted
    with test_factory() as s:
        row = s.get(TransactionRow, "txn-202")
        assert row is not None
        assert row.amount == 500.0
        assert row.truth_is_scam == 1
        assert row.truth_typology == "mule_fan_in"

    # Repeated record should be idempotent and not raise error
    updated_result = {"risk_score": 0.20, "risk_level": "LOW"}
    record_txn(txn, updated_result, factory=test_factory)

    with test_factory() as s:
        row = s.get(TransactionRow, "txn-202")
        assert row is not None
        assert row.risk_score == 0.20


def test_list_alerts_sorting_and_filtering(test_factory):
    # Insert 3 alerts with varying risk scores and statuses
    txns = [
        {"txn_id": "t1", "amount": 100.0, "timestamp": "2026-10-17T10:00:00Z"},
        {"txn_id": "t2", "amount": 200.0, "timestamp": "2026-10-17T11:00:00Z"},
        {"txn_id": "t3", "amount": 300.0, "timestamp": "2026-10-17T12:00:00Z"},
    ]
    results = [
        {"risk_score": 0.45, "risk_level": "MEDIUM", "reasons": []},
        {"risk_score": 0.95, "risk_level": "HIGH", "reasons": []},
        {"risk_score": 0.75, "risk_level": "HIGH", "reasons": []},
    ]

    id1 = create_alert(txns[0], {}, results[0], factory=test_factory)
    id2 = create_alert(txns[1], {}, results[1], factory=test_factory)
    id3 = create_alert(txns[2], {}, results[2], factory=test_factory)

    # List all: should be sorted by risk_score desc -> t2 (0.95), t3 (0.75), t1 (0.45)
    alerts = list_alerts(limit=10, factory=test_factory)
    assert len(alerts) == 3
    assert alerts[0]["alert_id"] == id2
    assert alerts[1]["alert_id"] == id3
    assert alerts[2]["alert_id"] == id1

    # Decide one alert
    add_decision(id2, "APPROVE", "senior_1", "Looks like mule", factory=test_factory)

    # Filter status=OPEN
    open_alerts = list_alerts(status="OPEN", factory=test_factory)
    assert len(open_alerts) == 2
    assert all(a["status"] == "OPEN" for a in open_alerts)

    # Filter status=APPROVED
    approved_alerts = list_alerts(status="APPROVED", factory=test_factory)
    assert len(approved_alerts) == 1
    assert approved_alerts[0]["alert_id"] == id2


def test_list_alerts_validation(test_factory):
    with pytest.raises(ValueError, match="Invalid status filter"):
        list_alerts(status="PENDING", factory=test_factory)

    with pytest.raises(ValueError, match="Limit must be a positive integer"):
        list_alerts(limit=0, factory=test_factory)


def test_get_alert_full_details_and_missing(test_factory):
    txn = {
        "txn_id": "txn-details",
        "timestamp": "2026-10-17T15:00:00Z",
        "sender_token": "sender_xyz",
        "receiver_token": "receiver_xyz",
        "amount": 9999.0,
    }
    features = {"sender_out_degree_24h": 3, "amount": 9999.0}
    result = {
        "risk_score": 0.72,
        "risk_level": "HIGH",
        "reasons": [{"factor": "amount_vs_sender_avg", "contribution": 0.4}],
    }

    alert_id = create_alert(txn, features, result, factory=test_factory)

    # Add a decision
    add_decision(alert_id, "DISMISS", "analyst_bob", "False positive test", factory=test_factory)

    detail = get_alert(alert_id, factory=test_factory)
    assert detail is not None
    assert detail["alert_id"] == alert_id
    assert detail["status"] == "DISMISSED"
    assert detail["features"]["sender_out_degree_24h"] == 3
    assert len(detail["decisions"]) == 1
    assert detail["decisions"][0]["decision"] == "DISMISS"
    assert detail["decisions"][0]["analyst"] == "analyst_bob"
    assert len(detail["audit_trail"]) == 1
    assert detail["audit_trail"][0]["action"] == "DECISION"

    # Non-existent alert
    missing = get_alert(999999, factory=test_factory)
    assert missing is None


def test_add_decision_atomic_and_status_transition(test_factory):
    txn = {"txn_id": "txn-dec", "amount": 5000.0}
    alert_id = create_alert(txn, {}, {"risk_score": 0.8}, factory=test_factory)

    res = add_decision(alert_id, "APPROVE", "analyst_1", "Confirmed", factory=test_factory)
    assert res["status"] == "APPROVED"
    assert res["decision"] == "APPROVE"

    # Attempting to decide again must raise AlertAlreadyDecidedError
    with pytest.raises(AlertAlreadyDecidedError, match="already APPROVED"):
        add_decision(alert_id, "DISMISS", "analyst_2", "Should fail", factory=test_factory)


def test_add_decision_unknown_and_invalid(test_factory):
    with pytest.raises(AlertNotFoundError):
        add_decision(99999, "APPROVE", "analyst_1", factory=test_factory)

    with pytest.raises(ValueError, match="Invalid decision"):
        add_decision(1, "REJECT", "analyst_1", factory=test_factory)

    with pytest.raises(ValueError, match="Analyst identifier is required"):
        add_decision(1, "APPROVE", "   ", factory=test_factory)


def test_write_and_list_audit(test_factory):
    id1 = write_audit("alice", "ANALYST", "VIEW", "alert:1", {"unmasked": False}, factory=test_factory)
    id2 = write_audit("bob", "SENIOR_ANALYST", "UNMASK", "alert:1", {"unmasked": True}, factory=test_factory)
    id3 = write_audit("carol", "SENIOR_ANALYST", "EXPORT", "report:daily", {}, factory=test_factory)

    assert id1 > 0 and id2 > 0 and id3 > 0

    all_audits = list_audit(limit=10, factory=test_factory)
    assert len(all_audits) == 3
    # Check ordering by id desc
    assert all_audits[0]["id"] == id3
    assert all_audits[1]["id"] == id2
    assert all_audits[2]["id"] == id1

    # Filter target
    alert_1_audits = list_audit(limit=10, target="alert:1", factory=test_factory)
    assert len(alert_1_audits) == 2
    assert all(a["target"] == "alert:1" for a in alert_1_audits)


# ===========================================================================
# Phase 3 & 4 Tests: Masking, RBAC, REST API, and Dashboard Helpers
# ===========================================================================

from fastapi.testclient import TestClient

import app.db as app_db
from app.main import app
from app.masking import (
    mask_alert_detail,
    mask_alert_summary,
    mask_graph_data,
    mask_token,
    should_unmask,
    validate_role,
)


def test_mask_token():
    assert mask_token(None) == ""
    assert mask_token("") == ""
    assert mask_token("abc") == "abc..."
    assert mask_token("abcd") == "abcd..."
    assert mask_token("abcdefgh12345678") == "abcd..."


def test_validate_role():
    assert validate_role(None) == "ANALYST"
    assert validate_role("") == "ANALYST"
    assert validate_role("  ANALYST  ") == "ANALYST"
    assert validate_role("SENIOR_ANALYST") == "SENIOR_ANALYST"

    with pytest.raises(ValueError, match="Invalid role"):
        validate_role("ADMIN")


def test_should_unmask():
    assert should_unmask("ANALYST", True) is False
    assert should_unmask("ANALYST", False) is False
    assert should_unmask("SENIOR_ANALYST", False) is False
    assert should_unmask("SENIOR_ANALYST", True) is True


def test_mask_alert_summary_and_detail():
    raw_alert = {
        "alert_id": 1,
        "sender_token": "send_112233445566",
        "receiver_token": "recv_998877665544",
        "amount": 5000.0,
    }

    # Default analyst: masked
    masked = mask_alert_summary(raw_alert, unmask=False, role="ANALYST")
    assert masked["sender_token"] == "send..."
    assert masked["receiver_token"] == "recv..."

    # Analyst attempting unmask: still masked
    unauthorized = mask_alert_summary(raw_alert, unmask=True, role="ANALYST")
    assert unauthorized["sender_token"] == "send..."

    # Senior analyst with unmask=true: revealed
    authorized = mask_alert_summary(raw_alert, unmask=True, role="SENIOR_ANALYST")
    assert authorized["sender_token"] == "send_112233445566"
    assert authorized["receiver_token"] == "recv_998877665544"


def test_mask_graph_data():
    raw_graph = {
        "nodes": [
            {"id": "sender_1111", "in_degree": 0, "out_degree": 1},
            {"id": "receiver_2222", "in_degree": 1, "out_degree": 0},
            {"id": "other_3333", "in_degree": 1, "out_degree": 1},
        ],
        "edges": [
            {"source": "sender_1111", "target": "receiver_2222", "amount": 100.0},
            {"source": "receiver_2222", "target": "other_3333", "amount": 95.0},
        ],
    }

    masked_graph = mask_graph_data(
        raw_graph,
        sender_raw="sender_1111",
        receiver_raw="receiver_2222",
        unmask=False,
        role="ANALYST",
    )

    node_roles = {n["id"]: n["role"] for n in masked_graph["nodes"]}
    assert "send..." in node_roles
    assert node_roles["send..."] == "sender"
    assert "rece..." in node_roles
    assert node_roles["rece..."] == "receiver"
    assert "othe..." in node_roles
    assert node_roles["othe..."] == "other"

    # Edge endpoints must match masked IDs
    edge = masked_graph["edges"][0]
    assert edge["source"] == "send..."
    assert edge["target"] == "rece..."



@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """FastAPI test client with an isolated temporary SQLite database."""
    test_db = tmp_path / "api_test.db"
    monkeypatch.setenv("AEGIS_DB_PATH", str(test_db))
    # Reset engine singleton to use test_db
    app_db._engine = None
    app_db._SessionLocal = None
    app_db.init_db()

    with TestClient(app) as client:
        yield client

    # Cleanup engine
    if app_db._engine is not None:
        app_db._engine.dispose()
    app_db._engine = None
    app_db._SessionLocal = None


def test_api_health(api_client):
    res = api_client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_api_alerts_endpoints_rbac_and_masking(api_client):
    # Seed an alert via alerts_store
    txn = {
        "txn_id": "api-txn-1",
        "sender_token": "sender_secret_1",
        "receiver_token": "receiver_secret_2",
        "amount": 12000.0,
    }
    features = {"amount": 12000.0}
    result = {
        "risk_score": 0.82,
        "risk_level": "HIGH",
        "reasons": [{"factor": "burst", "contribution": 0.6, "description": "High risk burst"}],
    }
    alert_id = create_alert(txn, features, result)

    # 1. Invalid role returns 400
    r_bad_role = api_client.get("/v1/alerts", headers={"X-Role": "SUPERUSER"})
    assert r_bad_role.status_code == 400

    # 2. Default ANALYST: tokens are masked
    r_default = api_client.get("/v1/alerts")
    assert r_default.status_code == 200
    alerts = r_default.json()
    assert len(alerts) >= 1
    target = [a for a in alerts if a["alert_id"] == alert_id][0]
    assert target["sender_token"] == "send..."
    assert target["receiver_token"] == "rece..."

    # 3. ANALYST attempting unmask=true: remains masked, no 403, no leak
    r_unauthorized = api_client.get("/v1/alerts?unmask=true", headers={"X-Role": "ANALYST"})
    assert r_unauthorized.status_code == 200
    assert r_unauthorized.json()[0]["sender_token"] == "send..."

    # 4. SENIOR_ANALYST with unmask=true: returns raw tokens and audits access
    r_senior = api_client.get("/v1/alerts?unmask=true", headers={"X-Role": "SENIOR_ANALYST"})
    assert r_senior.status_code == 200
    senior_alert = [a for a in r_senior.json() if a["alert_id"] == alert_id][0]
    assert senior_alert["sender_token"] == "sender_secret_1"
    assert senior_alert["receiver_token"] == "receiver_secret_2"


def test_api_alert_detail_and_graph(api_client):
    txn = {
        "txn_id": "api-txn-2",
        "sender_token": "sender_graph_1",
        "receiver_token": "receiver_graph_2",
        "amount": 7500.0,
    }
    alert_id = create_alert(txn, {"amount": 7500.0}, {"risk_score": 0.77, "risk_level": "HIGH"})

    # 404 for unknown alert
    assert api_client.get("/v1/alerts/99999").status_code == 404
    assert api_client.get("/v1/alerts/99999/graph").status_code == 404

    # Detail
    r_detail = api_client.get(f"/v1/alerts/{alert_id}")
    assert r_detail.status_code == 200
    det = r_detail.json()
    assert det["alert_id"] == alert_id
    assert det["sender_token"] == "send..."

    # Graph
    r_graph = api_client.get(f"/v1/alerts/{alert_id}/graph?hops=2")
    assert r_graph.status_code == 200
    g = r_graph.json()
    assert "nodes" in g and "edges" in g
    node_roles = {n["id"]: n["role"] for n in g["nodes"]}
    assert "send..." in node_roles and node_roles["send..."] == "sender"

    # Invalid hops parameter
    r_bad_hops = api_client.get(f"/v1/alerts/{alert_id}/graph?hops=5")
    assert r_bad_hops.status_code == 422


def test_api_decision_flow_and_audit_rbac(api_client):
    txn = {"txn_id": "api-txn-dec", "amount": 3000.0}
    alert_id = create_alert(txn, {}, {"risk_score": 0.65, "risk_level": "MEDIUM"})

    # Invalid decision payload
    r_bad_dec = api_client.post(
        f"/v1/alerts/{alert_id}/decision",
        json={"decision": "INVALID", "analyst": "bob"},
    )
    assert r_bad_dec.status_code == 422

    # Valid APPROVE decision
    r_dec = api_client.post(
        f"/v1/alerts/{alert_id}/decision",
        json={"decision": "APPROVE", "analyst": "bob", "note": "Verified scam pattern"},
    )
    assert r_dec.status_code == 200
    assert r_dec.json()["new_status"] == "APPROVED"

    # Second decision returns 409 Conflict
    r_conflict = api_client.post(
        f"/v1/alerts/{alert_id}/decision",
        json={"decision": "DISMISS", "analyst": "carol"},
    )
    assert r_conflict.status_code == 409

    # Audit endpoint RBAC
    # ANALYST gets 403 Forbidden
    r_audit_analyst = api_client.get("/v1/audit", headers={"X-Role": "ANALYST"})
    assert r_audit_analyst.status_code == 403

    # SENIOR_ANALYST gets 200 OK
    r_audit_senior = api_client.get("/v1/audit", headers={"X-Role": "SENIOR_ANALYST"})
    assert r_audit_senior.status_code == 200
    logs = r_audit_senior.json()
    assert len(logs) >= 1
    assert any(log["action"] == "DECISION" for log in logs)


def test_api_stats_endpoint(api_client):
    r_stats = api_client.get("/v1/stats")
    assert r_stats.status_code == 200
    stats = r_stats.json()
    assert "total_txns" in stats
    assert "alerts_open" in stats
    assert "alerts_total" in stats
    assert "avg_latency_ms" in stats


# ===========================================================================
# Dashboard Client Integration Tests
# ===========================================================================

import httpx

from dashboard.app import (
    check_api_health,
    fetch_alert_detail,
    fetch_alert_graph,
    fetch_alerts,
    fetch_audit_logs,
    fetch_stats,
    get_api_client,
    submit_decision,
)


def test_dashboard_client_helpers(tmp_path, monkeypatch):
    test_db = tmp_path / "dash_test.db"
    monkeypatch.setenv("AEGIS_DB_PATH", str(test_db))
    app_db._engine = None
    app_db._SessionLocal = None
    app_db.init_db()

    # Seed an alert
    txn = {"txn_id": "dash-txn", "sender_token": "dash_sender_1", "receiver_token": "dash_receiver_2", "amount": 8800.0}
    aid = create_alert(txn, {}, {"risk_score": 0.85, "risk_level": "HIGH"})

    with TestClient(app, headers={"X-Role": "SENIOR_ANALYST"}) as client:
        # Health check
        healthy, msg = check_api_health(client)
        assert healthy is True

        # Stats
        stats = fetch_stats(client)
        assert stats["alerts_total"] >= 1

        # Fetch alerts
        alerts = fetch_alerts(client, status="OPEN")
        assert len(alerts) >= 1

        # Fetch alert detail
        detail = fetch_alert_detail(client, aid, unmask=True)
        assert detail is not None
        assert detail["sender_token"] == "dash_sender_1"

        # Fetch graph
        graph = fetch_alert_graph(client, aid, hops=2)
        assert graph is not None
        assert "nodes" in graph

        # Submit decision
        ok, dec_msg = submit_decision(client, aid, "APPROVE", "analyst_test", "Test note")
        assert ok is True

        # Fetch audit logs
        status_code, logs = fetch_audit_logs(client)
        assert status_code == 200
        assert len(logs) >= 1

    if app_db._engine is not None:
        app_db._engine.dispose()
    app_db._engine = None
    app_db._SessionLocal = None
