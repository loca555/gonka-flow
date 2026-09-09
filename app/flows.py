"""Verified WGNK market activity through the latest block. No GNK-wide scans."""
import asyncio
import json
import re
import time
from collections import defaultdict
from fractions import Fraction
from .codec import parse_eth_log, TRANSFER, SWAP, MINT, BURN, LP_MINT, LP_BURN
from .config import TOKEN, USDT, ZERO, SEED_POOLS
from .db import tokens
from .timezones import TIME_ZONE, local_day, local_time
from .address_history import address_history
from .holder_groups import outside_holders
from .holder_history import holder_history

def price_raw(quote, quantity, quote_decimals=6):
    """USDT/WGNK at 12 decimal places, explicitly rounded down."""
    return int(quote)*10**21//(int(quantity)*10**quote_decimals) if int(quantity)>0 else 0

def history_end(db,start,head):
    end=start-1
    for row in db.conn.execute("SELECT lo,hi FROM ranges WHERE chain='ethereum' ORDER BY lo"):
        if row["hi"]<start: continue
        if row["lo"]>end+1: break
        end=min(head,max(end,row["hi"]))
        if end>=head: break
    return end

def event_rows(db,start,head):
    return [dict(r) for r in db.conn.execute(
        "SELECT * FROM events WHERE chain='ethereum' AND finalized=1 AND height BETWEEN ? AND ? ORDER BY height,idx,id",
        (start,head))]

def market_packet(db, snapshot_hash="", through=None):
    saved=db.get("flow:live",{})
    packets=[saved.get("current"),*saved.get("history",[])]
    for packet in packets:
        if not packet: continue
        snap=packet["snapshot"]
        if snapshot_hash and snap["hash"]!=snapshot_hash: continue
        if through is not None and snap["height"]!=through: continue
        start=db.get("mints:deployment",{}).get("height")
        if start and history_end(db,start,packet["base"])==packet["base"]:
            return packet
    return None


def market_rows(db,start,snapshot,packet=None):
    if packet:
        return event_rows(db,start,packet["base"])+packet["events"]
    return event_rows(db,start,snapshot["height"])


def balances(rows):
    result=defaultdict(int);minted=burned=0
    for e in rows:
        quantity=int(e["amount_raw"])
        if quantity<0: raise ValueError("Negative WGNK event amount")
        if e["kind"]=="bridge_mint":
            result[e["dst"]]+=quantity;minted+=quantity
        elif e["kind"]=="bridge_burn":
            result[e["src"]]-=quantity;burned+=quantity
        elif e["kind"]=="transfer":
            result[e["src"]]-=quantity;result[e["dst"]]+=quantity
    return result,minted,burned

def minter_totals(rows,pools):
    """All-history mint and confirmed sale totals at one finalized snapshot, not token lots."""
    recipients={}
    for e in rows:
        if e["kind"]=="bridge_mint":
            r=recipients.setdefault(e["dst"],{"address":e["dst"],"minted_raw":0,"sales_raw":0,"quote_raw":0,
                "sales_count":0,"mint_count":0,"first_mint_ts":e["ts"],"last_mint_ts":e["ts"]})
            r["minted_raw"]+=int(e["amount_raw"])
            r["mint_count"]+=1
            r["first_mint_ts"]=min(r["first_mint_ts"],e["ts"])
            r["last_mint_ts"]=max(r["last_mint_ts"],e["ts"])
    for e in rows:
        if e["kind"]!="sell" or e["pool"] not in pools or e["actor"] not in recipients: continue
        if json.loads(e["meta"]).get("attribution")!="initiator_net": continue
        r=recipients[e["actor"]]
        r["sales_raw"]+=int(e["amount_raw"]);r["quote_raw"]+=int(e["quote_raw"]);r["sales_count"]+=1
    return recipients

