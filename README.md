# Caídas de Luz - Monitor de Averías e-distribución (Conil / Fuente del Gallo)

Sistema automático de ingeniería inversa, monitorización e histórico de cortes de suministro eléctrico de **e-distribución** (Grupo Enel en España), especialmente enfocado en **Fuente del Gallo (Conil de la Frontera, Cádiz)**.

---

## 🚀 Inicio Rápido

El sistema funciona con Python 3 sin necesidad de instalar librerías externas (usa exclusivamente paquetes de la librería estándar: `urllib`, `sqlite3`, `json`, `csv`, `datetime`, `math`).

### Comandos Principales

1. **Chequeo en tiempo real (Actualiza DB, CSV y Dashboard HTML):**
   ```bash
   python3 script.py
   ```

2. **Ver el historial en la consola:**
   ```bash
   python3 script.py history
   ```

3. **Generar/Actualizar el Dashboard HTML:**
   ```bash
   python3 script.py html
   open index.html
   ```

4. **Consultar cualquier otro municipio:**
   ```bash
   python3 script.py check "Cádiz"
   ```

---

## 📁 Archivos del Proyecto

| Archivo | Descripción |
|---|---|
| [`script.py`](file:///Users/jaime/workspace/caidas-luz/script.py) | Script principal de consulta, deduplicación, cálculo de retrasos y generación de informes. |
| [`index.html`](file:///Users/jaime/workspace/caidas-luz/index.html) | Dashboard visual e interactivo con métricas, gráficos de frecuencia (Chart.js) y control de aplazamientos. |
| [`averias.db`](file:///Users/jaime/workspace/caidas-luz/averias.db) | Base de datos SQLite3 con el historial unificado y trazabilidad completa de incidencias. |
| [`averias.csv`](file:///Users/jaime/workspace/caidas-luz/averias.csv) | Exportación CSV automática para abrir en Excel, Numbers o Google Sheets. |
| [`AGENTS.md`](file:///Users/jaime/workspace/caidas-luz/AGENTS.md) | Documentación técnica detallada para Agentes de Inteligencia Artificial (Codex, Claude, etc.). |

---

## 💡 Características Clave

* **Ingeniería Inversa a ArcGIS Enterprise:** Consulta directa a los endpoints REST de e-distribución sin necesidad de scraping con headless browser.
* **Control de Prórrogas / Aplazamientos:** Rastrea cuándo e-distribución pospone la hora estimada de reparación (`reposition_date`) y contabiliza cuántas veces ha retrasado la resolución (`delay_count`).
* **Prevención de Falsas Resoluciones:** Evita cerrar incidencias ante micro-caídas del servidor de ArcGIS requiriendo 3 chequeos ausentes consecutivos (~30-45 mins) para dar por resuelta una avería.
* **Geofencing para Fuente del Gallo:** Clasifica y resalta automáticamente los cortes que ocurren dentro de las coordenadas de la Urbanización Fuente del Gallo ($36.285^\circ\text{N} \le \text{Lat} \le 36.305^\circ\text{N}$, $-6.120^\circ\text{W} \le \text{Lon} \le -6.100^\circ\text{W}$).

---

## ⏰ Programación Automática (Cron en macOS / Linux)

Para mantener el histórico actualizado cada 10 minutos automáticamente, puedes añadir una tarea a `crontab`:

```bash
crontab -e
```

Añade la siguiente línea:
```cron
*/10 * * * * cd /Users/jaime/workspace/caidas-luz && /usr/bin/python3 script.py > /tmp/caidas_luz_cron.log 2>&1
```
