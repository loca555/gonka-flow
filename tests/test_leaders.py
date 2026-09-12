import tempfile
import unittest
from datetime import datetime
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
        read.assert_called_once_with(None, side="all", limit=None, include_bridge=True)
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
        self.assertIsNone(pending["price_distribution"])
        self.assertIsNone(pending["price_days"])
        empty=self.compute([])
        self.assertTrue(empty["ready"])
        self.assertEqual(empty["summary"]["buy"]["volume_raw"], "0")
        self.assertEqual(empty["buyers"], [])
        self.assertEqual(empty["price_days"], {step: {"total_days": 0, "bands": []} for step in ("5", "10")})
        self.assertEqual(empty["price_distribution"], {"5":{"buy":[], "sell":[]}, "10":{"buy":[], "sell":[]}})

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

    def test_price_bands_use_each_swap_exact_boundaries_and_separate_sides(self):
        data=self.compute([
            trade("buy", A, UNIT+1, 50_000),  # Just below 0.05, even if displayed as 0.05.
            trade("buy", B, UNIT, 50_000),    # Exactly 0.05 belongs to [0.05, 0.10).
            trade("buy", B, 2*UNIT, 199_999),
            trade("buy", A, UNIT, 100_000),   # Exactly 0.10 belongs to [0.10, 0.15).
            trade("sell", B, 10*UNIT, 1_200_000),
            trade("sell", B, 2*UNIT, 600_000),
            trade("buy", C, 999*UNIT, 99_000_000, attribution="pool_only"),
            trade("sell", "", 999*UNIT, 99_000_000),
        ])
        five=data["price_distribution"]["5"]
        self.assertEqual([b["from_price_raw"] for b in five["buy"]], ["0", "50000000000", "100000000000"])
        self.assertEqual([b["from_price_raw"] for b in five["sell"]], ["100000000000", "300000000000"])
        self.assertEqual(five["buy"][1], {"from_price_raw":"50000000000", "to_price_raw":"100000000000",
            "volume_raw":str(3*UNIT), "volume":"3", "quote_raw":"249999", "quote":"0.249999", "swaps":2,
            "price_raw":"83333000000"})
        ten=data["price_distribution"]["10"]
        self.assertEqual([b["from_price_raw"] for b in ten["buy"]], ["0", "100000000000"])
        self.assertEqual(ten["buy"][0]["volume_raw"], str(4*UNIT+1))
        for steps in data["price_distribution"].values():
            for side in ("buy", "sell"):
                for field in ("volume_raw", "quote_raw", "swaps"):
                    self.assertEqual(sum(int(b[field]) for b in steps[side]), int(data["summary"][side][field]))

    def test_price_bands_do_not_round_uint256_ratios_across_boundary(self):
        base=2**200
        volume,quote=base*100_000,base*10  # Exact ratio 0.10 USDT per WGNK.
        data=self.compute([trade("buy", A, volume, quote-1), trade("buy", B, volume, quote)])
        points=data["price_distribution"]["5"]["buy"]
        self.assertEqual([p["from_price_raw"] for p in points], ["50000000000", "100000000000"])
        self.assertEqual(points[0]["volume_raw"], str(volume))
        self.assertEqual(points[0]["quote_raw"], str(quote-1))
        self.assertEqual(points[0]["price_raw"], str((quote-1)*10**15//volume))
        self.assertEqual(points[1]["price_raw"], "100000000000")
        self.assertEqual(data["price_distribution"]["5"]["sell"], [])

    def test_one_day_uses_combined_volume_not_swap_count_or_each_side_separately(self):
        data=self.compute([
            trade("buy", A, 4*UNIT, 440_000, ts=1),
            trade("sell", B, 8*UNIT, 960_000, ts=1),
            trade("buy", A, 10*UNIT, 1_600_000, ts=1),
            trade("buy", C, 999*UNIT, 300_000_000, ts=1, attribution="pool_only"),
            trade("buy", A, UNIT, 160_000, ts=86401),
            trade("sell", B, 20*UNIT, 7_000_000, ts=86401),
            trade("buy", A, UNIT, 110_000, ts=172801),
            trade("buy", A, UNIT, 110_000, ts=172801),
            trade("sell", B, 3*UNIT, 660_000, ts=172801),
            trade("buy", "", 1000*UNIT, 100_000_000, ts=259201),
        ])
        days=data["price_days"]["5"]
        self.assertEqual(days["total_days"], 3)
        self.assertEqual([(r["from_price_raw"], r["days"]) for r in days["bands"]],
                         [("100000000000", 1), ("200000000000", 1), ("350000000000", 1)])
        # Separate Swap executions in the same transaction still count separately.
        self.assertEqual(data["price_distribution"]["5"]["buy"][0]["swaps"], 3)
        for distribution in data["price_days"].values():
            self.assertEqual(sum(r["days"] for r in distribution["bands"]), 3)

    def test_day_winners_are_recomputed_when_price_step_changes(self):
        data=self.compute([trade("buy", A, 9*UNIT, 1_440_000),
                           trade("buy", A, 6*UNIT, 1_260_000),
                           trade("sell", B, 6*UNIT, 1_560_000)])
        self.assertEqual(data["price_days"]["5"]["bands"],
                         [{"from_price_raw":"150000000000", "to_price_raw":"200000000000", "days":1}])
        self.assertEqual(data["price_days"]["10"]["bands"],
                         [{"from_price_raw":"200000000000", "to_price_raw":"300000000000", "days":1}])

    def test_calendar_days_follow_site_timezone_in_winter_and_summer_without_filling_gaps(self):
        rows=[]
        for iso, quote in [("2026-01-01T21:59:59+00:00", 100_000),
                           ("2026-01-01T22:00:00+00:00", 200_000),
                           ("2026-07-01T20:59:59+00:00", 100_000),
                           ("2026-07-01T21:00:00+00:00", 200_000)]:
            rows.append(trade("buy", A, UNIT, quote, ts=int(datetime.fromisoformat(iso).timestamp())))
        days=self.compute(rows)["price_days"]["5"]
        self.assertEqual(days["total_days"], 4)
        self.assertEqual([r["days"] for r in days["bands"]], [2, 2])

    def test_day_volume_comparison_is_exact_and_ties_are_order_independent(self):
        huge=2**200
        lower=trade("buy", A, huge*UNIT, huge*100_000)
        higher=trade("sell", B, (huge+1)*UNIT, (huge+1)*200_000)
        days=self.compute([lower, higher])["price_days"]["5"]
        self.assertEqual(days["bands"][0]["from_price_raw"], "200000000000")
        equal=trade("sell", B, huge*UNIT, huge*200_000)
        for rows in ([lower, equal], [equal, lower]):
            days=self.compute(rows)["price_days"]["5"]
            self.assertEqual(days["total_days"], 1)
            self.assertEqual(days["bands"][0]["from_price_raw"], "100000000000")

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
