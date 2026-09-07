"""Bounded read-only probes of published public RPCs; no account or paid API."""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.sources import Sources
from app.config import Settings,TOKEN,SEED_POOLS
from app.codec import TRANSFER,SWAP,MINT,BURN,LP_MINT,LP_BURN,kh

async def probe(base):
    net=Sources(Settings())
    async def call(method,params):
        before=time.monotonic()
        r=await net.client.post(base,json={"jsonrpc":"2.0","id":1,"method":method,"params":params})
        try: data=r.json()
        except ValueError: data={"error":r.text[:500]}
        if r.status_code!=200 or data.get("error"):
            raise ValueError(json.dumps({"status":r.status_code,"reply":data})[:900])
        return data["result"],round(time.monotonic()-before,3)
    try:
        chain,_=await call("eth_chainId",[])
        if int(chain,16)!=1: raise ValueError("not Ethereum mainnet")
        await asyncio.sleep(1.1)
        block,seconds=await call("eth_getBlockByNumber",[hex(25260902),False])
        if block["hash"].lower()!="0xbea109d1f800811b170cef44c7b81c71f535f1461a2933c6169b9da9374ff950":
            raise ValueError("historical canonical block mismatch")
        await asyncio.sleep(1.1)
        logs,log_seconds=await call("eth_getLogs",[{"fromBlock":hex(25380000),"toBlock":hex(25380999),
            "address":[TOKEN]+SEED_POOLS,"topics":[[TRANSFER,SWAP,MINT,BURN,LP_MINT,LP_BURN]]}])
        identity=sorted([(l["blockHash"],l["transactionHash"],l["logIndex"],l["address"],l["data"],l["topics"]) for l in logs])
        await asyncio.sleep(1.1)
        tag="finalized" if "--recent-state" in sys.argv else hex(25260902)
        supply,_=await call("eth_call",[{"to":TOKEN,"data":kh("totalSupply()")[:10]},tag])
        receipt_ok=None
        if logs:
            await asyncio.sleep(1.1)
            receipt,_=await call("eth_getTransactionReceipt",[logs[0]["transactionHash"]])
            receipt_ok=receipt["blockHash"]==logs[0]["blockHash"]
            if not receipt_ok: raise ValueError("receipt block mismatch")
        print(json.dumps({"rpc":base,"ok":True,"block_seconds":seconds,"logs_seconds":log_seconds,
            "logs":len(logs),"log_digest":hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),
            "state_block":tag,"supply":str(int(supply,16)),"receipt_ok":receipt_ok}),flush=True)
    except Exception as e:
        print(json.dumps({"rpc":base,"ok":False,"error":type(e).__name__+": "+str(e)}),flush=True)
    finally: await net.close()

async def main():
    urls=[arg for arg in sys.argv[1:] if arg!="--recent-state"]
    await asyncio.gather(*(probe(url) for url in (urls or ["https://eth.merkle.io","https://eth.llamarpc.com"])))
asyncio.run(main())
