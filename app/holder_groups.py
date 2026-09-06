"""Current balances outside tracked pools, grouped by confirmed WGNK trade volume."""
import json
import re
from collections import defaultdict
from .config import ZERO
from .db import tokens


def outside_holders(rows, ledger, snapshot):
    """Pure ledger calculation: no additional RPC, identity inference or token-lot tracing."""
    result = {"ready": False, "reason": "ledger_unverified", "height": snapshot["height"],
              "ts": snapshot.get("ts"), "total_raw": None, "total": None, "addresses": None, "groups": [],
              "source": "verified_transfer_ledger", "period": "all_history", "volume_unit": "WGNK",
              "attribution": "initiator_net", "denominator": "bought_raw + sold_raw",
              "threshold": "strictly_more_than_90_percent", "share_denominator": "outside_pools_balance"}
    if not snapshot.get("ledger_verified") or snapshot.get("ts") is None:
        return result
    pools = {p["address"]: int(p["balance_raw"]) for p in snapshot["pools"]}
    supply = int(snapshot["supply_raw"])
    outside = supply - sum(pools.values())
    if (any(value < 0 for value in ledger.values()) or sum(ledger.values()) != supply or outside < 0
            or ledger.get(ZERO, 0) != 0
            or any(ledger.get(address, 0) != value for address, value in pools.items())):
        result["reason"] = "ledger_mismatch"
        return result
    volumes = defaultdict(lambda: {"buy": 0, "sell": 0})
    for event in rows:
        if (event["kind"] not in ("buy", "sell") or event["pool"] not in pools
                or not event["finalized"] or event["height"] > snapshot["height"]):
            continue
        address = event["actor"]
        if (not re.fullmatch(r"0x[0-9a-f]{40}", address or "") or address == ZERO
                or json.loads(event["meta"]).get("attribution") != "initiator_net"):
            continue
        quantity = int(event["amount_raw"])
        if quantity < 0:
            result["reason"] = "invalid_trade_amount"
            return result
        volumes[address][event["kind"]] += quantity
    groups = {key: {"id": key, "balance_raw": 0, "addresses": 0, "holders": []}
              for key in ("investors", "sellers", "traders", "unclassified")}
    for address, balance in ledger.items():
        if balance <= 0 or address in pools or address == ZERO:
            continue
        volume = volumes.get(address, {"buy": 0, "sell": 0})
        total = volume["buy"] + volume["sell"]
        # Exact cross multiplication: 90/10 is a trader, not an investor or seller.
        if total == 0:
            key = "unclassified"
        elif volume["buy"] * 10 > total * 9:
            key = "investors"
        elif volume["sell"] * 10 > total * 9:
            key = "sellers"
        else:
            key = "traders"
        groups[key]["balance_raw"] += balance
        groups[key]["addresses"] += 1
        groups[key]["holders"].append({"address": address, "balance_raw": str(balance), "balance": tokens(balance),
            "bought_raw": str(volume["buy"]), "bought": tokens(volume["buy"]),
            "sold_raw": str(volume["sell"]), "sold": tokens(volume["sell"])})
    for group in groups.values():
        group["holders"].sort(key=lambda holder: (-int(holder["balance_raw"]), holder["address"]))
    result.update(ready=True, reason=None, total_raw=str(outside), total=tokens(outside),
                  addresses=sum(group["addresses"] for group in groups.values()),
                  groups=[{**group, "balance_raw": str(group["balance_raw"]),
                           "balance": tokens(group["balance_raw"])} for group in groups.values()])
    return result
