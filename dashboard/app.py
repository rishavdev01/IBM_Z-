"""Aegis Exchange — Streamlit Analyst Dashboard (Part 4).

Features:
- Consumes the backend REST API exclusively using HTTPX.
- Real-time KPI statistics (/v1/stats).
- Filterable and searchable Fraud Alert Queue (/v1/alerts).
- Deep-dive alert investigation panel:
  - Transaction facts and risk score gauge (Plotly).
  - Explainable ML risk factors.
  - Interactive transfer neighborhood graph (PyVis).
  - Analyst decision actions (APPROVE / DISMISS).
- RBAC role selection and privacy unmasking with audit trail (/v1/audit).
"""

from __future__ import annotations

import os
import time
import hmac
from typing import Any

import httpx
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# Safe Plotly import
try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# Safe PyVis import
try:
    from pyvis.network import Network
    HAS_PYVIS = True
except ImportError:
    HAS_PYVIS = False


# ---------------------------------------------------------------------------
# API Client Helpers
# ---------------------------------------------------------------------------

def role_for_token(token: str) -> str | None:
    """Resolve a user-supplied bearer token to a configured role."""
    if token and os.getenv("AEGIS_ANALYST_TOKEN") and hmac.compare_digest(token, os.environ["AEGIS_ANALYST_TOKEN"]):
        return "ANALYST"
    if token and os.getenv("AEGIS_SENIOR_ANALYST_TOKEN") and hmac.compare_digest(token, os.environ["AEGIS_SENIOR_ANALYST_TOKEN"]):
        return "SENIOR_ANALYST"
    return None


def get_api_client(base_url: str, role: str, token: str = "") -> httpx.Client:
    """Return an HTTPX client using the credential entered by the dashboard user."""
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={"X-Role": role, "Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=10.0,
    )


def _set_api_error(name: str, message: str | None) -> None:
    """Store endpoint availability for display separately from empty results."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx(suppress_warning=True) is not None:
            errors = dict(st.session_state.get("_api_errors", {}))
            if message:
                errors[name] = message
            else:
                errors.pop(name, None)
            st.session_state["_api_errors"] = errors
    except Exception:
        return


def _show_api_errors() -> None:
    """Render current API failures in the dashboard."""
    errors = st.session_state.get("_api_errors", {})
    for name, message in errors.items():
        st.error(f"{name} unavailable: {message}")


def check_api_health(client: Any) -> tuple[bool, str]:
    """Verify backend connectivity via GET /health."""
    try:
        r = client.get("/health")
        if r.status_code == 200 and r.json().get("status") == "ok":
            _set_api_error("API health", None)
            return True, "Connected"
        _set_api_error("API health", f"HTTP {r.status_code}")
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        _set_api_error("API health", type(e).__name__)
        return False, f"Offline ({type(e).__name__})"


def fetch_stats(client: Any) -> dict[str, Any]:
    """Fetch KPI metrics from /v1/stats."""
    try:
        r = client.get("/v1/stats")
        if r.status_code == 200:
            _set_api_error("Statistics", None)
            return r.json()
        _set_api_error("Statistics", f"HTTP {r.status_code}")
    except Exception as e:
        _set_api_error("Statistics", type(e).__name__)
    return {"total_txns": 0, "alerts_open": 0, "alerts_total": 0, "avg_latency_ms": 0.0}


def fetch_alerts(
    client: Any,
    status: str | None = None,
    unmask: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch alerts queue from /v1/alerts."""
    params: dict[str, Any] = {"limit": limit, "unmask": str(unmask).lower()}
    if status and status != "ALL":
        params["status"] = status
    try:
        r = client.get("/v1/alerts", params=params)
        if r.status_code == 200:
            _set_api_error("Alerts", None)
            return r.json()
        _set_api_error("Alerts", f"HTTP {r.status_code}")
    except Exception as e:
        _set_api_error("Alerts", type(e).__name__)
    return []


def fetch_alert_detail(
    client: Any,
    alert_id: int,
    unmask: bool = False,
) -> dict[str, Any] | None:
    """Fetch full alert detail from /v1/alerts/{alert_id}."""
    try:
        r = client.get(f"/v1/alerts/{alert_id}", params={"unmask": str(unmask).lower()})
        if r.status_code == 200:
            _set_api_error("Alert detail", None)
            return r.json()
        _set_api_error("Alert detail", f"HTTP {r.status_code}")
    except Exception as e:
        _set_api_error("Alert detail", type(e).__name__)
    return None


