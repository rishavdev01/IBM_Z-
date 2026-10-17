"""Dataset extraction pipeline for Aegis ML.

Replays labeled transactions in timestamp order through GraphEngine to extract
the 16 contract graph features per transaction.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
import os
from typing import Any
import networkx as nx
import pandas as pd

from aegis_ml.features import FEATURE_NAMES

CHANNEL_MAP = {"UPI": 0, "IMPS": 1, "NEFT": 2}


class _ContractGraphEngine:
    """A high-performance rolling 24-hour graph engine implementing the exact 16 contract features.

    Used when app.graph_engine is not yet available, ensuring Part 3 is fully decoupled.
    """

    def __init__(self, window_hours: int = 24) -> None:
        self.window = timedelta(hours=window_hours)
        self.g = nx.DiGraph()
        # Per-account history: deques of (timestamp, other_party, amount)
        self.inbound: dict[str, deque[tuple[datetime, str, float]]] = defaultdict(deque)
        self.outbound: dict[str, deque[tuple[datetime, str, float]]] = defaultdict(deque)
        # Lifetime seen devices per sender
        self.devices_seen: dict[str, set[str]] = defaultdict(set)
        self.latest_time: datetime | None = None

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.window
        # Prune inbound deques
        for acc in list(self.inbound.keys()):
            dq = self.inbound[acc]
            while dq and dq[0][0] < cutoff:
                dq.popleft()
            if not dq and not self.outbound.get(acc):
                del self.inbound[acc]

        # Prune outbound deques
        for acc in list(self.outbound.keys()):
            dq = self.outbound[acc]
            while dq and dq[0][0] < cutoff:
                dq.popleft()
            if not dq and not self.inbound.get(acc):
                del self.outbound[acc]

        # Prune graph edges older than cutoff
        edges_to_remove = []
        for u, v, data in self.g.edges(data=True):
            txns = [t for t in data.get("txns", []) if t[0] >= cutoff]
            if txns:
                data["txns"] = txns
            else:
                edges_to_remove.append((u, v))

        for u, v in edges_to_remove:
            self.g.remove_edge(u, v)

        # Remove isolated nodes
        isolated = [n for n in self.g.nodes() if self.g.in_degree(n) == 0 and self.g.out_degree(n) == 0]
        for n in isolated:
            self.g.remove_node(n)

    def extract_features(self, txn: dict[str, Any]) -> dict[str, Any]:
        """Compute the 16 contract features, then add txn to graph."""
        ts_str = txn["timestamp"]
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if self.latest_time is None or ts > self.latest_time:
            self.latest_time = ts
        self._prune(ts)

        sender = str(txn["sender_token"])
        receiver = str(txn["receiver_token"])
        amount = float(txn["amount"])
        channel_str = str(txn.get("channel", "UPI"))
        channel_code = CHANNEL_MAP.get(channel_str, 0)
        device = str(txn.get("device_token", ""))

        # 1. Basic transaction facts
        hour_of_day = ts.hour

        # 2. History features (prior to adding this transaction)
        # sender_new_device
        sender_new_device = 1 if device not in self.devices_seen[sender] else 0

        # amount_vs_sender_avg
        sender_past_out = self.outbound.get(sender, deque())
        if sender_past_out:
            past_amounts = [t[2] for t in sender_past_out]
            avg_past = sum(past_amounts) / len(past_amounts)
            amount_vs_sender_avg = amount / avg_past if avg_past > 0 else 1.0
        else:
            amount_vs_sender_avg = 1.0

        # sender_seconds_since_last_inbound
        sender_in = self.inbound.get(sender, deque())
        if sender_in:
            last_in_ts = sender_in[-1][0]
            sender_seconds_since_last_inbound = max(0.0, (ts - last_in_ts).total_seconds())
        else:
            sender_seconds_since_last_inbound = -1.0

        # receiver_min_forward_seconds
        rec_in = self.inbound.get(receiver, deque())
        rec_out = self.outbound.get(receiver, deque())
        min_fwd = -1.0
        if rec_in and rec_out:
            for in_t, _, _ in rec_in:
                for out_t, _, _ in rec_out:
                    if out_t >= in_t:
                        gap = (out_t - in_t).total_seconds()
                        if min_fwd < 0 or gap < min_fwd:
                            min_fwd = gap
        receiver_min_forward_seconds = min_fwd

        # in_cycle & cycle_length (check if path receiver -> sender of length <= 4 exists)
        in_cycle = 0
        cycle_len = 0
        if self.g.has_node(receiver) and self.g.has_node(sender):
            try:
                # Find shortest path from receiver back to sender
                path = nx.shortest_path(self.g, source=receiver, target=sender)
                path_len = len(path) - 1
                if 1 <= path_len <= 4:
                    in_cycle = 1
                    cycle_len = path_len + 1
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                in_cycle = 0
                cycle_len = 0

        # 3. Aggregations including current transaction
        # sender_out_degree_24h (distinct receivers)
        current_sender_receivers = {t[1] for t in sender_past_out} | {receiver}
        sender_out_degree_24h = len(current_sender_receivers)

        # sender_txn_count_1h
        one_hour_ago = ts - timedelta(hours=1)
        sender_txn_count_1h = 1 + sum(1 for t in sender_past_out if t[0] >= one_hour_ago)

        # receiver_in_degree_24h (distinct senders)
        current_rec_senders = {t[1] for t in rec_in} | {sender}
        receiver_in_degree_24h = len(current_rec_senders)

        # receiver amounts
        rec_in_amt = sum(t[2] for t in rec_in) + amount
        rec_out_amt = sum(t[2] for t in rec_out)
        receiver_forward_ratio = (rec_out_amt / rec_in_amt) if rec_in_amt > 0 else 0.0

        # sender amounts
        sender_in_amt = sum(t[2] for t in sender_in)
        sender_out_amt = sum(t[2] for t in sender_past_out) + amount
        sender_forward_ratio = (sender_out_amt / sender_in_amt) if sender_in_amt > 0 else 0.0

        # 4. Update state with this transaction
        self.outbound[sender].append((ts, receiver, amount))
        self.inbound[receiver].append((ts, sender, amount))
        self.devices_seen[sender].add(device)

        if not self.g.has_edge(sender, receiver):
            self.g.add_edge(sender, receiver, txns=[(ts, amount)])
        else:
            self.g[sender][receiver]["txns"].append((ts, amount))

        return {
            "amount": amount,
            "hour_of_day": hour_of_day,
            "channel_code": channel_code,
            "sender_out_degree_24h": sender_out_degree_24h,
            "sender_txn_count_1h": sender_txn_count_1h,
            "receiver_in_degree_24h": receiver_in_degree_24h,
            "receiver_in_amount_24h": rec_in_amt,
            "receiver_out_amount_24h": rec_out_amt,
            "receiver_forward_ratio": receiver_forward_ratio,
            "receiver_min_forward_seconds": receiver_min_forward_seconds,
            "in_cycle": in_cycle,
            "cycle_length": cycle_len,
            "sender_seconds_since_last_inbound": sender_seconds_since_last_inbound,
            "sender_forward_ratio": sender_forward_ratio,
            "sender_new_device": sender_new_device,
            "amount_vs_sender_avg": amount_vs_sender_avg,
        }


def _get_engine() -> Any:
    """Return GraphEngine from app if available, else _ContractGraphEngine."""
    try:
        from app.graph_engine import GraphEngine  # type: ignore
        return GraphEngine()
    except (ImportError, AttributeError):
        return _ContractGraphEngine()


def build_training_frame(csv_path: str = "data/transactions_labeled.csv") -> pd.DataFrame:
    """Build a training DataFrame by replaying transactions through GraphEngine.

    Reads transactions in timestamp order, extracts the 16 contract graph features,
    and returns a DataFrame containing feature columns, 'is_scam' (0/1), and 'typology'.
    Never uses is_scam or typology as features.

    Args:
        csv_path: Path to the labeled transactions CSV.

    Returns:
        pd.DataFrame with 16 feature columns + 'is_scam' and 'typology'.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Transactions CSV not found at '{csv_path}'")

    df_raw = pd.read_csv(csv_path)
    df_raw = df_raw.sort_values(by="timestamp").reset_index(drop=True)

    engine = _get_engine()

    rows: list[dict[str, Any]] = []
    labels: list[int] = []
    typologies: list[str] = []

    for _, row in df_raw.iterrows():
        txn_dict = {
            "txn_id": str(row["txn_id"]),
            "timestamp": str(row["timestamp"]),
            "sender_token": str(row["sender_token"]),
            "receiver_token": str(row["receiver_token"]),
            "amount": float(row["amount"]),
            "channel": str(row["channel"]),
            "device_token": str(row["device_token"]),
        }
        features = engine.extract_features(txn_dict)
        rows.append(features)

        is_scam_val = str(row.get("is_scam", "False")).strip().lower() in ("true", "1")
        labels.append(1 if is_scam_val else 0)
        typologies.append(str(row.get("typology", "none")))

    df_feats = pd.DataFrame(rows)[FEATURE_NAMES]
    df_feats["is_scam"] = labels
    df_feats["typology"] = typologies
    return df_feats
