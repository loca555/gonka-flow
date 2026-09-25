import unittest

from app.depth import _integrate

Q96 = 2 ** 96
L = 10 ** 13
S = Q96  # tick 0: sqrt price equals the X96 scale


class DepthIntegrationTests(unittest.TestCase):
    def band(self, level):
        return S * (1 + level / 100) ** .5, S * (1 - level / 100) ** .5

    def test_no_ticks_matches_constant_liquidity(self):
        up, dn = self.band(2)
        out = _integrate({}, 0, L, S, up, dn, 0.2)
        self.assertEqual(int(out["up_usdt_raw"]), int(L * (up - S) / Q96))

    def test_tick_beyond_band_is_not_double_counted(self):
        # A wall just outside the band must not double the in-band cost
        # (regression: the first boundary past the limit was counted twice).
        up, dn = self.band(2)
        far = {60: L}  # position above the +2% boundary
        out = _integrate(far, 0, L, S, up, dn, 0.2)
        self.assertEqual(int(out["up_usdt_raw"]), int(L * (up - S) / Q96))
        far_below = {-60: L}
        out = _integrate(far_below, 0, L, S, up, dn, 0.2)
        self.assertEqual(int(out["down_wgnk_raw"]), int(L * Q96 * (S - dn) / (S * dn)))

    def test_wall_inside_band_adds_its_segment(self):
        up, dn = self.band(2)
        inside = {1: L}  # just above the active position
        out = _integrate(inside, 0, L, S, up, dn, 0.2)
        self.assertGreater(int(out["up_usdt_raw"]), int(L * (up - S) / Q96))


if __name__ == "__main__":
    unittest.main()
