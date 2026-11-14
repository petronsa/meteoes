import unittest
from datetime import datetime, timedelta, timezone

import mapa


class MapaTest(unittest.TestCase):

    def test_points_go_south_to_north_west_to_east(self):
        pts = mapa.points(-10.0, 35.0, 0.5, 3, 2)
        self.assertEqual(pts, [(35.0, -10.0), (35.0, -9.5), (35.0, -9.0), (35.5, -10.0), (35.5, -9.5), (35.5, -9.0)])

    def test_pack_is_hour_major_and_scaled(self):
        responses = [
            {"hourly": {"precipitation": [0.1, 1.25] + [0.0] * 22}},
            {"hourly": {"precipitation": [None, 2.0]}},
        ]
        flat = mapa.pack(responses, "precipitation", 10)
        self.assertEqual(len(flat), 2 * mapa.HOURS)
        self.assertEqual(flat[:4], [1, -999, 12, 20])
        # Horas que faltan: sin dato.
        self.assertEqual(flat[-1], -999)

    def test_reuses_a_recent_map(self):
        now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        stamp = lambda d: (now - d).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        self.assertEqual(mapa.decide(None, now), (True, {"dust", "waves"}))

        def h(n):
            return stamp(timedelta(hours=n))

        fresh = {"updated": h(1), "dustUpdated": h(1), "wavesUpdated": h(1)}
        self.assertEqual(mapa.decide(fresh, now), (False, set()))
        old = {"updated": h(3), "dustUpdated": h(3), "wavesUpdated": h(3)}
        self.assertEqual(mapa.decide(old, now), (True, set()))
        # Oleaje cada 6 horas y calima cada 12.
        self.assertEqual(mapa.decide({"updated": h(3), "dustUpdated": h(6), "wavesUpdated": h(6)}, now), (True, {"waves"}))
        self.assertEqual(mapa.decide({"updated": h(3), "dustUpdated": h(12), "wavesUpdated": h(3)}, now), (True, {"dust"}))

    def test_grids_stay_within_the_free_quota(self):
        total = sum(nx * ny for _, _, _, _, nx, ny in mapa.GRIDS)
        # Tiempo cada 3 h (8), oleaje cada 6 h (4) y calima cada 12 h (2) por punto y día.
        self.assertLess(total * (8 + 4 + 2), 10_000)


if __name__ == "__main__":
    unittest.main()
