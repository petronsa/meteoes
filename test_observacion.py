import unittest
from datetime import datetime, timezone

from observacion import latest, parse_time

NOW = datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)


def obs(idema="3469A", fint="2026-11-18T09:00:00+0000", prec=0.2, lat=39.47, lon=-6.34):
    return {"idema": idema, "fint": fint, "prec": prec, "lat": lat, "lon": lon, "ubi": "CÁCERES"}


class ObservacionTest(unittest.TestCase):
    def test_hora_con_y_sin_zona(self):
        self.assertEqual(parse_time("2026-11-18T09:00:00+0000"), datetime(2026, 11, 18, 9, tzinfo=timezone.utc))
        self.assertEqual(parse_time("2026-11-18T09:00:00"), datetime(2026, 11, 18, 9, tzinfo=timezone.utc))

    def test_se_queda_con_la_ultima_de_cada_estacion(self):
        s = latest([
            obs(fint="2026-11-18T08:00:00+0000", prec=0.0),
            obs(fint="2026-11-18T09:00:00+0000", prec=0.4),
            obs(idema="3526X", prec=0.0),
        ], NOW)
        self.assertEqual([x["id"] for x in s], ["3469A", "3526X"])
        self.assertEqual(s[0]["prec"], 0.4)
        self.assertEqual(s[0]["time"], "2026-11-18T09:00+00:00")

    def test_descarta_viejas_futuras_y_sin_lluvia_medida(self):
        s = latest([
            obs(idema="VIEJA", fint="2026-11-18T05:00:00+0000"),
            obs(idema="FUTURA", fint="2026-11-18T11:00:00+0000"),
            obs(idema="SINPREC", prec=None),
            obs(idema="NEGATIVA", prec=-1),
            {"idema": "ROTA", "fint": "x"},
        ], NOW)
        self.assertEqual(s, [])


if __name__ == "__main__":
    unittest.main()
