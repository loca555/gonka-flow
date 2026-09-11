"""Address leaderboards and price distributions from the same verified swap snapshot."""
import re
from .config import ZERO
from .db import tokens
from .flows import analysis, price_raw


def trade_leaders(db):
    data = analysis(db, side="all", limit=None)
    result = {key: data[key] for key in ("ready", "now", "timezone", "snapshot", "coverage", "status", "pools")}
    result.update(buyers=[], sellers=[], summary=None, excluded=None, price_distribution=None,
                  attribution="initiator_net", period="all_history",
                  scope="All addresses in tracked pools; gross swap volume attributed by transaction-wide WGNK net-flow direction")
    if not data["ready"]:
        return result
    groups = {side: {} for side in ("buy", "sell")}
    price_bins = {step: {side: {} for side in groups} for step in (5, 10)}
    excluded = {side: {"volume_raw": 0, "quote_raw": 0, "swaps": 0} for side in groups}
    for event in data["trades"]:
        side, address = event["kind"], event["actor"]
        quantity, quote = int(event["amount_raw"]), int(event["quote_raw"])
        if event["attribution"] != "initiator_net" or not re.fullmatch("0x[0-9a-f]{40}", address or "") or address == ZERO:
            row = excluded[side]
        else:
            row = groups[side].setdefault(address, {"address": address, "volume_raw": 0,
                "quote_raw": 0, "swaps": 0, "txs": set(), "first_ts": event["ts"], "last_ts": event["ts"]})
            row["txs"].add(event["tx_hash"])
            row["first_ts"] = min(row["first_ts"], event["ts"])
            row["last_ts"] = max(row["last_ts"], event["ts"])
            for step, sides in price_bins.items():
                # USDT has 6 decimals, WGNK 9. Classify the exact execution ratio,
                # without rounding the price first. Bounds are [lower, upper).
                index = quote * 100_000 // (quantity * step)
                bucket = sides[side].setdefault(index, {"volume_raw": 0, "quote_raw": 0, "swaps": 0})
                bucket["volume_raw"] += quantity
                bucket["quote_raw"] += quote
                bucket["swaps"] += 1
        row["volume_raw"] += quantity
        row["quote_raw"] += quote
        row["swaps"] += 1

    def quantities(row):
        return {"volume_raw": str(row["volume_raw"]), "volume": tokens(row["volume_raw"]),
                "quote_raw": str(row["quote_raw"]), "quote": tokens(row["quote_raw"], 6), "swaps": row["swaps"]}

    summary = {}
    for side, name in (("buy", "buyers"), ("sell", "sellers")):
        rows = sorted(groups[side].values(), key=lambda row: (-row["volume_raw"], row["address"]))
        result[name] = [{**quantities(row), "address": row["address"], "rank": rank,
                         "transactions": len(row["txs"]), "first_ts": row["first_ts"], "last_ts": row["last_ts"],
                         "average_price": tokens(price_raw(row["quote_raw"], row["volume_raw"]), 12)}
                        for rank, row in enumerate(rows, 1)]
        total = {key: sum(row[key] for row in rows) for key in ("volume_raw", "quote_raw", "swaps")}
        summary[side] = {**quantities(total), "addresses": len(rows)}
    result.update(summary=summary, excluded={side: quantities(row) for side, row in excluded.items()},
                  price_distribution={str(step): {
                      side: [{"from_price_raw": str(index * step * 10**10),
                              "to_price_raw": str((index + 1) * step * 10**10), **quantities(bucket),
                              "price_raw": str(price_raw(bucket["quote_raw"], bucket["volume_raw"]))}
                             for index, bucket in sorted(buckets.items())]
                      for side, buckets in sides.items()} for step, sides in price_bins.items()})
    return result