def fetch_alert_graph(
    client: Any,
    alert_id: int,
    hops: int = 2,
    unmask: bool = False,
) -> dict[str, Any] | None:
    """Fetch graph neighborhood from /v1/alerts/{alert_id}/graph."""
    try:
        r = client.get(
            f"/v1/alerts/{alert_id}/graph",
            params={"hops": hops, "unmask": str(unmask).lower()},
        )
        if r.status_code == 200:
            _set_api_error("Alert graph", None)
            return r.json()
        _set_api_error("Alert graph", f"HTTP {r.status_code}")
    except Exception as e:
        _set_api_error("Alert graph", type(e).__name__)
    return None


def submit_decision(
    client: Any,
    alert_id: int,
    decision: str,
    analyst: str,
    note: str,
) -> tuple[bool, str]:
    """Submit an APPROVE or DISMISS decision to /v1/alerts/{alert_id}/decision."""
    try:
        r = client.post(
            f"/v1/alerts/{alert_id}/decision",
            json={"decision": decision, "note": note},
        )
        if r.status_code == 200:
            _set_api_error("Decision", None)
            return True, f"Decision {decision} recorded successfully."
        _set_api_error("Decision", f"HTTP {r.status_code}")
        return False, f"Failed: HTTP {r.status_code} - {r.text}"
    except Exception as e:
        _set_api_error("Decision", type(e).__name__)
        return False, f"Error submitting decision: {e}"


def fetch_audit_logs(client: Any, limit: int = 200) -> tuple[int, list[dict[str, Any]]]:
    """Fetch audit logs from /v1/audit. Returns (status_code, logs)."""
    try:
        r = client.get("/v1/audit", params={"limit": limit})
        if r.status_code == 200:
            _set_api_error("Audit log", None)
            return 200, r.json()
        _set_api_error("Audit log", f"HTTP {r.status_code}")
        return r.status_code, []
    except Exception as e:
        _set_api_error("Audit log", type(e).__name__)
        return 500, []


# ---------------------------------------------------------------------------
# UI Components: Visualizations
# ---------------------------------------------------------------------------

def render_risk_gauge(score: float, level: str):
    """Render a risk score gauge using Plotly."""
    if not HAS_PLOTLY:
        st.metric("Risk Score", f"{score:.2f}", delta=level)
        return

    color_map = {"HIGH": "#EF4444", "MEDIUM": "#F59E0B", "LOW": "#10B981"}
    bar_color = color_map.get(level, "#3B82F6")

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=score,
            number={"valueformat": ".2f", "font": {"size": 28, "color": "#F8FAFC"}},
            title={"text": f"Risk Score ({level})", "font": {"size": 16, "color": "#94A3B8"}},
            gauge={
                "axis": {"range": [0, 1.0], "tickwidth": 1, "tickcolor": "#64748B"},
                "bar": {"color": bar_color, "thickness": 0.3},
                "bgcolor": "#1E293B",
                "borderwidth": 0,
                "steps": [
                    {"range": [0, 0.40], "color": "#064E3B"},
                    {"range": [0.40, 0.70], "color": "#78350F"},
                    {"range": [0.70, 1.0], "color": "#7F1D1D"},
                ],
                "threshold": {
                    "line": {"color": "#EF4444", "width": 3},
                    "thickness": 0.8,
                    "value": 0.70,
                },
            },
        )
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=180,
        margin={"t": 35, "b": 10, "l": 25, "r": 25},
    )
    st.plotly_chart(fig, use_container_width=True)


def render_graph_network(graph_data: dict[str, Any], height: int = 400):
    """Render interactive PyVis network of transfer neighborhood."""
    if not HAS_PYVIS:
        st.info("PyVis not installed; displaying graph nodes and edges as table.")
        st.json(graph_data)
        return

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    if not nodes:
        st.info("No neighborhood transfer activity found for this alert.")
        return

    net = Network(height=f"{height}px", width="100%", bgcolor="#0F172A", font_color="#F8FAFC")
    net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=120)

    # Add nodes with roles & degree sizing
    for n in nodes:
        node_id = str(n.get("id", ""))
        role = n.get("role", "other")
        in_deg = n.get("in_degree", 0)
        out_deg = n.get("out_degree", 0)
        total_deg = in_deg + out_deg

        size = max(18, min(40, 18 + total_deg * 4))

        if role == "sender":
            color = "#EF4444"   # Red for sender
            title = f"Sender (Alert Target)\nID: {node_id}\nIn: {in_deg}, Out: {out_deg}"
        elif role == "receiver":
            color = "#F97316"   # Orange for receiver
            title = f"Receiver (Destination)\nID: {node_id}\nIn: {in_deg}, Out: {out_deg}"
        else:
            color = "#3B82F6"   # Blue for other accounts
            title = f"Intermediary Node\nID: {node_id}\nIn: {in_deg}, Out: {out_deg}"

        net.add_node(
            node_id,
            label=node_id,
            color=color,
            size=size,
            title=title,
            borderWidth=2,
            shadow=True,
        )

    # Add edges with amounts and timestamps
    for e in edges:
        src = str(e.get("source", ""))
        tgt = str(e.get("target", ""))
        amt = e.get("amount", 0.0)
        ts = e.get("timestamp", "")
        label = f"₹{amt:,.0f}" if amt else ""
        net.add_edge(
            src,
            tgt,
            label=label,
            title=f"Amount: ₹{amt:,.2f}\nTime: {ts}",
            color="#64748B",
            arrows="to",
        )

    html_str = net.generate_html()
    components.html(html_str, height=height + 20)


