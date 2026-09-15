"""One-off backfill: store sqrtPriceX96 and activeLiquidity in stored swaps.

Swap logs already carry both values in their data words; re-reading the logs
(about one request per 10k blocks per pool) avoids re-fetching receipts. Only
the meta JSON of finalized swaps changes; the finalized-field conflict check
in app.flows ignores meta by design.
"""
import argparse
import asyncio
import json
import sqlite3
import sys

sys.path.insert(0, ".")

from app.codec import SWAP
from app.config import SEED_POOLS, Settings
from app.sources import Sources

WINDOW = 10_000


def word(data, index):
    return int(data[2 + index * 64:2 + (index + 1) * 64], 16)


async def backfill(db_path, start, end, window=WINDOW):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    net = Sources(Settings())
    updated = missing = 0
    try:
        for pool in SEED_POOLS:
            for base in range(start, end + 1, window):
                hi = min(end, base + window - 1)
                logs = await net.eth_logs({"address": pool, "fromBlock": hex(base),
                                           "toBlock": hex(hi), "topics": [SWAP]}, archive=True)
                for item in logs:
                    identity = "ethereum:%s:%d" % (item["transactionHash"].lower(),
                                                   int(item["logIndex"], 16))
                    row = conn.execute("SELECT meta FROM events WHERE id=?", (identity,)).fetchone()
                    if row is None:
                        missing += 1
                        continue
                    meta = json.loads(row["meta"])
                    if "liquidity_raw" in meta:
                        continue
                    meta["sqrt_price_raw"] = str(word(item["data"], 2))
                    meta["liquidity_raw"] = str(word(item["data"], 3))
                    with conn:
                        conn.execute("UPDATE events SET meta=? WHERE id=?",
                                     (json.dumps(meta, ensure_ascii=False), identity))
                    updated += 1
                print(f"{pool[:10]} {base}-{hi}: cumulative updated={updated}", flush=True)
    finally:
        await net.close()
        conn.close()
    print(f"done: updated={updated} unknown={missing}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(backfill(args.database, args.start, args.end))
