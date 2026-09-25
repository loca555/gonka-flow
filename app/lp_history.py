"""Historical pool tick liquidity rebuilt from Uniswap V3 Mint/Burn events.

Nodes only serve the *current* tick state, so exact depth for past days
cannot be queried retroactively. Mint and Burn events each carry the exact
liquidityNet delta (tickLower, tickUpper, uint128 amount), and the running
sum of those deltas at any height equals the on-chain liquidityNet map the
live depth worker reads via eth_call. `sync` collects the events into the
lp_ticks table; `rebuild_if_stale` walks them alongside the pool's Swap
events and computes each day's bands with the same exact tick math as
app.depth (verified once against the live map during development).
"""
import json
import math
import time

import httpx

from . import depth
from .flows import local_day

MINT_SIG = "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde"
BURN_SIG = "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c"
POOL = depth.POOL
Q96 = depth.Q96
SPACING = depth.SPACING
CHUNK = 100000          # blocks per getLogs; halves on range errors
MIN_CHUNK = 1000


def _signed24(word):
    # Indexed int24 topics are sign-extended to 256 bits; keep the low 24.
    value = int(word, 16) & 0xFFFFFF
    return value - (1 << 24) if value >= 1 << 23 else value


def decode_log(log):
    """(tx_hash, log_index, height, kind, tick_lower, tick_upper, amount) or None."""
    topics = log["topics"]
    if len(topics) < 4:
        return None
    sig = topics[0].lower()
    if sig == MINT_SIG:
        amount = int.from_bytes(bytes.fromhex(log["data"][2:][64:128]), "big")
    elif sig == BURN_SIG:
        amount = int.from_bytes(bytes.fromhex(log["data"][2:][:64]), "big")
    else:
        return None
    return (log["transactionHash"].lower(), int(log["logIndex"], 16),
            int(log["blockNumber"], 16),
            "mint" if sig == MINT_SIG else "burn",
            _signed24(topics[2]), _signed24(topics[3]), amount)


async def _rpc(endpoints, method, params):
    last = None
    async with httpx.AsyncClient(timeout=30,
            headers={"Content-Type": "application/json", "User-Agent": "gonka-flow/1.0"}) as client:
        for endpoint in endpoints:
            try:
                response = await client.post(endpoint, json={"jsonrpc": "2.0", "id": 1,
                        "method": method, "params": params})
                response.raise_for_status()
                data = response.json()
                if "result" in data:
                    return data["result"]
                last = ValueError(data.get("error") or "RPC error")
            except Exception as error:
                last = error
    raise last


