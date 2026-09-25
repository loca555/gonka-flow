"""Exact Uniswap V3 market depth by walking tick liquidity.

The band formulas used for the daily chart assume the active liquidity is
constant across the whole +/-N% range. Real V3 liquidity is piecewise
constant (manual LP ranges), so this worker computes the exact cost of
moving the price: it reads the tick bitmap and every initialized tick in
the range and integrates L over each sub-range. Cached in kv `depth:live`.
"""
import time

import httpx

POOL = "0x203ee836d417cf944133bbdd2c62b4bc7388c55d"  # 30 b.p. WGNK/USDT
SPACING = 60
LEVELS = (2, 5, 10, 20)
Q96 = 2 ** 96
RPC_TIMEOUT = 30


def _pad_int(value, size=32):
    return value.to_bytes(size, "big", signed=True).hex()


def _signed(word):
    return int.from_bytes(bytes.fromhex(word), "big", signed=True)


async def _batch(client, endpoint, calls):
    """One JSON-RPC batch; returns results in order (None on per-item error)."""
    payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
                "params": [call, "latest"]} for i, call in enumerate(calls)]
    response = await client.post(endpoint, json=payload,
                                 headers={"Content-Type": "application/json"})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("RPC batch rejected")
    out = [None] * len(calls)
    for item in data:
        out[item["id"]] = item.get("result")
    return out


def _bitmap_positions(hex_word, base):
    bits = int(hex_word, 16)
    return [base + bit for bit in range(256) if bits >> bit & 1]


def _tick_sqrt(pos):
    # sqrt(1.0001^(tick/2)) scaled by 2^96, float precision is ample here.
    return (1.0001 ** ((pos * SPACING) / 2)) * Q96


def _integrate(net, pos_now, active, sqrt, up_sqrt, dn_sqrt, price):
    """Exact token amounts: integrate piecewise-constant L between ticks."""
    usdt_up = 0.0
    walked = active
    edge = sqrt
    for pos in sorted(p for p in net if p > pos_now):
        boundary = _tick_sqrt(pos)
        if boundary >= up_sqrt:
            usdt_up += walked * (up_sqrt - edge) / Q96
            edge = up_sqrt
            break
        if boundary > edge:
            usdt_up += walked * (boundary - edge) / Q96
            edge = boundary
        walked += net[pos]
    if edge < up_sqrt:
        usdt_up += walked * (up_sqrt - edge) / Q96

    wgnk_dn = 0.0
    walked = active
    edge = sqrt
    for pos in sorted((p for p in net if p <= pos_now), reverse=True):
        boundary = _tick_sqrt(pos)
        if boundary <= dn_sqrt:
            wgnk_dn += walked * Q96 * (edge - dn_sqrt) / (edge * dn_sqrt)
            edge = dn_sqrt
            break
        if boundary < edge:
            wgnk_dn += walked * Q96 * (edge - boundary) / (edge * boundary)
            edge = boundary
        walked -= net[pos]
    if edge > dn_sqrt:
        wgnk_dn += walked * Q96 * (edge - dn_sqrt) / (edge * dn_sqrt)

    return {"up_usdt_raw": str(int(usdt_up)),        # USDT, 6 decimals
            "down_wgnk_raw": str(int(wgnk_dn)),      # WGNK, 9 decimals
            "down_usdt_raw": str(int(wgnk_dn * price / 1e3))}


async def snapshot(endpoints):
    """Exact per-level cost of moving the 30 b.p. pool price up and down."""
    async with httpx.AsyncClient(timeout=RPC_TIMEOUT) as client:
        endpoint = endpoints[0]
        slot0, liquidity = (await _batch(client, endpoint, [
            {"to": POOL, "data": "0x3850c7bd"},
            {"to": POOL, "data": "0x1a686502"}]))[:2]
        if not slot0 or not liquidity:
            raise ValueError("slot0/liquidity call failed")
        sqrt = int(slot0[2:66], 16)
        active = int(liquidity, 16)
        tick = _signed(slot0[66:130])

        span = 1920  # ticks for +/-20% (log(1.2)/log(1.0001) ~ 1823) plus air
        lo_pos = (tick - span) // SPACING
        hi_pos = (tick + span) // SPACING
        words = sorted({p >> 8 for p in range(lo_pos - 1, hi_pos + 2)})
        results = await _batch(client, endpoint, [
            {"to": POOL, "data": "0x5339c296" + _pad_int(w)} for w in words])
        positions = set()
        for word, result in zip(words, results):
            if result and result != "0x" and len(result) > 2:
                positions.update(_bitmap_positions(result[2:], word * 256))

        ticks = sorted(p for p in positions if lo_pos <= p <= hi_pos)
        net = {}
        if ticks:
            results = await _batch(client, endpoint, [
                {"to": POOL, "data": "0xf30dba93" + _pad_int(p * SPACING)}
                for p in ticks])
            for pos, result in zip(ticks, results):
                if result and len(result) >= 130:
                    net[pos] = _signed(result[66:130])  # liquidityNet

        price = (sqrt / Q96) ** 2 * 1e3  # USDT per WGNK
        bands = {str(level): _integrate(net, tick // SPACING, active,
                                        sqrt, sqrt * (1 + level / 100) ** .5,
                                        sqrt * (1 - level / 100) ** .5, price)
                 for level in LEVELS}
        return {"ts": int(time.time()), "price": price, "bands": bands}
