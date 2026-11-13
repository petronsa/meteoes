"""Descarga los avisos CAP de AEMET para toda España y publica un JSON pequeño.

Uso:  AEMET_API_KEY=... python avisos.py site/avisos.json

Se queda solo con los avisos amarillos, naranjas y rojos, en español, que no
han caducado y que ningún mensaje posterior ha sustituido. Cada aviso lleva
el polígono de su zona para que la app compruebe si la ubicación cae dentro.
"""

import io
import json
import os
import sys
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

URL = "https://opendata.aemet.es/opendata/api/avisos_cap/ultimoelaborado/area/esp"
NS = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
LEVELS = ("amarillo", "naranja", "rojo")
MAX_BYTES = 50 * 1024 * 1024


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "meteoes-avisos", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(MAX_BYTES + 1)


def fetch(api_key: str, attempts: int = 4) -> bytes:
    """Devuelve el tar con los CAP. AEMET falla a menudo: se reintenta."""
    error = None
    for i in range(attempts):
        try:
            meta = json.loads(_get(URL, {"api_key": api_key}))
            if meta.get("estado") != 200:
                raise RuntimeError(f"AEMET: {meta.get('estado')} {meta.get('descripcion')}")
            data = _get(meta["datos"])
            if len(data) > MAX_BYTES:
                raise RuntimeError("Respuesta demasiado grande")
            return data
        except Exception as e:  # noqa: BLE001 — cualquier fallo se reintenta
            error = e
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"No se pudieron descargar los avisos: {error}")


def _text(el, path):
    v = el.findtext(path, namespaces=NS)
    return v.strip() if v else None


def _polygon(text: str) -> list[list[float]]:
    points = []
    for pair in text.split():
        lat, lon = pair.split(",")
        points.append([float(lat), float(lon)])
    return points


def parse(tar_bytes: bytes, now: datetime) -> list[dict]:
    messages = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for m in tar.getmembers():
            if not m.isfile() or not m.name.endswith(".xml"):
                continue
            messages.append(ET.parse(tar.extractfile(m)).getroot())

    # Los mensajes de actualización o cancelación citan en <references> los que sustituyen.
    replaced = set()
    for root in messages:
        refs = _text(root, "c:references")
        for ref in (refs or "").split():
            parts = ref.split(",")
            if len(parts) >= 2:
                replaced.add(parts[1])

    alerts = []
    for root in messages:
        ident = _text(root, "c:identifier")
        if ident in replaced or _text(root, "c:msgType") not in ("Alert", "Update"):
            continue
        for info in root.findall("c:info", NS):
            if _text(info, "c:language") != "es-ES":
                continue
            params = {
                _text(p, "c:valueName"): _text(p, "c:value") for p in info.findall("c:parameter", NS)
            }
            level = params.get("AEMET-Meteoalerta nivel")
            if level not in LEVELS:
                continue
            expires = _text(info, "c:expires")
            if not expires or datetime.fromisoformat(expires) <= now:
                continue
            code, _, name = (_text(info, "c:eventCode/c:value") or "").partition(";")
            for area in info.findall("c:area", NS):
                poly = _text(area, "c:polygon")
                if not poly:
                    continue
                alerts.append({
                    "id": ident,
                    "event": code,
                    "name": name.rstrip(";"),
                    "level": level,
                    "onset": _text(info, "c:onset"),
                    "expires": expires,
                    "headline": _text(info, "c:headline"),
                    "description": _text(info, "c:description"),
                    "area": _text(area, "c:areaDesc"),
                    "zone": _text(area, "c:geocode/c:value"),
                    "polygon": _polygon(poly),
                })
    alerts.sort(key=lambda a: (a["zone"] or "", a["onset"] or ""))
    return alerts


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "site/avisos.json"
    key = os.environ.get("AEMET_API_KEY")
    if not key:
        sys.exit("Falta AEMET_API_KEY")
    now = datetime.now(timezone.utc)
    alerts = parse(fetch(key), now)
    doc = {
        "updated": now.isoformat(timespec="seconds"),
        "source": "AEMET. Agencia Estatal de Meteorología",
        "alerts": alerts,
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(alerts)} avisos -> {out}")


if __name__ == "__main__":
    main()
