import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from app.bridge_volume import bridge_daily
from app.leaders import trade_leaders
from app.timezones import local_day
from test_leaders import A, B, trade, source

UNIT = 10**9


def bridge_event(kind, raw, iso, tx="bridge", **extra):
    return dict(kind=kind, amount_raw=str(raw), ts=int(datetime.fromisoformat(iso).timestamp()),
                tx_hash=tx, idx=0, height=1, src=A, dst=B, **extra)


class BridgeVolumeTests(unittest.TestCase):
    def funding(self):
        return json.loads((Path(__file__).parent/"fixtures/wgnk_startup_funding.json").read_text())["events"]

    def compute(self, trades, bridge_rows, through=12):
        data=source(trades)
        data["bridge"]=bridge_daily(bridge_rows, through)
        with patch("app.leaders.analysis", return_value=data):
            return trade_leaders(None)

    def test_verified_startup_deposits_subtracted_once_not_entire_origin_inflow(self):
        rows=self.funding()
        # Further liquidity and later bridge inflows to the same origin stay in.
        later={**rows[0], "height":25900000, "ts":1788470400, "amount_raw":"1200000000000",
               "tx_hash":"later_bridge"}
        extra_transfer={**rows[-2], "tx_hash":"other_pool_deposit", "amount_raw":"99000000000000"}
        extra_lp={**rows[-1], "tx_hash":"other_pool_deposit", "amount_raw":"99000000000000"}
        rows += [later, extra_transfer, extra_lp]
        before=copy.deepcopy(rows)
        result=bridge_daily(rows,26000000)
        self.assertTrue(result["ready"])
        self.assertEqual(result["totals"]["pool_funding_raw"],"2253256036138261")
        self.assertEqual(result["totals"]["gross_in_raw"],"3002200000000000")
        self.assertEqual(result["totals"]["in_raw"],"748943963861739")
        first=result["daily"][0]
        self.assertEqual((first["date"],first["in_raw"]),("2026-06-09","747743963861739"))
        self.assertEqual(len(result["adjustment"]["deposits"]),7)
        self.assertEqual(result["totals"]["mints"],6)
        self.assertEqual(rows,before)  # No mutation of raw history.

    def test_snapshot_during_bootstrap_only_subtracts_observed_deposits(self):
        result=bridge_daily([e for e in self.funding() if e["height"]<=25280147],25280147)
        self.assertTrue(result["ready"])
        self.assertEqual(result["totals"]["pool_funding_raw"],"57144269905621")
        self.assertEqual(len(result["adjustment"]["deposits"]),1)

    def test_missing_mismatched_or_unfunded_startup_evidence_is_unknown_not_zero(self):
        rows=self.funding()
        changed=copy.deepcopy(rows)
        next(e for e in changed if e["kind"]=="liquidity_add")["amount_raw"]="1"
        bad_pool=copy.deepcopy(rows)
        next(e for e in bad_pool if e["kind"]=="liquidity_add")["pool"]=A
        bad_cases=[rows[:-1],changed,bad_pool,
                   [e for e in rows if e["kind"]!="bridge_mint"],
                   [e for e in rows if e["src"]!=rows[0]["dst"]]]
        for partial in bad_cases:
            with self.subTest(rows=len(partial)):
                result=bridge_daily(partial,26000000)
                self.assertFalse(result["ready"])
                self.assertIsNone(result["totals"])
                self.assertIsNone(result["daily"])
                output=self.compute([trade("buy",A,UNIT,100000)],partial,26000000)
                self.assertTrue(output["ready"])  # Trading remains usable.
                self.assertEqual(output["price_bridge"],{"5":None,"10":None})

    def test_bridge_follows_combined_day_winner_and_regroups_instead_of_merging_winners(self):
        # 5 cents: 0.15 wins; 10 cents: combined 0.20 and 0.25 wins.
        ts=int(datetime.fromisoformat("2026-07-01T12:00:00+00:00").timestamp())
        trades=[trade("buy",A,9*UNIT,1440000,ts=ts),trade("buy",A,6*UNIT,1260000,ts=ts),
                trade("sell",B,6*UNIT,1560000,ts=ts)]
        incoming=bridge_event("bridge_mint",123*UNIT,"2026-07-01T05:00:00+00:00")
        outgoing=bridge_event("bridge_burn",5*UNIT,"2026-07-01T17:00:00+00:00","out")
        data=self.compute(trades,[incoming,outgoing])
        for step,winner in [("5","150000000000"),("10","200000000000")]:
            bands=data["price_bridge"][step]["bands"]
            self.assertEqual(len(bands),1)
            self.assertEqual((bands[0]["from_price_raw"],bands[0]["in_raw"],bands[0]["out_raw"]),
                             (winner,str(123*UNIT),str(5*UNIT)))
            self.assertEqual(data["price_bridge"][step]["unassigned"]["days"],0)
        # Existing buy and sell totals are still just their swaps.
        self.assertEqual(data["summary"]["buy"]["volume_raw"],str(15*UNIT))
        self.assertEqual(data["summary"]["sell"]["volume_raw"],str(6*UNIT))

    def test_calendar_boundaries_no_trade_days_and_exact_uint256_reconcile(self):
        huge=2**200
        rows=[]
        for n,iso in enumerate(["2026-01-01T21:59:59+00:00","2026-01-01T22:00:00+00:00",
                                "2026-07-01T20:59:59+00:00","2026-07-01T21:00:00+00:00"]):
            rows.append(bridge_event("bridge_mint",huge+n,iso,str(n)))
        trades=[trade("buy",A,UNIT,110000,ts=rows[0]["ts"]),
                trade("sell",B,UNIT,210000,ts=rows[2]["ts"])]
        data=self.compute(trades,rows)
        for step in ("5","10"):
            series=data["price_bridge"][step]
            self.assertEqual(series["unassigned"]["days"],2)
            self.assertEqual(series["unassigned"]["in_raw"],str(2*huge+4))
            self.assertEqual(sum(int(b["in_raw"]) for b in series["bands"]),2*huge+2)
            self.assertEqual(sum(int(b["in_raw"]) for b in series["bands"])+int(series["unassigned"]["in_raw"]),
                             int(data["bridge"]["totals"]["in_raw"]))
        self.assertEqual([d["date"] for d in bridge_daily(rows,12)["daily"]],
                         ["2026-01-01","2026-01-02","2026-07-01","2026-07-02"])

    def test_empty_success_is_zero_but_missing_bridge_data_is_unknown(self):
        data=self.compute([trade("buy",A,UNIT,100000)],[])
        self.assertEqual(data["price_bridge"]["5"]["bands"][0]["in_raw"],"0")
        with patch("app.leaders.analysis",return_value=source([trade("buy",A,UNIT,100000)])):
            unknown=trade_leaders(None)
        self.assertIsNone(unknown["bridge"])
        self.assertEqual(unknown["price_bridge"],{"5":None,"10":None})
