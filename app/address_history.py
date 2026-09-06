"""Daily address activity from the same finalized ledger as its current balance."""
import json
from collections import defaultdict
from datetime import date, timedelta

from .timezones import TIME_ZONE, local_day


def address_history(rows, address, snapshot, expected_balance):
    """Swaps describe turnover; only token movements change the actual balance.

    The caller supplies the contiguous, finalized deployment-to-snapshot ledger.
    Trade filters and table sorting must never alter this all-history timeline.
    """
    pools = {p["address"] for p in snapshot["pools"]}
    days = defaultdict(lambda: {"delta_raw": 0, "bought_raw": 0, "sold_raw": 0,
                                "buys_count": 0, "sales_count": 0})
    for event in rows:
        kind = event["kind"]
        incoming = kind in ("transfer", "bridge_mint") and event["dst"] == address
        outgoing = kind in ("transfer", "bridge_burn") and event["src"] == address
        trade = (kind in ("buy", "sell") and event["actor"] == address
                 and event["pool"] in pools
                 and json.loads(event["meta"]).get("attribution") == "initiator_net")
        if not (incoming or outgoing or trade):
            continue
        quantity = int(event["amount_raw"])
        day = days[local_day(event["ts"])]
        day["delta_raw"] += quantity * (int(incoming) - int(outgoing))
        if trade:
            day["bought_raw" if kind == "buy" else "sold_raw"] += quantity
            day["buys_count" if kind == "buy" else "sales_count"] += 1

    last = local_day(snapshot["ts"])
    if days and max(days) > last:
        return None  # Inconsistent timestamps are not an empty/zero balance series.
    points, balance = [], 0
    if days:
        current, end = date.fromisoformat(min(days)), date.fromisoformat(last)
        while current <= end:
            key = current.isoformat()
            day = days[key]
            balance += day["delta_raw"]
            if balance < 0:
                return None
            points.append({"date": key, "balance_raw": str(balance),
                           "bought_raw": str(day["bought_raw"]), "sold_raw": str(day["sold_raw"]),
                           "buys_count": day["buys_count"], "sales_count": day["sales_count"]})
            current += timedelta(days=1)
    if balance != int(expected_balance):
        return None
    return {"points": points, "timezone": TIME_ZONE, "height": snapshot["height"],
            "ts": snapshot["ts"], "balance_raw": str(balance), "opening_balance_raw": "0",
            "source": "verified_transfer_ledger", "balance_basis": "end_of_day_or_snapshot"}
