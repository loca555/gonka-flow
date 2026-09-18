"""Address leaderboards and price distributions from the same verified swap snapshot."""
import re
from datetime import date, datetime, time
from .config import ZERO
from .db import tokens
from .flows import analysis, price_raw
from .timezones import CYPRUS, local_day
from .bridge_volume import bridge_price_bands, bridge_since


def trade_leaders(db, start_date=None, grouped=True):
    since = int(datetime.combine(date.fromisoformat(start_date), time.min, CYPRUS).timestamp()) if start_date else None
    data = analysis(db, side="all", limit=None, include_bridge=True)
    result = {key: data[key] for key in ("ready", "now", "timezone", "snapshot", "coverage", "status", "pools")}
    result.update(buyers=[], sellers=[], summary=None, excluded=None, price_distribution=None, price_days=None, price_bridge=None, bridge=None,
                  attribution="initiator_net_tx_net_initiator_only", period="since_date" if start_date else "all_history", start_date=start_date, since=since,
                  grouped=bool(grouped), groups=[],
                  scope="All addresses in tracked pools; gross swap volume attributed by transaction-wide WGNK net flow (recipient/payer), falling back to the executing initiator")
    if not data["ready"]:
        return result
    groups = {side: {} for side in ("buy", "sell")}
    price_bins = {step: {side: {} for side in groups} for step in (5, 10)}
    day_bins = {step: {} for step in price_bins}
    excluded = {side: {"volume_raw": 0, "quote_raw": 0, "swaps": 0} for side in groups}
    for event in data["trades"]:
        if since is not None and event["ts"] < since:
            continue
        side, address = event["kind"], event["actor"]
        quantity, quote = int(event["amount_raw"]), int(event["quote_raw"])
        # tx_net/initiator_only swaps carry the confirmed counterparty or, failing
        # that, the executing initiator (router, intent settler, round-trip bot).
        # Only swaps without any address or receipt evidence stay outside.
        if (event.get("attribution") == "pool_only"
                or not re.fullmatch("0x[0-9a-f]{40}", address or "") or address == ZERO):
            row = excluded[side]
        else:
            row = groups[side].setdefault(address, {"address": address, "volume_raw": 0,
                "quote_raw": 0, "swaps": 0, "txs": set(), "first_ts": event["ts"], "last_ts": event["ts"]})
            row["txs"].add(event["tx_hash"])
            row["first_ts"] = min(row["first_ts"], event["ts"])
            row["last_ts"] = max(row["last_ts"], event["ts"])
            day = local_day(event["ts"])
            for step, sides in price_bins.items():
                # USDT has 6 decimals, WGNK 9. Classify the exact execution ratio,
                # without rounding the price first. Bounds are [lower, upper).
                index = quote * 100_000 // (quantity * step)
                bucket = sides[side].setdefault(index, {"volume_raw": 0, "quote_raw": 0, "swaps": 0})
                bucket["volume_raw"] += quantity
                bucket["quote_raw"] += quote
                bucket["swaps"] += 1
                daily = day_bins[step].setdefault(day, {})
                daily[index] = daily.get(index, 0) + quantity
        row["volume_raw"] += quantity
        row["quote_raw"] += quote
        row["swaps"] += 1

    def quantities(row):
        return {"volume_raw": str(row["volume_raw"]), "volume": tokens(row["volume_raw"]),
                "quote_raw": str(row["quote_raw"]), "quote": tokens(row["quote_raw"], 6), "swaps": row["swaps"]}

    from .flows import realized_pnl
    PNL_MIN_RAW = 100 * 10**6
    def pnl_fields(buy_quote, buy_amount, sell_quote, sell_amount):
        """PNL of closed trades in USDT; shown only for two-sided
        addresses beyond ±100; the open remainder is not a loss."""
        pnl = realized_pnl(buy_quote, buy_amount, sell_quote, sell_amount)
        if pnl is None:
            return {"pnl_raw": None, "pnl": None}
        return {"pnl_raw": str(pnl), "pnl": tokens(pnl, 6)}

    # One calendar day belongs to one price band across both trade directions.
    # Recompute at each step: merging 5-cent winners would give wrong 10-cent days.
    # Equal volumes choose the lower band, independently of event ordering.
    price_days, price_bridge = {}, {}
    bridge = bridge_since(data.get("bridge"), start_date)
    if bridge:
        result["bridge"] = {key: value for key, value in bridge.items() if key != "daily"}
    for step, days in day_bins.items():
        counts, winners = {}, {}
        for day, volumes in days.items():
            winner = min(volumes, key=lambda index: (-volumes[index], index))
            counts[winner] = counts.get(winner, 0) + 1
            winners[day] = winner
        price_bridge[str(step)] = bridge_price_bands(bridge, winners, step)
        price_days[str(step)] = {
            "total_days": len(days),
            "bands": [{"from_price_raw": str(index * step * 10**10),
                       "to_price_raw": str((index + 1) * step * 10**10), "days": count}
                      for index, count in sorted(counts.items())]}

    summary = {}
    for side, name in (("buy", "buyers"), ("sell", "sellers")):
        rows = sorted(groups[side].values(), key=lambda row: (-row["volume_raw"], row["address"]))
        result[name] = [{**quantities(row), "address": row["address"], "rank": rank,
                         "transactions": len(row["txs"]), "first_ts": row["first_ts"], "last_ts": row["last_ts"],
                         "average_price": tokens(price_raw(row["quote_raw"], row["volume_raw"]), 12), "group": None,
                         "buy_quote_raw": str(groups["buy"].get(row["address"], {}).get("quote_raw", 0)),
                         "sell_quote_raw": str(groups["sell"].get(row["address"], {}).get("quote_raw", 0)),
                         "buy_amount_raw": str(groups["buy"].get(row["address"], {}).get("volume_raw", 0)),
                         "sell_amount_raw": str(groups["sell"].get(row["address"], {}).get("volume_raw", 0))}
                        for rank, row in enumerate(rows, 1)]
        total = {key: sum(row[key] for row in rows) for key in ("volume_raw", "quote_raw", "swaps")}
        summary[side] = {**quantities(total), "addresses": len(rows)}
    if grouped and db is not None:
        merge_groups(db, result, summary)
    for name in ("buyers", "sellers"):
        for row in result[name]:
            row.update(pnl_fields(int(row.get("buy_quote_raw") or 0), int(row.get("buy_amount_raw") or 0),
                                  int(row.get("sell_quote_raw") or 0), int(row.get("sell_amount_raw") or 0)))
    result.update(summary=summary, price_days=price_days, price_bridge=price_bridge, excluded={side: quantities(row) for side, row in excluded.items()},
                  price_distribution={str(step): {
                      side: [{"from_price_raw": str(index * step * 10**10),
                              "to_price_raw": str((index + 1) * step * 10**10), **quantities(bucket),
                              "price_raw": str(price_raw(bucket["quote_raw"], bucket["volume_raw"]))}
                             for index, bucket in sorted(buckets.items())]
                      for side, buckets in sides.items()} for step, sides in price_bins.items()})
    return result


