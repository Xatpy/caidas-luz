import sqlite3
import csv
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
import os
import sys
import math
import html
import fcntl

# Directorios y rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "averias.db")
CSV_PATH = os.path.join(BASE_DIR, "averias.csv")
PAGES_DIR = os.path.join(BASE_DIR, "docs")
HTML_PATH = os.path.join(PAGES_DIR, "index.html")
LOCK_PATH = os.path.join(BASE_DIR, ".script.lock")

# Endpoint de la API de e-distribución (ArcGIS FeatureServer)
API_URL = "https://dpa-portalgis.enel.com/server/rest/services/Hosted/ESP_Prod_power_cut_View/FeatureServer/0/query"

# Umbral para considerar que 2 puntos de avería son la misma ubicación geográfica (~150 metros)
DISTANCE_THRESHOLD_KM = 0.15

# Número de chequeos fallidos consecutivos necesarios para dar por RESUELTA una avería (~30-45 mins)
MISSING_THRESHOLD_CHECKS = 3

def acquire_lock():
    """Garantiza que solo una instancia del script se ejecute a la vez."""
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except OSError:
        print("⚠️ Otra instancia del script se está ejecutando. Cancelando ejecución paralela.")
        sys.exit(0)

def haversine_km(lat1, lon1, lat2, lon2):
    """Calcula la distancia en kilómetros entre dos coordenadas GPS."""
    if lat1 == 0.0 or lon1 == 0.0 or lat2 == 0.0 or lon2 == 0.0:
        return 99999.0  # Coordenadas inválidas: evitar falso positivo por (0,0)
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def es_fuente_del_gallo(lat, lon):
    """Devuelve True si las coordenadas están en el rectángulo de Fuente del Gallo / Conil costa."""
    if lat == 0.0 or lon == 0.0:
        return False
    return (36.285 <= lat <= 36.305) and (-6.120 <= lon <= -6.100)

