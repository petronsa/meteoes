import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from salud import problems, should_fail

NOW = datetime(2026, 11, 18, 12, 10, tzinfo=timezone.utc)


def iso(age: timedelta) -> str:
    return (NOW - age).isoformat(timespec="seconds")


def z(age: timedelta) -> str:
    return (NOW - age).strftime("%Y-%m-%dT%H:%M:%SZ")


class SaludTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.site = self.dir.name
        self.write(avisos=timedelta(minutes=30), obs=timedelta(minutes=30), mapa=timedelta(hours=2),
                   waves=timedelta(hours=5), dust=timedelta(hours=11))

    def tearDown(self):
        self.dir.cleanup()

    def write(self, avisos, obs, mapa, waves, dust):
        files = {
            "avisos.json": {"updated": iso(avisos), "alerts": []},
            "observacion.json": {"updated": iso(obs), "stations": []},
            "mapa.json": {"updated": z(mapa), "wavesUpdated": z(waves), "dustUpdated": z(dust)},
        }
        for name, doc in files.items():
            with open(os.path.join(self.site, name), "w", encoding="utf-8") as f:
                json.dump(doc, f)

    def test_todo_al_dia(self):
        found = problems(self.site, NOW)
        self.assertEqual(found, [])
        self.assertFalse(should_fail(found, NOW))

    def test_observaciones_recien_caducadas_fallan(self):
        self.write(timedelta(minutes=30), timedelta(hours=2, minutes=20), timedelta(hours=2), timedelta(hours=5), timedelta(hours=11))
        found = problems(self.site, NOW)
        self.assertEqual([f[0] for f in found], ["Lluvia de las estaciones de AEMET"])
        self.assertTrue(should_fail(found, NOW))

    def test_si_sigue_viejo_solo_avisa_una_vez_al_dia(self):
        self.write(timedelta(minutes=30), timedelta(hours=9), timedelta(hours=2), timedelta(hours=5), timedelta(hours=11))
        found = problems(self.site, NOW)
        self.assertFalse(should_fail(found, NOW))
        self.assertTrue(should_fail(found, NOW.replace(hour=7, minute=12)))

    def test_capa_lenta_del_mapa(self):
        self.write(timedelta(minutes=30), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=13, minutes=20), timedelta(hours=11))
        self.assertEqual([f[0] for f in problems(self.site, NOW)], ["Mapa: oleaje"])

    def test_archivo_que_falta(self):
        os.remove(os.path.join(self.site, "observacion.json"))
        found = problems(self.site, NOW)
        self.assertEqual(found[0][1], None)
        self.assertFalse(should_fail(found, NOW))
        self.assertTrue(should_fail(found, NOW.replace(hour=7, minute=12)))


if __name__ == "__main__":
    unittest.main()
