# 🚌 Pittsburgh Bus Tracker

Real-time bus arrival tracker for Pittsburgh Port Authority buses using the TrueTime API.

![CI](https://github.com/jdwumfour/bus-tracker/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## 📍 Supported Routes & Stops

| Route | Stop | Stop Name | Stop Number(s) |
|-------|------|-----------|----------------|
| 13 | stop_1009 | Center Ave + Chalfonte Ave | 1009 |
| 13 | stop_1016 | Center Ave + Chalfonte Ave | 1016 |
| 13 | westview | West View Plaza + Giant Eagle | 619 |
| 13 | stop_620 | Stop 620 | 620 |
| 13 | stop_1020 | Stop 1020 | 1020 |
| 13 | stop_618 | Stop 618 | 618 |
| 8 | westview | West View Plaza + Giant Eagle | 619 |

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
| `GET /predictions?route=13&stop=chalfonte` | Arrival predictions for a route/stop |
| `GET /predictions/multi?stop=westview` | Multi-route predictions (Route 8 + 13) |
| `GET /alerts?route=13` | Service alerts |

### Example Response: `/predictions?route=13&stop=chalfonte`

```json
{
  "stop_name": "Center Ave + Chalfonte Ave",
  "stop_numbers": {"outbound": "1009", "inbound": "1016"},
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
    "to_downtown": {
      "destination": "Downtown Pittsburgh",
      "direction": "INBOUND",
      "arrivals": [
        {"minutes": 3, "time": "06:18 PM", "vehicle_id": "6512", "status": "On Time"}
      ]
    }
  }
}
```

### Example Response: `/predictions/multi?stop=westview`

```json
{
  "stop_name": "West View Plaza + Giant Eagle",
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
| `STOP_ID` | Default stop ID | 1016 |
| `BUS_ROUTE` | Default route | 13 |
| `API_PORT` | Server port | 5001 |
| `FLASK_DEBUG` | Enable debug mode | false |
| `ALLOWED_ORIGINS` | CORS origins (comma-separated) | http://localhost:5001 |

---

Made with ❤️ for Pittsburgh 🏙️
