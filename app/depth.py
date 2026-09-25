"""Exact Uniswap V3 executable depth by walking tick liquidity.

Bands use the average-execution-rate semantics: level N answers "how much
USDT can buy WGNK until the *average* rate paid (pool fee included) is +N%
over the pool price" and "how much WGNK can be sold (USDT proceeds shown)
until the average rate received is -N%". Real V3 liquidity is piecewise
constant (manual LP ranges), so the endpoint of each band is found by
integrating L over ticks; the average rate is the volume-weighted ratio of
both sides. The 0.3% pool fee is charged on the input token, so trader
amounts are the curve amounts grossed up by 1/(1-fee). Cached in kv
`depth:live`.
"""
import time

import httpx

POOL = "0x203ee836d417cf944133bbdd2c62b4bc7388c55d"  # 30 b.p. WGNK/USDT
SPACING = 60
LEVELS = (2, 5, 10, 20)
FEE = 0.003
Q96 = 2 ** 96
RPC_TIMEOUT = 30
BATCH = 100


def _pad_int(value, size=32):
    return value.to_bytes(size, "big", signed=True).hex()


def _signed(word):
    return int.from_bytes(bytes.fromhex(word), "big", signed=True)


async def _batched(client, endpoints, calls):
    """Chunked JSON-RPC batch; every chunk tries the endpoints in order.

    Public providers cap batch sizes (blockpi: 5), so a rejected chunk
    falls through to the next endpoint instead of failing the worker.
    """
    results = []
    for start in range(0, len(calls), BATCH):
        chunk = calls[start:start + BATCH]
        payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
                    "params": [call, "latest"]} for i, call in enumerate(chunk)]
        last = None
        for endpoint in endpoints:
            try:
                response = await client.post(endpoint, json=payload,
                                             headers={"Content-Type": "application/json"})
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, list):
                    raise ValueError("RPC batch rejected")
                out = [None] * len(chunk)
                for item in data:
                    out[item["id"]] = item.get("result")
                results.extend(out)
                last = None
                break
            except Exception as error:
                last = error
        if last is not None:
            raise last
    return results


def _bitmap_positions(hex_word, base):
    bits = int(hex_word, 16)
    return [base + bit for bit in range(256) if bits >> bit & 1]


def _tick_sqrt(pos):
    # sqrt(1.0001^(tick/2)) scaled by 2^96, float precision is ample here.
    return (1.0001 ** ((pos * SPACING) / 2)) * Q96


def _amounts_up(net, pos_now, active, sqrt, s_end, order=None):
    """(usdt_in, wgnk_out) for buying WGNK until the price reaches s_end."""
    usdt = wgnk = 0.0
    walked = active
    edge = sqrt
    for pos in (order if order is not None else sorted(p for p in net if p > pos_now)):
        boundary = _tick_sqrt(pos)
        if boundary >= s_end:
            break
        if boundary > edge:
            usdt += walked * (boundary - edge) / Q96
            wgnk += walked * Q96 * (boundary - edge) / (edge * boundary)
            edge = boundary
        walked += net[pos]
    usdt += walked * (s_end - edge) / Q96
    wgnk += walked * Q96 * (s_end - edge) / (edge * s_end)
    return usdt, wgnk


def _amounts_down(net, pos_now, active, sqrt, s_end, order=None):
    """(usdt_out, wgnk_in) for selling WGNK until the price reaches s_end."""
    usdt = wgnk = 0.0
    walked = active
    edge = sqrt
    for pos in (order if order is not None else
                sorted((p for p in net if p <= pos_now), reverse=True)):
        boundary = _tick_sqrt(pos)
        if boundary <= s_end:
            break
        if boundary < edge:
            usdt += walked * (edge - boundary) / Q96
            wgnk += walked * Q96 * (edge - boundary) / (edge * boundary)
            edge = boundary
        walked -= net[pos]
    usdt += walked * (edge - s_end) / Q96
    wgnk += walked * Q96 * (edge - s_end) / (edge * s_end)
    return usdt, wgnk


