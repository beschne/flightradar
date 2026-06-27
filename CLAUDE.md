# Flightradar — CLAUDE.md

## Project summary
Local ADS-B flight radar for macOS. A single Python script starts a localhost
HTTP server that proxies keyless ADS-B APIs (adsb.lol, adsb.one) and serves a
Leaflet.js map to the browser. Bypasses CORS and Cloudflare browser challenges
by doing all external fetches server-side.

## Git / commits
Only the repository owner commits. Claude must never commit or push — not even
with `--no-verify`. Draft commit messages if asked, but leave the actual
commit to the owner.

## Stack
- Python 3.11+ stdlib only — no third-party packages, no pip installs. (`tomllib` is built-in since 3.11.)
- Frontend: Leaflet 1.9.4 loaded from unpkg CDN; no build step.
- Virtual environment: `.venv/` (present but empty — nothing to install).

## Running
```
python flightradar.py
# then open http://localhost:8742
```

## Architecture
```
Browser (Leaflet) -> localhost:8742 (Python ThreadingHTTPServer) -> ADS-B APIs
```
`GET /` serves the full HTML/JS page (embedded in `INDEX_HTML`).
`GET /api/aircraft` returns JSON with aircraft positions from the first working source.

The HTML template uses `__CENTER_LAT__`, `__CENTER_LON__`, `__RADIUS_KM__`, `__LOCATION_NAME__`,
and `__SECTORS_JSON__` placeholders substituted at request time in `do_GET`. Config changes
take effect on the next page load after a server restart.

## Source fallback chain
1. **adsb.lol** (ODbL, keyless, ADSBExchange v2 format)
2. **adsb.one** (keyless, ADSBExchange v2 format)

Adding a new source: implement `fetch_<name>(lat, lon, radius_nm)` and append
it to the `SOURCES` list.

## Configuration (`config.toml`, gitignored)
Location, port, and sectors live in `config.toml` (copy from `config.sample.toml`).
Loaded at startup via `tomllib`; a missing file exits with a clear error.

| Key | Meaning |
|---|---|
| `center_lat` / `center_lon` | Map center coordinates |
| `radius_km` | ADS-B query radius in km; auto-converted to NM (max ~463) |
| `port` | localhost port |
| `location_name` | Shown in the browser tab and HUD |
| `[[sectors]]` | Array of `{start, end}` compass bearings; serialised to `__SECTORS_JSON__` |

## Map features
- **Tile layer**: CARTO Voyager (colorful, detailed OSM-style)
- **Range rings**: dashed circles every 10 km up to `RADIUS_KM`; no labels
- **Center marker**: blue dot at office coordinates
- **Field of view**: two gray polygon sectors (office windows)
  - 210°–280° (south-southwest)
  - 310°–30° (north-northwest, wraps through 0°)
- **Aircraft markers**: `L.circleMarker` (radius 5) + `L.polyline` track line
  - Color: red (low altitude) → green (high altitude), mapped over 0–40,000 ft / 0–12 km / 0–6.6 NM
  - Track line length proportional to ground speed (`gs_kt * RADIUS_KM / 2500` km)
- **Zoom**: scroll-wheel and double-click zoom disabled; keyboard and ±-buttons enabled
- **Initial view**: computed from viewport height so the 40 km ring fills the screen regardless of aspect ratio; 50 km ring is clipped. Formula: `zoom = floor(log2(40075 * cos(lat) * mapHeight / 2 / 256 / desiredKm))` where `desiredKm = RADIUS_KM * 0.88`
- **HUD**: bottom-left, shows aircraft count, data source, last update time (24 h + timezone), altitude colour scale

## Visual range / horizon analysis

Observer location: N50.23046° E8.59337°, terrain ~190m ASL, office floor adds ~10m → **eye level ~200m ASL**.

Geometric horizon formula: `d = √(2 × R_earth × h)` where h is eye height in km.

| Eye height | Ground horizon |
|---|---|
| 200m ASL | **50.5 km** |

For aircraft at altitude the geometric line-of-sight is far larger (113 km at 1,000 ft, 419 km at 35,000 ft) but **atmospheric haze in the Rhine-Main region caps practical visibility**:

| Conditions | Practical range |
|---|---|
| Typical (hazy, summer) | 30–50 km |
| Good | 50–70 km |
| Clear day | up to ~80 km |

`FOV_RANGE_KM = 50` matches the geometric horizon exactly and is a good all-round default. Lower to 40 for typical hazy-day display, raise to 70–80 for clear-day. This variable is independent of `RADIUS_KM` (ADS-B query radius).

## Field-of-view sectors
Sectors are driven by `[[sectors]]` blocks in `config.toml`. Python serialises
the list to JSON and injects it as `__SECTORS_JSON__`; the JS iterates it with
`for (const s of SECTORS) fovPolygon(s.start, s.end)`.

`fovPolygon(startDeg, endDeg)` builds a `L.polygon` by stepping 1° at a time
and converting each bearing + `FOV_RANGE_KM` to a lat/lon offset. Sectors that
cross 0°/360° north are handled by looping to `endDeg + 360` and using
`b % 360` for the bearing.
