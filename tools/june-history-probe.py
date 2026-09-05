"""Read-only archive capability probe for the requested June 2026 history."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.sources import Sources
from app.config import Settings
from app.codec import parse_native

async def main():
    net=Sources(Settings())
    async def native(base,root):
        try:
            r=await net.client.get(base+root+"/status")
            r.raise_for_status()
            status=r.json()["result"]
            print("STATUS",base,json.dumps({"chain":status["node_info"]["network"],
                **status["sync_info"]}),flush=True)
            replies=[]
            for method in ("block","block_results"):
                r=await net.client.post(base+root+"/",json={"jsonrpc":"2.0","id":1,
                    "method":method,"params":{"height":"4400000"}})
                r.raise_for_status()
                data=r.json()
                if data.get("error"):
                    print("ARCHIVE",base,method,json.dumps(data["error"]),flush=True)
                    return
                replies.append(data)
            block,events=parse_native(replies[0],replies[1],{})
            print("ARCHIVE_OK",base,block,"money_events",len(events),flush=True)
        except Exception as exc:
            print("NATIVE_ERROR",base,type(exc).__name__,str(exc)[:180],flush=True)
    try:
        await native("https://rpc.gonka.gg","/chain-rpc")
    finally:
        await net.close()
asyncio.run(main())
