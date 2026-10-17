# Aegis Exchange

Real-time scam/fraud detection on payment streams (hackathon). Python 3.11 only. Target runtime: IBM LinuxONE (`s390x`) via Docker Compose.

## Repo layout

| Path | Owner | Notes |
| --- | --- | --- |
| `docs/CONTRACT.md` | everyone | Shared API / data / interface contract. Change only via a group PR. |
| `simulator/` | Part 1 | Synthetic stream, typologies, replay client |
| `app/main.py`, `app/models.py`, `app/graph_engine.py`, `app/pipeline.py` | Part 2 | FastAPI ingestion + 24h rolling graph |
| `aegis_ml/` | Part 3 | LightGBM / sklearn scorer |
| `app/db.py`, `app/alerts_store.py`, `app/masking.py`, `app/routers/alerts.py`, `dashboard/` | Part 4 | SQLite, RBAC, Streamlit, Docker |
| `data/` | generated | gitignored except `.gitkeep` |
| `tests/` | each part | `test_simulator.py`, `test_graph.py`, `test_ml.py`, `test_platform.py` |
| `requirements-api.txt` | Parts 2 and 3 | Append only |

## Branches

Work only in your folders, on these branches:

- `part-1-simulator`
- `part-2-api-graph`
- `part-3-ml`
- `part-4-platform`

Step 0 (this skeleton) lives on `main`. Clone, read `docs/CONTRACT.md`, then branch.

## Run the stub API

Python 3.11:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -r requirements-api.txt
uvicorn app.main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Stubs so nobody waits:

- `aegis_ml.scorer.score` always returns LOW / 0.1
- `app.alerts_store` is in-memory
- `app.routers.alerts` is an empty `/v1` router

Copy `.env.example` to `.env` for local defaults (`AEGIS_SALT`, `AEGIS_DB_PATH`, `AEGIS_API_URL`, `WINDOW_HOURS`, `ALERT_THRESHOLD`).
