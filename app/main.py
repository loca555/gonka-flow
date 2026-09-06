import asyncio
import csv
import io
import json
import re
import time
from hashlib import sha256
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from .config import Settings, TOKEN
from .db import Database, tokens
from .indexer import Indexer
from .analytics import overview, hosts, rankings, bridges
from .holders import holders_list,address_page,ETH_ADDRESS,GNK_ADDRESS
from .mints import MintIndexer, listing as mint_listing, public_event as mint_event, TOKEN as MINT_TOKEN
from .flows import analysis as flow_analysis, bridge_listing
from .leaders import trade_leaders
from .timezones import local_time, TIME_ZONE
from .seed import restore_seed
from .provenance import overview as provenance_overview, incoming_history, GNK, links_for
from .redemptions import native_addresses

STATIC = Path(__file__).parent / "static"
MINT_SORT_PATTERN = "^(newest|oldest|largest|(time|recipient|amount|tx|status)_(asc|desc))$"
TRADE_SORT_PATTERN = "^(time|kind|actor|amount|quote|price|pool|tx)_(asc|desc)$"
BRIDGE_SORT_PATTERN = "^(newest|oldest|largest|(time|kind|recipient|amount|tx|status)_(asc|desc))$"

def versioned_page(filename):
    """A new asset URL for changed code, including browsers with an older cached JS file."""
    html = (STATIC / filename).read_text(encoding="utf-8")
    def version(match):
        digest = sha256((STATIC / match[2]).read_bytes()).hexdigest()[:16]
        return f'{match[1]}/static/{match[2]}?v={digest}"'
    return re.sub(r'((?:src|href)=")/static/([a-zA-Z0-9._/-]+\.(?:js|css))"', version, html)

