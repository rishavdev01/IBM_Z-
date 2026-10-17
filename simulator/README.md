# Part 1: Data Simulation & Scam Typologies (`simulator/`)

Aegis Exchange synthetic payment generator, salted tokenization engine, and scam typology injectors.

## Overview

Part 1 delivers:
1. **Salted Tokenization (`simulator/tokenization.py`)**: Deterministic 16-hex character tokenization of accounts and devices via `sha256(AEGIS_SALT + raw_id)`.
2. **Pydantic v2 Models (`simulator/models.py`)**: Strict schema adherence to the `TRANSACTION` contract specification, including optional `_truth` ground-truth object and `to_payload()`.
3. **Benign Traffic Generator (`simulator/generator.py`)**: Realistic `AccountPool` (lognormal amounts, channel preferences, sparse counterparty graph) with exponential inter-arrival streaming.
4. **Scam Typology Injectors (`simulator/typologies.py`)**:
   - `mule_fan_in`: 8-20 senders pay ONE new mule account (2000-9000 INR) within 30 min.
   - `rapid_forwarding`: Mule receives from 3-6 senders, forwards 90-98% within 30-180 seconds to 1-3 downstream accounts.
   - `circular_layering`: Ring of 3-5 accounts passes money with 1-3% skim in <=10 minutes.
   - `account_takeover`: Victim account compromised on new device, sends 3-6 payments of 5-20x usual amount to new receivers.
   - `inject_scenarios`: Merges bursts into the benign stream to achieve an exact ~4% scam class balance.
5. **Standalone Mock API (`simulator/mock_api.py`)**: Independent FastAPI server for Part 1 development and end-to-end testing without waiting for Part 2.
6. **Replay & Dataset CLI (`simulator/main.py`)**: Subcommands for `dataset`, `stream`, and `burst`.

---

## Installation

Ensure Python 3.11+ is installed. Install dependencies:

```bash
pip install -r simulator/requirements.txt
```

---

## Running the Mock API

Start the standalone mock server to test streaming or live bursts:

```bash
python -m uvicorn simulator.mock_api:app --host 127.0.0.1 --port 8000
```

Verify health:
```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

---

## CLI Usage

### 1. Generate 50,000 Labeled Training Transactions (`dataset`)

Exports a fully reproducible labeled CSV dataset sorted strictly by timestamp:

```bash
python -m simulator.main dataset --n 50000 --seed 42 --out data/transactions_labeled.csv
```

**Example Output:**
```
======================================================================
Generating synthetic dataset: 50000 transactions (seed=42)
Output path: data/transactions_labeled.csv
======================================================================
Successfully generated 50000 transactions.

Class Balance:
  Total:      50,000
  Benign:     47,998 (96.00%)
  Scam:       2,002 (4.00%)

Typology Breakdown:
  - account_takeover    :   254 (0.51%)
  - circular_layering   :   228 (0.46%)
  - mule_fan_in         :   820 (1.64%)
  - none                : 47998 (96.00%)
  - rapid_forwarding    :   700 (1.40%)

Saved to: .../data/transactions_labeled.csv
```

---

### 2. Stream Transactions in Real Time (`stream`)

Replays transactions into the ingestion API (`POST /v1/transactions`) with concurrency limit (semaphore 20), retry mechanism, real-time rate pacing, and rolling precision/recall reporting:

```bash
python -m simulator.main stream --api-url http://localhost:8000 --rate 20 --duration 300 --scam-rate 0.04 --seed 42
```

**Live Reporting Example:**
```
[005.0s] Sent:   100 | Errors:   0 | Flagged:    4 | Precision: 100.0% | Recall: 100.0%
[010.0s] Sent:   200 | Errors:   0 | Flagged:    8 | Precision: 100.0% | Recall: 100.0%
...
Final Stream Summary:
Elapsed Time:    300.12 seconds
Total Sent:      6000
Errors:          0
Flagged Alerts:  240
Live Precision:  98.75% (TP=237, FP=3)
Live Recall:     98.75% (TP=237, FN=3)
```

Press `Ctrl+C` at any time to gracefully terminate and display the final summary.

---

### 3. Inject a Live Scam Burst (`burst`)

Triggers an immediate scam burst scenario to verify alerts and dashboard visualization during demos:

```bash
python -m simulator.main burst --typology mule_fan_in --api-url http://localhost:8000
python -m simulator.main burst --typology rapid_forwarding --api-url http://localhost:8000
python -m simulator.main burst --typology circular_layering --api-url http://localhost:8000
python -m simulator.main burst --typology account_takeover --api-url http://localhost:8000
```

---

## Testing

Run all 14 unit and integration tests:

```bash
python -m pytest tests/test_simulator.py -v
```
