import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

from auditoria import (aemet_daily, aemet_number, aggregate, build_day, load_places, previous_daily, read_json,
                       records, store_measurements, summarize, take_snapshot)

PLACE = {"id": "3469A", "nombre": "Cáceres", "lat": 39.4714, "lon": -6.3389, "zona": "Europe/Madrid"}
# 21-11-2026 a las 18:10 UTC (19:10 en la Península).
NOW = datetime(2026, 11, 21, 18, 10, tzinfo=timezone.utc)


def forecast(start: datetime, hours: int, rain=0.0, wind=10.0, gust=20.0, temp=12.0):
    times = [start + timedelta(hours=k) for k in range(hours)]
    return {"hourly": {
        "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
        "precipitation": [rain] * hours,
        "precipitation_probability": [40] * hours,
        "wind_speed_10m": [wind] * hours,
        "wind_gusts_10m": [gust] * hours,
        "temperature_2m": [temp + (k % 24) / 2 for k in range(hours)],
    }}


def obs(t: datetime, prec=0.0, vv=2.0, vmax=5.0, ta=11.0):
    return {"idema": "3469A", "fint": t.strftime("%Y-%m-%dT%H:%M:%S+0000"), "prec": prec, "vv": vv,
            "vmax": vmax, "ta": ta, "tamax": ta + 0.5, "tamin": ta - 0.5}


class AuditoriaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.datos = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_lugares(self):
        places = load_places()
        self.assertEqual(len(places), 53)
        self.assertEqual(len({p["id"] for p in places}), 53)
        self.assertIn("3526X", {p["id"] for p in places})

    def test_foto_guarda_la_hora_en_curso_y_las_siguientes(self):
        start = datetime(2026, 11, 21, tzinfo=timezone.utc)
        take_snapshot(self.datos, NOW, [PLACE], [forecast(start, 120, rain=12.6)])
        fotos = read_json(os.path.join(self.datos, "fotos", "2026-11-21.json"), {})
        self.assertEqual(fotos["3469A"]["2026-11-21T18"]["0"][0], 12.6)
        self.assertIn("1", fotos["3469A"]["2026-11-21T19"])
        # Las 18 UTC de mañana son de mañana también en la Península.
        manana = read_json(os.path.join(self.datos, "fotos", "2026-11-22.json"), {})
        self.assertIn("24", manana["3469A"]["2026-11-22T18"])

    def test_si_se_repite_en_la_misma_hora_vale_la_primera(self):
        start = datetime(2026, 11, 21, tzinfo=timezone.utc)
        take_snapshot(self.datos, NOW, [PLACE], [forecast(start, 120, rain=1.0)])
        take_snapshot(self.datos, NOW + timedelta(minutes=20), [PLACE], [forecast(start, 120, rain=5.0)])
        fotos = read_json(os.path.join(self.datos, "fotos", "2026-11-21.json"), {})
        self.assertEqual(fotos["3469A"]["2026-11-21T18"]["0"][0], 1.0)

    def test_foto_de_los_proximos_dias(self):
        start = datetime(2026, 11, 21, tzinfo=timezone.utc)
        take_snapshot(self.datos, NOW, [PLACE], [forecast(start, 120, rain=0.5)])
        dia = read_json(os.path.join(self.datos, "fotos_dias", "2026-11-22.json"), {})
        tmax, tmin, mm, prob = dia["3469A"]["1"]
        self.assertEqual(mm, 12.0)  # 24 horas de 0,5 mm
        self.assertEqual(prob, 40)
        self.assertGreater(tmax, tmin)
        self.assertIn("3", read_json(os.path.join(self.datos, "fotos_dias", "2026-11-24.json"), {})["3469A"])

    def test_antes_de_las_6_utc_no_hay_foto_de_dias(self):
        start = datetime(2026, 11, 21, tzinfo=timezone.utc)
        take_snapshot(self.datos, NOW.replace(hour=4), [PLACE], [forecast(start, 120)])
        self.assertFalse(os.path.exists(os.path.join(self.datos, "fotos_dias")))

    def test_medidas_en_kmh_y_solo_nuestras_estaciones(self):
        t = datetime(2026, 11, 21, 18, tzinfo=timezone.utc)
        n = store_measurements(self.datos, [PLACE], [
            obs(t, prec=8.0, vv=5.0, vmax=10.0),
            {**obs(t), "idema": "OTRA"},
            obs(t.replace(minute=30)),
            {**obs(t - timedelta(hours=1)), "prec": -1},
        ])
        self.assertEqual(n, 2)
        m = read_json(os.path.join(self.datos, "medidas", "2026-11-21.json"), {})["3469A"]
        self.assertEqual(m["2026-11-21T18"][:4], [8.0, 18.0, 36.0, 11.0])
        self.assertIsNone(m["2026-11-21T17"][0])

    def test_dia_completo_y_resumen(self):
        day = date(2026, 11, 20)
        # Foto tomada el día antes, y medidas de las 24 horas del día (hora peninsular).
        take_snapshot(self.datos, datetime(2026, 11, 19, 7, 5, tzinfo=timezone.utc), [PLACE],
                      [forecast(datetime(2026, 11, 19, tzinfo=timezone.utc), 120, rain=0.5)])
        for k in range(24):
            t = datetime(2026, 11, 19, 23, tzinfo=timezone.utc) + timedelta(hours=k)  # 00 a 23 en Madrid
            take_snapshot(self.datos, t, [PLACE], [forecast(t.replace(hour=0) - timedelta(days=1), 120, rain=12.6)])
            store_measurements(self.datos, [PLACE], [obs(t, prec=8.0)])
        doc = build_day(self.datos, day.isoformat(), [PLACE])
        lug = doc["lugares"]["3469A"]
        fila = next(f for f in lug["horas"] if f[0] == "2026-11-20T18")
        self.assertEqual(fila[1]["0"][0], 12.6)
        self.assertEqual(fila[2][0], 8.0)
        self.assertEqual(lug["dia"]["medido"], [11.5, 10.5, 192.0])
        self.assertEqual(lug["dia"]["prev"]["1"][2], 12.0)

        stats = aggregate(records(doc))
        lluvia = stats["h"]["total"]["0"]["lluvia"]
        self.assertEqual(lluvia[:3], [24, 4.6, 4.6])   # 12,6 previstos frente a 8 medidos
        self.assertEqual(lluvia[3:], [24, 0, 0, 0])    # todo aciertos de "llueve"
        self.assertEqual(stats["d"]["total"]["1"]["lluvia"][2], -180.0)

        r = summarize(self.datos, datetime(2026, 11, 21, 10, tzinfo=timezone.utc), [PLACE])
        self.assertIn("2026-11-20", r["dias"])
        self.assertEqual(r["stats"]["h"]["lugares"]["3469A"]["0"]["lluvia"][0], 24)

    def test_numeros_de_aemet(self):
        self.assertEqual(aemet_number("12,3"), 12.3)
        self.assertEqual(aemet_number("Ip"), 0.0)
        self.assertIsNone(aemet_number("Acum"))
        self.assertIsNone(aemet_number(None))
        self.assertEqual(aemet_number("2,5", 3.6), 9.0)
        d = aemet_daily([{"fecha": "2026-01-01", "tmax": "14,0", "tmin": "3,2", "prec": "Ip", "velmedia": "1,0", "racha": "5,0"}])
        self.assertEqual(d["2026-01-01"], [14.0, 3.2, 0.0, 3.6, 18.0])

    def test_previsiones_antiguas_por_dias(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        times = [start + timedelta(hours=k) for k in range(72)]
        hourly = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times]}
        for n in (1, 2, 3):
            hourly[f"temperature_2m_previous_day{n}"] = [float(k % 24) for k in range(72)]
            hourly[f"precipitation_previous_day{n}"] = [1.0 if t.hour == 10 else 0.0 for t in times]
            hourly[f"wind_speed_10m_previous_day{n}"] = [12.0] * 72
            hourly[f"wind_gusts_10m_previous_day{n}"] = [float(k) for k in range(72)]
        d = previous_daily({"hourly": hourly})
        self.assertEqual(d[("2026-01-01", 1)], [23.0, 0.0, 1.0, 12.0, 24.0])
        self.assertEqual(d[("2026-01-03", 2)][:2], [23.0, 0.0])
        self.assertIsNone(d[("2026-01-03", 2)][2])   # falta el día siguiente para la lluvia de 07 a 07


if __name__ == "__main__":
    unittest.main()