def bridge_listing(db,minimum=10000,hours=0,q="",sort="newest",limit=50,offset=0):
    """One row per explicit WGNKMinted/WGNKBurned event, never its ERC-20 duplicate."""
    deployment=db.get("mints:deployment")
    target=db.get("mints:status",{}).get("finalized_height")
    snapshot=db.get("flow:snapshot")
    result={"ready":False,"now":int(time.time()),"timezone":TIME_ZONE,"snapshot":snapshot,
            "coverage":{"complete":False,"missing":None},"items":[],"total":0,
            "offset":offset,"limit":limit,"has_more":False,"sort":sort,"minimum":minimum,
            "hours":hours,"q":q,"native_side_checked":False}
    if not deployment or not target: return result
    start=deployment["height"];end=history_end(db,start,target)
    result["coverage"]={"start":start,"head":target,"indexed_height":end,
                        "complete":end==target,"missing":max(0,target-end)}
    if not snapshot or snapshot["height"]>end: return result
    rows=event_rows(db,start,snapshot["height"])
    recipients=minter_totals(rows,{p["address"] for p in snapshot["pools"]})
    items=[]
    for e in rows:
        if e["kind"] not in ("bridge_mint","bridge_burn"): continue
        address=e["dst"] if e["kind"]=="bridge_mint" else e["src"]
        if int(e["amount_raw"])<minimum*10**9 or (hours and e["ts"]<result["now"]-hours*3600): continue
        if q and not any(q in v for v in (address,e["tx_hash"],e["request_key"])): continue
        meta=json.loads(e["meta"])
        totals=recipients[address] if e["kind"]=="bridge_mint" else None
        items.append({"kind":e["kind"],"address":address,"recipient":address,"ts":e["ts"],
            "tx_hash":e["tx_hash"],"log_index":e["idx"],"height":e["height"],"block_hash":e["block_hash"],
            "amount_raw":e["amount_raw"],"amount":tokens(e["amount_raw"]),"finalized":True,
            "request_id":e["request_key"] if e["kind"]=="bridge_mint" else "",
            "epoch_id":str(meta.get("epoch","")),"native_side_checked":False,
            "minter_totals":None if totals is None else {
                "minted_raw":str(totals["minted_raw"]),"sold_raw":str(totals["sales_raw"]),
                "minted":tokens(totals["minted_raw"]),"sold":tokens(totals["sales_raw"]),
                "sales_count":totals["sales_count"]}})
    order={"newest":"time_desc","oldest":"time_asc","largest":"amount_desc"}.get(sort,sort)
    field,direction=order.rsplit("_",1)
    keys={"time":lambda e:e["ts"],"recipient":lambda e:e["address"],"kind":lambda e:e["kind"],
          "amount":lambda e:int(e["amount_raw"]),"tx":lambda e:e["tx_hash"],"status":lambda e:e["height"]}
    if field not in keys or direction not in ("asc","desc"): raise ValueError("Invalid bridge sort")
    items.sort(key=lambda e:(keys[field](e),e["height"],e["log_index"],e["tx_hash"]),reverse=direction=="desc")
    result.update(ready=True,total=len(items),items=items[offset:offset+limit],has_more=offset+limit<len(items))
    return result

