import json
import unittest
from datetime import datetime, timezone

from app.config import ZERO
from app.flows import balances
from app.holder_groups import GROUPS, outside_holders
from app.holder_history import holder_history

A, B, POOL, OTHER = ["0x"+str(i)*40 for i in (1, 2, 3, 4)]
BASE = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())


def event(day, kind, quantity, src="", dst="", actor="", **extra):
    return {"height": day*10, "ts": BASE+(day-1)*86400+43200, "idx": 0, "kind": kind,
            "amount_raw": str(quantity), "src": src, "dst": dst, "actor": actor,
            "pool": POOL, "finalized": 1, "meta": json.dumps({"attribution": "initiator_net"}), **extra}


def report(rows, day, **options):
    snapshot = {"height": day*10+9, "ts": BASE+(day-1)*86400+46800, "ledger_verified": True}
    snapshot.update(options.pop("snapshot", {}))
    selected = [e for e in rows if e["finalized"] and e["height"] <= snapshot["height"]]
    ledger, minted, burned = balances(selected)
    snapshot.update(supply_raw=str(minted-burned), pools=[{"address": POOL, "balance_raw": str(ledger.get(POOL, 0))}])
    current = outside_holders(selected, ledger, snapshot)
    deployment = options.pop("deployment", {"height": 1, "ts": BASE})
    return holder_history(rows, ledger, snapshot, deployment, current), current