def _solve_avg(net, pos_now, active, sqrt, price, level, up):
    """Endpoint where the trader's average rate hits price*(1 +/- level%).

    The fee is taken from the swap input, so the curve itself only needs to
    deliver average price*(1+level)*(1-FEE) on a buy and price*(1-level)/(1-FEE)
    on a sell. The average rises monotonically with the endpoint (every extra
    unit trades at a marginal rate beyond the current average), so bisection
    is exact. Returns None when the range walked cannot absorb that much.
    """
    x = level / 100
    mult = 1 + x if up else 1 - x
    target = price * mult * ((1 - FEE) if up else 1 / (1 - FEE))
    # A uniform-liquidity pool reaches the target exactly at price*mult^2;
    # concentrate the search slightly beyond in case nearby liquidity is thin.
    far = sqrt * (mult * mult * 1.05 if up else mult * mult * 0.95)
    order = (sorted(p for p in net if p > pos_now) if up else
             sorted((p for p in net if p <= pos_now), reverse=True))

    def avg(s):
        usdt, wgnk = (_amounts_up if up else _amounts_down)(
            net, pos_now, active, sqrt, s, order)
        return usdt * 1e3 / wgnk if wgnk > 0 else 0.0

    far_avg = avg(far)
    if (up and far_avg < target) or (not up and far_avg > target):
        return None
    lo, hi = (sqrt, far) if up else (far, sqrt)
    for _ in range(40):
        mid = (lo + hi) / 2
        if avg(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _band(net, pos_now, active, sqrt, price, level):
    band = {}
    for up in (True, False):
        end = _solve_avg(net, pos_now, active, sqrt, price, level, up)
        if end is None:
            continue
        usdt, wgnk = (_amounts_up if up else _amounts_down)(net, pos_now, active, sqrt, end)
        if up:
            band.update(up_usdt_raw=str(int(usdt / (1 - FEE))),   # USDT spent incl. fee
                        up_wgnk_raw=str(int(wgnk)),               # WGNK bought, 9 decimals
                        up_final_price=(end / Q96) ** 2 * 1e3)
        else:
            band.update(down_usdt_raw=str(int(usdt)),             # USDT proceeds, 6 decimals
                        down_wgnk_raw=str(int(wgnk / (1 - FEE))), # WGNK sold incl. fee
                        down_final_price=(end / Q96) ** 2 * 1e3)
    return band


async def snapshot(endpoints):
    """Per-level executable amounts at an average rate of +/- LEVELS percent."""
    async with httpx.AsyncClient(timeout=RPC_TIMEOUT) as client:
        slot0, liquidity = (await _batched(client, endpoints, [
            {"to": POOL, "data": "0x3850c7bd"},
            {"to": POOL, "data": "0x1a686502"}]))[:2]
        if not slot0 or not liquidity:
            raise ValueError("slot0/liquidity call failed")
        sqrt = int(slot0[2:66], 16)
        active = int(liquidity, 16)
        tick = _signed(slot0[66:130])

        span = 5100  # ticks: a -20% average-rate band ends near -39% price
        lo_pos = (tick - span) // SPACING
        hi_pos = (tick + span) // SPACING
        words = sorted({p >> 8 for p in range(lo_pos - 1, hi_pos + 2)})
        results = await _batched(client, endpoints, [
            {"to": POOL, "data": "0x5339c296" + _pad_int(w)} for w in words])
        positions = set()
        for word, result in zip(words, results):
            if result and result != "0x" and len(result) > 2:
                positions.update(_bitmap_positions(result[2:], word * 256))

        ticks = sorted(p for p in positions if lo_pos <= p <= hi_pos)
        net = {}
        if ticks:
            results = await _batched(client, endpoints, [
                {"to": POOL, "data": "0xf30dba93" + _pad_int(p * SPACING)}
                for p in ticks])
            for pos, result in zip(ticks, results):
                if result and len(result) >= 130:
                    net[pos] = _signed(result[66:130])  # liquidityNet

        price = (sqrt / Q96) ** 2 * 1e3  # USDT per WGNK
        bands = {str(level): _band(net, tick // SPACING, active, sqrt, price, level)
                 for level in LEVELS}
        return {"ts": int(time.time()), "price": price, "bands": bands}