class FlowCollector:
    def __init__(self,owner):
        self.owner,self.db,self.net=owner,owner.db,owner.net
        self.pools={}

    async def batch(self,lo,hi,archive=False):
        events,blocks=await self.read_batch(lo,hi,archive=archive)
        self.db.save_batch("ethereum",lo,hi,events,blocks)

    async def read_batch(self,lo,hi,archive=False,finalized=True):
        topics=[TRANSFER,SWAP,MINT,BURN,LP_MINT,LP_BURN]
        logs=await self.net.eth_logs({"fromBlock":hex(lo),"toBlock":hex(hi),
            "address":[TOKEN]+list(self.pools),"topics":[topics]},archive=archive)
        allowed={TOKEN,*self.pools}
        for item in logs:
            if (item.get("removed") or item["address"].lower() not in allowed
                    or not item.get("topics") or item["topics"][0].lower() not in topics
                    or not lo<=int(item["blockNumber"],16)<=hi):
                raise ValueError("Unexpected WGNK market log")
        heights=sorted({lo,hi}|{int(item["blockNumber"],16) for item in logs})
        blocks=[]
        for pos in range(0,len(heights),2):
            blocks.extend(await asyncio.gather(*(self.owner.block(h,archive=archive,finalized=finalized) for h in heights[pos:pos+2])))
        by_height={b["height"]:b for b in blocks}
        receipts={}
        for tx in sorted({r["transactionHash"].lower() for r in logs
                          if r["address"].lower() in self.pools and r["topics"][0].lower()==SWAP}):
            receipt=await self.net.eth("eth_getTransactionReceipt",[tx],archive=archive)
            if receipt["transactionHash"].lower()!=tx or int(receipt["status"],16)!=1:
                raise ValueError("Unsuccessful or conflicting swap receipt")
            expected={int(r["logIndex"],16):r for r in logs if r["transactionHash"].lower()==tx
                      and r["address"].lower() in self.pools and r["topics"][0].lower()==SWAP}
            found={int(r["logIndex"],16):r for r in receipt["logs"]
                   if r["address"].lower() in self.pools and r.get("topics") and r["topics"][0].lower()==SWAP}
            if set(expected)!=set(found): raise ValueError("Missing pool swap in RPC response")
            for index,item in expected.items():
                if any(item[key]!=found[index][key] for key in ("address","data","topics","blockHash","transactionHash")):
                    raise ValueError("Swap receipt does not match log")
            receipts[tx]=receipt
        events=[]
        for item in logs:
            block=by_height[int(item["blockNumber"],16)]
            if item["blockHash"].lower()!=block["hash"]: raise ValueError("WGNK market block conflict")
            receipt=receipts.get(item["transactionHash"].lower())
            if receipt and (receipt["blockHash"].lower()!=block["hash"] or int(receipt["blockNumber"],16)!=block["height"]):
                raise ValueError("WGNK swap receipt block conflict")
            event=parse_eth_log(item,block,self.pools,receipt,finalized)
            if event: events.append(event)
        by_id={e["id"]:e for e in events}
        if len(by_id)!=len(events): raise ValueError("Duplicate market events")
        # Never erase already confirmed movements if a provider silently omits a log.
        fields=("height","block_hash","ts","tx_hash","idx","kind","src","dst","actor","amount_raw","quote_raw","quote_asset","pool")
        for old in self.db.conn.execute("SELECT * FROM events WHERE chain='ethereum' AND finalized=1 AND height BETWEEN ? AND ?",(lo,hi)):
            incoming=by_id.get(old["id"])
            if not incoming or any(str(old[k])!=str(incoming[k]) for k in fields):
                raise ValueError("Finalized market event conflict")
        return events,blocks

    async def snapshot(self,start,head):
        header=await self.owner.block(head)
        result=await self.checked_snapshot(head,header,event_rows(self.db,start,head))
        self.db.put("flow:snapshot",result)

    async def checked_snapshot(self,head,header,rows):
        ledger,minted,burned=balances(rows)
        if any(v<0 for v in ledger.values()): raise ValueError("Incomplete WGNK balance ledger")
        supply=int(await self.net.call(TOKEN,"totalSupply()",tag=hex(head)),16)
        if minted-burned!=supply or sum(ledger.values())!=supply:
            raise ValueError("WGNK supply does not reconcile with indexed mint/burn history")
        pools=[]
        for address,metadata in self.pools.items():
            raw=int(await self.net.call(TOKEN,"balanceOf(address)",address[2:].zfill(64),tag=hex(head)),16)
            if ledger.get(address,0)!=raw: raise ValueError("Pool balance does not reconcile with WGNK transfers")
            pools.append({**metadata,"balance_raw":str(raw),"balance":tokens(raw)})
        result={"height":head,"hash":header["hash"],"ts":header["ts"],"checked_at":int(time.time()),
                "minted_raw":str(minted),"burned_raw":str(burned),"supply_raw":str(supply),
                "pools":pools,"ledger_verified":True}
        return result

    async def live(self,start,final):
        # A packet owns its exact tail and immutable prefix boundary. Publishing one
        # KV value keeps rows, balances and the block identity consistent across RPC failures.
        raw=await self.net.eth("eth_getBlockByNumber",["latest",False])
        head=int(raw["number"],16)
        header={"height":head,"hash":raw["hash"].lower(),"ts":int(raw["timestamp"],16)}
        if head<final or not re.fullmatch("0x[0-9a-f]{64}",header["hash"]):
            raise ValueError("Invalid latest Ethereum block")
        if history_end(self.db,start,final)!=final:
            raise ValueError("Final market history is incomplete")
        saved=self.db.get("flow:live",{})
        previous=saved.get("current")
        canonical=False
        if previous and previous["snapshot"]["height"]<=head:
            old=previous["snapshot"]
            boundary=header if old["height"]==head else await self.owner.block(old["height"],finalized=False)
            canonical=boundary["hash"]==old["hash"]
        tail=[];lo=final+1
        if canonical:
            tail=[e for e in previous["events"] if e["height"]>final]
            lo=max(lo,previous["snapshot"]["height"]+1)
        for begin in range(lo,head+1,self.owner.cfg.eth_history_batch):
            end=min(head,begin+self.owner.cfg.eth_history_batch-1)
            events,_=await self.read_batch(begin,end,finalized=False)
            tail.extend({**e,"meta":json.dumps(e["meta"],ensure_ascii=False)} for e in events)
        tail.sort(key=lambda e:(e["height"],e["idx"],e["id"]))
        prefix=event_rows(self.db,start,final)
        if len({e["id"] for e in prefix+tail})!=len(prefix)+len(tail):
            raise ValueError("Duplicate live market event")
        result=await self.checked_snapshot(head,header,prefix+tail)
        boundary=await self.owner.block(head,finalized=False)
        if boundary["hash"]!=header["hash"]:
            raise ValueError("Latest Ethereum block changed during verification")
        packet={"base":final,"snapshot":result,"events":tail}
        history=[]
        if canonical:
            history=[p for p in [previous,*saved.get("history",[])] if p["snapshot"]["hash"]!=result["hash"]][:12]
        self.db.put("flow:live",{"current":packet,"history":history})
        self.owner.status("flow:status",indexed_height=head,latest_height=head,latest_ts=header["ts"])

    async def run(self):
        await self.owner.ready.wait()
        if not self.pools:
            verified={}
            for address in SEED_POOLS:
                metadata=await self.net.pool(address)
                if metadata["quote"]!=USDT or metadata["quote_decimals"]!=6:
                    raise ValueError("Expected WGNK/USDT seed pool")
                verified[address]=metadata
            self.pools=verified
            self.db.put("flow:pools",verified)
        start=self.db.get("mints:deployment")["height"]
        head=self.db.get("mints:status",{}).get("finalized_height")
        if head is None: return 5
        end=history_end(self.db,start,head)
        if end<head:
            lo=end+1;hi=min(head,lo+self.owner.cfg.eth_history_batch-1)
            await self.batch(lo,hi,archive=head-lo>500)
            self.owner.status("flow:status",indexed_height=hi)
            if hi<head: return .5
        snapshot=self.db.get("flow:snapshot",{})
        if snapshot.get("height")!=head or time.time()-snapshot.get("checked_at",0)>300:
            await self.snapshot(start,head)
        await self.live(start,head)
        return 5

