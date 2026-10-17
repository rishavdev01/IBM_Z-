"""Scam typology injectors for Aegis Exchange.

Implements four financial crime scam typologies:
1. mule_fan_in: 8-20 distinct senders pay ONE new mule account (2000-9000 INR) in <=30 min.
2. rapid_forwarding: mule receives from 3-6 senders, then forwards 90-98% to 1-3 downstreams
   within 30-180s.
3. circular_layering: 3-5 accounts pass money A->B->C...->A with 1-3% skim in <=10 min
   (20000-90000 INR).
4. account_takeover: victim appears on NEW device_token, sends 3-6 payments of 5-20x usual
   amount to brand new receivers in <=10 min.

Includes `inject_scenarios()` to merge bursts into a benign stream at a target scam rate (~4%).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Sequence
import uuid
import numpy as np

from simulator.generator import AccountPool, Account, CHANNELS
from simulator.models import Transaction, Truth, ChannelType
from simulator.tokenization import tokenize


def _make_txn_id(rng: np.random.Generator) -> str:
    """Generate deterministic UUIDv4 from RNG bytes."""
    return str(uuid.UUID(bytes=bytes(rng.bytes(16)), version=4))


def mule_fan_in(
    pool: AccountPool,
    t0: datetime,
    rng: np.random.Generator,
) -> list[Transaction]:
    """8-20 different senders pay ONE new mule account 2000-9000 INR each within 30 minutes."""
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    else:
        t0 = t0.astimezone(timezone.utc)

    # 8-20 different senders
    k = int(rng.integers(8, 21))
    sender_indices = rng.choice(pool.n_accounts, size=k, replace=False)

    # ONE new mule account
    mule_raw_id = f"MULE_FANIN_{rng.integers(10000000, 99999999)}"
    mule_token = tokenize(mule_raw_id)

    # Distributed within 30 minutes (0 to 1800 seconds)
    offsets = sorted([float(rng.uniform(0.0, 1800.0)) for _ in range(k)])

    txns: list[Transaction] = []
    truth = Truth(is_scam=True, typology="mule_fan_in")

    for i, sender_idx in enumerate(sender_indices):
        sender = pool.accounts[int(sender_idx)]
        amount = round(float(rng.uniform(2000.0, 9000.0)), 2)
        device_token = str(rng.choice(sender.device_tokens))
        channel: ChannelType = str(rng.choice(CHANNELS, p=sender.channel_probs))  # type: ignore
        ts = t0 + timedelta(seconds=offsets[i])

        txn = Transaction(
            txn_id=_make_txn_id(rng),
            timestamp=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            sender_token=sender.token,
            receiver_token=mule_token,
            amount=amount,
            channel=channel,
            device_token=device_token,
            truth=truth,
        )
        txns.append(txn)

    return txns


def rapid_forwarding(
    pool: AccountPool,
    t0: datetime,
    rng: np.random.Generator,
) -> list[Transaction]:
    """Mule receives from 3-6 senders, forwards 90-98% to 1-3 downstreams in 30-180 seconds."""
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    else:
        t0 = t0.astimezone(timezone.utc)

    num_senders = int(rng.integers(3, 7))
    num_downstreams = int(rng.integers(1, 4))

    chosen_indices = rng.choice(pool.n_accounts, size=num_senders + num_downstreams, replace=False)
    senders = [pool.accounts[int(i)] for i in chosen_indices[:num_senders]]
    downstreams = [pool.accounts[int(i)] for i in chosen_indices[num_senders:]]

    mule_raw = f"MULE_RAPID_{rng.integers(10000000, 99999999)}"
    mule_token = tokenize(mule_raw)
    mule_device_token = tokenize(f"DEV_{mule_raw}")

    txns: list[Transaction] = []
    truth = Truth(is_scam=True, typology="rapid_forwarding")

    # Each sender sends inbound money within first 10 minutes
    inbound_offsets = sorted([float(rng.uniform(0.0, 600.0)) for _ in range(num_senders)])

    for i, sender in enumerate(senders):
        in_amt = round(float(rng.uniform(3000.0, 15000.0)), 2)
        in_ts = t0 + timedelta(seconds=inbound_offsets[i])
        channel: ChannelType = str(rng.choice(CHANNELS, p=sender.channel_probs))  # type: ignore

        # 1. Inbound transaction to mule
        in_txn = Transaction(
            txn_id=_make_txn_id(rng),
            timestamp=in_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            sender_token=sender.token,
            receiver_token=mule_token,
            amount=in_amt,
            channel=channel,
            device_token=str(rng.choice(sender.device_tokens)),
            truth=truth,
        )
        txns.append(in_txn)

        # 2. Outbound forwarding 90-98% within 30-180s
        forward_ratio = float(rng.uniform(0.90, 0.98))
        total_forward = in_amt * forward_ratio
        forward_delay = float(rng.uniform(30.0, 180.0))
        out_ts = in_ts + timedelta(seconds=forward_delay)

        # Forward across downstream accounts
        amt_per_downstream = round(total_forward / num_downstreams, 2)
        for d_acc in downstreams:
            out_txn = Transaction(
                txn_id=_make_txn_id(rng),
                timestamp=out_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                sender_token=mule_token,
                receiver_token=d_acc.token,
                amount=amt_per_downstream,
                channel="IMPS",  # Fast immediate payment
                device_token=mule_device_token,
                truth=truth,
            )
            txns.append(out_txn)

    # Sort by timestamp
    txns.sort(key=lambda x: x.timestamp)
    return txns


def circular_layering(
    pool: AccountPool,
    t0: datetime,
    rng: np.random.Generator,
) -> list[Transaction]:
    """Ring of 3-5 accounts passes money A->B->C...->A with 1-3% skim in <=10 minutes."""
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    else:
        t0 = t0.astimezone(timezone.utc)

    ring_size = int(rng.integers(3, 6))  # 3, 4, or 5 accounts
    node_indices = rng.choice(pool.n_accounts, size=ring_size, replace=False)
    nodes = [pool.accounts[int(i)] for i in node_indices]

    # Large amounts: 20000 - 90000 INR
    current_amount = round(float(rng.uniform(20000.0, 90000.0)), 2)
    truth = Truth(is_scam=True, typology="circular_layering")

    txns: list[Transaction] = []
    # Whole ring finished within 10 minutes (600s), step per hop
    total_time_span = float(rng.uniform(180.0, 540.0))  # between 3 and 9 minutes
    step = total_time_span / ring_size

    for hop in range(ring_size):
        sender = nodes[hop]
        receiver = nodes[(hop + 1) % ring_size]

        hop_ts = t0 + timedelta(seconds=hop * step + float(rng.uniform(0.5, 5.0)))
        channel: ChannelType = "IMPS" if current_amount <= 50000 else "NEFT"

        txn = Transaction(
            txn_id=_make_txn_id(rng),
            timestamp=hop_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            sender_token=sender.token,
            receiver_token=receiver.token,
            amount=round(current_amount, 2),
            channel=channel,
            device_token=str(rng.choice(sender.device_tokens)),
            truth=truth,
        )
        txns.append(txn)

        # 1-3% skim kept by each hop
        skim_rate = float(rng.uniform(0.01, 0.03))
        current_amount = current_amount * (1.0 - skim_rate)

    txns.sort(key=lambda x: x.timestamp)
    return txns


def account_takeover(
    pool: AccountPool,
    t0: datetime,
    rng: np.random.Generator,
) -> list[Transaction]:
    """Victim account appears on NEW device_token, sends 3-6 payments 5-20x usual amount in <=10 min."""
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    else:
        t0 = t0.astimezone(timezone.utc)

    victim_idx = int(rng.integers(0, pool.n_accounts))
    victim = pool.accounts[victim_idx]

    # Brand new device token never seen before with this sender
    new_dev_raw = f"ATO_DEV_{victim.account_id}_{rng.integers(1000000, 9999999)}"
    new_device_token = tokenize(new_dev_raw)

    num_payments = int(rng.integers(3, 7))  # 3 to 6 payments
    # Payments of 5-20x usual amount
    multiplier = float(rng.uniform(5.0, 20.0))
    amount = round(float(victim.typical_median * multiplier), 2)
    # Ensure amount is reasonably high
    if amount < 5000.0:
        amount = round(float(rng.uniform(7000.0, 30000.0)), 2)

    # Brand new receivers (not in victim's counterparty list)
    brand_new_receivers: list[str] = []
    for _ in range(num_payments):
        raw_rec = f"ATO_REC_{rng.integers(10000000, 99999999)}"
        brand_new_receivers.append(tokenize(raw_rec))

    offsets = sorted([float(rng.uniform(0.0, 600.0)) for _ in range(num_payments)])
    truth = Truth(is_scam=True, typology="account_takeover")

    txns: list[Transaction] = []
    for i in range(num_payments):
        ts = t0 + timedelta(seconds=offsets[i])
        txn = Transaction(
            txn_id=_make_txn_id(rng),
            timestamp=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            sender_token=victim.token,
            receiver_token=brand_new_receivers[i],
            amount=amount,
            channel="IMPS",
            device_token=new_device_token,
            truth=truth,
        )
        txns.append(txn)

    return txns


def inject_scenarios(
    benign_txns: Sequence[Transaction],
    pool: AccountPool,
    scam_rate: float = 0.04,
    seed: int = 42,
) -> list[Transaction]:
    """Inject scam scenario bursts into a benign transaction list.

    Merges bursts at random points in the stream such that scam transactions
    account for approximately 3-5% of the total transactions (target scam_rate).
    Result is fully deterministic and sorted strictly by timestamp.

    Args:
        benign_txns: Input list of benign transactions.
        pool: AccountPool used to sample accounts.
        scam_rate: Target scam ratio (default 0.04 = 4%).
        seed: Random seed for reproducibility.

    Returns:
        List of transactions containing benign + injected scam transactions,
        sorted by timestamp.
    """
    if not benign_txns:
        return []

    rng = np.random.default_rng(seed)

    # Calculate target scam count
    n_benign = len(benign_txns)
    target_scam_count = max(1, int(round(n_benign * (scam_rate / (1.0 - scam_rate)))))

    # Parse stream time boundaries
    t_start = datetime.fromisoformat(benign_txns[0].timestamp.replace("Z", "+00:00"))
    t_end = datetime.fromisoformat(benign_txns[-1].timestamp.replace("Z", "+00:00"))
    total_seconds = max(1800.0, (t_end - t_start).total_seconds())

    scam_txns: list[Transaction] = []
    typology_funcs = [mule_fan_in, rapid_forwarding, circular_layering, account_takeover]

    # Generate scenario bursts until target count is reached
    idx = 0
    while len(scam_txns) < target_scam_count:
        func = typology_funcs[idx % len(typology_funcs)]
        idx += 1

        # Pick random start time within the stream window (with buffer)
        offset = float(rng.uniform(60.0, max(61.0, total_seconds - 1800.0)))
        t0 = t_start + timedelta(seconds=offset)

        burst = func(pool, t0, rng)
        scam_txns.extend(burst)

    # Merge and sort
    all_txns = list(benign_txns) + scam_txns
    all_txns.sort(key=lambda t: t.timestamp)
    return all_txns
