"""Address balances, complete censuses and evidence-scoped trading activity."""
import asyncio
import json
import re
import time
from collections import defaultdict
from .codec import TRANSFER
from .config import TOKEN, ZERO
from .db import tokens

ETH_ADDRESS = re.compile(r"0x[0-9a-f]{40}")
GNK_ADDRESS = re.compile(r"gonka1[023456789acdefghjklmnpqrstuvwxyz]{38,80}")

def metadata(db,key,value):
    """Caller owns the transaction so balances and cursors commit together."""
    db.conn.execute("INSERT OR REPLACE INTO kv VALUES(?,?)",
                    (key,json.dumps(value,ensure_ascii=False)))

def set_balance(db, asset, address, raw, height, source):
    raw = str(int(raw))
    if int(raw) < 0:
        raise ValueError("Negative holder balance")
    db.conn.execute("""
        INSERT INTO holder_balances VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(asset,address) DO UPDATE SET
        balance_raw=excluded.balance_raw,balance_digits=excluded.balance_digits,
        height=excluded.height,updated_at=excluded.updated_at,source=excluded.source
        WHERE excluded.height>=holder_balances.height
        """, (asset,address,raw,len(raw),height,int(time.time()),source))

def holder_record(db, address):
    r = db.conn.execute("SELECT * FROM holder_balances WHERE address=?", (address,)).fetchone()
    if not r:
        return None
    result={**dict(r),"balance":tokens(r["balance_raw"])}
    if r["asset"]=="WGNK":
        census=db.get("holders:WGNK",{})
        # An unchanged ERC-20 balance is still proven through the census cursor.
        result["height"]=census.get("height",r["height"])
        result["updated_at"]=census.get("updated_at",r["updated_at"])
    return result

def activity_map(db, since):
    result = {}
    rows = db.conn.execute("""
        SELECT actor address,kind,count(*) n,sumint(amount_raw) raw,sumint(quote_raw) quote,MAX(ts) last
        FROM events WHERE ts>=? AND kind IN ('buy','sell') AND actor!=''
        AND finalized=1 AND json_extract(meta,'$.attribution')='initiator_net'
        GROUP BY actor,kind""", (since,)).fetchall()
    for row in rows:
        a = result.setdefault(row["address"], {"buy_raw":0,"sell_raw":0,"buy_quote_raw":0,
            "sell_quote_raw":0,"buys":0,"sells":0,"last":0})
        k = row["kind"]
        a[k+"_raw"] += int(row["raw"])
        a[k+"_quote_raw"] += int(row["quote"])
        a["buys" if k=="buy" else "sells"] += row["n"]
        a["last"] = max(a["last"],row["last"])
    return result

def trading(a=None):
    a = a or {}
    buys, sells = a.get("buys",0), a.get("sells",0)
    return {"tag":"both" if buys and sells else "buy" if buys else "sell" if sells else "none",
        "buys":buys,"sells":sells,"bought":tokens(a.get("buy_raw",0)),
        "sold":tokens(a.get("sell_raw",0)),"buy_quote":tokens(a.get("buy_quote_raw",0),6),
        "sell_quote":tokens(a.get("sell_quote_raw",0),6),
        "net":tokens(a.get("buy_raw",0)-a.get("sell_raw",0)),"last_trade":a.get("last") or None}

