"""Auditoría de MeteoES: lo que mostró la app frente a lo que midieron las estaciones de AEMET.

Uso (DATOS es la carpeta de la rama «auditoria», donde se guarda todo):

  python auditoria.py foto DATOS                        cada hora: guarda lo que muestra la app
  AEMET_API_KEY=... python auditoria.py medir DATOS     cada hora: guarda lo medido en las estaciones
  python auditoria.py resumir DATOS                     rehace los resultados de la web (DATOS/web)
  AEMET_API_KEY=... python auditoria.py historico DATOS una vez: los últimos 12 meses, por días

La previsión se pide igual que la pide la app (Open-Meteo, modelo por defecto,
coordenadas con dos decimales) en la posición de cada estación de AEMET de
auditoria_lugares.json. Todas las horas van en UTC; cada valor horario es el de
la hora que termina en la etiqueta (las 18 son de 17:00 a 18:00), igual en
Open-Meteo y en AEMET.

Carpetas dentro de DATOS:
  fotos/AAAA-MM-DD.json       por estación y hora: {antelación en horas: [mm, %, viento, racha, temp.]}
  fotos_dias/AAAA-MM-DD.json  por estación: {antelación en días: [máx., mín., mm, %]}
  medidas/AAAA-MM-DD.json     por estación y hora: [mm, viento, racha, temp., máx. de la hora, mín. de la hora]
  historico.json              filas diarias de los últimos 12 meses (previsiones antiguas)
  web/                        lo que se publica en la página (resumen.json y dias/)
Los archivos van por fecha de la Península; el viento siempre en km/h.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FORECAST = "https://api.open-meteo.com/v1/forecast"
PREVIOUS = "https://previous-runs-api.open-meteo.com/v1/forecast"
AEMET_OBS = "https://opendata.aemet.es/opendata/api/observacion/convencional/todas"
AEMET_DAILY = ("https://opendata.aemet.es/opendata/api/valores/climatologicos/diarios/datos/"
               "fechaini/{ini}T00:00:00UTC/fechafin/{fin}T23:59:59UTC/estacion/{id}")
PLACES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auditoria_lugares.json")
MADRID = ZoneInfo("Europe/Madrid")

HOURLY = ("precipitation", "precipitation_probability", "wind_speed_10m", "wind_gusts_10m", "temperature_2m")
LEADS_H = (0, 1, 3, 6, 12, 24)   # horas de antelación que se guardan
LEADS_D = (1, 2, 3)              # días de antelación
# La foto de los próximos días se toma en la primera ejecución desde las 6 UTC (7 u 8 de la Península).
DAILY_FROM_UTC = 6
RAIN_H = 0.2   # mm en una hora para decir que llovió
RAIN_D = 1.0   # mm en un día
KMH = 3.6
RECENT_DAYS = 3        # días que se rehacen en cada resumen (por si llegan datos tarde)
HIST_DAYS = 365
AEMET_CHUNK_DAYS = 180


# ---------- utilidades ----------

def load_places(path: str = PLACES) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str, doc) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def hour_key(t: datetime) -> str:
    """Clave de una hora UTC: '2026-11-21T18'."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def parse_hour(key: str) -> datetime:
    return datetime.strptime(key[:13], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def file_date(t: datetime) -> str:
    """Fecha de la Península de una hora: da nombre a los archivos."""
    return t.astimezone(MADRID).date().isoformat()


def r1(v):
    return None if v is None else round(float(v), 1)


def _get(url: str, headers: dict | None = None, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "meteoes-auditoria", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _decode(data: bytes):
    # AEMET sirve los datos en ISO-8859-15 aunque a veces llegan en UTF-8.
    try:
        return json.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        return json.loads(data.decode("iso-8859-15"))


def open_meteo(url: str, params: dict) -> list[dict]:
    """Pide a Open-Meteo; con varias posiciones devuelve una lista (una por posición)."""
    error = None
    for i in range(3):
        try:
            doc = json.loads(_get(url + "?" + urllib.parse.urlencode(params, safe=",")))
            if isinstance(doc, dict) and doc.get("error"):
                raise RuntimeError(doc.get("reason"))
            return doc if isinstance(doc, list) else [doc]
        except Exception as e:  # noqa: BLE001
            error = e
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"Open-Meteo: {error}")


def aemet(url: str, key: str, attempts: int = 4):
    """Dos pasos de AEMET: la petición devuelve la dirección de los datos. Si no hay datos, None."""
    error = None
    for i in range(attempts):
        try:
            meta = _decode(_get(url, {"api_key": key}))
            if meta.get("estado") == 404:
                return None
            if meta.get("estado") == 429:
                raise RuntimeError("demasiadas peticiones")
            if meta.get("estado") != 200:
                raise RuntimeError(f"{meta.get('estado')} {meta.get('descripcion')}")
            return _decode(_get(meta["datos"]))
        except Exception as e:  # noqa: BLE001
            error = e
            time.sleep(20 * (i + 1))
    raise RuntimeError(f"AEMET: {error}")


def coord(v: float) -> str:
    # Como la app: dos decimales.
    return f"{v:.2f}"


# ---------- foto: lo que muestra la app ----------

def fetch_forecast(places: list[dict]) -> list[dict]:
    return open_meteo(FORECAST, {
        "latitude": ",".join(coord(p["lat"]) for p in places),
        "longitude": ",".join(coord(p["lon"]) for p in places),
        "hourly": ",".join(HOURLY),
        "timezone": "GMT",
        "forecast_days": 5,
    })


def take_snapshot(datos: str, now: datetime, places: list[dict], responses: list[dict]) -> int:
    """Guarda la previsión de la hora en curso y las siguientes y, una vez al día, la de los próximos días."""
    base = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    files: dict[str, dict] = {}
    days: dict[str, dict] = {}
    saved = 0
    for p, r in zip(places, responses):
        h = r.get("hourly") or {}
        times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h.get("time", [])]
        idx = {t: i for i, t in enumerate(times)}

        def vals(i):
            return [r1(h.get(v, [None] * len(times))[i]) for v in HOURLY]

        for lead in LEADS_H:
            t = base + timedelta(hours=lead)
            i = idx.get(t)
            if i is None:
                continue
            v = vals(i)
            if all(x is None for x in v):
                continue
            name = file_date(t)
            doc = files.setdefault(name, read_json(os.path.join(datos, "fotos", name + ".json"), {}))
            # setdefault: si la tarea se repite en la misma hora, vale la primera foto.
            doc.setdefault(p["id"], {}).setdefault(hour_key(t), {}).setdefault(str(lead), v)
            saved += 1

        if base.hour < DAILY_FROM_UTC:
            continue
        zone = ZoneInfo(p["zona"])
        today = base.astimezone(zone).date()
        for lead in LEADS_D:
            target = today + timedelta(days=lead)
            rows = [vals(i) for i, t in enumerate(times) if t.astimezone(zone).date() == target]
            if len(rows) < 24 or any(x is None for row in rows for x in (row[0], row[4])):
                continue
            name = target.isoformat()
            doc = days.setdefault(name, read_json(os.path.join(datos, "fotos_dias", name + ".json"), {}))
            probs = [row[1] for row in rows if row[1] is not None]
            doc.setdefault(p["id"], {}).setdefault(str(lead), [
                max(row[4] for row in rows),
                min(row[4] for row in rows),
                r1(sum(row[0] for row in rows)),
                max(probs) if probs else None,
            ])
    for name, doc in files.items():
        write_json(os.path.join(datos, "fotos", name + ".json"), doc)
    for name, doc in days.items():
        write_json(os.path.join(datos, "fotos_dias", name + ".json"), doc)
    return saved


