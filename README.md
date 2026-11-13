# MeteoES · datos públicos

Web y datos de la app MeteoES, publicados con GitHub Pages:

- `avisos.json`: avisos meteorológicos vigentes (amarillo, naranja y rojo) de AEMET para toda España, con el polígono de cada zona. Se actualiza cada 30 minutos con la tarea de `.github/workflows/avisos.yml`.
- `privacidad.html`: política de privacidad de la app.

La clave de AEMET OpenData se guarda como secreto `AEMET_API_KEY` del repositorio y nunca se publica.

Fuente de los avisos: © AEMET. Agencia Estatal de Meteorología.

## Probar en local

```
python -m unittest -v test_avisos
AEMET_API_KEY=... python avisos.py site/avisos.json
```