def holders_list(db, asset, hours=24, minimum=10000, tag="", query="", limit=50, offset=0, since=None):
    since = (int(time.time())-hours*3600 if hours else 0) if since is None else since
    acts = activity_map(db,since)
    where, args = ["asset=?", "balance_raw!='0'"], [asset]
    raw = str(minimum*10**9)
    if minimum:
        where += ["balance_digits>=?", "(balance_digits>? OR balance_raw>=?)"]
        args += [len(raw),len(raw),raw]
    if query:
        where.append("instr(address,?)>0")
        args.append(query.lower())
    if tag:
        matched = [a for a,s in acts.items() if trading(s)["tag"]==tag]
        if tag=="none":
            matched = list(acts)
            if matched:
                where.append("address NOT IN (SELECT value FROM json_each(?))")
                args.append(json.dumps(matched))
        elif matched:
            where.append("address IN (SELECT value FROM json_each(?))")
            args.append(json.dumps(matched))
        else:
            where.append("0")
    clause = " AND ".join(where)
    total = db.conn.execute("SELECT count(*) FROM holder_balances WHERE "+clause,args).fetchone()[0]
    rows = db.conn.execute("SELECT * FROM holder_balances WHERE "+clause+
        " ORDER BY balance_digits DESC,balance_raw DESC,address LIMIT ? OFFSET ?",args+[limit,offset]).fetchall()
    labels = db.labels()
    items = [{**dict(r),"balance":tokens(r["balance_raw"]),"label":labels.get(r["address"]),
              **trading(acts.get(r["address"]))} for r in rows]
    census=db.get("holders:"+asset,{})
    if asset=="WGNK":
        for item in items:
            item["height"]=census.get("height",item["height"])
            item["updated_at"]=census.get("updated_at",item["updated_at"])
    return {"items":items,"total":total,"offset":offset,"has_more":offset+len(items)<total,
        "asset":asset,"hours":hours,"since":since,"minimum":minimum,"census":db.get("holders:"+asset,{}),
        "collector":db.get("status:"+("gnk_holders" if asset=="GNK" else "wgnk_holders"),{}),
        "coverage":db.coverage("ethereum"),
        "note":"Метки — только финальные Swap с подтверждённым движением WGNK инициатора. Отсутствие метки не доказывает отсутствие торговли. Адреса разных сетей не объединяются по владельцу."}

def address_page(db, address, hours=0, kind="trades", limit=50, offset=0, since=None):
    since = (int(time.time())-hours*3600 if hours else 0) if since is None else since
    asset = "GNK" if address.startswith("gonka") else "WGNK"
    chain = "gonka" if asset=="GNK" else "ethereum"
    record = holder_record(db,address)
    act = trading(activity_map(db,since).get(address))
    events = db.events(since,"buy,sell" if kind=="trades" else "",chain,address,limit,offset)
    return {"address":address,"asset":asset,"holder":record,"label":db.labels().get(address),
        "activity":act,"events":events,"hours":hours,"since":since,"kind":kind,
        "coverage":db.coverage(chain),"census":db.get("holders:"+asset,{}),
        "note":"Все страницы доступной локальной истории, не обещание полной истории блокчейна. Итоги торговли включают только финальные события с подтверждённым движением WGNK инициатора; пул/маршрутизатор не автоматически покупатель."}

