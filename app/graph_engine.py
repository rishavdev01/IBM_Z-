"""In-memory transaction graph adapter for the shared Aegis feature contract.

This platform-owned implementation fills the missing Part 2 graph-engine
boundary. It computes features before adding each transaction to the graph.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

import networkx as nx


class GraphEngine:
    """Maintain a rolling directed transaction graph and extract contract features."""

    def __init__(self, window_hours: int = 24) -> None:
        self.window = timedelta(hours=window_hours)
        self._edges: deque[dict[str, Any]] = deque()
        self._lock = RLock()

    @staticmethod
    def _time(txn: dict[str, Any]) -> datetime:
        value = str(txn["timestamp"]).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.window
        self._edges = deque(edge for edge in self._edges if edge["_dt"] >= cutoff)

    def extract_features(self, txn: dict[str, Any]) -> dict[str, float]:
        """Compute all 16 contract features, then append the transaction edge."""
        now = self._time(txn)
        sender, receiver = str(txn["sender_token"]), str(txn["receiver_token"])
        amount, device = float(txn["amount"]), str(txn["device_token"])
        channel_codes = {"UPI": 0.0, "IMPS": 1.0, "NEFT": 2.0}
        with self._lock:
            self._prune(now)
            existing = next((edge for edge in self._edges if edge["txn_id"] == str(txn["txn_id"])), None)
            if existing is not None:
                return dict(existing["features"])
            history = [edge for edge in self._edges if edge["_dt"] <= now]
            outgoing = [e for e in history if e["sender"] == sender]
            inbound_to_receiver = [e for e in history if e["receiver"] == receiver]
            outbound_from_receiver = [e for e in history if e["sender"] == receiver]
            inbound_to_sender = [e for e in history if e["receiver"] == sender]
            receiver_in = sum(e["amount"] for e in inbound_to_receiver)
            receiver_out = sum(e["amount"] for e in outbound_from_receiver)
            sender_in = sum(e["amount"] for e in inbound_to_sender)
            sender_avg = (sum(e["amount"] for e in outgoing) / len(outgoing)) if outgoing else 0.0
            forward_gaps = [
                (out["_dt"] - inc["_dt"]).total_seconds()
                for inc in inbound_to_receiver
                for out in outbound_from_receiver
                if out["_dt"] >= inc["_dt"]
            ]
            prior_graph = nx.DiGraph()
            prior_graph.add_edges_from((e["sender"], e["receiver"]) for e in history)
            cycle_length = 0
            if sender == receiver:
                cycle_length = 1
            elif receiver in prior_graph and sender in prior_graph and nx.has_path(prior_graph, receiver, sender):
                path = nx.shortest_path(prior_graph, receiver, sender)
                if len(path) <= 5:
                    cycle_length = len(path)
            last_inbound = max((e["_dt"] for e in inbound_to_sender), default=None)
            seen_device = any(e["sender"] == sender and e["device"] == device for e in history)
            features = {
                "amount": amount,
                "hour_of_day": float(now.hour),
                "channel_code": channel_codes.get(str(txn["channel"]), 0.0),
                "sender_out_degree_24h": float(len({e["receiver"] for e in outgoing})),
                "sender_txn_count_1h": float(sum(e["_dt"] >= now - timedelta(hours=1) for e in outgoing)),
                "receiver_in_degree_24h": float(len({e["sender"] for e in inbound_to_receiver})),
                "receiver_in_amount_24h": receiver_in,
                "receiver_out_amount_24h": receiver_out,
                "receiver_forward_ratio": receiver_out / receiver_in if receiver_in else 0.0,
                "receiver_min_forward_seconds": min(forward_gaps) if forward_gaps else -1.0,
                "in_cycle": float(bool(cycle_length)),
                "cycle_length": float(cycle_length),
                "sender_seconds_since_last_inbound": (now - last_inbound).total_seconds() if last_inbound else -1.0,
                "sender_forward_ratio": (sum(e["amount"] for e in outgoing) + amount) / sender_in if sender_in else 0.0,
                "sender_new_device": float(not seen_device),
                "amount_vs_sender_avg": amount / sender_avg if sender_avg else 1.0,
            }
            self._edges.append({"sender": sender, "receiver": receiver, "amount": amount,
                                "timestamp": str(txn["timestamp"]), "txn_id": str(txn["txn_id"]),
                                "device": device, "_dt": now, "features": dict(features)})
            return features

    def restore_transactions(self, transactions: list[dict[str, Any]]) -> None:
        """Rebuild rolling graph state from persisted transactions in time order."""
        for txn in sorted(transactions, key=lambda item: self._time(item)):
            self.extract_features(txn)

    def neighborhood(self, token: str, hops: int = 2) -> dict[str, list[dict[str, Any]]]:
        """Return the directed graph neighborhood and contract edge fields."""
        with self._lock:
            graph = nx.DiGraph()
            now = datetime.now(timezone.utc)
            self._prune(now)
            graph.add_edges_from((e["sender"], e["receiver"]) for e in self._edges)
            if token not in graph:
                return {"nodes": [], "edges": []}
            visited = {token}
            frontier = {token}
            for _ in range(hops):
                frontier = set().union(*(set(graph.predecessors(n)) | set(graph.successors(n)) for n in frontier)) - visited
                visited |= frontier
            nodes = [{"id": n, "in_degree": int(graph.in_degree(n)), "out_degree": int(graph.out_degree(n))}
                     for n in sorted(visited)]
            edges = [{k: e[k] for k in ("source", "target", "amount", "timestamp", "txn_id")}
                     for e in ({"source": e["sender"], "target": e["receiver"], **e} for e in self._edges)
                     if e["source"] in visited and e["target"] in visited]
            return {"nodes": nodes, "edges": edges}

\n