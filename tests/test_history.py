import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from datetime import datetime, timezone
import httpx
from fastapi.testclient import TestClient
from app.config import Settings, ESCROW
from app.db import Database
from app.codec import blank
from app.history import timestamp, first_block, boundary_proof, archive_progress, period_coverage
from app.main import create_app
from app.sources import Sources
from app.indexer import Indexer

START = 1780779600
ISO = "2026-06-07T00:00:00+03:00"
USER = "0x" + "2"*40

class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_date_requires_timezone_and_exact_heights(self):
        self.assertEqual(timestamp(ISO), START)
        with self.assertRaises(ValueError): timestamp("2026-06-07")
        with self.assertRaises(ValueError): timestamp("2026-06-07T00:00:00")
        with self.assertRaises(ValueError): Settings(history_from=ISO)

    async def test_exact_boundary_and_uneven_times(self):
        times = [0, 5, 5, 9, 8000, 8001, 10000]
        async def read(h): return {"height":h, "ts":times[h-1], "hash":str(h)}
        for cutoff, expected in [(0,1),(1,2),(5,2),(6,4),(7999,5),(8000,5),(9000,7)]:
            p = await first_block(read,1,len(times),cutoff)
            self.assertEqual(p["first"]["height"],expected)
        with self.assertRaises(ValueError): await first_block(read,4,7,5)
        with self.assertRaises(ValueError): await first_block(read,1,7,10001)
        with self.assertRaises(ValueError): await boundary_proof(read,3,5)

    async def test_rpc_failure_does_not_prove_boundary(self):
        async def read(h):
            if h==2: raise RuntimeError("archive unavailable")
            return {"height":h,"ts":h*5,"hash":str(h)}
        with self.assertRaises(RuntimeError): await first_block(read,1,3,10)

    async def test_pruned_source_is_skipped_for_older_blocks(self):
        cfg=Settings(gonka_rpc=["https://pruned.test"],gonka=["https://archive.test"],history_from="")
        net=Sources(cfg)
        await net.client.aclose()
        called=[]
        def handle(r):
            called.append(r.url.host)
            data={"error":{"message":"height 20 is not available, lowest height is 100"}} if r.url.host=="pruned.test" else {"result":{}}
            return httpx.Response(200,json=data)
        net.client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            await net.native("/chain-rpc/block",{"height":"20"})
            net.native_next.clear()
            await net.native("/chain-rpc/block",{"height":"19"})
            self.assertEqual(called,["pruned.test","archive.test","archive.test"])
        finally: await net.close()

    async def test_expand_preserves_data_and_live_cursors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"db.sqlite3")
            cfg=Settings(data_dir=Path(tmp),history_from=ISO,gonka_start=5,eth_start=5)
            idx=Indexer(cfg,db)
            await idx.net.close()
            idx.net.api=AsyncMock(return_value={"accounts":[{"name":"bridge_escrow","base_account":{"address":ESCROW}}]})
            async def reply(path,params):
                h=int(params["height"])
                return {"result":{"block_id":{"hash":str(h)},"block":{"header":{
                    "height":str(h),"time":datetime.fromtimestamp(START+(h-5)*5,timezone.utc).isoformat(),
                    "chain_id":"gonka-mainnet"}}}}
            idx.net.native=reply
            for key,value in {"gonka_origin":10,"gonka_next":12,"gonka_back":9,"gonka_target":1}.items(): db.put(key,value)
            db.save_batch("gonka",10,11,[],[{"height":10,"ts":START+25,"hash":"10"}])
            await idx.native_setup(12)
            self.assertEqual(db.get("gonka_target"),1)  # never narrow an older archive
            self.assertEqual(db.get("gonka_next"),12)
            self.assertEqual(db.get("gonka_back"),9)
            self.assertEqual(db.coverage("gonka")["blocks"],2)
            idx.native_batch=AsyncMock(side_effect=RuntimeError("failed archive batch"))
            with self.assertRaises(RuntimeError): await idx.native_history()
            self.assertIsNone(db.get("history_boundary_seed"))
            self.assertEqual(db.get("gonka_back"),9)
            db.close()

    async def test_archive_outage_does_not_block_live_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"db.sqlite3")
            idx=Indexer(Settings(data_dir=Path(tmp),history_from=ISO,gonka_start=5,eth_start=5),db)
            await idx.net.close()
            idx.net.api=AsyncMock(return_value={"accounts":[{"name":"bridge_escrow","base_account":{"address":ESCROW}}]})
            idx.net.native=AsyncMock(side_effect=RuntimeError("archive down"))
            await idx.native_setup(100)
            self.assertTrue(idx.native_ready.is_set())
            previous=db.get("gonka_back")
            with self.assertRaises(RuntimeError): await idx.native_history()
            self.assertEqual(db.get("gonka_back"),previous)
            self.assertNotIn("gonka",idx.history_verified)
            db.close()


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"db.sqlite3")
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def test_coverage_counts_intersections_not_min_max(self):
        db=self.db
        db.put("history_request",{"iso":ISO,"since":START})
        db.put("history_boundary:gonka",{"since":START,"first":{"height":10,"ts":START,"hash":"10"}})
        db.put("status:gonka",{"remote_height":30})
        for lo,hi in [(1,12),(20,32)]:
            db.save_batch("gonka",lo,hi,[],[{"height":lo,"ts":START+(lo-10)*5,"hash":str(lo)}])
        p=archive_progress(db,"gonka")
        self.assertEqual(p["total_blocks"],21)
        self.assertEqual(p["covered_blocks"],14)
        self.assertEqual(p["missing_blocks"],7)
        self.assertFalse(p["complete"])
        self.assertFalse(period_coverage(db,"gonka",START))
        db.save_batch("gonka",13,19,[],[])
        self.assertTrue(archive_progress(db,"gonka")["complete"])
        self.assertTrue(period_coverage(db,"gonka",START))

    def test_unverified_boundary_never_claims_complete(self):
        db=self.db
        db.put("history_request",{"iso":ISO,"since":START})
        db.put("gonka_target",10)
        db.put("status:gonka",{"remote_height":20})
        db.save_batch("gonka",10,20,[],[])
        self.assertFalse(archive_progress(db,"gonka")["complete"])
        db.put("history_boundary:gonka",{"since":START-1,"first":{"height":10,"ts":START-1,"hash":"10"}})
        self.assertFalse(archive_progress(db,"gonka")["boundary_verified"])

    def test_eth_progress_ends_at_finalized_and_ignores_earlier_extra_history(self):
        db=self.db
        db.put("history_request",{"iso":ISO,"since":START})
        db.put("history_boundary:ethereum",{"since":START,"first":{"height":10,"ts":START+11,"hash":"10"}})
        db.put("status:ethereum",{"remote_height":30,"finalized_height":20})
        db.save_batch("ethereum",1,20,[],[])
        p=archive_progress(db,"ethereum")
        self.assertEqual(p["covered_blocks"],11)
        self.assertTrue(p["complete"])
        self.assertTrue(period_coverage(db,"ethereum",START))

    def test_exact_since_across_api_endpoints(self):
        cfg=Settings(data_dir=Path(self.temp.name)/"api",indexer_enabled=False,history_from="")
        with TestClient(create_app(cfg)) as client:
            db=client.app.state.db
            for h,ts,kind in [(1,START-1,"buy"),(2,START,"sell"),(3,START+1,"bridge_burn")]:
                b={"height":h,"ts":ts,"hash":str(h)}
                e=blank("ethereum",b,"tx"+str(h),0,kind,10**9,src=USER,actor=USER,meta={"attribution":"initiator_net","transaction_index":0})
                db.save_batch("ethereum",h,h,[e],[b])
            query="?since="+str(START)
            self.assertEqual(client.get("/api/events"+query).json()["total"],2)
            self.assertEqual(client.get("/api/events?hours=24").json()["total"],0)
            overview=client.get("/api/overview"+query).json()
            self.assertEqual(overview["since"],START)
            self.assertEqual({x["kind"] for x in overview["totals"]},{"sell","bridge_burn"})
            self.assertEqual(client.get("/api/rankings"+query+"&mode=buy").json()["items"],[])
            self.assertEqual(client.get("/api/rankings"+query+"&mode=sell").json()["items"][0]["n"],1)
            self.assertEqual(client.get("/api/bridges"+query).json()["total"],1)
            self.assertEqual(client.get("/api/addresses/"+USER+query+"&kind=all").json()["events"]["total"],2)
            self.assertEqual(client.get("/api/holders"+query).json()["since"],START)
            csv=client.get("/api/export.csv"+query).text
            self.assertNotIn("tx1",csv)
            self.assertIn("tx2",csv)
            self.assertEqual(client.get("/api/events?since=-1").status_code,422)
            self.assertEqual(client.get("/api/overview?since=no").status_code,422)
            self.assertEqual(client.post("/api/events"+query).status_code,405)


if __name__=="__main__": unittest.main()