class HolderCollector:
    def __init__(self,indexer):
        self.indexer=indexer
        self.db,self.net=indexer.db,indexer.net

    async def native(self):
        await self.indexer.native_ready.wait()
        scan=self.db.get("holders:GNK:scan")
        current=self.db.get("holders:GNK",{})
        if not scan:
            if current.get("complete") and time.time()-current.get("completed_at",0)<21600:
                return 60
            scan={"height":None,"key":None,"seen":0,"total":None,"started_at":int(time.time())}
            with self.db.conn:
                self.db.conn.execute("DELETE FROM native_holder_scan")
        if scan.get("done"):
            return await self.native_finalize(scan)
        params={"pagination.limit":"10000"}
        if scan["key"]:
            params["pagination.key"]=scan["key"]
        else:
            params["pagination.count_total"]="true"
        try:
            data,height=await self.net.api("/cosmos/bank/v1beta1/denom_owners/ngonka",params,
                height=scan["height"],with_height=True)
        except Exception:
            # A pruned pinned state cannot be resumed; keep last measured balances.
            if time.time()-scan["started_at"]>3600:
                self.db.put("holders:GNK:scan",None)
            raise
        if scan["height"] is None:
            scan["height"]=height
            scan["total"]=int(data["pagination"]["total"])
        page=data["denom_owners"]
        if not page and data["pagination"].get("next_key"):
            raise ValueError("Empty native owners page with continuation")
        rows=[]
        for owner in page:
            address=owner["address"].lower()
            balance=owner["balance"]
            if not GNK_ADDRESS.fullmatch(address) or balance["denom"]!="ngonka" or int(balance["amount"])<=0:
                raise ValueError("Invalid GNK holder")
            rows.append((address,str(int(balance["amount"]))))
        next_key=data["pagination"].get("next_key")
        if next_key and next_key==scan["key"]:
            raise ValueError("Repeated GNK pagination key")
        scan={**scan,"key":next_key,"seen":scan["seen"]+len(rows),"done":not next_key}
        with self.db.conn:
            for address,raw in rows:
                self.db.conn.execute("INSERT INTO native_holder_scan VALUES(?,?)",(address,raw))
                set_balance(self.db,"GNK",address,raw,height,"cosmos.bank.denom_owners")
            metadata(self.db,"holders:GNK:scan",scan)
            metadata(self.db,"holders:GNK",{**current,"complete":False,"height":height,
                "seen":scan["seen"],"total":scan["total"],"updated_at":int(time.time()),
                "source":"cosmos.bank.denom_owners, paginated pinned block"})
        self.db.revision+=1
        return 1

    async def native_finalize(self,scan):
        height=scan["height"]
        supply,served=await self.net.api("/cosmos/bank/v1beta1/supply/by_denom",
            {"denom":"ngonka"},height=height,with_height=True)
        found=self.db.conn.execute("SELECT count(*),sumint(balance_raw) FROM native_holder_scan").fetchone()
        if served!=height or found[0]!=scan["total"] or int(found[1] or 0)!=int(supply["amount"]["amount"]):
            self.db.put("holders:GNK:scan",None)
            raise ValueError("GNK census does not reconcile with supply/count")
        with self.db.conn:
            self.db.conn.execute("DELETE FROM holder_balances WHERE asset='GNK' AND height<?",(height,))
            metadata(self.db,"holders:GNK",{"complete":True,"height":height,"seen":scan["seen"],
                "total":scan["total"],"snapshot_supply":supply["amount"]["amount"],
                "completed_at":int(time.time()),"updated_at":int(time.time()),
                "source":"cosmos.bank.denom_owners, pinned block; sum verified against supply"})
            metadata(self.db,"holders:GNK:scan",None)
        self.db.revision+=1
        return 60

    async def native_updates(self):
        await self.indexer.native_ready.wait()
        census=self.db.get("holders:GNK",{})
        if not census.get("height"):
            return 20
        rows=self.db.conn.execute("SELECT * FROM holder_dirty ORDER BY height,address LIMIT 4").fetchall()
        for row in rows:
            if row["height"]<=census["height"]:
                with self.db.conn:
                    self.db.conn.execute("DELETE FROM holder_dirty WHERE address=?",(row["address"],))
                continue
            data,height=await self.net.api("/cosmos/bank/v1beta1/balances/"+row["address"]+"/by_denom",
                {"denom":"ngonka"},with_height=True)
            balance=data.get("balance")
            if not balance or balance["denom"]!="ngonka" or height<row["height"]:
                raise ValueError("Unverified native balance update")
            with self.db.conn:
                set_balance(self.db,"GNK",row["address"],balance["amount"],height,"cosmos.bank.balance, live observed address")
                self.db.conn.execute("DELETE FROM holder_dirty WHERE address=? AND height<=?",(row["address"],height))
        return 20

    async def deployment(self,head):
        saved=self.db.get("holders:WGNK:deployment")
        if saved:
            if not self.db.get("ethereum_full_history"):
                self.db.put("ethereum_target",min(saved,self.db.get("ethereum_target",saved)))
                self.db.put("ethereum_full_history",True)
            return saved
        if await self.net.eth("eth_getCode",[TOKEN,hex(head)])=="0x":
            raise ValueError("WGNK code absent at finalized head")
        lo,hi=0,head
        while lo+1<hi:
            mid=(lo+hi)//2
            code=await self.net.eth("eth_getCode",[TOKEN,hex(mid)],archive=True)
            if code=="0x":
                lo=mid
            else:
                hi=mid
        self.db.put("holders:WGNK:deployment",hi)
        self.db.put("ethereum_target",min(hi,self.db.get("ethereum_target",hi)))
        self.db.put("ethereum_full_history",True)
        return hi

    async def ethereum(self):
        await self.indexer.eth_ready.wait()
        final=await self.net.eth("eth_getBlockByNumber",["finalized",False])
        head=int(final["number"],16)
        start=await self.deployment(head)
        current=self.db.get("holders:WGNK",{})
        archive=head-current.get("height",start-1)>500
        if current.get("block_hash"):
            previous=await self.indexer.eth_block(current["height"],cached=False,archive=archive)
            if previous["hash"]!=current["block_hash"]:
                raise ValueError("Finalized WGNK holder checkpoint changed; refusing replay")
        lo=current.get("height",start-1)+1
        if lo>head:
            return 20
        hi=min(head,lo+self.indexer.cfg.eth_history_batch-1)
        logs=await self.net.eth_logs({"address":TOKEN,"fromBlock":hex(lo),
            "toBlock":hex(hi),"topics":[TRANSFER]},archive=archive)
        end=await self.indexer.eth_block(hi,archive=archive)
        supply=int(await self.net.call(TOKEN,"totalSupply()",tag=hex(hi),archive=archive),16)
        meta={"complete":hi==head,"height":hi,"head":head,"deployment":start,"block_hash":end["hash"],
            "remaining_blocks":head-hi,"updated_at":int(time.time()),
            "block_time":end["ts"],"snapshot_supply":str(supply),
            "source":"WGNK ERC-20 Transfer from deployment; finalized; sum verified against totalSupply"}
        apply_wgnk_batch(self.db,logs,lo,hi,supply,meta)
        return .5 if hi<head else 20

