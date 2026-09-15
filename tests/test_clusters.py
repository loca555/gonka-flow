import tempfile
import unittest
from pathlib import Path
from app.clusters import compute, LABELS
from app.config import ZERO
from app.db import Database
from app.mints import initialize
from app.powder import initialize as initialize_powder
from test_flows import A, POOL, UNIT, block, event

B = "0x" + "3" * 40
C = "0x" + "4" * 40
GNK1 = "gonka1" + "q" * 38


def link(tx, gnk, *addresses):
    import json
    value = {"tx_hash": tx, "log_index": 0, "eth_address": addresses[0],
             "gnk_address": gnk, "amount_raw": "1000000000"}
    return (tx, 0, gnk, json.dumps(value))


class ClusterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "t.sqlite3")
        initialize(self.db)
        initialize_powder(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_no_edges_yields_no_groups(self):
        self.assertEqual(compute(self.db, [A, B]), {"groups": [], "address_group": {}})

    def test_bridge_counterparty_groups_addresses(self):
        with self.db.conn:
            self.db.conn.execute("INSERT INTO gonka_mint_links VALUES(?,?,?,?)", link("0x" + "1" * 64, GNK1, A))
            self.db.conn.execute("INSERT INTO gonka_mint_links VALUES(?,?,?,?)",
                                 link("0x" + "2" * 64, GNK1, B))
        data = compute(self.db, [A, B, C])
        self.assertEqual([g["addresses"] for g in data["groups"]], [[A, B]])
        self.assertEqual(data["groups"][0]["evidence"][0]["label"], LABELS["bridge"])
        self.assertEqual(data["address_group"][A], data["address_group"][B])

    def test_wgnk_funding_groups_direct_transfer_only(self):
        rows = [event(1, "bridge_mint", 100000, ZERO, A),
                event(2, "transfer", 50, A, B),
                event(3, "transfer", 5000, A, C)]
        self.db.save_batch("ethereum", 1, 3, rows, [block(i) for i in range(1, 4)])
        data = compute(self.db, [A, B, C])
        self.assertEqual(sorted(data["address_group"]), sorted([A, C]))
        self.assertEqual(data["groups"][0]["evidence"][0]["type"], "funding")

    def test_common_stable_funder_groups_but_payer_router_does_not(self):
        with self.db.conn:
            for i, dst in enumerate((A, B)):
                self.db.conn.execute(
                    "INSERT INTO powder_transfers VALUES(?,?,?,?,?,?,?,?)",
                    (f"ethereum:0x{2**60 + i:064x}:0", "USDT", 10, 1, f"0x{2**60 + i:064x}",
                     "0x" + "9" * 40, dst, str(5_000 * 10**6)))
        data = compute(self.db, [A, B], pools=[POOL])
        self.assertEqual([g["addresses"] for g in data["groups"]], [[A, B]])
        self.assertEqual(data["groups"][0]["evidence"][0]["type"], "stable_funder")

    def test_same_tx_payer_below_router_limit_groups(self):
        from app.codec import blank
        tx = "0x" + "5" * 64
        swap = blank("ethereum", block(10), tx, 2, "buy", 10**9, POOL, B, actor=B, pool=POOL,
                     quote_raw="400000", quote_asset="USDT",
                     meta={"attribution": "tx_net", "initiator": "0x" + "9" * 40,
                           "initiator_net_raw": "0"})
        self.db.save_batch("ethereum", 10, 10, [swap], [block(10)])
        with self.db.conn:
            # A itself pays the pool inside B's attributed purchase.
            self.db.conn.execute("INSERT INTO powder_transfers VALUES(?,?,?,?,?,?,?,?)",
                                 (f"{tx}:1", "USDT", 10, 1, tx, A, POOL, str(10_000 * 10**6)))
        data = compute(self.db, [A, B], pools=[POOL])
        self.assertEqual([g["addresses"] for g in data["groups"]], [[A, B]])
        self.assertTrue(any(e["type"] == "executor" for g in data["groups"] for e in g["evidence"]))

    def test_executor_with_own_flow_groups_attributed_parties(self):
        from app.codec import blank
        initiator = "0x" + "8" * 40
        rows = []
        for i, actor in enumerate((A, B)):
            tx = "0x" + format(2**60 + i, "064x")
            rows.append(blank("ethereum", block(10), tx, 0, "buy", 10**9, POOL, actor,
                              actor=actor, pool=POOL, quote_raw="400000", quote_asset="USDT",
                              meta={"attribution": "tx_net", "initiator": initiator,
                                    "initiator_net_raw": "1000000000"}))
        self.db.save_batch("ethereum", 10, 10, rows, [block(10)])
        data = compute(self.db, [A, B])
        self.assertEqual([g["addresses"] for g in data["groups"]], [[A, B]])


if __name__ == "__main__":
    unittest.main()
