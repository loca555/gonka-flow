import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.codec import blank
from app.config import TOKEN, USDT, ZERO, SEED_POOLS
from app.db import Database
from app.flows import analysis, balances, event_rows, price_raw, FlowCollector, bridge_listing
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

    def test_holder_history_is_independent_of_trade_filters_and_checks_coverage(self):
        reference=analysis(self.db)["holder_history"]
        self.assertTrue(reference["ready"])
        for options in ({"q":A,"side":"buy","hours":1},{"q":"0xf","side":"all","offset":100,"limit":1},
                        {"sort":"price_asc","side":"sell"}):
            self.assertEqual(analysis(self.db,**options)["holder_history"],reference)
        self.db.conn.execute("DELETE FROM ranges WHERE chain='ethereum'")
        self.db._range("ethereum",1,5);self.db._range("ethereum",7,12);self.db.conn.commit()
        self.assertIsNone(analysis(self.db)["holder_history"])

    def test_outside_holders_use_all_history_and_current_balance(self):
        groups=analysis(self.db)["outside_holders"]
        self.assertTrue(groups["ready"])
        self.assertEqual(groups["total_raw"],str(45*UNIT))
        self.assertEqual(groups["groups"][1]["balance_raw"],str(45*UNIT))
        for options in ({"side":"buy","hours":1,"q":A}, {"q":"0xf","limit":1,"offset":100}):
            self.assertEqual(analysis(self.db,**options)["outside_holders"],groups)
        # A confirmed 10 WGNK purchase makes this a trader: 10 bought / 40 sold.
        self.db.conn.execute("UPDATE events SET meta=? WHERE kind='buy'",(json.dumps({"attribution":"initiator_net"}),))
        self.db.conn.commit()
        self.assertEqual(analysis(self.db)["outside_holders"]["groups"][2]["balance_raw"],str(45*UNIT))
        # A future purchase beyond the same snapshot cannot change that classification.
        self.db.save_batch("ethereum",13,13,[event(13,"buy",10000,actor=A,pool=POOL,
            meta={"attribution":"initiator_net"})],[block(13)])
        self.assertEqual(analysis(self.db)["outside_holders"]["groups"][2]["balance_raw"],str(45*UNIT))

    def test_minter_dates_only_include_final_mints_through_snapshot(self):
        first=analysis(self.db)["minters"][0]
        self.assertEqual((first["first_mint_ts"],first["last_mint_ts"],first["mint_count"]),
                         (block(1)["ts"],block(1)["ts"],1))
        self.db.save_batch("ethereum",11,14,[
            event(11,"bridge_mint",5,ZERO,A),
            {**event(12,"bridge_mint",7,ZERO,A),"finalized":0},
            event(13,"bridge_mint",8,ZERO,A)], [block(h) for h in range(11,15)])
        result=analysis(self.db,hours=24,q="0x"+"f"*40,side="buy")["minters"][0]
        self.assertEqual((result["first_mint_ts"],result["last_mint_ts"],result["mint_count"]),
                         (block(1)["ts"],block(11)["ts"],2))
        self.assertEqual(result["minted"],"105")

    def test_address_all_trades_separates_sides_and_pages_full_history(self):
        self.db.conn.execute("UPDATE events SET meta=? WHERE kind='buy'",(json.dumps({"attribution":"initiator_net"}),))
        self.db.conn.commit()
        result=analysis(self.db,q=A,side="all",limit=2)
        s=result["summary"]
        self.assertEqual((result["total"],s["sold"],s["bought"],s["sale_quote"],s["buy_quote"]),(3,"40","10","18","4"))
        self.assertEqual((s["sales_count"],s["buys_count"],s["sale_average_price"],s["buy_average_price"]),(2,1,"0.45","0.4"))
        self.assertEqual(s["average_price"],"0.44")
        self.assertEqual(result["sales"],[])
        self.assertEqual([e["kind"] for e in result["trades"]],["sell","buy"])
        self.assertTrue(result["has_more"])
        page=analysis(self.db,q=A,side="all",limit=2,offset=2)
        self.assertEqual([e["height"] for e in page["trades"]],[3])
        self.assertEqual(page["summary"],s)
        self.assertEqual([e["kind"] for e in analysis(self.db,q=A,side="all",sort="kind_asc")["trades"]],["buy","sell","sell"])
        self.db.conn.execute("UPDATE events SET meta=? WHERE kind='buy'",(json.dumps({"attribution":"initiator_only"}),))
        self.db.conn.commit()
        self.assertEqual(analysis(self.db,q=A,side="all")["total"],2)
        self.assertEqual(analysis(self.db,q="0x"+"f"*40,side="all")["total"],0)

    def test_partial_history_does_not_support_a_newer_snapshot(self):
        self.db.conn.execute("DELETE FROM ranges WHERE chain='ethereum'")
        self.db._range("ethereum",1,5);self.db._range("ethereum",7,12);self.db.conn.commit()
        result=analysis(self.db)
        self.assertFalse(result["ready"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertIsNone(result["summary"])
        self.assertFalse(bridge_listing(self.db,minimum=0)["ready"])

    def test_daily_sides_reconcile_with_full_filtered_totals(self):
        result=analysis(self.db,side="all",limit=1)
        self.assertEqual(len(result["trades"]),1)
        self.assertEqual(result["daily"],[{
            "date":local_day(block(3)["ts"]),"raw":str(50*UNIT),"quote_raw":"22000000","events":3,
            "bought_raw":str(10*UNIT),"sold_raw":str(40*UNIT),"buy_quote_raw":"4000000",
            "sale_quote_raw":"18000000","buys_count":1,"sales_count":2,"price_raw":"440000000000"}])
        self.assertEqual(result["daily"],analysis(self.db,side="all",limit=1,offset=2)["daily"])
        for side in ("all","buy","sell"):
            for q in ("",A,"0x"+"f"*40):
                data=analysis(self.db,side=side,q=q)
                for daily_key,summary_key in (("raw","volume_raw"),("quote_raw","quote_raw"),
                        ("bought_raw","bought_raw"),("sold_raw","sold_raw"),("buy_quote_raw","buy_quote_raw"),
                        ("sale_quote_raw","sale_quote_raw"),("buys_count","buys_count"),("sales_count","sales_count")):
                    self.assertEqual(sum(int(d[daily_key]) for d in data["daily"]),int(data["summary"][summary_key]))
                for day in data["daily"]:
                    self.assertEqual(int(day["raw"]),int(day["bought_raw"])+int(day["sold_raw"]))
                    self.assertEqual(int(day["quote_raw"]),int(day["buy_quote_raw"])+int(day["sale_quote_raw"]))
                    self.assertEqual(day["events"],day["buys_count"]+day["sales_count"])

    def test_daily_sides_preserve_large_integers_dates_and_period_filter(self):
        stamps=["2026-07-01T20:59:00+00:00","2026-07-01T21:01:00+00:00","2026-07-04T12:00:00+00:00"]
        for h,value in zip((3,5,7),stamps):
            self.db.conn.execute("UPDATE events SET ts=? WHERE height=?",
                                 (int(datetime.fromisoformat(value).timestamp()),h))
        raw=2**200+1;quote=2**210+3
        self.db.conn.execute("UPDATE events SET amount_raw=?,quote_raw=? WHERE kind='buy'",(str(raw),str(quote)))
        self.db.conn.commit()
        data=analysis(self.db,side="all")
        self.assertEqual([d["date"] for d in data["daily"]],["2026-07-01","2026-07-02","2026-07-04"])
        day=data["daily"][1]
        self.assertEqual((day["bought_raw"],day["buy_quote_raw"],day["sold_raw"]),(str(raw),str(quote),"0"))
        self.assertEqual(day["price_raw"],str(price_raw(quote,raw)))
        with patch("app.flows.time.time",return_value=datetime.fromisoformat(stamps[-1]).timestamp()+60):
            recent=analysis(self.db,side="all",hours=24)
        self.assertEqual(len(recent["daily"]),1)
        self.assertEqual((recent["daily"][0]["date"],recent["summary"]["bought"],recent["summary"]["sold"]),
                         ("2026-07-04","0","10"))

    def test_address_balance_uses_all_transfers_at_verified_snapshot(self):
        balance=analysis(self.db,q=A,side="all")["address_balance"]
        self.assertEqual((balance["amount"],balance["amount_raw"],balance["height"]),("45",str(45*UNIT),12))
        self.assertEqual(analysis(self.db,q=A,side="buy",hours=1)["address_balance"],balance)
        self.assertEqual(analysis(self.db,q=POOL)["address_balance"]["amount"],"50")
        self.assertEqual(analysis(self.db,q="0x"+"f"*40)["address_balance"]["amount"],"0")
        self.db.save_batch("ethereum",11,13,[
            {**event(11,"transfer",3,A,POOL),"finalized":0},event(13,"transfer",4,A,POOL)],
            [block(h) for h in range(11,14)])
        self.assertEqual(analysis(self.db,q=A)["address_balance"],balance)
        self.db.put("flow:status",{"error":"RPC offline"})
        self.assertEqual(analysis(self.db,q=A)["address_balance"],balance)
        snapshot=self.db.get("flow:snapshot")
        self.db.put("flow:snapshot",{**snapshot,"ledger_verified":False})
        self.assertIsNone(analysis(self.db,q=A)["address_balance"])
        self.db.put("flow:snapshot",None)
        self.assertIsNone(analysis(self.db,q=A)["address_balance"])

    def test_address_endpoint_returns_all_trades_beyond_normal_page_limit(self):
        from fastapi.testclient import TestClient
        from app.config import Settings
        from app.main import create_app
        cfg=Settings(mode="mints",data_dir=Path(self.temp.name)/"api",indexer_enabled=False,history_from="")
        with TestClient(create_app(cfg)) as client:
            db=client.app.state.db
            db.put("mints:deployment",block(1));db.put("mints:status",{"finalized_height":12})
            extra=[blank("ethereum",block(11),"0x"+format(2000+i,"064x"),0,"buy",UNIT,
                         actor=A,pool=POOL,quote_raw="1000000",quote_asset="USDT",
                         meta={"attribution":"initiator_net"}) for i in range(230)]
            db.save_batch("ethereum",1,12,self.events+extra,[block(h) for h in range(1,13)])
            db.put("flow:snapshot",self.db.get("flow:snapshot"))
            data=client.get("/api/mints/address/"+A).json()
            self.assertTrue(data["ready"]);self.assertFalse(data["has_more"])
            self.assertEqual((data["total"],len(data["trades"]),data["offset"]),(232,232,0))
            self.assertEqual(data["address_balance"]["amount"],"45")
            self.assertEqual(data["summary"]["buys_count"],230)
            asc=client.get("/api/mints/address/"+A+"?sort=time_asc").json()
            self.assertEqual([e["id"] for e in asc["trades"]],list(reversed([e["id"] for e in data["trades"]])))
            self.assertEqual(client.get("/api/mints/flows?q="+A+"&side=all").json()["limit"],25)
            self.assertEqual(client.get("/api/mints/address/0x123").status_code,400)
            self.assertEqual(client.get("/api/mints/address/"+A+"?sort=unsafe").status_code,422)
            empty=client.get("/api/mints/address/"+"0x"+"f"*40).json()
            self.assertEqual((empty["total"],empty["trades"],empty["address_balance"]["amount"]),(0,[],"0"))

    def test_bridge_feed_includes_mints_burns_not_transfers_or_swaps(self):
        data=bridge_listing(self.db,minimum=0)
        self.assertTrue(data["ready"])
        self.assertEqual(data["total"],2)
        self.assertEqual([e["kind"] for e in data["items"]],["bridge_burn","bridge_mint"])
        self.assertEqual([e["address"] for e in data["items"]],[A,A])
        self.assertEqual([e["amount"] for e in data["items"]],["5","100"])
        self.assertTrue(all(e["finalized"] and not e["native_side_checked"] for e in data["items"]))
        self.assertEqual(bridge_listing(self.db,minimum=6)["total"],1)
        self.assertEqual(bridge_listing(self.db,minimum=0,q="0x"+"f"*40)["total"],0)
        self.assertEqual(bridge_listing(self.db,minimum=0,q=A,limit=1,offset=1)["items"][0]["kind"],"bridge_mint")
        for field in ("time","kind","recipient","amount","tx","status"):
            asc=bridge_listing(self.db,minimum=0,sort=field+"_asc")["items"]
            desc=bridge_listing(self.db,minimum=0,sort=field+"_desc")["items"]
            self.assertEqual(asc,list(reversed(desc)))
        self.db.save_batch("ethereum",11,13,[
            {**event(11,"bridge_burn",1,A,ZERO),"finalized":0},
            event(13,"bridge_burn",2,A,ZERO)],[block(h) for h in range(11,14)])
        self.assertEqual(bridge_listing(self.db,minimum=0)["total"],2)

    def test_uint256_price_and_ledger_remain_exact(self):
        self.assertEqual(price_raw(12*10**6,30*UNIT),400000000000)
        q=2**210+1;n=2**200+3
        self.assertEqual(price_raw(q,n),q*10**21//(n*10**6))
        ledger,minted,burned=balances(event_rows(self.db,1,12))
        self.assertEqual(sum(ledger.values()),minted-burned)
        self.assertEqual(ledger[POOL],50*UNIT)

    def test_bridge_minter_totals_match_all_history_not_feed_filters(self):
        self.db.save_batch("ethereum",11,12,[event(11,"bridge_mint",20,ZERO,A)],[block(11),block(12)])
        expected={"minted_raw":str(120*UNIT),"sold_raw":str(40*UNIT),
                  "minted":"120","sold":"40","sales_count":2}
        for filters in ({},{"minimum":50},{"q":A,"limit":1,"offset":2},{"q":event(1,"bridge_mint",100,ZERO,A)["tx_hash"]}):
            result=bridge_listing(self.db,**{"minimum":0,**filters})
            for row in result["items"]:
                self.assertEqual(row["minter_totals"],expected if row["kind"]=="bridge_mint" else None)
        with patch("app.flows.time.time",return_value=block(11)["ts"]+86400):
            recent=bridge_listing(self.db,minimum=0,hours=24)
        self.assertEqual(len(recent["items"]),1)
        self.assertEqual(recent["items"][0]["minter_totals"],expected)
        self.assertEqual(expected["sold"],analysis(self.db)["minters"][0]["sold"])

    def test_bridge_minter_totals_exclude_unconfirmed_untracked_and_future_sales(self):
        self.db.conn.execute("UPDATE events SET meta=? WHERE kind='sell' AND height=7",(json.dumps({"attribution":"initiator_only"}),))
        self.db.conn.commit()
        self.db.save_batch("ethereum",11,14,[
            event(11,"sell",200,actor=A,pool="0x"+"f"*40,meta={"attribution":"initiator_net"}),
            {**event(12,"sell",300,actor=A,pool=POOL,meta={"attribution":"initiator_net"}),"finalized":0},
            event(13,"sell",400,actor=A,pool=POOL,meta={"attribution":"initiator_net"}),
            event(14,"bridge_mint",500,ZERO,A)],[block(h) for h in range(11,15)])
        mint=next(e for e in bridge_listing(self.db,minimum=0)["items"] if e["kind"]=="bridge_mint")
        self.assertEqual((mint["minter_totals"]["minted"],mint["minter_totals"]["sold"],mint["minter_totals"]["sales_count"]),("100","30",1))

    def test_bridge_minter_totals_exact_zero_and_sales_above_minted(self):
        large=2**200+1
        self.db.conn.execute("UPDATE events SET amount_raw=? WHERE kind='sell' AND height=3",(str(large),))
        self.db.conn.commit()
        get=lambda:next(e for e in bridge_listing(self.db,minimum=0)["items"] if e["kind"]=="bridge_mint")["minter_totals"]
        self.assertEqual(get()["sold_raw"],str(large+10*UNIT))
        self.assertEqual(get()["minted_raw"],str(100*UNIT))
        self.db.conn.execute("UPDATE events SET meta=? WHERE kind='sell'",(json.dumps({"attribution":"initiator_only"}),))
        self.db.conn.commit()
        self.assertEqual((get()["sold_raw"],get()["sold"],get()["sales_count"]),("0","0",0))

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