# ---------- medir: lo que midieron las estaciones ----------

def parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("+0000", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def kmh(v):
    return None if v is None else round(float(v) * KMH, 1)


def store_measurements(datos: str, places: list[dict], observations: list[dict]) -> int:
    """Guarda las observaciones horarias de nuestras estaciones (AEMET da las últimas 24 horas)."""
    ids = {p["id"] for p in places}
    files: dict[str, dict] = {}
    saved = 0
    for o in observations:
        try:
            ident = o["idema"]
            if ident not in ids:
                continue
            t = parse_time(o["fint"])
            if t.minute != 0:
                continue
            v = [r1(o.get("prec")), kmh(o.get("vv")), kmh(o.get("vmax")),
                 r1(o.get("ta")), r1(o.get("tamax")), r1(o.get("tamin"))]
        except (KeyError, TypeError, ValueError):
            continue
        if v[0] is not None and v[0] < 0:
            v[0] = None
        if all(x is None for x in v):
            continue
        name = file_date(t)
        doc = files.setdefault(name, read_json(os.path.join(datos, "medidas", name + ".json"), {}))
        doc.setdefault(ident, {})[hour_key(t)] = v
        saved += 1
    for name, doc in files.items():
        write_json(os.path.join(datos, "medidas", name + ".json"), doc)
    return saved


# ---------- resumir: resultados para la web ----------

def build_day(datos: str, day: str, places: list[dict]) -> dict | None:
    """Lo previsto y lo medido de un día (fecha de la Península), hora a hora y en total."""
    d = date.fromisoformat(day)
    near = [(d + timedelta(days=k)).isoformat() for k in (-1, 0, 1)]
    fotos = read_json(os.path.join(datos, "fotos", day + ".json"), {})
    medidas = {}
    for name in near:
        for ident, hours in read_json(os.path.join(datos, "medidas", name + ".json"), {}).items():
            medidas.setdefault(ident, {}).update(hours)
    fotos_dias = read_json(os.path.join(datos, "fotos_dias", day + ".json"), {})
    out = {}
    for p in places:
        ident = p["id"]
        m = medidas.get(ident, {})
        horas = []
        for key in sorted(fotos.get(ident, {})):
            mv = m.get(key)
            horas.append([key, fotos[ident][key], mv[:4] if mv else None])
        # El día completo en la hora local del lugar, con la misma regla que Open-Meteo (horas 00 a 23).
        zone = ZoneInfo(p["zona"])
        mine = [v for k, v in m.items() if parse_hour(k).astimezone(zone).date() == d]
        medido = None
        if len(mine) >= 22:
            tmax = [v[4] if v[4] is not None else v[3] for v in mine]
            tmin = [v[5] if v[5] is not None else v[3] for v in mine]
            rain = [v[0] for v in mine]
            medido = [
                max((x for x in tmax if x is not None), default=None),
                min((x for x in tmin if x is not None), default=None),
                r1(sum(rain)) if None not in rain and len(mine) == 24 else None,
            ]
        prev = fotos_dias.get(ident)
        if horas or prev:
            out[ident] = {"horas": horas, "dia": {"prev": prev or {}, "medido": medido}}
    return {"fecha": day, "lugares": out} if out else None


def _acc():
    return [0, 0.0, 0.0, 0, 0, 0, 0]   # n, suma |error|, suma error, aciertos, falsas alarmas, sorpresas, secos


def _add(acc, f, m, rain=None):
    if f is None or m is None:
        return
    e = f - m
    acc[0] += 1
    acc[1] += abs(e)
    acc[2] += e
    if rain is not None:
        pf, pm = f >= rain, m >= rain
        acc[3 if pf and pm else 4 if pf else 5 if pm else 6] += 1


def _out(acc, rain: bool):
    n = acc[0]
    base = [n, round(acc[1] / n, 2) if n else None, round(acc[2] / n, 2) if n else None]
    return base + acc[3:] if rain else base


def records(day_doc: dict):
    """(tipo, lugar, mes, antelación, variable, previsto, medido, umbral de lluvia)."""
    month = day_doc["fecha"][:7]
    for ident, lug in day_doc["lugares"].items():
        for _, prev, med in lug["horas"]:
            if not med:
                continue
            for lead, f in prev.items():
                yield "h", ident, month, lead, "lluvia", f[0], med[0], RAIN_H
                yield "h", ident, month, lead, "viento", f[2], med[1], None
                yield "h", ident, month, lead, "racha", f[3], med[2], None
                yield "h", ident, month, lead, "temp", f[4], med[3], None
        med = lug["dia"]["medido"]
        if med:
            for lead, f in lug["dia"]["prev"].items():
                yield "d", ident, month, lead, "tmax", f[0], med[0], None
                yield "d", ident, month, lead, "tmin", f[1], med[1], None
                yield "d", ident, month, lead, "lluvia", f[2], med[2], RAIN_D


HIST_COLS = ("tmax", "tmin", "lluvia", "viento", "racha")


def hist_records(rows: list[list]):
    for ident, day, lead, *vals in rows:
        f, m = vals[:5], vals[5:]
        for i, name in enumerate(HIST_COLS):
            yield "hist", ident, day[:7], str(lead), name, f[i], m[i], RAIN_D if name == "lluvia" else None


def aggregate(recs) -> dict:
    stats: dict = {}
    for kind, ident, month, lead, var, f, m, rain in recs:
        k = stats.setdefault(kind, {"total": {}, "lugares": {}, "meses": {}})
        for group in (k["total"], k["lugares"].setdefault(ident, {}), k["meses"].setdefault(month, {})):
            _add(group.setdefault(lead, {}).setdefault(var, [_acc(), rain is not None])[0], f, m, rain)

    def finish(node):
        if isinstance(node, list):
            return _out(*node)
        return {k: finish(v) for k, v in node.items()}

    return finish(stats)


def summarize(datos: str, now: datetime, places: list[dict]) -> dict:
    web = os.path.join(datos, "web")
    today = now.astimezone(MADRID).date()
    for k in range(RECENT_DAYS):
        day = (today - timedelta(days=k)).isoformat()
        doc = build_day(datos, day, places)
        if doc:
            write_json(os.path.join(web, "dias", day + ".json"), doc)
    dias_dir = os.path.join(web, "dias")
    days = sorted(n[:-5] for n in os.listdir(dias_dir) if n.endswith(".json")) if os.path.isdir(dias_dir) else []

    def all_records():
        for day in days:
            # El día de hoy aún no ha terminado: se ve en su página, pero no cuenta en el resumen.
            if day >= today.isoformat():
                continue
            yield from records(read_json(os.path.join(dias_dir, day + ".json"), {"fecha": day, "lugares": {}}))
        hist = read_json(os.path.join(datos, "historico.json"), {})
        yield from hist_records(hist.get("filas", []))

    hist = read_json(os.path.join(datos, "historico.json"), {})
    resumen = {
        "updated": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "fuentes": "Previsión: Open-Meteo.com (CC BY 4.0). Medidas: © AEMET. Agencia Estatal de Meteorología.",
        "lugares": [{"id": p["id"], "nombre": p["nombre"], "zona": p["zona"]} for p in places],
        "dias": days,
        "historico": {"desde": hist.get("desde"), "hasta": hist.get("hasta")},
        "umbrales": {"lluviaHora": RAIN_H, "lluviaDia": RAIN_D},
        "stats": aggregate(all_records()),
    }
    write_json(os.path.join(web, "resumen.json"), resumen)
    return resumen


# ---------- histórico: últimos 12 meses con las previsiones antiguas ----------

PREV_VARS = ("temperature_2m", "precipitation", "wind_speed_10m", "wind_gusts_10m")


def fetch_previous(place: dict, start: date, end: date) -> dict:
    hourly = ",".join(f"{v}_previous_day{n}" for v in PREV_VARS for n in LEADS_D)
    return open_meteo(PREVIOUS, {
        "latitude": coord(place["lat"]), "longitude": coord(place["lon"]),
        "hourly": hourly, "timezone": "GMT",
        "start_date": start.isoformat(), "end_date": (end + timedelta(days=1)).isoformat(),
    })[0]


def previous_daily(doc: dict) -> dict[tuple[str, int], list]:
    """Resume por días UTC, como los valores diarios de AEMET.

    Temperatura máxima, mínima y viento medio de las horas 00 a 23; racha máxima de
    las horas 01 a 24; lluvia de 07 a 07 del día siguiente (el día pluviométrico de AEMET).
    """
    h = doc.get("hourly") or {}
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h.get("time", [])]
    idx = {t: i for i, t in enumerate(times)}
    out = {}
    days = sorted({t.date() for t in times})
    for d in days:
        start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        for lead in LEADS_D:
            def series(var, first, count):
                col = h.get(f"{var}_previous_day{lead}")
                if not col:
                    return None
                vals = []
                for k in range(first, first + count):
                    i = idx.get(start + timedelta(hours=k))
                    if i is None or col[i] is None:
                        return None
                    vals.append(col[i])
                return vals
            temp = series("temperature_2m", 0, 24)
            wind = series("wind_speed_10m", 0, 24)
            gust = series("wind_gusts_10m", 1, 24)
            rain = series("precipitation", 8, 24)
            out[(d.isoformat(), lead)] = [
                r1(max(temp)) if temp else None,
                r1(min(temp)) if temp else None,
                r1(sum(rain)) if rain else None,
                r1(sum(wind) / 24) if wind else None,
                r1(max(gust)) if gust else None,
            ]
    return out


