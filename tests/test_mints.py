import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from app.config import Settings, TOKEN, ZERO
from app.db import Database
from app.codec import MINT, TRANSFER, blank
from app.main import create_app
from app.mints import initialize, decode_mint, save_batch, bootstrap, listing, progress, gap, MintIndexer, SCOPE

RECIPIENT="0x"+"2"*40
TS=1778698115
def block(h=10): return {"height":h,"hash":"0x"+format(h,"064x"),"ts":TS+h}
def mint(h=10,idx=1,qty=10000*10**9,finalized=True):
    b=block(h)
    return {"tx_hash":"0x"+format(h*100,"064x"),"log_index":idx,"height":h,
        "block_hash":b["hash"],"ts":b["ts"],"recipient":RECIPIENT,"amount_raw":str(qty),
        "request_id":"0x"+format(h*100+idx,"064x"),"epoch_id":"100","finalized":int(finalized),"source":"ethereum_rpc"}
def raw_log(e):
    return {"address":TOKEN,"topics":[MINT,"0x"+format(int(e["epoch_id"]),"064x"),e["request_id"],
        "0x"+e["recipient"][2:].zfill(64)],"data":"0x"+format(int(e["amount_raw"]),"064x"),
        "blockNumber":hex(e["height"]),"blockHash":e["block_hash"],"transactionHash":e["tx_hash"],
        "logIndex":hex(e["log_index"]),"removed":False}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"db.sqlite3")
        initialize(self.db)
    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def test_exact_uint256_and_duplicate_transaction_counts(self):
        events=[mint(idx=1,qty=2**200+1),mint(idx=2,qty=10**9+1)]
        save_batch(self.db,10,10,events,[block()])
        data=listing(self.db,minimum=0)
        self.assertEqual(data["summary"]["events"],2)
        self.assertEqual(data["summary"]["transactions"],1)
        self.assertEqual(data["summary"]["recipients"],1)
        self.assertEqual(data["items"][0]["amount_raw"],str(10**9+1))
        save_batch(self.db,10,10,events,[block()])
        self.assertEqual(listing(self.db,minimum=0)["total"],2)
        self.assertFalse(self.db.covered("ethereum",10))
        self.assertTrue(self.db.covered(SCOPE,10))

    def test_threshold_is_per_mint_not_recipient_or_transaction(self):
        events=[mint(idx=1,qty=9999*10**9),mint(idx=2,qty=10000*10**9),mint(idx=3,qty=1)]
        save_batch(self.db,10,10,events,[block()])
        data=listing(self.db)
        self.assertEqual(data["total"],1)
        self.assertEqual(data["summary"]["amount"],"10000")

    def test_missing_duplicate_or_conflicting_batch_never_advances(self):
        event=mint()
        save_batch(self.db,10,10,[event],[block()],next_height=11)
        with self.assertRaises(ValueError): save_batch(self.db,10,11,[],[block(),block(11)],next_height=12)
        self.assertEqual(self.db.get("mints:next"),11)
        self.assertFalse(self.db.covered(SCOPE,11))
        with self.assertRaises(ValueError): save_batch(self.db,11,11,[mint(11),mint(11)],[block(11)])
        with self.assertRaises(ValueError): save_batch(self.db,10,10,[{**event,"amount_raw":"2"}],[block()])
        self.assertEqual(listing(self.db,0)["items"][0]["amount_raw"],event["amount_raw"])

    def test_pending_replay_removed_and_finalized_without_double_count(self):
        pending=mint(finalized=False)
        save_batch(self.db,10,10,[pending],[],False)
        self.assertEqual(listing(self.db,0)["total"],0)
        self.assertEqual(listing(self.db,0,finality="all")["total"],1)
        save_batch(self.db,10,10,[],[],False)
        self.assertEqual(listing(self.db,0,finality="all")["total"],0)
        save_batch(self.db,10,10,[pending],[],False)
        save_batch(self.db,10,10,[mint()],[block()])
        self.assertEqual(listing(self.db,0,finality="all")["total"],1)
        with self.assertRaises(ValueError): save_batch(self.db,10,10,[pending],[],False)

    def test_coverage_requires_all_intervals_from_deployment(self):
        self.db.put("mints:deployment",block(5))
        self.db.put("mints:status",{"finalized_height":20,"latest_height":30})
        save_batch(self.db,5,9,[],[])
        save_batch(self.db,15,20,[],[])
        self.assertEqual(progress(self.db)["missing"],5)
        self.assertFalse(progress(self.db)["complete"])
        self.assertEqual(gap(self.db,5,20),(10,14))
        save_batch(self.db,10,14,[],[])
        self.assertTrue(progress(self.db)["complete"])
        self.assertIsNone(gap(self.db,5,20))

    def test_bootstrap_imports_only_verified_mints_preserving_legacy_data(self):
        self.db.put("ethereum_full_history",True)
        self.db.put("holders:WGNK:deployment",5)
        self.db.put("gonka_back",555)
        e=blank("ethereum",block(),"0x"+format(1000,"064x"),1,"bridge_mint",10**13,ZERO,RECIPIENT,
            request_key="0x"+"3"*64,meta={"contract":TOKEN,"topic":MINT,"epoch":"0x64"})
        swap=blank("ethereum",block(11),"0x"+format(1100,"064x"),1,"buy",10**9,RECIPIENT)
        self.db.save_batch("ethereum",5,15,[e,swap],[block(5),block(),block(11),block(15)])
        before=self.db.conn.execute("SELECT count(*) FROM events").fetchone()[0]
        bootstrap(self.db,5,15)
        self.assertEqual(listing(self.db,0)["total"],1)
        self.assertEqual(listing(self.db,0)["items"][0]["epoch_id"],"100")
        self.assertEqual(self.db.get("gonka_back"),555)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM events").fetchone()[0],before)
        self.assertTrue(self.db.covered(SCOPE,5))
        self.assertEqual(self.db.get("mints:bootstrapped")["imported"],1)
        bootstrap(self.db,5,15)
        self.assertEqual(listing(self.db,0)["total"],1)

    def test_decoder_rejects_wrong_contract_topics_height_removed_and_zero(self):
        e=mint()
        raw=raw_log(e)
        self.assertEqual(decode_mint(raw,block(),True),e)
        for bad in [{**raw,"address":RECIPIENT},{**raw,"removed":True},{**raw,"blockNumber":"0xb"},
                    {**raw,"topics":[MINT]},{**raw,"data":"0x"+"0"*64}]:
            with self.assertRaises(ValueError): decode_mint(bad,block(),True)

    def test_filter_sort_pagination_and_recipients_use_exact_sums(self):
        for h,qty in [(10,9*10**9),(11,11*10**9),(12,2*10**9)]:
            save_batch(self.db,h,h,[mint(h,qty=qty)],[block(h)])
        self.assertEqual([e["height"] for e in listing(self.db,0,sort="largest")["items"]],[11,10,12])
        self.assertEqual(listing(self.db,0,sort="oldest",limit=1,offset=1)["items"][0]["height"],11)
        self.assertEqual(listing(self.db,0,q=RECIPIENT)["recipients"][0]["amount"],"22")
        self.assertEqual(listing(self.db,0,q="0xdead")["total"],0)
        for field,key in {"time":lambda e:e["ts"],"recipient":lambda e:e["recipient"],
                          "amount":lambda e:int(e["amount_raw"]),"tx":lambda e:e["tx_hash"],
                          "status":lambda e:(e["finalized"],e["height"])}.items():
            for direction in ("asc","desc"):
                data=listing(self.db,0,sort=field+"_"+direction)
                self.assertEqual([key(e) for e in data["items"]],sorted([key(e) for e in data["items"]],reverse=direction=="desc"))
                self.assertEqual(listing(self.db,0,sort=field+"_"+direction,limit=1,offset=1)["items"][0],data["items"][1])


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"db.sqlite3")
        self.idx=MintIndexer(Settings(mode="mints",history_from=""),self.db)
        await self.idx.net.close()
        self.idx.net.native=AsyncMock(side_effect=AssertionError("GNK must remain off"))
        self.idx.net.api=AsyncMock(side_effect=AssertionError("GNK API must remain off"))
        self.idx.net.pool=AsyncMock(side_effect=AssertionError("DEX must remain off"))
    async def asyncTearDown(self):
        self.db.close();self.temp.cleanup()

    async def test_new_mint_requires_matching_receipt_and_erc20_mint(self):
        e=mint();raw=raw_log(e)
        self.idx.net.eth_logs=AsyncMock(return_value=[raw])
        self.idx.block=AsyncMock(return_value=block())
        transfer={**raw,"topics":[TRANSFER,"0x"+"0"*64,"0x"+RECIPIENT[2:].zfill(64)],"logIndex":"0x0"}
        receipt={"transactionHash":e["tx_hash"],"blockHash":e["block_hash"],"blockNumber":hex(10),
                 "status":"0x1","logs":[raw,transfer]}
        self.idx.net.eth=AsyncMock(return_value={**receipt,"logs":[raw]})
        with self.assertRaises(ValueError): await self.idx.batch(10,10,next_height=11)
        self.assertIsNone(self.db.get("mints:next"))
        self.idx.net.eth=AsyncMock(return_value=receipt)
        await self.idx.batch(10,10,next_height=11)
        self.assertEqual(self.db.get("mints:next"),11)
        self.assertEqual(listing(self.db,0)["total"],1)
        self.idx.net.native.assert_not_called();self.idx.net.api.assert_not_called();self.idx.net.pool.assert_not_called()

    async def test_deployment_is_verified_and_does_not_use_june_start(self):
        self.db.put("holders:WGNK:deployment",5)
        self.idx.net.call=AsyncMock(return_value="0x9")
        self.idx.block=AsyncMock(return_value=block(5))
        async def eth(method,args,**kw):
            self.assertEqual(method,"eth_getCode")
            self.assertTrue(kw["archive"])
            return "0x" if args[1]=="0x4" else "0x6000"
        self.idx.net.eth=eth
        await self.idx.setup(20)
        self.assertEqual(self.db.get("mints:deployment")["height"],5)
        self.assertTrue(self.idx.ready.is_set())
        self.idx.net.native.assert_not_called();self.idx.net.api.assert_not_called()

    async def test_only_ethereum_mint_and_market_workers_are_started(self):
        started=[]
        async def loop(key,fn,interval):
            started.append(key)
            await asyncio.Event().wait()
        self.idx.loop=loop
        self.idx.start()
        await asyncio.sleep(0)
        self.assertEqual(sorted(started),["flow:status","mints:history","mints:status"])
        self.assertEqual({t.get_name() for t in self.idx.tasks},{"wgnk_mints_live","wgnk_mints_history","wgnk_market_flow"})
        await self.idx.stop()


