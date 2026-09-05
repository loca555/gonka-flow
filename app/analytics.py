import json
import time
from .db import tokens
from .history import archive_progress, period_coverage

def overview(db, hours, since=None):
    since = int(time.time()) - hours * 3600 if since is None else since
    grouped = db.conn.execute("""
        SELECT kind,chain,asset,count(*) n,sumint(amount_raw) amount,
        sumint(quote_raw) quote,quote_asset FROM events WHERE ts>=?
        GROUP BY kind,chain,asset,quote_asset""", (since,)).fetchall()
    totals = [{**dict(r), "amount":tokens(r["amount"]), "quote":tokens(r["quote"],6)} for r in grouped]
    buckets = db.conn.execute("""
        SELECT CAST(ts/3600 AS INTEGER)*3600 ts,kind,sumint(amount_raw) amount,count(*) n
        FROM events WHERE ts>=? AND kind IN ('buy','sell','bridge_lock','bridge_burn','reward_paid','vesting_unlock')
        GROUP BY CAST(ts/3600 AS INTEGER),kind ORDER BY ts""", (since,)).fetchall()
    counts = db.conn.execute("SELECT count(*),MIN(ts),MAX(ts) FROM events").fetchone()
    activity = db.conn.execute("""
        SELECT chain,count(*) events,count(DISTINCT tx_hash) transactions
        FROM events WHERE ts>=? GROUP BY chain""", (since,)).fetchall()
    status = {name: db.get("status:"+name, {"ok":False, "error": "Запуск источника"}) for name in
              ("gonka","gonka_history","ethereum","ethereum_history","network","market","bridge")}
    return {"now": int(time.time()), "hours": hours, "since": since, "revision": db.revision,
            "history_request": db.get("history_request"),
            "archive": {c: archive_progress(db,c) for c in ("gonka","ethereum")},
            "period_complete": {c: period_coverage(db,c,since) for c in ("gonka","ethereum")},
            "totals": totals, "buckets": [{**dict(r), "amount":tokens(r["amount"])} for r in buckets],
            "status": status, "coverage": {c: db.coverage(c) for c in ("gonka","ethereum")},
            "activity": [dict(r) for r in activity], "stored_events":counts[0],
            "first_event":counts[1], "last_event":counts[2],
            "snapshots": {k: db.get(k) for k in ("supply","wgnk_supply","escrow","tokenomics","market")},
            "pools": db.get("verified_pools",{}), "undecoded_pools": db.get("undecoded_pools",[])}

def hosts(db):
    snap = db.get("hosts")
    if not snap:
        return {"items":[], "total_weight":"0", "epoch":None, "updated_at":None}
    data = snap["data"]
    participants = data.get("participants", [])
    total = sum(max(0, int(p.get("weight",0))) for p in participants)
    items = []
    for p in participants:
        weight = max(0, int(p.get("weight",0)))
        items.append({"address":p["index"], "weight":str(weight),
                      "share": weight / total * 100 if total else 0,
                      "models":p.get("models",[]), "endpoint":p.get("inference_url",""),
                      "voting_powers":p.get("voting_powers",[])})
    items.sort(key=lambda x:int(x["weight"]), reverse=True)
    return {"items":items, "total_weight":str(total), "epoch":data.get("epoch_id"),
            "updated_at":snap["updated_at"], "block":data.get("effective_block_height"),
            "top10_share":sum(x["share"] for x in items[:10])}

