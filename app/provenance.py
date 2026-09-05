"""Targeted, read-only Gonka provenance. Never scans the whole chain.

Bridge links are checked against native block results and the final Ethereum mint.
Incoming history comes from the explicitly labelled GonkaLabs address index. An
exhausted explorer feed is NOT a proof of complete on-chain coverage.
"""
import asyncio
import base64
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .codec import attr, kh, stamp
from .config import ESCROW, TOKEN
from .db import tokens

GNK = re.compile(r"gonka1(?:[023456789acdefghjklmnpqrstuvwxyz]{38}|[023456789acdefghjklmnpqrstuvwxyz]{58})")
HASH = re.compile(r"[0-9a-fA-F]{64}")
SOURCE = "https://rpc.gonka.gg"
PAGE = 100
OVERLAP = 10
BALANCE_INTERVAL = 60


def initialize(db):
    db.conn.executescript("""
      CREATE TABLE IF NOT EXISTS gonka_mint_links(
        tx_hash TEXT NOT NULL, log_index INTEGER NOT NULL, gnk_address TEXT NOT NULL,
        value TEXT NOT NULL, PRIMARY KEY(tx_hash,log_index));
      CREATE INDEX IF NOT EXISTS gonka_links_address ON gonka_mint_links(gnk_address);
      CREATE TABLE IF NOT EXISTS gonka_link_attempts(
        tx_hash TEXT NOT NULL, log_index INTEGER NOT NULL, attempts INTEGER NOT NULL,
        retry_at INTEGER NOT NULL, error TEXT NOT NULL, PRIMARY KEY(tx_hash,log_index));
      CREATE TABLE IF NOT EXISTS gonka_incoming(
        address TEXT NOT NULL, tx_hash TEXT NOT NULL, event_index INTEGER NOT NULL,
        height INTEGER NOT NULL, ts INTEGER NOT NULL, src TEXT NOT NULL,
        amount_raw TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
        PRIMARY KEY(address,tx_hash,event_index));
      CREATE INDEX IF NOT EXISTS gonka_incoming_time ON gonka_incoming(address,ts DESC);
      CREATE TABLE IF NOT EXISTS gonka_address_sync(address TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS gonka_address_balances(address TEXT PRIMARY KEY,value TEXT NOT NULL);
    """)


def coins(value):
    """Exact Cosmos coin parsing; foreign denoms and zero are not GNK inflows."""
    if not isinstance(value, str):
        raise ValueError("Malformed Cosmos coins")
    result = 0
    for part in value.split(","):
        if not part:
            continue
        match = re.fullmatch(r"(0|[1-9][0-9]*)([a-zA-Z][a-zA-Z0-9/:._-]*)", part)
        if not match:
            raise ValueError("Malformed Cosmos coin amount")
        if match[2] == "ngonka":
            result += int(match[1])
    return result


