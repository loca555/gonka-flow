"""Point-in-time holder groups, replayed from the complete finalized WGNK ledger."""
import json
import re
from collections import defaultdict
from datetime import date, timedelta

from .config import ZERO
from .holder_groups import GROUPS, category
from .timezones import TIME_ZONE, local_day


def holder_history(rows, ledger, snapshot, deployment, current):
    """Caller provides contiguous deployment-to-snapshot events, before any filters.

    Every historical address is included, even if it has since emptied its wallet.
    Category changes move its entire then-current balance, not just that day's flow.
    No current category or future trade is projected backwards.
    """
    result = {"ready": False, "reason": "ledger_unverified", "points": [],
              "height": snapshot["height"], "ts": snapshot.get("ts"),
              "start_height": deployment["height"], "start_ts": deployment.get("ts"),
              "timezone": TIME_ZONE, "source": "verified_transfer_ledger", "interval": "day",
              "classification_basis": "cumulative_trades_as_of_day",
              "balance_basis": "end_of_day_or_snapshot", "scope": "all_historical_addresses_outside_tracked_pools"}
    if not current.get("ready") or deployment.get("ts") is None or snapshot.get("ts") is None:
        return result
    first, last = local_day(deployment["ts"]), local_day(snapshot["ts"])
    if snapshot["ts"] < deployment["ts"]:
        result["reason"] = "invalid_time_range"
        return result
    pools = {p["address"] for p in snapshot["pools"]}
    days = defaultdict(list)
    for event in rows:
        if not event["finalized"] or not deployment["height"] <= event["height"] <= snapshot["height"]:
            continue
        if not deployment["ts"] <= event["ts"] <= snapshot["ts"]:
            result["reason"] = "invalid_event_timestamp"
            return result
        days[local_day(event["ts"])].append(event)
    balances = defaultdict(int)
    volumes = defaultdict(lambda: {"buy": 0, "sell": 0})
    minted = burned = 0
    points = []
    day, end = date.fromisoformat(first), date.fromisoformat(last)
    while day <= end:
        key = day.isoformat()
        for event in days.get(key, ()):
            kind, quantity = event["kind"], int(event["amount_raw"])
            if quantity < 0:
                result["reason"] = "invalid_event_amount"
                return result
            if kind == "bridge_mint":
                balances[event["dst"]] += quantity
                minted += quantity
            elif kind == "bridge_burn":
                balances[event["src"]] -= quantity
                burned += quantity
            elif kind == "transfer":
                balances[event["src"]] -= quantity
                balances[event["dst"]] += quantity
            elif (kind in ("buy", "sell") and event["pool"] in pools
                  and re.fullmatch(r"0x[0-9a-f]{40}", event["actor"] or "")
                  and event["actor"] != ZERO
                  and json.loads(event["meta"]).get("attribution") == "initiator_net"):
                volumes[event["actor"]][kind] += quantity
        totals, counts = dict.fromkeys(GROUPS, 0), dict.fromkeys(GROUPS, 0)
        for address, balance in balances.items():
            if balance < 0 or (address == ZERO and balance):
                result["reason"] = "historical_ledger_mismatch"
                return result
            if not balance or address in pools or address == ZERO:
                continue
            volume = volumes.get(address, {"buy": 0, "sell": 0})
            group = category(volume["buy"], volume["sell"])
            totals[group] += balance
            counts[group] += 1
        outside = minted - burned - sum(balances.get(pool, 0) for pool in pools)
        if outside < 0 or sum(totals.values()) != outside:
            result["reason"] = "historical_supply_mismatch"
            return result
        points.append({"date": key, **{g+"_raw": str(totals[g]) for g in GROUPS},
                       "total_raw": str(outside), "addresses": counts})
        day += timedelta(days=1)
    # Same endpoint as the cards, including exact balances and positive-holder counts.
    if (any(balances.get(a, 0) != ledger.get(a, 0) for a in set(balances) | set(ledger))
            or not points or points[-1]["total_raw"] != current["total_raw"]
            or any(points[-1][g["id"]+"_raw"] != g["balance_raw"]
                   or points[-1]["addresses"][g["id"]] != g["addresses"] for g in current["groups"])):
        result["reason"] = "current_snapshot_mismatch"
        return result
    result.update(ready=True, reason=None, points=points)
    return result
