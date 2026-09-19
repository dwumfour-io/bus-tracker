# 🚌 Pittsburgh Bus Tracker

Real-time bus arrival tracker for Pittsburgh Regional Transit using TrueTime,
GTFS-Realtime, and the official static GTFS schedule.

![CI](https://github.com/jdwumfour/bus-tracker/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## 📍 Supported Routes & Stops

| Route | Stop | Stop Name | Stop Number(s) |
|-------|------|-----------|----------------|
| 13 | stop_1009 | Center Ave + Chalfonte Ave | 1009 |
| 13 | stop_1016 | Center Ave + Chalfonte Ave | 1016 |
| 13 | westview | West View Plaza Fire Lane + Giant Eagle | 619 |
| 13 | stop_620 | West View Plaza Fire Lane + U-Haul | 620 |
| 13 | stop_618 | West View Park Dr + West View Towers | 618 westbound / 733 downtown-bound |
| 8 | westview, stop_620, stop_618 | West View stops | 619, 620, 618 / 733 |

**Destinations Tracked:**
- ➡️ **West View:** West View Plaza Fire Lane + Giant Eagle
- ⬅️ **Downtown:** Downtown Pittsburgh

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd bus-tracker
pip install -r requirements.txt
```

### 2. Set Environment Variables

Create a `.env` file:

```bash
PAAC_API_KEY=your_truetime_api_key
STOP_ID=1016
BUS_ROUTE=13
```

Get your API key at [truetime.portauthority.org](https://truetime.portauthority.org/)

### 3. Run the API Server

```bash
python api.py
```

The app will be available at `http://localhost:5001`

## 🐳 Docker Deployment

```bash
docker-compose up -d
```

## ☁️ Cloud Deployment (Render)

1. Push to GitHub
2. Connect to Render.com
3. Set Root Directory: `bus-tracker`
4. Set Build Command: `pip install -r requirements.txt`
5. Set Start Command: `gunicorn api:app --bind 0.0.0.0:$PORT`
6. Add environment variable: `PAAC_API_KEY`

## 🔗 API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Main web interface (PWA) |
| `GET /health` | Health check |
| `GET /predictions?route=13&stop=stop_1009` | Arrival predictions for a route/stop |
| `GET /predictions/multi?stop=westview` | Multi-route predictions (Route 8 + 13) |
| `GET /alerts?route=13` | Service alerts |

### Data Source Priority

1. TrueTime predictions
2. GTFS-Realtime TripUpdates when TrueTime has no arrivals
3. Static GTFS scheduled times when neither live source has an arrival

Scheduled-only results are labeled `Scheduled` and return `is_live: false`.
GTFS-Realtime results include both `time` and `scheduled_time` when the trip can
be matched to the static feed, allowing the tracker to report early or late service.

### Example Response: `/predictions?route=13&stop=stop_1009`

```json
{
  "stop_name": "Center Ave + Chalfonte Ave",
  "stop_number": "1009",
  "stop_numbers": {"outbound": "1009", "inbound": "1009"},
  "route": "13",
  "last_updated": "06:15:32 PM",
  "data_source": "truetime",
  "is_live": true,
  "expected_headway": 20,
  "schedule_period": "pm_peak",
  "predictions": {
    "to_west_view": {
      "destination": "West View Plaza Fire Lane + Giant Eagle",
      "direction": "OUTBOUND",
      "arrivals": [
        {"minutes": 5, "time": "06:20 PM", "vehicle_id": "6434", "status": "On Time"},
        {"minutes": 18, "time": "06:33 PM", "vehicle_id": "6310", "status": "On Time"}
      ]
    },
    "to_downtown": {"destination": "Downtown Pittsburgh", "arrivals": []}
  }
}
```

### Example Response: `/predictions/multi?stop=westview`

```json
{
  "stop_name": "West View Plaza Fire Lane + Giant Eagle",
  "stop_number": "619",
  "stop_numbers": {"outbound": "619", "inbound": "619"},
  "routes": ["8", "13"],
  "is_terminus": true,
  "predictions": {
    "to_downtown": {
      "arrivals": [
        {"minutes": 4, "time": "06:19 PM", "vehicle_id": "6434", "route": "13", "status": "On Time"},
        {"minutes": 12, "time": "06:27 PM", "vehicle_id": "6201", "route": "8", "status": "On Time"}
      ],
      "note": "Buses arriving at terminus - board to go downtown"
    }
  }
}
```

## 📁 Project Structure

```
bus-tracker/
├── api.py              # Flask API + static file serving
├── gtfs_static.py      # Static schedule and stop metadata reader
├── gtfs/               # Filtered official GTFS feed for Routes 8 and 13
├── scripts/            # GTFS subset build utility
├── index.html          # Frontend UI
├── app.js              # Frontend JavaScript
├── style.css           # Styling
├── manifest.json       # PWA manifest
├── service-worker.js   # PWA offline support
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker container config
├── docker-compose.yml  # Docker Compose config
├── render.yaml         # Render.com deployment config
├── tests/              # Unit & integration tests
├── .github/workflows/  # CI/CD automation
├── CONTRIBUTING.md     # Contribution guidelines
└── LICENSE             # MIT License
```

## 🔒 Security Features

- **Rate limiting:** 100 requests/minute per IP
- **CORS protection:** Configurable allowed origins
- **Security headers:** X-Frame-Options, CSP, XSS protection
- **Input validation:** Route/stop parameter validation
- **Sanitized errors:** No sensitive data in error responses

## 📱 PWA Features

- Add to home screen on mobile
- Offline support via service worker
- Auto-refresh every 30 seconds
- Service alerts display

## ⚙️ Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PAAC_API_KEY` | TrueTime API key | (required) |
| `STOP_ID` | Default stop ID | 1009 |
| `BUS_ROUTE` | Default route | 13 |
| `API_PORT` | Server port | 5001 |
| `FLASK_DEBUG` | Enable debug mode | false |
| `ALLOWED_ORIGINS` | CORS origins (comma-separated) | http://localhost:5001 |

## Updating the Static GTFS Feed

Download and extract PRT's current static feed, then rebuild the small archive
used by the app:

```bash
python scripts/build_gtfs_subset.py /path/to/GTFS \
  --output gtfs/google_transit.zip --routes 8 13
```

The bundled feed currently covers `2026-06-28` through `2026-10-14`. Rebuild it
when PRT publishes a new schedule so scheduled fallback times remain current.

---

Made with ❤️ for Pittsburgh 🏙️