def selected_trades(market_trades,pools,hours,q,side,sort,now,minimum=0):
    if side not in ("sell","buy","all"): raise ValueError("Invalid trade side")
    field,direction=sort.rsplit("_",1)
    if field not in ("time","kind","actor","amount","quote","price","pool","tx") or direction not in ("asc","desc"):
        raise ValueError("Invalid trade sort")
    selected=[]
    all_trades=[e for e in market_trades if side=="all" or e["kind"]==side]
    for e in all_trades:
        if int(e["amount_raw"])<minimum*10**9: continue
        meta=json.loads(e["meta"])
        if hours and e["ts"]<now-hours*3600: continue
        if q:
            if re.fullmatch("0x[0-9a-f]{40}",q):
                if e["actor"]!=q or meta.get("attribution")!="initiator_net": continue
            elif q not in e["tx_hash"]: continue
        selected.append({**e,"meta":meta})
    # Sort the full filtered history before pagination; prices compare exact ratios.
    keys={"time":lambda e:e["ts"],"kind":lambda e:e["kind"],"actor":lambda e:e["actor"],
          "amount":lambda e:int(e["amount_raw"]),"quote":lambda e:int(e["quote_raw"]),
          "price":lambda e:Fraction(int(e["quote_raw"]),int(e["amount_raw"])),
          "pool":lambda e:(pools[e["pool"]]["fee"],e["pool"]),"tx":lambda e:e["tx_hash"]}
    selected.sort(key=lambda e:(keys[field](e),e["height"],e["idx"],e["tx_hash"]),reverse=direction=="desc")
    return selected

