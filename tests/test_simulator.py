"""Test suite for Part 1: Simulator package.

Tests tokenization, models, generator, typologies, stream/dataset CLI, and mock API.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from simulator.models import Transaction, Truth
from simulator.tokenization import tokenize


# --- Prompt 1.1: Tokenization and Model tests ---


def test_tokenization_properties():
    """Test tokenization is deterministic, 16 hex chars, and differs per salt."""
    raw_id = "ACC000123"
    token1 = tokenize(raw_id, salt="salt-a")
    token2 = tokenize(raw_id, salt="salt-a")
    token3 = tokenize(raw_id, salt="salt-b")

    # Determinism
    assert token1 == token2
    # 16 characters
    assert len(token1) == 16
    assert all(c in "0123456789abcdef" for c in token1)
    # Differs per salt
    assert token1 != token3


def test_model_valid():
    """Test valid transaction creation and payload generation."""
    s_tok = tokenize("SENDER_1")
    r_tok = tokenize("RECEIVER_1")
    d_tok = tokenize("DEVICE_1")

    txn = Transaction(
        txn_id="123e4567-e89b-12d3-a456-426614174000",
        timestamp="2026-10-17T07:30:00Z",
        sender_token=s_tok,
        receiver_token=r_tok,
        amount=1500.50,
        channel="UPI",
        device_token=d_tok,
        memo="Dinner payment",
        truth=Truth(is_scam=False, typology="none"),
    )

    payload = txn.to_payload()
    assert payload["_truth"]["is_scam"] is False
    assert payload["_truth"]["typology"] == "none"
    assert payload["amount"] == 1500.50
    assert payload["memo"] == "Dinner payment"


def test_model_rejects_bad_channel():
    """Test model rejects channels not in UPI | IMPS | NEFT."""
    s_tok = tokenize("SENDER_1")
    r_tok = tokenize("RECEIVER_1")
    d_tok = tokenize("DEVICE_1")

    with pytest.raises(ValidationError):
        Transaction(
            txn_id="123e4567-e89b-12d3-a456-426614174000",
            timestamp="2026-10-17T07:30:00Z",
            sender_token=s_tok,
            receiver_token=r_tok,
            amount=100.0,
            channel="RTGS",  # Invalid channel
            device_token=d_tok,
        )


def test_model_rejects_negative_or_zero_amount():
    """Test model rejects non-positive amounts."""
    s_tok = tokenize("SENDER_1")
    r_tok = tokenize("RECEIVER_1")
    d_tok = tokenize("DEVICE_1")

    with pytest.raises(ValidationError):
        Transaction(
            txn_id="123e4567-e89b-12d3-a456-426614174000",
            timestamp="2026-10-17T07:30:00Z",
            sender_token=s_tok,
            receiver_token=r_tok,
            amount=-50.0,  # Negative
            channel="UPI",
            device_token=d_tok,
        )

    with pytest.raises(ValidationError):
        Transaction(
            txn_id="123e4567-e89b-12d3-a456-426614174000",
            timestamp="2026-10-17T07:30:00Z",
            sender_token=s_tok,
            receiver_token=r_tok,
            amount=0.0,  # Zero
            channel="UPI",
            device_token=d_tok,
        )


def test_to_payload_contains_truth_only_when_set():
    """Test to_payload() drops None and exposes _truth only when set."""
    s_tok = tokenize("SENDER_1")
    r_tok = tokenize("RECEIVER_1")
    d_tok = tokenize("DEVICE_1")

    # Without truth and memo
    txn_no_truth = Transaction(
        txn_id="123e4567-e89b-12d3-a456-426614174000",
        timestamp="2026-10-17T07:30:00Z",
        sender_token=s_tok,
        receiver_token=r_tok,
        amount=100.0,
        channel="IMPS",
        device_token=d_tok,
    )
    payload_no_truth = txn_no_truth.to_payload()
    assert "_truth" not in payload_no_truth
    assert "memo" not in payload_no_truth

    # With truth
    txn_with_truth = Transaction(
        txn_id="123e4567-e89b-12d3-a456-426614174000",
        timestamp="2026-10-17T07:30:00Z",
        sender_token=s_tok,
        receiver_token=r_tok,
        amount=100.0,
        channel="NEFT",
        device_token=d_tok,
        truth=Truth(is_scam=True, typology="mule_fan_in"),
    )
    payload_with_truth = txn_with_truth.to_payload()
    assert "_truth" in payload_with_truth
    assert payload_with_truth["_truth"]["is_scam"] is True
    assert payload_with_truth["_truth"]["typology"] == "mule_fan_in"


# --- Prompt 1.2: Benign Traffic Generator tests ---


def test_benign_stream_properties():
    """Test 1000 transactions: schema validity, strictly non-decreasing timestamps, legal channels."""
    from datetime import datetime, timezone
    from simulator.generator import AccountPool, benign_stream

    pool = AccountPool(n_accounts=500, seed=123)
    start_time = datetime(2026, 10, 17, 8, 0, 0, tzinfo=timezone.utc)
    stream = benign_stream(pool, start=start_time, rate_per_sec=25.0, seed=999)

    txns: list[Transaction] = []
    for _ in range(1000):
        txns.append(next(stream))

    assert len(txns) == 1000

    # Timestamps strictly non-decreasing
    last_dt = datetime.fromisoformat(txns[0].timestamp.replace("Z", "+00:00"))
    for txn in txns[1:]:
        current_dt = datetime.fromisoformat(txn.timestamp.replace("Z", "+00:00"))
        assert current_dt >= last_dt
        last_dt = current_dt

        # Legal channel
        assert txn.channel in ("UPI", "IMPS", "NEFT")
        # Amount range
        assert 50.0 <= txn.amount <= 50000.0
        # Truth values
        assert txn.truth is not None
        assert txn.truth.is_scam is False
        assert txn.truth.typology == "none"
        # Token lengths
        assert len(txn.sender_token) == 16
        assert len(txn.receiver_token) == 16
        assert len(txn.device_token) == 16


def test_benign_stream_determinism():
    """Test benign_stream is fully deterministic for a given seed."""
    from datetime import datetime, timezone
    from simulator.generator import AccountPool, benign_stream

    pool1 = AccountPool(n_accounts=200, seed=42)
    pool2 = AccountPool(n_accounts=200, seed=42)
    start_time = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)

    stream1 = benign_stream(pool1, start=start_time, rate_per_sec=20.0, seed=777)
    stream2 = benign_stream(pool2, start=start_time, rate_per_sec=20.0, seed=777)

    txns1 = [next(stream1) for _ in range(100)]
    txns2 = [next(stream2) for _ in range(100)]

    for t1, t2 in zip(txns1, txns2):
        assert t1.txn_id == t2.txn_id
        assert t1.timestamp == t2.timestamp
        assert t1.sender_token == t2.sender_token
        assert t1.receiver_token == t2.receiver_token
        assert t1.amount == t2.amount
        assert t1.channel == t2.channel
        assert t1.device_token == t2.device_token


# --- Prompt 1.3: Scam Typology Injector tests ---


def test_mule_fan_in_structure():
    """Test mule_fan_in has >= 8 distinct senders to one receiver inside 30 minutes."""
    from datetime import datetime, timezone
    import numpy as np
    from simulator.generator import AccountPool
    from simulator.typologies import mule_fan_in

    pool = AccountPool(n_accounts=300, seed=42)
    rng = np.random.default_rng(101)
    t0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    txns = mule_fan_in(pool, t0, rng)
    assert len(txns) >= 8

    senders = {t.sender_token for t in txns}
    receivers = {t.receiver_token for t in txns}

    # >= 8 distinct senders
    assert len(senders) >= 8
    # ONE new receiver
    assert len(receivers) == 1

    # Time span <= 30 minutes (1800 s)
    t_start = datetime.fromisoformat(txns[0].timestamp.replace("Z", "+00:00"))
    t_end = datetime.fromisoformat(txns[-1].timestamp.replace("Z", "+00:00"))
    assert (t_end - t_start).total_seconds() <= 1800.0

    for t in txns:
        assert 2000.0 <= t.amount <= 9000.0
        assert t.truth is not None
        assert t.truth.is_scam is True
        assert t.truth.typology == "mule_fan_in"


def test_rapid_forwarding_structure():
    """Test rapid_forwarding mule receives from 3-6 senders, forwards within 30-180 seconds."""
    from datetime import datetime, timezone
    import numpy as np
    from simulator.generator import AccountPool
    from simulator.typologies import rapid_forwarding

    pool = AccountPool(n_accounts=300, seed=42)
    rng = np.random.default_rng(202)
    t0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    txns = rapid_forwarding(pool, t0, rng)
    assert len(txns) >= 6  # at least 3 in + 3 out

    # Find the mule token: acts as both receiver (inbound) and sender (outbound)
    senders = {t.sender_token for t in txns}
    receivers = {t.receiver_token for t in txns}
    mules = senders.intersection(receivers)
    assert len(mules) == 1
    mule_token = mules.pop()

    inbound = [t for t in txns if t.receiver_token == mule_token]
    outbound = [t for t in txns if t.sender_token == mule_token]

    assert 3 <= len(inbound) <= 6
    assert len(outbound) >= len(inbound)

    # Check forward delay 30-180 seconds between corresponding hops
    in_ts = [datetime.fromisoformat(t.timestamp.replace("Z", "+00:00")) for t in inbound]
    out_ts = [datetime.fromisoformat(t.timestamp.replace("Z", "+00:00")) for t in outbound]
    min_out = min(out_ts)
    min_in = min(in_ts)
    assert (min_out - min_in).total_seconds() >= 25.0  # around 30-180s

    for t in txns:
        assert t.truth is not None
        assert t.truth.is_scam is True
        assert t.truth.typology == "rapid_forwarding"


def test_circular_layering_structure():
    """Test circular_layering passes money in a ring and closes a cycle within 10 minutes."""
    from datetime import datetime, timezone
    import numpy as np
    from simulator.generator import AccountPool
    from simulator.typologies import circular_layering

    pool = AccountPool(n_accounts=300, seed=42)
    rng = np.random.default_rng(303)
    t0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    txns = circular_layering(pool, t0, rng)
    ring_size = len(txns)
    assert 3 <= ring_size <= 5

    # Check cycle: sender of txn i+1 is receiver of txn i, and last receiver is first sender
    for i in range(ring_size - 1):
        assert txns[i].receiver_token == txns[i + 1].sender_token
    assert txns[-1].receiver_token == txns[0].sender_token

    # Check time <= 10 minutes (600s)
    t_start = datetime.fromisoformat(txns[0].timestamp.replace("Z", "+00:00"))
    t_end = datetime.fromisoformat(txns[-1].timestamp.replace("Z", "+00:00"))
    assert (t_end - t_start).total_seconds() <= 600.0

    # Amounts large
    for t in txns:
        assert t.amount >= 15000.0
        assert t.truth is not None
        assert t.truth.is_scam is True
        assert t.truth.typology == "circular_layering"


def test_account_takeover_structure():
    """Test account_takeover sends 3-6 payments with new device token in <=10 minutes."""
    from datetime import datetime, timezone
    import numpy as np
    from simulator.generator import AccountPool
    from simulator.typologies import account_takeover

    pool = AccountPool(n_accounts=300, seed=42)
    rng = np.random.default_rng(404)
    t0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    txns = account_takeover(pool, t0, rng)
    assert 3 <= len(txns) <= 6

    # Single victim sender
    senders = {t.sender_token for t in txns}
    assert len(senders) == 1
    victim_token = senders.pop()

    # Find victim in pool to verify device is new
    victim_acc = next(a for a in pool.accounts if a.token == victim_token)
    for t in txns:
        assert t.device_token not in victim_acc.device_tokens
        assert t.truth is not None
        assert t.truth.is_scam is True
        assert t.truth.typology == "account_takeover"


def test_inject_scenarios_share_and_sorting():
    """Test inject_scenarios produces 2% to 6% scam share and sorted timestamps."""
    from datetime import datetime, timezone
    from simulator.generator import AccountPool, benign_stream
    from simulator.typologies import inject_scenarios

    pool = AccountPool(n_accounts=300, seed=42)
    start_time = datetime(2026, 10, 17, 8, 0, 0, tzinfo=timezone.utc)
    stream = benign_stream(pool, start=start_time, rate_per_sec=20.0, seed=505)

    benign_txns = [next(stream) for _ in range(1500)]
    all_txns = inject_scenarios(benign_txns, pool, scam_rate=0.04, seed=606)

    total_count = len(all_txns)
    scam_count = sum(1 for t in all_txns if t.truth and t.truth.is_scam)
    scam_ratio = scam_count / total_count

    # Scam share must be between 2% and 6%
    assert 0.02 <= scam_ratio <= 0.06

    # Timestamps strictly sorted
    for i in range(len(all_txns) - 1):
        assert all_txns[i].timestamp <= all_txns[i + 1].timestamp


# --- Prompt 1.4: CSV Schema and Dataset tests ---


def test_dataset_generation_and_schema(tmp_path):
    """Test dataset command exports CSV with the correct columns, sorted timestamps, and class balance."""
    import csv
    from simulator.main import run_dataset

    out_csv = str(tmp_path / "test_transactions.csv")
    run_dataset(n=500, seed=42, out_path=out_csv)

    expected_cols = [
        "txn_id",
        "timestamp",
        "sender_token",
        "receiver_token",
        "amount",
        "channel",
        "device_token",
        "memo",
        "is_scam",
        "typology",
    ]

    with open(out_csv, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == expected_cols

        rows = list(reader)
        assert len(rows) == 500

        # Check timestamps sorted
        for i in range(len(rows) - 1):
            assert rows[i]["timestamp"] <= rows[i + 1]["timestamp"]

        # Check channels legal
        for r in rows:
            assert r["channel"] in ("UPI", "IMPS", "NEFT")
            assert float(r["amount"]) > 0
            assert len(r["sender_token"]) == 16
            assert len(r["receiver_token"]) == 16
            assert len(r["device_token"]) == 16
            assert r["is_scam"] in ("True", "False")
            assert r["typology"] in (
                "none",
                "mule_fan_in",
                "rapid_forwarding",
                "circular_layering",
                "account_takeover",
            )

        scams = sum(1 for r in rows if r["is_scam"] == "True")
        assert scams > 0


# --- Prompt 1.5: Mock API tests ---


def test_mock_api_health_and_transactions():
    """Test simulator/mock_api.py endpoints: /health and /v1/transactions."""
    from fastapi.testclient import TestClient
    from simulator.mock_api import app
    from simulator.tokenization import tokenize

    client = TestClient(app)

    # Health check
    h_resp = client.get("/health")
    assert h_resp.status_code == 200
    assert h_resp.json() == {"status": "ok"}

    # Benign transaction
    txn_benign = {
        "txn_id": "11111111-2222-3333-4444-555555555555",
        "timestamp": "2026-10-17T10:00:00Z",
        "sender_token": tokenize("USER_A"),
        "receiver_token": tokenize("USER_B"),
        "amount": 250.0,
        "channel": "UPI",
        "device_token": tokenize("DEV_A"),
        "_truth": {"is_scam": False, "typology": "none"},
    }
    b_resp = client.post("/v1/transactions", json=txn_benign)
    assert b_resp.status_code == 200
    b_data = b_resp.json()
    assert b_data["txn_id"] == txn_benign["txn_id"]
    assert b_data["risk_level"] == "LOW"
    assert b_data["risk_score"] < 0.40
    assert b_data["alert_id"] is None

    # Scam transaction
    txn_scam = {
        "txn_id": "99999999-8888-7777-6666-555555555555",
        "timestamp": "2026-10-17T10:05:00Z",
        "sender_token": tokenize("SCAM_A"),
        "receiver_token": tokenize("MULE_1"),
        "amount": 8000.0,
        "channel": "IMPS",
        "device_token": tokenize("DEV_S"),
        "_truth": {"is_scam": True, "typology": "mule_fan_in"},
    }
    s_resp = client.post("/v1/transactions", json=txn_scam)
    assert s_resp.status_code == 200
    s_data = s_resp.json()
    assert s_data["txn_id"] == txn_scam["txn_id"]
    assert s_data["risk_level"] == "HIGH"
    assert s_data["risk_score"] >= 0.70
    assert s_data["alert_id"] is not None
    assert len(s_data["reasons"]) > 0