def merge_groups(db, result, summary):
    """Combine rows of one probable group into a single expandable row per side."""
    from .clusters import cached
    pools = [p["address"] for p in result.get("pools") or []]
    rated = {row["address"] for name in ("buyers", "sellers") for row in result[name]}
    data = cached(db, rated, pools)
    result["groups"] = data["groups"]
    if not data["address_group"]:
        return
    by_key = {group["key"]: group for group in data["groups"]}
    for name, side in (("buyers", "buy"), ("sellers", "sell")):
        plain = {row["address"]: row for row in result[name]}
        merged, seen = [], set()
        for address, row in plain.items():
            key = data["address_group"].get(address)
            if not key or key in seen:
                continue
            seen.add(key)
            members = [plain[a] for a in by_key[key]["addresses"] if a in plain]
            if len(members) < 2:
                continue
            volume = sum(int(m["volume_raw"]) for m in members)
            quote = sum(int(m["quote_raw"]) for m in members)
            buy_quote = sum(int(m.get("buy_quote_raw") or 0) for m in members)
            sell_quote = sum(int(m.get("sell_quote_raw") or 0) for m in members)
            buy_amount = sum(int(m.get("buy_amount_raw") or 0) for m in members)
            sell_amount = sum(int(m.get("sell_amount_raw") or 0) for m in members)
            merged.append({"address": members[0]["address"], "group": {
                "addresses": [m["address"] for m in members],
                "evidence": by_key[key]["evidence"]},
                "volume_raw": str(volume), "volume": tokens(volume),
                "quote_raw": str(quote), "quote": tokens(quote, 6),
                "swaps": sum(m["swaps"] for m in members),
                "transactions": sum(m["transactions"] for m in members),
                "first_ts": min(m["first_ts"] for m in members),
                "last_ts": max(m["last_ts"] for m in members),
                "average_price": tokens(price_raw(quote, volume), 12),
                "buy_quote_raw": str(buy_quote), "sell_quote_raw": str(sell_quote),
                "buy_amount_raw": str(buy_amount), "sell_amount_raw": str(sell_amount),
                "members": sorted(members, key=lambda m: -int(m["volume_raw"]))})
        kept = [row for row in result[name] if row["address"] not in data["address_group"]]
        rows = sorted(merged + kept, key=lambda row: (-int(row["volume_raw"]), row["address"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        result[name] = rows
        summary[side]["groups"] = len(merged)
