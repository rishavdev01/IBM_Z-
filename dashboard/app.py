"""Aegis Exchange — Streamlit Analyst Dashboard (Part 4).

Features:
- Consumes the backend REST API exclusively using HTTPX.
- Real-time KPI statistics (/v1/stats).
- Filterable and searchable Fraud Alert Queue (/v1/alerts).
- Deep-dive alert investigation panel:
  - Transaction facts and risk score gauge (Plotly).
  - Explainable scorer reasons.
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

def mask_identifier(value: str | None) -> str:
    """Mask a sensitive identifier for the dashboard's default analyst view."""
    if not value:
        return ""
    value = str(value).strip()
    return value if len(value) <= 4 else value[:4] + "..."


def role_for_token(token: str) -> str | None:
    """Resolve a user-supplied bearer token to a configured role."""
    if token and os.getenv("AEGIS_ANALYST_TOKEN") and hmac.compare_digest(token, os.environ["AEGIS_ANALYST_TOKEN"]):
        return "ANALYST"
    if token and os.getenv("AEGIS_SENIOR_ANALYST_TOKEN") and hmac.compare_digest(token, os.environ["AEGIS_SENIOR_ANALYST_TOKEN"]):
        return "SENIOR_ANALYST"
    return None


def get_api_client(base_url: str, role: str, token: str = "") -> httpx.Client:
    """Return an HTTPX client using the credential entered by the dashboard user."""
    headers = {"X-Role": role, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers=headers,
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


def _show_api_errors(name: str) -> None:
    """Render current API failures in the dashboard."""
    errors = st.session_state.get("_api_errors", {})
    if name in errors:
        st.error(f"{name} unavailable: {errors[name]}")


def _http_error(status_code: int) -> str:
    """Provide a useful auth hint while preserving the actual HTTP status."""
    if status_code == 401:
        return "HTTP 401: the supplied credential was rejected"
    if status_code == 403:
        return "HTTP 403: this role is not permitted to access the resource"
    return f"HTTP {status_code}"


def _exception_message(error: Exception) -> str:
    """Describe common transport failures without exposing request headers or URLs."""
    if isinstance(error, httpx.TimeoutException):
        return "request timed out"
    if isinstance(error, httpx.ConnectError):
        return "could not connect to the API"
    if isinstance(error, httpx.LocalProtocolError):
        return "invalid local HTTP request (check the API URL and request headers)"
    return type(error).__name__


def check_api_health(client: Any) -> tuple[bool, str]:
    """Verify backend connectivity via GET /health."""
    try:
        r = client.get("/health")
        if r.status_code == 200 and r.json().get("status") == "ok":
            _set_api_error("API health", None)
            return True, "Connected"
        message = _http_error(r.status_code)
        _set_api_error("API health", message)
        return False, message
    except Exception as e:
        message = _exception_message(e)
        _set_api_error("API health", message)
        return False, f"Offline ({message})"


def _fetch_stats_response(client: Any) -> tuple[dict[str, Any] | None, int | None]:
    """Fetch KPI metrics and retain the HTTP status for authentication handling."""
    try:
        r = client.get("/v1/stats")
        if r.status_code == 200:
            _set_api_error("Statistics", None)
            return r.json(), r.status_code
        _set_api_error("Statistics", _http_error(r.status_code))
        return None, r.status_code
    except Exception as e:
        _set_api_error("Statistics", _exception_message(e))
        return None, None


def fetch_stats(client: Any) -> dict[str, Any] | None:
    """Fetch KPI metrics from /v1/stats."""
    return _fetch_stats_response(client)[0]


def load_dashboard_stats(
    client: Any,
    *,
    api_reachable: bool,
    token: str,
) -> tuple[dict[str, Any] | None, str]:
    """Fetch protected statistics only when reachable and a credential is present."""
    if not api_reachable:
        _set_api_error("Statistics", None)
        _set_api_error("Alerts", None)
        _set_api_error("Audit log", None)
        return None, "offline"
    if not token.strip():
        _set_api_error("Statistics", None)
        _set_api_error("Alerts", None)
        _set_api_error("Audit log", None)
        return None, "missing_credentials"

    stats, status_code = _fetch_stats_response(client)
    if status_code == 401:
        # Suppress duplicate per-panel 401s; the dashboard renders one auth state.
        _set_api_error("Statistics", None)
        _set_api_error("Alerts", None)
        _set_api_error("Audit log", None)
        return None, "invalid_credentials"
    return stats, "authenticated" if status_code == 200 else "request_error"


def fetch_alerts(
    client: Any,
    status: str | None = None,
    unmask: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]] | None:
    """Fetch alerts queue from /v1/alerts."""
    params: dict[str, Any] = {"limit": limit, "unmask": str(unmask).lower()}
    if status and status != "ALL":
        params["status"] = status
    try:
        r = client.get("/v1/alerts", params=params)
        if r.status_code == 200:
            _set_api_error("Alerts", None)
            return r.json()
        _set_api_error("Alerts", _http_error(r.status_code))
    except Exception as e:
        _set_api_error("Alerts", _exception_message(e))
    return None


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
        _set_api_error("Alert detail", _http_error(r.status_code))
    except Exception as e:
        _set_api_error("Alert detail", _exception_message(e))
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
        _set_api_error("Alert graph", _http_error(r.status_code))
    except Exception as e:
        _set_api_error("Alert graph", _exception_message(e))
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
        message = _http_error(r.status_code)
        _set_api_error("Decision", message)
        return False, f"Failed: {message}"
    except Exception as e:
        message = _exception_message(e)
        _set_api_error("Decision", message)
        return False, f"Error submitting decision: {message}"


