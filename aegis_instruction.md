Aegis Exchange - 4-Part Build Prompts
Oct 17, 2026 · @Vivesh
How to use this doc
Four teammates each own one part of Aegis Exchange in one shared GitHub repo; each pastes the Shared Context Block first, then their part's prompts in order, into their AI coding assistant.
Part	Suggested owner	Folders they own	Delivers	Needs from others
1. Simulation	Teammate A	simulator/	Synthetic payment stream with scam typologies, salted tokenization, replay client, labeled CSV for training	API URL (a stub server is enough at first)
2. API + graph	Teammate B (also repo lead)	app/main.py, app/models.py, app/graph_engine.py, app/pipeline.py	FastAPI ingestion, 24-hour rolling graph, graph features per transaction	aegis_ml.scorer (Part 3) and app/alerts_store.py (Part 4), stubbed first
3. ML scoring	Teammate C	aegis_ml/	Trained LightGBM model, score() function, explainable reason codes	Labeled CSV from Part 1 (make own stub data first)
4. Platform	Teammate D	app/db.py, app/alerts_store.py, app/routers/alerts.py, dashboard/, docker-compose.yml, Dockerfiles	SQLite audit store, Streamlit + PyVis analyst dashboard, Docker deployment for LinuxONE	REST endpoints from Part 2
Git workflow
1.	Step 0, before anyone starts (Teammate B, about 20 minutes): create the repo aegis-exchange, add the skeleton and docs/CONTRACT.md from the Shared Context Block below, push to main, and invite the other three.
2.	Everyone clones, then works only inside the folders listed for their part, on a branch named part-1-simulator, part-2-api-graph, part-3-ml, or part-4-platform.
3.	Commit small and often; open a pull request into main about every two hours so integration problems show up early.
4.	Never change the contract alone. If a field name or endpoint must change, say it in the group chat, update docs/CONTRACT.md in one pull request, and everyone rebases.
5.	Build stub-first: every part ships a fake version of what it depends on, so nobody waits for a teammate.
Timeline
This assumes a 24-hour hackathon; stretch or shrink the hour marks to fit your event.
Hours	Goal
0-1	Step 0 skeleton and contract pushed; everyone clones and reads the Shared Context Block
1-8	Each person builds their part against stubs and merges to main
8-12	First full integration: docker compose up runs all four parts together
12-18	Improve model quality, dashboard polish, edge cases, tests
18-22	Demo rehearsal, README, slides (see the last section)
22-24	Buffer; no new features
Shared Context Block
Every teammate pastes this block at the top of each AI-assistant session, before their part's prompt; it is also the content of docs/CONTRACT.md in the repo.
PROJECT: Aegis Exchange - real-time scam/fraud detection on payment streams (hackathon).
STACK: Python 3.11 only (no Go, no Node). FastAPI + Uvicorn, NetworkX, Scikit-Learn + LightGBM,
Streamlit + PyVis, SQLite via SQLAlchemy, Docker Compose (python:3.11-slim).
TARGET: containers must run on IBM LinuxONE (s390x). Use only pure-Python packages or packages
with s390x wheels/builds; pin versions; avoid exotic native dependencies.

DATA FLOW:
  simulator --POST /v1/transactions--> FastAPI app
  FastAPI app -> graph_engine.extract_features(txn) -> aegis_ml.scorer.score(features)
  -> alerts_store.create_alert(...) (SQLite) -> dashboard reads GET /v1/alerts etc.

REPO LAYOUT (monorepo aegis-exchange):
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

ENV VARS (defaults): AEGIS_SALT=hackathon-salt, AEGIS_DB_PATH=data/aegis.db,
  AEGIS_API_URL=http://localhost:8000, WINDOW_HOURS=24, ALERT_THRESHOLD=0.40
PORTS: api 8000, dashboard 8501.

TRANSACTION (JSON body of POST /v1/transactions):
  txn_id        str   uuid4
  timestamp     str   ISO-8601 UTC, e.g. 2026-10-17T07:30:00Z
  sender_token  str   sha256(salt + raw_account_id) hex, first 16 chars
  receiver_token str  same rule
  amount        float INR
  channel       str   UPI | IMPS | NEFT
  device_token  str   same tokenization rule applied to the device id
  memo          str   optional
  _truth        obj   optional, simulator only: {"is_scam": bool, "typology": str}
                      typology is one of: mule_fan_in, rapid_forwarding, circular_layering,
                      account_takeover, none.  Scoring code must NEVER read _truth.

