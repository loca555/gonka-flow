"""Exact timestamp boundaries and coverage of the requested archive, not min/max guesses."""
import argparse
import asyncio
import json
from datetime import datetime


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.microsecond:
        raise ValueError("HISTORY_FROM must include a timezone and whole seconds")
    return int(parsed.timestamp())


def native_header(reply, height):
    data = reply["result"]
    h = data["block"]["header"]
    if h["chain_id"] != "gonka-mainnet" or int(h["height"]) != height:
        raise ValueError("Unexpected native archive block")
    return {"height": height, "hash": data["block_id"]["hash"],
            "ts": int(datetime.fromisoformat(h["time"].replace("Z", "+00:00")).timestamp())}


async def boundary_proof(get_block, height, since):
    first = await get_block(height)
    previous = await get_block(height - 1) if height > 1 else None
    if first["ts"] < since or (previous and previous["ts"] >= since):
        raise ValueError("Archive start height does not match HISTORY_FROM")
    return {"since": since, "first": first, "previous": previous}


async def first_block(get_block, lo, hi, since):
    """Interpolation selects probes only; adjacent timestamps prove the result."""
    cache = {}
    async def read(h):
        if h not in cache:
            cache[h] = await get_block(h)
        return cache[h]
    left, right = await read(lo), await read(hi)
    if right["ts"] < since:
        raise ValueError("Requested date is after the chain head")
    if left["ts"] >= since:
        if lo != 1:
            raise ValueError("Provider has pruned the requested date")
        return await boundary_proof(read, lo, since)
    attempts = 0
    while hi - lo > 1:
        attempts += 1
        # Revert to bisection regularly to guarantee bounded progress on uneven block times.
        guess = lo + (since-left["ts"]) * (hi-lo) // max(1,right["ts"]-left["ts"])
        mid = (lo+hi)//2 if attempts % 3 == 0 else max(lo+1,min(hi-1,guess))
        block = await read(mid)
        if not left["ts"] <= block["ts"] <= right["ts"]:
            raise ValueError("Non-monotonic archive timestamps")
        if block["ts"] >= since:
            hi, right = mid, block
        else:
            lo, left = mid, block
    return await boundary_proof(read, hi, since)


def archive_progress(db, chain):
    proof = db.get("history_boundary:" + chain)
    status = db.get("status:" + chain, {})
    request = db.get("history_request")
    if not request or not proof or proof["since"] != request["since"]:
        proof = None
    target = proof["first"]["height"] if proof else db.get(chain + "_target")
    head = status.get("finalized_height" if chain == "ethereum" else "remote_height")
    history_status = db.get("status:" + chain + "_history", {})
    result = {"requested_from": request, "boundary_verified": bool(proof),
              "target_height": target, "head_height": head, "first_block": proof["first"] if proof else None,
              "covered_blocks": 0, "total_blocks": None, "missing_blocks": None,
              "complete": False, "error": history_status.get("error"),
              "checked_at": history_status.get("checked_at"),
              "retry_at": history_status.get("retry_at"), "rpc_category": history_status.get("rpc_category")}
    if target is None or head is None or head < target:
        return result
    intervals = db.conn.execute("SELECT lo,hi FROM ranges WHERE chain=? AND hi>=? AND lo<=?",
                               (chain, target, head)).fetchall()
    covered = sum(min(head, r["hi"]) - max(target, r["lo"]) + 1 for r in intervals)
    total = head - target + 1
    result.update(covered_blocks=covered, total_blocks=total, missing_blocks=total-covered,
                  complete=covered==total and (not request or bool(proof)))
    return result


def period_coverage(db, chain, since):
    status = db.get("status:" + chain, {})
    head = status.get("finalized_height" if chain == "ethereum" else "remote_height")
    proof = db.get("history_boundary:" + chain)
    if not head:
        return False
    for r in db.conn.execute("SELECT lo,hi FROM ranges WHERE chain=? AND hi>=?", (chain, head)):
        start = db.conn.execute("SELECT ts FROM blocks WHERE chain=? AND height=?", (chain,r["lo"])).fetchone()
        if start and start[0] <= since:
            return True
        if proof and proof["since"] == since and r["lo"] <= proof["first"]["height"]:
            return True
    return False


async def resolve(value):
    from .config import Settings
    from .sources import Sources
    since = timestamp(value)
    # Official archive node; the community live RPC is pruned to roughly 100k blocks.
    net = Sources(Settings(gonka_rpc=[], gonka=["https://node2.gonka.ai:8443", "https://node1.gonka.ai:8443"]))
    try:
        async def gonka(h):
            return native_header(await net.native("/chain-rpc/block", {"height": str(h)}), h)
        async def ethereum(h):
            b = await net.eth("eth_getBlockByNumber", [hex(h), False], archive=True)
            if int(b["number"],16) != h:
                raise ValueError("Unexpected Ethereum archive block")
            return {"height": h, "hash": b["hash"], "ts": int(b["timestamp"],16)}
        async def native_plan():
            status = await net.rpc("status")
            sync = status["sync_info"]
            proof = await first_block(gonka, int(sync["earliest_block_height"]), int(sync["latest_block_height"]), since)
            print("GONKA", json.dumps(proof), flush=True)
        async def eth_plan():
            head = await net.eth("eth_getBlockByNumber", ["finalized", False])
            proof = await first_block(ethereum, 1, int(head["number"],16), since)
            print("ETHEREUM", json.dumps(proof), flush=True)
        await asyncio.gather(native_plan(), eth_plan())
    finally:
        await net.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only exact start-block resolver; no database writes")
    parser.add_argument("--from", dest="start", required=True, help="ISO timestamp with explicit timezone")
    asyncio.run(resolve(parser.parse_args().start))
