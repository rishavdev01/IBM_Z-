"""FastAPI entrypoint for Aegis Exchange transaction and analyst APIs."""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import FastAPI, HTTPException

from app.alerts_store import init_db, list_recent_transactions
from app.auth import validate_production_secrets
from app.graph_engine import GraphEngine
from app.pipeline import process_transaction
from app.routers.alerts import router as alerts_router
from app.models import HealthResponse, RootResponse, Transaction, TransactionResponse


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Validate mandatory production secrets and initialize app graph state."""
    if os.getenv("AEGIS_ENV", "development").lower() == "production":
        validate_production_secrets()
    window_hours = int(os.getenv("WINDOW_HOURS", "24"))
    init_db()
    application.state.engine = GraphEngine(window_hours=window_hours)
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    application.state.engine.restore_transactions(list_recent_transactions(since))
    yield


app = FastAPI(
    title="Aegis Exchange",
    version="0.1.0",
    description=(
        "Fraud triage API. Public routes: GET /, GET /health, and POST /v1/transactions "
        "(ingestion currently has no bearer requirement). Alert, graph, statistics, and decision "
        "routes require HTTP Bearer credentials; X-Role selects ANALYST or SENIOR_ANALYST but "
        "does not authenticate the caller. GET /v1/audit additionally requires SENIOR_ANALYST."
    ),
    openapi_tags=[
        {"name": "service", "description": "Public service information and liveness."},
        {"name": "transactions", "description": "Open simulator transaction ingestion and scoring."},
        {"name": "alerts", "description": "Bearer-protected alert review and decisions."},
        {"name": "graph", "description": "Bearer-protected graph neighborhood queries."},
        {"name": "statistics", "description": "Bearer-protected persisted platform statistics."},
        {"name": "audit", "description": "Senior-role-only audit history."},
    ],
    lifespan=lifespan,
)
app.include_router(alerts_router)


@app.get("/", response_model=RootResponse, summary="Service information", tags=["service"])
def root() -> dict[str, str]:
    """Return basic service information and links to the API probes/docs."""
    return {
        "name": "Aegis Exchange",
        "status": "ok",
        "health": "/health",
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse, summary="Liveness check", tags=["service"])
def health() -> dict[str, str]:
    """Liveness probe used by teammates and Docker healthchecks."""
    return {"status": "ok"}


@app.post(
    "/v1/transactions",
    response_model=TransactionResponse,
    summary="Ingest and score a transaction",
    description=(
        "Currently unauthenticated. A unique txn_id is idempotent: retries return the persisted "
        "score and alert instead of creating duplicates. Transaction and qualifying alert persist "
        "atomically. Optional _truth is retained only for offline evaluation and is excluded from scoring."
    ),
    responses={422: {"description": "Invalid transaction fields, UUID4, UTC timestamp, or token format."},
               500: {"description": "Processing or persistence failed; no success result is returned."}},
    tags=["transactions"],
)
def ingest_transaction(payload: Transaction) -> dict:
    """Validate, score, and atomically persist a simulator transaction."""
    try:
        txn_version = UUID(payload.txn_id).version
        timestamp = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="txn_id must be a UUID4 and timestamp must be ISO-8601.") from exc
    if txn_version != 4:
        raise HTTPException(status_code=422, detail="txn_id must be a UUID4.")
    if timestamp.tzinfo is None:
        raise HTTPException(status_code=422, detail="timestamp must include a UTC offset.")
    if timestamp.utcoffset().total_seconds() != 0:
        raise HTTPException(status_code=422, detail="timestamp must be UTC.")
    if any(set(token.lower()) - set("0123456789abcdef") for token in
           (payload.sender_token, payload.receiver_token, payload.device_token)):
        raise HTTPException(status_code=422, detail="Transaction tokens must be 16 hexadecimal characters.")
    return process_transaction(payload.model_dump(by_alias=True, exclude_none=True), app.state.engine)
