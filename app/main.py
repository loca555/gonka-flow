import asyncio
import csv
import io
import json
import re
import time
from hashlib import sha256
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date as calendar_date
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from .config import Settings, TOKEN
from .db import Database, tokens
from .indexer import Indexer
from .analytics import overview, hosts, rankings, bridges
from .holders import holders_list,address_page,ETH_ADDRESS,GNK_ADDRESS
from .mints import MintIndexer, listing as mint_listing, public_event as mint_event, TOKEN as MINT_TOKEN
from .flows import analysis as flow_analysis, bridge_listing, trade_page
from .leaders import trade_leaders
from .timezones import local_time, TIME_ZONE
from .seed import restore_seed
from . import snapshots
from .api_policy import APIAdmission, PublicAPIGuard, ResponseCache
from .provenance import overview as provenance_overview, incoming_history, GNK, links_for
from .redemptions import native_addresses
from .read_api import ReadAPI

STATIC = Path(__file__).parent / "static"
MINT_SORT_PATTERN = "^(newest|oldest|largest|(time|recipient|amount|tx|status)_(asc|desc))$"
TRADE_SORT_PATTERN = "^(time|kind|actor|amount|quote|price|pool|tx|pnl)_(asc|desc)$"
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
    cache = ResponseCache(cfg.api_cache_entries, cfg.api_cache_bytes)
    index_html = versioned_page("mints.html" if cfg.mode == "mints" else "index.html")

    def local_otc(request):
        return cfg.otc_local_enabled and request.url.hostname in ("localhost", "127.0.0.1")

    async def ping_self():
        """Free hosting discards the runtime database after idle sleeps, forcing a
        full history reload on the next visit. A periodic self-request keeps the
        single instance warm; it is opt-out via KEEP_ALIVE_ENABLED=false."""
        import logging
        import httpx
        log = logging.getLogger("gonka-flow.keepalive")
        url = cfg.keep_alive_url.rstrip("/") + "/healthz"
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            while True:
                try:
                    response = await client.get(url)
                    if response.status_code != 200:
                        log.debug("keep-alive %s -> HTTP %s", url, response.status_code)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    log.debug("keep-alive %s failed: %s", url, str(error)[:120])
                await asyncio.sleep(cfg.keep_alive_seconds)

    @asynccontextmanager
    async def lifespan(app):
        if cfg.mode == "mints" and cfg.seed_enabled:
            await snapshots.restore_fresh(cfg.data_dir / "gonka-flow.sqlite3", cfg)
        db = Database(cfg.data_dir / "gonka-flow.sqlite3")
        from .attribution import migrate
        migrate(db)
        indexer = MintIndexer(cfg, db) if cfg.mode == "mints" else Indexer(cfg, db)
        app.state.db, app.state.indexer = db, indexer
        app.state.read_api = ReadAPI(db, cache, max_pending=cfg.api_max_concurrent)
        indexer.read_api = app.state.read_api
        keep_alive = (asyncio.create_task(ping_self(), name="keep_alive")
                      if cfg.keep_alive_enabled and cfg.keep_alive_url else None)
        if cfg.indexer_enabled:
            indexer.start()
        try:
            yield
        finally:
            if keep_alive:
                keep_alive.cancel()
                await asyncio.gather(keep_alive, return_exceptions=True)
            await indexer.stop()
            await app.state.read_api.close()
            db.close()

    app = FastAPI(title="Gonka Flow", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    admission = APIAdmission(cfg)
    app.state.api_admission = admission
    app.state.response_cache = cache
    app.add_middleware(PublicAPIGuard, admission=admission)

    @app.middleware("http")
    async def headers_and_limits(request: Request, call_next):
        if (cfg.mode == "mints" and request.url.path.startswith("/api/")
                and not (request.url.path == "/api/docs" or request.url.path == "/api/mints"
                         or request.url.path.startswith("/api/mints/"))):
            response = JSONResponse({"detail":"This section is disabled. Only the WGNK mint monitor is active.",
                                     "mode":"mints"},410)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            + ("connect-src 'self' http://127.0.0.1:8791 http://127.0.0.1:8793 http://127.0.0.1:8794; " if local_otc(request)
               else "connect-src 'self'; ")
            + "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/") and Path(request.url.path).suffix in (".js", ".css", ".html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # Public read-only data is also consumed by the IPFS frontend on gateway origins.
    # Register outside headers_and_limits so API errors retain CORS headers too.
    app.add_middleware(CORSMiddleware, allow_origins=cfg.api_allowed_origins,
                       allow_methods=["GET"], allow_credentials=False,
                       expose_headers=["Retry-After"])

    @app.get("/")
    async def index(request: Request):
        html = index_html.replace('data-otc-enabled="false"', 'data-otc-enabled="true"') if local_otc(request) else index_html
        return HTMLResponse(html, headers={"Cache-Control":"no-cache"})

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
        if cfg.mode!="mints": raise HTTPException(404,"Mint monitor is disabled")
        key=("mints",minimum,hours,q.lower(),finality,sort,limit,offset)
        def build(db):
            data=mint_listing(db,minimum,hours,q.lower(),finality,sort,limit,offset)
            data["indexer_enabled"]=cfg.indexer_enabled
            data["workers"]=[t.get_name() for t in request.app.state.indexer.tasks if not t.done()]
            return data
        return await request.app.state.read_api.run(build,key=key,ttl=3)

    @app.get("/api/mints/flows")
    async def mint_flows(request:Request,hours:int=Query(0,ge=0,le=175200),
                         q:str=Query("",max_length=66,pattern="^(|0x[0-9a-fA-F]{1,64})$"),
                         limit:int=Query(25,ge=1,le=200),offset:int=Query(0,ge=0,le=5000000),
                         side:str=Query("sell",pattern="^(sell|buy|all)$"),
                         sort:str=Query("time_desc",pattern=TRADE_SORT_PATTERN),
                         minimum:int=Query(0,ge=0,le=10**12)):
        if cfg.mode!="mints": raise HTTPException(404,"WGNK monitor is disabled")
        key=("flows",hours,q.lower(),limit,offset,side,sort,minimum)
        return await request.app.state.read_api.run(
            lambda db: flow_analysis(db,hours,q.lower(),limit,offset,side,sort,minimum),key=key,ttl=8)

    @app.get("/api/mints/trades")
    async def mint_trade_page(request:Request,through:int=Query(...,ge=1),as_of:int=Query(...,ge=0),
                              hours:int=Query(0,ge=0,le=175200),
                              q:str=Query("",max_length=66,pattern="^(|0x[0-9a-fA-F]{1,64})$"),
                              limit:int=Query(25,ge=1,le=200),offset:int=Query(0,ge=0,le=5000000),
                              side:str=Query("sell",pattern="^(sell|buy|all)$"),
                              sort:str=Query("time_desc",pattern=TRADE_SORT_PATTERN),
                              minimum:int=Query(0,ge=0,le=10**12),
                              snapshot_hash:str=Query("",pattern="^(|0x[0-9a-fA-F]{64})$")):
        if cfg.mode!="mints":raise HTTPException(404,"WGNK monitor is disabled")
        return await request.app.state.read_api.run(lambda db: trade_page(db,through=through,as_of=as_of,hours=hours,
                                       q=q.lower(),limit=limit,offset=offset,side=side,sort=sort,minimum=minimum,
                                       snapshot_hash=snapshot_hash.lower()))

    @app.get("/api/mints/holders")
    async def mint_holders(request:Request,asset:str=Query("WGNK",pattern="^(WGNK|GNK)$"),
                           q:str=Query("",max_length=90,pattern="^[a-zA-Z0-9]*$"),
                           limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0,le=5000000)):
        if cfg.mode!="mints": raise HTTPException(404,"WGNK monitor is disabled")
        from .public_holders import listing
        key=("public_holders",asset,q.lower(),limit,offset)
        def build(db):
            data=listing(db,asset,q,limit,offset)
            data["indexer_enabled"]=cfg.indexer_enabled
            return data
        return await request.app.state.read_api.run(build,key=key,ttl=8)

    @app.get("/api/mints/leaders")
    async def mint_leaders(request:Request,start_date:calendar_date|None=Query(None),
                           grouped:bool=Query(True)):
        start_date=start_date.isoformat() if start_date else None
        if cfg.mode!="mints": raise HTTPException(404,"WGNK monitor is disabled")
        db=request.app.state.db
        key=("trade_leaders",db.revision,start_date,grouped)
        return await request.app.state.read_api.run(
            lambda reader: trade_leaders(reader,start_date=start_date,grouped=grouped),key=key,ttl=8)

    @app.get("/api/mints/address/{address}")
    async def mint_address(request:Request,address:str,sort:str=Query("time_desc",pattern=TRADE_SORT_PATTERN),
                           limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0,le=5000000),
                           through:int|None=Query(None,ge=1),as_of:int|None=Query(None,ge=0),
                           snapshot_hash:str=Query("",pattern="^(|0x[0-9a-fA-F]{64})$")):
        if cfg.mode!="mints": raise HTTPException(404,"WGNK monitor is disabled")
        if not re.fullmatch("0x[0-9a-fA-F]{40}",address): raise HTTPException(400,"A full Ethereum address is required")
        address=address.lower()
        pinned=through is not None or as_of is not None or bool(snapshot_hash)
        if pinned and (through is None or as_of is None or not snapshot_hash):
            raise HTTPException(400,"The page requires the snapshot block, time and hash. Refresh the address history.")
        db=request.app.state.db
        key=("address_trades",db.revision,address,sort,limit,offset,through,as_of,snapshot_hash.lower())
        def build(db):
            if pinned:
                data=trade_page(db,through=through,as_of=as_of,snapshot_hash=snapshot_hash.lower(),
                                q=address,side="all",sort=sort,limit=limit,offset=offset)
                if not data["ready"]:
                    raise HTTPException(409,"The snapshot of this history is no longer available. Click “Refresh”.")
            else:
                full=flow_analysis(db,q=address,side="all",sort=sort,limit=limit,offset=offset)
                # Address views need full-history aggregates/chart, never all other minters/holders.
                fields=("ready","now","timezone","snapshot","coverage","status","summary","pools",
                        "address_balance","address_history","trades","total","offset","limit","has_more","sort")
                data={name:full[name] for name in fields}
                data["as_of"]=full["now"]
            return data
        return await request.app.state.read_api.run(build,key=key,ttl=8)

    @app.get("/api/mints/bridge")
    async def bridge_feed(request:Request,minimum:int=Query(10000,ge=0,le=10**12),
                          hours:int=Query(0,ge=0,le=175200),
                          q:str=Query("",max_length=66,pattern="^(|0x[0-9a-fA-F]{1,64})$"),
                          sort:str=Query("newest",pattern=BRIDGE_SORT_PATTERN),
                          limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0,le=5000000)):
        if cfg.mode!="mints": raise HTTPException(404,"WGNK monitor is disabled")
        key=("bridge",minimum,hours,q.lower(),sort,limit,offset)
        return await request.app.state.read_api.run(
            lambda db: bridge_listing(db,minimum,hours,q.lower(),sort,limit,offset),key=key,ttl=3)

    @app.get("/api/mints/bridge/export.csv")
    async def bridge_export(request:Request,minimum:int=Query(10000,ge=0,le=10**12),
                            sort:str=Query("newest",pattern=BRIDGE_SORT_PATTERN)):
        if cfg.mode!="mints": raise HTTPException(404,"WGNK monitor is disabled")
        def build(db):
            data=bridge_listing(db,minimum=minimum,sort=sort,limit=50000)
            if not data["ready"]: raise HTTPException(503,"The reconciled bridge snapshot is not ready yet")
            if data["has_more"]: raise HTTPException(413,"Too many events for one CSV")
            output=io.StringIO()
            fields=["kind","time_local","address","amount","amount_raw","tx_hash","log_index","height","finalized"]
            writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
            for e in data["items"]:
                writer.writerow({k:local_time(e["ts"]) if k=="time_local" else e[k] for k in fields})
            return Response(output.getvalue(),media_type="text/csv",
                            headers={"Content-Disposition":'attachment; filename="wgnk-bridge.csv"'})
        return await request.app.state.read_api.run(build)

    @app.get("/api/mints/provenance")
    async def mint_provenance(request:Request,address:str=Query("",pattern="^(|0x[0-9a-fA-F]{40})$")):
        if cfg.mode!="mints": raise HTTPException(404,"WGNK monitor is disabled")
        return await request.app.state.read_api.run(lambda db: provenance_overview(db,address.lower() or None))

    @app.get("/api/mints/gonka/{address}")
    async def gonka_incoming(request:Request,address:str,
                            sort:str=Query("time_desc",pattern="^(time|amount|sender|kind|tx)_(asc|desc)$")):
        if cfg.mode!="mints": raise HTTPException(404,"WGNK monitor is disabled")
        if not GNK.fullmatch(address): raise HTTPException(400,"A full Gonka address is required")
        def build(db):
            if address not in native_addresses(db):
                raise HTTPException(404,"The address is not yet linked to a confirmed transfer through the WGNK bridge")
            return incoming_history(db,address,sort)
        return await request.app.state.read_api.run(build)

    @app.get("/api/mints/tx/{tx_hash}")
    async def mint_transaction(request:Request,tx_hash:str):
        if cfg.mode!="mints": raise HTTPException(404,"Mint monitor is disabled")
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}",tx_hash): raise HTTPException(400,"An Ethereum transaction hash is required")
        def build(db):
            rows=db.conn.execute("SELECT * FROM wgnk_mints WHERE tx_hash=? ORDER BY log_index",
                                  (tx_hash.lower(),)).fetchall()
            if not rows: raise HTTPException(404,"No mint in this transaction was found in the index")
            return {"contract":MINT_TOKEN,"tx_hash":tx_hash.lower(),"items":[mint_event(r) for r in rows],
                    "native_links":[e for e in links_for(db) if e["tx_hash"]==tx_hash.lower()]}
        return await request.app.state.read_api.run(build)

    @app.get("/api/mints/export.csv")
    async def export_mints(request:Request,minimum:int=Query(0,ge=0,le=10**12),
                           hours:int=Query(0,ge=0,le=175200),
                           q:str=Query("",max_length=66,pattern="^(|0x[0-9a-fA-F]{1,64})$"),
                           finality:str=Query("finalized",pattern="^(finalized|all)$"),
                           sort:str=Query("newest",pattern=MINT_SORT_PATTERN)):
        if cfg.mode!="mints": raise HTTPException(404,"Mint monitor is disabled")
        def build(db):
            data=mint_listing(db,minimum,hours,q.lower(),finality,sort,50000,0)
            if data["has_more"]: raise HTTPException(413,"More than 50,000 mints. Reduce the period.")
            output=io.StringIO()
            columns=["tx_hash","log_index","height","block_hash","ts","time_local","timezone","recipient","amount","amount_raw",
                     "epoch_id","request_id","finalized","source"]
            writer=csv.DictWriter(output,columns,extrasaction="ignore")
            writer.writeheader()
            for e in data["items"]: writer.writerow({**e,"time_local":local_time(e["ts"]),"timezone":TIME_ZONE})
            return Response("\ufeff"+output.getvalue(),media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition":'attachment; filename="wgnk-mints.csv"'})
        return await request.app.state.read_api.run(build)

    @app.get("/api/overview")
    async def get_overview(request: Request, hours: int = Query(24, ge=1, le=168),
                           since: int | None = Query(None, ge=0, le=4102444800)):
        key = ("overview",hours,since)
        return cache.get_or_create(key,4,lambda: overview(request.app.state.db,hours,since))

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
            raise HTTPException(400,"A valid Gonka or Ethereum address is required")
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
            raise HTTPException(400,"A full Gonka (gonka1…) or Ethereum (0x…) address is required.")
        db, net = request.app.state.db, request.app.state.indexer.net
        key = ("balance",address)
        balance_data = cache.get(key)
        if balance_data is None:
            try:
                if address.startswith("0x"):
                    raw = await net.call(TOKEN,"balanceOf(address)",address[2:].zfill(64))
                    balance = {"asset":"WGNK","amount":tokens(int(raw,16))}
                else:
                    result = await net.api("/cosmos/bank/v1beta1/balances/"+address+"/by_denom",{"denom":"ngonka"})
                    balance = {"asset":"GNK","amount":tokens(result["balance"]["amount"])}
                balance_data = {"balance":balance,"updated_at":int(time.time()),"error":None}
            except Exception:
                balance_data = {"balance":None,"updated_at":None,
                                "error":"RPC did not return a balance; this is not a zero balance."}
            cache.put(key,balance_data,45)
        activity = db.events(int(time.time())-hours*3600 if since is None else since,address=address,limit=100)
        linked = []
        for b in bridges(db,hours,limit=5000000,since=since)["items"]:
            if address in (b["source"],b["target"]):
                linked.append(b)
        return {"address":address,**balance_data,"label":db.labels().get(address),
                "events":activity,"bridges":linked[:50],"hours":hours}

    @app.get("/api/export.csv")
    async def export(request: Request, hours: int=Query(24,ge=1,le=168),
                     since: int | None = Query(None,ge=0,le=4102444800)):
        rows = request.app.state.db.events(int(time.time())-hours*3600 if since is None else since,limit=50000)
        if rows["has_more"]:
            raise HTTPException(413,"More than 50,000 events in the window. Reduce the period.")
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