class HolderHistoryTests(unittest.TestCase):
    def progression(self):
        return [event(1, "bridge_mint", 100, ZERO, A), event(1, "bridge_mint", 1000, ZERO, POOL),
                event(2, "transfer", 90, POOL, A), event(2, "buy", 90, actor=A),
                event(3, "transfer", 10, A, POOL), event(3, "sell", 10, actor=A),
                event(4, "bridge_mint", 1000, ZERO, A), event(4, "transfer", 900, A, POOL), event(4, "sell", 900, actor=A),
                event(5, "transfer", 80, A, B), event(6, "bridge_burn", 200, A, ZERO)]

    def test_historical_categories_and_new_addresses_not_current_cohorts(self):
        history, current = report(self.progression(), 6)
        self.assertTrue(history["ready"])
        self.assertEqual([[p[g+"_raw"] for g in GROUPS] for p in history["points"]],
                         [["0", "0", "0", "100"], ["190", "0", "0", "0"], ["0", "0", "180", "0"],
                          ["0", "280", "0", "0"], ["0", "200", "0", "80"], ["0", "0", "0", "80"]])
        self.assertEqual(history["points"][3]["addresses"]["unclassified"], 0)
        self.assertEqual(history["points"][4]["addresses"]["unclassified"], 1)
        # A has since emptied its wallet, yet its historical balance is retained.
        self.assertNotIn(A, [h["address"] for g in current["groups"] for h in g["holders"]])
        self.assertEqual(history["points"][1]["investors_raw"], "190")
        for point in history["points"]:
            self.assertEqual(sum(int(point[g+"_raw"]) for g in GROUPS), int(point["total_raw"]))
        for group in current["groups"]:
            self.assertEqual(history["points"][-1][group["id"]+"_raw"], group["balance_raw"])
            self.assertEqual(history["points"][-1]["addresses"][group["id"]], group["addresses"])

    def test_future_trades_cannot_change_earlier_points(self):
        rows = self.progression()
        prefix, _ = report(rows, 3)
        full, _ = report(rows, 6)
        self.assertEqual(prefix["points"], full["points"][:3])

    def test_zero_days_before_first_receipt_then_carry_forward(self):
        history, _ = report([event(3, "bridge_mint", 500, ZERO, A)], 5)
        self.assertTrue(history["ready"])
        self.assertEqual([p["unclassified_raw"] for p in history["points"]], ["0", "0", "500", "500", "500"])
        self.assertEqual([p["addresses"]["unclassified"] for p in history["points"]], [0, 0, 1, 1, 1])

    def test_transfers_and_lp_do_not_make_recipients_investors(self):
        rows = [event(1, "bridge_mint", 100, ZERO, A), event(1, "transfer", 30, A, B),
                event(1, "transfer", 20, B, POOL), event(1, "liquidity_add", 20, actor=B),
                event(1, "transfer", 9, A, A), event(2, "transfer", 10, POOL, B),
                event(2, "liquidity_remove", 10, actor=B)]
        history, _ = report(rows, 2)
        self.assertTrue(history["ready"])
        self.assertEqual([p["unclassified_raw"] for p in history["points"]], ["80", "90"])
        self.assertEqual(history["points"][-1]["addresses"]["unclassified"], 2)

    def test_trade_scope_and_pending_data_are_excluded(self):
        rows = [event(1, "bridge_mint", 100, ZERO, A),
                event(1, "buy", 900, actor=A, meta='{"attribution":"initiator_only"}'),
                event(1, "sell", 900, actor=A, pool=OTHER), event(1, "buy", 900, actor=A, finalized=0),
                event(1, "buy", 900, actor=ZERO), event(1, "buy", 900, actor="unknown"),
                event(1, "bridge_mint", 900, ZERO, B, finalized=0), event(3, "buy", 900, actor=A)]
        history, _ = report(rows, 2)
        self.assertTrue(history["ready"])
        self.assertEqual([p["unclassified_raw"] for p in history["points"]], ["100", "100"])

    def test_strict_threshold_reclassifies_entire_large_balance_exactly(self):
        n = 2**200
        rows = [event(1, "bridge_mint", 100*n, ZERO, A), event(1, "buy", 9*n, actor=A),
                event(2, "sell", n, actor=A), event(3, "buy", 1, actor=A)]
        history, _ = report(rows, 3)
        self.assertTrue(history["ready"])
        self.assertEqual(history["points"][0]["investors_raw"], str(100*n))
        self.assertEqual(history["points"][1]["traders_raw"], str(100*n))
        self.assertEqual(history["points"][2]["investors_raw"], str(100*n))

    def test_historical_negative_balance_is_not_faked_as_zero(self):
        rows = [event(1, "transfer", 10, A, POOL), event(2, "bridge_mint", 10, ZERO, A)]
        history, current = report(rows, 2)
        self.assertTrue(current["ready"])
        self.assertFalse(history["ready"])
        self.assertEqual(history["points"], [])
        self.assertEqual(history["reason"], "historical_ledger_mismatch")

    def test_invalid_timestamp_or_unverified_snapshot_not_shown_as_zero(self):
        for rows, opts in [([event(1, "bridge_mint", 10, ZERO, A, ts=BASE-1)], {}),
                           ([event(1, "bridge_mint", 10, ZERO, A)], {"snapshot": {"ledger_verified": False}}),
                           ([], {"deployment": {"height": 1}})]:
            with self.subTest(options=opts):
                history, _ = report(rows, 2, **opts)
                self.assertFalse(history["ready"])
                self.assertEqual(history["points"], [])

    def test_calendar_day_boundary_uses_existing_timezone(self):
        rows = [event(1, "bridge_mint", 10, ZERO, A, ts=BASE+20*3600+59*60),
                event(1, "bridge_mint", 20, ZERO, A, ts=BASE+21*3600+60)]
        history, _ = report(rows, 2)
        self.assertEqual([p["unclassified_raw"] for p in history["points"]], ["10", "30"])

    def test_daylight_saving_uses_calendar_days_not_24_hour_buckets(self):
        start = int(datetime(2026, 10, 24, 21, tzinfo=timezone.utc).timestamp())
        rows = [event(1, "bridge_mint", 10, ZERO, A, ts=start+1800),
                event(2, "bridge_mint", 20, ZERO, A, ts=start+86400+1800)]
        history, _ = report(rows, 2, deployment={"height": 1, "ts": start}, snapshot={"ts": start+27*3600})
        self.assertTrue(history["ready"])
        self.assertEqual([p["date"] for p in history["points"]], ["2026-10-25", "2026-10-26"])
        self.assertEqual([p["unclassified_raw"] for p in history["points"]], ["30", "30"])

    def test_empty_ledger_has_four_valid_zero_series(self):
        history, _ = report([], 1)
        self.assertTrue(history["ready"])
        self.assertEqual([history["points"][0][g+"_raw"] for g in GROUPS], ["0"]*4)
