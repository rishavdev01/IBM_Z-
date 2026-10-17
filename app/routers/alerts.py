"""Alerts, Graph, Stats, and Audit REST API router for Aegis Exchange (Part 4).

Endpoints implemented:
- GET  /v1/alerts
- GET  /v1/alerts/{alert_id}
- POST /v1/alerts/{alert_id}/decision
- GET  /v1/alerts/{alert_id}/graph
- GET  /v1/stats
- GET  /v1/audit
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.alerts_store import (
    AlertAlreadyDecidedError,
    AlertNotFoundError,
    add_decision,
    get_alert,
    get_stats,
    list_alerts,
    list_audit,
    write_audit,
)
from app.auth import authenticated_role
from app.masking import (
    mask_alert_detail,
    mask_alert_summary,
    mask_graph_data,
    should_unmask,
)

router = APIRouter(prefix="/v1")


class DecisionRequest(BaseModel):
    decision: Literal["APPROVE", "DISMISS"]
    # Retained for request compatibility; never used as authenticated identity.
    analyst: str = Field(default="", description="Deprecated display field; attribution comes from bearer role")
    note: str = Field(default="", description="Optional analyst investigation notes")


# ---------------------------------------------------------------------------
# Alerts Endpoints
# ---------------------------------------------------------------------------


@router.get("/alerts")
def get_alerts_endpoint(
    status: str | None = Query(None, description="Filter by status: OPEN, APPROVED, DISMISSED"),
    limit: int = Query(100, ge=1, le=1000, description="Max alerts to return"),
    unmask: bool = Query(False, description="Unmask account tokens (requires SENIOR_ANALYST)"),
    role: str = Depends(authenticated_role),
) -> list[dict[str, Any]]:
    """List fraud alerts with deterministic sorting and privacy masking."""
    try:
        alerts = list_alerts(status=status, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if should_unmask(role, unmask):
        write_audit(
            actor=f"{role}_USER",
            role=role,
            action="UNMASK",
            target="alerts_list",
            detail={"count": len(alerts), "status": status},
        )

    return [mask_alert_summary(a, unmask=unmask, role=role) for a in alerts]


@router.get("/alerts/{alert_id}")
def get_alert_detail_endpoint(
    alert_id: int,
    unmask: bool = Query(False, description="Unmask account tokens (requires SENIOR_ANALYST)"),
    role: str = Depends(authenticated_role),
) -> dict[str, Any]:
    """Retrieve full alert details including reasons, features, decision history, and audit log."""
    alert = get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")

    if should_unmask(role, unmask):
        write_audit(
            actor=f"{role}_USER",
            role=role,
            action="UNMASK",
            target=f"alert:{alert_id}",
            detail={"alert_id": alert_id},
        )

    return mask_alert_detail(alert, unmask=unmask, role=role)


@router.post("/alerts/{alert_id}/decision")
def post_decision_endpoint(
    alert_id: int,
    payload: DecisionRequest,
    role: str = Depends(authenticated_role),
) -> dict[str, Any]:
    """Submit an analyst decision (APPROVE or DISMISS).

    Atomically updates alert status, records decision record, and appends to audit log.
    """
    try:
        res = add_decision(
            alert_id=alert_id,
            decision=payload.decision,
            analyst=role,
            note=payload.note,
            role=role,
        )
        return {
            "status": "ok",
            "alert_id": alert_id,
            "decision": res["decision"],
            "new_status": res["status"],
            "analyst": res["analyst"],
            "decided_at": res["decided_at"],
        }
    except AlertNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except AlertAlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/alerts/{alert_id}/graph")
def get_alert_graph_endpoint(
    alert_id: int,
    request: Request,
    hops: int = Query(2, ge=1, le=3, description="Neighborhood hops (1 to 3)"),
    unmask: bool = Query(False, description="Unmask node IDs (requires SENIOR_ANALYST)"),
    role: str = Depends(authenticated_role),
) -> dict[str, Any]:
    """Retrieve the transfer neighborhood graph for the alert's sender.

    Nodes are tagged server-side with roles ('sender', 'receiver', 'other') before masking.
    """
    alert = get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")

    raw_sender = str(alert.get("sender_token", ""))
    raw_receiver = str(alert.get("receiver_token", ""))

    # Query graph engine from request.app.state if available (Part 2)
    graph: dict[str, Any] | None = None
    engine = getattr(request.app.state, "engine", None)
    if engine is not None and hasattr(engine, "neighborhood"):
        try:
            graph = engine.neighborhood(raw_sender, hops=hops)
        except Exception:
            graph = None

    # Graceful fallback graph if engine is not running or neighborhood empty
    if not graph or not graph.get("nodes"):
        graph = {
            "nodes": [
                {"id": raw_sender, "in_degree": 0, "out_degree": 1},
                {"id": raw_receiver, "in_degree": 1, "out_degree": 0},
            ],
            "edges": [
                {
                    "source": raw_sender,
                    "target": raw_receiver,
                    "amount": alert.get("amount", 0.0),
                    "timestamp": alert.get("created_at", ""),
                    "txn_id": alert.get("txn_id", ""),
                }
            ],
        }

    if should_unmask(role, unmask):
        write_audit(
            actor=f"{role}_USER",
            role=role,
            action="UNMASK",
            target=f"graph:alert:{alert_id}",
            detail={"alert_id": alert_id, "hops": hops},
        )

    return mask_graph_data(
        graph,
        sender_raw=raw_sender,
        receiver_raw=raw_receiver,
        unmask=unmask,
        role=role,
    )


@router.get("/graph/neighborhood/{token}")
def get_graph_neighborhood_endpoint(
    token: str,
    request: Request,
    hops: int = Query(2, ge=1, le=3),
    unmask: bool = Query(False),
    role: str = Depends(authenticated_role),
) -> dict[str, Any]:
    """Return a role-authorized graph neighborhood with masked identifiers."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Graph engine is unavailable.")
    graph = engine.neighborhood(token, hops=hops)
    if should_unmask(role, unmask):
        write_audit(actor=role, role=role, action="UNMASK", target="graph_neighborhood",
                    detail={"hops": hops})
    return mask_graph_data(graph, sender_raw=token, receiver_raw="", unmask=unmask, role=role)


# ---------------------------------------------------------------------------
# Platform Stats & Audit Endpoints
# ---------------------------------------------------------------------------


@router.get("/stats")
def get_stats_endpoint(role: str = Depends(authenticated_role)) -> dict[str, Any]:
    """Return platform statistics for the analyst dashboard."""
    return get_stats()


@router.get("/audit")
def get_audit_endpoint(
    limit: int = Query(200, ge=1, le=1000, description="Max audit entries to return"),
    role: str = Depends(authenticated_role),
) -> list[dict[str, Any]]:
    """Retrieve immutable audit log records. Restricted to SENIOR_ANALYST."""
    if role != "SENIOR_ANALYST":
        raise HTTPException(
            status_code=403,
            detail="Forbidden: SENIOR_ANALYST role is required to view audit logs.",
        )
    return list_audit(limit=limit)
