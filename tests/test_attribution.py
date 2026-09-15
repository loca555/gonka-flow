import json
import tempfile
import unittest
from pathlib import Path
from app.codec import TRANSFER, SWAP, blank, parse_eth_log
from app.config import TOKEN, USDT, ZERO, SEED_POOLS
from app.db import Database
from app.mints import initialize
from app.attribution import migrate
from test_flows import A, POOL, UNIT, block, event

E = "0x" + "5" * 40      # executor / transaction initiator
P = "0x" + "6" * 40      # confirmed counterparty
Q = "0x" + "7" * 40
POOL2 = SEED_POOLS[1]
POOLS = {pool: {"token0": TOKEN, "quote": USDT, "quote_decimals": 6, "quote_symbol": "USDT"}
         for pool in (POOL, POOL2)}


def topic(address):
    return "0x" + address[2:].zfill(64)


def word(value):
    return format(value & ((1 << 256) - 1), "064x")


def eth_log(block, tx, idx, address, topics, data_words):
    return {"address": address, "transactionHash": tx, "blockHash": block["hash"],
            "blockNumber": hex(block["height"]), "transactionIndex": "0x0",
            "logIndex": hex(idx), "removed": False, "topics": topics,
            "data": "0x" + "".join(data_words)}


def swap_log(block, tx, idx, kind, quantity, quote, pool=POOL):
    token = -quantity if kind == "buy" else quantity
    paid = quote if kind == "buy" else -quote
    return eth_log(block, tx, idx, pool, [SWAP, topic(E), topic(P)], [word(token), word(paid)])


def transfer_log(block, tx, idx, source, target, quantity):
    return eth_log(block, tx, idx, TOKEN, [TRANSFER, topic(source), topic(target)], [word(quantity)])


def receipt(block, tx, logs, origin=E):
    return {"from": origin, "transactionHash": tx, "blockHash": block["hash"],
            "blockNumber": hex(block["height"]), "status": "0x1", "logs": logs}


