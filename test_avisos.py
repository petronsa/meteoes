import io
import tarfile
import unittest
from datetime import datetime, timezone

from avisos import parse

CAP = """<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>{id}</identifier>
  <msgType>{type}</msgType>
  {refs}
  <info>
    <language>es-ES</language>
    <eventCode><valueName>AEMET-Meteoalerta fenomeno</valueName><value>VI;Vientos</value></eventCode>
    <onset>2026-11-13T02:00:00+01:00</onset>
    <expires>{expires}</expires>
    <headline>Aviso de vientos de nivel {level}. Litoral</headline>
    <description>Rachas de 80 km/h.</description>
    <parameter><valueName>AEMET-Meteoalerta nivel</valueName><value>{level}</value></parameter>
    <area>
      <areaDesc>Litoral</areaDesc>
      <polygon>41.0,2.0 41.5,2.0 41.5,2.5 41.0,2.0</polygon>
      <geocode><valueName>AEMET-Meteoalerta zona</valueName><value>690801</value></geocode>
    </area>
  </info>
  <info>
    <language>en-GB</language>
    <parameter><valueName>AEMET-Meteoalerta nivel</valueName><value>{level}</value></parameter>
  </info>
</alert>"""

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
LATER = "2026-11-13T23:59:59+01:00"


def cap(id, level="amarillo", type="Alert", expires=LATER, replaces=None):
    refs = f"<references>http://www.aemet.es,{replaces},2026-11-13T07:00:00-00:00</references>" if replaces else ""
    return CAP.format(id=id, level=level, type=type, expires=expires, refs=refs)


def tar_of(*docs):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for i, doc in enumerate(docs):
            data = doc.encode()
            info = tarfile.TarInfo(f"aviso{i}.xml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class ParseTest(unittest.TestCase):
    def test_keeps_active_yellow_in_spanish(self):
        [a] = parse(tar_of(cap("A1")), NOW)
        self.assertEqual(a["event"], "VI")
        self.assertEqual(a["name"], "Vientos")
        self.assertEqual(a["level"], "amarillo")
        self.assertEqual(a["zone"], "690801")
        self.assertEqual(a["polygon"][1], [41.5, 2.0])

    def test_drops_green_and_expired(self):
        docs = tar_of(cap("A1", level="verde"), cap("A2", expires="2026-11-13T09:59:59+01:00"))
        self.assertEqual(parse(docs, NOW), [])

    def test_update_replaces_previous(self):
        docs = tar_of(cap("A1"), cap("A2", level="naranja", type="Update", replaces="A1"))
        [a] = parse(docs, NOW)
        self.assertEqual((a["id"], a["level"]), ("A2", "naranja"))

    def test_cancel_removes_alert(self):
        docs = tar_of(cap("A1"), cap("A2", type="Cancel", replaces="A1"))
        self.assertEqual(parse(docs, NOW), [])


if __name__ == "__main__":
    unittest.main()
