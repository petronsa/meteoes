import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from auditoria import (BUOYS, WAVES, aggregate, buoy_hours, build_day, load_places, read_json, records,
                       store_buoys, store_measurements, summarize, take_snapshot)

PLACE = {"id": "torrejoncillo", "nombre": "Torrejoncillo", "lat": 39.8967, "lon": -6.4683,
         "zona": "Europe/Madrid", "estacion": "3526X"}
COAST = {"id": "bilbao", "nombre": "Bilbao", "lat": 43.4, "lon": -3.13, "zona": "Europe/Madrid",
         "estacion": "1082", "boya": "Bilbao-coast-buoy"}
# 21-11-2026 a las 18:10 UTC (19:10 en la Península).
NOW = datetime(2026, 11, 21, 18, 10, tzinfo=timezone.utc)


def forecast(start: datetime, hours: int, rain=0.0, wind=10.0, gust=20.0, temp=12.0):
    times = [start + timedelta(hours=k) for k in range(hours)]
    return {"hourly": {
        "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
        "precipitation": [rain] * hours,
        "precipitation_probability": [40] * hours,
        "wind_speed_10m": [wind] * hours,
        "wind_gusts_10m": [gust + (k % 24) for k in range(hours)],
        "temperature_2m": [temp + (k % 24) / 2 for k in range(hours)],
    }}


def obs(t: datetime, prec=0.0, vv=2.0, vmax=5.0, ta=11.0, idema="3526X"):
    return {"idema": idema, "fint": t.strftime("%Y-%m-%dT%H:%M:%S+0000"), "prec": prec, "vv": vv,
            "vmax": vmax, "ta": ta, "tamax": ta + 0.5, "tamin": ta - 0.5}


class AuditoriaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.datos = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_sitios(self):
        places = load_places()
        self.assertEqual([p["id"] for p in places], ["torrejoncillo", "caceres"])
        self.assertTrue(all(p["estacion"] for p in places))
        buoys = {b["id"] for b in load_places(BUOYS)}
        self.assertTrue(all(p["boya"] in buoys for p in places if p.get("boya")))

    def test_foto_guarda_la_hora_en_curso_y_las_siguientes(self):
        start = datetime(2026, 11, 20, tzinfo=timezone.utc)
        take_snapshot(self.datos, NOW, [PLACE], [forecast(start, 144, rain=12.6)])
        fotos = read_json(os.path.join(self.datos, "fotos", "2026-11-21.json"), {})
        self.assertEqual(fotos["torrejoncillo"]["2026-11-21T18"]["0"][0], 12.6)
        self.assertIn("1", fotos["torrejoncillo"]["2026-11-21T19"])
        manana = read_json(os.path.join(self.datos, "fotos", "2026-11-22.json"), {})
        self.assertIn("24", manana["torrejoncillo"]["2026-11-22T18"])

    def test_si_se_repite_en_la_misma_hora_vale_la_primera(self):
        start = datetime(2026, 11, 20, tzinfo=timezone.utc)
        take_snapshot(self.datos, NOW, [PLACE], [forecast(start, 144, rain=1.0)])
        take_snapshot(self.datos, NOW + timedelta(minutes=20), [PLACE], [forecast(start, 144, rain=5.0)])
        fotos = read_json(os.path.join(self.datos, "fotos", "2026-11-21.json"), {})
        self.assertEqual(fotos["torrejoncillo"]["2026-11-21T18"]["0"][0], 1.0)

    def test_foto_de_los_dias_incluido_hoy(self):
        start = datetime(2026, 11, 20, tzinfo=timezone.utc)
        take_snapshot(self.datos, NOW, [PLACE], [forecast(start, 144, rain=0.5)])
        hoy = read_json(os.path.join(self.datos, "fotos_dias", "2026-11-21.json"), {})
        self.assertIn("0", hoy["torrejoncillo"])
        tmax, tmin, mm, prob, viento, racha = read_json(
            os.path.join(self.datos, "fotos_dias", "2026-11-22.json"), {})["torrejoncillo"]["1"]
        self.assertEqual((mm, prob, viento, racha), (12.0, 40, 10.0, 43.0))
        self.assertGreater(tmax, tmin)
        self.assertIn("3", read_json(os.path.join(self.datos, "fotos_dias", "2026-11-24.json"), {})["torrejoncillo"])

    def test_antes_de_las_6_utc_no_hay_foto_de_dias(self):
        start = datetime(2026, 11, 20, tzinfo=timezone.utc)
        take_snapshot(self.datos, NOW.replace(hour=4), [PLACE], [forecast(start, 144)])
        self.assertFalse(os.path.exists(os.path.join(self.datos, "fotos_dias")))

    def test_medidas_en_kmh_y_solo_nuestras_estaciones(self):
        t = datetime(2026, 11, 21, 18, tzinfo=timezone.utc)
        n = store_measurements(self.datos, {"3526X"}, [
            obs(t, prec=8.0, vv=5.0, vmax=10.0),
            obs(t, idema="OTRA"),
            obs(t.replace(minute=30)),
            {**obs(t - timedelta(hours=1)), "prec": -1},
        ])
        self.assertEqual(n, 2)
        m = read_json(os.path.join(self.datos, "medidas", "2026-11-21.json"), {})["3526X"]
        self.assertEqual(m["2026-11-21T18"][:4], [8.0, 18.0, 36.0, 11.0])
        self.assertIsNone(m["2026-11-21T17"][0])

    def test_dia_completo_y_resumen(self):
        # Foto tomada el día antes, y luego cada hora del día 20 (hora peninsular) con sus medidas.
        take_snapshot(self.datos, datetime(2026, 11, 19, 7, 5, tzinfo=timezone.utc), [PLACE],
                      [forecast(datetime(2026, 11, 18, tzinfo=timezone.utc), 144, rain=0.5)])
        for k in range(24):
            t = datetime(2026, 11, 19, 23, tzinfo=timezone.utc) + timedelta(hours=k)  # 00 a 23 en Madrid
            take_snapshot(self.datos, t, [PLACE], [forecast(datetime(2026, 11, 18, tzinfo=timezone.utc), 144, rain=12.6)])
            store_measurements(self.datos, {"3526X"}, [obs(t, prec=8.0, vmax=10.0 + k)])
        doc = build_day(self.datos, "2026-11-20", [PLACE])
        lug = doc["lugares"]["torrejoncillo"]
        fila = next(f for f in lug["horas"] if f[0] == "2026-11-20T18")
        self.assertEqual(fila[1]["0"][0], 12.6)
        self.assertEqual(fila[2][0], 8.0)
        self.assertEqual(lug["dia"]["medido"], [11.5, 10.5, 192.0, 7.2, 118.8])
        self.assertEqual(lug["dia"]["horasMedidas"], 24)
        self.assertEqual(lug["dia"]["prev"]["1"][2], 12.0)
        # La foto de las 7 UTC del mismo día 20 es la de «esa mañana».
        self.assertEqual(lug["dia"]["prev"]["0"][2], 302.4)

        stats = aggregate(records(doc))
        lluvia = stats["h"]["total"]["0"]["lluvia"]
        self.assertEqual(lluvia[:3], [24, 4.6, 4.6])   # 12,6 previstos frente a 8 medidos
        self.assertEqual(lluvia[3:], [24, 0, 0, 0])    # todo aciertos de «llueve»
        self.assertEqual(stats["d"]["total"]["1"]["lluvia"][2], -180.0)

        r = summarize(self.datos, datetime(2026, 11, 21, 10, tzinfo=timezone.utc), [PLACE])
        self.assertIn("2026-11-20", r["dias"])
        self.assertEqual(r["stats"]["h"]["lugares"]["torrejoncillo"]["0"]["lluvia"][0], 24)
        self.assertEqual(r["lugares"][0]["estacion"], "3526X")

    def test_dia_sin_todas_las_horas_no_da_lluvia_total(self):
        for k in range(20):
            t = datetime(2026, 11, 19, 23, tzinfo=timezone.utc) + timedelta(hours=k)
            store_measurements(self.datos, {"3526X"}, [obs(t, prec=1.0)])
        lug = build_day(self.datos, "2026-11-20", [PLACE])["lugares"]["torrejoncillo"]
        self.assertIsNone(lug["dia"]["medido"][2])
        self.assertEqual(lug["dia"]["horasMedidas"], 20)
        # Las horas medidas salen en la tabla aunque no se guardara su previsión.
        self.assertEqual(len(lug["horas"]), 20)
        self.assertEqual(lug["horas"][0][1], {})

    def test_medidas_de_boya_en_horas_en_punto(self):
        # Días desde 1950: 28083 es el 21-11-2026 a las 00:00 UTC.
        rows = [
            (28083 + 18 / 24, 2.71, 7.2, 318.0),
            (28083 + 18.5 / 24, 3.0, 7.0, 300.0),          # media hora: fuera
            (28083 + 19 / 24, None, None, None),           # sin medida: fuera
        ]
        self.assertEqual(buoy_hours(rows), {"2026-11-21T18": [2.71, 7.2, 318]})

    def test_olas_previstas_y_medidas(self):
        start = datetime(2026, 11, 21, tzinfo=timezone.utc)
        times = [start + timedelta(hours=k) for k in range(72)]
        marine = {"hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
                             "wave_height": [2.0] * 72, "wave_period": [8.0] * 72, "wave_direction": [310] * 72}}
        take_snapshot(self.datos, NOW, [COAST], [marine], WAVES, "fotos_olas")
        self.assertFalse(os.path.exists(os.path.join(self.datos, "fotos_dias")))
        store_buoys(self.datos, {"Bilbao-coast-buoy": {"2026-11-21T18": [2.7, 7.2, 318]}})
        doc = build_day(self.datos, "2026-11-21", [COAST])
        self.assertEqual(doc["lugares"]["bilbao"]["olas"][0], ["2026-11-21T18", {"0": [2.0, 8.0, 310.0]}, [2.7, 7.2, 318]])
        self.assertEqual(aggregate(records(doc))["o"]["total"]["0"]["ola"], [1, 0.7, -0.7])


if __name__ == "__main__":
    unittest.main()
