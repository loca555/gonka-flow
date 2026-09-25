"""WGNK monitor with targeted native bridge/address enrichment; no full Gonka scan."""
import asyncio
import os
import json
import logging
import re
import time
from collections import defaultdict
from .codec import MINT, TRANSFER
from .config import TOKEN, ZERO, SEED_POOLS
from .db import tokens
from .sources import Sources
from .timezones import TIME_ZONE, local_day

log = logging.getLogger("gonka-flow.mints")
SCOPE = "wgnk_mints"
HEX32 = re.compile(r"0x[0-9a-fA-F]{64}")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
FIELDS = "tx_hash log_index height block_hash ts recipient amount_raw request_id epoch_id finalized source".split()


def initialize(db):
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS wgnk_mints(
            tx_hash TEXT NOT NULL, log_index INTEGER NOT NULL, height INTEGER NOT NULL,
            block_hash TEXT NOT NULL, ts INTEGER NOT NULL, recipient TEXT NOT NULL,
            amount_raw TEXT NOT NULL, request_id TEXT NOT NULL, epoch_id TEXT NOT NULL,
            finalized INTEGER NOT NULL, source TEXT NOT NULL,
            PRIMARY KEY(tx_hash,log_index));
        CREATE INDEX IF NOT EXISTS wgnk_mints_time ON wgnk_mints(ts DESC,height DESC,log_index DESC);
        CREATE INDEX IF NOT EXISTS wgnk_mints_recipient ON wgnk_mints(recipient,ts DESC);
    """)
    from .provenance import initialize as initialize_provenance
    initialize_provenance(db)
    from .powder import initialize as initialize_powder
    initialize_powder(db)


def validate_mint(e):
    if (not HEX32.fullmatch(e["tx_hash"]) or not HEX32.fullmatch(e["block_hash"])
            or not HEX32.fullmatch(e["request_id"]) or not ADDRESS.fullmatch(e["recipient"])
            or e["recipient"] == ZERO or e["height"] < 1 or e["log_index"] < 0
            or not 0 < int(e["amount_raw"]) < 2**256 or not 0 <= int(e["epoch_id"]) < 2**64):
        raise ValueError("Invalid WGNK mint")
    if str(int(e["amount_raw"])) != e["amount_raw"] or str(int(e["epoch_id"])) != e["epoch_id"]:
        raise ValueError("Non-canonical mint integer")


def decode_mint(item, block, finalized):
    topics = item.get("topics", [])
    if (item.get("address", "").lower() != TOKEN or item.get("removed") or len(topics) != 4
            or topics[0].lower() != MINT or not all(HEX32.fullmatch(t) for t in topics)
            or not re.fullmatch(r"0x0{24}[0-9a-fA-F]{40}", topics[3])
            or not HEX32.fullmatch(item.get("data", ""))):
        raise ValueError("Malformed WGNKMinted log")
    if int(item["blockNumber"], 16) != block["height"] or item["blockHash"].lower() != block["hash"]:
        raise ValueError("Mint log disagrees with block")
    e = {"tx_hash":item["transactionHash"].lower(), "log_index":int(item["logIndex"],16),
         "height":block["height"], "block_hash":block["hash"], "ts":block["ts"],
         "recipient":"0x"+topics[3][-40:].lower(), "amount_raw":str(int(item["data"],16)),
         "request_id":topics[2].lower(), "epoch_id":str(int(topics[1],16)),
         "finalized":int(finalized), "source":"ethereum_rpc"}
    validate_mint(e)
    return e


def save_batch(db, lo, hi, events, blocks, finalized=True, next_height=None):
    if lo > hi:
        return
    identities=set()
    for e in events:
        validate_mint(e)
        identity=(e["tx_hash"],e["log_index"])
        if not lo<=e["height"]<=hi or identity in identities or bool(e["finalized"]) != finalized:
            raise ValueError("Invalid/duplicate mint in batch")
        identities.add(identity)
    with db.conn:
        if finalized:
            # A different response for an already indexed final range must not erase confirmed mints.
            old=db.conn.execute("SELECT * FROM wgnk_mints WHERE finalized=1 AND height BETWEEN ? AND ?",(lo,hi)).fetchall()
            incoming={(e["tx_hash"],e["log_index"]):e for e in events}
            for row in old:
                replacement=incoming.get((row["tx_hash"],row["log_index"]))
                if not replacement or any(str(row[k])!=str(replacement[k]) for k in FIELDS if k!="source"):
                    raise ValueError("Finalized mint conflict; refusing replacement")
            db.conn.execute("DELETE FROM wgnk_mints WHERE height BETWEEN ? AND ?",(lo,hi))
        else:
            db.conn.execute("DELETE FROM wgnk_mints WHERE finalized=0")
        for b in blocks:
            old=db.conn.execute("SELECT hash FROM blocks WHERE chain='ethereum' AND height=?",(b["height"],)).fetchone()
            if old and old[0]!=b["hash"]:
                raise ValueError("Finalized Ethereum block conflict")
            if finalized:
                db.conn.execute("INSERT OR IGNORE INTO blocks VALUES('ethereum',?,?,?)",(b["height"],b["hash"],b["ts"]))
        for e in events:
            old=db.conn.execute("SELECT finalized FROM wgnk_mints WHERE tx_hash=? AND log_index=?",
                                (e["tx_hash"],e["log_index"])).fetchone()
            if old and old[0] and not finalized:
                raise ValueError("Cannot overwrite a finalized mint with a provisional one")
            db.conn.execute(f"INSERT OR REPLACE INTO wgnk_mints VALUES({','.join('?' for _ in FIELDS)})",[e[k] for k in FIELDS])
        if finalized:
            # This scope is intentionally NOT the old all-events Ethereum coverage.
            db._range(SCOPE,lo,hi)
            if next_height is not None:
                db.conn.execute("INSERT OR REPLACE INTO kv VALUES('mints:next',?)",(json.dumps(next_height),))
    db.revision+=1


def bootstrap(db, deployment, head):
    """Reuse only final, verified local Ethereum ranges that already included WGNKMinted."""
    if db.get("mints:bootstrapped"):
        return
    if not db.get("ethereum_full_history") or db.get("holders:WGNK:deployment") != deployment:
        db.put("mints:bootstrapped",{"imported":0})
        return
    count=0
    for interval in list(db.conn.execute("SELECT lo,hi FROM ranges WHERE chain='ethereum' ORDER BY lo")):
        lo,hi=max(deployment,interval["lo"]),min(head,interval["hi"])
        if lo>hi:
            continue
        events=[]
        for row in db.conn.execute("""SELECT * FROM events WHERE chain='ethereum' AND kind='bridge_mint'
                                     AND finalized=1 AND height BETWEEN ? AND ?""",(lo,hi)):
            meta=json.loads(row["meta"])
            if meta.get("contract") != TOKEN or meta.get("topic") != MINT:
                raise ValueError("Unverified legacy mint")
            block=db.conn.execute("SELECT hash,ts FROM blocks WHERE chain='ethereum' AND height=?",(row["height"],)).fetchone()
            if not block or block["hash"]!=row["block_hash"] or block["ts"]!=row["ts"]:
                raise ValueError("Legacy mint has no matching final block")
            epoch=meta["epoch"]
            events.append({"tx_hash":row["tx_hash"],"log_index":row["idx"],"height":row["height"],
                "block_hash":row["block_hash"],"ts":row["ts"],"recipient":row["dst"],
                "amount_raw":row["amount_raw"],"request_id":row["request_key"],
                "epoch_id":str(int(epoch,16) if isinstance(epoch,str) and epoch.startswith("0x") else int(epoch)),
                "finalized":1,"source":"verified_local_ethereum_archive"})
        save_batch(db,lo,hi,events,[],True)
        count+=len(events)
    db.put("mints:bootstrapped",{"imported":count,"at":int(time.time())})


def progress(db):
    deployment=db.get("mints:deployment")
    status=db.get("mints:status",{})
    history=db.get("mints:history",{})
    head=status.get("finalized_height")
    ranges=[dict(r) for r in db.conn.execute("SELECT lo,hi FROM ranges WHERE chain=? ORDER BY lo",(SCOPE,))]
    result={"deployment":deployment,"head":head,"covered":0,"total":None,"missing":None,
            "complete":False,"ranges":ranges,"live":status,"history":history}
    if deployment and head is not None and head>=deployment["height"]:
        start=deployment["height"]
        total=head-start+1
        covered=sum(max(0,min(head,r["hi"])-max(start,r["lo"])+1) for r in ranges)
        result.update(covered=covered,total=total,missing=total-covered,complete=total==covered)
    return result


def gap(db, lo, hi):
    for row in db.conn.execute("SELECT lo,hi FROM ranges WHERE chain=? AND hi>=? ORDER BY lo",(SCOPE,lo)):
        if row["lo"]>lo:
            return lo,min(hi,row["lo"]-1)
        lo=max(lo,row["hi"]+1)
        if lo>hi:
            return None
    return (lo,hi) if lo<=hi else None


class MintIndexer:
    def __init__(self,settings,db):
        self.cfg,self.db=settings,db
        initialize(db)
        self.net=Sources(settings)
        self.tasks=[]
        self.ready=asyncio.Event()
        self.cache={}
        from .flows import FlowCollector
        self.flows=FlowCollector(self)
        from .provenance import ProvenanceCollector
        self.provenance=ProvenanceCollector(self)
        from .public_holders import NativeHolders
        self.holders=NativeHolders(self)
        from .gnk_holder_history import HolderHistory
        self.holder_history=HolderHistory(self)
        from .powder import PowderCollector
        self.powder=PowderCollector(self)

    async def coingecko(self):
        """CoinGecko edits WGNK circulating supply (vesting-unlocked GNK);
        the market cap follows that definition, refreshed hourly. Failures
        propagate: the runner loop records the exact provider error."""
        import httpx
        from decimal import Decimal
        async with httpx.AsyncClient(timeout=20,follow_redirects=True,
                headers={"accept":"application/json","user-agent":"gonka-flow/1.0"}) as client:
            response=await client.get("https://api.coingecko.com/api/v3/coins/wrapped-gonka")
            response.raise_for_status()
            data=response.json()
        circulating=(data.get("market_data") or {}).get("circulating_supply")
        if not circulating:
            raise ValueError("CoinGecko circulating supply is empty")
        raw=int((Decimal(str(circulating))*10**9).to_integral_value())
        self.db.put("coingecko:wgnk",{"circulating_raw":str(raw),"ts":int(time.time())})
        return 3600

    async def depth_ticks(self):
        """Exact depth via tick liquidity; see app.depth."""
        from . import depth
        snap = await depth.snapshot(self.cfg.ethereum)
        self.db.put("depth:live", snap)
        return 300

    async def lp_tick_history(self):
        """Historical tick liquidity from Mint/Burn events; see app.lp_history."""
        from . import lp_history
        await lp_history.sync(self.cfg, self.db)
        lp_history.rebuild_if_stale(self.db)
        return 300

    async def auto_snapshot(self):
        """Rolling crash-recovery snapshot; see app.snapshots."""
        from . import snapshots
        if not self.cfg.snapshot_token:
            return 3600
        last = self.db.get("snapshot:last") or {}
        if time.time() - last.get("ts", 0) < self.cfg.snapshot_hours * 3600:
            return 300
        snapshot = self.db.get("flow:snapshot") or {}
        if not snapshot.get("ledger_verified"):
            return 600
        if snapshot["height"] - last.get("height", 0) < self.cfg.snapshot_min_blocks:
            return 600
        height = await snapshots.publish_snapshot(self.cfg,
                self.cfg.data_dir / "gonka-flow.sqlite3", snapshot["height"])
        self.db.put("snapshot:last", {"height": height, "ts": int(time.time())})
        return 300

    async def network_weight(self):
        """Gonka network weight (GPU miner capacity) per epoch, from the
        public ranking.gonkadb snapshots. Backfills a few epochs per run."""
        import httpx
        from datetime import datetime, timedelta
        batch=max(1,int(os.getenv("NETWORK_WEIGHT_BATCH","8")))
        async with httpx.AsyncClient(timeout=30,follow_redirects=True,
                headers={"accept":"application/json","user-agent":"gonka-flow/1.0"}) as client:
            epochs=(await client.get("https://ranking.gonkadb.com/api/epochs")).json()["summary"]
            cached=self.db.get("network_weight:history") or {"items":{}}
            items=cached["items"]
            todo=[e for e in epochs if str(e["epoch_index"]) not in items][:batch]
            targets=todo+[e for e in epochs[:2] if e not in todo]
            for e in targets:
                try:
                    response=await client.get("https://ranking.gonkadb.com/api/snapshot",
                                              params={"epoch":e["epoch_index"]})
                    response.raise_for_status()
                    data=response.json()
                except Exception:
                    continue
                weight=(data.get("meta") or {}).get("total_network_weight")
                if weight is None:
                    continue
                gpus=sum(int(r.get("gpu_count") or 0) for r in data.get("rows") or [])
                stamp=datetime.fromisoformat(e["first_at"].replace("Z","+00:00"))+timedelta(hours=3)
                items[str(e["epoch_index"])]={"date":stamp.strftime("%Y-%m-%d"),
                                              "weight":int(weight),"gpus":gpus}
                await asyncio.sleep(.5)
            # AI tokens per epoch (billed prompt+completion), same source family.
            try:
                summary=(await client.get("https://ranking.gonkadb.com/api/inference/summary")).json()
                tokens={}
                for e in summary.get("epochs") or []:
                    stamp=datetime.fromisoformat(e["window"]["started_at"].replace("Z","+00:00"))+timedelta(hours=3)
                    tokens[str(e["epoch"])]={"date":stamp.strftime("%Y-%m-%d"),
                                             "tokens":int(e.get("total_prompt") or 0)+int(e.get("total_completion") or 0)}
                if tokens:
                    self.db.put("inference_tokens:history",{"items":tokens,"updated_at":int(time.time())})
            except Exception as error:
                self.status("network_weight:status",tokens_error=str(error)[:200])
        self.db.put("network_weight:history",{"items":items,"updated_at":int(time.time())})
        return 300

    def status(self,key,**values):
        self.db.put(key,{**self.db.get(key,{}),**values})

    async def loop(self,key,fn,interval):
        failures=0
        while True:
            try:
                delay=await fn()
                failures=0
                self.status(key,ok=True,error=None,retry_at=None,checked_at=int(time.time()))
                await asyncio.sleep(interval if delay is None else delay)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures+=1
                delay=max(min(120,8*2**min(failures-1,4)),getattr(error,"retry_after",0))
                self.status(key,ok=False,error=str(error)[:240],retry_at=int(time.time()+delay),
                            attempted_at=int(time.time()))
                log.warning("%s: %s",key,str(error)[:240])
                await asyncio.sleep(delay)

    def start(self):
        self.tasks=[asyncio.create_task(self.loop("coingecko:status",self.coingecko,60),name="coingecko_circulating"),
                    asyncio.create_task(self.loop("network_weight:status",self.network_weight,60),name="gonka_network_weight"),
                    asyncio.create_task(self.loop("snapshot:status",self.auto_snapshot,120),name="gonka_auto_snapshot"),
                    asyncio.create_task(self.loop("depth:status",self.depth_ticks,60),name="eth_depth_ticks"),
                    asyncio.create_task(self.loop("lp_ticks:status",self.lp_tick_history,60),name="eth_lp_tick_history"),
                    asyncio.create_task(self.loop("holder_history:status",self.holder_history.run,10),name="gonka_holder_history"),
                    asyncio.create_task(self.loop("public_holders:GNK:status",self.holders.run,60),name="gonka_holder_census"),
                    asyncio.create_task(self.loop("mints:status",self.live,12),name="wgnk_mints_live"),
                    asyncio.create_task(self.loop("mints:history",self.history,2),name="wgnk_mints_history"),
                    asyncio.create_task(self.loop("flow:status",self.flows.run,15),name="wgnk_market_flow"),
                    asyncio.create_task(self.loop("powder:status",self.powder.run,10),name="stable_powder"),
                    asyncio.create_task(self.loop("provenance:status",self.provenance.links,20),name="gonka_bridge_links"),
                    asyncio.create_task(self.loop("provenance:burns",self.provenance.burns,20),name="gonka_burn_links"),
                    asyncio.create_task(self.loop("provenance:incoming",self.provenance.incoming,5),name="gonka_targeted_incoming"),
                    asyncio.create_task(self.loop("provenance:balances",self.provenance.balances,3),name="gonka_targeted_balances")]

    async def stop(self):
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        await self.net.close()

    async def block(self,height,archive=False,finalized=True):
        if finalized and height in self.cache:
            return self.cache[height]
        b=await self.net.eth("eth_getBlockByNumber",[hex(height),False],archive=archive)
        if int(b["number"],16)!=height or not HEX32.fullmatch(b["hash"]):
            raise ValueError("Invalid Ethereum block")
        result={"height":height,"hash":b["hash"].lower(),"ts":int(b["timestamp"],16)}
        if finalized:
            if len(self.cache)>2500: self.cache.clear()
            self.cache[height]=result
        return result

    async def setup(self,head):
        if int(await self.net.call(TOKEN,"decimals()"),16)!=9:
            raise ValueError("Unexpected WGNK decimals")
        saved=self.db.get("mints:deployment")
        candidate=(saved or {}).get("height") or self.db.get("holders:WGNK:deployment")
        if not candidate:
            if await self.net.eth("eth_getCode",[TOKEN,hex(head)])=="0x":
                raise ValueError("WGNK contract absent")
            lo,hi=0,head
            while lo+1<hi:
                mid=(lo+hi)//2
                code=await self.net.eth("eth_getCode",[TOKEN,hex(mid)],archive=True)
                if code=="0x": lo=mid
                else: hi=mid
            candidate=hi
        first=await self.block(candidate,archive=True)
        if saved and saved["hash"]!=first["hash"]:
            raise ValueError("Deployment block hash changed")
        before,after=await asyncio.gather(
            self.net.eth("eth_getCode",[TOKEN,hex(candidate-1)],archive=True),
            self.net.eth("eth_getCode",[TOKEN,hex(candidate)],archive=True))
        if before!="0x" or not after or after=="0x":
            raise ValueError("WGNK deployment boundary not verified")
        self.db.put("mints:deployment",first)
        bootstrap(self.db,candidate,head)
        if self.db.get("mints:next") is None:
            self.db.put("mints:next",max(candidate,head-100))
        self.ready.set()

    async def batch(self,lo,hi,finalized=True,archive=False,next_height=None):
        if lo>hi: return
        logs=await self.net.eth_logs({"address":TOKEN,"fromBlock":hex(lo),"toBlock":hex(hi),"topics":[MINT]},archive=archive)
        heights=sorted({lo,hi}|{int(item["blockNumber"],16) for item in logs})
        if any(h<lo or h>hi for h in heights):
            raise ValueError("Mint log outside requested range")
        blocks=[]
        for pos in range(0,len(heights),2):
            blocks.extend(await asyncio.gather(*(self.block(h,archive,finalized) for h in heights[pos:pos+2])))
        by_height={b["height"]:b for b in blocks}
        events=[decode_mint(item,by_height[int(item["blockNumber"],16)],finalized) for item in logs]
        # For new data verify the actual ERC-20 mint in each successful receipt, not just a custom event.
        for tx in sorted({e["tx_hash"] for e in events}):
            receipt=await self.net.eth("eth_getTransactionReceipt",[tx],archive=archive)
            group=[e for e in events if e["tx_hash"]==tx]
            if (receipt["transactionHash"].lower()!=tx or int(receipt["status"],16)!=1
                    or any(receipt["blockHash"].lower()!=e["block_hash"] or int(receipt["blockNumber"],16)!=e["height"] for e in group)):
                raise ValueError("Mint receipt conflicts with canonical block")
            mint_logs=[r for r in receipt["logs"] if r["address"].lower()==TOKEN and r["topics"] and r["topics"][0].lower()==MINT]
            receipt_mints=[decode_mint(r,by_height[int(r["blockNumber"],16)],finalized) for r in mint_logs]
            if {(e["tx_hash"],e["log_index"]) for e in receipt_mints}!={(e["tx_hash"],e["log_index"]) for e in group}:
                raise ValueError("RPC omitted a mint from a transaction")
            canonical={e["log_index"]:e for e in receipt_mints}
            if any(e!=canonical[e["log_index"]] for e in group):
                raise ValueError("Receipt mint fields differ from getLogs")
            transfers=defaultdict(int)
            for r in receipt["logs"]:
                topics=r.get("topics",[])
                if (r["address"].lower()==TOKEN and len(topics)==3 and topics[0].lower()==TRANSFER
                        and topics[1].lower()=="0x"+"0"*64):
                    transfers[("0x"+topics[2][-40:].lower(),str(int(r["data"],16)))]+=1
            for e in group:
                key=(e["recipient"],e["amount_raw"])
                if transfers[key]<1: raise ValueError("WGNKMinted has no matching ERC-20 mint")
                transfers[key]-=1
        save_batch(self.db,lo,hi,events,blocks,finalized,next_height)

    async def live(self):
        final,latest=await asyncio.gather(
            self.net.eth("eth_getBlockByNumber",["finalized",False]),
            self.net.eth("eth_getBlockByNumber",["latest",False]))
        head,tip=int(final["number"],16),int(latest["number"],16)
        previous=self.db.get("mints:status",{}).get("finalized_height",0)
        if head<previous or tip<head: raise ValueError("Ethereum finality moved backwards")
        if not self.ready.is_set(): await self.setup(head)
        self.status("mints:status",finalized_height=head,latest_height=tip,
                    latest_ts=int(latest["timestamp"],16),provider=self.net.current.get("ethereum"))
        start=self.db.get("mints:next")
        if start<=head:
            end=min(head,start+499)
            await self.batch(start,end,next_height=end+1,archive=head-start>500)
            self.status("mints:status",indexed_height=end)
        if tip-head>400:
            raise ValueError("Ethereum finality lag exceeds 400 blocks; provisional feed paused")
        await self.batch(head+1,tip,finalized=False)
        self.status("mints:status",provisional_height=tip)
        return .5 if self.db.get("mints:next")<=head else 12

    async def history(self):
        await self.ready.wait()
        head=self.db.get("mints:status",{}).get("finalized_height")
        if head is None: return 2
        hole=gap(self.db,self.db.get("mints:deployment")["height"],head)
        if not hole: return 30
        lo,end=hole
        hi=min(end,lo+self.cfg.eth_history_batch-1)
        await self.batch(lo,hi,archive=True)
        self.status("mints:history",last_range=[lo,hi],provider=self.net.current.get("ethereum_archive"))
        return .5


def public_event(row):
    e=dict(row)
    e["amount"]=tokens(e["amount_raw"])
    e["finalized"]=bool(e["finalized"])
    return e


def daily_rows(price_by_day,weight_by_day,tokens_by_day,daily,daily_counts):
    """Day rows with the latest known pool price and network weight carried forward."""
    from .db import tokens as _tokens
    rows=[];last_weight={}
    for d,raw in sorted(daily.items()):
        last_weight=weight_by_day.get(d,last_weight)
        rows.append({"date":d,"amount_raw":str(raw),"amount":_tokens(raw),"events":daily_counts[d],
                     "price_raw":price_by_day.get(d),
                     "weight":last_weight.get("weight"),"gpus":last_weight.get("gpus"),
                     "tokens":tokens_by_day.get(d)})
    return rows


def listing(db,minimum=10000,hours=0,q="",finality="finalized",sort="newest",limit=50,offset=0):
    now=int(time.time())
    rows=[public_event(r) for r in db.conn.execute("SELECT * FROM wgnk_mints")]
    final=[r for r in rows if r["finalized"]]
    def summarize(items):
        return {"events":len(items),"transactions":len({e["tx_hash"] for e in items}),
                "recipients":len({e["recipient"] for e in items}),
                "amount":tokens(sum(int(e["amount_raw"]) for e in items)),
                "largest":tokens(max((int(e["amount_raw"]) for e in items),default=0))}
    items=[e for e in rows if int(e["amount_raw"])>=minimum*10**9 and (not hours or e["ts"]>=now-hours*3600)
           and (finality=="all" or e["finalized"]) and (not q or any(q in e[k] for k in ("recipient","tx_hash","request_id")))]
    order={"newest":"time_desc","oldest":"time_asc","largest":"amount_desc"}.get(sort,sort)
    field,direction=order.rsplit("_",1)
    keys={"time":lambda e:e["ts"],"recipient":lambda e:e["recipient"],
          "amount":lambda e:int(e["amount_raw"]),"tx":lambda e:e["tx_hash"],
          "status":lambda e:(e["finalized"],e["height"])}
    if field not in keys or direction not in ("asc","desc"): raise ValueError("Invalid mint sort")
    items.sort(key=lambda e:(keys[field](e),e["height"],e["log_index"],e["tx_hash"]),
               reverse=direction=="desc")
    summary=summarize(items)
    recipients={}
    daily=defaultdict(int)
    daily_counts=defaultdict(int)
    # Daily VWAP of the verified pools: the price context for the rhythm chart.
    from .flows import price_raw
    price_daily={}
    pool_marks=",".join("?"*len(SEED_POOLS))
    for r in db.conn.execute("SELECT amount_raw,quote_raw,ts FROM events WHERE chain='ethereum' AND finalized=1 "
                             f"AND kind IN ('buy','sell') AND pool IN ({pool_marks})",SEED_POOLS):
        day2=local_day(r["ts"])
        acc=price_daily.setdefault(day2,[0,0])
        acc[0]+=int(r["amount_raw"]); acc[1]+=int(r["quote_raw"])
    price_by_day={d:(str(price_raw(q,a)) if a else None) for d,(a,q) in price_daily.items()}
    # Network weight per calendar day: latest epoch known as of that day.
    weight_by_day={}
    for entry in sorted((db.get("network_weight:history") or {}).get("items",{}).values(),
                        key=lambda x:(x["date"],x.get("weight",0))):
        weight_by_day[entry["date"]]=entry
    tokens_by_day={e["date"]:e.get("tokens") for e in (db.get("inference_tokens:history") or {}).get("items",{}).values()}
    for e in items:
        row=recipients.setdefault(e["recipient"],{"address":e["recipient"],"amount_raw":0,"events":0})
        row["amount_raw"]+=int(e["amount_raw"]); row["events"]+=1
        day=local_day(e["ts"])
        daily[day]+=int(e["amount_raw"])
        daily_counts[day]+=1
    leaders=sorted(recipients.values(),key=lambda r:r["amount_raw"],reverse=True)[:6]
    for row in leaders:
        row["amount"]=tokens(row["amount_raw"]); row["amount_raw"]=str(row["amount_raw"])
    return {"now":now,"mode":"mints","contract":TOKEN,"decimals":9,"timezone":TIME_ZONE,"coverage":progress(db),
            "all_summary":summarize(final),"pending_events":len(rows)-len(final),"summary":summary,
            "first_mint":min((e["ts"] for e in final),default=None),"last_mint":max((e["ts"] for e in final),default=None),
            "items":items[offset:offset+limit],"total":len(items),"offset":offset,"limit":limit,"has_more":offset+limit<len(items),
            "minimum":minimum,"hours":hours,"q":q,"sort":sort,"finality":finality,"recipients":leaders,
            "daily":daily_rows(price_by_day,weight_by_day,tokens_by_day,daily,daily_counts),
            "imported":db.get("mints:bootstrapped"),"disabled":["gonka_full_scan","holder_census","external_prices","mining"]}
