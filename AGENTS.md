# Technical Specification & Context for AI Agents (Codex / Claude / Antigravity)

This document provides complete reverse-engineering context, API contracts, architectural decisions, and domain rules for AI coding agents modifying or maintaining this codebase.

---

## 1. Reverse Engineering & Source Analysis

### Web Source
- **Public Facing Page:** `https://www.edistribucion.com/es/averias.html`
- **Embedded WebApp:** `https://dpa-portalgis.enel.com/portal/apps/instant/basic/index.html?appid=21f5d825ceeb48dba6ef6ee97f840168&locale=es`
- **ArcGIS WebMap ID:** `d6ded175312e444db589836ed2f543dd`

### Clarification on Chrome DevTools Requests (`32?f=json` and `.pbf`)
When inspecting network traffic on the e-distribución map, DevTools shows requests like:
- Vector Tile binaries (`*.pbf`)
- UTFGrid / Index JSONs (`32?f=json` returning arrays `data: [0, 0, 0...]`)

**Note for Agents:** These `.pbf` and `32?f=json` files are **not** the raw outage database endpoints. They are Mapbox/ArcGIS canvas interaction grids used by the client browser for hover/click hit testing on map icons.

### Direct REST API Endpoint
The authoritative data layer for power outages in Spain is hosted on Enel's ArcGIS FeatureServer:

```text
https://dpa-portalgis.enel.com/server/rest/services/Hosted/ESP_Prod_power_cut_View/FeatureServer/0/query
```

#### Query Parameters:
- `where`: SQL filter clause (e.g. `municipality LIKE '%Conil de la Frontera%'` or `territory = 'AND'`)
- `outFields`: `*` (returns all attributes)
- `f`: `json`
- `returnGeometry`: `true`

#### API Response Schema (Attributes per feature):
- `cd_code` (string): ID of the power grid transformation center (e.g., `"22653"`).
- `municipality` (string): Municipality name (e.g., `"Conil de la Frontera"`).
- `territory` (string): Regional code (e.g., `"AND"` for Andalucía).
- `service_type` (string): Voltage level (e.g., `"GM"` = Medium Voltage, `"LV"` = Low Voltage).
- `affected_client` (integer): Number of affected customers connected to that center.
- `interruption_date` (string): Reported outage start timestamp (e.g., `"26/08/2026 08:30"`).
- `reposition_date` (string): Estimated restoration timestamp (e.g., `"26/08/2026 13:30"`).
- `update_time` (string): Last update timestamp from e-distribución (e.g., `"26/08/2026 13:20"`).
- `des_cause_es` (string): Outage cause in Spanish (e.g., `"Avería"` vs `"Trabajos programados"`).
- `latitude` / `longitude` (double): Precise GPS coordinates.

---

## 2. Core Domain Rules & Production Protections

### Rule 1: JSON Error & Status Code Handling
ArcGIS REST endpoints occasionally return HTTP 200 OK containing a JSON payload with an `error` key: `{"error": {"code": 400, "message": "..."}}`.
- Agents **must** explicitly check `"error" in data` before reading `data.get("features", [])`.
- If an error payload is returned, return `None` so that active outages are **not** falsely marked as missing or resolved.

### Rule 2: Process Lock & Atomic Storage
To support concurrent cron executions without database locks or corrupt files:
- Use `fcntl.flock` on `.script.lock` to prevent overlapping runs.
- Set `sqlite3.connect(DB_PATH, timeout=30.0)`.
- Write `averias.csv` and `index.html` via `.tmp` intermediate files followed by `os.replace`.

### Rule 3: Continuous Incident Identity & In-Payload Deduplication
An incoming API point is matched to an existing active database record if:
1. `incoming.cd_code == existing.cd_code` (and `cd_code != "DESCONOCIDO"`), OR
2. Valid coordinates exist (`lat != 0.0` and `lon != 0.0`) AND Spatial Haversine distance `distance < 0.15 km` (150 meters).

In-memory active records are updated during the iteration loop so that multiple duplicate points within a single API response payload are deduplicated into the same row.

### Rule 4: Postponed Resolution Tracking (`delay_count`)
- Store `initial_reposition_date` upon first detection.
- When updating an active record, if `new_reposition_date != current_reposition_date`:
  - Increment `delay_count += 1`.
  - Update `current_reposition_date = new_reposition_date`.
- In UI/exports, clearly flag incidents with `delay_count > 0` as **postponed/delayed**.

### Rule 5: Grace Period Against False Resolutions
- Increment `missing_count += 1` when an outage is absent from an API response.
- Change `status = 'RESOLVED'` **only when** `missing_count >= 3` (3 consecutive failed checks, ~30-45 mins).
- When resolving, set `resolved_at = now_str` (the timestamp when resolution is confirmed).

### Rule 6: HTML Escaping & Security
All text fields extracted from the API (`cd_code`, `municipality`, `cause`, timestamps) must be sanitized using `html.escape()` before rendering in `index.html` to prevent XSS.

---

## 3. Database Schema & Indexes (`averias_v2`)

```sql
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
);

CREATE INDEX IF NOT EXISTS idx_averias_muni_status ON averias_v2 (municipality, status);
CREATE INDEX IF NOT EXISTS idx_averias_cd_code ON averias_v2 (cd_code);
```
