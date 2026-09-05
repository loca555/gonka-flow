"""Cross-check a small sample of indexed balances against pinned public RPC state."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.sources import Sources
from app.config import Settings,TOKEN

async def main():
    net=Sources(Settings())
    try:
        for asset in ("GNK","WGNK"):
            result=await net.get("http://127.0.0.1:8790/api/holders",
                                 {"asset":asset,"minimum":10000,"limit":2})
            assert len(result["items"])==2
            for row in result["items"]:
                if asset=="WGNK":
                    value=int(await net.call(TOKEN,"balanceOf(address)",row["address"][2:].zfill(64),
                                             tag=hex(row["height"])),16)
                else:
                    data,height=await net.api("/cosmos/bank/v1beta1/balances/"+row["address"]+"/by_denom",
                        {"denom":"ngonka"},height=row["height"],with_height=True)
                    assert height==row["height"]
                    value=int(data["balance"]["amount"])
                assert value==int(row["balance_raw"]),(asset,row["address"],row["balance_raw"],value)
                print(asset,row["address"],"block",row["height"],"EXACT MATCH",flush=True)
    finally:
        await net.close()
asyncio.run(main())
