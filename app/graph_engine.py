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
            sender_out_degree: set[str] = set()
            sender_txn_count_1h = 0
            sender_out_amount = 0.0
            prior_sender_count = 0
            receiver_in_senders: set[str] = set()
            receiver_in = receiver_out = sender_in = 0.0
            inbound_to_receiver: list[datetime] = []
            outbound_from_receiver: list[datetime] = []
            inbound_to_sender: list[datetime] = []
            seen_device = False
            adjacency: dict[str, set[str]] = {}
            for edge in self._edges:
                if edge["_dt"] > now:
                    continue
                edge_sender, edge_receiver = edge["sender"], edge["receiver"]
                edge_time, edge_amount = edge["_dt"], edge["amount"]
                adjacency.setdefault(edge_sender, set()).add(edge_receiver)
                if edge_sender == sender:
                    sender_out_degree.add(edge_receiver)
                    sender_out_amount += edge_amount
                    prior_sender_count += 1
                    sender_txn_count_1h += edge_time >= now - timedelta(hours=1)
                    seen_device = seen_device or edge["device"] == device
                if edge_receiver == receiver:
                    receiver_in_senders.add(edge_sender)
                    receiver_in += edge_amount
                    inbound_to_receiver.append(edge_time)
                if edge_sender == receiver:
                    receiver_out += edge_amount
                    outbound_from_receiver.append(edge_time)
                if edge_receiver == sender:
                    sender_in += edge_amount
                    inbound_to_sender.append(edge_time)
            # Average by prior transfer count, not distinct receiver count.
            sender_avg = sender_out_amount / prior_sender_count if prior_sender_count else 0.0
            forward_gaps: list[float] = []
            inbound_to_receiver.sort()
            outbound_from_receiver.sort()
            next_outbound = 0
            for inbound_time in inbound_to_receiver:
                while next_outbound < len(outbound_from_receiver) and outbound_from_receiver[next_outbound] < inbound_time:
                    next_outbound += 1
                if next_outbound < len(outbound_from_receiver):
                    forward_gaps.append((outbound_from_receiver[next_outbound] - inbound_time).total_seconds())
            cycle_length = 0
            if sender == receiver:
                cycle_length = 1
            else:
                # Only paths with at most four edges qualify (path length <= 5 nodes).
                frontier = [receiver]
                visited = {receiver}
                for depth in range(1, 5):
                    next_frontier: list[str] = []
                    for node in frontier:
                        for neighbor in adjacency.get(node, ()):
                            if neighbor == sender:
                                cycle_length = depth + 1
                                break
                            if neighbor not in visited:
                                visited.add(neighbor)
                                next_frontier.append(neighbor)
                        if cycle_length:
                            break
                    if cycle_length or not next_frontier:
                        break
                    frontier = next_frontier
            last_inbound = max(inbound_to_sender, default=None)
            features = {
                "amount": amount,
                "hour_of_day": float(now.hour),
                "channel_code": channel_codes.get(str(txn["channel"]), 0.0),
                "sender_out_degree_24h": float(len(sender_out_degree)),
                "sender_txn_count_1h": float(sender_txn_count_1h),
                "receiver_in_degree_24h": float(len(receiver_in_senders)),
                "receiver_in_amount_24h": receiver_in,
                "receiver_out_amount_24h": receiver_out,
                "receiver_forward_ratio": receiver_out / receiver_in if receiver_in else 0.0,
                "receiver_min_forward_seconds": min(forward_gaps) if forward_gaps else -1.0,
                "in_cycle": float(bool(cycle_length)),
                "cycle_length": float(cycle_length),
                "sender_seconds_since_last_inbound": (now - last_inbound).total_seconds() if last_inbound else -1.0,
                "sender_forward_ratio": (sender_out_amount + amount) / sender_in if sender_in else 0.0,
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
