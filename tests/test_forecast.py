import tempfile
import unittest
from pathlib import Path
from app.config import ZERO, SEED_POOLS
from app.db import Database
from app.forecast import snapshot, model_prompt, _momentum, _flow_windows
from app.mints import initialize
from app.powder import save_snapshot
from test_flows import A, POOL, UNIT, block, event

POOL = SEED_POOLS[0]


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "t.sqlite3")
        initialize(self.db)
        self.db.put("mints:deployment", block(1))
        import time
        self.now = int(time.time())
        self.db.put("mints:status", {"finalized_height": 12})
        self.db.put("flow:snapshot", {"height": 12, "ts": self.now, "supply_raw": str(5000 * UNIT),
                                      "ledger_verified": True,
                                      "pools": [{"address": POOL, "fee": 3000,
                                                 "balance_raw": str(5000 * UNIT)}]})

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def seed_swaps(self, buy_quote, sell_quote, hours_ago=1):
        ts = block(1)["ts"]
        rows = [event(1, "bridge_mint", 5000, ZERO, A)]
        rows.append({**event(2, "buy", 1000, actor=A, pool=POOL, quote_raw=str(buy_quote),
                             quote_asset="USDT", meta={"attribution": "initiator_net"}), "ts": self.now - hours_ago * 3600})
        rows.append({**event(3, "sell", 1000, actor="0x" + "3" * 40, pool=POOL, quote_raw=str(sell_quote),
                             quote_asset="USDT", meta={"attribution": "initiator_net"}), "ts": self.now - hours_ago * 3600})
        self.db.save_batch("ethereum", 1, 12, rows, [block(i) for i in range(1, 13)])

    def test_not_ready_without_verified_snapshot(self):
        self.db.put("flow:snapshot", {"height": 12})
        self.assertFalse(snapshot(self.db)["ready"])

    def test_strong_buy_pressure_scores_up(self):
        self.seed_swaps(900 * 10**6, 100 * 10**6)
        data = snapshot(self.db)
        self.assertTrue(data["ready"])
        flow = next(r for r in data["rules"] if r["key"] == "flow")
        self.assertEqual(flow["direction"], "up")
        self.assertEqual(data["verdict"]["direction"], "up")

    def test_strong_sell_pressure_scores_down(self):
        self.seed_swaps(100 * 10**6, 900 * 10**6)
        data = snapshot(self.db)
        flow = next(r for r in data["rules"] if r["key"] == "flow")
        self.assertEqual(flow["direction"], "down")

    def test_bridge_rule_reads_mint_burn_events(self):
        self.seed_swaps(500 * 10**6, 500 * 10**6)
        rows = [{**event(4, "bridge_mint", 8000, ZERO, "0x" + "4" * 40), "ts": self.now - 3600},
                {**event(5, "bridge_burn", 1000, "0x" + "5" * 40, ZERO), "ts": self.now - 3600}]
        for row in rows:
            self.db.save_batch("ethereum", row["height"], row["height"], [row], [block(row["height"])])
        data = snapshot(self.db)
        bridge = next(r for r in data["rules"] if r["key"] == "bridge")
        self.assertEqual(bridge["direction"], "down")

    def test_model_prompt_contains_numbers(self):
        self.seed_swaps(900 * 10**6, 100 * 10**6)
        data = snapshot(self.db)
        prompt = model_prompt(data)
        self.assertIn("ВЕРДИКТ", prompt)
        self.assertIn("рост", prompt)

    def test_reload_rule_uses_snapshot_history(self):
        self.seed_swaps(500 * 10**6, 500 * 10**6)
        with self.db.conn:
            self.db.conn.execute("INSERT INTO powder_snapshots VALUES(?,?,?,?,?,?,?,?)",
                                 (self.now - 25 * 3600, "1000000", "0", "0", "0", "0", 1, 0))
            self.db.conn.execute("INSERT INTO powder_snapshots VALUES(?,?,?,?,?,?,?,?)",
                                 (self.now - 3600, "5000000", "0", "0", "0", "0", 1, 0))
        data = snapshot(self.db)
        reload = next(r for r in data["rules"] if r["key"] == "reload")
        self.assertEqual(reload["direction"], "up")


if __name__ == "__main__":
    unittest.main()
