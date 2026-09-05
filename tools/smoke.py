"""Read-only integration check against live sources; stores only public chain data."""
import asyncio
from app.config import Settings
from app.db import Database
from app.indexer import Indexer
from app.analytics import bridges

async def run():
    d = Database("data/gonka-flow.sqlite3")
    i = Indexer(Settings(),d)
    status = await i.net.rpc("status")
    await i.native_setup(int(status["sync_info"]["latest_block_height"]))
    for lo,hi in [(5916917,5916922),(5916446,5916446)]:
        await i.native_batch(lo,hi)
        print("Indexed verified block batch",lo,hi,flush=True)
    print("Locks:",d.events(kind="bridge_lock")["total"])
    print("Matched bridge routes:",sum(b["status"]=="completed" for b in bridges(d,168)["items"]))
    await i.net.close()
    d.close()

if __name__=="__main__":
    asyncio.run(run())
