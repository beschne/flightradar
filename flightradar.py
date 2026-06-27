#!/usr/bin/env python3
"""
flightradar.py — Local ADS-B flight radar for Mac.

Architecture (structurally solves CORS and Cloudflare blocking):

    Browser (Leaflet)  ->  local Python server  ->  keyless ADS-B API
    (localhost only)       (server-side fetch,       (api.adsb.lol etc.)
                            no CORS, custom UA)

The browser only ever talks to your own localhost server.
Python makes the actual API calls — Cloudflare browser challenges and
CORS policies don't apply there.

Setup: copy config.sample.toml to config.toml and edit your location.
Start: python3 flightradar.py
Then:  open http://localhost:<port> in your browser.

No external Python dependencies (stdlib only).
"""

import json
import os
import sys
import threading
import time
import tomllib
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# Configuration (loaded from config.toml)
# ---------------------------------------------------------------------------

def _load_config():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        print(
            "Error: config.toml not found.\n"
            "Copy config.sample.toml to config.toml and fill in your location.",
            file=sys.stderr,
        )
        sys.exit(1)

_cfg          = _load_config()
_radar        = _cfg["radar"]
CENTER_LAT    = float(_radar["center_lat"])
CENTER_LON    = float(_radar["center_lon"])
RADIUS_KM     = float(_radar["radius_km"])
PORT          = int(_radar["port"])
LOCATION_NAME = _radar.get("location_name", "Local Radar")
SECTORS            = _cfg.get("sectors", [])   # list of {start, end} dicts
OBSERVER_ALT_M     = float(_radar.get("observer_alt_m", 0.0))
GEOID_OFFSET_M     = float(_radar.get("geoid_offset_m", 0.0))
OBSERVER_ALT_MSL_M = OBSERVER_ALT_M - GEOID_OFFSET_M   # orthometric / MSL height

RADIUS_NM    = RADIUS_KM / 1.852   # Search radius in nautical miles (max 250 for adsb.lol)
FOV_RANGE_KM = RADIUS_KM           # Visual range for FOV sectors matches query radius

USER_AGENT = "flightradar-local/1.0 (personal hobby use)"

# ---------------------------------------------------------------------------
# Backend abstraction
# ---------------------------------------------------------------------------
# Each source is a function (lat, lon, radius_nm) -> list of normalised dicts.
# Swapping the data source in one place is transparent to the frontend.
# Normalising to a common schema (see _normalize_*) fully decouples the
# frontend from any particular API format.

def _http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_adsb_lol(lat, lon, radius_nm):
    """ADSB.lol — ODbL licensed, keyless. Format: ADSBExchange v2."""
    url = f"https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}"
    data = _http_get_json(url)
    return [_normalize_adsbx(ac) for ac in data.get("ac", [])]


def fetch_adsb_one(lat, lon, radius_nm):
    """airplanes.live / ADSB One — keyless, 1 request/second. Format: ADSBExchange v2."""
    url = f"https://api.adsb.one/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}"
    data = _http_get_json(url)
    return [_normalize_adsbx(ac) for ac in data.get("ac", [])]


def _normalize_adsbx(ac):
    """Reduce an ADSBExchange v2 record to a lean, unified schema."""
    alt = ac.get("alt_baro")
    if alt == "ground":
        alt = 0
    return {
        "hex":      ac.get("hex"),
        "callsign": (ac.get("flight") or "").strip() or None,
        "reg":      ac.get("r"),
        "type":     ac.get("t"),
        "lat":      ac.get("lat"),
        "lon":      ac.get("lon"),
        "alt_ft":   alt,
        "track":    ac.get("track"),          # heading over ground in degrees
        "gs_kt":    ac.get("gs"),             # ground speed in knots
    }

# Active sources — tried in order, first success wins.
SOURCES = [
    ("ADSB.lol",  fetch_adsb_lol),
    ("ADSB.one",  fetch_adsb_one),
]


_aircraft_cache: dict | None = None   # last successful response

def get_aircraft():
    """Try each source in order; move to the next on error.
    On total failure, return the last known positions (stale=True) so the
    map is not wiped by a transient 403 or network blip."""
    global _aircraft_cache
    last_err = None
    for name, fn in SOURCES:
        try:
            planes = fn(CENTER_LAT, CENTER_LON, RADIUS_NM)
            planes = [p for p in planes if p["lat"] is not None and p["lon"] is not None]
            _aircraft_cache = {"source": name, "aircraft": planes}
            return _aircraft_cache
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
            last_err = f"{name}: {e}"
            continue
    if _aircraft_cache is not None:
        return {**_aircraft_cache, "stale": True, "error": last_err}
    return {"source": None, "aircraft": [], "stale": True, "error": last_err or "no source reachable"}

