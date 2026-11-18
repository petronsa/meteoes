"""Comprueba que lo publicado no se ha quedado viejo y, si es así, hace fallar la tarea.

Uso:  python salud.py site

El mapa y las observaciones no paran la publicación cuando fallan: se vuelve a
publicar lo anterior. Así los avisos siguen saliendo, pero nadie se entera del
fallo. Este paso va al final y, si algo lleva demasiado tiempo sin renovarse,
termina con error para que GitHub mande un correo.

Para no mandar un correo cada 30 minutos, solo falla cuando algo acaba de
quedarse viejo y, mientras siga así, una vez al día (en la ejecución de las 7 UTC).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

# (archivo, campo con la hora, antigüedad máxima, qué es). Unas dos o tres
# renovaciones fallidas seguidas antes de avisar: un fallo suelto no importa.
CHECKS = [
    ("avisos.json", "updated", timedelta(hours=2), "Avisos de AEMET"),
    ("observacion.json", "updated", timedelta(hours=2), "Lluvia de las estaciones de AEMET"),
    ("mapa.json", "updated", timedelta(hours=7), "Mapa: lluvia, nubes, viento y temperatura"),
    ("mapa.json", "wavesUpdated", timedelta(hours=13), "Mapa: oleaje"),
    ("mapa.json", "dustUpdated", timedelta(hours=25), "Mapa: calima"),
]
# Entre ejecuciones hay 30 minutos (a veces más, si GitHub se retrasa).
JUST_STALE = timedelta(minutes=40)
DAILY_HOUR = 7


def parse(stamp: str) -> datetime:
    t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def problems(site: str, now: datetime) -> list[tuple[str, timedelta | None, timedelta]]:
    """(qué, antigüedad o None si falta, límite) de lo que se ha quedado viejo."""
    found = []
    for name, field, limit, label in CHECKS:
        try:
            with open(os.path.join(site, name), encoding="utf-8") as f:
                age = now - parse(json.load(f)[field])
        except (OSError, ValueError, KeyError, TypeError):
            found.append((label, None, limit))
            continue
        if age > limit:
            found.append((label, age, limit))
    return found


def should_fail(found: list[tuple[str, timedelta | None, timedelta]], now: datetime) -> bool:
    """Falla al quedarse algo viejo y luego una vez al día, no en cada ejecución."""
    if not found:
        return False
    if now.hour == DAILY_HOUR and now.minute < 30:
        return True
    # Un archivo que falta no tiene hora: no se sabe si es nuevo, así que va con el aviso diario.
    return any(age is not None and age - limit < JUST_STALE for _, age, limit in found)


def _hours(d: timedelta) -> str:
    return f"{d.total_seconds() / 3600:.1f} h"


def main() -> int:
    site = sys.argv[1] if len(sys.argv) > 1 else "site"
    now = datetime.now(timezone.utc)
    found = problems(site, now)
    lines = ["Todo lo publicado está al día."] if not found else [
        f"- {label}: " + ("no se ha publicado" if age is None else f"sin renovar desde hace {_hours(age)} (límite {_hours(limit)})")
        for label, age, limit in found
    ]
    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Estado de los datos publicados\n\n" + report + "\n")
    if should_fail(found, now):
        print("Algo lleva demasiado tiempo sin actualizarse: revisa los pasos de esta ejecución.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
