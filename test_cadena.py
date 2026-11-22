import unittest
from datetime import datetime, timezone

from cadena import next_slot, others_waiting


def at(h, m, s=0):
    return datetime(2026, 11, 22, h, m, s, tzinfo=timezone.utc)


class CadenaTest(unittest.TestCase):
    def test_siguiente_turno(self):
        self.assertEqual(next_slot(at(10, 3), {7, 37}), at(10, 7))
        self.assertEqual(next_slot(at(10, 9, 30), {7, 37}), at(10, 37))
        self.assertEqual(next_slot(at(10, 40), {7, 37}), at(11, 7))
        self.assertEqual(next_slot(at(23, 50), {12}), datetime(2026, 11, 23, 0, 12, tzinfo=timezone.utc))

    def test_al_menos_un_minuto_de_espera(self):
        # A las 10:06:30 el turno de las 10:07 está demasiado cerca: va al siguiente.
        self.assertEqual(next_slot(at(10, 6, 30), {7, 37}), at(10, 37))
        self.assertEqual(next_slot(at(10, 5, 50), {7, 37}), at(10, 7))

    def test_otra_ejecucion_esperando(self):
        runs = [{"databaseId": 1, "status": "in_progress"}, {"databaseId": 2, "status": "completed"}]
        self.assertFalse(others_waiting(runs, 1))
        self.assertTrue(others_waiting(runs + [{"databaseId": 3, "status": "pending"}], 1))
        self.assertTrue(others_waiting(runs + [{"databaseId": 3, "status": "queued"}], 1))
        self.assertFalse(others_waiting([{"databaseId": 1, "status": "queued"}], 1))


if __name__ == "__main__":
    unittest.main()