# ---------------------------------------------------------------------------
# HTTP server: serves the map and a JSON endpoint
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep console quiet

    def do_GET(self):
        try:
            self._do_get()
        except BrokenPipeError:
            # Client closed the connection — harmless (e.g. page reload, navigate away).
            pass

    def _do_get(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = (INDEX_HTML
                .replace("__LOCATION_NAME__", LOCATION_NAME)
                .replace("__CENTER_LAT__",    str(CENTER_LAT))
                .replace("__CENTER_LON__",    str(CENTER_LON))
                .replace("__RADIUS_KM__",     str(int(RADIUS_KM)))
                .replace("__FOV_RANGE_KM__",  str(int(FOV_RANGE_KM)))
                .replace("__SECTORS_JSON__",       json.dumps(SECTORS))
                .replace("__OBSERVER_ALT_MSL_M__", f"{OBSERVER_ALT_MSL_M:.2f}")
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/aircraft":
            payload = json.dumps(get_aircraft()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_error(404)


# ---------------------------------------------------------------------------
# Frontend (Leaflet). Colours markers by altitude, rotates them by heading,
# popup shows callsign / reg / type / altitude / speed.
# ---------------------------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Flightradar — __LOCATION_NAME__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body { margin: 0; height: 100%; background: #e8eef7; font-family: -apple-system, system-ui, sans-serif; }
  #map { position: absolute; inset: 0; }
  #hud {
    position: absolute; z-index: 1000; bottom: 12px; left: 12px;
    background: rgba(255,255,255,.90); color: #1a2f5a; padding: 10px 14px;
    border-radius: 10px; font-size: 13px; line-height: 1.5;
    backdrop-filter: blur(6px); border: 1px solid rgba(80,120,220,.20);
    box-shadow: 0 2px 8px rgba(0,0,0,.12);
  }
  #hud b { color: #0d1e40; font-weight: 600; }
  #hud .src { color: #2255cc; }
  #hud .err { color: #cc2020; }
  .leaflet-popup-content { font-size: 13px; }
  .leaflet-popup-content code { color: #2a4d8f; }
  .az-label { background: none; border: none; box-shadow: none; padding: 0 2px;
    font-size: 10px; font-weight: 600; white-space: nowrap; }
  .az-label::before { display: none; }</style>
</head>
<body>
<div id="map"></div>
<div id="hud">
  <div><b>Flightradar — __LOCATION_NAME__</b></div>
  <div><span id="count">–</span> aircraft · Source: <span class="src" id="src">–</span></div>
  <div id="status">loading …</div>
  <div style="margin-top:7px;padding-top:7px;border-top:1px solid rgba(80,120,220,.15)">
    <div style="font-size:11px;margin-bottom:3px;color:#555">Altitude</div>
    <div style="display:flex;align-items:center;gap:6px;font-size:10px;color:#333">
      <span>0</span>
      <div style="flex:1;height:7px;border-radius:3px;background:linear-gradient(to right,hsl(0,90%,38%),hsl(65,90%,38%),hsl(130,90%,38%))"></div>
      <span>12 km / 6.6 NM</span>
    </div>
  </div>
</div>
<script>
  const CENTER             = [__CENTER_LAT__, __CENTER_LON__];
  const RADIUS_KM          = __RADIUS_KM__;
  const FOV_RANGE_KM       = __FOV_RANGE_KM__;
  const SECTORS            = __SECTORS_JSON__;
  const OBSERVER_ALT_MSL_M = __OBSERVER_ALT_MSL_M__;

  const map = L.map('map', { zoomControl: true, scrollWheelZoom: false, doubleClickZoom: false });
  map.setView(CENTER, 10); // temporary — corrected below once map size is known
  const cosLat = Math.cos(CENTER[0] * Math.PI / 180);
  const desiredKm = RADIUS_KM * 0.88; // 40 km ring visible; 50 km ring clipped
  const zoom = Math.log2(40075 * cosLat * map.getSize().y / 2 / 256 / desiredKm);
  map.setView(CENTER, Math.floor(zoom));

  // CARTO Voyager tile layer.
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    attribution: '© OpenStreetMap, © CARTO', subdomains: 'abcd', maxZoom: 19
  }).addTo(map);

  // Concentric range rings every 10 km
  for (let r = 10; r <= RADIUS_KM; r += 10) {
    L.circle(CENTER, {
      radius: r * 1000, color: '#3366cc', weight: r === RADIUS_KM ? 1.5 : 1,
      fill: false, dashArray: '8 5', opacity: r === RADIUS_KM ? 0.45 : 0.25
    }).addTo(map);
  }

  // Center marker
  L.circleMarker(CENTER, { radius: 5, color: '#1a8fff', fillColor: '#1a8fff',
    fillOpacity: 1, weight: 1 }).addTo(map).bindPopup('Center');

  // Field-of-view sectors from config
  function fovPolygon(startDeg, endDeg) {
    const pts = [CENTER];
    const end = endDeg > startDeg ? endDeg : endDeg + 360;
    for (let b = startDeg; b <= end; b++) {
      const tr = (b % 360) * Math.PI / 180;
      pts.push([CENTER[0] + FOV_RANGE_KM / 111 * Math.cos(tr),
                CENTER[1] + FOV_RANGE_KM / (111 * cosLat) * Math.sin(tr)]);
    }
    return L.polygon(pts, { color: '#888', weight: 1, opacity: 0.5,
      fillColor: '#888', fillOpacity: 0.15 }).addTo(map);
  }
  for (const s of SECTORS) fovPolygon(s.start, s.end);

  // Azimuth (compass bearing) from observer to a point, in degrees 0–360.
  function azimuth(lat2, lon2) {
    const φ1 = CENTER[0] * Math.PI / 180, φ2 = lat2 * Math.PI / 180;
    const Δλ = (lon2 - CENTER[1]) * Math.PI / 180;
    const y = Math.sin(Δλ) * Math.cos(φ2);
    const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
  }

  // Haversine distance in metres.
  function distanceM(lat2, lon2) {
    const R = 6371000;
    const φ1 = CENTER[0] * Math.PI / 180, φ2 = lat2 * Math.PI / 180;
    const Δφ = (lat2 - CENTER[0]) * Math.PI / 180;
    const Δλ = (lon2 - CENTER[1]) * Math.PI / 180;
    const a = Math.sin(Δφ/2)**2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function inAnySector(az) {
    return SECTORS.some(s => {
      const end = s.end > s.start ? s.end : s.end + 360;
      const b   = az >= s.start   ? az   : az  + 360;
      return b >= s.start && b <= end;
    });
  }

  // Label shown beneath in-sector aircraft: "az 245°  ↑3.2°"
  function azLabel(p, az, color) {
    let txt = 'az ' + Math.round(az) + '°';
    if (p.alt_ft != null) {
      const dh  = p.alt_ft * 0.3048 - OBSERVER_ALT_MSL_M;
      const el  = Math.atan2(dh, distanceM(p.lat, p.lon)) * 180 / Math.PI;
      txt += '<br>↑' + (el >= 0 ? '+' : '') + el.toFixed(1) + '°';
    }
    return '<span style="color:' + color + '">' + txt + '</span>';
  }

  // Altitude-dependent colour (red = low, green = high)
  function altColor(ft) {
    if (ft == null) return '#999';
    const t = Math.max(0, Math.min(1, ft / 40000));
    const hue = 0 + t * 130;            // 0° red -> 130° green
    return `hsl(${hue}, 90%, 38%)`;
  }

  const markers = new Map();   // hex -> { dot: L.circleMarker, line: L.polyline|null }

  // Compute the track-line endpoint: length scales with speed relative to radius.
  function trackEnd(lat, lon, trackDeg, gs_kt) {
    if (!trackDeg && trackDeg !== 0) return null;
    const lenKm = (gs_kt || 0) * RADIUS_KM / 2500;
    const tr = trackDeg * Math.PI / 180;
    const dlat = lenKm / 111 * Math.cos(tr);
    const dlon = lenKm / (111 * Math.cos(lat * Math.PI / 180)) * Math.sin(tr);
    return [lat + dlat, lon + dlon];
  }

  function popup(p, az, el) {
    let alt = 'on ground';
    if (p.alt_ft == null)          alt = '–';
    else if (p.alt_ft !== 0)       alt = p.alt_ft.toLocaleString('en-US') + ' ft / '
                                       + (p.alt_ft * 0.0003048).toFixed(1) + ' km';
    const spd = p.gs_kt != null
      ? Math.round(p.gs_kt) + ' kt / ' + Math.round(p.gs_kt * 1.852) + ' km/h'
      : '–';
    const azStr = az != null ? Math.round(az) + '°' : '–';
    const elStr = el != null ? (el >= 0 ? '+' : '') + el.toFixed(1) + '°' : '–';
    const regLink = p.reg
      ? `<a href="https://globe.adsbexchange.com/?icao=${p.hex}" target="_blank" rel="noopener">${p.reg}</a>`
      : p.hex
        ? `<a href="https://globe.adsbexchange.com/?icao=${p.hex}" target="_blank" rel="noopener">${p.hex}</a>`
        : '–';
    return `<b>${p.callsign || p.hex}</b><br>`
         + `Reg: ${regLink} · Type: ${p.type || '–'}<br>`
         + `Alt: ${alt}<br>`
         + `Speed: ${spd}<br>`
         + `Heading: ${p.track != null ? Math.round(p.track) + '°' : '–'}<br>`
         + `Az: <code>${azStr}</code> · El: <code>${elStr}</code>`;
  }

  async function tick() {
    try {
      const r = await fetch('/api/aircraft');
      const data = await r.json();
      const seen = new Set();

      for (const p of data.aircraft) {
        seen.add(p.hex);
        const color  = altColor(p.alt_ft);
        const end    = trackEnd(p.lat, p.lon, p.track, p.gs_kt);
        const az     = azimuth(p.lat, p.lon);
        const inFov  = inAnySector(az);
        const label  = inFov ? azLabel(p, az, color) : null;
        const el     = p.alt_ft != null
          ? Math.atan2(p.alt_ft * 0.3048 - OBSERVER_ALT_MSL_M, distanceM(p.lat, p.lon)) * 180 / Math.PI
          : null;
        let entry = markers.get(p.hex);
        if (entry) {
          entry.dot.setLatLng([p.lat, p.lon]).setStyle({ color, fillColor: color });
          entry.dot.getPopup() && entry.dot.setPopupContent(popup(p, az, el));
          if (inFov) {
            if (entry.dot.getTooltip()) entry.dot.setTooltipContent(label);
            else entry.dot.bindTooltip(label, { permanent: true, direction: 'bottom', className: 'az-label', offset: [0, 4] });
          } else {
            if (entry.dot.getTooltip()) entry.dot.unbindTooltip();
          }
          if (end) {
            if (entry.line) entry.line.setLatLngs([[p.lat, p.lon], end]).setStyle({ color });
            else { entry.line = L.polyline([[p.lat, p.lon], end], { color, weight: 1.5, opacity: 0.8 }).addTo(map); }
          } else if (entry.line) {
            map.removeLayer(entry.line); entry.line = null;
          }
        } else {
          const dot = L.circleMarker([p.lat, p.lon], {
            radius: 5, color, fillColor: color, fillOpacity: 1, weight: 1.5
          }).addTo(map).bindPopup(popup(p, az, el));
          if (inFov) dot.bindTooltip(label, { permanent: true, direction: 'bottom', className: 'az-label', offset: [0, 4] });
          const line = end ? L.polyline([[p.lat, p.lon], end], { color, weight: 1.5, opacity: 0.8 }).addTo(map) : null;
          markers.set(p.hex, { dot, line });
        }
      }
      // remove aircraft that disappeared
      for (const [hex, { dot, line }] of markers) {
        if (!seen.has(hex)) { map.removeLayer(dot); if (line) map.removeLayer(line); markers.delete(hex); }
      }

      document.getElementById('count').textContent = data.aircraft.length;
      document.getElementById('src').textContent = data.source
        ? data.stale ? data.source + ' (stale)' : data.source
        : '–';
      const ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZoneName: 'short' });
      document.getElementById('status').innerHTML = data.stale
        ? `<span style="color:#b06000">⚠ API error — showing last known positions</span>`
        : 'updated ' + ts;
    } catch (e) {
      document.getElementById('status').innerHTML =
        `<span class="err">local server unreachable</span>`;
    }
  }

  tick();
  setInterval(tick, 5000);   // poll every 5 s (respects API rate limits)
</script>
</body>
</html>
"""


def main():
    print(f"Flightradar — {LOCATION_NAME}")
    print(f"Running at  http://localhost:{PORT}")
    print(f"Center: {CENTER_LAT}, {CENTER_LON}  ·  Radius: {RADIUS_KM} km ({RADIUS_NM:.1f} NM)")
    print(f"Sectors: {len(SECTORS)}")
    print("Stop with Ctrl-C.")
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
