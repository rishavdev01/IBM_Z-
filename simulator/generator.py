"""Realistic benign payment generator for Aegis Exchange.

Simulates account pool with realistic topologies (sparse graph via frequent counterparties),
lognormal transaction amounts, channel preferences, and exponential inter-arrival streaming.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Iterator
import uuid
import numpy as np

from simulator.models import Transaction, Truth, ChannelType
from simulator.tokenization import tokenize

CHANNELS: list[ChannelType] = ["UPI", "IMPS", "NEFT"]


@dataclass
class Account:
    """Represents a simulated bank account with behavioural parameters."""

    account_id: str
    token: str
    raw_devices: list[str]
    device_tokens: list[str]
    typical_median: float
    typical_amount_mu: float
    typical_amount_sigma: float
    channel_probs: list[float]  # UPI, IMPS, NEFT
    active_start_hour: int
    active_end_hour: int
    counterparty_indices: list[int] = field(default_factory=list)


class AccountPool:
    """A pool of simulated accounts with realistic banking profiles."""

    def __init__(self, n_accounts: int = 2000, seed: int = 42) -> None:
        self.n_accounts = n_accounts
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.accounts: list[Account] = []
        self._build_pool()

    def _build_pool(self) -> None:
        """Initialize accounts, devices, amounts, and sparse counterparty graph."""
        # Step 1: Create accounts
        for i in range(self.n_accounts):
            raw_id = f"ACC{i:06d}"
            acc_token = tokenize(raw_id)

            # 1-2 devices per account (75% 1 device, 25% 2 devices)
            num_devices = 1 if self.rng.random() < 0.75 else 2
            raw_devices = [f"DEV_{raw_id}_{d}" for d in range(num_devices)]
            dev_tokens = [tokenize(d) for d in raw_devices]

            # Typical amount: lognormal with median around 1500 INR
            # Account median varies around 1500 (e.g. 700 to 3000)
            account_median = float(self.rng.uniform(700.0, 3000.0))
            mu = float(np.log(account_median))
            sigma = float(self.rng.uniform(0.4, 0.7))

            # Channel preference: UPI dominant (60-80%), IMPS (15-30%), NEFT (5-15%)
            alpha = [7.0, 2.0, 1.0]
            raw_weights = self.rng.dirichlet(alpha)
            channel_probs = [float(w) for w in raw_weights]

            # Active hours: typically active 6am-10pm UTC
            start_hour = int(self.rng.integers(5, 9))
            end_hour = int(self.rng.integers(21, 24))

            acc = Account(
                account_id=raw_id,
                token=acc_token,
                raw_devices=raw_devices,
                device_tokens=dev_tokens,
                typical_median=account_median,
                typical_amount_mu=mu,
                typical_amount_sigma=sigma,
                channel_probs=channel_probs,
                active_start_hour=start_hour,
                active_end_hour=end_hour,
            )
            self.accounts.append(acc)

        # Step 2: Assign 5-15 frequent counterparties per account
        for i, acc in enumerate(self.accounts):
            k = int(self.rng.integers(5, 16))
            # Pick k random accounts excluding self
            candidates = self.rng.choice(self.n_accounts, size=k * 2, replace=False)
            counterparties = [int(c) for c in candidates if c != i][:k]
            acc.counterparty_indices = counterparties


def benign_stream(
    pool: AccountPool,
    start: datetime,
    rate_per_sec: float = 20.0,
    seed: int = 42,
) -> Iterator[Transaction]:
    """Generate a continuous deterministic stream of benign transactions.

    Args:
        pool: The AccountPool to sample accounts from.
        start: Starting datetime (UTC).
        rate_per_sec: Mean transaction rate per second.
        seed: Random seed for reproducibility.

    Yields:
        Transaction instances with truth: {is_scam: False, typology: "none"}.
    """
    rng = np.random.default_rng(seed)

    if start.tzinfo is None:
        current_time = start.replace(tzinfo=timezone.utc)
    else:
        current_time = start.astimezone(timezone.utc)

    truth_benign = Truth(is_scam=False, typology="none")

    while True:
        # Exponential inter-arrival time
        dt_sec = float(rng.exponential(1.0 / rate_per_sec))
        # Ensure strictly positive step (minimum 1 millisecond)
        if dt_sec < 0.001:
            dt_sec = 0.001
        current_time += timedelta(seconds=dt_sec)

        # Sender selection
        sender_idx = int(rng.integers(0, pool.n_accounts))
        sender = pool.accounts[sender_idx]

        # Receiver selection: 90% to frequent counterparties, 10% to random
        if rng.random() < 0.90 and sender.counterparty_indices:
            receiver_idx = int(rng.choice(sender.counterparty_indices))
        else:
            receiver_idx = int(rng.integers(0, pool.n_accounts))
            while receiver_idx == sender_idx:
                receiver_idx = int(rng.integers(0, pool.n_accounts))
        receiver = pool.accounts[receiver_idx]

        # Device selection from sender's devices
        device_token = str(rng.choice(sender.device_tokens))

        # Channel selection from sender's distribution
        channel: ChannelType = str(rng.choice(CHANNELS, p=sender.channel_probs))  # type: ignore

        # Amount generation: mostly 50-5000 INR with rare spikes up to 50000
        if rng.random() < 0.01:
            # 1% rare high-value spike
            amt = float(rng.uniform(10000.0, 50000.0))
        else:
            sampled_amt = float(rng.lognormal(sender.typical_amount_mu, sender.typical_amount_sigma))
            amt = float(np.clip(sampled_amt, 50.0, 5000.0))
        amount = round(amt, 2)

        # Deterministic UUID from RNG bytes
        uuid_bytes = bytes(rng.bytes(16))
        txn_id = str(uuid.UUID(bytes=uuid_bytes, version=4))

        ts_str = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        txn = Transaction(
            txn_id=txn_id,
            timestamp=ts_str,
            sender_token=sender.token,
            receiver_token=receiver.token,
            amount=amount,
            channel=channel,
            device_token=device_token,
            truth=truth_benign,
        )
        yield txn
