# MeteoES · datos públicos

Web y datos de la app MeteoES, publicados con GitHub Pages:

- `avisos.json`: avisos meteorológicos vigentes (amarillo, naranja y rojo) de AEMET para toda España, con el polígono de cada zona. Se actualiza cada 30 minutos con la tarea de `.github/workflows/avisos.yml`.
- `mapa.json`: 24 horas de lluvia, nubes, viento, temperatura, calima y oleaje en una malla sobre la Península y Baleares (cada 0,6°) y Canarias (cada 0,5°), para el mapa animado de la app. Lo genera `mapa.py` cada 3 horas (el oleaje cada 6 y la calima cada 12) con datos de Open-Meteo (CC BY 4.0); entre medias se reutiliza el publicado.
- `privacidad.html`: política de privacidad de la app.
- `auditoria.html`: resumen de cada día con lo que mostró la app y lo que pasó de verdad en los sitios de `auditoria_lugares.json` (ahora Torrejoncillo, con la estación de Coria, y Cáceres). Cada hora `auditoria.py` (tarea `.github/workflows/auditoria.yml`) guarda la previsión que da la app para la hora en curso y las de dentro de 1, 3, 6, 12 y 24 horas (lluvia, viento, rachas y temperatura) y, una vez al día, la del día y los 3 siguientes; también guarda lo medido en la estación de AEMET de cada sitio y rehace los resultados. Los datos van en la rama `auditoria` y la tarea de los avisos publica su carpeta `web/` en `auditoria/`. Para añadir un sitio basta con ponerlo en `auditoria_lugares.json` con su estación de AEMET; uno de costa puede llevar también `"boya"` (una de `auditoria_boyas.json`, boyas de Puertos del Estado que publica Copernicus Marine sin necesidad de cuenta) para comparar las olas.

GitHub se salta muchas ejecuciones programadas (la tarea de cada 30 minutos llegó a correr 3 o 4 veces al día), así que cada tarea termina esperando a su siguiente turno y lanzándose otra vez con `cadena.py`; la programación queda de respaldo. Cancelar una ejecución en GitHub para la cadena hasta la siguiente programada.

La clave de AEMET OpenData se guarda como secreto `AEMET_API_KEY` del repositorio y nunca se publica.

Fuente de los avisos: © AEMET. Agencia Estatal de Meteorología.

## Probar en local

```
python -m unittest -v test_avisos test_mapa test_observacion test_salud test_auditoria
AEMET_API_KEY=... python avisos.py site/avisos.json
python mapa.py site/mapa.json
python auditoria.py foto datos && python auditoria.py resumir datos
```
