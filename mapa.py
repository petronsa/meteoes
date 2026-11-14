"""Genera el mapa animado de MeteoES: 24 horas de lluvia, nubes, viento,
temperatura, calima y oleaje en una malla sobre la Península, Baleares y Canarias.

Uso:  python mapa.py site/mapa.json

Los datos son de Open-Meteo (CC BY 4.0). Como la tarea de los avisos publica
cada 30 minutos y el mapa solo cambia cada pocas horas, si el mapa publicado
es reciente se reutiliza en vez de volver a pedirlo: así se respeta el límite
de consultas gratuitas. Si falla la descarga, también se conserva el anterior.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PUBLISHED = "https://petronsa.github.io/meteoes/mapa.json"
FORECAST = "https://api.open-meteo.com/v1/forecast"
AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
HOURS = 24
BATCH = 100
# El tiempo se regenera cada 3 horas.
MAX_AGE = timedelta(hours=2, minutes=50)

# Capas que cambian despacio y se piden con menos frecuencia, para no pasar
# de las 10.000 consultas diarias gratuitas: (API, {campo: (variable, escala)}, edad máxima).
# Entre medias se reutilizan, con su hora de inicio ("<grupo>Start") para cuadrarlas.
SLOW = {
    "dust": (AIR, {"dust": ("dust", 1)}, timedelta(hours=11, minutes=50)),  # µg/m³
    "waves": (MARINE, {"wave": ("wave_height", 10), "wdir": ("wave_direction", 1)},  # dm y grados
              timedelta(hours=5, minutes=50)),
}

# Mallas: (nombre, lon0, lat0, paso, columnas, filas). La fila 0 es la más al sur.
# La principal cubre todo el recorte del mapa, Atlántico incluido.
GRIDS = [
    ("main", -13.5, 34.5, 0.6, 32, 18),     # -13.5..5.1 E, 34.5..44.7 N
    ("canarias", -18.5, 27.5, 0.5, 12, 5),  # -18.5..-13 E, 27.5..29.5 N
]

FIELDS = {
    # nombre en el JSON: (variable de Open-Meteo, factor de escala)
    "rain": ("precipitation", 10),       # décimas de mm
    "cloud": ("cloud_cover", 1),         # %
    "wind": ("wind_speed_10m", 1),       # km/h
    "dir": ("wind_direction_10m", 1),    # grados
    "temp": ("temperature_2m", 1),       # °C
    "day": ("is_day", 1),                # 0/1
}


def points(lon0: float, lat0: float, step: float, nx: int, ny: int) -> list[tuple[float, float]]:
    """Puntos (lat, lon) fila a fila, de sur a norte y de oeste a este."""
    return [(round(lat0 + y * step, 2), round(lon0 + x * step, 2)) for y in range(ny) for x in range(nx)]


def _get(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "meteoes-mapa"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read(20 * 1024 * 1024))


def fetch(base: str, pts: list[tuple[float, float]], variables: list[str], attempts: int = 3) -> list[dict]:
    """Datos horarios de todos los puntos, pedidos en tandas de BATCH."""
    out = []
    for i in range(0, len(pts), BATCH):
        chunk = pts[i:i + BATCH]
        query = urllib.parse.urlencode({
            "latitude": ",".join(str(p[0]) for p in chunk),
            "longitude": ",".join(str(p[1]) for p in chunk),
            "hourly": ",".join(variables),
            "forecast_hours": HOURS,
            "timezone": "GMT",
            "wind_speed_unit": "kmh",
        })
        error = None
        for a in range(attempts):
            try:
                data = _get(f"{base}?{query}")
                data = data if isinstance(data, list) else [data]
                if len(data) != len(chunk):
                    raise RuntimeError(f"Esperaba {len(chunk)} puntos y llegaron {len(data)}")
                out.extend(data)
                break
            except Exception as e:  # noqa: BLE001 — se reintenta cualquier fallo
                error = e
                time.sleep(5 * (a + 1))
        else:
            raise RuntimeError(f"Open-Meteo no respondió: {error}")
        time.sleep(1)  # sin prisa: no hace falta saturar el servicio
    return out


def pack(responses: list[dict], variable: str, scale: float) -> list[int]:
    """Una capa en una lista plana: hora a hora y, dentro, punto a punto."""
    series = []
    for r in responses:
        values = (r.get("hourly") or {}).get(variable) or []
        values = (values + [None] * HOURS)[:HOURS]
        series.append(values)
    flat = []
    for h in range(HOURS):
        for s in series:
            v = s[h]
            flat.append(-999 if v is None else round(v * scale))
    return flat


def start_time(responses: list[dict]) -> str:
    times = (responses[0].get("hourly") or {}).get("time") or []
    if not times:
        raise RuntimeError("Sin horas en la respuesta")
    return times[0] + "Z"


def build(previous: dict | None, now: datetime, due: set[str]) -> dict:
    """Mapa nuevo: el tiempo siempre; de las capas lentas, solo las de [due]."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    grids = []
    start = None
    for name, lon0, lat0, step, nx, ny in GRIDS:
        pts = points(lon0, lat0, step, nx, ny)
        weather = fetch(FORECAST, pts, [v for v, _ in FIELDS.values()])
        start = start or start_time(weather)
        grid = {"name": name, "lon0": lon0, "lat0": lat0, "step": step, "nx": nx, "ny": ny}
        for key, (variable, scale) in FIELDS.items():
            grid[key] = pack(weather, variable, scale)
        old = next((g for g in (previous or {}).get("grids", []) if g.get("name") == name), None)
        for group, (api, fields, _) in SLOW.items():
            if group in due:
                data = fetch(api, pts, [v for v, _ in fields.values()])
                for key, (variable, scale) in fields.items():
                    grid[key] = pack(data, variable, scale)
                grid[f"{group}Start"] = start
            elif old and all(k in old for k in fields):
                for key in fields:
                    grid[key] = old[key]
                grid[f"{group}Start"] = old.get(f"{group}Start", previous.get("start"))
        grids.append(grid)
    result = {
        "updated": stamp,
        "source": "Open-Meteo (CC BY 4.0)",
        "start": start,
        "hours": HOURS,
        "grids": grids,
    }
    for group in SLOW:
        result[f"{group}Updated"] = stamp if group in due else (previous or {}).get(f"{group}Updated")
    return result


def _age(stamp: str | None, now: datetime) -> timedelta:
    if not stamp:
        return timedelta.max
    return now - datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def decide(previous: dict | None, now: datetime) -> tuple[bool, set[str]]:
    """(regenerar el tiempo, capas lentas que toca renovar a la vez)."""
    weather = previous is None or _age(previous.get("updated"), now) > MAX_AGE
    if not weather:
        return False, set()
    due = {g for g, (_, _, age) in SLOW.items() if previous is None or _age(previous.get(f"{g}Updated"), now) > age}
    return True, due


def main(out: str) -> int:
    now = datetime.now(timezone.utc)
    try:
        previous = _get(PUBLISHED)
    except Exception:  # noqa: BLE001 — la primera vez no existe
        previous = None
    refresh, due = decide(previous, now)
    result = previous
    if refresh:
        try:
            result = build(previous, now, due)
        except Exception as e:  # noqa: BLE001
            print(f"No se pudo regenerar el mapa: {e}", file=sys.stderr)
    if result is None:
        print("Sin mapa que publicar", file=sys.stderr)
        return 0
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, separators=(",", ":"))
    print(f"Mapa {'nuevo' if result is not previous else 'reutilizado'}: {result['updated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "site/mapa.json"))
