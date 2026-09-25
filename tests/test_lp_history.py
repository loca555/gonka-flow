import json
import tempfile
import unittest
from pathlib import Path

from app import lp_history
from app.db import Database

POOL = lp_history.POOL
Q96 = lp_history.Q96
L = 10 ** 13


def word(value):
    return "0x" + format(value % 2 ** 256, "064x")


def lp_event(tx, idx, height, kind, lower, upper, amount):
    return {"tx_hash": tx, "idx": idx, "height": height, "kind": kind,
            "tick_lower": lower, "tick_upper": upper, "amount": str(amount)}


def swap_row(tx, height, idx, ts, sqrt, liquidity, pool=POOL):
    return {"id": f"ethereum:{tx}:{idx}", "chain": "ethereum", "height": height,
            "block_hash": word(height), "ts": ts, "tx_hash": tx, "idx": idx,
            "kind": "sell", "src": "", "dst": "", "actor": "", "amount_raw": "1",
            "asset": "WGNK", "quote_raw": "1", "quote_asset": "USDT", "pool": pool,
            "finalized": 1, "request_key": "",
            "meta": json.dumps({"sqrt_price_raw": str(sqrt), "liquidity_raw": str(liquidity)})}


class DecodeTests(unittest.TestCase):
    def test_signatures_match_uniswap_v3(self):
        # Guard against a mistyped topic hash silently dropping every Burn.
        self.assertEqual(lp_history.BURN_SIG,
                         "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c")
        self.assertEqual(lp_history.MINT_SIG,
                         "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde")

    def test_mint_and_burn_logs(self):
        mint = {"topics": [lp_history.MINT_SIG, word(7), word(2 ** 23), word(600)],
                "data": "0x" + "00" * 32 + format(5, "064x") + "00" * 64,
                "transactionHash": "0x" + "a" * 64, "logIndex": "0x3",
                "blockNumber": "0x64"}
        self.assertEqual(lp_history.decode_log(mint),
                         ("0x" + "a" * 64, 3, 100, "mint", -(2 ** 23), 600, 5))
        burn = {"topics": [lp_history.BURN_SIG, word(7), word(-120), word(120)],
                "data": "0x" + format(9, "064x") + "00" * 64,
                "transactionHash": "0x" + "b" * 64, "logIndex": "0x1",
                "blockNumber": "0x65"}
        self.assertEqual(lp_history.decode_log(burn),
                         ("0x" + "b" * 64, 1, 101, "burn", -120, 120, 9))

    def test_unknown_topic_rejected(self):
        self.assertIsNone(lp_history.decode_log(
            {"topics": ["0x" + "c" * 64, word(1), word(2), word(3)],
             "data": "0x" + "00" * 96, "transactionHash": "0x" + "d" * 64,
             "logIndex": "0x0", "blockNumber": "0x1"}))


class DailyBandsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "db.sqlite3")
        self.day = 1778698115  # ts of a swap; local_day slices by date

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def _swap(self, height, idx=1, tx=None):
        tx = tx or "0x" + format(height, "064x")
        self.db.conn.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ethereum:{tx}:{idx}", "ethereum", height, word(height), self.day + height,
             tx, idx, "sell", "", "", "", "1", "WGNK", "1", "USDT", POOL, 1, "",
             json.dumps({"sqrt_price_raw": str(Q96), "liquidity_raw": str(L)})))

    def _lp(self, event):
        self.db.conn.execute("INSERT INTO lp_ticks VALUES(?,?,?,?,?,?,?)", (
            event["tx_hash"], event["idx"], event["height"], event["kind"],
            event["tick_lower"], event["tick_upper"], event["amount"]))

    def test_uniform_day_matches_exact_math(self):
        self._swap(100)
        out = lp_history.daily_bands(self.db)
        band = out["pools"][POOL]["2"][-1]
        # No ticks: buy average +2% (fee included) at sqrt endpoint 1.02*0.997.
        expected = int(L * Q96 * (1.02 * (1 - 0.003) - 1) / Q96 / (1 - 0.003))
        self.assertEqual(band["up_usdt_raw"], str(expected))

    def test_lp_event_after_last_swap_excluded_that_day(self):
        self._swap(100)
        # Lower edge inside the +2% band (tick 120 ~ +1.2%): crossing it adds
        # liquidity mid-walk (active L comes from the swap meta itself).
        self._lp(lp_event("0x" + "e" * 64, 1, 200, "mint", 120, 6000, 10 ** 16))
        first = lp_history.daily_bands(self.db)["pools"][POOL]["2"][-1]
        self._swap(300)
        second = lp_history.daily_bands(self.db)["pools"][POOL]["2"][-1]
        self.assertNotEqual(first["up_usdt_raw"], second["up_usdt_raw"])
        self.assertGreater(int(second["up_usdt_raw"]), int(first["up_usdt_raw"]))

    def test_rebuild_only_when_underlying_events_change(self):
        self._swap(100)
        self.assertTrue(lp_history.rebuild_if_stale(self.db))
        self.assertFalse(lp_history.rebuild_if_stale(self.db))
        self._lp(lp_event("0x" + "f" * 64, 1, 150, "mint", 600, 6600, 10 ** 15))
        self.assertTrue(lp_history.rebuild_if_stale(self.db))

    def test_current_net_sums_mint_and_burn(self):
        self._lp(lp_event("0x" + "1" * 64, 1, 10, "mint", -6000, 6000, 100))
        self._lp(lp_event("0x" + "2" * 64, 1, 11, "burn", 0, 6000, 40))
        net = lp_history.current_net(self.db)
        self.assertEqual(net, {-100: 100, 0: -40, 100: -60})


if __name__ == "__main__":
    unittest.main()
