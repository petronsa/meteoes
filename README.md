# MeteoES · datos públicos

Web y datos de la app MeteoES, publicados con GitHub Pages:

- `avisos.json`: avisos meteorológicos vigentes (amarillo, naranja y rojo) de AEMET para toda España, con el polígono de cada zona. Se actualiza cada 30 minutos con la tarea de `.github/workflows/avisos.yml`.
- `mapa.json`: 24 horas de lluvia, nubes, viento, temperatura, calima y oleaje en una malla sobre la Península y Baleares (cada 0,6°) y Canarias (cada 0,5°), para el mapa animado de la app. Lo genera `mapa.py` cada 3 horas (el oleaje cada 6 y la calima cada 12) con datos de Open-Meteo (CC BY 4.0); entre medias se reutiliza el publicado.
- `privacidad.html`: política de privacidad de la app.

La clave de AEMET OpenData se guarda como secreto `AEMET_API_KEY` del repositorio y nunca se publica.

Fuente de los avisos: © AEMET. Agencia Estatal de Meteorología.

## Probar en local

```
python -m unittest -v test_avisos test_mapa
AEMET_API_KEY=... python avisos.py site/avisos.json
python mapa.py site/mapa.json
```