def init_db():
    """Inicializa la tabla SQLite y crea los índices de rendimiento necesesarios."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS averias_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cd_code TEXT,
            municipality TEXT,
            territory TEXT,
            service_type TEXT,
            affected_client INTEGER,
            interruption_date TEXT,
            initial_reposition_date TEXT,
            current_reposition_date TEXT,
            delay_count INTEGER DEFAULT 0,
            update_time TEXT,
            cause TEXT,
            latitude REAL,
            longitude REAL,
            first_seen TEXT,
            last_seen TEXT,
            resolved_at TEXT,
            missing_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)

    # Índices para búsquedas rápidas al crecer el histórico
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_averias_muni_status ON averias_v2 (municipality, status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_averias_cd_code ON averias_v2 (cd_code);")

    conn.commit()
    conn.close()

def consultar_api(municipio="Conil de la Frontera"):
    """Consulta la API pública de e-distribución para un municipio detectando errores HTTP 200 de ArcGIS."""
    params = {
        "where": f"municipality LIKE '%{municipio}%'",
        "outFields": "*",
        "f": "json",
        "returnGeometry": "true",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            
            # Arreglo crítico 1: Verificar si ArcGIS devolvió un JSON con error
            if "error" in data:
                print(f"⚠️ La API de ArcGIS devolvió una respuesta con error: {data['error']}")
                return None
                
            return data.get("features", [])
    except Exception as e:
        print(f"⚠️ Error al conectar con la API de e-distribución: {e}")
        return None  # Retornar None previene falsas ausencias

def registrar_averias(features, municipio="Conil de la Frontera"):
    """Registra y actualiza las averías identificando extensiones de horario y evitando falsas resoluciones."""
    if features is None:
        print("⚠️ No se pudo consultar la API. Se mantiene el estado previo sin hacer cambios.")
        return

    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Obtener todas las averías actualmente activas en la DB
    cursor.execute("""
        SELECT id, cd_code, interruption_date, initial_reposition_date, current_reposition_date, 
               delay_count, latitude, longitude, missing_count, status
        FROM averias_v2
        WHERE municipality LIKE ? AND status = 'ACTIVE'
    """, (f"%{municipio}%",))

    # Cargar en memoria y mantener lista dinámica para deduplicar también dentro de la misma respuesta de la API
    active_in_db = [list(row) for row in cursor.fetchall()]
    matched_db_ids = set()

    for feat in features:
        attr = feat.get("attributes", {})
        cd_code = str(attr.get("cd_code") or "DESCONOCIDO")
        interruption_date = attr.get("interruption_date") or ""
        reposition_date = attr.get("reposition_date") or ""
        affected_client = attr.get("affected_client") or 0
        update_time = attr.get("update_time") or ""
        cause = attr.get("des_cause_es") or ""
        latitude = float(attr.get("latitude") or 0.0)
        longitude = float(attr.get("longitude") or 0.0)
        municipality_val = attr.get("municipality") or municipio
        territory = attr.get("territory") or ""
        service_type = attr.get("service_type") or ""

        # Intentar enlazar con una avería existente (o recién añadida en este mismo ciclo)
        matched_row_idx = None
        for idx, db_row in enumerate(active_in_db):
            db_id, db_cd, db_start, db_init_repo, db_curr_repo, db_delays, db_lat, db_lon, db_missing, db_status = db_row
            
            # Coincidencia por código O por proximidad física (validando que las coordenadas no sean 0.0)
            is_same_code = (db_cd == cd_code and cd_code != "DESCONOCIDO")
            is_near = (latitude != 0.0 and db_lat != 0.0) and (haversine_km(latitude, longitude, db_lat, db_lon) < DISTANCE_THRESHOLD_KM)
            
            if is_same_code or is_near:
                matched_row_idx = idx
                matched_id = db_id
                
                # Comprobar si e-distribución ha cambiado/pospuesto la hora estimada de resolución
                new_delays = db_delays
                if reposition_date and db_curr_repo and (reposition_date != db_curr_repo):
                    new_delays += 1
                    print(f"  🚨 ¡APLAZAMIENTO DETECTADO! [{cd_code}] Hora estimada cambió de '{db_curr_repo}' a '{reposition_date}' (Aplazado {new_delays} vez/veces).")

                # Actualizar la avería en SQLite
                cursor.execute("""
                    UPDATE averias_v2 SET
                        cd_code = ?,
                        affected_client = ?,
                        current_reposition_date = ?,
                        delay_count = ?,
                        update_time = ?,
                        latitude = ?,
                        longitude = ?,
                        last_seen = ?,
                        missing_count = 0,
                        status = 'ACTIVE'
                    WHERE id = ?
                """, (cd_code, affected_client, reposition_date, new_delays, update_time, latitude, longitude, now_str, db_id))
                
                # Actualizar también el objeto en memoria para subsiguientes elementos de la misma llamada
                db_row[4] = reposition_date
                db_row[5] = new_delays
                db_row[6] = latitude
                db_row[7] = longitude
                db_row[8] = 0
                matched_db_ids.add(db_id)
                break

        # Si no coincidió con ninguna existente ni dentro de esta llamada, es una NUEVA avería
        if matched_row_idx is None:
            print(f"  ➕ Nueva avería detectada en API: [{cd_code}] {municipality_val} (Afectados: {affected_client})")
            cursor.execute("""
                INSERT INTO averias_v2 (
                    cd_code, municipality, territory, service_type, affected_client,
                    interruption_date, initial_reposition_date, current_reposition_date, delay_count,
                    update_time, cause, latitude, longitude, first_seen, last_seen, missing_count, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 0, 'ACTIVE')
            """, (
                cd_code, municipality_val, territory, service_type, affected_client,
                interruption_date, reposition_date, reposition_date, update_time, cause,
                latitude, longitude, now_str, now_str
            ))
            
            new_id = cursor.lastrowid
            # Insertar en memoria para evitar duplicados en la misma iteración
            active_in_db.append([
                new_id, cd_code, interruption_date, reposition_date, reposition_date,
                0, latitude, longitude, 0, 'ACTIVE'
            ])
            matched_db_ids.add(new_id)

    # Procesar averías en DB que no aparecieron en esta respuesta de la API
    for db_row in active_in_db:
        db_id = db_row[0]
        if db_id not in matched_db_ids:
            current_missing = db_row[8] + 1
            if current_missing >= MISSING_THRESHOLD_CHECKS:
                # Arreglo crítico 3: Guardar el momento actual (now_str) como fecha real de confirmación de resolución
                cursor.execute("""
                    UPDATE averias_v2 
                    SET status = 'RESOLVED', missing_count = ?, resolved_at = ?
                    WHERE id = ?
                """, (current_missing, now_str, db_id))
                print(f"  🟢 Avería ID {db_id} [{db_row[1]}] confirmada RESUELTA en {now_str} (tras {current_missing} ausencias).")
            else:
                cursor.execute("""
                    UPDATE averias_v2 
                    SET missing_count = ?
                    WHERE id = ?
                """, (current_missing, db_id))
                print(f"  ⏳ Avería ID {db_id} [{db_row[1]}] no aparece en API (Ausencia {current_missing}/{MISSING_THRESHOLD_CHECKS}). Manteniendo en seguimiento...")

    conn.commit()
    conn.close()

def exportar_csv():
    """Exporta todo el historial a CSV de manera atómica (mediante archivo temporal)."""
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, cd_code, municipality, territory, service_type, affected_client,
               interruption_date, initial_reposition_date, current_reposition_date, delay_count,
               update_time, cause, latitude, longitude, first_seen, last_seen, resolved_at, status
        FROM averias_v2
        ORDER BY first_seen DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    
    headers = [
        "ID", "Código Centro", "Municipio", "Territorio", "Tipo Servicio", "Clientes Afectados",
        "Fecha Inicio Corte", "Estimación Inicial", "Estimación Actual", "Veces Aplazado",
        "Última Act. API", "Causa", "Latitud", "Longitud", "Primera Detección DB", "Última Detección DB",
        "Resuelto En", "Estado"
    ]

    tmp_csv = CSV_PATH + ".tmp"
    with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    os.replace(tmp_csv, CSV_PATH)
    return len(rows)

def generar_html():
    """Genera el Dashboard HTML con prevención de XSS (escapado de HTML) y escritura atómica."""
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, cd_code, municipality, territory, service_type, affected_client,
               interruption_date, initial_reposition_date, current_reposition_date, delay_count,
               update_time, cause, latitude, longitude, first_seen, last_seen, resolved_at, status
        FROM averias_v2
        ORDER BY first_seen DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    total_incidencias = len(rows)
    activas_total = sum(1 for r in rows if r[17] == "ACTIVE")
    
    rows_fg = [r for r in rows if es_fuente_del_gallo(r[12], r[13])]
    total_fg = len(rows_fg)
    activas_fg = sum(1 for r in rows_fg if r[17] == "ACTIVE")
    max_afectados_fg = max([r[5] for r in rows_fg], default=0)

    # Conteo por fecha
    fechas_dict = {}
    for r in rows:
        f_str = r[6].split(" ")[0] if r[6] else r[14].split(" ")[0]
        fechas_dict[f_str] = fechas_dict.get(f_str, 0) + 1

    fechas_json = json.dumps([html.escape(k) for k in fechas_dict.keys()])
    conteo_json = json.dumps(list(fechas_dict.values()))

    tabla_rows_html = ""
    for r in rows:
        _id, cd, mun, terr, stype, aff, start, init_repo, curr_repo, delays, upd, cause, lat, lon, first, last, res, status = r
        is_fg = es_fuente_del_gallo(lat, lon)
        
        # Arreglo crítico 6: Escapar adecuadamente todos los textos contra XSS
        cd_e = html.escape(str(cd or ''))
        start_e = html.escape(str(start or ''))
        init_repo_e = html.escape(str(init_repo or ''))
        curr_repo_e = html.escape(str(curr_repo or ''))
        res_e = html.escape(str(res or 'En curso'))
        cause_e = html.escape(str(cause or ''))
        
        badge_status = '<span class="badge badge-active">🔴 EN CURSO</span>' if status == "ACTIVE" else '<span class="badge badge-resolved">🟢 RESUELTO</span>'
        badge_zona = '<span class="badge badge-fg">📍 Fuente del Gallo</span>' if is_fg else '<span class="badge badge-mun">Conil Centro/Otro</span>'
        
        if delays > 0:
            delay_info = f'<div class="delay-warning">⚠️ Pospuesto {delays} vez/veces<br><small>(Inicial: {init_repo_e} → Actual: {curr_repo_e})</small></div>'
        else:
            delay_info = f'<div>{curr_repo_e or "-"}</div>'

        gmaps_url = f"https://www.google.com/maps?q={lat},{lon}"
        
        tabla_rows_html += f"""
        <tr class="{'row-fg' if is_fg else ''}">
            <td><strong>{cd_e}</strong></td>
            <td>{badge_zona}</td>
            <td><strong>{aff}</strong> pers.</td>
            <td>{start_e}</td>
            <td>{delay_info}</td>
            <td>{res_e}</td>
            <td>{cause_e}</td>
            <td>{badge_status}</td>
            <td><a href="{gmaps_url}" target="_blank" class="map-link">🌐 Ver Mapa ({lat:.4f}, {lon:.4f})</a></td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Historial Cortes de Luz - Fuente del Gallo (Conil)</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-color: #f8fafc;
            --text-muted: #94a3b8;
            --accent-blue: #38bdf8;
            --accent-red: #f43f5e;
            --accent-green: #10b981;
            --accent-amber: #f59e0b;
            --border-color: #334155;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 16px;
        }}
        h1 {{
            margin: 0;
            font-size: 1.8rem;
            color: var(--accent-blue);
        }}
        .last-update {{
            font-size: 0.9rem;
            color: var(--text-muted);
            text-align: right;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        .metric-card {{
            background-color: var(--card-bg);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid var(--border-color);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }}
        .metric-title {{
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}
        .metric-value {{
            font-size: 2rem;
            font-weight: 700;
        }}
        .metric-sub {{
            font-size: 0.85rem;
            margin-top: 4px;
        }}
        .val-red {{ color: var(--accent-red); }}
        .val-blue {{ color: var(--accent-blue); }}
        .val-green {{ color: var(--accent-green); }}
        .val-amber {{ color: var(--accent-amber); }}

        .section-title {{
            font-size: 1.3rem;
            margin: 32px 0 16px 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .card-box {{
            background-color: var(--card-bg);
            border-radius: 12px;
            padding: 24px;
            border: 1px solid var(--border-color);
            margin-bottom: 32px;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-weight: 600;
        }}
        .badge-active {{ background-color: rgba(244, 63, 94, 0.2); color: #f43f5e; border: 1px solid #f43f5e; }}
        .badge-resolved {{ background-color: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid #10b981; }}
        .badge-fg {{ background-color: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid #38bdf8; }}
        .badge-mun {{ background-color: rgba(148, 163, 184, 0.2); color: #94a3b8; border: 1px solid #94a3b8; }}

        .delay-warning {{
            color: var(--accent-amber);
            font-weight: 600;
            font-size: 0.85rem;
        }}
        .table-responsive {{
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.9rem;
        }}
        th {{
            background-color: #0f172a;
            color: var(--text-muted);
            padding: 12px;
            border-bottom: 2px solid var(--border-color);
        }}
        td {{
            padding: 14px 12px;
            border-bottom: 1px solid var(--border-color);
        }}
        tr.row-fg {{
            background-color: rgba(56, 189, 248, 0.05);
        }}
        tr:hover {{
            background-color: rgba(255, 255, 255, 0.03);
        }}
        .map-link {{
            color: var(--accent-blue);
            text-decoration: none;
        }}
        .map-link:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>⚡ Historial de Cortes de Luz - e-distribución</h1>
                <div style="color: var(--text-muted); font-size: 0.95rem; margin-top: 4px;">Seguimiento en <strong>Fuente del Gallo</strong> y Conil de la Frontera</div>
            </div>
            <div class="last-update">
                Última verificación:<br><strong>{datetime.now().strftime("%d/%m/%Y %H:%M:%S")}</strong>
            </div>
        </header>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-title">Fuente del Gallo (En Curso)</div>
                <div class="metric-value val-red">{activas_fg}</div>
                <div class="metric-sub val-amber">Máx. {max_afectados_fg} vecinos afectados</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Total Fuente del Gallo (Histórico)</div>
                <div class="metric-value val-blue">{total_fg}</div>
                <div class="metric-sub" style="color: var(--text-muted);">Averías registradas</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Total Conil (En Curso)</div>
                <div class="metric-value val-red">{activas_total}</div>
                <div class="metric-sub" style="color: var(--text-muted);">Puntos en todo el municipio</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Total Registros en BD</div>
                <div class="metric-value val-green">{total_incidencias}</div>
                <div class="metric-sub" style="color: var(--text-muted);">Histórico acumulado</div>
            </div>
        </div>

        <div class="section-title">📊 Frecuencia de Averías por Fecha</div>
        <div class="card-box">
            <canvas id="frequencyChart" height="90"></canvas>
        </div>

        <div class="section-title">📋 Registro de Averías (Control de Aplazamientos)</div>
        <div class="card-box table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>Código Centro</th>
                        <th>Zona</th>
                        <th>Afectados</th>
                        <th>Inicio Corte</th>
                        <th>Estimación Reposición / Aplazamientos</th>
                        <th>Hora Resuelto</th>
                        <th>Causa</th>
                        <th>Estado</th>
                        <th>Ubicación</th>
                    </tr>
                </thead>
                <tbody>
                    {tabla_rows_html}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const ctx = document.getElementById('frequencyChart').getContext('2d');
        const dates = {fechas_json};
        const counts = {conteo_json};

        new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: dates,
                datasets: [{{
                    label: 'Número de Averías Registradas',
                    data: counts,
                    backgroundColor: '#38bdf8',
                    borderColor: '#0284c7',
                    borderWidth: 1,
                    borderRadius: 6
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ labels: {{ color: '#f8fafc' }} }}
                }},
                scales: {{
                    x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
                    y: {{ ticks: {{ color: '#94a3b8', stepSize: 1 }}, grid: {{ color: '#334155' }}, beginAtZero: true }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""

    os.makedirs(PAGES_DIR, exist_ok=True)
    tmp_html = HTML_PATH + ".tmp"
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    os.replace(tmp_html, HTML_PATH)
    return HTML_PATH

def mostrar_historial():
    """Imprime el historial por pantalla."""
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT cd_code, municipality, affected_client, interruption_date, initial_reposition_date, current_reposition_date, delay_count, cause, status, first_seen, resolved_at, latitude, longitude
        FROM averias_v2
        ORDER BY first_seen DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    print(f"\n================ HISTORIAL DE AVERÍAS REGISTRADAS ({len(rows)}) ================")
    if not rows:
        print("No hay averías registradas todavía.")
        return

    for r in rows:
        cd, mun, aff, start, init_repo, curr_repo, delays, cause, status, first, res, lat, lon = r
        is_fg = " [📍 FUENTE DEL GALLO]" if es_fuente_del_gallo(lat, lon) else ""
        estado_str = f"🟢 RESUELTO ({res})" if status == "RESOLVED" else "🔴 EN CURSO"
        delay_str = f" [⚠️ POSPUESTO {delays} veces! Inicial: {init_repo} -> Actual: {curr_repo}]" if delays > 0 else f" [Estimado: {curr_repo}]"
        
        print(f"\n[{estado_str}]{is_fg} Código: {cd} | Municipio: {mun}")
        print(f"  Clientes Afectados: {aff} | Causa: {cause}")
        print(f"  Inicio Incidencia:  {start}")
        print(f"  Horario Reposición: {delay_str}")
        print(f"  Registrado en DB:   {first}")
        print(f"  Coordenadas:        Lat {lat}, Lon {lon}")
    print("========================================================================\n")

def ejecutar_chequeo(municipio="Conil de la Frontera"):
    """Ejecuta la verificación en vivo con bloqueo de proceso y actualización segura."""
    lock_file = acquire_lock()
    try:
        features = consultar_api(municipio)
        if features is not None:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] API e-distribución: {len(features)} puntos devueltos en '{municipio}'.")
            registrar_averias(features, municipio)
            total_csv = exportar_csv()
            html_file = generar_html()
            print(f" -> DB, CSV ({CSV_PATH}) y Dashboard HTML ({html_file}) actualizados correctamente.")
            return True

        print("❌ Chequeo cancelado: no se actualizaron los archivos porque la API no respondió correctamente.")
        return False
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    
    if cmd == "history":
        mostrar_historial()
    elif cmd == "export":
        n = exportar_csv()
        print(f"Exportado {n} registros a {CSV_PATH}")
    elif cmd == "html":
        h = generar_html()
        print(f"HTML generado en {h}")
    else:
        municipio = sys.argv[2] if len(sys.argv) > 2 else "Conil de la Frontera"
        sys.exit(0 if ejecutar_chequeo(municipio) else 1)
