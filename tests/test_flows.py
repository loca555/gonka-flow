import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.codec import blank
from app.config import TOKEN, USDT, ZERO, SEED_POOLS
from app.db import Database
from app.flows import analysis, balances, event_rows, price_raw, FlowCollector
from app.mints import initialize, save_batch, listing
from app.timezones import local_day, local_time, TIME_ZONE

A="0x"+"2"*40
POOL=SEED_POOLS[0]
UNIT=10**9
def block(h):
    return {"height":h,"hash":"0x"+format(h,"064x"),"ts":1778698115+h}
def event(h,kind,quantity,src="",dst="",**kw):
    return blank("ethereum",block(h),"0x"+format(h*100,"064x"),0,kind,quantity*UNIT,src,dst,**kw)

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"test.sqlite3")
        initialize(self.db)
        self.db.put("mints:deployment",block(1))
        self.db.put("mints:status",{"finalized_height":12})
        self.events=[
            event(1,"bridge_mint",100,ZERO,A),
            event(2,"transfer",30,A,POOL),
            event(3,"sell",30,actor=A,pool=POOL,quote_raw=str(12*10**6),quote_asset="USDT",meta={"attribution":"initiator_net"}),
            event(4,"transfer",10,POOL,A),
            event(5,"buy",10,actor=A,pool=POOL,quote_raw=str(4*10**6),quote_asset="USDT"),
            event(6,"transfer",10,A,POOL),
            event(7,"sell",10,actor=A,pool=POOL,quote_raw=str(6*10**6),quote_asset="USDT",meta={"attribution":"initiator_net"}),
            event(8,"transfer",20,A,POOL),event(9,"liquidity_add",20,pool=POOL),
            event(10,"bridge_burn",5,A,ZERO)]
        self.db.save_batch("ethereum",1,12,self.events,[block(i) for i in range(1,13)])
        self.metadata={"address":POOL,"quote":USDT,"quote_decimals":6,"quote_symbol":"USDT","fee":3000,"token0":TOKEN}
        self.db.put("flow:snapshot",{"height":12,"hash":block(12)["hash"],"ts":block(12)["ts"],"checked_at":block(12)["ts"],
            "supply_raw":str(95*UNIT),"ledger_verified":True,
            "pools":[{**self.metadata,"balance_raw":str(50*UNIT),"balance":"50"}]})
    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def test_sales_are_turnover_not_unique_minted_coins(self):
        result=analysis(self.db)
        s=result["summary"]
        self.assertTrue(result["ready"])
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual((s["minted"],s["pooled"],s["outside_pools"],s["burned"]),("100","50","45","5"))
        self.assertEqual((s["sold"],s["quote"],s["average_price"]),("40","18","0.45"))
        self.assertEqual(s["liquidity_added"],"20")
        self.assertEqual([s["price"] for s in result["sales"]],["0.6","0.4"])
        self.assertEqual(result["minters"][0]["balance"],"45")
        self.assertNotIn("owner_changes",s)
        self.assertEqual(result["timezone"],TIME_ZONE)

    def test_address_sales_require_confirmed_token_outflow(self):
        self.db.conn.execute("UPDATE events SET meta=? WHERE kind='sell' AND height=7",(json.dumps({"attribution":"initiator_only"}),))
        self.db.conn.commit()
        self.assertEqual(analysis(self.db)["summary"]["sold"],"40")
        self.assertEqual(analysis(self.db,q=A)["summary"]["sold"],"30")
        self.assertEqual(analysis(self.db)["minters"][0]["sold"],"30")
        self.assertEqual(analysis(self.db,limit=1,offset=1)["sales"][0]["height"],3)

    def test_partial_history_does_not_support_a_newer_snapshot(self):
        self.db.conn.execute("DELETE FROM ranges WHERE chain='ethereum'")
        self.db._range("ethereum",1,5);self.db._range("ethereum",7,12);self.db.conn.commit()
        result=analysis(self.db)
        self.assertFalse(result["ready"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertIsNone(result["summary"])

    def test_uint256_price_and_ledger_remain_exact(self):
        self.assertEqual(price_raw(12*10**6,30*UNIT),400000000000)
        q=2**210+1;n=2**200+3
        self.assertEqual(price_raw(q,n),q*10**21//(n*10**6))
        ledger,minted,burned=balances(event_rows(self.db,1,12))
        self.assertEqual(sum(ledger.values()),minted-burned)
        self.assertEqual(ledger[POOL],50*UNIT)

    def test_buy_filter_has_own_totals_and_requires_confirmed_inflow(self):
        result=analysis(self.db,side="buy")
        self.assertEqual(result["summary"]["volume"],"10")
        self.assertEqual(result["summary"]["bought"],"10")
        self.assertEqual(result["summary"]["sold"],"0")
        self.assertEqual(result["summary"]["quote"],"4")
        self.assertEqual(result["sales"],[])
        self.assertTrue(all(e["kind"]=="buy" for e in result["trades"]))
        self.assertEqual(analysis(self.db,side="buy",q=A)["total"],0)
        self.db.conn.execute("UPDATE events SET meta=? WHERE kind='buy'",(json.dumps({"attribution":"initiator_net"}),))
        self.db.conn.commit()
        self.assertEqual(analysis(self.db,side="buy",q=A)["total"],1)
        self.assertEqual(result["minters"][0]["sold"],"40")

    def test_all_addresses_included_and_transfers_are_not_trades(self):
        other="0x"+"3"*40
        self.db.conn.execute("UPDATE events SET actor=? WHERE kind='sell' AND height=7",(other,))
        self.db.conn.commit()
        result=analysis(self.db)
        self.assertEqual(result["summary"]["sold"],"40")
        self.assertEqual(result["minters"][0]["sold"],"30")
        self.assertEqual(analysis(self.db,q=other)["summary"]["sold"],"10")
        self.assertEqual(result["total"],2)

    def test_all_trade_columns_sort_before_pagination(self):
        from fractions import Fraction
        for field,key in {"time":lambda e:e["ts"],"actor":lambda e:e["actor"],
                          "amount":lambda e:int(e["amount_raw"]),"quote":lambda e:int(e["quote_raw"]),
                          "price":lambda e:Fraction(int(e["quote_raw"]),int(e["amount_raw"])),
                          "pool":lambda e:e["pool"],"tx":lambda e:e["tx_hash"]}.items():
            for direction in ("asc","desc"):
                rows=analysis(self.db,sort=field+"_"+direction)["trades"]
                self.assertEqual([key(e) for e in rows],sorted([key(e) for e in rows],reverse=direction=="desc"))
                self.assertEqual(analysis(self.db,sort=field+"_"+direction,limit=1,offset=1)["trades"][0]["id"],rows[1]["id"])
        with self.assertRaises(ValueError): analysis(self.db,side="transfer")
        with self.assertRaises(ValueError): analysis(self.db,sort="unsafe_asc")

    def test_price_sort_does_not_round_to_float_or_display_precision(self):
        n=2**200
        self.db.conn.execute("UPDATE events SET amount_raw=?,quote_raw=? WHERE kind='sell' AND height=3",(str(n),str(n+2)))
        self.db.conn.execute("UPDATE events SET amount_raw=?,quote_raw=? WHERE kind='sell' AND height=7",(str(n),str(n+1)))
        self.db.conn.commit()
        self.assertEqual([e["height"] for e in analysis(self.db,sort="price_asc")["trades"]],[7,3])
        self.assertEqual([e["height"] for e in analysis(self.db,sort="price_desc")["trades"]],[3,7])

    def test_seed_is_ethereum_only_verified_and_never_overwrites_runtime(self):
        from app.seed import export_seed, restore_seed
        e=self.events[0]
        save_batch(self.db,1,12,[{"tx_hash":e["tx_hash"],"log_index":e["idx"],"height":e["height"],
            "block_hash":e["block_hash"],"ts":e["ts"],"recipient":A,"amount_raw":e["amount_raw"],
            "request_id":"0x"+"1"*64,"epoch_id":"1","finalized":1,"source":"test"}],[block(1),block(12)])
        self.db.put("private:config", {"api_key":"test-only-do-not-export"})
        self.db.conn.execute("INSERT INTO labels VALUES(?,?,?,?,?)",(A,"private label","test","local",1))
        self.db.conn.commit()
        folder=Path(self.temp.name)/"seed"
        manifest=export_seed(Path(self.temp.name)/"test.sqlite3",folder)
        self.assertEqual(manifest["height"],12)
        archive=folder/"wgnk.sqlite3.gz";target=Path(self.temp.name)/"runtime"/"test.sqlite3"
        self.assertTrue(restore_seed(target,archive))
        restored=Database(target)
        try:
            self.assertIsNone(restored.get("private:config"))
            self.assertEqual(restored.conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0],0)
            self.assertTrue(analysis(restored)["ready"])
            restored.put("runtime:keep",True)
        finally: restored.close()
        self.assertFalse(restore_seed(target,archive))
        restored=Database(target)
        try: self.assertTrue(restored.get("runtime:keep"))
        finally: restored.close()
        archive.write_bytes(b"corrupted")
        with self.assertRaises(ValueError): restore_seed(Path(self.temp.name)/"corrupt.sqlite3",archive)

    def test_snapshot_is_checked_at_one_final_block_and_failure_preserves_last(self):
        async def run():
            async def call(contract,signature,args="",tag="latest",**kw):
                self.assertEqual(contract,TOKEN);self.assertEqual(tag,"0xc")
                return hex(95*UNIT if signature=="totalSupply()" else 50*UNIT)
            net=SimpleNamespace(call=AsyncMock(side_effect=call))
            owner=SimpleNamespace(db=self.db,net=net,block=AsyncMock(return_value=block(12)))
            collector=FlowCollector(owner);collector.pools={POOL:self.metadata}
            await collector.snapshot(1,12)
            saved=self.db.get("flow:snapshot")
            net.call=AsyncMock(return_value=hex(999*UNIT))
            with self.assertRaises(ValueError): await collector.snapshot(1,12)
            self.assertEqual(self.db.get("flow:snapshot"),saved)
        import asyncio
        asyncio.run(run())

class CyprusTests(unittest.TestCase):
    def test_winter_summer_and_dst_fold(self):
        def ts(value): return int(datetime.fromisoformat(value).timestamp())
        self.assertEqual(local_day(ts("2026-01-01T21:30:00+00:00")),"2026-01-01")
        self.assertEqual(local_day(ts("2026-07-01T21:30:00+00:00")),"2026-07-02")
        self.assertTrue(local_time(ts("2026-01-01T21:30:00+00:00")).endswith("+02:00"))
        self.assertTrue(local_time(ts("2026-07-01T21:30:00+00:00")).endswith("+03:00"))
        self.assertEqual(local_day(ts("2026-10-25T00:30:00+00:00")),local_day(ts("2026-10-25T01:30:00+00:00")))

    def test_mint_daily_grouping_is_not_fixed_moscow_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"test.sqlite3");initialize(db)
            try:
                for h,stamp in [(1,"2026-01-01T21:30:00+00:00"),(2,"2026-01-01T22:30:00+00:00")]:
                    ts=int(datetime.fromisoformat(stamp).timestamp())
                    e={"tx_hash":"0x"+format(h,"064x"),"log_index":0,"height":h,"block_hash":block(h)["hash"],"ts":ts,
                       "recipient":A,"amount_raw":"1","request_id":"0x"+format(h,"064x"),"epoch_id":"1","finalized":1,"source":"test"}
                    save_batch(db,h,h,[e],[{**block(h),"ts":ts}])
                data=listing(db,minimum=0)
                self.assertEqual([(d["date"],d["amount_raw"],d["events"]) for d in data["daily"]],
                                 [("2026-01-01","1",1),("2026-01-02","1",1)])
            finally: db.close()

if __name__=="__main__": unittest.main()
