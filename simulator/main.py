"""CLI for Part 1: Simulator.

Supports three commands:
- stream: Replays synthetic transactions into the API in real time.
- dataset: Exports 50,000 labeled transactions to CSV for ML training.
- burst: Injects an immediate scam burst to trigger a live demo alert.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone, timedelta
import os
import sys
import time
from typing import Any
import httpx
import numpy as np

from simulator.generator import AccountPool, benign_stream
from simulator.models import Transaction
from simulator.typologies import (
    account_takeover,
    circular_layering,
    inject_scenarios,
    mule_fan_in,
    rapid_forwarding,
)


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    sem: asyncio.Semaphore,
    retries: int = 2,
) -> dict[str, Any] | None:
    """POST transaction with semaphore concurrency limit and up to 2 retries."""
    for attempt in range(retries + 1):
        try:
            async with sem:
                resp = await client.post(url, json=payload, timeout=5.0)
                if resp.status_code in (200, 201):
                    return resp.json()
        except Exception:
            if attempt == retries:
                return None
            await asyncio.sleep(0.1 * (2**attempt))
    return None


async def run_stream(
    api_url: str,
    rate: float = 20.0,
    duration: float = 300.0,
    scam_rate: float = 0.04,
    seed: int = 42,
) -> None:
    """Stream transactions to the API at the configured rate and print live metrics."""
    print("=" * 70)
    print("Aegis Exchange - Transaction Stream Client")
    print(f"API Target:    {api_url}/v1/transactions")
    print(f"Send Rate:     {rate:.1f} txns/sec")
    print(f"Duration:      {duration:.0f} seconds")
    print(f"Scam Rate:     {scam_rate * 100:.1f}%")
    print(f"Random Seed:   {seed}")
    print("=" * 70)

    endpoint = f"{api_url.rstrip('/')}/v1/transactions"
    sem = asyncio.Semaphore(20)

    # Pre-generate transactions for duration + buffer
    target_count = int(rate * duration * 1.1) + 200
    pool = AccountPool(n_accounts=2000, seed=seed)
    start_time = datetime.now(timezone.utc)
    b_stream = benign_stream(pool, start=start_time, rate_per_sec=rate, seed=seed)

    n_benign = max(100, int(target_count * (1.0 - scam_rate)))
    benign_txns = [next(b_stream) for _ in range(n_benign)]
    all_txns = inject_scenarios(benign_txns, pool, scam_rate=scam_rate, seed=seed)

    # Metrics state
    total_sent = 0
    total_errors = 0
    total_flagged = 0
    tp = 0
    fp = 0
    fn = 0
    tn = 0

    stop_event = asyncio.Event()

    async def report_loop() -> None:
        start_t = time.time()
        while not stop_event.is_set():
            await asyncio.sleep(5.0)
            elapsed = time.time() - start_t
            precision = (tp / (tp + fp) * 100) if (tp + fp) > 0 else 0.0
            recall = (tp / (tp + fn) * 100) if (tp + fn) > 0 else 0.0
            print(
                f"[{elapsed:05.1f}s] Sent: {total_sent:5d} | "
                f"Errors: {total_errors:3d} | "
                f"Flagged: {total_flagged:4d} | "
                f"Precision: {precision:5.1f}% | "
                f"Recall: {recall:5.1f}%"
            )

    async def worker(client: httpx.AsyncClient, txn: Transaction) -> None:
        nonlocal total_sent, total_errors, total_flagged, tp, fp, fn, tn
        payload = txn.to_payload()
        res = await _post_with_retry(client, endpoint, payload, sem, retries=2)
        total_sent += 1

        is_scam_truth = txn.truth.is_scam if txn.truth else False

        if res is None:
            total_errors += 1
            return

        risk_level = res.get("risk_level", "LOW")
        is_flagged = risk_level in ("MEDIUM", "HIGH")

        if is_flagged:
            total_flagged += 1

        if is_flagged and is_scam_truth:
            tp += 1
        elif is_flagged and not is_scam_truth:
            fp += 1
        elif not is_flagged and is_scam_truth:
            fn += 1
        else:
            tn += 1

    reporter_task = asyncio.create_task(report_loop())
    interval = 1.0 / rate
    tasks: list[asyncio.Task] = []

    start_wall = time.time()
    try:
        async with httpx.AsyncClient() as client:
            for txn in all_txns:
                if time.time() - start_wall >= duration:
                    break

                t = asyncio.create_task(worker(client, txn))
                tasks.append(t)

                # Real-time pacing
                await asyncio.sleep(interval)

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[!] Stream interrupted by user.")
    finally:
        stop_event.set()
        reporter_task.cancel()
        elapsed = time.time() - start_wall
        precision = (tp / (tp + fp) * 100) if (tp + fp) > 0 else 0.0
        recall = (tp / (tp + fn) * 100) if (tp + fn) > 0 else 0.0
        print("\n" + "=" * 70)
        print("Final Stream Summary:")
        print(f"Elapsed Time:    {elapsed:.2f} seconds")
        print(f"Total Sent:      {total_sent}")
        print(f"Errors:          {total_errors}")
        print(f"Flagged Alerts:  {total_flagged}")
        print(f"Live Precision:  {precision:.2f}% (TP={tp}, FP={fp})")
        print(f"Live Recall:     {recall:.2f}% (TP={tp}, FN={fn})")
        print("=" * 70)


def run_dataset(n: int = 50000, seed: int = 42, out_path: str = "data/transactions_labeled.csv") -> None:
    """Generate n labeled transactions and export to CSV sorted by timestamp."""
    print("=" * 70)
    print(f"Generating synthetic dataset: {n} transactions (seed={seed})")
    print(f"Output path: {out_path}")
    print("=" * 70)

    # 4% scam rate
    scam_rate = 0.04
    pool = AccountPool(n_accounts=2000, seed=seed)
    start_time = datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc)
    b_stream = benign_stream(pool, start=start_time, rate_per_sec=20.0, seed=seed)

    # Compute approximate benign count to reach n after injection
    n_benign = max(100, int(n * (1.0 - scam_rate)))
    benign_txns = [next(b_stream) for _ in range(n_benign)]

    all_txns = inject_scenarios(benign_txns, pool, scam_rate=scam_rate, seed=seed)

    # Ensure exactly n rows
    if len(all_txns) > n:
        all_txns = all_txns[:n]
    elif len(all_txns) < n:
        needed = n - len(all_txns)
        extra_benign = [next(b_stream) for _ in range(needed)]
        all_txns.extend(extra_benign)
        all_txns.sort(key=lambda t: t.timestamp)

    # Ensure output dir exists
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    counts: dict[str, int] = {"none": 0}
    scam_count = 0

    fieldnames = [
        "txn_id",
        "timestamp",
        "sender_token",
        "receiver_token",
        "amount",
        "channel",
        "device_token",
        "memo",
        "is_scam",
        "typology",
    ]

    with open(out_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for txn in all_txns:
            is_scam = txn.truth.is_scam if txn.truth else False
            typology = txn.truth.typology if txn.truth else "none"

            counts[typology] = counts.get(typology, 0) + 1
            if is_scam:
                scam_count += 1

            writer.writerow(
                {
                    "txn_id": txn.txn_id,
                    "timestamp": txn.timestamp,
                    "sender_token": txn.sender_token,
                    "receiver_token": txn.receiver_token,
                    "amount": f"{txn.amount:.2f}",
                    "channel": txn.channel,
                    "device_token": txn.device_token,
                    "memo": txn.memo or "",
                    "is_scam": "True" if is_scam else "False",
                    "typology": typology,
                }
            )

    benign_count = n - scam_count
    print(f"Successfully generated {len(all_txns)} transactions.")
    print("\nClass Balance:")
    print(f"  Total:      {n:,}")
    print(f"  Benign:     {benign_count:,} ({benign_count / n * 100:.2f}%)")
    print(f"  Scam:       {scam_count:,} ({scam_count / n * 100:.2f}%)")
    print("\nTypology Breakdown:")
    for typ, c in sorted(counts.items()):
        print(f"  - {typ:<20}: {c:5d} ({c / n * 100:.2f}%)")
    print(f"\nSaved to: {os.path.abspath(out_path)}")


def run_burst(
    typology: str,
    api_url: str = "http://localhost:8000",
) -> None:
    """Post ONE immediate scam burst scenario to trigger a live demo alert."""
    print("=" * 70)
    print(f"Aegis Exchange - Injecting Live Scam Burst: '{typology}'")
    print(f"Target API: {api_url}/v1/transactions")
    print("=" * 70)

    pool = AccountPool(n_accounts=500, seed=int(time.time() * 1000) % 100000)
    rng = np.random.default_rng()
    t0 = datetime.now(timezone.utc)

    if typology == "mule_fan_in":
        txns = mule_fan_in(pool, t0, rng)
    elif typology == "rapid_forwarding":
        txns = rapid_forwarding(pool, t0, rng)
    elif typology == "circular_layering":
        txns = circular_layering(pool, t0, rng)
    elif typology == "account_takeover":
        txns = account_takeover(pool, t0, rng)
    else:
        print(f"[!] Unknown typology: {typology}")
        sys.exit(1)

    print(f"Generated {len(txns)} transactions for burst scenario '{typology}'.")
    endpoint = f"{api_url.rstrip('/')}/v1/transactions"

    alerts_triggered = 0
    with httpx.Client(timeout=10.0) as client:
        for i, txn in enumerate(txns, start=1):
            payload = txn.to_payload()
            try:
                resp = client.post(endpoint, json=payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    risk = data.get("risk_level", "UNKNOWN")
                    score = data.get("risk_score", 0.0)
                    alert_id = data.get("alert_id")
                    if alert_id:
                        alerts_triggered += 1
                    print(
                        f"  [{i}/{len(txns)}] Sent {txn.amount:>8.2f} INR | "
                        f"{txn.sender_token[:6]}... -> {txn.receiver_token[:6]}... | "
                        f"Risk: {risk:<6} ({score:.4f}) | Alert ID: {alert_id}"
                    )
                else:
                    print(f"  [{i}/{len(txns)}] Error {resp.status_code}: {resp.text}")
            except Exception as e:
                print(f"  [{i}/{len(txns)}] Request failed: {e}")

    print("\n" + "=" * 70)
    print(f"Burst Injection Complete: {len(txns)} sent, {alerts_triggered} alert(s) triggered.")
    print("=" * 70)


def main() -> None:
    """CLI entrypoint."""
    default_api_url = os.getenv("AEGIS_API_URL", "http://localhost:8000")

    parser = argparse.ArgumentParser(
        prog="python -m simulator.main",
        description="Aegis Exchange - Part 1 Simulation CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: stream
    p_stream = subparsers.add_parser("stream", help="Stream transactions to API in real-time")
    p_stream.add_argument("--api-url", default=default_api_url, help=f"API base URL (default: {default_api_url})")
    p_stream.add_argument("--rate", type=float, default=20.0, help="Send rate in txns/sec (default: 20)")
    p_stream.add_argument("--duration", type=float, default=300.0, help="Stream duration in seconds (default: 300)")
    p_stream.add_argument("--scam-rate", type=float, default=0.04, help="Target scam fraction (default: 0.04)")
    p_stream.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")

    # Subcommand: dataset
    p_dataset = subparsers.add_parser("dataset", help="Generate labeled CSV dataset for training")
    p_dataset.add_argument("--n", type=int, default=50000, help="Number of transactions (default: 50000)")
    p_dataset.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    p_dataset.add_argument("--out", default="data/transactions_labeled.csv", help="Output file path")

    # Subcommand: burst
    p_burst = subparsers.add_parser("burst", help="Post an immediate scam scenario for live demo alerts")
    p_burst.add_argument(
        "--typology",
        required=True,
        choices=["mule_fan_in", "rapid_forwarding", "circular_layering", "account_takeover"],
        help="Typology to trigger",
    )
    p_burst.add_argument("--api-url", default=default_api_url, help=f"API base URL (default: {default_api_url})")

    args = parser.parse_args()

    if args.command == "stream":
        asyncio.run(
            run_stream(
                api_url=args.api_url,
                rate=args.rate,
                duration=args.duration,
                scam_rate=args.scam_rate,
                seed=args.seed,
            )
        )
    elif args.command == "dataset":
        run_dataset(n=args.n, seed=args.seed, out_path=args.out)
    elif args.command == "burst":
        run_burst(typology=args.typology, api_url=args.api_url)


if __name__ == "__main__":
    main()
