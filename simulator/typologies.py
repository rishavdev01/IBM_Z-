"""Four scam injectors plus a deterministic merger into a benign stream."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from simulator.generator import AccountPool, make_transaction, pick_channel
from simulator.models import Transaction


def _aware(t0: datetime) -> datetime:
    if t0.tzinfo is None:
        return t0.replace(tzinfo=timezone.utc)
    return t0.astimezone(timezone.utc)


def _new_raw(rng: np.random.Generator, prefix: str) -> str:
    return f"{prefix}{int(rng.integers(0, 1_000_000_000)):09d}"


def mule_fan_in(pool: AccountPool, t0: datetime, rng: np.random.Generator) -> list[Transaction]:
    """8-20 senders pay one new mule 2000-9000 INR each within 30 minutes."""
    t0 = _aware(t0)
    n_senders = int(rng.integers(8, 21))
    mule_raw = _new_raw(rng, "MULE")
    unique_senders = []
    used: set[str] = set()
    while len(unique_senders) < n_senders:
        acct = pool.random_account(rng)
        if acct.raw_id in used:
            continue
        used.add(acct.raw_id)
        unique_senders.append(acct)
    txns: list[Transaction] = []
    for acct in unique_senders:
        when = t0 + timedelta(seconds=int(rng.integers(0, 30 * 60)))
        amount = float(rng.uniform(2000.0, 9000.0))
        txns.append(
            make_transaction(
                rng=rng,
                sender_raw=acct.raw_id,
                receiver_raw=mule_raw,
                device_raw=str(rng.choice(acct.devices)),
                amount=amount,
                channel=pick_channel(rng, acct),
                when=when,
                is_scam=True,
                typology="mule_fan_in",
            )
        )
    return sorted(txns, key=lambda t: t.timestamp)


def rapid_forwarding(pool: AccountPool, t0: datetime, rng: np.random.Generator) -> list[Transaction]:
    """Mule receives from 3-6 senders, then forwards 90-98% onward within 30-180s."""
    t0 = _aware(t0)
    n_in = int(rng.integers(3, 7))
    mule_raw = _new_raw(rng, "RFWD")
    mule_dev = _new_raw(rng, "RDEV")
    n_down = int(rng.integers(1, 4))
    downs = [_new_raw(rng, "DNST") for _ in range(n_down)]
    txns: list[Transaction] = []
    used: set[str] = set()
    for i in range(n_in):
        sender = pool.random_account(rng)
        tries = 0
        while sender.raw_id in used and tries < 10:
            sender = pool.random_account(rng, exclude=sender.raw_id)
            tries += 1
        used.add(sender.raw_id)
        t_in = t0 + timedelta(seconds=int(rng.integers(0, 120)) + i * 15)
        inbound = float(rng.uniform(3000.0, 20000.0))
        txns.append(
            make_transaction(
                rng=rng,
                sender_raw=sender.raw_id,
                receiver_raw=mule_raw,
                device_raw=str(rng.choice(sender.devices)),
                amount=inbound,
                channel=pick_channel(rng, sender),
                when=t_in,
                is_scam=True,
                typology="rapid_forwarding",
            )
        )
        fwd_total = inbound * float(rng.uniform(0.90, 0.98))
        shares = rng.dirichlet(np.ones(n_down))
        t_out = t_in + timedelta(seconds=int(rng.integers(30, 181)))
        for share, dest in zip(shares, downs, strict=True):
            txns.append(
                make_transaction(
                    rng=rng,
                    sender_raw=mule_raw,
                    receiver_raw=dest,
                    device_raw=mule_dev,
                    amount=float(share) * fwd_total,
                    channel="IMPS",
                    when=t_out + timedelta(milliseconds=int(rng.integers(1, 200))),
                    is_scam=True,
                    typology="rapid_forwarding",
                )
            )
    return sorted(txns, key=lambda t: t.timestamp)


def circular_layering(pool: AccountPool, t0: datetime, rng: np.random.Generator) -> list[Transaction]:
    """Ring of 3-5 accounts; 1-3% skim per hop; finishes within 10 minutes."""
    t0 = _aware(t0)
    k = int(rng.integers(3, 6))
    ring = [_new_raw(rng, "RING") for _ in range(k)]
    devices = [_new_raw(rng, "RGDV") for _ in range(k)]
    amount = float(rng.uniform(20000.0, 90000.0))
    total_span = 10 * 60
    step = total_span / k
    txns: list[Transaction] = []
    for i in range(k):
        skim = float(rng.uniform(0.01, 0.03))
        when = t0 + timedelta(seconds=step * i + float(rng.uniform(0, max(step * 0.2, 0.5))))
        sender = pool.by_raw_id(ring[i])  # ring accounts are new; channel mix default UPI
        channel = pick_channel(rng, pool.accounts[i % len(pool.accounts)])
        _ = sender
        txns.append(
            make_transaction(
                rng=rng,
                sender_raw=ring[i],
                receiver_raw=ring[(i + 1) % k],
                device_raw=devices[i],
                amount=amount,
                channel=channel,
                when=when,
                is_scam=True,
                typology="circular_layering",
            )
        )
        amount = amount * (1.0 - skim)
    return sorted(txns, key=lambda t: t.timestamp)


def account_takeover(pool: AccountPool, t0: datetime, rng: np.random.Generator) -> list[Transaction]:
    """Victim account on a new device sends 3-6 large payments to new receivers."""
    t0 = _aware(t0)
    victim = pool.random_account(rng)
    new_device = _new_raw(rng, "TODEV")
    n_pay = int(rng.integers(3, 7))
    txns: list[Transaction] = []
    for i in range(n_pay):
        dest = _new_raw(rng, "TORECV")
        when = t0 + timedelta(seconds=int(rng.integers(0, 10 * 60)))
        amount = victim.typical_amount * float(rng.uniform(5.0, 20.0))
        txns.append(
            make_transaction(
                rng=rng,
                sender_raw=victim.raw_id,
                receiver_raw=dest,
                device_raw=new_device,
                amount=amount,
                channel=pick_channel(rng, victim),
                when=when,
                is_scam=True,
                typology="account_takeover",
            )
        )
    return sorted(txns, key=lambda t: t.timestamp)


TYPOLOGY_FNS = {
    "mule_fan_in": mule_fan_in,
    "rapid_forwarding": rapid_forwarding,
    "circular_layering": circular_layering,
    "account_takeover": account_takeover,
}


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _shift(txns: list[Transaction], new_start: datetime) -> list[Transaction]:
    if not txns:
        return []
    origin = _parse_ts(txns[0].timestamp)
    shifted: list[Transaction] = []
    for txn in txns:
        delta = _parse_ts(txn.timestamp) - origin
        payload = txn.to_payload()
        payload["timestamp"] = (new_start + delta).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        shifted.append(Transaction.model_validate(payload))
    return shifted


def inject_scenarios(
    benign_txns: list[Transaction],
    pool: AccountPool,
    scam_rate: float = 0.04,
    seed: int = 42,
) -> list[Transaction]:
    """Merge scam bursts into ``benign_txns`` so scams are about 3-5% of the mix."""
    rng = np.random.default_rng(seed)
    if not benign_txns:
        return []
    names = list(TYPOLOGY_FNS.keys())
    n_benign = len(benign_txns)
    # scam / (benign + scam) ≈ scam_rate  =>  scam ≈ rate / (1-rate) * benign
    needed = max(1, int(round((scam_rate / max(1.0 - scam_rate, 1e-6)) * n_benign)))
    t_min = _parse_ts(benign_txns[0].timestamp)
    t_max = _parse_ts(benign_txns[-1].timestamp)
    span = max((t_max - t_min).total_seconds(), 1.0)
    scams: list[Transaction] = []
    guard = 0
    while len(scams) < needed and guard < 500:
        guard += 1
        name = str(rng.choice(names))
        offset = float(rng.uniform(0.0, span))
        t0 = t_min + timedelta(seconds=offset)
        burst = TYPOLOGY_FNS[name](pool, t0, rng)
        if len(scams) + len(burst) > needed * 1.8 and scams:
            break
        scams.extend(_shift(burst, t0) if burst else burst)
        # _shift is relative to burst[0], which is already near t0; still fine
    merged = list(benign_txns) + scams
    merged.sort(key=lambda t: t.timestamp)
    return merged