class ParseAttributionTests(unittest.TestCase):
    def parse(self, kind, receipt_logs, pool=POOL):
        blk, tx = block(20), "0x" + "7" * 64
        swap = swap_log(blk, tx, 0, kind, 100 * UNIT, 40_000_000, pool)
        return parse_eth_log(swap, blk, POOLS, receipt(blk, tx, [swap, *receipt_logs]))

    def test_executor_buy_is_attributed_to_wgnk_recipient(self):
        parsed = self.parse("buy", [transfer_log(block(20), "0x" + "7" * 64, 1, POOL, P, 100 * UNIT)])
        self.assertEqual(parsed["kind"], "buy")
        self.assertEqual(parsed["actor"], P)
        meta = parsed["meta"]
        self.assertEqual(meta["attribution"], "tx_net")
        self.assertEqual(meta["initiator"], E)
        self.assertEqual(meta["initiator_net_raw"], "0")
        self.assertEqual(meta["attributed"], P)
        self.assertEqual(meta["attributed_net_raw"], str(100 * UNIT))

    def test_direct_buy_keeps_initiator_net(self):
        blk, tx = block(20), "0x" + "7" * 64
        swap = swap_log(blk, tx, 0, "buy", 100 * UNIT, 40_000_000)
        parsed = parse_eth_log(swap, blk, POOLS,
                               receipt(blk, tx, [swap, transfer_log(blk, tx, 1, POOL, P, 100 * UNIT)], origin=P))
        self.assertEqual(parsed["actor"], P)
        self.assertEqual(parsed["meta"]["attribution"], "initiator_net")

    def test_largest_matching_counterparty_wins_deterministically(self):
        blk, tx = block(21), "0x" + "8" * 64
        swap = swap_log(blk, tx, 0, "buy", 100 * UNIT, 40_000_000)
        parsed = parse_eth_log(swap, blk, POOLS, receipt(blk, tx, [
            swap,
            transfer_log(blk, tx, 1, POOL, P, 60 * UNIT),
            transfer_log(blk, tx, 2, POOL, Q, 30 * UNIT),
            transfer_log(blk, tx, 3, Q, P, 20 * UNIT)]))
        self.assertEqual(parsed["actor"], P)
        self.assertEqual(parsed["meta"]["attributed_net_raw"], str(80 * UNIT))

    def test_pool_to_pool_round_trip_stays_with_initiator_only(self):
        blk, tx = block(22), "0x" + "9" * 64
        swap = swap_log(blk, tx, 0, "buy", 100 * UNIT, 40_000_000)
        parsed = parse_eth_log(swap, blk, POOLS, receipt(blk, tx, [
            swap,
            transfer_log(blk, tx, 1, POOL, POOL2, 100 * UNIT),
            transfer_log(blk, tx, 2, POOL2, POOL, 100 * UNIT)]))
        self.assertEqual(parsed["actor"], E)
        self.assertEqual(parsed["meta"]["attribution"], "initiator_only")
        self.assertNotIn("attributed", parsed["meta"])

    def test_executor_sell_is_attributed_to_wgnk_payer(self):
        blk, tx = block(23), "0x" + "a" * 64
        swap = swap_log(blk, tx, 0, "sell", 100 * UNIT, 40_000_000)
        parsed = parse_eth_log(swap, blk, POOLS, receipt(blk, tx, [
            swap, transfer_log(blk, tx, 1, P, POOL, 100 * UNIT)]))
        self.assertEqual(parsed["kind"], "sell")
        self.assertEqual(parsed["actor"], P)
        self.assertEqual(parsed["meta"]["attribution"], "tx_net")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        initialize(self.db)
        self.db.put("mints:deployment", block(1))
        self.db.put("mints:status", {"finalized_height": 6})
        self.tx = "0x" + "b" * 64
        blk = block(4)
        rows = [event(1, "bridge_mint", 200, ZERO, A),
                blank("ethereum", blk, self.tx, 0, "transfer", 100 * UNIT, POOL, P),
                blank("ethereum", blk, self.tx, 1, "buy", 100 * UNIT, actor=E, pool=POOL,
                      quote_raw="40000000", quote_asset="USDT",
                      meta={"attribution": "initiator_only", "initiator": E, "initiator_net_raw": "0"}),
                event(5, "transfer", 20, A, POOL),
                event(6, "sell", 20, actor=A, pool=POOL, quote_raw="8000000", quote_asset="USDT",
                      meta={"attribution": "initiator_net"})]
        self.db.save_batch("ethereum", 1, 6, rows, [block(i) for i in range(1, 7)])
        self.db.put("flow:snapshot", {"height": 6, "supply_raw": str(180 * UNIT),
                                      "pools": [{"address": POOL, "fee": 3000, "balance_raw": str(20 * UNIT)}]})

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def swap_row(self):
        return self.db.conn.execute(
            "SELECT actor,meta FROM events WHERE kind='buy' AND tx_hash=?", (self.tx,)).fetchone()

    def test_migration_reattributes_stored_initiator_only_rows(self):
        self.assertEqual(self.swap_row()["actor"], E)
        self.assertEqual(migrate(self.db), 1)
        row = self.swap_row()
        self.assertEqual(row["actor"], P)
        meta = json.loads(row["meta"])
        self.assertEqual(meta["attribution"], "tx_net")
        self.assertEqual(meta["attributed"], P)
        self.assertEqual(meta["attributed_net_raw"], str(100 * UNIT))
        self.assertEqual(meta["initiator"], E)
        self.assertEqual(self.db.get("attribution:scheme"), 2)
        self.assertEqual(migrate(self.db), 0)
        self.assertEqual(self.swap_row()["actor"], P)

    def test_migration_does_not_touch_confirmed_rows(self):
        migrate(self.db)
        row = self.db.conn.execute(
            "SELECT actor,meta FROM events WHERE kind='sell'").fetchone()
        self.assertEqual(row["actor"], A)
        self.assertEqual(json.loads(row["meta"])["attribution"], "initiator_net")

    def test_migration_counts_in_leaders(self):
        from app.leaders import trade_leaders
        migrate(self.db)
        data = trade_leaders(self.db)
        self.assertTrue(data["ready"])
        buyers = {row["address"]: row for row in data["buyers"]}
        self.assertEqual(buyers[P]["volume"], "100")
        self.assertNotIn(E, buyers)
        self.assertEqual(data["summary"]["buy"]["volume_raw"], str(100 * UNIT))
        self.assertEqual(int(data["excluded"]["buy"]["volume_raw"]), 0)


if __name__ == "__main__":
    unittest.main()
