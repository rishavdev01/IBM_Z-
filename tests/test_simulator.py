"""Part 1 tests: tokenization, model, generator, typologies, CSV, mock API."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from simulator.generator import AccountPool, take_benign
from simulator.main import CSV_COLUMNS, cmd_dataset
from simulator.mock_api import app as mock_app
from simulator.models import Transaction
from simulator.tokenization import tokenize
from simulator.typologies import (
    account_takeover,
    circular_layering,
    inject_scenarios,
    mule_fan_in,
    rapid_forwarding,
)

START = datetime(2026, 10, 17, 7, 0, 0, tzinfo=timezone.utc)


def test_tokenize_deterministic_and_16_hex() -> None:
    a = tokenize("ACC000001", salt="hackathon-salt")
    b = tokenize("ACC000001", salt="hackathon-salt")
    assert a == b
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


def test_tokenize_differs_per_salt() -> None:
    assert tokenize("ACC000001", salt="s1") != tokenize("ACC000001", salt="s2")


def test_model_rejects_bad_channel() -> None:
    payload = _valid_payload()
    payload["channel"] = "WIRE"
    with pytest.raises(ValidationError):
        Transaction.model_validate(payload)


def test_model_rejects_negative_amount() -> None:
    payload = _valid_payload()
    payload["amount"] = -10
    with pytest.raises(ValidationError):
        Transaction.model_validate(payload)


def test_to_payload_truth_only_when_set() -> None:
    payload = _valid_payload()
    payload.pop("_truth", None)
    txn = Transaction.model_validate(payload)
    dumped = txn.to_payload()
    assert "_truth" not in dumped
    payload["_truth"] = {"is_scam": False, "typology": "none"}
    txn2 = Transaction.model_validate(payload)
    dumped2 = txn2.to_payload()
    assert dumped2["_truth"] == {"is_scam": False, "typology": "none"}


def test_benign_stream_deterministic() -> None:
    pool = AccountPool(n_accounts=200, seed=7)
    a = take_benign(pool, START, 50, rate_per_sec=10.0, seed=99)
    b = take_benign(pool, START, 50, rate_per_sec=10.0, seed=99)
    assert [t.txn_id for t in a] == [t.txn_id for t in b]
    assert [t.timestamp for t in a] == [t.timestamp for t in b]


def test_benign_stream_schema_and_time_order() -> None:
    pool = AccountPool(n_accounts=500, seed=1)
    txns = take_benign(pool, START, 1000, rate_per_sec=20.0, seed=2)
    assert len(txns) == 1000
    stamps = [t.timestamp for t in txns]
    assert stamps == sorted(stamps)
    for txn in txns:
        txn.model_validate(txn.to_payload())
        assert txn.channel in ("UPI", "IMPS", "NEFT")
        assert txn.truth is not None
        assert txn.truth.is_scam is False
        assert txn.truth.typology == "none"


def test_mule_fan_in_structure() -> None:
    pool = AccountPool(n_accounts=200, seed=3)
    rng = np.random.default_rng(11)
    txns = mule_fan_in(pool, START, rng)
    assert txns
    receivers = {t.receiver_token for t in txns}
    assert len(receivers) == 1
    senders = {t.sender_token for t in txns}
    assert len(senders) >= 8
    times = [datetime.fromisoformat(t.timestamp.replace("Z", "+00:00")) for t in txns]
    assert max(times) - min(times) <= timedelta(minutes=30)
    assert all(t.truth and t.truth.typology == "mule_fan_in" for t in txns)


def test_rapid_forwarding_structure() -> None:
    pool = AccountPool(n_accounts=200, seed=4)
    rng = np.random.default_rng(12)
    txns = rapid_forwarding(pool, START, rng)
    senders = {t.sender_token for t in txns}
    receivers = {t.receiver_token for t in txns}
    mule = next(iter(senders & receivers))
    inbound = [t for t in txns if t.receiver_token == mule]
    outbound = [t for t in txns if t.sender_token == mule]
    assert len(inbound) >= 3
    assert outbound
    for out in outbound:
        prior = [
            t
            for t in inbound
            if datetime.fromisoformat(t.timestamp.replace("Z", "+00:00"))
            < datetime.fromisoformat(out.timestamp.replace("Z", "+00:00"))
        ]
        assert prior
        gap = datetime.fromisoformat(out.timestamp.replace("Z", "+00:00")) - datetime.fromisoformat(
            prior[-1].timestamp.replace("Z", "+00:00")
        )
        assert gap <= timedelta(seconds=181)


def test_circular_layering_closes_cycle() -> None:
    pool = AccountPool(n_accounts=50, seed=5)
    rng = np.random.default_rng(13)
    txns = circular_layering(pool, START, rng)
    assert 3 <= len(txns) <= 5
    nodes = []
    for t in txns:
        nodes.append((t.sender_token, t.receiver_token))
    start = nodes[0][0]
    cur = start
    seen = 0
    mapping = {s: r for s, r in nodes}
    while seen < len(nodes) + 1:
        cur = mapping[cur]
        seen += 1
        if cur == start:
            break
    assert cur == start
    assert seen == len(nodes)


def test_account_takeover_new_device_and_receivers() -> None:
    pool = AccountPool(n_accounts=80, seed=6)
    rng = np.random.default_rng(14)
    txns = account_takeover(pool, START, rng)
    assert 3 <= len(txns) <= 6
    senders = {t.sender_token for t in txns}
    devices = {t.device_token for t in txns}
    recvs = {t.receiver_token for t in txns}
    assert len(senders) == 1
    assert len(devices) == 1
    assert len(recvs) == len(txns)


def test_inject_scam_share() -> None:
    pool = AccountPool(n_accounts=400, seed=8)
    benign = take_benign(pool, START, 800, rate_per_sec=20.0, seed=8)
    mixed = inject_scenarios(benign, pool, scam_rate=0.04, seed=8)
    scams = [t for t in mixed if t.truth and t.truth.is_scam]
    share = len(scams) / len(mixed)
    assert 0.02 <= share <= 0.06
    mixed2 = inject_scenarios(benign, pool, scam_rate=0.04, seed=8)
    assert [t.txn_id for t in mixed] == [t.txn_id for t in mixed2]


def test_dataset_csv_schema(tmp_path: Path) -> None:
    out = tmp_path / "transactions_labeled.csv"
    cmd_dataset(n=200, seed=42, out=str(out))
    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows
    assert list(rows[0].keys()) == CSV_COLUMNS
    stamps = [r["timestamp"] for r in rows]
    assert stamps == sorted(stamps)
    for row in rows:
        assert row["channel"] in ("UPI", "IMPS", "NEFT")
        assert row["is_scam"] in ("True", "False")


def test_mock_api_health_and_ingest() -> None:
    client = TestClient(mock_app)
    assert client.get("/health").json() == {"status": "ok"}
    payload = _valid_payload()
    payload["_truth"] = {"is_scam": False, "typology": "none"}
    resp = client.post("/v1/transactions", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["txn_id"] == payload["txn_id"]
    assert body["risk_level"] == "LOW"
    assert body["alert_id"] is None
    assert "reasons" in body


def _valid_payload() -> dict:
    tok = tokenize("ACC000001")
    dev = tokenize("DEV000001_0")
    recv = tokenize("ACC000002")
    return {
        "txn_id": "11111111-1111-4111-8111-111111111111",
        "timestamp": "2026-10-17T07:30:00Z",
        "sender_token": tok,
        "receiver_token": recv,
        "amount": 1500.0,
        "channel": "UPI",
        "device_token": dev,
    }
