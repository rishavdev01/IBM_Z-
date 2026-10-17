"""Privacy masking and RBAC helpers for Aegis Exchange (Part 4).

Contract Rules:
- Tokens/account identifiers are masked as the first four characters + '...' by default.
- Missing role header defaults to ANALYST.
- Accepted roles: ANALYST, SENIOR_ANALYST.
- Invalid role values raise HTTP 400.
- Raw tokens may be revealed ONLY when role is SENIOR_ANALYST and unmask=true.
- Every authorized unmask operation must trigger an audit log.
"""

from __future__ import annotations

import copy
from typing import Any

VALID_ROLES = {"ANALYST", "SENIOR_ANALYST"}


def validate_role(role: str | None) -> str:
    """Validate and normalize the X-Role header.

    Defaults to ANALYST if missing or blank.
    Raises ValueError for any unrecognized role.
    """
    if role is None or not role.strip():
        return "ANALYST"
    normalized = role.strip()
    if normalized not in VALID_ROLES:
        raise ValueError(
            f"Invalid role: '{role}'. Accepted roles are: {', '.join(sorted(VALID_ROLES))}."
        )
    return normalized


def should_unmask(role: str, unmask: bool) -> bool:
    """Check if the requester is authorized to view unmasked data."""
    return role == "SENIOR_ANALYST" and bool(unmask)


def mask_token(token: str | None) -> str:
    """Mask an identifier as first 4 characters + '...'.

    Safe for None, empty, or short strings.
    """
    if not token:
        return ""
    token_str = str(token).strip()
    if len(token_str) <= 4:
        return token_str + "..."
    return token_str[:4] + "..."


def mask_alert_summary(
    alert: dict[str, Any],
    unmask: bool = False,
    role: str = "ANALYST",
) -> dict[str, Any]:
    """Return a copy of an alert with tokens masked unless authorized."""
    res = copy.deepcopy(alert)
    if not should_unmask(role, unmask):
        res["sender_token"] = mask_token(res.get("sender_token"))
        res["receiver_token"] = mask_token(res.get("receiver_token"))
    return res


def mask_alert_detail(
    detail: dict[str, Any],
    unmask: bool = False,
    role: str = "ANALYST",
) -> dict[str, Any]:
    """Return a copy of full alert details with tokens masked unless authorized."""
    res = copy.deepcopy(detail)
    if not should_unmask(role, unmask):
        res["sender_token"] = mask_token(res.get("sender_token"))
        res["receiver_token"] = mask_token(res.get("receiver_token"))
    return res


def mask_graph_data(
    graph: dict[str, Any],
    sender_raw: str,
    receiver_raw: str,
    unmask: bool = False,
    role: str = "ANALYST",
) -> dict[str, Any]:
    """Add role tags to nodes ('sender', 'receiver', 'other') and mask tokens consistently."""
    res = copy.deepcopy(graph)
    nodes = res.get("nodes", [])
    edges = res.get("edges", [])

    is_unmasked = should_unmask(role, unmask)

    # 1. Assign semantic roles server-side before any masking
    for node in nodes:
        raw_id = str(node.get("id", ""))
        if raw_id == sender_raw:
            node["role"] = "sender"
        elif raw_id == receiver_raw:
            node["role"] = "receiver"
        else:
            node["role"] = "other"

    if is_unmasked:
        return res

    # 2. Consistent node ID mapping to preserve graph structure
    token_map: dict[str, str] = {}

    def _get_masked_id(raw_id: str) -> str:
        if raw_id not in token_map:
            token_map[raw_id] = mask_token(raw_id)
        return token_map[raw_id]

    # Mask nodes
    for node in nodes:
        raw_id = str(node.get("id", ""))
        node["id"] = _get_masked_id(raw_id)

    # Mask edges
    for edge in edges:
        source_raw = str(edge.get("source", ""))
        target_raw = str(edge.get("target", ""))
        edge["source"] = _get_masked_id(source_raw)
        edge["target"] = _get_masked_id(target_raw)

    return res
