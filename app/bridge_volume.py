"""Bridge volume on the market snapshot, with an explicit startup-liquidity adjustment.

Tokens are fungible: this is a subtraction of verified initial pool deposits from
that day's bridge inflow, not a claim about which individual tokens reached it.
The raw mint/burn ledger is never changed.
"""
from .timezones import local_day

ORIGIN = "0xbd3ca5a43f72e45faf4b142832110842113dc9f8"
RELAY = "0x244186297b43ba43c92d68efe78c04170fbc0b10"
POOL = "0x203ee836d417cf944133bbdd2c62b4bc7388c55d"
STARTUP_DAY = "2026-06-09"
# Successful receipts independently checked on Ethereum, 2026-09-11.
# (block, transaction, WGNK Transfer log, pool Mint log, WGNK raw amount)
STARTUP_DEPOSITS = (
    (25280147, "0x44564b598bdde2fb4a8e36305ee76a44a9c62f15b0d0f4940a2cce80e9f125ba", 20, 22, 57144269905621),
    (25280215, "0x7e529d3469e49386894bf7427bed4f89d4f5ae2ff6841f19501a6d99cecbd446", 253, 255, 270232940596108),
    (25280239, "0xfa3d9ac71225d7b5d0f149928c5c8f28fa87f8e0ed41b30212c43aef64546a4b", 43, 45, 382264784468078),
    (25280249, "0xad9d9c4f0d69f4f563490b4579ee01483dde05537163206254fc371f58f42982", 33, 35, 193614041168467),
    (25280392, "0x43a7e597e4716d745ffacef7f39d9009baa98953a99c4c108c07cd86b392ba12", 277, 278, 899999999999997),
    (25280443, "0x05f14be03b9cd4f9d582ab22ef7695cb82e652ff95a721624bbafbe013429c0d", 403, 404, 299999999999997),
    (25280481, "0x0ccd25e1b32ecb9bb28358f67b098a6945fc1c91140920fa4c314eb2e8209e70", 457, 458, 149999999999993),
)
FIELDS = ("gross_in_raw", "in_raw", "out_raw", "pool_funding_raw", "mints", "burns")


def empty_volume():
    return dict.fromkeys(FIELDS, 0)


def public_volume(row):
    return {key: str(row[key]) if key.endswith("_raw") else row[key] for key in FIELDS}


def bridge_daily(rows, through):
    """Consume exactly the same immutable, covered rows as the price/day charts."""
    days = {}
    for event in rows:
        if event["kind"] not in ("bridge_mint", "bridge_burn"):
            continue
        day = days.setdefault(local_day(event["ts"]), empty_volume())
        mint = event["kind"] == "bridge_mint"
        day["gross_in_raw" if mint else "out_raw"] += int(event["amount_raw"])
        day["mints" if mint else "burns"] += 1
    lookup = {(e["tx_hash"], e["idx"], e["kind"]): e for e in rows}
    deposits, excluded = [], 0
    for height, tx, transfer_idx, mint_idx, quantity in STARTUP_DEPOSITS:
        if height > through:
            continue
        transfer = lookup.get((tx, transfer_idx, "transfer"))
        mint = lookup.get((tx, mint_idx, "liquidity_add"))
        valid = (transfer and mint and transfer["src"] == RELAY and transfer["dst"] == POOL
                 and mint["pool"] == POOL and transfer["height"] == mint["height"] == height
                 and int(transfer["amount_raw"]) == int(mint["amount_raw"]) == quantity
                 and transfer["block_hash"] == mint["block_hash"]
                 and transfer["ts"] == mint["ts"] and local_day(transfer["ts"]) == STARTUP_DAY)
        # Require the bridge input and the origin-to-relay funding to be present
        # before each deposit. Never turn incomplete adjustment evidence into zero.
        source_in = sum(int(e["amount_raw"]) for e in rows if e["kind"] == "bridge_mint"
                        and e["dst"] == ORIGIN and e["height"] <= height and local_day(e["ts"]) == STARTUP_DAY)
        relayed = sum(int(e["amount_raw"]) for e in rows if e["kind"] == "transfer"
                      and e["src"] == ORIGIN and e["dst"] == RELAY and e["height"] <= height
                      and local_day(e["ts"]) == STARTUP_DAY)
        if not valid or excluded + quantity > min(source_in, relayed):
            return {"ready": False, "error": "startup_pool_funding_unverified", "daily": None,
                    "totals": None, "adjustment": None}
        excluded += quantity
        deposits.append({"tx_hash": tx, "height": height, "log_index": transfer_idx,
                         "pool_log_index": mint_idx, "amount_raw": str(quantity)})
    if excluded:
        days[STARTUP_DAY]["pool_funding_raw"] = excluded
    totals = empty_volume()
    for day in days.values():
        day["in_raw"] = day["gross_in_raw"] - day["pool_funding_raw"]
        for key in FIELDS:
            totals[key] += day[key]
    return {"ready": True, "error": None,
            "daily": [{"date": date, **public_volume(day)} for date, day in sorted(days.items())],
            "totals": public_volume(totals),
            "adjustment": {"date": STARTUP_DAY, "origin": ORIGIN, "relay": RELAY, "pool": POOL,
                           "amount_raw": str(excluded), "deposits": deposits,
                           "method": "subtract_verified_startup_liquidity_from_same_day_bridge_inflow"}}


def bridge_price_bands(bridge, winners, step):
    """Assign each day's entire bridge volume once, using the common trade winner."""
    if not bridge or not bridge["ready"]:
        return None
    bands = {index: empty_volume() for index in winners.values()}
    unassigned, unassigned_days = empty_volume(), 0
    for day in bridge["daily"]:
        index = winners.get(day["date"])
        total = unassigned if index is None else bands[index]
        unassigned_days += index is None
        for key in FIELDS:
            total[key] += int(day[key])
    return {"bands": [{"from_price_raw": str(index * step * 10**10),
                       "to_price_raw": str((index + 1) * step * 10**10), **public_volume(row)}
                      for index, row in sorted(bands.items())],
            "unassigned": {"days": unassigned_days, **public_volume(unassigned)}}