async def sync(cfg, db):
    """Fetch Mint/Burn logs of the 30 b.p. pool into lp_ticks, chunk by chunk."""
    deployment = db.get("mints:deployment") or {}
    start = int(deployment.get("height") or 0)
    if not start:
        return 0
    finalized = int((db.get("mints:status") or {}).get("finalized_height") or 0)
    if not finalized:
        return 0
    saved = db.get("lp_ticks:head") or {"height": start - 1}
    lo = saved["height"] + 1
    if lo > finalized:
        return 0
    step = CHUNK
    fetched = 0
    while lo <= finalized:
        hi = min(finalized, lo + step - 1)
        try:
            logs = await _rpc(cfg.ethereum, "eth_getLogs", [{
                "address": POOL, "fromBlock": hex(lo), "toBlock": hex(hi),
                "topics": [[MINT_SIG, BURN_SIG]]}])
        except Exception:
            if step > MIN_CHUNK:
                step = max(MIN_CHUNK, step // 2)
                continue
            raise
        rows = []
        for item in logs:
            decoded = decode_log(item)
            if decoded:
                rows.append(decoded)
        if rows:
            db.conn.executemany(
                "INSERT OR REPLACE INTO lp_ticks VALUES(?,?,?,?,?,?,?)", rows)
            db.conn.commit()
        fetched += len(rows)
        db.put("lp_ticks:head", {"height": hi, "ts": int(time.time())})
        lo = hi + 1
        step = min(CHUNK, step * 2)
    _verify_counts(db, start, finalized)
    return fetched


def _verify_counts(db, start, end):
    """Indexed Mint/Burn logs must match the indexer's own liquidity events."""
    lp = db.conn.execute("SELECT count(*) FROM lp_ticks WHERE height BETWEEN ? AND ?",
                         (start, end)).fetchone()[0]
    indexed = db.conn.execute(
        "SELECT count(*) FROM events WHERE chain='ethereum' AND finalized=1 "
        "AND kind IN ('liquidity_add','liquidity_remove') AND height BETWEEN ? AND ? "
        "AND lower(json_extract(meta,'$.contract'))=?", (start, end, POOL)).fetchone()[0]
    if lp != indexed:
        raise ValueError(f"lp_ticks {lp} != indexed liquidity events {indexed}")


def _day_swaps(db):
    """Last finalized pool swap per local day: {day: (height, idx, sqrt, L)}."""
    days = {}
    for row in db.conn.execute(
            "SELECT height, idx, ts, meta FROM events WHERE chain='ethereum' AND finalized=1 "
            "AND kind IN ('buy','sell') AND pool=? ORDER BY height, idx", (POOL,)):
        meta = json.loads(row["meta"])
        sqrt_raw, liquidity = meta.get("sqrt_price_raw"), meta.get("liquidity_raw")
        if not sqrt_raw or not liquidity:
            continue
        day = local_day(row["ts"])
        current = days.get(day)
        if current is None or (row["height"], row["idx"]) > (current[0], current[1]):
            days[day] = (row["height"], row["idx"], int(sqrt_raw), int(liquidity))
    return days


def _lp_rows(db):
    return [{"height": r["height"], "idx": r["idx"], "kind": r["kind"],
             "lower": r["tick_lower"], "upper": r["tick_upper"], "amount": int(r["amount"])}
            for r in db.conn.execute("SELECT * FROM lp_ticks ORDER BY height, idx")]


def _apply(net, row):
    sign = 1 if row["kind"] == "mint" else -1
    net[row["lower"] // SPACING] = net.get(row["lower"] // SPACING, 0) + sign * row["amount"]
    net[row["upper"] // SPACING] = net.get(row["upper"] // SPACING, 0) - sign * row["amount"]


def daily_bands(db):
    """Exact per-day band series for the pool, same math as the live line."""
    days = _day_swaps(db)
    if not days:
        return {"pools": {}, "built_at": int(time.time()), "fingerprint": (0, 0, 0, 0)}
    lp = _lp_rows(db)
    points = {str(level): [] for level in depth.LEVELS}
    net = {}
    cursor = 0
    for day in sorted(days):
        height, idx, sqrt, active = days[day]
        while cursor < len(lp) and (lp[cursor]["height"], lp[cursor]["idx"]) < (height, idx):
            _apply(net, lp[cursor])
            cursor += 1
        snapshot = dict(net)
        price = (sqrt / Q96) ** 2 * 1e3
        tick = math.floor(math.log(price / 1e3) / math.log(1.0001))
        pos_now = tick // SPACING
        for level in depth.LEVELS:
            band = depth._band(snapshot, pos_now, active, sqrt, price, level)
            if not band.get("up_usdt_raw") or not band.get("down_usdt_raw"):
                # A side may be unreachable on thin historical days; the
                # chart carries the last known value across such gaps.
                continue
            # Band contents for the chart lines: WGNK above the day price and
            # USDT below it within the +/-level% range (final-price edges).
            _, wgnk_up = depth._amounts_up(snapshot, pos_now, active, sqrt,
                                           sqrt * (1 + level / 100) ** .5)
            usdt_dn, _ = depth._amounts_down(snapshot, pos_now, active, sqrt,
                                             sqrt * (1 - level / 100) ** .5)
            points[str(level)].append({"date": day,
                                       "wgnk_raw": str(int(wgnk_up)),
                                       "usdt_raw": str(int(usdt_dn)),
                                       "up_usdt_raw": band["up_usdt_raw"],
                                       "down_usdt_raw": band["down_usdt_raw"],
                                       "down_wgnk_raw": band["down_wgnk_raw"],
                                       "up_final_price": band["up_final_price"],
                                       "down_final_price": band["down_final_price"]})
    series = {POOL: {level: rows for level, rows in points.items() if rows}}
    counts = db.conn.execute("SELECT count(*), coalesce(max(height),0) FROM lp_ticks").fetchone()
    swaps = db.conn.execute(
        "SELECT count(*), coalesce(max(height),0) FROM events WHERE chain='ethereum' "
        "AND finalized=1 AND kind IN ('buy','sell') AND pool=?", (POOL,)).fetchone()
    return {"pools": series, "built_at": int(time.time()),
            "fingerprint": (counts[0], counts[1], swaps[0], swaps[1])}


def rebuild_if_stale(db):
    """Recompute the series only when the underlying events changed."""
    counts = db.conn.execute("SELECT count(*), coalesce(max(height),0) FROM lp_ticks").fetchone()
    swaps = db.conn.execute(
        "SELECT count(*), coalesce(max(height),0) FROM events WHERE chain='ethereum' "
        "AND finalized=1 AND kind IN ('buy','sell') AND pool=?", (POOL,)).fetchone()
    fingerprint = (counts[0], counts[1], swaps[0], swaps[1])
    saved = db.get("flow:depth_history") or {}
    # kv round-trips the tuple as a JSON list.
    if tuple(saved.get("fingerprint") or ()) == fingerprint:
        return False
    db.put("flow:depth_history", daily_bands(db))
    return True


def current_net(db):
    """Tick net map from all stored events (verification helper)."""
    net = {}
    for row in _lp_rows(db):
        _apply(net, row)
    return {tick: value for tick, value in net.items() if value}