def public_trade(event):
    return {**event,"amount":tokens(event["amount_raw"]),"quote":tokens(event["quote_raw"],6),
            "price":tokens(price_raw(event["quote_raw"],event["amount_raw"]),12),
            "time_local":local_time(event["ts"]),"attribution":event["meta"].get("attribution","pool_only")}

def trade_page(db,*,through,as_of,hours=0,q="",limit=25,offset=0,side="sell",sort="time_desc",minimum=0,snapshot_hash=""):
    """Page a pinned verified snapshot without replaying balances, charts or minters."""
    result={"ready":False,"snapshot_height":through,"as_of":as_of,"hours":hours,"q":q,
            "side":side,"sort":sort,"minimum":minimum,"offset":offset,"limit":limit,"total":0,"has_more":False,"trades":[]}
    deployment=db.get("mints:deployment")
    if not deployment or not 0<=as_of<=int(time.time()):return result
    packet=market_packet(db,snapshot_hash,through)
    if packet:
        snap=packet["snapshot"];pools={p["address"]:p for p in snap["pools"]}
        rows=[e for e in market_rows(db,deployment["height"],snap,packet) if e["kind"] in ("buy","sell") and e["pool"] in pools]
        selected=selected_trades(rows,pools,hours,q,side,sort,as_of,minimum)
        result.update(ready=True,snapshot_hash=snap["hash"],total=len(selected),has_more=offset+limit<len(selected),
                      trades=[public_trade(event) for event in selected[offset:offset+limit]])
        return result
    target=db.get("mints:status",{}).get("finalized_height")
    snapshot=db.get("flow:snapshot")
    if not target or not snapshot:return result
    if snapshot_hash:
        block=db.conn.execute("SELECT hash FROM blocks WHERE chain='ethereum' AND height=?",(through,)).fetchone()
        known=snapshot["hash"] if through==snapshot["height"] else block[0] if block else None
        if known!=snapshot_hash:return result
    result["snapshot_hash"]=snapshot_hash or (snapshot.get("hash") if through==snapshot["height"] else None)
    start=deployment["height"]
    if not start<=through<=snapshot["height"]<=history_end(db,start,target) or not 0<=as_of<=int(time.time()):
        return result
    pools={p["address"]:p for p in snapshot["pools"]}
    if not pools:return result
    placeholders=",".join("?" for _ in pools)
    where="chain='ethereum' AND finalized=1 AND height BETWEEN ? AND ? AND kind IN ('buy','sell') AND pool IN ("+placeholders+")"
    params=[start,through,*pools]
    if minimum:
        # Raw amounts are canonical decimal strings and may exceed SQLite's integer range.
        raw=str(minimum*10**9)
        where+=" AND (length(amount_raw)>? OR (length(amount_raw)=? AND amount_raw>=?))"
        params.extend((len(raw),len(raw),raw))
    # The default feed can page in SQLite without decoding every historical Swap.
    if not q and sort in ("time_asc","time_desc") and side in ("all","buy","sell"):
        if side!="all":where+=" AND kind=?";params.append(side)
        if hours:where+=" AND ts>=?";params.append(as_of-hours*3600)
        total=db.conn.execute("SELECT COUNT(*) FROM events INDEXED BY events_trade_page WHERE "+where,params).fetchone()[0]
        direction="DESC" if sort=="time_desc" else "ASC"
        order=",".join(field+" "+direction for field in ("ts","height","idx","tx_hash"))
        rows=[dict(row) for row in db.conn.execute(
            "SELECT * FROM events INDEXED BY events_trade_page WHERE "+where+" ORDER BY "+order+" LIMIT ? OFFSET ?",(*params,limit,offset))]
        for row in rows:row["meta"]=json.loads(row["meta"])
        result.update(ready=True,total=total,has_more=offset+limit<total,trades=[public_trade(row) for row in rows])
        return result
    rows=[dict(row) for row in db.conn.execute("SELECT * FROM events WHERE "+where,params)]
    selected=selected_trades(rows,pools,hours,q,side,sort,as_of,minimum)
    result.update(ready=True,total=len(selected),has_more=offset+limit<len(selected),
                  trades=[public_trade(event) for event in selected[offset:offset+limit]])
    return result

