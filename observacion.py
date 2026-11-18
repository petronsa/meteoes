"""Descarga la última observación de las estaciones de AEMET y publica la lluvia medida.

Uso:  AEMET_API_KEY=... python observacion.py site/observacion.json

Por cada estación se guarda su posición, la hora de su última observación y la
precipitación medida en los 60 minutos anteriores. La app busca la estación más
cercana y, si está lloviendo allí, lo muestra aunque el modelo no lo prevea.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://opendata.aemet.es/opendata/api/observacion/convencional/todas"
MAX_BYTES = 50 * 1024 * 1024
# Estaciones que llevan más de 3 horas sin dar datos no sirven para decir "ahora".
MAX_AGE = timedelta(hours=3)


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "meteoes-observacion", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(MAX_BYTES + 1)


def _decode(data: bytes) -> object:
    # AEMET sirve los datos en ISO-8859-15 aunque a veces llegan en UTF-8.
    try:
        return json.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        return json.loads(data.decode("iso-8859-15"))


def fetch(api_key: str, attempts: int = 4) -> list[dict]:
    """Devuelve la lista de observaciones. AEMET falla a menudo: se reintenta."""
    error = None
    for i in range(attempts):
        try:
            meta = _decode(_get(URL, {"api_key": api_key}))
            if meta.get("estado") != 200:
                raise RuntimeError(f"AEMET: {meta.get('estado')} {meta.get('descripcion')}")
            data = _get(meta["datos"])
            if len(data) > MAX_BYTES:
                raise RuntimeError("Respuesta demasiado grande")
            obs = _decode(data)
            if not isinstance(obs, list):
                raise RuntimeError("Formato inesperado")
            return obs
        except Exception as e:  # noqa: BLE001 — cualquier fallo se reintenta
            error = e
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"No se pudieron descargar las observaciones: {error}")


def parse_time(text: str) -> datetime:
    """'2026-11-18T09:00:00+0000' o sin zona (AEMET da la hora en UTC)."""
    t = datetime.fromisoformat(text.replace("+0000", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def latest(observations: list[dict], now: datetime) -> list[dict]:
    """La última observación con lluvia medida de cada estación, si es reciente."""
    best: dict[str, tuple[datetime, dict]] = {}
    for o in observations:
        try:
            ident = o["idema"]
            when = parse_time(o["fint"])
            lat, lon = float(o["lat"]), float(o["lon"])
            prec = o.get("prec")
            if prec is None:
                continue
            prec = float(prec)
        except (KeyError, TypeError, ValueError):
            continue
        if prec < 0 or not timedelta(0) <= now - when <= MAX_AGE:
            continue
        if ident not in best or when > best[ident][0]:
            best[ident] = (when, {
                "id": ident,
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "time": when.isoformat(timespec="minutes"),
                "prec": round(prec, 1),
            })
    return sorted((s for _, s in best.values()), key=lambda s: s["id"])


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "site/observacion.json"
    key = os.environ.get("AEMET_API_KEY")
    if not key:
        sys.exit("Falta AEMET_API_KEY")
    now = datetime.now(timezone.utc)
    stations = latest(fetch(key), now)
    doc = {
        "updated": now.isoformat(timespec="seconds"),
        "source": "AEMET. Agencia Estatal de Meteorología",
        "stations": stations,
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    raining = sum(1 for s in stations if s["prec"] > 0)
    print(f"{len(stations)} estaciones ({raining} con lluvia) -> {out}")


if __name__ == "__main__":
    main()
