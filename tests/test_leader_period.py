"""A report period must apply to every total and use complete Cyprus calendar days."""
import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.bridge_volume import bridge_daily, bridge_since
from app.config import Settings
from app.leaders import trade_leaders
from app.main import create_app
from test_leaders import A, B, C, UNIT, source, trade
from test_bridge_volume import bridge_event


def stamp(iso):
    return int(datetime.fromisoformat(iso).timestamp())


class LeaderPeriodTests(unittest.TestCase):
    def test_september_boundary_recalculates_rankings_prices_counts_days_and_exclusions(self):
        midnight=stamp("2026-08-31T21:00:00+00:00")
        rows=[trade("buy",A,100*UNIT,40_000_000,ts=midnight-1),
              trade("buy",B,10*UNIT,1_000_000,ts=midnight),
              trade("buy",A,2*UNIT,300_000,ts=midnight+1),
              trade("buy",A,2*UNIT,300_000,ts=midnight+2),
              trade("sell",C,100*UNIT,30_000_000,ts=midnight-1),
              trade("sell",A,5*UNIT,1_000_000,ts=midnight+86400),
              trade("buy",C,999*UNIT,99_900_000,ts=midnight-1,attribution="pool_only"),
              trade("buy",C,30*UNIT,3_000_000,ts=midnight,attribution="pool_only")]
        data=source(rows)
        before=copy.deepcopy(data)
        with patch("app.leaders.analysis",return_value=data):
            result=trade_leaders(None,"2026-09-01")
            full=trade_leaders(None)
        self.assertEqual((result["start_date"],result["since"],result["period"]),("2026-09-01",midnight,"since_date"))
        self.assertEqual([r["address"] for r in full["buyers"]],[A,B])
        self.assertEqual([r["address"] for r in result["buyers"]],[B,A])
        self.assertEqual([r["rank"] for r in result["buyers"]],[1,2])
        self.assertEqual((result["buyers"][1]["average_price"],result["buyers"][1]["swaps"],result["buyers"][1]["transactions"]),("0.15",2,1))
        self.assertEqual(result["summary"]["buy"]["volume"],"14")
        self.assertEqual(result["summary"]["buy"]["quote"],"1.6")
        self.assertEqual(result["summary"]["sell"]["volume"],"5")
        self.assertEqual(result["excluded"]["buy"]["volume"],"30")
        for step in ("5","10"):
            self.assertEqual(result["price_days"][step]["total_days"],2)
            self.assertEqual(sum(b["days"] for b in result["price_days"][step]["bands"]),2)
            for side in ("buy","sell"):
                for key in ("volume_raw","quote_raw","swaps"):
                    self.assertEqual(sum(int(b[key]) for b in result["price_distribution"][step][side]),int(result["summary"][side][key]))
        self.assertEqual(data,before)

    def test_midnight_uses_cyprus_offset_in_winter_and_summer(self):
        for day,iso in [("2026-01-02","2026-01-01T22:00:00+00:00"),("2026-07-02","2026-07-01T21:00:00+00:00")]:
            with self.subTest(day=day):
                cutoff=stamp(iso)
                rows=[trade("buy",A,10*UNIT,1_000_000,ts=cutoff-1),trade("buy",B,UNIT,100_000,ts=cutoff)]
                with patch("app.leaders.analysis",return_value=source(rows)):
                    data=trade_leaders(None,day)
                self.assertEqual(data["since"],cutoff)
                self.assertEqual([r["address"] for r in data["buyers"]],[B])

    def test_bridge_period_excludes_earlier_days_and_keeps_startup_verification(self):
        funding=json.loads((Path(__file__).parent/"fixtures/wgnk_startup_funding.json").read_text())["events"]
        rows=funding+[
            bridge_event("bridge_mint",100*UNIT,"2026-08-31T20:59:59+00:00","before"),
            bridge_event("bridge_mint",37*UNIT,"2026-08-31T21:00:00+00:00","in"),
            bridge_event("bridge_burn",3*UNIT,"2026-09-01T19:00:00+00:00","out"),
            bridge_event("bridge_mint",8*UNIT,"2026-09-02T21:00:00+00:00","no_trades")]
        bridge=bridge_daily(rows,26000000)
        before=copy.deepcopy(bridge)
        data=source([trade("buy",A,UNIT,100_000,ts=stamp("2026-08-31T21:00:00+00:00"))])
        data["bridge"]=bridge
        with patch("app.leaders.analysis",return_value=data):
            result=trade_leaders(None,"2026-09-01")
        totals=result["bridge"]["totals"]
        self.assertEqual((totals["gross_in_raw"],totals["in_raw"],totals["out_raw"]),(str(45*UNIT),str(45*UNIT),str(3*UNIT)))
        self.assertEqual((totals["pool_funding_raw"],totals["mints"],totals["burns"]),("0",2,1))
        self.assertEqual(result["bridge"]["adjustment"]["amount_raw"],"0")
        self.assertEqual(result["bridge"]["adjustment"]["deposits"],[])
        for step in ("5","10"):
            series=result["price_bridge"][step]
            self.assertEqual((series["unassigned"]["days"],series["unassigned"]["in_raw"]),(1,str(8*UNIT)))
            self.assertEqual(series["bands"][0]["in_raw"],str(37*UNIT))
            self.assertEqual(series["bands"][0]["out_raw"],str(3*UNIT))
            for key in totals:
                self.assertEqual(sum(int(b[key]) for b in series["bands"])+int(series["unassigned"][key]),int(totals[key]))
        self.assertEqual(bridge_since(bridge,"2026-06-09"),before)
        self.assertEqual(bridge,before)
        self.assertFalse(bridge_since(bridge_daily(funding[:-1],26000000),"2026-09-01")["ready"])

    def test_no_trades_after_date_is_empty_while_unavailable_snapshot_stays_unknown(self):
        data=source([trade("buy",A,UNIT,100_000,ts=stamp("2026-08-31T20:59:59+00:00"))])
        data["bridge"]=bridge_daily([],12)
        with patch("app.leaders.analysis",return_value=data):
            empty=trade_leaders(None,"2026-09-01")
        self.assertTrue(empty["ready"])
        self.assertEqual(empty["buyers"],[])
        self.assertEqual(empty["summary"]["buy"]["volume_raw"],"0")
        self.assertEqual(empty["price_days"]["5"]["total_days"],0)
        self.assertEqual(empty["price_bridge"]["5"]["unassigned"]["days"],0)
        with patch("app.leaders.analysis",return_value=source([],False)):
            unavailable=trade_leaders(None,"2026-09-01")
        self.assertFalse(unavailable["ready"])
        self.assertIsNone(unavailable["summary"])
        self.assertEqual(unavailable["start_date"],"2026-09-01")

    def test_api_validates_dates_and_caches_each_period_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg=Settings(mode="mints",data_dir=Path(temp),indexer_enabled=False,history_from="")
            with TestClient(create_app(cfg)) as client, patch("app.main.trade_leaders",side_effect=lambda db,start_date=None,grouped=True:{"start_date":start_date,"grouped":grouped}) as calculate:
                for day in (None,"2026-09-01","2026-09-02","2026-09-01",None):
                    response=client.get("/api/mints/leaders",params={"start_date":day} if day else {})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.json()["start_date"],day)
                self.assertEqual(calculate.call_count,3)
                for invalid in ("2026-02-30","01.09.2026","not-a-date"):
                    self.assertEqual(client.get("/api/mints/leaders",params={"start_date":invalid}).status_code,422)
                self.assertEqual(calculate.call_count,3)


if __name__=="__main__":
    unittest.main()
