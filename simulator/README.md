# Simulator (Part 1)

Synthetic INR payment stream with four scam typologies, salted tokenization, and a replay client.

## Install

From the repo root, Python 3.11:

```bash
pip install -r simulator/requirements.txt
# pytest lives in requirements-api.txt
pip install -r requirements-api.txt
```

## Mock API (no Part 2 required)

```bash
uvicorn simulator.mock_api:app --reload --port 8000
```

`GET /health` → `{"status":"ok"}`.  
`POST /v1/transactions` validates the contract body and returns a random **LOW** risk result.

## Commands

```bash
python -m simulator.main dataset --n 50000 --seed 42 --out data/transactions_labeled.csv
python -m simulator.main stream --api-url http://localhost:8000 --rate 20 --duration 300 --scam-rate 0.04 --seed 42
python -m simulator.main burst --typology mule_fan_in --api-url http://localhost:8000
```

Typologies for `burst`: `mule_fan_in`, `rapid_forwarding`, `circular_layering`, `account_takeover`.

`stream` prints every 5 seconds: `sent`, `errors`, `flagged` (risk_level not LOW), live precision/recall vs local `_truth`. Ctrl+C prints a final summary.

## Dataset columns

`txn_id,timestamp,sender_token,receiver_token,amount,channel,device_token,memo,is_scam,typology`

Seeded runs are deterministic (same seed → same ids, timestamps, and labels).

## Tests

```bash
pytest tests/test_simulator.py
```