def aemet_number(v, factor: float = 1.0):
    """'12,3' → 12.3; 'Ip' (inapreciable) → 0; vacío, 'Acum' o 'Varias' → None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    if s == "Ip":
        return 0.0
    try:
        return round(float(s) * factor, 1)
    except ValueError:
        return None


def aemet_daily(rows: list[dict]) -> dict[str, list]:
    out = {}
    for r in rows or []:
        try:
            out[r["fecha"]] = [aemet_number(r.get("tmax")), aemet_number(r.get("tmin")), aemet_number(r.get("prec")),
                               aemet_number(r.get("velmedia"), KMH), aemet_number(r.get("racha"), KMH)]
        except (KeyError, TypeError):
            continue
    return out


def build_history(datos: str, key: str, places: list[dict], today: date) -> dict:
    start, end = today - timedelta(days=HIST_DAYS), today - timedelta(days=1)
    filas = []
    for n, p in enumerate(places, 1):
        try:
            prev = previous_daily(fetch_previous(p, start, end))
        except Exception as e:  # noqa: BLE001
            print(f"{p['nombre']}: sin previsiones antiguas ({e})", file=sys.stderr)
            continue
        medido: dict[str, list] = {}
        a = start
        while a <= end:
            b = min(a + timedelta(days=AEMET_CHUNK_DAYS - 1), end)
            try:
                medido.update(aemet_daily(aemet(AEMET_DAILY.format(ini=a, fin=b, id=p["id"]), key)))
            except Exception as e:  # noqa: BLE001
                print(f"{p['nombre']} {a}..{b}: sin datos de AEMET ({e})", file=sys.stderr)
            time.sleep(3)   # AEMET limita las peticiones por minuto
            a = b + timedelta(days=1)
        for (day, lead), f in sorted(prev.items()):
            m = medido.get(day)
            if m and day <= end.isoformat() and any(x is not None for x in f):
                filas.append([p["id"], day, lead, *f, *m])
        print(f"[{n}/{len(places)}] {p['nombre']}: {sum(1 for x in filas if x[0] == p['id'])} filas")
    doc = {"desde": start.isoformat(), "hasta": end.isoformat(),
           "columnas": ["estacion", "fecha", "antelacion_dias",
                        *(f"prev_{c}" for c in HIST_COLS), *(f"medido_{c}" for c in HIST_COLS)],
           "filas": filas}
    write_json(os.path.join(datos, "historico.json"), doc)
    return doc


# ---------- programa ----------

def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in ("foto", "medir", "resumir", "historico"):
        sys.exit(__doc__)
    mode, datos = sys.argv[1], sys.argv[2]
    places = load_places()
    now = datetime.now(timezone.utc)
    if mode == "foto":
        print(f"{take_snapshot(datos, now, places, fetch_forecast(places))} previsiones horarias guardadas")
    elif mode in ("medir", "historico"):
        key = os.environ.get("AEMET_API_KEY")
        if not key:
            sys.exit("Falta AEMET_API_KEY")
        if mode == "medir":
            obs = aemet(AEMET_OBS, key)
            if not isinstance(obs, list):
                sys.exit("AEMET no ha dado observaciones")
            print(f"{store_measurements(datos, places, obs)} medidas horarias guardadas")
        else:
            doc = build_history(datos, key, places, now.date())
            print(f"Histórico {doc['desde']} a {doc['hasta']}: {len(doc['filas'])} filas")
    else:
        r = summarize(datos, now, places)
        print(f"Resumen con {len(r['dias'])} días -> {os.path.join(datos, 'web')}")


if __name__ == "__main__":
    main()
