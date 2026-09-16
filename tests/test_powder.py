import tempfile
import unittest
from pathlib import Path
from app.config import ZERO, SEED_POOLS
from app.db import Database
from app.mints import initialize
from app.powder import (aggregate, funders, hubs, save_snapshot, side_split,
                        stable_balances, tracked_addresses, EXCHANGES, PowderCollector)
from test_flows import A, POOL, UNIT, block, event

B = "0x" + "3" * 40
EXCHANGE = next(iter(EXCHANGES))


def powder_transfer(db, name, tx, src, dst, amount, height=10):
    with db.conn:
        db.conn.execute("INSERT INTO powder_transfers VALUES(?,?,?,?,?,?,?,?)",
                        (f"ethereum:{tx}:{abs(hash((src,dst,amount)))%64}", name, height, 1000, tx, src, dst, str(amount)))


def balance(db, address, usdt=0, usdc=0, height=10):
    with db.conn:
        for name, raw in (("USDT", usdt), ("USDC", usdc)):
            if raw:
                db.conn.execute("INSERT OR REPLACE INTO powder_balances VALUES(?,?,?,?,?)",
                                (address, name, str(raw), height, 1))


class PowderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "t.sqlite3")
        initialize(self.db)
        self.db.put("mints:deployment", block(1))
        self.db.put("mints:status", {"finalized_height": 12})
        rows = [event(1, "bridge_mint", 5000, ZERO, A),
                event(2, "buy", 5000, actor=A, pool=POOL, quote_raw=str(2000 * 10**6),
                      quote_asset="USDT", meta={"attribution": "initiator_net"}),
                event(3, "sell", 5000, actor=B, pool=POOL, quote_raw=str(2000 * 10**6),
                      quote_asset="USDT", meta={"attribution": "initiator_net"})]
        self.db.save_batch("ethereum", 1, 12, rows, [block(i) for i in range(1, 13)])
        self.db.put("flow:snapshot", {"height": 12, "ts": block(12)["ts"], "supply_raw": str(5000 * UNIT),
                                      "ledger_verified": True,
                                      "pools": [{"address": POOL, "fee": 3000,
                                                 "balance_raw": str(5000 * UNIT)}]})

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_tracking_thresholds(self):
        tracked = tracked_addresses(self.db)
        self.assertEqual(set(tracked), {A, B})
        sides = side_split(self.db)
        self.assertEqual((sides[A]["buy_quote_raw"], sides[A]["sell_quote_raw"]), (2000 * 10**6, 0))

    def test_exchange_money_is_excluded_from_powder(self):
        powder_transfer(self.db, "USDT", "0x" + "1" * 64, EXCHANGE, A, 100_000 * 10**6)
        powder_transfer(self.db, "USDT", "0x" + "2" * 64, B, A, 50_000 * 10**6)
        balance(self.db, EXCHANGE, usdt=10**12)
        balance(self.db, A, usdt=1_000 * 10**6)
        balance(self.db, B, usdt=9_000 * 10**6)
        picture = aggregate(self.db)
        self.assertTrue(picture["ready"])
        buyer = next(r for r in picture["buyers"] if r["address"] == A)
        # B funds A and is counted; the exchange hot wallet is not.
        self.assertEqual(int(buyer["chain_raw"]), 9_000 * 10**6)
        self.assertEqual(int(buyer["own_raw"]), 1_000 * 10**6)
        self.assertEqual({f["address"] for f in buyer["funders"]}, {B})

    def test_hub_sender_is_treated_as_exchange(self):
        many = ["0x" + format(2**160 + i, "064x") for i in range(60)]
        for i, dst in enumerate(many):
            powder_transfer(self.db, "USDT", "0x" + format(2**64 + i, "064x"), "0x" + "7" * 40, dst, 5_000 * 10**6)
        self.assertIn("0x" + "7" * 40, hubs(self.db))

    def test_snapshots_keep_history_and_throttle(self):
        balance(self.db, A, usdt=5_000 * 10**6)  # above the dust filter
        picture = aggregate(self.db)
        save_snapshot(self.db, picture)
        save_snapshot(self.db, picture)  # immediate repeat is ignored
        rows = self.db.conn.execute("SELECT COUNT(*) FROM powder_snapshots").fetchone()[0]
        self.assertEqual(rows, 1)
        self.assertEqual(int(self.db.conn.execute(
            "SELECT buy_own_raw FROM powder_snapshots").fetchone()[0]), 5_000 * 10**6)

    def test_group_collapses_to_main_address(self):
        # C is a smaller buyer funded by A with a direct WGNK transfer: one
        # probable participant, only the main address (A) is analysed.
        c = "0x" + "4" * 40
        rows = [event(13, "buy", 3000, actor=c, pool=POOL, quote_raw=str(1200 * 10**6),
                      quote_asset="USDT", meta={"attribution": "initiator_net"}),
                event(14, "transfer", 1500, A, c)]
        self.db.save_batch("ethereum", 13, 14, rows, [block(13), block(14)])
        self.db.put("flow:snapshot", {"height": 14, "ts": block(14)["ts"], "supply_raw": str(8000 * UNIT),
                                      "ledger_verified": True,
                                      "pools": [{"address": POOL, "fee": 3000, "balance_raw": "0"}]})
        balance(self.db, A, usdt=2_000 * 10**6)
        picture = aggregate(self.db)
        buyers = [r["address"] for r in picture["buyers"]]
        self.assertEqual(buyers, [A])
        self.assertEqual(picture["buyers"][0]["group_size"], 2)
        self.assertEqual(picture["balance_coverage"]["qualified"], 1)
        self.assertEqual(picture["balance_coverage"]["covered"], 1)

    def test_seller_reserves_use_wgnk_ledger(self):
        balance(self.db, B, usdt=0)
        picture = aggregate(self.db)
        seller = next(r for r in picture["sellers"] if r["address"] == B)
        # B sold everything to the pool; its WGNK reserve is zero.
        self.assertEqual(int(seller["wgnk_raw"]), 0)
        self.assertIn("escrow_raw", picture["totals"])


if __name__ == "__main__":
    unittest.main()
