import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.address_history import address_history
from app.codec import blank
from app.config import SEED_POOLS, USDT, ZERO
from app.db import Database
from app.flows import analysis, event_rows
from app.mints import initialize

A = "0x" + "2" * 40
B = "0x" + "3" * 40
POOL = SEED_POOLS[0]
UNIT = 10**9


def block(height, stamp):
    return {"height": height, "hash": "0x" + format(height, "064x"),
            "ts": int(datetime.fromisoformat(stamp).timestamp())}


class AddressHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        initialize(self.db)
        self.blocks = [block(h, ts) for h, ts in [
            (1, "2026-06-30T19:00:00+00:00"), (2, "2026-07-01T20:59:00+00:00"),
            (3, "2026-07-01T21:01:00+00:00"), (4, "2026-07-02T12:00:00+00:00"),
            (5, "2026-07-02T13:00:00+00:00"), (6, "2026-07-03T12:00:00+00:00"),
            (10, "2026-07-05T12:00:00+00:00")]]
        by_height = {b["height"]: b for b in self.blocks}
        events = []

        def add(h, kind, quantity, src="", dst="", **kw):
            events.append(blank("ethereum", by_height[h], "0x" + format(h * 100, "064x"),
                                len(events), kind, quantity * UNIT, src, dst, **kw))
        add(1, "bridge_mint", 1000, ZERO, A)
        add(1, "bridge_mint", 1000, ZERO, POOL)
        add(1, "bridge_mint", 100, ZERO, B)
        add(2, "transfer", 25, B, A)
        add(3, "transfer", 40, POOL, A)
        trade = {"actor": A, "pool": POOL, "quote_raw": "1000000", "quote_asset": "USDT",
                 "meta": {"attribution": "initiator_net"}}
        add(3, "buy", 40, **trade)
        add(4, "transfer", 10, A, POOL)
        add(4, "sell", 10, **trade)
        add(5, "transfer", 5, A, POOL)
        add(5, "liquidity_add", 5, actor=A, pool=POOL)
        add(5, "transfer", 7, A, A)  # Self-transfer is neutral, not a sale.
        add(5, "sell", 900, **{**trade, "meta": {"attribution": "initiator_only"}})
        add(5, "buy", 800, **{**trade, "pool": B})  # Not a verified pool.
        add(6, "bridge_burn", 500, A, ZERO)
        self.db.save_batch("ethereum", 1, 10, events, self.blocks)
        self.db.put("mints:deployment", self.blocks[0])
        self.db.put("mints:status", {"finalized_height": 10})
        self.snapshot = {**self.blocks[-1], "checked_at": self.blocks[-1]["ts"],
                         "supply_raw": str(1600 * UNIT), "ledger_verified": True,
                         "pools": [{"address": POOL, "balance_raw": str(975 * UNIT),
                                    "balance": "975", "fee": 3000, "quote": USDT}]}
        self.db.put("flow:snapshot", self.snapshot)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_actual_balances_include_mint_burn_transfers_and_lp_not_swaps_twice(self):
        data = analysis(self.db, q=A, side="all", limit=None)
        history = data["address_history"]
        self.assertEqual([p["date"] for p in history["points"]],
                         ["2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"])
        self.assertEqual([int(p["balance_raw"]) // UNIT for p in history["points"]],
                         [1000, 1025, 1050, 550, 550, 550])
        self.assertEqual(history["balance_raw"], data["address_balance"]["amount_raw"])
        self.assertEqual((history["height"], history["ts"]), (10, self.snapshot["ts"]))
        self.assertEqual(history["points"][-1]["balance_raw"], str(550 * UNIT))
        for key in ("bought_raw", "sold_raw", "buys_count", "sales_count"):
            self.assertEqual(sum(int(p[key]) for p in history["points"]), int(data["summary"][key]))
        self.assertEqual(data["summary"]["buys_count"], 1)
        self.assertEqual(data["summary"]["sales_count"], 1)
        # A transfer-only day and quiet days must not disappear from the line.
        self.assertEqual(history["points"][1]["bought_raw"], "0")
        self.assertEqual(history["points"][4]["sales_count"], 0)

    def test_timeline_is_independent_of_table_sort_pagination_and_trade_filters(self):
        expected = analysis(self.db, q=A, side="all")["address_history"]
        for options in ({"sort": "amount_asc", "limit": 1}, {"sort": "price_desc", "offset": 1},
                        {"hours": 1, "side": "buy"}):
            self.assertEqual(analysis(self.db, q=A, **options)["address_history"], expected)

    def test_empty_and_unverified_are_distinct(self):
        empty = analysis(self.db, q="0x" + "f" * 40)["address_history"]
        self.assertEqual((empty["points"], empty["balance_raw"]), ([], "0"))
        self.assertIsNone(analysis(self.db)["address_history"])
        self.db.put("flow:snapshot", {**self.snapshot, "ledger_verified": False})
        self.assertIsNone(analysis(self.db, q=A)["address_history"])
        self.db.put("flow:snapshot", self.snapshot)
        self.db.conn.execute("DELETE FROM ranges")
        self.db.conn.commit()
        self.assertIsNone(analysis(self.db, q=A)["address_history"])

    def test_only_finalized_events_through_same_snapshot_even_when_stale(self):
        expected = analysis(self.db, q=A)["address_history"]
        future = block(11, "2026-07-06T12:00:00+00:00")
        provisional = blank("ethereum", self.blocks[-1], "0x" + "a" * 64, 99,
                            "bridge_burn", UNIT, A, ZERO, finalized=False)
        later = blank("ethereum", future, "0x" + "b" * 64, 0, "bridge_burn", UNIT, A, ZERO)
        self.db.save_batch("ethereum", 10, 11, [provisional, later], [self.blocks[-1], future])
        self.db.put("mints:status", {"finalized_height": 20})
        self.db.put("flow:status", {"error": "RPC offline"})
        result = analysis(self.db, q=A)
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["address_history"], expected)

    def test_exact_large_raw_values_and_subunit_movements(self):
        raw = 2**200 + 7
        self.db.conn.execute("UPDATE events SET amount_raw=? WHERE kind='bridge_mint' AND dst=?", (str(raw), A))
        self.db.conn.execute("UPDATE events SET amount_raw='1' WHERE kind='bridge_burn'")
        self.db.conn.commit()
        data = analysis(self.db, q=A, side="all")
        self.assertEqual(data["address_history"]["points"][0]["balance_raw"], str(raw))
        self.assertEqual(data["address_history"]["balance_raw"], str(raw + 50 * UNIT - 1))
        self.assertIsInstance(data["address_history"]["balance_raw"], str)

    def test_transfer_only_address_has_balance_line_without_trades(self):
        data = analysis(self.db, q=B, side="all")
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["address_history"]["balance_raw"], str(75 * UNIT))
        self.assertTrue(data["address_history"]["points"])
        self.assertTrue(all(p["bought_raw"] == p["sold_raw"] == "0" for p in data["address_history"]["points"]))

    def test_bad_balance_or_timestamps_are_not_reported_as_zero(self):
        rows = event_rows(self.db, 1, 10)
        self.assertIsNone(address_history(rows, A, self.snapshot, 1))
        self.assertIsNone(address_history(rows, A, {**self.snapshot, "ts": self.blocks[0]["ts"]}, 550 * UNIT))
        rows = [e for e in rows if not (e["kind"] == "bridge_mint" and e["dst"] == A)]
        self.assertIsNone(address_history(rows, A, self.snapshot, -450 * UNIT))


if __name__ == "__main__":
    unittest.main()
