"""FastAPI entrypoint. GET /health is live from Step 0; Part 2 adds ingestion."""

from fastapi import FastAPI

from app.routers.alerts import router as alerts_router

app = FastAPI(title="Aegis Exchange", version="0.0.0")
app.include_router(alerts_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by teammates and Docker healthchecks."""
    return {"status": "ok"}
