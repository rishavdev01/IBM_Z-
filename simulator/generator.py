"""Deterministic benign UPI/IMPS/NEFT traffic."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

import numpy as np

from simulator.models import Channel, Transaction, Truth
from simulator.tokenization import tokenize

CHANNELS: tuple[Channel, Channel, Channel] = ("UPI", "IMPS", "NEFT")


@dataclass
class Account:
    """One synthetic account in the pool."""

    raw_id: str
    devices: list[str]
    typical_amount: float
    channel_p: np.ndarray
    active_hours: set[int]
    counterparties: list[str] = field(default_factory=list)


class AccountPool:
    """Fixed set of accounts with devices, counterparties, and amount habits."""

    def __init__(self, n_accounts: int = 2000, seed: int = 42) -> None:
        self.n_accounts = n_accounts
        self.seed = seed
        rng = np.random.default_rng(seed)
        self.accounts: list[Account] = []
        for i in range(n_accounts):
            raw_id = f"ACC{i:06d}"
            n_devices = int(rng.integers(1, 3))
            devices = [f"DEV{i:06d}_{d}" for d in range(n_devices)]
            # lognormal with median ~1500 INR: median = exp(mu)
            typical = float(np.clip(rng.lognormal(mean=np.log(1500.0), sigma=0.55), 80.0, 25000.0))
            weights = rng.dirichlet([6.0, 2.5, 1.5])
            n_hours = int(rng.integers(8, 17))
            hours = set(int(h) for h in rng.choice(24, size=n_hours, replace=False))
            self.accounts.append(
                Account(
                    raw_id=raw_id,
                    devices=devices,
                    typical_amount=typical,
                    channel_p=weights,
                    active_hours=hours,
                )
            )
        for i, acct in enumerate(self.accounts):
            n_cp = int(rng.integers(5, 16))
            others = [j for j in range(n_accounts) if j != i]
            chosen = rng.choice(others, size=min(n_cp, len(others)), replace=False)
            acct.counterparties = [self.accounts[int(j)].raw_id for j in chosen]

    def by_raw_id(self, raw_id: str) -> Account | None:
        """Look up an account, or None if it is not in the pool."""
        if raw_id.startswith("ACC") and raw_id[3:].isdigit():
            idx = int(raw_id[3:])
            if 0 <= idx < len(self.accounts) and self.accounts[idx].raw_id == raw_id:
                return self.accounts[idx]
        for acct in self.accounts:
            if acct.raw_id == raw_id:
                return acct
        return None

    def random_account(self, rng: np.random.Generator, exclude: str | None = None) -> Account:
        """Pick a uniform random pool account, optionally excluding one raw id."""
        idx = int(rng.integers(0, len(self.accounts)))
        acct = self.accounts[idx]
        if exclude is not None and acct.raw_id == exclude:
            idx = (idx + 1) % len(self.accounts)
            acct = self.accounts[idx]
        return acct


def uuid4_from_rng(rng: np.random.Generator) -> str:
    """Deterministic UUID4 from a numpy Generator."""
    return str(UUID(bytes=bytes(rng.bytes(16)), version=4))


def format_ts(dt: datetime) -> str:
    """Format a timezone-aware datetime as ISO-8601 UTC with a Z suffix."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def pick_channel(rng: np.random.Generator, acct: Account) -> Channel:
    """Sample a channel from the account's preferred mix."""
    idx = int(rng.choice(3, p=acct.channel_p))
    return CHANNELS[idx]


def pick_amount(rng: np.random.Generator, typical: float) -> float:
    """Mostly 50-5000 INR around the account typical, with rare spikes to 50000."""
    if rng.random() < 0.02:
        return float(rng.uniform(8000.0, 50000.0))
    draw = float(rng.lognormal(mean=np.log(typical), sigma=0.45))
    return float(np.clip(draw, 50.0, 5000.0))


def make_transaction(
    *,
    rng: np.random.Generator,
    sender_raw: str,
    receiver_raw: str,
    device_raw: str,
    amount: float,
    channel: Channel,
    when: datetime,
    is_scam: bool,
    typology: str,
    memo: str | None = None,
) -> Transaction:
    """Build a contract-shaped Transaction with tokens and ground truth."""
    return Transaction(
        txn_id=uuid4_from_rng(rng),
        timestamp=format_ts(when),
        sender_token=tokenize(sender_raw),
        receiver_token=tokenize(receiver_raw),
        amount=round(float(amount), 2),
        channel=channel,
        device_token=tokenize(device_raw),
        memo=memo,
        truth=Truth(is_scam=is_scam, typology=typology),  # type: ignore[arg-type]
    )


def _active_accounts(pool: AccountPool, hour: int) -> list[Account]:
    active = [a for a in pool.accounts if hour in a.active_hours]
    return active if active else list(pool.accounts)


def benign_stream(
    pool: AccountPool,
    start: datetime,
    rate_per_sec: float,
    seed: int,
) -> Iterator[Transaction]:
    """Infinite (until stopped) benign payments with exponential inter-arrivals.

    Fully deterministic for a given ``(pool.seed, seed, start, rate_per_sec)``.
    Every transaction has ``truth {is_scam: false, typology: "none"}``.
    """
    rng = np.random.default_rng(seed)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    t = start
    mean_gap = 1.0 / rate_per_sec if rate_per_sec > 0 else 1.0
    while True:
        gap = float(rng.exponential(mean_gap))
        t = t + timedelta(seconds=max(gap, 1e-4))
        hour = t.astimezone(timezone.utc).hour
        active = _active_accounts(pool, hour)
        sender = active[int(rng.integers(0, len(active)))]
        if rng.random() < 0.90 and sender.counterparties:
            recv_raw = sender.counterparties[int(rng.integers(0, len(sender.counterparties)))]
        else:
            recv_raw = pool.random_account(rng, exclude=sender.raw_id).raw_id
        device_raw = sender.devices[int(rng.integers(0, len(sender.devices)))]
        yield make_transaction(
            rng=rng,
            sender_raw=sender.raw_id,
            receiver_raw=recv_raw,
            device_raw=device_raw,
            amount=pick_amount(rng, sender.typical_amount),
            channel=pick_channel(rng, sender),
            when=t,
            is_scam=False,
            typology="none",
        )


def take_benign(
    pool: AccountPool,
    start: datetime,
    n: int,
    rate_per_sec: float,
    seed: int,
) -> list[Transaction]:
    """Return the first ``n`` benign transactions from the stream."""
    out: list[Transaction] = []
    for txn in benign_stream(pool, start, rate_per_sec, seed):
        out.append(txn)
        if len(out) >= n:
            break
    return out
