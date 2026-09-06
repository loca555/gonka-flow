import json
import unittest
from app.config import ZERO
from app.holder_groups import outside_holders


POOL = "0x" + "f" * 40


def address(index):
    return "0x" + format(index, "040x")


def trade(owner, side, quantity, **extra):
    return {"kind": side, "actor": owner, "amount_raw": str(quantity), "height": 10,
            "finalized": 1, "pool": POOL, "meta": json.dumps({"attribution": "initiator_net"}), **extra}


def report(rows, ledger, **changes):
    snapshot = {"height": 10, "ts": 100, "ledger_verified": True,
                "supply_raw": str(sum(ledger.values())),
                "pools": [{"address": POOL, "balance_raw": str(ledger.get(POOL, 0))}], **changes}
    return outside_holders(rows, ledger, snapshot)


class HolderGroupTests(unittest.TestCase):
    def test_groups_use_volume_not_trade_count_or_balance(self):
        owners = [address(i) for i in range(1, 6)]
        ledger = {**dict(zip(owners, [20, 30, 40, 10, 0])), POOL: 500}
        rows = [trade(owners[0], "buy", 100), trade(owners[0], "sell", 5),
                trade(owners[1], "buy", 5), trade(owners[1], "sell", 100),
                trade(owners[2], "buy", 60), trade(owners[2], "sell", 40),
                trade(owners[4], "buy", 9999), trade(POOL, "sell", 9999)]
        data = report(rows, ledger)
        self.assertTrue(data["ready"])
        self.assertEqual((data["total_raw"], data["addresses"]), ("100", 4))
        self.assertEqual([g["balance_raw"] for g in data["groups"]], ["20", "30", "40", "10"])
        self.assertEqual([g["addresses"] for g in data["groups"]], [1, 1, 1, 1])
        # Two tiny sales still count by WGNK volume, not by two trades versus one buy.
        rows += [trade(owners[0], "sell", 1)]
        self.assertEqual([g["balance_raw"] for g in report(rows, ledger)["groups"]], ["20", "30", "40", "10"])
        for group in data["groups"]:
            self.assertEqual(len(group["holders"]), group["addresses"])
            self.assertEqual(sum(int(h["balance_raw"]) for h in group["holders"]), int(group["balance_raw"]))
        self.assertEqual(data["groups"][0]["holders"][0]["bought_raw"], "100")
        self.assertEqual(data["groups"][0]["holders"][0]["sold_raw"], "5")

    def test_strict_90_percent_boundary_is_exact_for_large_integers(self):
        n = 2**200
        amounts = [(9*n, n), (n, 9*n), (9*n+1, n), (n, 9*n+1), (1, 0), (0, 1)]
        ledger = {address(i): n+i for i in range(1, 7)}
        rows = [trade(address(i), side, quantity) for i, pair in enumerate(amounts, 1)
                for side, quantity in zip(("buy", "sell"), pair)]
        data = report(rows, ledger)
        groups = {g["id"]: g for g in data["groups"]}
        self.assertEqual(groups["traders"]["balance_raw"], str(2*n+3))
        self.assertEqual(groups["investors"]["balance_raw"], str(2*n+8))
        self.assertEqual(groups["sellers"]["balance_raw"], str(2*n+10))
        self.assertEqual(sum(int(g["balance_raw"]) for g in groups.values()), int(data["total_raw"]))

    def test_unconfirmed_other_pools_transfers_mints_burns_lp_not_trades(self):
        owner = address(1)
        rows = [trade(owner, "buy", 100, meta='{"attribution":"initiator_only"}'),
                trade(owner, "sell", 100, pool=address(7)),
                trade(owner, "buy", 100, finalized=0), trade(owner, "sell", 100, height=11)]
        rows += [trade(owner, kind, 100) for kind in ("transfer", "bridge_mint", "bridge_burn", "liquidity_add")]
        data = report(rows, {owner: 1})
        self.assertEqual(data["groups"][-1]["balance_raw"], "1")
        rows.append(trade(owner, "buy", 1))
        self.assertEqual(report(rows, {owner: 1})["groups"][0]["balance_raw"], "1")

    def test_unavailable_or_inconsistent_balance_is_not_zero(self):
        owner = address(1)
        for ledger, changes in [({owner: 1}, {"ledger_verified": False}),
                                 ({owner: -1, POOL: 2}, {}), ({owner: 1}, {"supply_raw": "2"}),
                                 ({owner: 1}, {"pools": [{"address": POOL, "balance_raw": "1"}]}),
                                 ({ZERO: 1}, {})]:
            with self.subTest(ledger=ledger, changes=changes):
                data = report([], ledger, **changes)
                self.assertFalse(data["ready"])
                self.assertIsNone(data["total_raw"])
                self.assertEqual(data["groups"], [])

    def test_zero_balance_and_zero_volume(self):
        for ledger in ({}, {POOL: 5}):
            data = report([], ledger)
            self.assertTrue(data["ready"])
            self.assertEqual((data["total_raw"], data["addresses"]), ("0", 0))
        data = report([trade(address(1), "buy", 0)], {address(1): 1})
        self.assertEqual(data["groups"][-1]["addresses"], 1)


if __name__ == "__main__":
    unittest.main()