def verify_link(mint, bls, block_reply, results_reply):
    from .mints import validate_mint
    validate_mint(mint)
    if not mint["finalized"]:
        raise ValueError("Only finalized Ethereum mints can be linked")
    signing = bls["signing_request"]
    height = int(signing["created_block_height"])
    request = "0x" + base64.b64decode(signing["request_id"], validate=True).hex()
    if (request != mint["request_id"] or str(signing["current_epoch_id"]) != mint["epoch_id"]
            or signing["status"] not in (3,"THRESHOLD_SIGNING_STATUS_COMPLETED")):
        raise ValueError("BLS request/epoch/status differs from Ethereum mint")
    data = [base64.b64decode(v, validate=True) for v in signing["data"]]
    if (len(data) != 5 or len(data[0]) != 32 or int.from_bytes(data[0], "big") != 1
            or len(data[2]) != 20 or "0x" + data[2].hex() != mint["recipient"]
            or len(data[3]) != 20 or "0x" + data[3].hex() != TOKEN
            or len(data[4]) != 32 or str(int.from_bytes(data[4], "big")) != mint["amount_raw"]):
        raise ValueError("BLS signing payload differs from Ethereum mint")
    result, block = results_reply["result"], block_reply["result"]["block"]
    block_hash = block_reply["result"]["block_id"]["hash"].upper()
    if (height < 1 or int(result["height"]) != height or int(block["header"]["height"]) != height
            or block["header"]["chain_id"] != "gonka-mainnet" or not HASH.fullmatch(block_hash)):
        raise ValueError("Wrong native block, results or chain")
    txs, outcomes = block["data"].get("txs") or [], result.get("txs_results") or []
    if len(txs) != len(outcomes):
        raise ValueError("Missing native transaction results")
    found = []
    for encoded, outcome in zip(txs, outcomes):
        if int(outcome.get("code", 0)) != 0:
            continue
        raw = base64.b64decode(encoded, validate=True)
        tx_hash = hashlib.sha256(raw).hexdigest().upper()
        events = outcome.get("events") or []
        for idx, event in enumerate(events):
            if event["type"] != "bridge_mint_requested":
                continue
            a = attr(event)
            if kh(a.get("request_id", "")) != mint["request_id"]:
                continue
            if (not GNK.fullmatch(a.get("user", "")) or a.get("amount") != mint["amount_raw"]
                    or a.get("destination_address", "").lower() != mint["recipient"]
                    or a.get("destination_bridge_address", "").lower() != TOKEN
                    or a.get("chain_id") != "ethereum" or a.get("epoch_index") != mint["epoch_id"]):
                raise ValueError("Native bridge event differs from Ethereum mint")
            transfers = [attr(e) for e in events if e["type"] == "transfer"]
            if not any(t.get("sender") == a["user"] and t.get("recipient") == ESCROW
                       and coins(t.get("amount", "")) == int(mint["amount_raw"])
                       and t.get("msg_index", "") == a.get("msg_index", "") for t in transfers):
                raise ValueError("Bridge request has no matching native escrow transfer")
            found.append({"tx_hash":mint["tx_hash"], "log_index":mint["log_index"],
                "eth_address":mint["recipient"], "eth_height":mint["height"], "eth_ts":mint["ts"],
                "request_id":mint["request_id"], "epoch_id":mint["epoch_id"], "amount_raw":mint["amount_raw"],
                "gnk_address":a["user"], "gnk_tx_hash":tx_hash, "gnk_height":height,
                "gnk_ts":stamp(block["header"]["time"]), "gnk_block_hash":block_hash,
                "event_index":idx, "native_request_id":a["request_id"],
                "verified_at":int(time.time()), "verification":"native_block_results_and_bls_request"})
    if len(found) != 1:
        raise ValueError("Native mint request not found uniquely in its block")
    if found[0]["gnk_ts"] > mint["ts"]:
        raise ValueError("Native lock is later than Ethereum mint")
    return found[0]


def save_link(db, link):
    with db.conn:
        old = db.conn.execute("SELECT value FROM gonka_mint_links WHERE tx_hash=? AND log_index=?",
                              (link["tx_hash"],link["log_index"])).fetchone()
        if old:
            previous = json.loads(old[0])
            if any(previous[k] != v for k,v in link.items() if k != "verified_at"):
                raise ValueError("Conflicting verified bridge link")
            return
        db.conn.execute("INSERT INTO gonka_mint_links VALUES(?,?,?,?)", (
            link["tx_hash"],link["log_index"],link["gnk_address"],json.dumps(link)))
        db.conn.execute("DELETE FROM gonka_link_attempts WHERE tx_hash=? AND log_index=?",
                        (link["tx_hash"],link["log_index"]))
    db.revision += 1