class APITests(unittest.TestCase):
    def test_mints_only_routes_export_and_legacy_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(mode="mints",data_dir=Path(tmp),indexer_enabled=False,history_from="")
            with TestClient(create_app(cfg)) as client:
                db=client.app.state.db
                save_batch(db,10,10,[mint(),mint(idx=2,qty=10**9)],[block()])
                data=client.get("/api/mints").json()
                self.assertEqual(data["total"],1)
                self.assertEqual(data["disabled"],["gonka","holder_census","gnk_bridge_verification","external_prices","mining"])
                self.assertEqual(client.get("/api/mints?minimum=0").json()["total"],2)
                self.assertEqual(client.get("/api/mints/tx/"+mint()["tx_hash"]).json()["items"][1]["amount"],"1")
                csv=client.get("/api/mints/export.csv?minimum=0")
                self.assertEqual(csv.status_code,200)
                self.assertEqual(len(csv.text.strip().splitlines()),3)
                for route in ("/api/overview","/api/holders","/api/hosts","/api/bridges","/api/rankings","/api/stream","/api/wallet/"+RECIPIENT):
                    self.assertEqual(client.get(route).status_code,410,route)
                self.assertEqual(client.post("/api/mints").status_code,405)
                self.assertEqual(client.get("/api/mints?minimum=-1").status_code,422)
                self.assertEqual(client.get("/api/mints?q=%3Cscript%3E").status_code,422)
                self.assertEqual(client.get("/healthz").json()["mode"],"mints")
                html=client.get("/").text
                self.assertIn("mints.js",html)
                self.assertNotIn("/static/app.js",html)
                self.assertIn("Минтеры",html)
                self.assertIn("Лента мост",html)
                self.assertNotIn('id="search-form"',html)
                self.assertNotIn('class="filter-panel"',html)
                self.assertIn('class="connection-status"',html)
                self.assertEqual(client.get("/api/mints/flows?side=all&sort=kind_asc").status_code,200)
                self.assertEqual(client.get("/api/mints/bridge?minimum=0").status_code,200)
                self.assertEqual(client.get("/api/mints/bridge?sort=unsafe_desc").status_code,422)
                self.assertEqual(client.get("/api/mints/bridge/export.csv?minimum=0").status_code,503)


if __name__=="__main__": unittest.main()
