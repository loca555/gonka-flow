"""One small read-only JSON-RPC batch per endpoint. Does not modify the index."""
import asyncio
import json
import sys
import time
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.codec import parse_native


async def check(url):
    heights = [4448701, 5100000, 5700000]
    payload = [{"jsonrpc": "2.0", "id": f"{method}:{h}", "method": method, "params": {"height": str(h)}}
               for h in heights for method in ("block", "block_results")]
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=35, headers={"User-Agent":"GonkaFlow/0.1 read-only archive check"}) as client:
            reply = await client.post(url, json=payload)
            reply.raise_for_status()
            rows = reply.json()
            if not isinstance(rows, list):
                raise ValueError("Batch rejected: " + str(rows)[:300])
            by_id = {row["id"]: row for row in rows}
            if len(by_id) != len(rows) or set(by_id) != {row["id"] for row in payload}:
                raise ValueError("Missing/duplicate/unexpected batch response IDs")
            blocks = []
            for h in heights:
                a, b = (by_id[f"{m}:{h}"] for m in ("block", "block_results"))
                if a.get("error") or b.get("error"):
                    raise ValueError(str(a.get("error") or b.get("error"))[:300])
                block, events = parse_native(a, b, {})
                if block["height"] != h:
                    raise ValueError("Wrong block height")
                if h == 4448701 and block["hash"] != "54A98C71211D8DA39F97A1AA78045EF31BE4C94CE8365FBAD2CDB97CC9C291B0":
                    raise ValueError("June boundary hash mismatch")
                blocks.append({**block, "events":len(events), "txs":len(a["result"]["block"]["data"].get("txs") or [])})
            print(json.dumps({"url":url,"ok":True,"seconds":round(time.monotonic()-started,2),
                              "bytes":len(reply.content),"blocks":blocks}), flush=True)
    except Exception as error:
        print(json.dumps({"url":url,"ok":False,"seconds":round(time.monotonic()-started,2),
                          "error":type(error).__name__+": "+str(error)[:400]}), flush=True)


async def main():
    for url in sys.argv[1:] or ["https://node1.gonka.ai:8443/chain-rpc/", "https://node2.gonka.ai:8443/chain-rpc/",
                              "https://node3.gonka.ai/chain-rpc/"]:
        await check(url)


asyncio.run(main())