def create_app(settings=None):
    cfg = settings or Settings()
    cache, hits = {}, defaultdict(deque)
    index_html = versioned_page("mints.html" if cfg.mode == "mints" else "index.html")

    @asynccontextmanager
    async def lifespan(app):
        if cfg.mode == "mints" and cfg.seed_enabled:
            restore_seed(cfg.data_dir / "gonka-flow.sqlite3")
        db = Database(cfg.data_dir / "gonka-flow.sqlite3")
        indexer = MintIndexer(cfg, db) if cfg.mode == "mints" else Indexer(cfg, db)
        app.state.db, app.state.indexer = db, indexer
        if cfg.indexer_enabled:
            indexer.start()
        try:
            yield
        finally:
            await indexer.stop()
            db.close()

    app = FastAPI(title="Gonka Flow", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def headers_and_limits(request: Request, call_next):
        if (cfg.mode == "mints" and request.url.path.startswith("/api/")
                and not (request.url.path == "/api/docs" or request.url.path == "/api/mints"
                         or request.url.path.startswith("/api/mints/"))):
            return JSONResponse({"detail":"Этот раздел отключён. Активен только монитор чеканки WGNK.",
                                 "mode":"mints"},410,headers={"Cache-Control":"no-store"})
        if request.url.path.startswith("/api/"):
            now = time.monotonic()
            ip = request.client.host if request.client else "local"
            if ip not in hits and len(hits) > 10000:
                hits.clear()
            window = hits[ip]
            while window and window[0] < now-60:
                window.popleft()
            if len(window) >= 180:
                return JSONResponse({"detail":"Слишком много запросов; повторите через минуту."},429,
                                    headers={"Retry-After":"60"})
            window.append(now)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/") and Path(request.url.path).suffix in (".js", ".css", ".html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/")
    async def index():
        return HTMLResponse(index_html, headers={"Cache-Control":"no-cache"})

    @app.get("/healthz")
    async def health():
        return {"ok":True, "indexer_enabled":cfg.indexer_enabled, "mode":cfg.mode, "version":"0.2.0"}

    @app.get("/api/docs",include_in_schema=False)
    async def api_docs():
        return FileResponse(STATIC / ("mints-api.html" if cfg.mode == "mints" else "api.html"))

    @app.get("/api/mints")
    async def get_mints(request: Request, minimum: int=Query(10000,ge=0,le=10**12),
                        hours: int=Query(0,ge=0,le=175200),
                        q: str=Query("",max_length=66,pattern="^(|0x[0-9a-fA-F]{1,64})$"),
                        finality: str=Query("finalized",pattern="^(finalized|all)$"),
                        sort: str=Query("newest",pattern=MINT_SORT_PATTERN),
                        limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0,le=5000000)):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор чеканки отключён")
        key=("mints",minimum,hours,q.lower(),finality,sort,limit,offset)
        if len(cache)>1000: cache.clear()
        if key not in cache or time.time()-cache[key][0]>3:
            data=mint_listing(request.app.state.db,minimum,hours,q.lower(),finality,sort,limit,offset)
            data["indexer_enabled"]=cfg.indexer_enabled
            data["workers"]=[t.get_name() for t in request.app.state.indexer.tasks if not t.done()]
            cache[key]=(time.time(),data)
        return cache[key][1]

    @app.get("/api/mints/flows")
    async def mint_flows(request:Request,hours:int=Query(0,ge=0,le=175200),
                         q:str=Query("",max_length=66,pattern="^(|0x[0-9a-fA-F]{1,64})$"),
                         limit:int=Query(25,ge=1,le=200),offset:int=Query(0,ge=0,le=5000000),
                         side:str=Query("sell",pattern="^(sell|buy|all)$"),
                         sort:str=Query("time_desc",pattern=TRADE_SORT_PATTERN)):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор WGNK отключён")
        key=("flows",hours,q.lower(),limit,offset,side,sort)
        if len(cache)>1000: cache.clear()
        if key not in cache or time.time()-cache[key][0]>8:
            cache[key]=(time.time(),flow_analysis(request.app.state.db,hours,q.lower(),limit,offset,side,sort))
        return cache[key][1]

    @app.get("/api/mints/leaders")
    async def mint_leaders(request:Request):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор WGNK отключён")
        key=("trade_leaders",)
        if len(cache)>1000: cache.clear()
        if key not in cache or time.time()-cache[key][0]>8:
            cache[key]=(time.time(),trade_leaders(request.app.state.db))
        return cache[key][1]

    @app.get("/api/mints/address/{address}")
    async def mint_address(request:Request,address:str,sort:str=Query("time_desc",pattern=TRADE_SORT_PATTERN)):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор WGNK отключён")
        if not re.fullmatch("0x[0-9a-fA-F]{40}",address): raise HTTPException(400,"Нужен полный Ethereum-адрес")
        address=address.lower()
        key=("address_trades",address,sort)
        if len(cache)>1000: cache.clear()
        if key not in cache or time.time()-cache[key][0]>8:
            cache[key]=(time.time(),flow_analysis(request.app.state.db,q=address,side="all",sort=sort,limit=None))
        return cache[key][1]

    @app.get("/api/mints/bridge")
    async def bridge_feed(request:Request,minimum:int=Query(10000,ge=0,le=10**12),
                          hours:int=Query(0,ge=0,le=175200),
                          q:str=Query("",max_length=66,pattern="^(|0x[0-9a-fA-F]{1,64})$"),
                          sort:str=Query("newest",pattern=BRIDGE_SORT_PATTERN),
                          limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0,le=5000000)):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор WGNK отключён")
        key=("bridge",minimum,hours,q.lower(),sort,limit,offset)
        if len(cache)>1000: cache.clear()
        if key not in cache or time.time()-cache[key][0]>3:
            cache[key]=(time.time(),bridge_listing(request.app.state.db,minimum,hours,q.lower(),sort,limit,offset))
        return cache[key][1]

    @app.get("/api/mints/bridge/export.csv")
    async def bridge_export(request:Request,minimum:int=Query(10000,ge=0,le=10**12),
                            sort:str=Query("newest",pattern=BRIDGE_SORT_PATTERN)):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор WGNK отключён")
        data=bridge_listing(request.app.state.db,minimum=minimum,sort=sort,limit=50000)
        if not data["ready"]: raise HTTPException(503,"Сверенный снимок моста ещё не готов")
        if data["has_more"]: raise HTTPException(413,"Слишком много событий для одного CSV")
        output=io.StringIO()
        fields=["kind","time_local","address","amount","amount_raw","tx_hash","log_index","height","finalized"]
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
        for e in data["items"]:
            writer.writerow({k:local_time(e["ts"]) if k=="time_local" else e[k] for k in fields})
        return Response(output.getvalue(),media_type="text/csv",
                        headers={"Content-Disposition":'attachment; filename="wgnk-bridge.csv"'})

    @app.get("/api/mints/provenance")
    async def mint_provenance(request:Request,address:str=Query("",pattern="^(|0x[0-9a-fA-F]{40})$")):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор WGNK отключён")
        return provenance_overview(request.app.state.db,address.lower() or None)

    @app.get("/api/mints/gonka/{address}")
    async def gonka_incoming(request:Request,address:str,
                            sort:str=Query("time_desc",pattern="^(time|amount|sender|kind|tx)_(asc|desc)$")):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор WGNK отключён")
        if not GNK.fullmatch(address): raise HTTPException(400,"Нужен полный адрес Gonka")
        db=request.app.state.db
        if address not in native_addresses(db):
            raise HTTPException(404,"Адрес ещё не связан с подтверждённым переводом через мост WGNK")
        return incoming_history(db,address,sort)

    @app.get("/api/mints/tx/{tx_hash}")
    async def mint_transaction(request:Request,tx_hash:str):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор чеканки отключён")
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}",tx_hash): raise HTTPException(400,"Нужен хеш Ethereum-транзакции")
        rows=request.app.state.db.conn.execute("SELECT * FROM wgnk_mints WHERE tx_hash=? ORDER BY log_index",
                                               (tx_hash.lower(),)).fetchall()
        if not rows: raise HTTPException(404,"Чеканка в этой транзакции не найдена в индексе")
        return {"contract":MINT_TOKEN,"tx_hash":tx_hash.lower(),"items":[mint_event(r) for r in rows],
                "native_links":[e for e in links_for(request.app.state.db) if e["tx_hash"]==tx_hash.lower()]}

    @app.get("/api/mints/export.csv")
    async def export_mints(request:Request,minimum:int=Query(0,ge=0,le=10**12),
                           hours:int=Query(0,ge=0,le=175200),
                           q:str=Query("",max_length=66,pattern="^(|0x[0-9a-fA-F]{1,64})$"),
                           finality:str=Query("finalized",pattern="^(finalized|all)$"),
                           sort:str=Query("newest",pattern=MINT_SORT_PATTERN)):
        if cfg.mode!="mints": raise HTTPException(404,"Монитор чеканки отключён")
        data=mint_listing(request.app.state.db,minimum,hours,q.lower(),finality,sort,50000,0)
        if data["has_more"]: raise HTTPException(413,"Более 50 000 выпусков. Уменьшите период.")
        output=io.StringIO()
        columns=["tx_hash","log_index","height","block_hash","ts","time_local","timezone","recipient","amount","amount_raw",
                 "epoch_id","request_id","finalized","source"]
        writer=csv.DictWriter(output,columns,extrasaction="ignore")
        writer.writeheader()
        for e in data["items"]: writer.writerow({**e,"time_local":local_time(e["ts"]),"timezone":TIME_ZONE})
        return Response("\ufeff"+output.getvalue(),media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":'attachment; filename="wgnk-mints.csv"'})

    @app.get("/api/overview")
    async def get_overview(request: Request, hours: int = Query(24, ge=1, le=168),
                           since: int | None = Query(None, ge=0, le=4102444800)):
        key = ("overview",hours,since)
        if len(cache)>1000:
            cache.clear()
        if key not in cache or time.time()-cache[key][0] > 4:
            cache[key] = (time.time(), overview(request.app.state.db,hours,since))
        return cache[key][1]

    @app.get("/api/events")
    async def get_events(request: Request, hours: int = Query(24,ge=1,le=168),
                         kind: str = Query("",max_length=180), chain: str = Query("",pattern="^(|gonka|ethereum)$"),
                         address: str = Query("",max_length=100), limit: int = Query(50,ge=1,le=200),
                         offset: int = Query(0,ge=0,le=5000000), minimum: int = Query(0,ge=0,le=10**12),
                         since: int | None = Query(None,ge=0,le=4102444800)):
        return request.app.state.db.events(int(time.time())-hours*3600 if since is None else since, kind, chain, address,
                                           limit,offset,minimum*10**9)

    @app.get("/api/hosts")
    async def get_hosts(request: Request):
        return hosts(request.app.state.db)

    @app.get("/api/labels")
    async def get_labels(request: Request):
        return request.app.state.db.labels()

    @app.get("/api/holders")
    async def get_holders(request: Request, asset: str=Query("GNK",pattern="^(GNK|WGNK)$"),
                          hours: int=Query(24,ge=0,le=168), minimum: int=Query(10000,ge=0,le=10**12),
                          tag: str=Query("",pattern="^(|buy|sell|both|none)$"),
                          q: str=Query("",max_length=100), limit: int=Query(50,ge=1,le=200),
                          offset: int=Query(0,ge=0,le=5000000),
                          since: int | None = Query(None,ge=0,le=4102444800)):
        return holders_list(request.app.state.db,asset,hours,minimum,tag,q,limit,offset,since)

    @app.get("/api/addresses/{address}")
    async def get_address(request: Request,address: str,hours: int=Query(0,ge=0,le=168),
                          kind: str=Query("trades",pattern="^(trades|all)$"),
                          limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0,le=5000000),
                          since: int | None = Query(None,ge=0,le=4102444800)):
        address=address.lower()
        if not (ETH_ADDRESS.fullmatch(address) or GNK_ADDRESS.fullmatch(address)):
            raise HTTPException(400,"Нужен корректный адрес Gonka или Ethereum")
        return address_page(request.app.state.db,address,hours,kind,limit,offset,since)

    @app.get("/api/rankings")
    async def get_rankings(request: Request, hours: int=Query(24,ge=1,le=168),
                           mode: str=Query("sell",pattern="^(buy|sell|rewards|unlocks)$"),
                           since: int | None = Query(None,ge=0,le=4102444800)):
        return rankings(request.app.state.db,hours,mode,since)

    @app.get("/api/bridges")
    async def get_bridges(request: Request, hours: int=Query(24,ge=1,le=168),
                          limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0,le=5000000),
                          since: int | None = Query(None,ge=0,le=4102444800)):
        return bridges(request.app.state.db,hours,limit,offset,since)

    @app.get("/api/wallet/{address}")
    async def wallet(request: Request, address: str, hours: int=Query(24,ge=1,le=168),
                     since: int | None = Query(None,ge=0,le=4102444800)):
        address = address.lower()
        if not re.fullmatch(r"(0x[0-9a-f]{40}|gonka1[02-9ac-hj-np-z]{38})",address):
            raise HTTPException(400,"Нужен полный адрес Gonka (gonka1…) или Ethereum (0x…).")
        db, net = request.app.state.db, request.app.state.indexer.net
        key = ("balance",address)
        if key not in cache or time.time()-cache[key][0] > 45:
            try:
                if address.startswith("0x"):
                    raw = await net.call(TOKEN,"balanceOf(address)",address[2:].zfill(64))
                    balance = {"asset":"WGNK","amount":tokens(int(raw,16))}
                else:
                    result = await net.api("/cosmos/bank/v1beta1/balances/"+address+"/by_denom",{"denom":"ngonka"})
                    balance = {"asset":"GNK","amount":tokens(result["balance"]["amount"])}
                cache[key] = (time.time(),{"balance":balance,"updated_at":int(time.time()),"error":None})
            except Exception:
                cache[key] = (time.time(),{"balance":None,"updated_at":None,
                                         "error":"RPC не вернул баланс; это не нулевой баланс."})
            if len(cache)>1000:
                for stale in list(cache)[:200]:
                    cache.pop(stale,None)
        activity = db.events(int(time.time())-hours*3600 if since is None else since,address=address,limit=100)
        linked = []
        for b in bridges(db,hours,limit=5000000,since=since)["items"]:
            if address in (b["source"],b["target"]):
                linked.append(b)
        return {"address":address,**cache[key][1],"label":db.labels().get(address),
                "events":activity,"bridges":linked[:50],"hours":hours}

    @app.get("/api/export.csv")
    async def export(request: Request, hours: int=Query(24,ge=1,le=168),
                     since: int | None = Query(None,ge=0,le=4102444800)):
        rows = request.app.state.db.events(int(time.time())-hours*3600 if since is None else since,limit=50000)
        if rows["has_more"]:
            raise HTTPException(413,"В окне больше 50 000 событий. Уменьшите период.")
        output = io.StringIO()
        fields = ["chain","height","ts","tx_hash","idx","kind","src","dst","actor","amount","asset",
                  "quote_amount","quote_asset","pool","finalized","request_key"]
        writer = csv.DictWriter(output,fields,extrasaction="ignore")
        writer.writeheader()
        for e in rows["items"]:
            writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(("=","+","-","@")) else v)
                             for k,v in e.items()})
        return Response("\ufeff"+output.getvalue(),media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":'attachment; filename="gonka-flow-events.csv"'})

    @app.get("/api/stream")
    async def stream(request: Request):
        async def generate():
            last = -1
            while not await request.is_disconnected():
                revision = request.app.state.db.revision
                if revision != last:
                    yield "data: "+json.dumps({"revision":revision,"now":int(time.time())})+"\n\n"
                    last = revision
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(5)
        return StreamingResponse(generate(),media_type="text/event-stream",
                                 headers={"X-Accel-Buffering":"no","Cache-Control":"no-cache"})

    app.mount("/static",StaticFiles(directory=STATIC,check_dir=False),name="static")
    return app

app = create_app()