def rankings(db, hours, mode, since=None):
    since = int(time.time()) - hours * 3600 if since is None else since
    if mode in ("buy","sell"):
        rows = db.conn.execute("""
            SELECT actor address,kind,count(*) n,sumint(amount_raw) raw,sumint(quote_raw) quote,
            MIN(ts) first,MAX(ts) last FROM events
            WHERE ts>=? AND kind=? AND actor!=''
            AND json_extract(meta,'$.attribution')='initiator_net'
            GROUP BY actor,kind""", (since,mode)).fetchall()
    else:
        kinds = ("reward_paid","reward_vested") if mode == "rewards" else ("vesting_unlock",)
        rows = db.conn.execute(f"""
            SELECT dst address,kind,count(*) n,sumint(amount_raw) raw,'0' quote,
            MIN(ts) first,MAX(ts) last FROM events
            WHERE ts>=? AND kind IN ({','.join('?' for _ in kinds)}) AND dst!=''
            GROUP BY dst,kind""", [since]+list(kinds)).fetchall()
    results = sorted([dict(r) for r in rows], key=lambda x:int(x["raw"]), reverse=True)[:100]
    for r in results:
        r["amount"] = tokens(r.pop("raw"))
        r["quote"] = tokens(r["quote"],6)
    notes = {
        "rewards": "Получатели GNK: выплаченные награды и начисления в вестинг показаны раздельно; это не чистая прибыль майнинга.",
        "unlocks": "Получатели разблокированного GNK из вестинга; это не новые награды и не подтверждённые продажи."
    }
    return {"items":results, "mode":mode, "hours":hours, "since":since,
            "note":notes.get(mode,"Адреса инициаторов с подтверждённым направлением WGNK; это не позиции и не личности.")}

def bridges(db, hours, limit=100, offset=0, since=None):
    since = int(time.time())-hours*3600 if since is None else since
    rows = db.conn.execute("""
        SELECT * FROM events WHERE ts>=? AND kind IN ('bridge_lock','bridge_mint','bridge_burn','bridge_release')
        ORDER BY ts DESC""", (since,)).fetchall()
    results, seen = [], set()
    for row in rows:
        e = db.event(row)
        direction = "out" if e["kind"] in ("bridge_lock","bridge_mint") else "in"
        key = (direction, e["request_key"] or e["id"], e["amount_raw"])
        if key in seen:
            continue
        seen.add(key)
        peer_kind = {"bridge_lock":"bridge_mint","bridge_mint":"bridge_lock",
                     "bridge_burn":"bridge_release","bridge_release":"bridge_burn"}[e["kind"]]
        peers = db.conn.execute("SELECT * FROM events WHERE request_key=? AND kind=? AND amount_raw=?",
                                (e["request_key"],peer_kind,e["amount_raw"])).fetchall() if e["request_key"] else []
        peer = db.event(peers[0]) if len(peers)==1 else None
        native = e if e["chain"]=="gonka" else peer
        eth = e if e["chain"]=="ethereum" else peer
        if direction == "out" and native and eth and native["meta"].get("destination") != eth["dst"]:
            peer = None
            native = e if e["chain"]=="gonka" else None
            eth = e if e["chain"]=="ethereum" else None
        recipient_record = None
        if direction == "in" and eth:
            checked = db.conn.execute("SELECT value FROM bridge_receipts WHERE event_id=?", (eth["id"],)).fetchone()
            recipient_record = json.loads(checked[0]) if checked else None
        completed = bool(native and eth) or bool(recipient_record and recipient_record["status"]=="completed")
        status = "completed" if completed else ("awaiting_counterpart" if
                 e["kind"] in ("bridge_lock","bridge_burn") else "origin_not_indexed")
        if eth and not eth["finalized"]:
            status = "provisional"
        source = native["src"] if direction=="out" and native else (eth["src"] if direction=="in" and eth else "")
        target = eth["dst"] if direction=="out" and eth else (native["dst"] if direction=="in" and native else "")
        if not target and recipient_record and recipient_record.get("record"):
            target = recipient_record["record"].get("ownerAddress","")
        if not target and direction=="out" and native:
            target = native["meta"].get("destination","")
        results.append({"id":e["id"],"ts":e["ts"],"direction":direction,"amount":e["amount"],
                        "status":status,"source":source,"target":target,"native":native,"ethereum":eth,
                        "receipt":recipient_record,"key":e["request_key"],
                        "seconds":(abs(native["ts"]-eth["ts"]) if native and eth else None)})
    results.sort(key=lambda x:x["ts"],reverse=True)
    return {"items":results[offset:offset+limit],"total":len(results),"offset":offset,
            "has_more":offset+limit<len(results)}
