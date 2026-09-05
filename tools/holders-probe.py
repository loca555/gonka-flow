"""Read-only endpoint discovery; not part of the production collectors."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.sources import Sources
from app.config import Settings, TOKEN
from app.codec import TRANSFER

async def main():
    net = Sources(Settings())
    try:
        async def native():
            for base in net.settings.gonka[:2]:
                try:
                    r = await net.client.get(base + "/chain-api/cosmos/bank/v1beta1/denom_owners/ngonka",
                        params={"pagination.limit": "2", "pagination.count_total": "true"})
                    print("GNK", base, r.status_code, dict(r.headers), r.text[:1500], flush=True)
                except Exception as exc:
                    print("GNK error", base, type(exc).__name__, str(exc)[:160], flush=True)
        async def eth():
            final = await net.eth("eth_getBlockByNumber", ["finalized", False])
            height = int(final["number"], 16)
            logs = await net.eth("eth_getLogs", [{"address": TOKEN, "fromBlock": hex(height-9999),
                "toBlock": hex(height), "topics": [TRANSFER]}])
            print("ETH logs 10k", height, len(logs), json.dumps(logs[:1]), flush=True)
            print("ETH code at block 24000000", str(await net.eth("eth_getCode", [TOKEN, hex(24000000)]))[:90], flush=True)
        await asyncio.gather(native(), eth())
    finally:
        await net.close()
asyncio.run(main())