def indexed_incoming(tx, address, modules=None):
    """Use individual bank events, not the indexer's one-amount transaction summary."""
    modules = modules or {}
    if tx.get("success") is False:
        return []
    if tx.get("success") is not True or not HASH.fullmatch(tx.get("tx_hash", "")):
        raise ValueError("Malformed indexed transaction")
    height = int(tx["block_height"])
    if height < 1 or not GNK.fullmatch(address):
        raise ValueError("Invalid address/height")
    timestamp = datetime.fromisoformat(tx["block_time"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)  # ClickHouse DateTime64 is UTC.
    events = tx["events"]
    if isinstance(events,str):
        events = json.loads(events)
    if not isinstance(events,list):
        raise ValueError("Indexed events missing")
    actions = [attr(e) for e in events if e.get("type") == "message" and attr(e).get("action")]
    result, covered = [], Counter()
    def add(idx, a, sender, quantity, fallback=False):
        module = modules.get(sender, "")
        reward = any(e.get("action", "").endswith("MsgClaimRewards") and
                     e.get("msg_index", "") == a.get("msg_index", "") for e in actions)
        kind = "vesting_unlock" if module == "streamvesting" else (
            "reward_paid" if reward and module == "inference" else
            "escrow_release" if sender == ESCROW else "module_transfer" if module else "transfer")
        result.append({"address":address,"tx_hash":tx["tx_hash"].upper(),"event_index":idx,
            "height":height,"ts":int(timestamp.timestamp()),"src":sender,"amount_raw":str(quantity),
            "kind":"received_unknown" if fallback else kind,"message_index":a.get("msg_index"),
            "source_label":module or None,"tx_type":str(tx.get("tx_type", ""))[:160],
            "self_transfer":sender == address,"source":"gonkalabs_address_index"})
    for idx,event in enumerate(events):
        if event.get("type") != "transfer":
            continue
        a = attr(event)
        # Reject ambiguous repeated attributes rather than assigning a guessed sender.
        attributes = event.get("attributes", [])
        keys = [v["key"] for v in attributes if v["key"] in ("sender","recipient","amount")]
        if len(keys) != len(set(keys)):
            if any(v.get("value") == address for v in attributes):
                raise ValueError("Ambiguous multi-transfer event")
            continue
        if a.get("recipient") != address:
            continue
        quantity = coins(a.get("amount", ""))
        if not quantity:
            continue
        sender = a.get("sender", "")
        if sender and not GNK.fullmatch(sender):
            raise ValueError("Invalid transfer sender")
        add(idx,a,sender,quantity,not sender)
        covered[(a.get("msg_index", ""), quantity)] += 1
    # CoinReceived is a fallback, never a second count of the same Transfer.
    for idx,event in enumerate(events):
        if event.get("type") != "coin_received":
            continue
        a = attr(event)
        if a.get("receiver") != address:
            continue
        quantity = coins(a.get("amount", ""))
        if not quantity:
            continue
        key = (a.get("msg_index", ""),quantity)
        if covered[key]:
            covered[key] -= 1
        else:
            add(idx,a,"",quantity,True)
    return result


def sync_state(db,address):
    row = db.conn.execute("SELECT value FROM gonka_address_sync WHERE address=?",(address,)).fetchone()
    return json.loads(row[0]) if row else {"offset":0,"anchor":None,"head":None,
        "phase":"history","exhausted":False,"pages":0,"next_check":0,"checked_at":None}


def save_page(db,address,page,modules=None):
    state = sync_state(db,address)
    offset = state["offset"]
    rows = page.get("txs")
    if (page.get("address") != address or page.get("offset") != offset
            or not isinstance(rows,list) or page.get("count") != len(rows)
            or not isinstance(page.get("has_more"),bool) or len(rows) > PAGE
            or (not rows and page["has_more"])):
        raise ValueError("Malformed explorer page")
    # The gateway guarantees newest blocks first but does not consistently sort
    # transactions inside the same block. The overlapping hash checks the boundary.
    order = [int(r["block_height"]) for r in rows]
    if order != sorted(order,reverse=True):
        raise ValueError("Explorer pagination order changed")
    hashes = [r["tx_hash"].lower() for r in rows]
    if len(hashes) != len(set(hashes)):
        raise ValueError("Duplicate transactions within explorer page")
    if state["anchor"] and state["anchor"] not in hashes:
        # Offset feeds move as new transactions arrive. Never silently jump over
        # a missing boundary; restart this pass while retaining all cached rows.
        state.update(offset=0,anchor=None,next_check=0,
                     error="Порядок истории изменился; повторяем проход без удаления сохранённых данных")
        with db.conn:
            db.conn.execute("INSERT OR REPLACE INTO gonka_address_sync VALUES(?,?)",(address,json.dumps(state)))
        return
    incoming = [e for r in rows for e in indexed_incoming(r,address,modules)]
    now = int(time.time())
    if offset == 0:
        state["new_head"] = hashes[0] if hashes else None
    reached = state["phase"] == "live" and state.get("head") in hashes
    done = not page["has_more"] or reached
    state.update(checked_at=now,error=None,pages=state["pages"]+1)
    if done:
        state.update(offset=0,anchor=None,head=state.get("new_head"),phase="live",
                     exhausted=True,next_check=now+300,last_pass_at=now)
    else:
        if len(rows) <= OVERLAP:
            raise ValueError("Explorer page cannot advance")
        next_offset = offset+len(rows)-OVERLAP
        if next_offset > 1000000:
            raise ValueError("Достигнут лимит пагинации эксплорера; история неполная")
        state.update(offset=next_offset,anchor=hashes[-1],next_check=0)
    with db.conn:
        for event in incoming:
            identity = (address,event["tx_hash"],event["event_index"])
            old = db.conn.execute("SELECT amount_raw,src,height FROM gonka_incoming WHERE address=? AND tx_hash=? AND event_index=?",identity).fetchone()
            if old and tuple(old) != (event["amount_raw"],event["src"],event["height"]):
                raise ValueError("Explorer changed an already cached incoming transfer")
            db.conn.execute("INSERT OR IGNORE INTO gonka_incoming VALUES(?,?,?,?,?,?,?,?,?)",(
                *identity,event["height"],event["ts"],event["src"],event["amount_raw"],event["kind"],json.dumps(event)))
        db.conn.execute("INSERT OR REPLACE INTO gonka_address_sync VALUES(?,?)",(address,json.dumps(state)))
    db.revision += 1


def links_for(db,address=None,through=None):
    where,args = ["m.finalized=1"],[]
    if address:
        where.append("m.recipient=?");args.append(address)
    if through is not None:
        where.append("m.height<=?");args.append(through)
    return [json.loads(r[0]) for r in db.conn.execute("""SELECT l.value FROM gonka_mint_links l
        JOIN wgnk_mints m ON m.tx_hash=l.tx_hash AND m.log_index=l.log_index WHERE """+
        " AND ".join(where)+" ORDER BY m.ts DESC,m.height DESC,m.log_index DESC",args)]


def overview(db,address=None,through=None):
    where,args = ["finalized=1"],[]
    if address:
        where.append("recipient=?");args.append(address)
    if through is not None:
        where.append("height<=?");args.append(through)
    total = db.conn.execute("SELECT COUNT(*) FROM wgnk_mints WHERE "+" AND ".join(where),args).fetchone()[0]
    links = links_for(db,address,through)
    by_address = defaultdict(list)
    for link in links:
        by_address[link["gnk_address"]].append(link)
    sources = []
    for native,group in sorted(by_address.items()):
        raw = sum(int(e["amount_raw"]) for e in group)
        sources.append({"address":native,"mints":len(group),"amount_raw":str(raw),"amount":tokens(raw),
                        "first_ts":min(e["gnk_ts"] for e in group),"last_ts":max(e["gnk_ts"] for e in group),
                        "history":sync_state(db,native)})
    return {"total":total,"verified":len(links),"pending":total-len(links),"complete":total>0 and total==len(links),
            "sources":sources,"links":[{**e,"amount":tokens(e["amount_raw"])} for e in links],
            "status":db.get("provenance:status",{}),"history_source":SOURCE,
            "mining_attribution":"not_inferred","ownership_attribution":"not_inferred"}


def balance_state(db,address):
    row=db.conn.execute("SELECT value FROM gonka_address_balances WHERE address=?",(address,)).fetchone()
    return json.loads(row[0]) if row else {"snapshot":None,"last_attempt":None,"next_check":0,"error":None}


def balance_height(headers):
    heights=[headers.get(k) for k in ("x-cosmos-block-height","grpc-metadata-x-cosmos-block-height") if headers.get(k)]
    if not heights or any(not re.fullmatch(r"[1-9][0-9]*",str(h)) for h in heights) or len(set(heights))!=1:
        raise ValueError("Источник не подтвердил высоту снимка баланса Gonka")
    return int(heights[0])


def save_balance(db,address,coin,height,checked_at=None):
    """Keep exact bank balance, separate from historical receipts and module funds."""
    now=int(time.time()) if checked_at is None else checked_at
    if (not GNK.fullmatch(address) or not isinstance(coin,dict) or coin.get("denom")!="ngonka"
            or not isinstance(coin.get("amount"),str) or not re.fullmatch(r"0|[1-9][0-9]*",coin["amount"])
            or type(height) is not int or height<1 or type(now) is not int or now<1):
        raise ValueError("Некорректный снимок баланса Gonka")
    state=balance_state(db,address);old=state["snapshot"]
    if old and (height<old["height"] or (height==old["height"] and coin["amount"]!=old["amount_raw"])):
        raise ValueError("Устаревший или противоречивый снимок баланса Gonka")
    state.update(snapshot={"address":address,"denom":"ngonka","amount_raw":coin["amount"],
        "height":height,"checked_at":now,"source":SOURCE,"scope":"bank_balance"},
        last_attempt=now,next_check=now+BALANCE_INTERVAL,error=None)
    with db.conn:
        db.conn.execute("INSERT OR REPLACE INTO gonka_address_balances VALUES(?,?)",(address,json.dumps(state)))
    db.revision+=1


def incoming_history(db,address,sort="time_desc"):
    field,direction = sort.rsplit("_",1)
    keys = {"time":lambda e:e["ts"],"amount":lambda e:int(e["amount_raw"]),
            "sender":lambda e:e["src"],"kind":lambda e:e["kind"],"tx":lambda e:e["tx_hash"]}
    if field not in keys or direction not in ("asc","desc"):
        raise ValueError("Invalid incoming history sort")
    if not GNK.fullmatch(address):
        raise ValueError("Invalid Gonka address")
    rows = [json.loads(r[0]) for r in db.conn.execute("SELECT value FROM gonka_incoming WHERE address=?",(address,))]
    rows.sort(key=lambda e:(keys[field](e),e["height"],e["tx_hash"],e["event_index"]),reverse=direction=="desc")
    state = sync_state(db,address)
    total = sum(int(e["amount_raw"]) for e in rows)
    balance=balance_state(db,address);snapshot=balance["snapshot"]
    return {"address":address,"items":[{**e,"amount":tokens(e["amount_raw"])} for e in rows],
            "total":len(rows),"amount_raw":str(total),"amount":tokens(total),"sort":sort,
            "history":state,"source":SOURCE,"full_chain_verified":False,
            "address_balance":{**snapshot,"amount":tokens(snapshot["amount_raw"])} if snapshot else None,
            "balance_status":{k:v for k,v in balance.items() if k!="snapshot"},
            "balance_poll_seconds":BALANCE_INTERVAL,"now":int(time.time()),
            "index_head":db.get("provenance:index_head"),
            "coverage_note":"Поступления из адресной истории GonkaLabs. Завершение загрузки означает конец ленты эксплорера, не независимую проверку всей сети. Автоматические выплаты в событиях блока и неиндексированные получатели могут отсутствовать."}


class ProvenanceCollector:
    def __init__(self,owner):
        self.owner,self.db,self.net = owner,owner.db,owner.net
        self.lock = asyncio.Lock()
        self.next_request = 0
        self.cooldown = 0
        self.blocks = {}
        self.gateway_floor = 0
        self.modules = None
        self.balance_chain_checked = 0

    async def request(self,path,params=None,payload=None,include_height=False):
        # One shared limiter for bridge lookups and address history, independent
        # of Ethereum. No arbitrary URL or wallet is accepted from browser input.
        async with self.lock:
            if self.cooldown > time.monotonic():
                raise RuntimeError("GonkaLabs временно недоступен; сохранённые данные не удалены")
            await asyncio.sleep(max(0,self.next_request-time.monotonic()))
            self.next_request = time.monotonic()+.3
        response = await (self.net.client.post(SOURCE+path,json=payload) if payload is not None else
                          self.net.client.get(SOURCE+path,params=params))
        if response.status_code in (429,502,503,504):
            self.cooldown = time.monotonic()+60
        response.raise_for_status()
        result = response.json()
        if isinstance(result,dict) and result.get("error"):
            raise ValueError("GonkaLabs RPC вернул ошибку")
        return {"data":result,"height":balance_height(response.headers)} if include_height else result

    async def native_blocks(self,heights):
        heights=sorted(set(h for h in heights if h not in self.blocks))
        if not heights:return
        if len(heights)>5 or any(h<1 for h in heights):
            raise ValueError("Targeted RPC batch must contain 1..5 requested heights")
        payload=[{"jsonrpc":"2.0","id":f"{m}:{h}","method":m,"params":{"height":str(h)}}
                 for h in heights for m in ("block","block_results")]
        expected={r["id"] for r in payload}
        def accept(rows):
            if not isinstance(rows,list) or len(rows)!=len(payload):
                raise ValueError("Неполный ответ Gonka block/results")
            by_id={r.get("id"):r for r in rows}
            if set(by_id)!=expected or any(r.get("error") for r in rows):
                raise ValueError("Блок Gonka временно недоступен")
            for h in heights:
                b,r=by_id[f"block:{h}"],by_id[f"block_results:{h}"]
                if (b["result"]["block"]["header"]["chain_id"]!="gonka-mainnet" or
                    int(b["result"]["block"]["header"]["height"])!=h or int(r["result"]["height"])!=h):
                    raise ValueError("Gonka block/results chain or height mismatch")
            if len(self.blocks)>100:self.blocks.clear()
            self.blocks.update({h:(by_id[f"block:{h}"],by_id[f"block_results:{h}"]) for h in heights})
        if min(heights)>=self.gateway_floor:
            try:
                rows=await self.request("/chain-rpc/",payload=payload)
                if isinstance(rows,list):
                    for row in rows:
                        floor=re.search(r"lowest height is (\d+)",str(row.get("error","")))
                        if floor:self.gateway_floor=max(self.gateway_floor,int(floor[1]))
                accept(rows);return
            except asyncio.CancelledError:raise
            except Exception:
                pass
        # Only these exact bridge heights, never a contiguous/full-chain scan.
        endpoints=sorted(self.owner.cfg.gonka_archive,key=lambda u:self.net.native_next.get(u.removesuffix('/chain-rpc'),0))
        errors=[]
        for endpoint in endpoints:
            base=endpoint.removesuffix('/chain-rpc')
            now=time.monotonic()
            if self.net.native_cooldown.get(base,0)>now:continue
            slot=max(now,self.net.native_next.get(base,now));self.net.native_next[base]=slot+4
            await asyncio.sleep(max(0,slot-now))
            try:
                response=await self.net.client.post(endpoint+'/',json=payload)
                response.raise_for_status();accept(response.json());return
            except asyncio.CancelledError:raise
            except Exception as error:
                self.net.native_cooldown[base]=time.monotonic()+60
                errors.append(type(error).__name__)
        raise ValueError("Архивные блоки Gonka временно недоступны: "+','.join(errors))

    async def native_block(self,height):
        await self.native_blocks([height])
        return self.blocks[height]

    async def resolve(self,mint,bls=None):
        if bls is None:bls=await self.request("/v1/bls/signatures/"+mint["request_id"][2:])
        height = int(bls["signing_request"]["created_block_height"])
        block,results = await self.native_block(height)
        save_link(self.db,verify_link(mint,bls,block,results))

    async def links(self):
        rows = self.db.conn.execute("""SELECT m.* FROM wgnk_mints m
            LEFT JOIN gonka_mint_links l ON l.tx_hash=m.tx_hash AND l.log_index=m.log_index
            LEFT JOIN gonka_link_attempts a ON a.tx_hash=m.tx_hash AND a.log_index=m.log_index
            WHERE m.finalized=1 AND l.tx_hash IS NULL AND (a.retry_at IS NULL OR a.retry_at<=?)
            ORDER BY m.height DESC,m.log_index DESC LIMIT 6""",(int(time.time()),)).fetchall()
        lookups=await asyncio.gather(*(self.request("/v1/bls/signatures/"+r["request_id"][2:]) for r in rows),return_exceptions=True)
        heights=[]
        for bls in lookups:
            if isinstance(bls,dict):
                try:heights.append(int(bls["signing_request"]["created_block_height"]))
                except (KeyError,TypeError,ValueError):pass
        block_errors={}
        heights=sorted(set(heights))
        for offset in range(0,len(heights),5):
            batch=heights[offset:offset+5]
            try:await self.native_blocks(batch)
            except asyncio.CancelledError:raise
            except Exception as error:block_errors.update({h:error for h in batch})
        async def one(row,bls):
            try:
                if isinstance(bls,Exception):raise bls
                error=block_errors.get(int(bls["signing_request"]["created_block_height"]))
                if error:raise error
                await self.resolve(dict(row),bls)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                old = self.db.conn.execute("SELECT attempts FROM gonka_link_attempts WHERE tx_hash=? AND log_index=?",
                                           (row["tx_hash"],row["log_index"])).fetchone()
                attempts = (old[0] if old else 0)+1
                with self.db.conn:
                    self.db.conn.execute("INSERT OR REPLACE INTO gonka_link_attempts VALUES(?,?,?,?,?)",(
                        row["tx_hash"],row["log_index"],attempts,int(time.time())+min(3600,30*2**min(attempts,6)),
                        (type(error).__name__+": "+str(error))[:240]))
        await asyncio.gather(*(one(row,bls) for row,bls in zip(rows,lookups)))
        pending = self.db.conn.execute("SELECT COUNT(*) FROM gonka_link_attempts").fetchone()[0]
        self.owner.status("provenance:status",retrying=pending)
        return .2 if rows else 20

    async def balances(self):
        # Only existing bridge senders. Opening a UI/API never adds addresses.
        now=int(time.time())
        candidates=[(r[0],balance_state(self.db,r[0])) for r in self.db.conn.execute("SELECT DISTINCT gnk_address FROM gonka_mint_links")]
        candidates=[(a,s) for a,s in candidates if s["next_check"]<=now]
        if not candidates:return 3
        address,state=min(candidates,key=lambda item:item[1].get("last_attempt") or 0)
        try:
            if now-self.balance_chain_checked>60:
                status=(await self.request("/chain-rpc/status"))["result"]
                if status["node_info"]["network"]!="gonka-mainnet" or status["sync_info"]["catching_up"]:
                    raise ValueError("Источник баланса Gonka не синхронизирован с основной сетью")
                self.balance_chain_checked=now
            reply=await self.request("/chain-api/cosmos/bank/v1beta1/balances/"+address+"/by_denom",
                                     {"denom":"ngonka"},include_height=True)
            save_balance(self.db,address,reply["data"]["balance"],reply["height"])
        except asyncio.CancelledError:raise
        except Exception as error:
            state.update(error=(type(error).__name__+": "+str(error))[:240],last_attempt=now,next_check=now+60)
            with self.db.conn:
                self.db.conn.execute("INSERT OR REPLACE INTO gonka_address_balances VALUES(?,?)",(address,json.dumps(state)))
            self.db.revision+=1
        return .2

    async def incoming(self):
        addresses = [r[0] for r in self.db.conn.execute("SELECT DISTINCT gnk_address FROM gonka_mint_links")]
        if not addresses:
            return 5
        now = int(time.time())
        candidates = [(a,sync_state(self.db,a)) for a in addresses]
        candidates = [(a,s) for a,s in candidates if s["next_check"]<=now]
        if not candidates:
            return 5
        candidates.sort(key=lambda pair:pair[1].get("checked_at") or 0)
        address,state = candidates[0]
        try:
            if self.modules is None:
                data = await self.request("/chain-api/cosmos/auth/v1beta1/module_accounts")
                self.modules = {a["base_account"]["address"]:a["name"] for a in data["accounts"]}
            if now-self.db.get("provenance:index_head",{}).get("checked_at",0)>60:
                head = (await self.request("/api/ch/blocks/latest"))["block"]
                self.db.put("provenance:index_head",{"height":int(head["block_height"]),"checked_at":now})
            page = await self.request("/api/ch/address/"+address,{"limit":PAGE,"offset":state["offset"]})
            save_page(self.db,address,page,self.modules)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Keep the last successful cursor and all transfers on any failure.
            state.update(error=(type(error).__name__+": "+str(error))[:240],next_check=now+120,checked_at=now)
            with self.db.conn:
                self.db.conn.execute("INSERT OR REPLACE INTO gonka_address_sync VALUES(?,?)",(address,json.dumps(state)))
        return .2
