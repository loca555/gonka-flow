import unittest

from app.depth import FEE, _amounts_down, _amounts_up, _solve_avg

Q96 = 2 ** 96
L = 10 ** 13
S = Q96  # tick 0: sqrt price equals the X96 scale
PRICE = 1e3  # (S/Q96)^2 * 1e3: pool price at tick 0


class DepthAverageRateTests(unittest.TestCase):
    def test_uniform_buy_reaches_average_target(self):
        # Uniform liquidity, fee on the input: the curve reaches the trader's
        # +2% average at sqrt endpoint 1.02*(1-FEE)*S.
        end = _solve_avg({}, 0, L, S, PRICE, 2, up=True)
        self.assertAlmostEqual(end / S, 1.02 * (1 - FEE), places=9)
        usdt, wgnk = _amounts_up({}, 0, L, S, end)
        self.assertAlmostEqual(usdt, L * (end - S) / Q96, places=3)
        self.assertAlmostEqual(usdt / (1 - FEE) * 1e3 / wgnk, PRICE * 1.02, places=4)

    def test_uniform_sell_reaches_average_target(self):
        end = _solve_avg({}, 0, L, S, PRICE, 2, up=False)
        self.assertAlmostEqual(end / S, 0.98 / (1 - FEE), places=9)
        usdt, wgnk = _amounts_down({}, 0, L, S, end)
        self.assertAlmostEqual(wgnk, L * Q96 * (S - end) / (S * end), places=-3)
        self.assertAlmostEqual(usdt * 1e3 / wgnk * (1 - FEE), PRICE * 0.98, places=4)

    def test_wall_beyond_solution_does_not_distort_it(self):
        # Extra liquidity past the solved endpoint is inside the search
        # range but must not move the solution (no double counting).
        end = _solve_avg({10: L}, 0, L, S, PRICE, 2, up=True)
        self.assertAlmostEqual(end / S, 1.02 * (1 - FEE), places=9)
        end = _solve_avg({-10: L}, 0, L, S, PRICE, 2, up=False)
        self.assertAlmostEqual(end / S, 0.98 / (1 - FEE), places=9)

    def test_thin_range_is_reported_unreachable(self):
        # Liquidity stops one slot away: the average can never reach +2%.
        self.assertIsNone(_solve_avg({1: -L}, 0, L, S, PRICE, 2, up=True))
        self.assertIsNone(_solve_avg({-1: L}, 0, L, S, PRICE, 2, up=False))


if __name__ == "__main__":
    unittest.main()
