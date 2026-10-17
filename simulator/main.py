"""CLI: stream payments to the API, export a labeled CSV, or fire a demo burst."""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from simulator.generator import AccountPool, take_benign
from simulator.models import Transaction
from simulator.typologies import TYPOLOGY_FNS, inject_scenarios

load_dotenv()

DEFAULT_API = os.getenv("AEGIS_API_URL", "http://localhost:8000")
DEFAULT_START = datetime(2026, 10, 17, 7, 0, 0, tzinfo=timezone.utc)
CSV_COLUMNS = [
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


def _build_dataset(n: int, seed: int, scam_rate: float = 0.04) -> list[Transaction]:
    pool = AccountPool(n_accounts=2000, seed=seed)
    n_benign = max(1, int(round(n * (1.0 - scam_rate))))
    benign = take_benign(pool, DEFAULT_START, n_benign, rate_per_sec=20.0, seed=seed)
    return inject_scenarios(benign, pool, scam_rate=scam_rate, seed=seed)


def cmd_dataset(n: int, seed: int, out: str) -> None:
    """Write a labeled CSV sorted by timestamp and print class balance."""
    txns = _build_dataset(n, seed)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    n_scam = 0
    by_type: dict[str, int] = {}
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for txn in txns:
            truth = txn.truth
            is_scam = bool(truth and truth.is_scam)
            typology = truth.typology if truth else "none"
            n_scam += int(is_scam)
            by_type[typology] = by_type.get(typology, 0) + 1
            writer.writerow(
                {
                    "txn_id": txn.txn_id,
                    "timestamp": txn.timestamp,
                    "sender_token": txn.sender_token,
                    "receiver_token": txn.receiver_token,
                    "amount": txn.amount,
                    "channel": txn.channel,
                    "device_token": txn.device_token,
                    "memo": txn.memo or "",
                    "is_scam": is_scam,
                    "typology": typology,
                }
            )
    total = len(txns)
    share = (n_scam / total) if total else 0.0
    print(f"wrote {total} rows to {path}")
    print(f"scam={n_scam} share={share:.4f} mix={by_type}")


async def _post_txn(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    payload: dict,
) -> tuple[bool, dict | None]:
    async with sem:
        last_error: Exception | None = None
        for _ in range(3):
            try:
                resp = await client.post(url, json=payload, timeout=5.0)
                resp.raise_for_status()
                return True, resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
        return False, {"error": str(last_error)}


async def cmd_stream(api_url: str, rate: float, duration: int, scam_rate: float, seed: int) -> None:
    """Generate a window of traffic and POST it at ``rate`` txns/sec."""
    n = max(1, int(round(rate * duration)))
    txns = _build_dataset(n, seed, scam_rate=scam_rate)
    url = api_url.rstrip("/") + "/v1/transactions"
    sem = asyncio.Semaphore(20)
    stop = asyncio.Event()

    def _handle_sigint(*_args: object) -> None:
        stop.set()

    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except (ValueError, OSError):
        pass

    sent = 0
    errors = 0
    flagged = 0
    tp = fp = fn = tn = 0
    started = time.perf_counter()
    last_print = started

    def summary() -> None:
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        elapsed = time.perf_counter() - started
        print(
            f"sent={sent} errors={errors} flagged={flagged} "
            f"precision={prec:.3f} recall={rec:.3f} elapsed_s={elapsed:.1f}"
        )

    async with httpx.AsyncClient() as client:
        tasks: list[asyncio.Task[None]] = []

        async def send_one(i: int, txn: Transaction) -> None:
            nonlocal sent, errors, flagged, tp, fp, fn, tn, last_print
            if stop.is_set():
                return
            target = started + (i / rate if rate > 0 else 0.0)
            delay = target - time.perf_counter()
            if delay > 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    return
                except TimeoutError:
                    pass
            payload = txn.to_payload()
            ok, body = await _post_txn(client, sem, url, payload)
            is_scam = bool(txn.truth and txn.truth.is_scam)
            if not ok:
                errors += 1
            else:
                sent += 1
                risk_level = str((body or {}).get("risk_level", "LOW"))
                is_flag = risk_level != "LOW"
                if is_flag:
                    flagged += 1
                if is_flag and is_scam:
                    tp += 1
                elif is_flag and not is_scam:
                    fp += 1
                elif (not is_flag) and is_scam:
                    fn += 1
                else:
                    tn += 1
            now = time.perf_counter()
            if now - last_print >= 5.0:
                last_print = now
                summary()

        for i, txn in enumerate(txns):
            if stop.is_set():
                break
            tasks.append(asyncio.create_task(send_one(i, txn)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    summary()
    print("stream finished")


async def cmd_burst(api_url: str, typology: str) -> None:
    """Post one scam scenario with current timestamps (live demo alert)."""
    rng = __import__("numpy").random.default_rng(int(time.time()) % 10_000)
    pool = AccountPool(n_accounts=2000, seed=42)
    now = datetime.now(timezone.utc)
    txns = TYPOLOGY_FNS[typology](pool, now, rng)
    url = api_url.rstrip("/") + "/v1/transactions"
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(20)
        for txn in txns:
            ok, body = await _post_txn(client, sem, url, txn.to_payload())
            print(f"{txn.txn_id} ok={ok} body={body}")
    print(f"burst {typology} posted n={len(txns)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="simulator.main", description="Aegis Exchange Part 1")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stream = sub.add_parser("stream", help="replay a synthetic stream into the API")
    p_stream.add_argument("--api-url", default=DEFAULT_API)
    p_stream.add_argument("--rate", type=float, default=20.0)
    p_stream.add_argument("--duration", type=int, default=300)
    p_stream.add_argument("--scam-rate", type=float, default=0.04)
    p_stream.add_argument("--seed", type=int, default=42)

    p_ds = sub.add_parser("dataset", help="write labeled CSV")
    p_ds.add_argument("--n", type=int, default=50_000)
    p_ds.add_argument("--seed", type=int, default=42)
    p_ds.add_argument("--out", default="data/transactions_labeled.csv")

    p_burst = sub.add_parser("burst", help="post one scam scenario now")
    p_burst.add_argument(
        "--typology",
        required=True,
        choices=list(TYPOLOGY_FNS.keys()),
    )
    p_burst.add_argument("--api-url", default=DEFAULT_API)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "dataset":
            cmd_dataset(n=args.n, seed=args.seed, out=args.out)
            return 0
        if args.cmd == "stream":
            asyncio.run(
                cmd_stream(
                    api_url=args.api_url,
                    rate=args.rate,
                    duration=args.duration,
                    scam_rate=args.scam_rate,
                    seed=args.seed,
                )
            )
            return 0
        if args.cmd == "burst":
            asyncio.run(cmd_burst(api_url=args.api_url, typology=args.typology))
            return 0
    except KeyboardInterrupt:
        print("\ninterrupted — exiting with summary above if any", file=sys.stderr)
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
