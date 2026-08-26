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
   open docs/index.html
   ```

4. **Consultar cualquier otro municipio:**
   ```bash
   python3 script.py check "Cádiz"
   ```

---

## 📁 Archivos del Proyecto

| Archivo | Descripción |
|---|---|
| [`script.py`](script.py) | Script principal de consulta, deduplicación, cálculo de retrasos y generación de informes. |
| [`docs/index.html`](docs/index.html) | Dashboard visual e interactivo con métricas, gráficos de frecuencia (Chart.js) y control de aplazamientos. |
| [`averias.db`](averias.db) | Base de datos SQLite3 con el historial unificado y trazabilidad completa de incidencias. |
| [`averias.csv`](averias.csv) | Exportación CSV automática para abrir en Excel, Numbers o Google Sheets. |
| [`AGENTS.md`](AGENTS.md) | Documentación técnica detallada para Agentes de Inteligencia Artificial (Codex, Claude, etc.). |

---

## 💡 Características Clave

* **Ingeniería Inversa a ArcGIS Enterprise:** Consulta directa a los endpoints REST de e-distribución sin necesidad de scraping con headless browser.
* **Control de Prórrogas / Aplazamientos:** Rastrea cuándo e-distribución pospone la hora estimada de reparación (`reposition_date`) y contabiliza cuántas veces ha retrasado la resolución (`delay_count`).
* **Prevención de Falsas Resoluciones:** Evita cerrar incidencias ante micro-caídas del servidor de ArcGIS requiriendo 3 chequeos ausentes consecutivos (~30-45 mins) para dar por resuelta una avería.
* **Geofencing para Fuente del Gallo:** Clasifica y resalta automáticamente los cortes que ocurren dentro de las coordenadas de la Urbanización Fuente del Gallo ($36.285^\circ\text{N} \le \text{Lat} \le 36.305^\circ\text{N}$, $-6.120^\circ\text{W} \le \text{Lon} \le -6.100^\circ\text{W}$).

---

## ⏰ Programación Automática (GitHub Actions)

El workflow [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml) consulta la API cada hora, al minuto 17 UTC, actualiza SQLite, CSV y el dashboard, y guarda los cambios en el repositorio.

Para publicar el dashboard gratis con GitHub Pages, configura en **Settings → Pages**:

- **Source:** Deploy from a branch
- **Branch:** `main`
- **Folder:** `/docs`
