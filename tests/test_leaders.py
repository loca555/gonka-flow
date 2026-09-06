import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.mints import initialize
from app.leaders import trade_leaders
from app.flows import analysis
from test_flows import A, POOL, UNIT, block, event

B = "0x" + "3" * 40
C = "0x" + "4" * 40


def trade(side, address, volume, quote, tx="tx1", ts=1, attribution="initiator_net"):
    return dict(kind=side, actor=address, amount_raw=str(volume), quote_raw=str(quote),
                tx_hash=tx, ts=ts, attribution=attribution)


def source(rows, ready=True):
    return dict(ready=ready, now=10, timezone="Asia/Nicosia", snapshot={"height":12},
                coverage={"complete":True}, status={}, pools=[], trades=rows)


class LeaderTests(unittest.TestCase):
    def compute(self, rows):
        with patch("app.leaders.analysis", return_value=source(rows)) as read:
            result = trade_leaders(None)
        read.assert_called_once_with(None, side="all", limit=None)
        return result

    def test_both_sides_gross_volume_weighted_price_and_transaction_count(self):
        data = self.compute([
            trade("buy", A, 10*UNIT, 2_000_000, ts=1),
            trade("buy", A, 20*UNIT, 8_000_000, ts=2),
            trade("sell", A, 5*UNIT, 3_000_000, "tx2", ts=3),
            trade("buy", B, 40*UNIT, 16_000_000, "tx3"),
        ])
        self.assertEqual([row["address"] for row in data["buyers"]], [B, A])
        buyer = data["buyers"][1]
        self.assertEqual((buyer["volume"], buyer["quote"], buyer["average_price"]), ("30", "10", "0.333333333333"))
        self.assertEqual((buyer["swaps"], buyer["transactions"], buyer["first_ts"], buyer["last_ts"]), (2, 1, 1, 2))
        self.assertEqual(data["sellers"][0]["address"], A)
        self.assertEqual(data["sellers"][0]["volume"], "5")
        self.assertEqual(data["summary"]["buy"]["volume"], "70")
        self.assertEqual(data["summary"]["buy"]["addresses"], 2)

    def test_unattributed_and_missing_actor_excluded_and_totals_reconcile(self):
        rows = [
            trade("buy", A, 10, 20), trade("sell", B, 30, 40),
            trade("buy", C, 50, 60, attribution="initiator_only"),
            trade("sell", C, 70, 80, attribution="pool_only"),
            trade("buy", "", 90, 100), trade("sell", "0x"+"0"*40, 110, 120),
        ]
        data = self.compute(rows)
        self.assertEqual([r["address"] for r in data["buyers"]], [A])
        self.assertEqual([r["address"] for r in data["sellers"]], [B])
        self.assertEqual(data["excluded"]["buy"]["volume_raw"], "140")
        self.assertEqual(data["excluded"]["sell"]["volume_raw"], "180")
        for side in ("buy", "sell"):
            for key, event_key in (("volume_raw","amount_raw"), ("quote_raw","quote_raw")):
                self.assertEqual(int(data["summary"][side][key])+int(data["excluded"][side][key]),
                                 sum(int(row[event_key]) for row in rows if row["kind"]==side))

    def test_uint256_differences_and_ties_are_not_float_sorted_or_truncated(self):
        huge = 2**200
        data = self.compute([trade("buy", B, huge, huge), trade("buy", A, huge+1, huge),
                             trade("sell", B, huge, huge), trade("sell", A, huge, huge)])
        self.assertEqual([r["address"] for r in data["buyers"]], [A, B])
        self.assertEqual([r["address"] for r in data["sellers"]], [A, B])
        self.assertEqual(data["summary"]["buy"]["volume_raw"], str(huge*2+1))
        self.assertEqual([r["rank"] for r in data["buyers"]], [1, 2])

    def test_missing_snapshot_is_not_zero_and_empty_success_is_zero(self):
        with patch("app.leaders.analysis", return_value=source([], False)):
            pending=trade_leaders(None)
        self.assertFalse(pending["ready"])
        self.assertIsNone(pending["summary"])
        self.assertIsNone(pending["excluded"])
        empty=self.compute([])
        self.assertTrue(empty["ready"])
        self.assertEqual(empty["summary"]["buy"]["volume_raw"], "0")
        self.assertEqual(empty["buyers"], [])

    def test_integration_uses_final_snapshot_and_all_addresses_not_only_minters(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Database(Path(temp)/"test.sqlite3")
            try:
                initialize(db)
                db.put("mints:deployment", block(1));db.put("mints:status", {"finalized_height":12})
                rows=[event(1,"bridge_mint",100,"0x"+"0"*40,A),
                      event(2,"buy",30,actor=B,pool=POOL,quote_raw="6000000",meta={"attribution":"initiator_net"}),
                      event(3,"sell",10,actor=A,pool=POOL,quote_raw="2000000",meta={"attribution":"initiator_net"}),
                      event(4,"liquidity_add",40,actor=B,pool=POOL),
                      event(5,"transfer",20,A,B),
                      {**event(6,"buy",999,actor=C,pool=POOL,quote_raw="6000000",meta={"attribution":"initiator_net"}),"finalized":0},
                      event(11,"buy",999,actor=C,pool=POOL,quote_raw="6000000",meta={"attribution":"initiator_net"})]
                db.save_batch("ethereum",1,12,rows,[block(i) for i in range(1,13)])
                db.put("flow:snapshot",{"height":10,"supply_raw":str(100*UNIT),"pools":[{"address":POOL,"fee":3000,"balance_raw":"0"}]})
                result=trade_leaders(db)
                self.assertEqual([r["address"] for r in result["buyers"]], [B])
                self.assertEqual(result["buyers"][0]["volume"], "30")
                self.assertEqual(result["sellers"][0]["volume"], "10")
                self.assertEqual(result["buyers"][0]["volume_raw"], analysis(db,q=B,side="buy")["summary"]["volume_raw"])
            finally:
                db.close()

    def test_api_and_local_navigation(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg=Settings(mode="mints",data_dir=Path(temp),indexer_enabled=False,history_from="")
            with TestClient(create_app(cfg)) as client:
                response=client.get("/api/mints/leaders")
                self.assertEqual(response.status_code,200)
                self.assertFalse(response.json()["ready"])
                self.assertEqual(client.post("/api/mints/leaders").status_code,405)
                html=client.get("/").text
                self.assertIn('data-view="leaders"',html)
                self.assertIn('id="leaders-view"',html)
                self.assertEqual(client.get("/static/leaders.js").status_code,200)
                self.assertEqual(client.get("/static/leaders.css").status_code,200)


if __name__=="__main__":
    unittest.main()