def fetch_audit_logs(client: Any, limit: int = 200) -> tuple[int, list[dict[str, Any]]]:
    """Fetch audit logs from /v1/audit. Returns (status_code, logs)."""
    try:
        r = client.get("/v1/audit", params={"limit": limit})
        if r.status_code == 200:
            _set_api_error("Audit log", None)
            return 200, r.json()
        # A 403 is an expected analyst-role restriction, shown as a friendly
        # access message in the audit tab rather than a generic API failure.
        _set_api_error("Audit log", None if r.status_code == 403 else _http_error(r.status_code))
        return r.status_code, []
    except Exception as e:
        _set_api_error("Audit log", _exception_message(e))
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

        st.markdown("### API Reachability")
        if is_healthy:
            st.success("● API Reachable")
        else:
            st.error(f"● API Offline ({health_msg})")
        st.caption("IBM LinuxONE Architecture: `s390x`")

    st.title("Aegis Exchange — Real-Time Payment Fraud Detection")
    st.caption("Streaming graph features, transparent rule-based risk scoring, and privacy-first analyst workflows.")

    # KPI Statistics Banner. Health is public reachability; these data require a bearer token.
    stats, access_state = load_dashboard_stats(
        client,
        api_reachable=is_healthy,
        token=access_token,
    )
    if access_state == "missing_credentials":
        st.info("API is reachable. Enter the configured local analyst credential to load statistics and alerts.")
    elif access_state == "offline" and not access_token.strip():
        st.info("No credential is entered. Add the configured local analyst credential after the API is reachable.")
    elif access_state == "invalid_credentials":
        st.error("Authentication failed (HTTP 401). Enter a valid configured analyst or senior-role credential.")
    elif access_state == "offline":
        st.info("Protected data is unavailable while the API is offline. No cached data is being shown.")
    else:
        _show_api_errors("Statistics")
        if access_state == "authenticated":
            st.caption(f"Credentials accepted for {role.replace('_', ' ').lower()} access.")
    k1, k2, k3, k4 = st.columns(4)
    if stats is None:
        k1.metric("Transactions Processed", "—")
        k2.metric("Open Alerts", "—")
        k3.metric("Total Alerts", "—")
        k4.metric("Avg Latency", "—")
    else:
        k1.metric("Transactions Processed", f"{stats['total_txns']:,}")
        k2.metric("Open Alerts", f"{stats['alerts_open']:,}")
        k3.metric("Total Alerts", f"{stats['alerts_total']:,}")
        k4.metric("Avg Latency", f"{stats['avg_latency_ms']:.1f} ms")

    st.divider()

    tab_alerts, tab_audit = st.tabs(["🚨 Fraud Alert Queue", "📜 Audit Log"])

    with tab_alerts:
        col_filter, col_search = st.columns([1, 2])
        with col_filter:
            status_filter = st.selectbox("Filter Status", ["ALL", "OPEN", "APPROVED", "DISMISSED"], index=1)

        if access_state == "authenticated" or access_state == "request_error":
            alerts = fetch_alerts(client, status=status_filter, unmask=unmask, limit=100)
        else:
            alerts = None
        if access_state == "authenticated" or access_state == "request_error":
            _show_api_errors("Alerts")

        if alerts is None:
            if access_state == "missing_credentials":
                st.info("Enter the configured local analyst credential to retrieve alerts.")
            elif access_state == "invalid_credentials":
                st.info("Alerts are unavailable until authentication succeeds.")
            elif access_state == "offline":
                st.info("Alerts are unavailable while the API is offline. No cached results are shown.")
            else:
                st.info("Alert list is unavailable; see the API error above.")
        elif not alerts:
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
                _show_api_errors("Alert detail")
                if alert_detail:
                    st.subheader(f"🔍 Investigation: Alert #{selected_id}")

                    col_facts, col_reasons = st.columns([1, 1])

                    with col_facts:
                        st.markdown("#### Transaction Facts")
                        transaction_id = alert_detail.get("txn_id", "")
                        if not unmask:
                            transaction_id = mask_identifier(transaction_id)
                        st.write(f"**Transaction ID:** `{transaction_id}`")
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
                            st.info("The active scorer did not return factor explanations.")

                    st.markdown("#### Transfer Neighborhood Graph (2-Hop)")
                    st.caption("🔴 Red: Alert Sender | 🟠 Orange: Alert Receiver | 🔵 Blue: Intermediary Accounts")
                    graph_data = fetch_alert_graph(client, selected_id, hops=2, unmask=unmask)
                    _show_api_errors("Alert graph")
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

        if access_state == "missing_credentials":
            st.info("Enter the configured senior-role credential to view audit history.")
            status_code, audit_logs = None, []
        elif access_state == "offline":
            st.info("Audit history is unavailable while the API is offline. No cached results are shown.")
            status_code, audit_logs = None, []
        elif access_state == "invalid_credentials":
            st.info("Audit history is unavailable until authentication succeeds.")
            status_code, audit_logs = None, []
        else:
            status_code, audit_logs = fetch_audit_logs(client, limit=200)

        if status_code == 403:
            st.warning("🔒 Audit history requires senior-role access.")
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
        elif status_code == 200:
            st.info("No audit log records found.")
        elif status_code is not None:
            error = st.session_state.get("_api_errors", {}).get("Audit log", _http_error(status_code))
            st.error(f"Audit history unavailable: {error}.")

    if auto_refresh:
        time.sleep(3)
        st.rerun()


if __name__ == "__main__":
    run_dashboard()
