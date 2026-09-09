import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import test_flows as fixtures
from app.codec import blank
from app.flows import FlowCollector, analysis, trade_page, bridge_listing, event_rows


class LiveMarketTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.FlowTests.setUp(self)
        self.head=13
        self.headers={h:fixtures.block(h) for h in range(1,20)}
        self.pool_balance=48*fixtures.UNIT
        self.tail=self.make_tail(13,2)
        async def eth(method,params,**kw):
            self.assertEqual((method,params),("eth_getBlockByNumber",["latest",False]))
            b=self.headers[self.head]
            return {"number":hex(self.head),"hash":b["hash"],"timestamp":hex(b["ts"])}
        async def get_block(height,**kw):return dict(self.headers[height])
        async def call(contract,signature,args="",tag="latest",**kw):
            self.assertEqual(tag,hex(self.head))
            return hex(95*fixtures.UNIT if signature=="totalSupply()" else self.pool_balance)
        self.net=SimpleNamespace(eth=AsyncMock(side_effect=eth),call=AsyncMock(side_effect=call))
        self.owner=SimpleNamespace(db=self.db,net=self.net,block=AsyncMock(side_effect=get_block),
                                   cfg=SimpleNamespace(eth_history_batch=1000),status=lambda key,**v:self.db.put(key,v))
        self.collector=FlowCollector(self.owner);self.collector.pools={fixtures.POOL:self.metadata}
        async def read_batch(lo,hi,**kw):
            self.assertFalse(kw["finalized"])
            return [dict(e) for e in self.tail if lo<=e["height"]<=hi],[self.headers[lo],self.headers[hi]]
        self.collector.read_batch=AsyncMock(side_effect=read_batch)

    def tearDown(self):fixtures.FlowTests.tearDown(self)

    def make_tail(self,height,amount):
        b=self.headers[height] if hasattr(self,"headers") else fixtures.block(height)
        tx="0x"+format(height*100,"064x")
        return [blank("ethereum",b,tx,0,"transfer",amount*fixtures.UNIT,fixtures.POOL,fixtures.A,finalized=0),
                blank("ethereum",b,tx,1,"buy",amount*fixtures.UNIT,actor=fixtures.A,pool=fixtures.POOL,
                      quote_raw="1000000",quote_asset="USDT",finalized=0,meta={"attribution":"initiator_net"})]

    async def test_live_trade_counts_as_normal_in_filters_totals_price_and_address(self):
        await self.collector.live(1,12)
        all_data=analysis(self.db,side="all")
        self.assertEqual((all_data["total"],all_data["summary"]["volume"]),(4,"52"))
        self.assertEqual((all_data["coverage"]["head"],all_data["snapshot"]["height"]),(13,13))
        self.assertTrue(all_data["coverage"]["complete"])
        self.assertEqual((all_data["latest_trade"]["height"],all_data["latest_trade"]["price"]),(13,"0.5"))
        self.assertEqual(analysis(self.db,side="buy")["summary"]["bought"],"12")
        self.assertEqual(analysis(self.db,q=fixtures.A,side="all")["address_balance"]["amount"],"47")
        self.assertEqual(analysis(self.db,side="all",minimum=3)["total"],3)
        self.assertEqual(sum(int(day["raw"]) for day in all_data["daily"]),52*fixtures.UNIT)
        self.assertEqual(len(event_rows(self.db,1,13)),len(self.events))
        self.assertEqual(bridge_listing(self.db,minimum=0)["snapshot"]["height"],12)

    async def test_incremental_extension_reuses_validated_tail_without_duplicates(self):
        await self.collector.live(1,12)
        self.head=14;self.tail+=self.make_tail(14,3);self.pool_balance=45*fixtures.UNIT
        await self.collector.live(1,12)
        self.assertEqual(self.collector.read_batch.call_args.args,(14,14))
        data=analysis(self.db,side="all")
        self.assertEqual((data["total"],data["summary"]["bought"]),(5,"15"))
        self.assertEqual(len({t["id"] for t in data["trades"]}),5)
        self.collector.read_batch.reset_mock()
        await self.collector.live(1,12)
        self.collector.read_batch.assert_not_called()
        self.assertEqual(analysis(self.db,side="all")["total"],5)

    async def test_finalization_does_not_count_live_trade_twice(self):
        await self.collector.live(1,12)
        final=[{**e,"finalized":1} for e in self.tail]
        self.db.save_batch("ethereum",13,13,final,[self.headers[13]])
        self.db.put("mints:status",{"finalized_height":13,"latest_height":14})
        self.head=14
        await self.collector.live(1,13)
        self.assertEqual(analysis(self.db,side="all")["total"],4)
        self.assertEqual(self.db.get("flow:live")["current"]["events"],[])

    async def test_reorg_replaces_events_and_rejects_orphaned_page(self):
        await self.collector.live(1,12)
        old=analysis(self.db,side="all")
        self.headers[13]={**self.headers[13],"hash":"0x"+"a"*64}
        self.tail=[];self.pool_balance=50*fixtures.UNIT
        await self.collector.live(1,12)
        data=analysis(self.db,side="all")
        self.assertEqual(data["total"],3)
        self.assertNotEqual(data["snapshot"]["hash"],old["snapshot"]["hash"])
        self.assertFalse(trade_page(self.db,through=13,as_of=old["now"],snapshot_hash=old["snapshot"]["hash"])["ready"])

    async def test_failed_verification_keeps_last_coherent_snapshot(self):
        await self.collector.live(1,12)
        saved=self.db.get("flow:live");old=analysis(self.db,side="all")
        self.head=14;self.pool_balance=999*fixtures.UNIT
        with self.assertRaisesRegex(ValueError,"Pool balance"):
            await self.collector.live(1,12)
        self.assertEqual(self.db.get("flow:live"),saved)
        self.assertEqual(analysis(self.db,side="all")["trades"],old["trades"])

    async def test_block_changing_during_verification_is_not_published(self):
        await self.collector.live(1,12)
        saved=self.db.get("flow:live")
        self.head=14
        original=self.owner.block.side_effect
        async def unstable(height,**kw):
            b=await original(height,**kw)
            return {**b,"hash":"0x"+"f"*64} if height==14 else b
        self.owner.block.side_effect=unstable
        with self.assertRaisesRegex(ValueError,"changed during verification"):
            await self.collector.live(1,12)
        self.assertEqual(self.db.get("flow:live"),saved)

    async def test_pagination_stays_pinned_while_live_feed_advances(self):
        await self.collector.live(1,12)
        old=analysis(self.db,side="all")
        self.head=14;self.tail+=self.make_tail(14,3);self.pool_balance=45*fixtures.UNIT
        await self.collector.live(1,12)
        for side in ("all","buy","sell"):
            for sort in ("time_desc","amount_asc","price_desc"):
                for minimum in (0,3):
                    expected=[t for t in old["trades"] if (side=="all" or t["kind"]==side) and int(t["amount_raw"])>=minimum*fixtures.UNIT]
                    page=trade_page(self.db,through=13,as_of=old["now"],snapshot_hash=old["snapshot"]["hash"],
                                    side=side,sort=sort,minimum=minimum,limit=1,offset=0)
                    self.assertTrue(page["ready"])
                    self.assertEqual(page["total"],len(expected))
                    self.assertEqual(page["snapshot_hash"],old["snapshot"]["hash"])
                    self.assertTrue(all(t["height"]<=13 for t in page["trades"]))

    async def test_missing_final_prefix_cannot_be_marked_complete(self):
        self.db.conn.execute("DELETE FROM ranges WHERE chain='ethereum'");self.db.conn.commit()
        with self.assertRaisesRegex(ValueError,"history is incomplete"):
            await self.collector.live(1,12)
        self.assertIsNone(self.db.get("flow:live"))

    async def test_http_api_includes_live_trade_and_accepts_snapshot_pin(self):
        from fastapi.testclient import TestClient
        from app.config import Settings
        from app.main import create_app
        from pathlib import Path
        await self.collector.live(1,12)
        cfg=Settings(mode="mints",data_dir=Path(self.temp.name)/"api",indexer_enabled=False,history_from="")
        with TestClient(create_app(cfg)) as client:
            self.db.conn.backup(client.app.state.db.conn)
            full=client.get("/api/mints/flows",params={"side":"all"}).json()
            self.assertEqual((full["total"],full["trades"][0]["height"]),(4,13))
            response=client.get("/api/mints/trades",params={"side":"all","through":13,"as_of":full["now"],
                                 "snapshot_hash":full["snapshot"]["hash"],"minimum":0})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()["trades"],full["trades"])
            self.assertEqual(client.get("/api/mints/trades",params={"through":13,"as_of":full["now"],
                             "snapshot_hash":"invalid"}).status_code,422)

    async def test_live_read_validates_receipt_and_never_writes_final_ranges(self):
        import test_core as raw
        self.head=13
        swap=raw.ethlog(raw.SWAP,[-2*fixtures.UNIT,1000000,0,0,0],
                        [raw.topic_addr(raw.OTHER),raw.topic_addr(fixtures.A)],fixtures.POOL)
        transfer=raw.ethlog(raw.TRANSFER,[2*fixtures.UNIT],
                            [raw.topic_addr(fixtures.POOL),raw.topic_addr(fixtures.A)])
        for item,index in [(swap,1),(transfer,0)]:
            item.update(blockNumber=hex(13),blockHash=self.headers[13]["hash"],logIndex=hex(index))
        receipt={"transactionHash":swap["transactionHash"],"blockHash":swap["blockHash"],"blockNumber":hex(13),
                 "status":"0x1","from":fixtures.A,"logs":[transfer,swap]}
        self.net.eth_logs=AsyncMock(return_value=[transfer,swap])
        self.net.eth=AsyncMock(return_value=receipt)
        collector=FlowCollector(self.owner);collector.pools={fixtures.POOL:self.metadata}
        events,_=await collector.read_batch(13,13,finalized=False)
        trade=next(e for e in events if e["kind"]=="buy")
        self.assertEqual((trade["finalized"],trade["meta"]["attribution"]),(0,"initiator_net"))
        self.assertFalse(self.db.covered("ethereum",13))
        self.net.eth=AsyncMock(return_value={**receipt,"status":"0x0"})
        with self.assertRaisesRegex(ValueError,"Unsuccessful"):
            await collector.read_batch(13,13,finalized=False)
