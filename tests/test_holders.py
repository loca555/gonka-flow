import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.config import Settings,TOKEN,ZERO
from app.codec import TRANSFER,blank
from app.db import Database
from app.main import create_app
from app.holders import (HolderCollector,set_balance,apply_wgnk_batch,
    holders_list,address_page,holder_record)

A="0x"+"1"*40
B="0x"+"2"*40
G="gonka1"+"q"*38
G2="gonka1"+"p"*38
BLOCK={"height":100,"hash":"0x"+"a"*64,"ts":int(time.time())}

def transfer(src,dst,amount,idx=0,height=100):
    return {"address":TOKEN,"topics":[TRANSFER,"0x"+src[2:].zfill(64),"0x"+dst[2:].zfill(64)],
        "blockNumber":hex(height),"transactionHash":"0x"+"b"*64,"logIndex":hex(idx),
        "data":"0x"+hex(amount)[2:].zfill(64),"removed":False}

class HolderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"test.sqlite3")
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def test_default_10k_exact_and_large_integer_sort(self):
        with self.db.conn:
            set_balance(self.db,"WGNK",A,10000*10**9,100,"test")
            set_balance(self.db,"WGNK",B,10000*10**9-1,100,"test")
            set_balance(self.db,"WGNK",TOKEN,10**30,100,"test")
        result=holders_list(self.db,"WGNK")
        self.assertEqual(result["minimum"],10000)
        self.assertEqual([r["address"] for r in result["items"]],[TOKEN,A])
        self.assertEqual(holders_list(self.db,"WGNK",minimum=0)["total"],3)
    def test_old_snapshot_never_overwrites_live_balance(self):
        with self.db.conn:
            set_balance(self.db,"GNK",G,123,101,"live")
            set_balance(self.db,"GNK",G,999,100,"census")
        self.assertEqual(holder_record(self.db,G)["balance_raw"],"123")
    def test_tags_only_finalized_attributable_swaps(self):
        with self.db.conn:
            set_balance(self.db,"WGNK",A,20000*10**9,100,"test")
            set_balance(self.db,"WGNK",B,30000*10**9,100,"test")
        events=[blank("ethereum",BLOCK,"buy",1,"buy",12,actor=A,meta={"attribution":"initiator_net"}),
                blank("ethereum",BLOCK,"sell",2,"sell",7,actor=A,meta={"attribution":"initiator_net"}),
                blank("ethereum",BLOCK,"router",3,"sell",99,actor=B,meta={"attribution":"initiator_only"}),
                blank("ethereum",BLOCK,"transfer",4,"transfer",22,A,B)]
        provisional=blank("ethereum",BLOCK,"pending",5,"buy",2,actor=B,meta={"attribution":"initiator_net"})
        provisional["finalized"]=False;events.append(provisional)
        self.db.save_batch("ethereum",100,100,events,[BLOCK])
        result=holders_list(self.db,"WGNK",tag="both")
        self.assertEqual(result["total"],1)
        self.assertEqual(result["items"][0]["address"],A)
        self.assertEqual(holders_list(self.db,"WGNK",tag="none")["items"][0]["address"],B)
        self.assertEqual(holders_list(self.db,"WGNK",tag="buy")["total"],0)
        self.assertEqual(address_page(self.db,A)["activity"]["net"],"0.000000005")
    def test_address_history_paginated_without_fixed_100_limit(self):
        events=[blank("ethereum",BLOCK,str(i),i,"buy",1,actor=A,meta={"attribution":"initiator_net"}) for i in range(125)]
        self.db.save_batch("ethereum",100,100,events,[BLOCK])
        result=address_page(self.db,A,limit=50,offset=100)
        self.assertEqual(len(result["events"]["items"]),25)
        self.assertEqual(result["events"]["total"],125)
        self.assertFalse(result["events"]["has_more"])
    def test_mint_burn_self_transfer_and_atomic_cursor(self):
        logs=[transfer(ZERO,A,100),transfer(A,B,30,1),transfer(B,B,10,2),transfer(B,ZERO,5,3)]
        apply_wgnk_batch(self.db,logs,100,100,95,{"height":100,"complete":True})
        self.assertEqual(holder_record(self.db,A)["balance_raw"],"70")
        self.assertEqual(holder_record(self.db,B)["balance_raw"],"25")
        self.assertIsNone(holder_record(self.db,ZERO))
        self.assertEqual(self.db.get("holders:WGNK")["height"],100)
        with self.assertRaises(ValueError):
            apply_wgnk_batch(self.db,[transfer(A,B,80,height=101)],101,101,95,{"height":101})
        self.assertEqual(holder_record(self.db,A)["balance_raw"],"70")
        self.assertEqual(self.db.get("holders:WGNK")["height"],100)
    def test_bad_supply_duplicate_and_removed_logs_rollback(self):
        log=transfer(ZERO,A,10)
        for logs,supply in [([log],11),([log,log],10),([{**log,"removed":True}],10)]:
            with self.assertRaises(ValueError):
                apply_wgnk_batch(self.db,logs,100,100,supply,{"height":100})
            self.assertIsNone(holder_record(self.db,A))
            self.assertIsNone(self.db.get("holders:WGNK"))
    def test_pinned_native_census_can_resume_final_validation(self):
        class Net:
            fail=True
            requests=[]
            async def api(net,path,params,**kw):
                net.requests.append((path,params,kw))
                if "supply" in path:
                    if net.fail:
                        net.fail=False;raise RuntimeError("temporary RPC failure")
                    return {"amount":{"denom":"ngonka","amount":"30"}},100
                if params.get("pagination.key"):
                    return {"denom_owners":[{"address":G2,"balance":{"denom":"ngonka","amount":"20"}}],
                        "pagination":{"next_key":None,"total":"0"}},100
                return {"denom_owners":[{"address":G,"balance":{"denom":"ngonka","amount":"10"}}],
                    "pagination":{"next_key":"next","total":"2"}},100
        ready=asyncio.Event();ready.set();net=Net()
        async def run():
            collector=HolderCollector(SimpleNamespace(db=self.db,net=net,native_ready=ready))
            await collector.native()
            self.assertFalse(self.db.get("holders:GNK")["complete"])
            await collector.native()
            with self.assertRaises(RuntimeError):await collector.native()
            # A restarted collector validates the last page instead of importing it twice.
            restarted=HolderCollector(SimpleNamespace(db=self.db,net=net,native_ready=ready))
            await restarted.native()
        asyncio.run(run())
        self.assertTrue(self.db.get("holders:GNK")["complete"])
        self.assertEqual(self.db.get("holders:GNK")["seen"],2)
        self.assertEqual(net.requests[1][2]["height"],100)
        self.assertEqual(sum("denom_owners" in p for p,_,_ in net.requests),2)
    def test_api_filters_and_address_validation(self):
        cfg=Settings(data_dir=Path(self.temp.name)/"api",indexer_enabled=False)
        with TestClient(create_app(cfg)) as client:
            self.assertEqual(client.get("/api/holders").json()["minimum"],10000)
            self.assertEqual(client.get("/api/holders?asset=FAKE").status_code,422)
            self.assertEqual(client.get("/api/holders?minimum=-1").status_code,422)
            self.assertEqual(client.get("/api/addresses/not-an-address").status_code,400)
            self.assertEqual(client.get("/api/addresses/"+A+"?hours=0").status_code,200)
            self.assertEqual(client.get("/api/addresses/"+A+"?kind=inject").status_code,422)

if __name__=="__main__":unittest.main()
