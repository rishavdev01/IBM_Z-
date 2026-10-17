# Aegis Exchange — Shared Contract

PROJECT: Aegis Exchange - real-time scam/fraud detection on payment streams (hackathon).

STACK: Python 3.11 only (no Go, no Node). FastAPI + Uvicorn, NetworkX, Scikit-Learn + LightGBM, Streamlit + PyVis, SQLite via SQLAlchemy, Docker Compose (python:3.11-slim).

TARGET: containers must run on IBM LinuxONE (s390x). Use only pure-Python packages or packages with s390x wheels/builds; pin versions; avoid exotic native dependencies.

DATA FLOW:

```
simulator --POST /v1/transactions--> FastAPI app
FastAPI app -> graph_engine.extract_features(txn) -> aegis_ml.scorer.score(features)
-> alerts_store.create_alert(...) (SQLite) -> dashboard reads GET /v1/alerts etc.
```

REPO LAYOUT (monorepo aegis-exchange):

```
docs/CONTRACT.md              this block
simulator/                    PART 1  (own requirements.txt)
app/main.py, models.py, graph_engine.py, pipeline.py        PART 2
app/db.py, alerts_store.py, masking.py, routers/alerts.py   PART 4
aegis_ml/features.py, train.py, scorer.py, model/           PART 3
dashboard/                    PART 4  (own requirements.txt)
data/                         gitignored except .gitkeep; holds transactions_labeled.csv, aegis.db
tests/                        test_simulator.py, test_graph.py, test_ml.py, test_platform.py
requirements-api.txt          Parts 2 and 3 append lines only
docker-compose.yml, Dockerfile.api, Dockerfile.dashboard, Dockerfile.simulator   PART 4
```

ENV VARS (defaults): `AEGIS_SALT=hackathon-salt`, `AEGIS_DB_PATH=data/aegis.db`, `AEGIS_API_URL=http://localhost:8000`, `WINDOW_HOURS=24`, `ALERT_THRESHOLD=0.40`

PORTS: api 8000, dashboard 8501.

TRANSACTION (JSON body of POST `/v1/transactions`):

- `txn_id` str uuid4
- `timestamp` str ISO-8601 UTC, e.g. `2026-10-17T07:30:00Z`
- `sender_token` str sha256(salt + raw_account_id) hex, first 16 chars
- `receiver_token` str same rule
- `amount` float INR
- `channel` str `UPI` | `IMPS` | `NEFT`
- `device_token` str same tokenization rule applied to the device id
- `memo` str optional
- `_truth` obj optional, simulator only: `{"is_scam": bool, "typology": str}`
  - typology is one of: `mule_fan_in`, `rapid_forwarding`, `circular_layering`, `account_takeover`, `none`.
  - Scoring code must NEVER read `_truth`.

GRAPH FEATURES: `app.state.engine` (GraphEngine in `app/graph_engine.py`): `engine.extract_features(txn: dict) -> dict` computes the features, THEN adds the txn to the graph (one call per txn, in timestamp order). Exactly these 16 numeric keys:

- `amount`
- `hour_of_day`
- `channel_code` (UPI=0, IMPS=1, NEFT=2)
- `sender_out_degree_24h` (distinct receivers)
- `sender_txn_count_1h`
- `receiver_in_degree_24h` (distinct senders)
- `receiver_in_amount_24h`
- `receiver_out_amount_24h`
- `receiver_forward_ratio` (out_amount / in_amount, 0 if no inbound)
- `receiver_min_forward_seconds` (smallest gap from an inbound txn to the next outbound txn of the receiver in the window; -1 if none)
- `in_cycle` (0/1: this edge closes a cycle of length <= 5 in the window)
- `cycle_length` (0 if none)
- `sender_seconds_since_last_inbound` (seconds since the sender last received money; -1 if none)
- `sender_forward_ratio` (sender outbound amount incl. this txn / sender inbound amount in window; 0 if none)
- `sender_new_device` (0/1: device_token never seen with this sender before)
- `amount_vs_sender_avg` (amount / sender's average amount in window; 1.0 if no history)

SCORE RESULT: `aegis_ml.scorer.score(features: dict) -> dict`

```python
{
  "risk_score": float,  # 0..1
  "risk_level": "LOW" | "MEDIUM" | "HIGH",  # LOW <0.40, MEDIUM 0.40-0.69, HIGH >=0.70
  "reasons": [  # top 3
    {"factor": str, "contribution": float, "description": str}
  ],
}
```

- `score()` must never raise; on any failure it returns a rule-based fallback result.
- An alert is created when `risk_score >= ALERT_THRESHOLD`.

CROSS-PART PYTHON INTERFACES:

```python
from aegis_ml.scorer import score                           # Part 3 -> used by Part 2
from app.alerts_store import create_alert, record_txn      # Part 4 -> used by Part 2

create_alert(txn: dict, features: dict, result: dict) -> int   # returns alert_id
record_txn(txn: dict, result: dict) -> None
```

Stub versions of both exist from Step 0 so every part runs on day one.

API ENDPOINTS (JSON; base `http://localhost:8000`):

- `GET  /health` -> `{"status": "ok"}`
- `POST /v1/transactions` -> `{"txn_id", "risk_score", "risk_level", "reasons", "alert_id" (int|null)}`
- `GET  /v1/alerts?status=OPEN|APPROVED|DISMISSED&limit=100` -> `[Alert]`
- `GET  /v1/alerts/{alert_id}` -> Alert + features + audit trail
- `POST /v1/alerts/{alert_id}/decision` body `{"decision": "APPROVE"|"DISMISS", "analyst": str, "note": str}`
- `GET  /v1/graph/neighborhood/{token}?hops=2` -> `{"nodes": [{"id", "in_degree", "out_degree"}], "edges": [{"source", "target", "amount", "timestamp", "txn_id"}]}`
- `GET  /v1/stats` -> `{"total_txns", "alerts_open", "alerts_total", "avg_latency_ms"}`
- `GET  /v1/alerts/{alert_id}/graph?hops=2` -> neighborhood shape for the alert's sender, ids masked per RBAC, nodes carry role `sender|receiver|other` (Part 4; uses `request.app.state.engine`)
- `GET  /v1/audit?limit=200` -> `[AuditEntry]` (SENIOR_ANALYST only, else 403) (Part 4)

Alert = `{"alert_id", "txn_id", "created_at", "status", "risk_score", "risk_level", "reasons", "sender_token", "receiver_token", "amount"}`

Decision meaning:

- `APPROVE` = analyst confirms the alert as real suspicious activity
- `DISMISS` = false positive

RBAC MOCK (Part 4): header `X-Role` is `ANALYST` or `SENIOR_ANALYST`. Alert endpoints return tokens masked (first 4 chars + `"..."`) unless `X-Role` is `SENIOR_ANALYST` and the query has `unmask=true`; every unmask is written to `audit_log`.

SQLITE TABLES (Part 4 owns): `alerts`, `decisions`, `audit_log`, `transactions`.

CODING RULES: type hints everywhere, pydantic v2 models, docstrings on public functions, pytest tests for your part in `tests/`, no secrets in git (use `.env.example`), never edit another part's folders — ask in the group chat instead.
