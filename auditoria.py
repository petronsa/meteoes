"""Auditoría de MeteoES: lo que mostró la app en unos sitios elegidos frente a lo que pasó de verdad.

Uso (DATOS es la carpeta de la rama «auditoria», donde se guarda todo):

  python auditoria.py foto DATOS                        cada hora: guarda lo que muestra la app
  AEMET_API_KEY=... python auditoria.py medir DATOS     cada hora: guarda lo medido en las estaciones
  python auditoria.py boyas DATOS                       cada hora: olas medidas (solo si algún sitio tiene boya)
  python auditoria.py resumir DATOS                     rehace los resultados de la web (DATOS/web)

Los sitios están en auditoria_lugares.json: la previsión se pide como la pide la
app (Open-Meteo, modelo por defecto, coordenadas con dos decimales) en las
coordenadas del sitio, y se compara con la estación de AEMET indicada en
«estacion». Un sitio de costa puede llevar también «boya» (una de
auditoria_boyas.json, boyas de Puertos del Estado publicadas por Copernicus
Marine) para comparar las olas; leerlas necesita netCDF4.

Todas las horas van en UTC; cada valor horario es el de la hora que termina en la
etiqueta (las 18 son de 17:00 a 18:00), igual en Open-Meteo y en AEMET.

Carpetas dentro de DATOS (archivos por fecha de la Península):
  fotos/        por sitio y hora: {antelación en horas: [mm, %, viento, racha, temp.]}
  fotos_dias/   por sitio: {antelación en días: [máx., mín., mm, %, viento máx., racha máx.]}
  fotos_olas/   por sitio y hora: {antelación en horas: [altura, periodo, dirección]}
  medidas/      por estación y hora: [mm, viento, racha, temp., máx. de la hora, mín. de la hora]
  medidas_olas/ por boya y hora: [altura, periodo, dirección]
  web/          lo que se publica en la página (resumen.json y dias/)
El viento siempre en km/h.
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
MARINE = "https://marine-api.open-meteo.com/v1/marine"
AEMET_OBS = "https://opendata.aemet.es/opendata/api/observacion/convencional/todas"
BUOY_FILE = ("https://s3.waw3-1.cloudferro.com/mdl-native-03/native/INSITU_IBI_PHYBGCWAV_DISCRETE_MYNRT_013_033/"
             "cmems_obs-ins_ibi_phybgcwav_mynrt_na_irr_202311/latest/{day}/IR_TS_MO_{id}_{day}.nc")
HERE = os.path.dirname(os.path.abspath(__file__))
PLACES = os.path.join(HERE, "auditoria_lugares.json")
BUOYS = os.path.join(HERE, "auditoria_boyas.json")
MADRID = ZoneInfo("Europe/Madrid")

HOURLY = ("precipitation", "precipitation_probability", "wind_speed_10m", "wind_gusts_10m", "temperature_2m")
WAVES = ("wave_height", "wave_period", "wave_direction")
LEADS_H = (0, 1, 3, 6, 12, 24)   # horas de antelación que se guardan
LEADS_D = (0, 1, 2, 3)           # días de antelación (0: lo que decía esa misma mañana)
# La foto de los días se toma en la primera ejecución desde las 6 UTC (7 u 8 de la Península).
DAILY_FROM_UTC = 6
RAIN_H = 0.2   # mm en una hora para decir que llovió
RAIN_D = 1.0   # mm en un día
KMH = 3.6
RECENT_DAYS = 3        # días que se rehacen en cada resumen (por si llegan datos tarde)


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
    """Dos pasos de AEMET: la petición devuelve la dirección de los datos."""
    error = None
    for i in range(attempts):
        try:
            meta = _decode(_get(url, {"api_key": key}))
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
        "past_days": 1,   # como la app; así el día de hoy está completo desde las 00 de la Península
    })


def fetch_waves(places: list[dict]) -> list[dict]:
    """Oleaje como lo pide la app: el punto de mar más cercano al sitio."""
    return open_meteo(MARINE, {
        "latitude": ",".join(coord(p["lat"]) for p in places),
        "longitude": ",".join(coord(p["lon"]) for p in places),
        "hourly": ",".join(WAVES),
        "timezone": "GMT",
        "forecast_days": 3,
        "cell_selection": "sea",
    })


def take_snapshot(datos: str, now: datetime, places: list[dict], responses: list[dict],
                  variables: tuple = HOURLY, folder: str = "fotos") -> int:
    """Guarda la previsión de la hora en curso y las siguientes y, una vez al día, la de los días
    (esto último solo con la previsión del tiempo, no con la del oleaje)."""
    base = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    files: dict[str, dict] = {}
    days: dict[str, dict] = {}
    saved = 0
    for p, r in zip(places, responses):
        h = r.get("hourly") or {}
        times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h.get("time", [])]
        idx = {t: i for i, t in enumerate(times)}

        def vals(i):
            return [r1(h.get(v, [None] * len(times))[i]) for v in variables]

        for lead in LEADS_H:
            t = base + timedelta(hours=lead)
            i = idx.get(t)
            if i is None:
                continue
            v = vals(i)
            if all(x is None for x in v):
                continue
            name = file_date(t)
            doc = files.setdefault(name, read_json(os.path.join(datos, folder, name + ".json"), {}))
            # setdefault: si la tarea se repite en la misma hora, vale la primera foto.
            doc.setdefault(p["id"], {}).setdefault(hour_key(t), {}).setdefault(str(lead), v)
            saved += 1

        if base.hour < DAILY_FROM_UTC or variables is not HOURLY:
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

            def top(col):
                xs = [row[col] for row in rows if row[col] is not None]
                return max(xs) if xs else None

            doc.setdefault(p["id"], {}).setdefault(str(lead), [
                max(row[4] for row in rows),
                min(row[4] for row in rows),
                r1(sum(row[0] for row in rows)),
                top(1), top(2), top(3),
            ])
    for name, doc in files.items():
        write_json(os.path.join(datos, folder, name + ".json"), doc)
    for name, doc in days.items():
        write_json(os.path.join(datos, "fotos_dias", name + ".json"), doc)
    return saved


# ---------- medir: lo que midieron las estaciones ----------

def parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("+0000", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def kmh(v):
    return None if v is None else round(float(v) * KMH, 1)


def store_measurements(datos: str, stations: set[str], observations: list[dict]) -> int:
    """Guarda las observaciones horarias de nuestras estaciones (AEMET da las últimas 24 horas)."""
    files: dict[str, dict] = {}
    saved = 0
    for o in observations:
        try:
            ident = o["idema"]
            if ident not in stations:
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


# ---------- boyas: olas medidas ----------

def read_buoy_file(raw: bytes) -> list[tuple]:
    """(días desde 1950, altura, periodo medio, dirección) de cada medida de un archivo NetCDF de boya.

    Se queda con el primer valor válido de cada fila (la boya da uno por profundidad)
    y descarta los marcados como malos por el control de calidad (bueno: 0, 1 o 2).
    """
    import netCDF4  # solo hace falta en la tarea de GitHub

    with netCDF4.Dataset("boya.nc", memory=raw) as d:
        if not d["TIME"].units.startswith("days since 1950-01-01"):
            raise ValueError(f"Unidades de tiempo inesperadas: {d['TIME'].units}")
        times = [float(x) for x in d["TIME"][:]]

        def col(name):
            if name not in d.variables:
                return [None] * len(times)
            vals = d[name][:]
            qc = d[name + "_QC"][:] if name + "_QC" in d.variables else None
            out = []
            for i in range(len(times)):
                row = vals[i] if vals.ndim > 1 else [vals[i]]
                flags = (qc[i] if qc.ndim > 1 else [qc[i]]) if qc is not None else [0] * len(row)
                good = [float(v) for v, f in zip(row, flags)
                        if not getattr(v, "mask", False) and (getattr(f, "mask", False) or int(f) in (0, 1, 2))]
                out.append(good[0] if good else None)
            return out

        return list(zip(times, col("VHM0"), col("VTM02"), col("VMDR")))


def buoy_hours(rows: list[tuple]) -> dict[str, list]:
    """Medidas en horas en punto: {hora: [altura, periodo, dirección]}."""
    base = datetime(1950, 1, 1, tzinfo=timezone.utc)
    out = {}
    for days, hs, tm, dr in rows:
        t = base + timedelta(days=days)
        t = t.replace(second=0, microsecond=0) + timedelta(minutes=1 if t.second >= 30 else 0)
        if t.minute != 0 or (hs is None and tm is None):
            continue
        out[hour_key(t)] = [None if hs is None else round(hs, 2), r1(tm), None if dr is None else round(dr)]
    return out


def store_buoys(datos: str, measured: dict[str, dict[str, list]]) -> int:
    files: dict[str, dict] = {}
    saved = 0
    for ident, hours in measured.items():
        for key, v in hours.items():
            name = file_date(parse_hour(key))
            doc = files.setdefault(name, read_json(os.path.join(datos, "medidas_olas", name + ".json"), {}))
            doc.setdefault(ident, {})[key] = v
            saved += 1
    for name, doc in files.items():
        write_json(os.path.join(datos, "medidas_olas", name + ".json"), doc)
    return saved


def fetch_buoys(buoy_ids: list[str], now: datetime) -> dict[str, dict[str, list]]:
    """Archivos de hoy y de ayer (UTC) de cada boya; una boya que falla no para las demás."""
    measured = {}
    days = [(now - timedelta(days=k)).strftime("%Y%m%d") for k in (1, 0)]
    for ident in buoy_ids:
        hours = {}
        for day in days:
            try:
                hours.update(buoy_hours(read_buoy_file(_get(BUOY_FILE.format(day=day, id=ident), timeout=60))))
            except Exception as e:  # noqa: BLE001
                print(f"Boya {ident} {day}: {e}", file=sys.stderr)
        if hours:
            measured[ident] = hours
    return measured


# ---------- resumir: resultados para la web ----------

def _max(xs):
    xs = [x for x in xs if x is not None]
    return max(xs) if xs else None


def _min(xs):
    xs = [x for x in xs if x is not None]
    return min(xs) if xs else None


def build_day(datos: str, day: str, places: list[dict]) -> dict | None:
    """Lo previsto y lo medido de un día (fecha de la Península), hora a hora y en total."""
    d = date.fromisoformat(day)
    fotos = read_json(os.path.join(datos, "fotos", day + ".json"), {})
    fotos_dias = read_json(os.path.join(datos, "fotos_dias", day + ".json"), {})
    fotos_olas = read_json(os.path.join(datos, "fotos_olas", day + ".json"), {})
    olas = read_json(os.path.join(datos, "medidas_olas", day + ".json"), {})
    medidas: dict[str, dict] = {}
    for name in ((d + timedelta(days=k)).isoformat() for k in (-1, 0, 1)):
        for ident, hours in read_json(os.path.join(datos, "medidas", name + ".json"), {}).items():
            medidas.setdefault(ident, {}).update(hours)
    out = {}
    for p in places:
        m = medidas.get(p["estacion"], {})
        horas = [[key, prev, (m.get(key) or [None] * 4)[:4] if key in m else None]
                 for key, prev in sorted(fotos.get(p["id"], {}).items())]
        # El día completo en la hora local del sitio, con la misma regla que Open-Meteo (horas 00 a 23).
        zone = ZoneInfo(p["zona"])
        mine = [v for k, v in m.items() if parse_hour(k).astimezone(zone).date() == d]
        medido = None
        if mine:
            rain = [v[0] for v in mine]
            medido = [
                _max(v[4] if v[4] is not None else v[3] for v in mine),
                _min(v[5] if v[5] is not None else v[3] for v in mine),
                r1(sum(rain)) if None not in rain and len(mine) == 24 else None,
                _max(v[1] for v in mine),
                _max(v[2] for v in mine),
            ]
        lug = {"horas": horas, "dia": {"prev": fotos_dias.get(p["id"], {}), "medido": medido,
                                       "horasMedidas": len(mine)}}
        if p.get("boya"):
            mo = olas.get(p["boya"], {})
            lug["olas"] = [[key, prev, mo.get(key)] for key, prev in sorted(fotos_olas.get(p["id"], {}).items())]
        if horas or lug["dia"]["prev"] or mine or lug.get("olas"):
            out[p["id"]] = lug
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
    """(tipo, sitio, antelación, variable, previsto, medido, umbral de lluvia)."""
    for ident, lug in day_doc["lugares"].items():
        for _, prev, med in lug["horas"]:
            if not med:
                continue
            for lead, f in prev.items():
                yield "h", ident, lead, "lluvia", f[0], med[0], RAIN_H
                yield "h", ident, lead, "viento", f[2], med[1], None
                yield "h", ident, lead, "racha", f[3], med[2], None
                yield "h", ident, lead, "temp", f[4], med[3], None
        med = lug["dia"]["medido"]
        if med and lug["dia"]["horasMedidas"] >= 22:
            for lead, f in lug["dia"]["prev"].items():
                yield "d", ident, lead, "tmax", f[0], med[0], None
                yield "d", ident, lead, "tmin", f[1], med[1], None
                yield "d", ident, lead, "lluvia", f[2], med[2], RAIN_D
                yield "d", ident, lead, "viento", f[4], med[3], None
                yield "d", ident, lead, "racha", f[5], med[4], None
        for _, prev, med in lug.get("olas", []):
            if not med:
                continue
            for lead, f in prev.items():
                yield "o", ident, lead, "ola", f[0], med[0], None
                yield "o", ident, lead, "periodo", f[1], med[1], None


def aggregate(recs) -> dict:
    stats: dict = {}
    for kind, ident, lead, var, f, m, rain in recs:
        k = stats.setdefault(kind, {"total": {}, "lugares": {}})
        for group in (k["total"], k["lugares"].setdefault(ident, {})):
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
            if day < today.isoformat():
                yield from records(read_json(os.path.join(dias_dir, day + ".json"), {"fecha": day, "lugares": {}}))

    buoys = {b["id"]: b["nombre"] for b in load_places(BUOYS)}
    resumen = {
        "updated": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "lugares": [{"id": p["id"], "nombre": p["nombre"], "zona": p["zona"],
                     "estacion": p.get("estacionNombre") or p["estacion"],
                     "boya": buoys.get(p.get("boya"))} for p in places],
        "dias": days,
        "umbrales": {"lluviaHora": RAIN_H, "lluviaDia": RAIN_D},
        "stats": aggregate(all_records()),
    }
    write_json(os.path.join(web, "resumen.json"), resumen)
    return resumen


# ---------- programa ----------

def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in ("foto", "medir", "boyas", "resumir"):
        sys.exit(__doc__)
    mode, datos = sys.argv[1], sys.argv[2]
    places = load_places()
    coast = [p for p in places if p.get("boya")]
    now = datetime.now(timezone.utc)
    if mode == "foto":
        print(f"{take_snapshot(datos, now, places, fetch_forecast(places))} previsiones horarias guardadas")
        if coast:
            n = take_snapshot(datos, now, coast, fetch_waves(coast), WAVES, "fotos_olas")
            print(f"{n} previsiones de oleaje guardadas")
    elif mode == "medir":
        key = os.environ.get("AEMET_API_KEY")
        if not key:
            sys.exit("Falta AEMET_API_KEY")
        obs = aemet(AEMET_OBS, key)
        if not isinstance(obs, list):
            sys.exit("AEMET no ha dado observaciones")
        print(f"{store_measurements(datos, {p['estacion'] for p in places}, obs)} medidas horarias guardadas")
    elif mode == "boyas":
        if not coast:
            print("Ningún sitio tiene boya")
            return
        measured = fetch_buoys(sorted({p["boya"] for p in coast}), now)
        print(f"{store_buoys(datos, measured)} medidas de {len(measured)} boyas guardadas")
        if not measured:
            sys.exit("Ninguna boya ha dado datos")
    else:
        r = summarize(datos, now, places)
        print(f"Resumen con {len(r['dias'])} días -> {os.path.join(datos, 'web')}")


if __name__ == "__main__":
    main()