# ---------------------------------------------------------------------------
# Main Application Execution
# ---------------------------------------------------------------------------

def run_dashboard():
    """Execute the interactive Streamlit dashboard."""
    st.set_page_config(
        page_title="Aegis Exchange — Fraud Analyst Console",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    default_api_url = os.getenv("AEGIS_API_URL", "http://localhost:8000")

    with st.sidebar:
        st.title("🛡️ Aegis Console")
        st.markdown("Fraud investigation and privacy-conscious decision support.")
        st.divider()

        api_url = st.text_input("Backend API URL", value=default_api_url)
        access_token = st.text_input("Analyst access token", type="password")
        role = role_for_token(access_token)

        is_senior = (role == "SENIOR_ANALYST")
        unmask = st.checkbox(
            "🔓 Unmask Identifiers",
            value=False,
            disabled=not is_senior,
            help="Reveals raw account tokens only with the configured senior credential; access is audited.",
        )
        if not is_senior and unmask:
            unmask = False

        st.divider()
        auto_refresh = st.checkbox("⚡ Auto-refresh (3s)", value=False)
        if st.button("🔄 Refresh Data Now"):
            st.rerun()

        # API Connection Check
        role = role or "ANALYST"
        client = get_api_client(api_url, role, access_token)
        is_healthy, health_msg = check_api_health(client)

        st.markdown("### System Health")
        if is_healthy:
            st.success(f"● API {health_msg}")
        else:
            st.error(f"● API {health_msg}")
        _show_api_errors()

        st.caption("IBM LinuxONE Architecture: `s390x`")

    st.title("Aegis Exchange — Real-Time Payment Fraud Detection")
    st.caption("High-throughput streaming graph features, LightGBM explainability, and privacy-first analyst workflows.")

    # KPI Statistics Banner
    stats = fetch_stats(client)
    _show_api_errors()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Transactions Processed", f"{stats.get('total_txns', 0):,}")
    k2.metric("Open Alerts", f"{stats.get('alerts_open', 0):,}")
    k3.metric("Total Alerts", f"{stats.get('alerts_total', 0):,}")
    k4.metric("Avg Latency", f"{stats.get('avg_latency_ms', 0.0):.1f} ms")

    st.divider()

    tab_alerts, tab_audit = st.tabs(["🚨 Fraud Alert Queue", "📜 Audit Log"])

    with tab_alerts:
        col_filter, col_search = st.columns([1, 2])
        with col_filter:
            status_filter = st.selectbox("Filter Status", ["ALL", "OPEN", "APPROVED", "DISMISSED"], index=1)

        alerts = fetch_alerts(client, status=status_filter, unmask=unmask, limit=100)
        _show_api_errors()

        if not alerts:
            st.info("No alerts found matching the current filter criteria.")
        else:
            df_display = pd.DataFrame(alerts)[
                ["alert_id", "risk_level", "risk_score", "amount", "sender_token", "receiver_token", "status", "created_at"]
            ]
            df_display["amount"] = df_display["amount"].apply(lambda v: f"₹{v:,.2f}")
            df_display["risk_score"] = df_display["risk_score"].apply(lambda v: f"{v:.2f}")

            st.dataframe(
                df_display,
                column_config={
                    "alert_id": "Alert ID",
                    "risk_level": "Risk Level",
                    "risk_score": "Score",
                    "amount": "Amount",
                    "sender_token": "Sender",
                    "receiver_token": "Receiver",
                    "status": "Status",
                    "created_at": "Timestamp",
                },
                use_container_width=True,
                hide_index=True,
            )

            st.divider()

            alert_ids = [a["alert_id"] for a in alerts]
            selected_id = st.selectbox("Select Alert to Investigate", alert_ids, format_func=lambda aid: f"Alert #{aid}")

            if selected_id:
                alert_detail = fetch_alert_detail(client, selected_id, unmask=unmask)
                if alert_detail:
                    st.subheader(f"🔍 Investigation: Alert #{selected_id}")

                    col_facts, col_reasons = st.columns([1, 1])

                    with col_facts:
                        st.markdown("#### Transaction Facts")
                        st.write(f"**Transaction ID:** `{alert_detail.get('txn_id')}`")
                        st.write(f"**Amount:** ₹{alert_detail.get('amount', 0.0):,.2f}")
                        st.write(f"**Sender Token:** `{alert_detail.get('sender_token')}`")
                        st.write(f"**Receiver Token:** `{alert_detail.get('receiver_token')}`")
                        st.write(f"**Current Status:** `{alert_detail.get('status')}`")
                        st.write(f"**Created At:** {alert_detail.get('created_at')}")

                        render_risk_gauge(
                            alert_detail.get("risk_score", 0.0),
                            alert_detail.get("risk_level", "LOW"),
                        )

                    with col_reasons:
                        st.markdown("#### Explainable Risk Reasons")
                        reasons = alert_detail.get("reasons", [])
                        if reasons:
                            for r in reasons[:3]:
                                st.markdown(
                                    f"""
                                    <div style="background-color: #1E293B; padding: 10px; border-radius: 6px; margin-bottom: 8px; border-left: 4px solid #EF4444;">
                                        <b>{r.get('factor', 'Feature')}</b> (Impact: {r.get('contribution', 0.0):.2f})<br/>
                                        <span style="color: #94A3B8; font-size: 0.9em;">{r.get('description', 'Suspicious activity pattern')}</span>
                                    </div>
                                    """,
                                    unsafe_allow_html=True,
                                )
                        else:
                            st.info("No specific risk explanations provided by ML model.")

                    st.markdown("#### Transfer Neighborhood Graph (2-Hop)")
                    st.caption("🔴 Red: Alert Sender | 🟠 Orange: Alert Receiver | 🔵 Blue: Intermediary Accounts")
                    graph_data = fetch_alert_graph(client, selected_id, hops=2, unmask=unmask)
                    if graph_data:
                        render_graph_network(graph_data, height=360)

                    st.divider()
                    st.markdown("#### Analyst Decision")

                    current_status = alert_detail.get("status")
                    if current_status == "OPEN":
                        note = st.text_area("Investigation Note", placeholder="Provide rationale for decision...", key=f"note_{selected_id}")
                        btn_approve, btn_dismiss = st.columns(2)

                        with btn_approve:
                            if st.button("🚨 Approve (Confirmed Fraud)", type="primary", use_container_width=True):
                                ok, msg = submit_decision(client, selected_id, "APPROVE", "", note)
                                if ok:
                                    st.success(msg)
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error(msg)

                        with btn_dismiss:
                            if st.button("✅ Dismiss (False Positive)", use_container_width=True):
                                ok, msg = submit_decision(client, selected_id, "DISMISS", "", note)
                                if ok:
                                    st.success(msg)
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error(msg)
                    else:
                        st.info(f"This alert has already been decided: **{current_status}**.")
                        decisions = alert_detail.get("decisions", [])
                        if decisions:
                            for d in decisions:
                                st.write(f"- **{d.get('decision')}** by `{d.get('analyst')}` at {d.get('decided_at')}: *{d.get('note') or 'No note'}*")

    with tab_audit:
        st.subheader("Immutable Audit Trail")
        st.caption("Tracks all decisions, exports, and authorized unmask operations. Restricted to SENIOR_ANALYST.")

        status_code, audit_logs = fetch_audit_logs(client, limit=200)

        if status_code == 403:
            st.warning(
                "🔒 **Access Restricted:** Viewing audit logs requires the `SENIOR_ANALYST` role.\n\n"
                "To access this tab, switch your role to **SENIOR_ANALYST** in the sidebar."
            )
        elif status_code == 200 and audit_logs:
            df_audit = pd.DataFrame(audit_logs)[["id", "ts", "actor", "role", "action", "target", "detail"]]
            df_audit["detail"] = df_audit["detail"].apply(lambda d: str(d))
            st.dataframe(
                df_audit,
                column_config={
                    "id": "ID",
                    "ts": "Timestamp",
                    "actor": "Actor",
                    "role": "Role",
                    "action": "Action",
                    "target": "Target",
                    "detail": "Details",
                },
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No audit log records found.")

    if auto_refresh:
        time.sleep(3)
        st.rerun()


if __name__ == "__main__":
    run_dashboard()
