# Local ADS-B Flight Radar

A local ADS-B flight radar that runs entirely on your Mac — no API key, no cloud service, no Cloudflare friction.

## How it works

```
Browser (Leaflet map)  →  localhost (Python)  →  ADS-B APIs
```

The Python server does all external API calls itself, so neither CORS restrictions nor Cloudflare browser challenges apply. Your browser only ever talks to `localhost`.

## Setup

```bash
cp config.sample.toml config.toml
# Edit config.toml: set your center coordinates, radius, port, and sectors
python flightradar.py
# Open http://localhost:<port>
```

Stop with `Ctrl-C`.

## Requirements

- Python 3.11+ (stdlib only — `tomllib` is built in)
- Internet access to reach `api.adsb.lol` or `api.adsb.one`

## Configuration

Copy `config.sample.toml` to `config.toml` (gitignored) and edit:

```toml
[radar]
center_lat     = 48.8566
center_lon     = 2.3522
radius_km      = 50
port           = 8742
location_name  = "My Location"
observer_alt_m = 150.0   # GPS / WGS-84 ellipsoidal height in metres
geoid_offset_m = 47.0    # geoid undulation; MSL = observer_alt_m − geoid_offset_m

# One block per field-of-view sector. Remove all to draw no sectors.
[[sectors]]
start = 270
end   = 360
```

| Key | Description |
|---|---|
| `center_lat` / `center_lon` | Map center coordinates |
| `radius_km` | ADS-B query radius in km (max ~463) |
| `port` | Local web server port |
| `location_name` | Display name shown in the browser tab and HUD |
| `observer_alt_m` | GPS / WGS-84 ellipsoidal height of the observer in metres (optional) |
| `geoid_offset_m` | Geoid undulation N; MSL height = `observer_alt_m − geoid_offset_m` (optional) |
| `[[sectors]]` | Field-of-view sectors: `start`/`end` compass bearings in degrees. Sectors crossing north (e.g. 310 → 30) work automatically. |

### Finding your geoid offset

The geoid offset (undulation N) converts your GPS altitude (WGS-84 ellipsoidal) to metres above mean sea level. Three ways to get it:

1. **GPS device or app** — many receivers show it directly as *geoid height* or *geoid separation* in their status or satellite screen.
2. **[GeoidEval](https://geographiclib.sourceforge.io/cgi-bin/GeoidEval)** — enter your latitude and longitude, read off N in metres (EGM2008 model, accurate to ~1 m worldwide).
3. **National geodesy authority** — higher-accuracy national models:
   - Germany: [BKG GCG2016](https://gibs.bkg.bund.de/geoid/gscomp.php?p=g)
   - USA: [NGS GEOID18](https://geodesy.noaa.gov/GEOID/GEOID18/computation.html)

## Data sources

Aircraft data comes from free, keyless ADS-B APIs with automatic fallback:

1. **[adsb.lol](https://adsb.lol)** — ODbL licensed, ADSBExchange v2 format
2. **[adsb.one](https://adsb.one)** — community feed, same format

## Map features

- CARTO Voyager tile layer
- Concentric range rings every 10 km
- Field-of-view sectors shaded in gray (configured in `config.toml`)
- Aircraft inside a sector show azimuth and elevation angle beneath their dot: `az 245°  ↑+3.2°`
- Aircraft colored by altitude (red = low → green = high)
- Track line proportional to ground speed
- Click any aircraft for callsign, registration, type, altitude, speed, and heading
- Auto-refreshes every 5 seconds