def analysis(db,hours=0,q="",limit=25,offset=0,side="sell",sort="time_desc",minimum=0):
    if side not in ("sell","buy","all"): raise ValueError("Invalid trade side")
    field,direction=sort.rsplit("_",1)
    if field not in ("time","kind","actor","amount","quote","price","pool","tx") or direction not in ("asc","desc"):
        raise ValueError("Invalid trade sort")
    now=int(time.time())
    deployment=db.get("mints:deployment")
    status=db.get("flow:status",{})
    chain=db.get("mints:status",{})
    packet=market_packet(db)
    snapshot=packet["snapshot"] if packet else db.get("flow:snapshot")
    target=max(chain.get("latest_height",0),chain.get("finalized_height",0),status.get("latest_height",0)) or None
    result={"now":now,"timezone":TIME_ZONE,"status":status,"snapshot":snapshot,"ready":False,
            "coverage":{"complete":False,"missing":None},"summary":None,"pools":[],
            "sales":[],"daily":[],"minters":[],"total":0,"offset":offset,"limit":limit,
            "has_more":False,"hours":hours,"q":q,"side":side,"sort":sort,"minimum":minimum,"trades":[],"address_balance":None,"address_history":None,
            "outside_holders":None,"holder_history":None,"latest_trade":None,
            "scope":"All addresses in 2 verified Uniswap V3 WGNK/USDT pools"}
    if not deployment or not target: return result
    start=deployment["height"];end=packet["snapshot"]["height"] if packet else history_end(db,start,target)
    target=max(target,end)
    result["coverage"]={"start":start,"head":target,"indexed_height":end,
        "complete":end==target,"missing":max(0,target-end)}
    if not snapshot or snapshot["height"]>end: return result
    cut=snapshot["height"]
    rows=market_rows(db,start,snapshot,packet)
    ledger,minted,burned=balances(rows)
    result["outside_holders"]=outside_holders(rows,ledger,snapshot)
    result["holder_history"]=holder_history(rows,ledger,snapshot,deployment,result["outside_holders"])
    if re.fullmatch("0x[0-9a-f]{40}",q) and snapshot.get("ledger_verified") and ledger.get(q,0)>=0:
        result["address_balance"]={"address":q,"amount":tokens(ledger.get(q,0)),
            "amount_raw":str(ledger.get(q,0)),"height":cut,"ts":snapshot["ts"],"source":"verified_transfer_ledger"}
        result["address_history"]=address_history(rows,q,snapshot,ledger.get(q,0))
    pools={p["address"]:p for p in snapshot["pools"]}
    market_trades=[e for e in rows if e["kind"] in ("sell","buy") and e["pool"] in pools]
    # The header quote is global: latest verified Swap, before any view filters.
    latest=max(market_trades,key=lambda e:(e["height"],e["idx"],e["tx_hash"]),default=None)
    if latest:
        result["latest_trade"]={key:latest[key] for key in ("ts","height","tx_hash","kind","pool")}
        result["latest_trade"].update(log_index=latest["idx"],
            price=tokens(price_raw(latest["quote_raw"],latest["amount_raw"]),12))
    all_sales=[e for e in market_trades if e["kind"]=="sell"]
    recipient_data=minter_totals(rows,pools)
    minters=[]
    from .provenance import links_for
    native_links=defaultdict(list)
    for link in links_for(db,through=cut):
        native_links[link["eth_address"]].append(link)
    for r in sorted(recipient_data.values(),key=lambda r:(r["sales_raw"],r["minted_raw"]),reverse=True):
        minters.append({**r,"minted_raw":str(r["minted_raw"]),"sales_raw":str(r["sales_raw"]),"quote_raw":str(r["quote_raw"]),
            "gnk_addresses":sorted({e["gnk_address"] for e in native_links[r["address"]]}),
            "gnk_verified_mints":len(native_links[r["address"]]),
            "minted":tokens(r["minted_raw"]),"sold":tokens(r["sales_raw"]),"balance":tokens(ledger.get(r["address"],0)),
            "quote":tokens(r["quote_raw"],6),"average_price":tokens(price_raw(r["quote_raw"],r["sales_raw"]),12) if r["sales_raw"] else None})
    selected=selected_trades(market_trades,pools,hours,q,side,sort,now,minimum)
    sold=sum(int(e["amount_raw"]) for e in selected)
    quote=sum(int(e["quote_raw"]) for e in selected)
    sides={kind:{"raw":0,"quote":0,"count":0} for kind in ("sell","buy")}
    for e in selected:
        total=sides[e["kind"]]
        total["raw"]+=int(e["amount_raw"]);total["quote"]+=int(e["quote_raw"]);total["count"]+=1
    pooled=sum(int(p["balance_raw"]) for p in pools.values())
    supply=int(snapshot["supply_raw"])
    liquidity_added=sum(int(e["amount_raw"]) for e in rows if e["kind"]=="liquidity_add" and e["pool"] in pools)
    daily=defaultdict(lambda:{"raw":0,"quote_raw":0,"events":0,"sold_raw":0,"bought_raw":0,
                              "sale_quote_raw":0,"buy_quote_raw":0,"sales_count":0,"buys_count":0})
    for e in selected:
        day=daily[local_day(e["ts"])]
        day["raw"]+=int(e["amount_raw"]);day["quote_raw"]+=int(e["quote_raw"]);day["events"]+=1
        buy=e["kind"]=="buy"
        day["bought_raw" if buy else "sold_raw"]+=int(e["amount_raw"])
        day["buy_quote_raw" if buy else "sale_quote_raw"]+=int(e["quote_raw"])
        day["buys_count" if buy else "sales_count"]+=1
    page_limit=len(selected) if limit is None else limit
    page=[public_trade(event) for event in selected[offset:offset+page_limit]]
    result.update(ready=True,summary={"minted":tokens(minted),"burned":tokens(burned),"supply":tokens(supply),
        "pooled":tokens(pooled),"outside_pools":tokens(supply-pooled),"pooled_raw":str(pooled),
        "outside_raw":str(supply-pooled),"minted_raw":str(minted),"burned_raw":str(burned),
        "volume":tokens(sold),"volume_raw":str(sold),
        "sold":tokens(sides["sell"]["raw"]),"sold_raw":str(sides["sell"]["raw"]),
        "bought":tokens(sides["buy"]["raw"]),"bought_raw":str(sides["buy"]["raw"]),
        "sale_quote":tokens(sides["sell"]["quote"],6),"buy_quote":tokens(sides["buy"]["quote"],6),
        "sale_quote_raw":str(sides["sell"]["quote"]),"buy_quote_raw":str(sides["buy"]["quote"]),
        "sales_count":sides["sell"]["count"],"buys_count":sides["buy"]["count"],
        "sale_average_price":tokens(price_raw(sides["sell"]["quote"],sides["sell"]["raw"]),12) if sides["sell"]["raw"] else None,
        "buy_average_price":tokens(price_raw(sides["buy"]["quote"],sides["buy"]["raw"]),12) if sides["buy"]["raw"] else None,
        "quote":tokens(quote,6),"quote_raw":str(quote),
        "average_price":tokens(price_raw(quote,sold),12) if sold else None,
        "transactions":len({e["tx_hash"] for e in selected}),"swaps":len(selected),
        "liquidity_added":tokens(liquidity_added),"all_sales":len(all_sales)},
        pools=list(pools.values()),trades=page,
        sales=page if side=="sell" else [],total=len(selected),limit=page_limit,
        has_more=offset+page_limit<len(selected),minters=minters,
        daily=[{"date":day,**{key:str(value) if key.endswith("raw") else value for key,value in d.items()},
                "price_raw":str(price_raw(d["quote_raw"],d["raw"]))} for day,d in sorted(daily.items())])
    return result