GRAPH FEATURES: app.state.engine (GraphEngine in app/graph_engine.py): engine.extract_features(txn: dict) -> dict computes the features, THEN adds the txn to the graph (one call per txn, in timestamp order). Exactly these 16 numeric keys:
  amount, hour_of_day, channel_code (UPI=0, IMPS=1, NEFT=2),
  sender_out_degree_24h (distinct receivers), sender_txn_count_1h,
  receiver_in_degree_24h (distinct senders), receiver_in_amount_24h, receiver_out_amount_24h,
  receiver_forward_ratio (out_amount / in_amount, 0 if no inbound),
  receiver_min_forward_seconds (smallest gap from an inbound txn to the next outbound txn
    of the receiver in the window; -1 if none),
  in_cycle (0/1: this edge closes a cycle of length <= 5 in the window), cycle_length (0 if none),
  sender_seconds_since_last_inbound (seconds since the sender last received money; -1 if none),
  sender_forward_ratio (sender outbound amount incl. this txn / sender inbound amount in window; 0 if none),
  sender_new_device (0/1: device_token never seen with this sender before),
  amount_vs_sender_avg (amount / sender's average amount in window; 1.0 if no history)

SCORE RESULT: aegis_ml.scorer.score(features: dict) -> dict
  {"risk_score": float 0..1,
   "risk_level": "LOW" (<0.40) | "MEDIUM" (0.40-0.69) | "HIGH" (>=0.70),
   "reasons": [ {"factor": str, "contribution": float, "description": str} ]  (top 3) }
  score() must never raise; on any failure it returns a rule-based fallback result.
  An alert is created when risk_score >= ALERT_THRESHOLD.

CROSS-PART PYTHON INTERFACES:
  from aegis_ml.scorer import score                           (Part 3 -> used by Part 2)
  from app.alerts_store import create_alert, record_txn      (Part 4 -> used by Part 2)
    create_alert(txn: dict, features: dict, result: dict) -> int   # returns alert_id
    record_txn(txn: dict, result: dict) -> None
  Stub versions of both exist from Step 0 so every part runs on day one.

API ENDPOINTS (JSON; base http://localhost:8000):
  GET  /health                              -> {"status": "ok"}
  POST /v1/transactions                     -> {"txn_id", "risk_score", "risk_level", "reasons", "alert_id" (int|null)}
  GET  /v1/alerts?status=OPEN|APPROVED|DISMISSED&limit=100   -> [Alert]
  GET  /v1/alerts/{alert_id}                -> Alert + features + audit trail
  POST /v1/alerts/{alert_id}/decision       body {"decision": "APPROVE"|"DISMISS", "analyst": str, "note": str}
  GET  /v1/graph/neighborhood/{token}?hops=2 -> {"nodes": [{"id", "in_degree", "out_degree"}],
                                                "edges": [{"source", "target", "amount", "timestamp", "txn_id"}]}
  GET  /v1/stats                            -> {"total_txns", "alerts_open", "alerts_total", "avg_latency_ms"}
  GET  /v1/alerts/{alert_id}/graph?hops=2   -> neighborhood shape for the alert's sender, ids masked per RBAC, nodes carry role sender|receiver|other (Part 4; uses request.app.state.engine)
  GET  /v1/audit?limit=200                  -> [AuditEntry] (SENIOR_ANALYST only, else 403) (Part 4)
  Alert = {"alert_id", "txn_id", "created_at", "status", "risk_score", "risk_level", "reasons",
           "sender_token", "receiver_token", "amount"}
  Decision meaning: APPROVE = analyst confirms the alert as real suspicious activity;
                    DISMISS = false positive.

RBAC MOCK (Part 4): header X-Role is ANALYST or SENIOR_ANALYST. Alert endpoints return tokens
  masked (first 4 chars + "...") unless X-Role is SENIOR_ANALYST and the query has unmask=true;
  every unmask is written to audit_log.

SQLITE TABLES (Part 4 owns): alerts, decisions, audit_log, transactions.

CODING RULES: type hints everywhere, pydantic v2 models, docstrings on public functions, pytest
  tests for your part in tests/, no secrets in git (use .env.example), never edit another
  part's folders - ask in the group chat instead.
Teammate B's Step 0 prompt (in Part 2) turns this block into the repo skeleton with working stubs.
Part 1: Data simulation and scam typologies (Teammate A)
Part 1 produces the synthetic payment stream, injects four scam typologies with ground-truth labels, tokenizes accounts, and replays the stream into the API; work only in simulator/ and tests/test_simulator.py, on branch part-1-simulator. For every prompt below, paste the Shared Context Block first.
Prompt 1.1: Setup, tokenization, transaction model
You are building PART 1 (simulator/) of Aegis Exchange. Work only in simulator/ and
tests/test_simulator.py.
1. Create simulator/__init__.py and simulator/requirements.txt (httpx, pydantic>=2, numpy,
   python-dotenv, fastapi, uvicorn - pin versions).
2. Create simulator/tokenization.py (do NOT name it tokenize.py, it shadows the stdlib):
   tokenize(raw_id: str, salt: str | None = None) -> str = first 16 hex chars of
   sha256(salt + raw_id); salt defaults to env AEGIS_SALT (fallback "hackathon-salt").
3. Create simulator/models.py: a pydantic v2 Transaction model that matches the TRANSACTION
   section of the contract exactly. The optional _truth field is exposed as `truth` with
   alias "_truth". Add a to_payload() that dumps by alias and drops None values.
4. Add pytest tests: tokenization is deterministic, 16 chars, differs per salt; the model
   rejects a bad channel and a negative amount; to_payload() contains _truth only when set.
Run pytest and show the results.
Prompt 1.2: Benign traffic generator
In simulator/generator.py build a realistic benign payment generator.
- AccountPool(n_accounts=2000, seed): raw ids like ACC000123; each account has 1-2 devices,
  a lognormal typical amount (median about 1500 INR), a preferred channel mix across
  UPI/IMPS/NEFT, active hours, and 5-15 frequent counterparties so the graph is sparse and
  realistic (not uniformly random).
- benign_stream(pool, start: datetime, rate_per_sec: float, seed) -> Iterator[Transaction]:
  exponential inter-arrival times, 90% of payments go to frequent counterparties,
  amounts mostly 50-5000 INR with rare spikes up to 50000, tokens built with tokenization.py.
  Every benign transaction has truth {is_scam: false, typology: "none"}.
- Fully deterministic for a given seed.
Add tests: determinism, schema validity of 1000 transactions, timestamps strictly
non-decreasing, channel values legal. Run pytest.
Prompt 1.3: Scam typology injectors
In simulator/typologies.py implement four functions. Each takes (pool, t0: datetime, rng)
and returns a list[Transaction] with truth {is_scam: true, typology: <name>} on every
transaction it creates. Use the exact typology strings from the contract.
1. mule_fan_in: 8-20 different senders pay ONE new mule account 2000-9000 INR each
   within 30 minutes.
2. rapid_forwarding: a mule receives from 3-6 senders, then forwards 90-98% of each
   inbound amount to 1-3 downstream accounts within 30-180 seconds of the inbound.
3. circular_layering: a ring of 3-5 accounts passes money A->B->C->...->A, each hop keeping
   a 1-3% skim, whole ring finished within 10 minutes (large amounts 20000-90000).
4. account_takeover: an existing victim account appears on a NEW device_token and sends
   3-6 payments of 5-20x its usual amount to brand-new receivers within 10 minutes.
Then add inject_scenarios(benign_txns, pool, scam_rate=0.04, seed) that merges scam bursts
into the benign stream at random times so scam transactions are about 3-5% of the total,
returns everything sorted by timestamp, and is deterministic per seed.
Tests: each typology yields the expected structure (for example mule_fan_in has >= 8
distinct senders to one receiver inside 30 minutes; circular_layering closes a cycle),
scam share is between 2% and 6%. Run pytest.
Prompt 1.4: Stream client and dataset export (the CLI)
Create simulator/main.py (argparse, run as python -m simulator.main) with three commands:
- stream: --api-url (default AEGIS_API_URL) --rate 20 --duration 300 --scam-rate 0.04
  --seed 42. Generate transactions and POST each to /v1/transactions with httpx.AsyncClient
  (semaphore of 20 concurrent requests, 5 s timeout, retry twice). Sleep so the real-time
  send rate matches --rate. Every 5 seconds print: sent, errors, flagged (risk_level not
  LOW), and live precision/recall computed from each response versus the local _truth.
- dataset: --n 50000 --seed 42 --out data/transactions_labeled.csv. Write one row per
  transaction with columns txn_id, timestamp, sender_token, receiver_token, amount, channel,
  device_token, memo, is_scam, typology, sorted by timestamp. Print the class balance.
- burst: --typology mule_fan_in|rapid_forwarding|circular_layering|account_takeover posts
  ONE scam scenario right now with current timestamps (used to trigger a live demo alert).
Handle Ctrl+C cleanly with a final summary. Add a small test for the CSV schema.
Prompt 1.5: Mock API, tests, README, push
1. Create simulator/mock_api.py: a tiny FastAPI app serving GET /health and POST
   /v1/transactions that validates the body and returns random low-risk results in the
   contract format, so Part 1 can be tested without Part 2. Document how to run it.
2. Make sure pytest passes for all of tests/test_simulator.py.
3. Write simulator/README.md: install, run stream/dataset/burst, example output.
4. Print the exact git commands to commit on branch part-1-simulator, push to origin, and
   open a pull request into main. Do not touch other folders.
Done when: python -m simulator.main dataset --n 50000 writes the CSV, python -m simulator.main stream posts to the mock API with live precision/recall lines, pytest tests/test_simulator.py passes, and the PR is open.
Part 2: FastAPI ingestion and NetworkX graph engine (Teammate B)
Part 2 is the backbone: it receives transactions, keeps the 24-hour rolling graph in memory, computes the 16 graph features, calls the scorer, and creates alerts; Teammate B also runs Step 0 so the other three can start. Work only in app/main.py, app/models.py, app/graph_engine.py, app/pipeline.py, tests/test_graph.py, on branch part-2-api-graph (Step 0 goes straight to main). Paste the Shared Context Block before every prompt.
Prompt 2.0: Step 0, repo skeleton with stubs (do this first, push to main)
Create the monorepo skeleton for aegis-exchange exactly as in the REPO LAYOUT section of
the contract, and make it importable and runnable immediately:
1. docs/CONTRACT.md = the full Shared Context Block text I pasted.
2. Folders with __init__.py: simulator/, app/, app/routers/, aegis_ml/, dashboard/, tests/.
   data/.gitkeep. .gitignore (Python, data/*.db, data/*.csv, .env, aegis_ml/model/*.joblib
   is NOT ignored - we commit the trained model), .env.example with the ENV VARS.
3. STUBS so nothing blocks:
   - aegis_ml/scorer.py: score(features: dict) -> dict returning
     {"risk_score": 0.1, "risk_level": "LOW", "reasons": []}.
   - app/alerts_store.py: in-memory create_alert(txn, features, result) -> int (incrementing
     id) and record_txn(txn, result) -> None.
   - app/routers/alerts.py: an APIRouter with prefix "/v1" and no routes yet.
   - app/main.py: FastAPI app with GET /health, including the alerts router.
4. requirements-api.txt with fastapi, uvicorn[standard], pydantic>=2, networkx, scikit-learn,
   lightgbm, joblib, numpy, pandas, sqlalchemy, httpx, pytest (pinned versions that have
   s390x-compatible builds or are pure Python where possible).
5. README.md with the repo layout, how to run `uvicorn app.main:app --reload`, and the
   branch names part-1-simulator, part-2-api-graph, part-3-ml, part-4-platform.
Verify: pip install -r requirements-api.txt; uvicorn starts; GET /health returns ok.
Then print the git commands to create the repo, commit to main, and push.
Prompt 2.1: Models and app skeleton
You own app/main.py, app/models.py, app/graph_engine.py, app/pipeline.py (not the Part 3 or
Part 4 files). In app/models.py write pydantic v2 models matching the contract: TransactionIn
(with _truth exposed as `truth`, alias "_truth", optional), Reason, ScoreResult, TxnResponse,
GraphNode, GraphEdge, GraphNeighborhood, Stats. Validation: amount > 0, channel in
UPI/IMPS/NEFT, timestamp parseable ISO-8601 UTC, tokens are 16 hex chars.
In app/main.py use a lifespan handler that creates ONE GraphEngine instance stored as app.state.engine,
keep the existing /health and the alerts router include, and add CORS for the dashboard
(allow all origins is fine for the hackathon). Add structured logging (one line per request
with txn_id, risk_score, latency_ms).
Prompt 2.2: The graph engine
Implement app/graph_engine.py: class GraphEngine(window_hours: int = 24) using networkx.DiGraph
plus per-account indexes, guarded by threading.RLock.
Public methods:
  extract_features(txn: dict) -> dict   # computes the 16 features, THEN adds the txn to the graph
  neighborhood(token: str, hops: int = 2) -> dict   # contract GraphNeighborhood shape
  prune(now: datetime) -> None          # drops transactions older than the window
  stats() -> dict                       # node_count, edge_count, txn_count
Use the transaction timestamp as the clock (not wall time) so replays and dataset training
work; prune relative to the newest timestamp seen. Keep deques of (ts, other_party, amount)
per account for inbound and outbound, a set of device tokens per sender (kept for the whole
run), and edge data on the DiGraph. Remove edges and isolated nodes when their last
transaction expires.
Features (exact names, all floats or ints). Rule: counts and sums INCLUDE the current txn;
the four marked (history) use only EARLIER transactions:
  amount; hour_of_day (UTC); channel_code (UPI 0, IMPS 1, NEFT 2);
  sender_out_degree_24h (distinct receivers); sender_txn_count_1h;
  receiver_in_degree_24h (distinct senders); receiver_in_amount_24h; receiver_out_amount_24h;
  receiver_forward_ratio = out_amount / in_amount of the receiver in window (0 if no inbound);
  receiver_min_forward_seconds (history): smallest gap from any earlier inbound txn of the
    receiver to its next outbound txn, -1 if none;
  sender_seconds_since_last_inbound (history): seconds since the sender last received money,
    -1 if none;
  sender_forward_ratio = sender outbound amount (incl. this txn) / sender inbound amount in
    window, 0 if no inbound;
  in_cycle (0/1) and cycle_length: before adding sender->receiver, if a path receiver->sender
    of length <= 4 exists then in_cycle = 1 and cycle_length = path length + 1, else 0 and 0;
  sender_new_device (history, 0/1); amount_vs_sender_avg (history; 1.0 if no history).
Make extract_features fast: no full-graph scans, O(window activity of the two accounts).
Add tests/test_graph.py with a hand-built scenario for EVERY feature: fan-in of 10 senders,
rapid forwarding within 60 s, a 4-account ring, a new device, pruning after 25 hours.
Prompt 2.3: Pipeline and endpoints
Implement app/pipeline.py: async process_transaction(txn: TransactionIn, engine: GraphEngine)
-> TxnResponse. Steps: features = engine.extract_features(txn_dict); result =
await asyncio.to_thread(score, features) imported from aegis_ml.scorer; if
result["risk_score"] >= ALERT_THRESHOLD (env, default 0.40) then alert_id =
create_alert(txn_dict, features, result) from app.alerts_store, else None; always call
record_txn(txn_dict, result); update in-memory counters for /v1/stats (total_txns,
alerts_total, rolling avg_latency_ms over the last 500). A duplicate txn_id returns the
cached previous response (small LRU of 10000) instead of reprocessing. Never read _truth
for scoring; pass it through to record_txn only.
In app/main.py add: POST /v1/transactions, GET /v1/graph/neighborhood/{token}?hops=2 (hops
1-3, 404 if the token is unknown), GET /v1/stats (alerts_open comes from the alerts store if
it exposes count_open(), otherwise 0). Return exactly the shapes in the contract. Do not
add the alerts routes (Part 4 owns them).
Prompt 2.4: Load test, hardening, push
1. Create scripts/load_test.py (httpx async): send 2000 transactions from the simulator
   dataset (or synthetic random ones if the CSV is missing) at 200 concurrent-ish pace and
   print p50/p95/p99 latency and throughput. Goal: p95 under 50 ms with the stub scorer.
2. Add tests/test_api.py with FastAPI TestClient: valid POST, invalid channel -> 422,
   duplicate txn_id returns identical response, neighborhood returns nodes and edges,
   /v1/stats counts match.
3. Update README.md with API usage curl examples.
4. Print the git commands for branch part-2-api-graph and the PR into main.
Done when: pytest tests/test_graph.py tests/test_api.py passes, uvicorn app.main:app serves all contract endpoints Part 2 owns, the load test shows p95 under 50 ms, and the PR is open.
Part 3: ML scoring and explainability (Teammate C)
Part 3 turns the 16 graph features into a 0-1 risk score with the top three reasons, and guarantees a rule-based fallback so the demo never breaks; work only in aegis_ml/, scripts/eval_report.py, docs/model_report.md, and tests/test_ml.py, on branch part-3-ml. Paste the Shared Context Block before every prompt.
LinuxONE caveat: I could not confirm that LightGBM ships s390x wheels, so it may compile from source in the container. Prompt 3.2 therefore builds two interchangeable backends (LightGBM and scikit-learn's HistGradientBoostingClassifier), selected by AEGIS_MODEL_BACKEND.
Prompt 3.1: Feature vector and training data
You own aegis_ml/ (not app/ or simulator/). Create:
1. aegis_ml/features.py: FEATURE_NAMES = the 16 feature names from the contract's GRAPH
   FEATURES section in a fixed order, and to_vector(features: dict) -> numpy array of that
   order (missing keys default to 0.0, never raise).
2. aegis_ml/dataset.py: build_training_frame(csv_path) -> pandas DataFrame with the 16
   feature columns plus label (is_scam) and typology. It replays data/transactions_labeled.csv
   in timestamp order through app.graph_engine.GraphEngine().extract_features(txn_dict)
   (one call per transaction, in order). Never use is_scam or typology as a feature.
3. aegis_ml/synthetic_features.py: until Part 1 and Part 2 are merged, generate a fake
   labeled feature table (about 20000 rows, 4% positives) where scam typologies shift the
   relevant features (fan-in raises receiver_in_degree_24h, rapid forwarding lowers
   sender_seconds_since_last_inbound and raises sender_forward_ratio, rings set in_cycle,
   takeover sets sender_new_device and raises amount_vs_sender_avg). Use it to build and
   test everything below; switch to build_training_frame once the real CSV and
   GraphEngine exist.
Add tests for to_vector (order, defaults) and the synthetic generator.
Prompt 3.2: Training and evaluation
Create aegis_ml/train.py (run as python -m aegis_ml.train --source synthetic|csv).
- Time-ordered split: first 70% train, next 15% validation, last 15% test (no random
  shuffling, to avoid leakage).
- Train two backends and save both under aegis_ml/model/: LightGBM (LGBMClassifier,
  scale_pos_weight for imbalance, early stopping on validation) as model_lightgbm.joblib,
  and sklearn HistGradientBoostingClassifier (class_weight="balanced") as
  model_sklearn.joblib. Also train a plain rule baseline for comparison.
- Save aegis_ml/model/metadata.json: feature order, benign median per feature (used for
  explanations), metrics per backend, thresholds 0.40 and 0.70.
- Report on the test split: ROC-AUC, PR-AUC, precision and recall at 0.40 and at 0.70, and
  recall per typology (mule_fan_in, rapid_forwarding, circular_layering, account_takeover).
  Print a clean comparison table. If PR-AUC is below 0.80, print which typology is weakest
  so we can tune.
- Seed everything (seed 42) so results are reproducible.
Prompt 3.3: Scorer with explainability and fallback
Replace the stub in aegis_ml/scorer.py with the real scorer. Keep the exact signature
score(features: dict) -> dict and the contract's result shape.
- Backend chosen by env AEGIS_MODEL_BACKEND (lightgbm default, sklearn alternative); load
  the model and metadata.json lazily ONCE (module-level cache), thread-safe.
- risk_score = predicted probability of the scam class (float 0..1, rounded to 4 places);
  risk_level LOW below 0.40, MEDIUM 0.40-0.69, HIGH 0.70 and above.
- Explainability (model-agnostic): for each of the 16 features, contribution_i = p(x) -
  p(x with feature i replaced by its benign median from metadata.json). Return the top 3
  positive contributors as {"factor": feature name, "contribution": rounded float,
  "description": human sentence using the real value}, for example "Receiver got money from
  14 different senders in 24 hours" or "Sender forwarded 96% of incoming funds within 45
  seconds". Write a description template for all 16 features. Batch the 17 predictions in
  one call so scoring stays under 5 ms.
- Fallback: if the model files are missing or anything raises, return a rule-based result
  (weighted rules on receiver_in_degree_24h, sender_forward_ratio, in_cycle,
  sender_new_device with amount_vs_sender_avg) with reasons built the same way. score()
  must NEVER raise and must never read _truth.
Prompt 3.4: Tests, report, push
1. tests/test_ml.py: result shape matches the contract; risk_level boundaries at 0.40 and
   0.70; reasons has at most 3 items with positive contributions; fallback works when model
   files are renamed (use monkeypatch); a clear fan-in example scores higher than a benign
   example; 1000 sequential score() calls average under 5 ms.
2. scripts/eval_report.py writes docs/model_report.md: metrics table per backend, per-typology
   recall, top global features, and 3 example explanations.
3. Commit the trained model files and metadata.json (they must be in the repo so Docker can
   run without training).
4. Update README with how to retrain. Print the git commands for branch part-3-ml and the
   PR into main.
Done when: python -m aegis_ml.train runs end to end, score() returns contract-shaped results with readable reasons in under 5 ms, the fallback is tested, trained model files are committed, and the PR is open.
Part 4: Database, audit, dashboard, Docker and LinuxONE (Teammate D)
Part 4 stores alerts and analyst decisions with an audit trail, enforces the mock role-based masking, builds the Streamlit analyst dashboard with the network graph, and packages everything for IBM LinuxONE; work only in app/db.py, app/alerts_store.py, app/masking.py, app/routers/alerts.py, dashboard/, scripts/, docker-compose.yml, the Dockerfiles, docs/DEPLOY_LINUXONE.md, and tests/test_platform.py, on branch part-4-platform. Paste the Shared Context Block before every prompt.
Prompt 4.1: SQLite store (replaces the stub)
You own app/db.py, app/alerts_store.py, app/masking.py, app/routers/alerts.py, dashboard/,
Dockerfiles and docker-compose.yml. Do not touch Part 1-3 files.
1. app/db.py: SQLAlchemy 2.0 engine on sqlite:///AEGIS_DB_PATH (create the folder if
   missing), WAL mode and check_same_thread=False, create_all on import. Tables:
   transactions(txn_id PK, timestamp, sender_token, receiver_token, amount, channel,
     device_token, risk_score, risk_level, truth_is_scam NULL, truth_typology NULL),
   alerts(alert_id PK autoincrement, txn_id, created_at, status OPEN|APPROVED|DISMISSED
     default OPEN, risk_score, risk_level, reasons_json, features_json, sender_token,
     receiver_token, amount),
   decisions(id, alert_id, decision, analyst, note, decided_at),
   audit_log(id, ts, actor, role, action, target, detail_json).
2. app/alerts_store.py: replace the in-memory stub but KEEP the contract signatures:
   create_alert(txn: dict, features: dict, result: dict) -> int and
   record_txn(txn: dict, result: dict) -> None. Add count_open() -> int, list_alerts(status,
   limit), get_alert(alert_id), add_decision(alert_id, decision, analyst, note) (409-style
   error if the alert is not OPEN), write_audit(actor, role, action, target, detail), and
   list_audit(limit, target=None). Use short transactions and a lock so concurrent ingestion
   is safe; record_txn must be fast (the API calls it per transaction).
3. Add tests for every function in tests/test_platform.py using a temp database file.
Prompt 4.2: Alerts router and role-based masking
Implement app/masking.py and app/routers/alerts.py (APIRouter, prefix "/v1", already
included by app/main.py).
- masking.py: get_role(request) reads header X-Role (ANALYST default, SENIOR_ANALYST; any
  other value -> 400); mask(token) = first 4 chars + "..."; can_unmask(role, unmask_flag) is
  true only for SENIOR_ANALYST with ?unmask=true.
- GET /v1/alerts?status=&limit=: list of Alert objects per the contract, tokens masked
  unless can_unmask. Sorted by risk_score desc then created_at desc.
- GET /v1/alerts/{alert_id}: Alert + features + reasons + decision history + audit entries
  for that alert. Writing an UNMASK audit entry whenever tokens are revealed.
- POST /v1/alerts/{alert_id}/decision: body {decision: APPROVE|DISMISS, analyst, note};
  APPROVE = confirm real suspicious activity, DISMISS = false positive; 404 unknown alert,
  409 already decided; writes decisions row, updates status, writes an audit entry (actor =
  analyst, role from header).
- GET /v1/alerts/{alert_id}/graph?hops=2: calls request.app.state.engine.neighborhood() for
  the alert's SENDER token (real token server-side) and returns the same shape as the
  contract's neighborhood, but with every node id and edge endpoint masked via mask() unless
  can_unmask. Masking is deterministic, so graph structure is preserved. Also mark which
  node is the alert sender and which is the receiver by adding "role": "sender"|"receiver"|
  "other" to each node.
- GET /v1/audit?limit=200: SENIOR_ANALYST only (403 otherwise).
Tests with FastAPI TestClient: masking by role, unmask audit entry, decision flow, 409.
Prompt 4.3: Streamlit analyst dashboard
Build dashboard/app.py (Streamlit) with dashboard/requirements.txt (streamlit, pyvis, httpx,
pandas, plotly). It talks to the API ONLY over REST (AEGIS_API_URL).
- Sidebar: API URL, role selector (ANALYST / SENIOR_ANALYST), analyst name, an "Unmask
  tokens" toggle enabled only for SENIOR_ANALYST, auto-refresh every 3 seconds. Send the
  X-Role header on every call.
- Top row of KPIs from /v1/stats: transactions processed, open alerts, alerts total, average
  latency in ms.
- Alert queue: dataframe of alerts (id, time, risk level colored HIGH red / MEDIUM amber,
  score, amount, sender, receiver), filters for status and level, sorted by score. Selecting
  a row opens a detail panel.
- Detail panel: transaction facts, a risk gauge (plotly), the top 3 reasons as a horizontal
  bar chart with the descriptions, a PyVis network of /v1/alerts/{id}/graph (sender red,
  receiver orange, others blue, node size by degree, edge label = amount) rendered with
  streamlit.components.v1.html, and Approve / Dismiss buttons with a note box that POST the
  decision and refresh.
- Tabs: "Alert queue" and "Audit log" (the audit tab shows /v1/audit for SENIOR_ANALYST and
  a friendly 403 message otherwise).
Handle API errors with st.error, never a stack trace. Dark theme via .streamlit/config.toml.
Prompt 4.4: Docker, Compose and LinuxONE
Create Dockerfile.api, Dockerfile.dashboard, Dockerfile.simulator (all FROM python:3.11-slim,
non-root user, no cache pip installs) and docker-compose.yml.
- Dockerfile.api: apt-get install build-essential cmake libgomp1 (needed if LightGBM must be
  compiled on s390x), then pip install -r requirements-api.txt, copy app/ and aegis_ml/ (the
  committed model files included), run uvicorn app.main:app --host 0.0.0.0 --port 8000.
- Compose services: api (port 8000, volume ./data:/app/data, healthcheck on /health, env
  from .env), dashboard (port 8501, AEGIS_API_URL=http://api:8000, depends_on api healthy),
  simulator (compose profile "demo", runs `python -m simulator.main stream --api-url
  http://api:8000`, depends_on api healthy).
- Makefile targets: up, down, demo (compose --profile demo up), test, logs.
- docs/DEPLOY_LINUXONE.md: how to build for s390x with docker buildx --platform linux/s390x
  (and QEMU via tonistiigi/binfmt for testing on a laptop), expected slowness under
  emulation, the switch AEGIS_MODEL_BACKEND=sklearn if LightGBM fails to build, and a
  checklist of what to verify on the real LinuxONE host (architecture with uname -m,
  compose up, health endpoint, dashboard reachable).
Do not run privileged commands without telling me first.
Prompt 4.5: Smoke test, README, push
1. scripts/smoke.py: with the stack running, post a normal transaction (expect LOW and no
   alert), then post a scripted mule_fan_in burst of 12 transactions to one receiver and
   assert at least one alert is created and appears in GET /v1/alerts; print PASS or FAIL.
2. scripts/seed_demo.sh: starts the stack and the simulator in demo profile.
3. Finish the root README.md: architecture diagram in Mermaid, quick start with docker
   compose, role/unmasking explanation, API table, screenshots placeholder, team credits.
4. Print the git commands for branch part-4-platform and the PR into main.
Done when: docker compose up brings up the API and dashboard, the dashboard shows alerts with the network graph and Approve/Dismiss working, masking changes with the role, python scripts/smoke.py prints PASS, and the PR is open.
Integration, demo and fallback plan
Integrate around hour 8 in four merges, in the order below, so each step has something real to test against.
Order	Who	Action	Check
1	Teammate A	Merge Part 1; everyone runs python -m simulator.main dataset --n 50000 --seed 42 (deterministic, so no CSV is shared)	data/transactions_labeled.csv exists, about 4% scam
2	Teammate B	Merge Part 2	uvicorn app.main:app runs; pytest tests/test_graph.py passes
3	Teammate C	Retrain on the real CSV: python -m aegis_ml.train --source csv, commit the model files, merge Part 3	PR-AUC and per-typology recall printed; score() under 5 ms
4	Teammate D	Merge Part 4	docker compose up is healthy; python scripts/smoke.py prints PASS
Integration checklist
•	☐ docker compose up brings up api and dashboard with no errors
•	☐ python scripts/smoke.py prints PASS
•	☐ A burst command makes an alert appear in the dashboard within 5 seconds
•	☐ p95 latency under 50 ms measured with the real scorer (scripts/load_test.py)
•	☐ Masking differs for ANALYST and SENIOR_ANALYST; unmasking writes an audit entry
•	☐ README quick start works on a fresh clone
•	☐ docs/model_report.md is committed for the judges
Five-minute demo script
1.	State the problem and the one-line architecture (20 seconds).
2.	Run make demo: benign traffic with about 4% injected scams streams in; the dashboard KPIs tick up.
3.	Run python -m simulator.main burst --typology mule_fan_in: a HIGH alert appears; open it to show the top three reasons and the network graph.
4.	Switch the role from ANALYST to SENIOR_ANALYST and unmask a token; show the audit log entry it created.
5.	Approve one alert and dismiss another; show both in the audit log.
6.	Show the platform angle: uname -m on the LinuxONE host, docker compose up, and the average latency KPI.
7.	Close with the model report: PR-AUC and recall for each of the four typologies.
Fallback plan
If this goes wrong	Do this
LightGBM will not build on s390x	Set AEGIS_MODEL_BACKEND=sklearn; the scorer and explanations work the same
No LinuxONE access during the event	Demo on a laptop and show the s390x build notes in docs/DEPLOY_LINUXONE.md; say plainly that the target is LinuxONE and what was tested where
Part 3 is not finished	The rule-based fallback inside score() keeps the whole pipeline demoable
Graph engine is slow	Lower WINDOW_HOURS to 6 and the stream rate to 10 per second
Network graph fails to render	Hide the graph panel; the alert table, reasons and decisions still work
Merge conflicts late	Contract changes only through one pull request; freeze the contract at hour 18