def apply_wgnk_batch(db,logs,lo,hi,supply,meta=None):
    """Atomic finalized ERC-20 reconstruction; zero address is never a holder."""
    delta=defaultdict(int)
    seen=set()
    for item in logs:
        topics=item["topics"]
        height=int(item["blockNumber"],16)
        identity=(item["transactionHash"].lower(),int(item["logIndex"],16))
        if (item["address"].lower()!=TOKEN or len(topics)!=3 or topics[0].lower()!=TRANSFER
                or item.get("removed") or not lo<=height<=hi or identity in seen):
            raise ValueError("Invalid/duplicate WGNK holder log")
        seen.add(identity)
        for word in topics[1:]:
            if not re.fullmatch(r"0x0{24}[0-9a-fA-F]{40}",word):
                raise ValueError("Invalid ERC-20 address encoding")
        src,dst=("0x"+v[-40:].lower() for v in topics[1:])
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}",item["data"]):
            raise ValueError("Invalid ERC-20 amount encoding")
        amount=int(item["data"],16)
        if src!=ZERO: delta[src]-=amount
        if dst!=ZERO: delta[dst]+=amount
    with db.conn:
        for address,change in delta.items():
            old=db.conn.execute("SELECT balance_raw FROM holder_balances WHERE asset='WGNK' AND address=?",(address,)).fetchone()
            value=int(old[0]) if old else 0
            set_balance(db,"WGNK",address,value+change,hi,"ERC-20 Transfer reconstruction")
        actual=db.conn.execute("SELECT sumint(balance_raw) FROM holder_balances WHERE asset='WGNK'").fetchone()[0]
        if int(actual or 0)!=supply:
            raise ValueError("WGNK balances do not reconcile with totalSupply")
        if meta is not None:
            count=db.conn.execute("SELECT count(*) FROM holder_balances WHERE asset='WGNK' AND balance_raw!='0'").fetchone()[0]
            metadata(db,"holders:WGNK",{**meta,"seen":count})
    db.revision+=1
