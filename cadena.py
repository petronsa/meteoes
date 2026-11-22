"""Espera al siguiente turno de una tarea de GitHub Actions y la vuelve a lanzar.

Uso (al final de la tarea, con GH_TOKEN y permiso «actions: write»):

  python cadena.py avisos.yml 7,37      siguiente ejecución en el minuto 7 o 37 de la hora

GitHub se salta muchas ejecuciones programadas: en este repo la tarea de cada
30 minutos se ejecutaba 3 o 4 veces al día. Para no depender de ellas, cada
ejecución espera a su siguiente turno y lanza la siguiente; la programación del
.yml queda de respaldo por si la cadena se corta. Cancelar una ejecución en
GitHub para la cadena (el paso no corre si la tarea se cancela).

Nunca hay más de una cadena: si ya hay otra ejecución esperando (por ejemplo, la
programada o la de un push), no se lanza otra.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

WAITING = {"queued", "pending", "waiting", "requested"}
MIN_WAIT = timedelta(minutes=1)


def next_slot(now: datetime, minutes: set[int], min_wait: timedelta = MIN_WAIT) -> datetime:
    """El siguiente minuto de la lista que esté al menos min_wait por delante."""
    t = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while t.minute not in minutes or t - now < min_wait:
        t += timedelta(minutes=1)
    return t


def others_waiting(runs: list[dict], me: int) -> bool:
    return any(r.get("databaseId") != me and r.get("status") in WAITING for r in runs)


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    workflow = sys.argv[1]
    minutes = {int(m) for m in sys.argv[2].split(",")}
    now = datetime.now(timezone.utc)
    slot = next_slot(now, minutes)
    print(f"Siguiente ejecución a las {slot:%H:%M} UTC")
    time.sleep((slot - now).total_seconds())
    runs = json.loads(_gh("run", "list", "--workflow", workflow, "--limit", "20", "--json", "databaseId,status"))
    if others_waiting(runs, int(os.environ.get("GITHUB_RUN_ID", "0"))):
        print("Ya hay otra ejecución esperando: no se lanza otra")
        return
    _gh("workflow", "run", workflow, "--ref", os.environ.get("GITHUB_REF_NAME") or "main")
    print("Lanzada")


if __name__ == "__main__":
    main()
