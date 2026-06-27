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
center_lat    = 48.8566
center_lon    = 2.3522
radius_km     = 50
port          = 8742
location_name = "My Location"

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
| `[[sectors]]` | Field-of-view sectors: `start`/`end` compass bearings in degrees. Sectors crossing north (e.g. 310 → 30) work automatically. |

## Data sources

Aircraft data comes from free, keyless ADS-B APIs with automatic fallback:

1. **[adsb.lol](https://adsb.lol)** — ODbL licensed, ADSBExchange v2 format
2. **[adsb.one](https://adsb.one)** — community feed, same format

## Map features

- CARTO Voyager tile layer
- Concentric range rings every 10 km
- Field-of-view sectors shaded in gray (configured in `config.toml`)
- Aircraft colored by altitude (red = low → green = high)
- Track line proportional to ground speed
- Click any aircraft for callsign, registration, type, altitude, speed, and heading
- Auto-refreshes every 5 seconds
