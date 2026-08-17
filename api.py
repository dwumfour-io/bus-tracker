"""
Pittsburgh Port Authority Bus Tracker API
Fetches real-time arrival predictions for your stop
"""

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import logging
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from google.transit import gtfs_realtime_pb2

# Pittsburgh timezone
EASTERN_TZ = ZoneInfo("America/New_York")

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# CORS - Restrict to allowed origins (add your production domain)
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5001").split(",")
CORS(app, origins=ALLOWED_ORIGINS)

# Rate limiting - prevent API abuse
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["100 per minute"],
    storage_uri="memory://"
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Security headers middleware
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )
    return response


# Configuration - from environment variables
PAAC_API_KEY = os.environ.get("PAAC_API_KEY", "")
TRUETIME_BASE_URL = "https://truetime.portauthority.org/bustime/api/v3"
GTFSRT_TRIPS_URL = "https://truetime.portauthority.org/gtfsrt-bus/trips"
API_PORT = int(os.environ.get("API_PORT", 5001))

# Bus 13 Configuration
BUS_ROUTE = os.environ.get("BUS_ROUTE", "13")
STOP_ID = os.environ.get("STOP_ID", "1016")  # Default to Center Ave + Chalfonte Ave
STOP_NAME = os.environ.get("STOP_NAME", "Center Ave + Chalfonte Ave")
DESTINATION_WEST_VIEW = os.environ.get("DESTINATION_WEST_VIEW", "West View Plaza Fire Lane + Giant Eagle")
DESTINATION_DOWNTOWN = os.environ.get("DESTINATION_DOWNTOWN", "Downtown Pittsburgh")

# Valid routes and stops for input validation
VALID_ROUTES = ['8', '13']

# Stop metadata, including the public stop numbers riders see at the stop.
STOP_CONFIGS = {
    "chalfonte": {
        "name": "Center Ave + Chalfonte Ave",
        "numbers": {"outbound": "1009", "inbound": "1016"},
    },
    "westview": {
        "name": "West View Plaza + Giant Eagle",
        "numbers": {"outbound": "619", "inbound": "619"},
    },
}
VALID_STOPS = list(STOP_CONFIGS.keys())

# Some stops use the same ID for both directions. Others have different
# physical stops depending on travel direction.
PAIRED_STOPS = {key: config["numbers"] for key, config in STOP_CONFIGS.items()}
STOP_ID_TO_KEY = {
    stop_number: stop_key
    for stop_key, config in STOP_CONFIGS.items()
    for stop_number in config["numbers"].values()
}
STOP_NAME_MAP = {
    **{key: config["name"] for key, config in STOP_CONFIGS.items()},
    **{stop_id: STOP_CONFIGS[stop_key]["name"] for stop_id, stop_key in STOP_ID_TO_KEY.items()},
}
DEFAULT_STOP_KEY = STOP_ID_TO_KEY.get(STOP_ID, "chalfonte")

# Route 13 typical headways (minutes between buses) by time of day
# Based on Port Authority schedule patterns
# GTFS-verified headways for Route 13 at Center Ave + Chalfonte (stops 1009/1016)
# Source: Port Authority GTFS Static Data (January 2026)
ROUTE_HEADWAYS = {
    "13": {
        "early": {"start": 5, "end": 6, "headway": 20},     # Early morning: every 20 min
        "peak": {"start": 6, "end": 9, "headway": 20},      # AM rush: every 20 min
        "midday": {"start": 9, "end": 14, "headway": 37},   # Midday: every 37 min
        "pm_peak": {"start": 14, "end": 18, "headway": 20}, # PM rush: every 20 min
        "evening": {"start": 18, "end": 22, "headway": 37}, # Evening: every 37 min
        "night": {"start": 22, "end": 5, "headway": 37},    # Night: every 37-60 min
    },
    "8": {
        "peak": {"start": 6, "end": 9, "headway": 15},
        "midday": {"start": 9, "end": 15, "headway": 25},
        "pm_peak": {"start": 15, "end": 19, "headway": 15},
        "evening": {"start": 19, "end": 23, "headway": 40},
        "night": {"start": 23, "end": 6, "headway": 60},
    }
}


def _resolve_stop_key(stop):
    """Resolve a friendly stop key or public stop number to the app stop key."""
    if stop in STOP_CONFIGS:
        return stop
    return STOP_ID_TO_KEY.get(str(stop))


def _get_stop_name(stop):
    """Get the display name for a friendly stop key or public stop number."""
    stop_key = _resolve_stop_key(stop)
    if stop_key:
        return STOP_CONFIGS[stop_key]["name"]
    return STOP_NAME


def _get_stop_numbers(stop):
    """Get outbound/inbound public stop numbers for a stop."""
    stop_key = _resolve_stop_key(stop)
    if stop_key:
        return dict(STOP_CONFIGS[stop_key]["numbers"])
    selected_stop = str(stop or STOP_ID)
    return {"outbound": selected_stop, "inbound": selected_stop}


def _same_stop_number_label(stop_numbers):
    """Get a single stop number when both directions use the same stop."""
    outbound = stop_numbers.get("outbound")
    inbound = stop_numbers.get("inbound")
    return outbound if outbound == inbound else None


def _get_expected_headway(route):
    """Get expected headway (minutes between buses) based on current time."""
    now = datetime.now(EASTERN_TZ)
    hour = now.hour

    headways = ROUTE_HEADWAYS.get(route, ROUTE_HEADWAYS["13"])

    for period, config in headways.items():
        start = config["start"]
        end = config["end"]

        # Handle overnight period (e.g., 23-6)
        if start > end:
            if hour >= start or hour < end:
                return config["headway"], period
        else:
            if start <= hour < end:
                return config["headway"], period

    return 30, "unknown"  # Default fallback


def _empty_predictions_response(stop_name, route, stop=None):
    """Helper to create an empty predictions response structure."""
    now_label = datetime.now(EASTERN_TZ).strftime("%I:%M:%S %p")
    headway, period = _get_expected_headway(route)
    stop_numbers = _get_stop_numbers(stop)
    single_stop_number = str(stop) if str(stop) in STOP_ID_TO_KEY else _same_stop_number_label(stop_numbers)

    return {
        "stop_name": stop_name,
        "stop_number": single_stop_number,
        "stop_numbers": stop_numbers,
        "route": route,
        "last_updated": now_label,
        "data_source": "truetime",
        "is_live": True,
        "expected_headway": headway,
        "schedule_period": period,
        "predictions": {
            "to_west_view": {
                "destination": DESTINATION_WEST_VIEW,
                "direction": "OUTBOUND",
                "stop_number": stop_numbers["outbound"],
                "arrivals": [],
            },
            "to_downtown": {
                "destination": DESTINATION_DOWNTOWN,
                "direction": "INBOUND",
                "stop_number": stop_numbers["inbound"],
                "arrivals": [],
            },
        },
    }


def _minutes_until(arrival_epoch):
    """Convert epoch seconds into minutes from now (non-negative)."""
    now_epoch = datetime.now(timezone.utc).timestamp()
    minutes = max(0, round((arrival_epoch - now_epoch) / 60))
    return minutes


def _format_status(delay_seconds: int | None) -> str:
    """Turn delay seconds into a short status label."""
    if delay_seconds is None:
        return "On Time"
    minutes = int(delay_seconds / 60)
    if minutes > 0:
        return f"Delayed +{minutes} min"
    if minutes < 0:
        return f"Early {abs(minutes)} min"
    return "On Time"


def get_predictions_truetime(route=None, stop=None):
    """Fetch predictions using TrueTime API (PRIMARY - requires API key)"""
    if not PAAC_API_KEY:
        logger.info("TrueTime: No API key configured, skipping")
        return None

    selected_route = route or BUS_ROUTE
    selected_stop = stop or STOP_ID

    # Don't filter by route in API call - TrueTime sometimes misses results
    # We'll filter by route on our end instead
    params = {
        "key": PAAC_API_KEY,
        "stpid": selected_stop,
        "format": "json",
        "rtpidatafeed": "Port Authority Bus",
        "top": "10"  # Get more results to filter from
    }

    try:
        response = requests.get(f"{TRUETIME_BASE_URL}/getpredictions", params=params, timeout=10)
        if response.status_code == 200:
            # Fix invalid JSON escapes from TrueTime API
            raw_text = response.text.replace('\\-', '-')
            data = json.loads(raw_text)
            # Let _format_truetime_response handle errors (including "No arrival times")
            logger.info("Successfully fetched predictions from TrueTime")
            return _format_truetime_response(data, selected_route, selected_stop)
        else:
            logger.warning(f"TrueTime API returned status {response.status_code}")
    except Exception as e:
        logger.error(f"Error fetching TrueTime predictions: {e}")

    return None


def _format_truetime_response(data, route=None, stop=None):
    """Format TrueTime JSON response into our standard format."""
    selected_route = route or BUS_ROUTE
    selected_stop = stop or STOP_ID

    # Use shared stop metadata so physical stop numbers stay in the response.
    display_stop_name = _get_stop_name(selected_stop)
    stop_numbers = _get_stop_numbers(selected_stop)
    selected_stop_number = str(selected_stop) if str(selected_stop) in STOP_ID_TO_KEY else _same_stop_number_label(stop_numbers)

    try:
        predictions = data.get("bustime-response", {})
        if "error" in predictions:
            error_msg = predictions["error"][0].get("msg", "") if predictions["error"] else ""
            # Handle various "no buses" scenarios - return empty response instead of error
            no_bus_messages = [
                "No arrival times",
                "No service scheduled",
                "no data",
                "No buses"
            ]
            if any(msg.lower() in error_msg.lower() for msg in no_bus_messages):
                return _empty_predictions_response(display_stop_name, selected_route, selected_stop)
            logger.warning(f"TrueTime API error: {predictions['error']}")
            return None

        pred_list = predictions.get("prd", [])
        if not pred_list:
            # No predictions - return empty response
            return _empty_predictions_response(display_stop_name, selected_route, selected_stop)

        to_west_view = []
        to_downtown = []

        for pred in pred_list:
            # Filter by route - only include predictions for the requested route
            pred_route = pred.get("rt", "")
            if selected_route and pred_route and pred_route != selected_route:
                continue

            arrival_time_raw = pred.get("prdtm", "N/A")  # Format: "20260102 23:38"
            vehicle_id = pred.get("vid", "N/A")
            is_delayed = pred.get("dly", False)
            status = "Delayed" if is_delayed else "On Time"

            # Use the countdown from API (already in minutes)
            minutes = int(pred.get("prdctdn", 0))

            # Format time for display (12-hour format)
            try:
                arr_dt = datetime.strptime(arrival_time_raw, "%Y%m%d %H:%M")
                arrival_time_display = arr_dt.strftime("%I:%M %p")
            except Exception as e:
                logger.warning(f"Error parsing time {arrival_time_raw}: {e}")
                arrival_time_display = arrival_time_raw

            record = {
                "minutes": minutes,
                "time": arrival_time_display,
                "vehicle_id": vehicle_id,
                "status": status,
            }

            direction = pred.get("rtdir", "OUTBOUND").upper()
            if "INBOUND" in direction:
                to_downtown.append(record)
            else:
                to_west_view.append(record)

        to_west_view.sort(key=lambda r: r["minutes"])
        to_downtown.sort(key=lambda r: r["minutes"])

        if not to_west_view and not to_downtown:
            # No arrivals in either direction - return empty response
            return _empty_predictions_response(display_stop_name, selected_route, selected_stop)

        now_label = datetime.now(EASTERN_TZ).strftime("%I:%M:%S %p")

        return {
            "stop_name": display_stop_name,
            "stop_number": selected_stop_number,
            "stop_numbers": stop_numbers,
            "route": selected_route,
            "last_updated": now_label,
            "data_source": "truetime",
            "is_live": True,
            "predictions": {
                "to_west_view": {
                    "destination": DESTINATION_WEST_VIEW,
                    "direction": "OUTBOUND",
                    "stop_number": selected_stop_number or stop_numbers["outbound"],
                    "arrivals": to_west_view,
                },
                "to_downtown": {
                    "destination": DESTINATION_DOWNTOWN,
                    "direction": "INBOUND",
                    "stop_number": selected_stop_number or stop_numbers["inbound"],
                    "arrivals": to_downtown,
                },
            },
        }
    except Exception as e:
        logger.error(f"Error formatting TrueTime response: {e}")
        return None


def get_predictions_gtfsrt(route=None, stop=None):
    """Fetch predictions from GTFS-Realtime TripUpdates feed (FALLBACK).

    This uses the Port Authority GTFS-RT feed which provides real-time
    arrival predictions directly without requiring an API key.
    """
    try:
        stop = stop or DEFAULT_STOP_KEY
        route = route or BUS_ROUTE
        stop_key = _resolve_stop_key(stop) or DEFAULT_STOP_KEY
        stop_config = PAIRED_STOPS[stop_key]
        stop_name = _get_stop_name(stop_key)
        stop_numbers = _get_stop_numbers(stop_key)
        single_stop_number = _same_stop_number_label(stop_numbers)

        outbound_stop_id = stop_config["outbound"]
        inbound_stop_id = stop_config["inbound"]

        logger.info(f"GTFS-RT: Fetching predictions for route {route}, stops {outbound_stop_id}/{inbound_stop_id}")

        # Fetch GTFS-RT protobuf feed
        response = requests.get(GTFSRT_TRIPS_URL, timeout=10)
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        now = datetime.now(EASTERN_TZ)
        now_label = now.strftime("%I:%M:%S %p")
        now_epoch = datetime.now(timezone.utc).timestamp()

        outbound_arrivals = []
        inbound_arrivals = []

        # Parse each trip entity
        for entity in feed.entity:
            if not entity.HasField('trip_update'):
                continue

            trip = entity.trip_update.trip

            # Filter by route
            if trip.route_id != route:
                continue

            # Check each stop time update for our stops
            for stu in entity.trip_update.stop_time_update:
                stop_id = stu.stop_id

                if stop_id not in [outbound_stop_id, inbound_stop_id]:
                    continue

                # Get arrival time
                arrival_time = stu.arrival.time if stu.HasField('arrival') else None
                if not arrival_time:
                    continue

                # Skip if arrival is in the past
                if arrival_time < now_epoch:
                    continue

                # Calculate minutes until arrival
                minutes = max(0, round((arrival_time - now_epoch) / 60))

                # Format arrival time
                arrival_dt = datetime.fromtimestamp(arrival_time, tz=EASTERN_TZ)
                arrival_str = arrival_dt.strftime("%I:%M %p").lstrip("0")

                arrival_data = {
                    "minutes": minutes,
                    "arrival_time": arrival_str,
                    "is_delayed": False,
                    "status": "On Time",
                    "stop_number": stop_id,
                    "vehicle_id": entity.trip_update.vehicle.id if entity.trip_update.HasField('vehicle') else None,
                }

                if stop_id == outbound_stop_id:
                    outbound_arrivals.append(arrival_data)
                elif stop_id == inbound_stop_id:
                    inbound_arrivals.append(arrival_data)

        # Sort by minutes and limit to 5 per direction
        outbound_arrivals.sort(key=lambda x: x["minutes"])
        inbound_arrivals.sort(key=lambda x: x["minutes"])
        outbound_arrivals = outbound_arrivals[:5]
        inbound_arrivals = inbound_arrivals[:5]

        logger.info(f"GTFS-RT: Found {len(outbound_arrivals)} outbound, {len(inbound_arrivals)} inbound arrivals")

        headway, period = _get_expected_headway(route)

        return {
            "stop_name": stop_name,
            "stop_number": single_stop_number,
            "stop_numbers": stop_numbers,
            "route": route,
            "last_updated": now_label,
            "data_source": "gtfs-rt",
            "is_live": True,
            "expected_headway": headway,
            "schedule_period": period,
            "predictions": {
                "to_west_view": {
                    "destination": DESTINATION_WEST_VIEW,
                    "direction": "OUTBOUND",
                    "stop_number": outbound_stop_id,
                    "arrivals": outbound_arrivals,
                },
                "to_downtown": {
                    "destination": DESTINATION_DOWNTOWN,
                    "direction": "INBOUND",
                    "stop_number": inbound_stop_id,
                    "arrivals": inbound_arrivals,
                },
            },
        }

    except Exception as e:
        logger.error(f"GTFS-RT error: {e}")
        return None


def get_predictions_with_fallback(route=None, stop=None):
    """Try TrueTime API first, then fall back to GTFS-RT."""
    # Try TrueTime API first (requires API key)
    truetime_data = get_predictions_truetime(route, stop)
    if truetime_data:
        return truetime_data

    # Fall back to GTFS-RT (no API key needed)
    logger.info("TrueTime failed, trying GTFS-RT fallback...")
    gtfsrt_data = get_predictions_gtfsrt(route, stop)
    if gtfsrt_data:
        return gtfsrt_data

    # Return error response if both fail
    logger.error("Both TrueTime and GTFS-RT failed")
    return None


@app.route("/")
def home():
    """Serve the main HTML page"""
    return send_file('index.html')


@app.route("/style.css")
def serve_css():
    """Serve the CSS file"""
    return send_file('style.css', mimetype='text/css')


@app.route("/app.js")
def serve_js():
    """Serve the JavaScript file"""
    return send_file('app.js', mimetype='application/javascript')


@app.route("/manifest.json")
def serve_manifest():
    """Serve the PWA manifest"""
    return send_file('manifest.json', mimetype='application/json')


@app.route("/service-worker.js")
def serve_service_worker():
    """Serve the service worker"""
    return send_file('service-worker.js', mimetype='application/javascript')


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


def _build_predictions_payload(route, requested_stop):
    """Build the combined prediction payload for a route and stop."""
    stop = _resolve_stop_key(requested_stop)

    # Validate route parameter
    if route not in VALID_ROUTES:
        return {
            "error": f"Invalid route '{route}'. Valid routes: {', '.join(VALID_ROUTES)}"
        }, 400

    # Validate stop parameter
    if not stop:
        valid_stop_numbers = sorted(STOP_ID_TO_KEY.keys())
        return {
            "error": (
                f"Invalid stop '{requested_stop}'. Valid stops: {', '.join(VALID_STOPS)} "
                f"or stop numbers: {', '.join(valid_stop_numbers)}"
            )
        }, 400

    # All stops use paired logic to fetch both directions
    paired = PAIRED_STOPS[stop]
    outbound_stop = paired["outbound"]
    inbound_stop = paired["inbound"]

    # Fetch from both stops (may be same ID for some stops)
    outbound_data = get_predictions_with_fallback(route, outbound_stop)
    inbound_data = get_predictions_with_fallback(route, inbound_stop) if inbound_stop != outbound_stop else outbound_data

    # Get expected headway for schedule info
    headway, period = _get_expected_headway(route)

    # Combine results
    now_label = datetime.now(EASTERN_TZ).strftime("%I:%M:%S %p")
    stop_numbers = _get_stop_numbers(stop)
    westview_predictions = (
        dict(outbound_data["predictions"]["to_west_view"])
        if outbound_data
        else {"destination": DESTINATION_WEST_VIEW, "direction": "OUTBOUND", "arrivals": []}
    )
    downtown_predictions = (
        dict(inbound_data["predictions"]["to_downtown"])
        if inbound_data
        else {"destination": DESTINATION_DOWNTOWN, "direction": "INBOUND", "arrivals": []}
    )
    westview_predictions["stop_number"] = stop_numbers["outbound"]
    downtown_predictions["stop_number"] = stop_numbers["inbound"]

    return {
        "stop_name": STOP_NAME_MAP.get(stop, "Unknown Stop"),
        "stop_number": _same_stop_number_label(stop_numbers),
        "stop_numbers": stop_numbers,
        "route": route,
        "last_updated": now_label,
        "data_source": "truetime",
        "is_live": True,
        "expected_headway": headway,
        "schedule_period": period,
        "predictions": {
            "to_west_view": westview_predictions,
            "to_downtown": downtown_predictions
        }
    }, 200


@app.route("/predictions")
def get_all_predictions():
    """Get all bus arrival predictions for both directions"""
    try:
        route = request.args.get('route', BUS_ROUTE)
        requested_stop = request.args.get('stop', DEFAULT_STOP_KEY)
        payload, status = _build_predictions_payload(route, requested_stop)
        return jsonify(payload), status
    except Exception as e:
        logger.error(f"Error in /predictions endpoint: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/predictions/westview")
def get_westview_predictions():
    """Get West View-bound arrivals for backwards-compatible API clients."""
    try:
        route = request.args.get('route', BUS_ROUTE)
        requested_stop = request.args.get('stop', DEFAULT_STOP_KEY)
        payload, status = _build_predictions_payload(route, requested_stop)
        if status != 200:
            return jsonify(payload), status
        payload["predictions"] = {"to_west_view": payload["predictions"]["to_west_view"]}
        return jsonify(payload), status
    except Exception as e:
        logger.error(f"Error in /predictions/westview endpoint: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/predictions/downtown")
def get_downtown_predictions():
    """Get downtown-bound arrivals for backwards-compatible API clients."""
    try:
        route = request.args.get('route', BUS_ROUTE)
        requested_stop = request.args.get('stop', DEFAULT_STOP_KEY)
        payload, status = _build_predictions_payload(route, requested_stop)
        if status != 200:
            return jsonify(payload), status
        payload["predictions"] = {"to_downtown": payload["predictions"]["to_downtown"]}
        return jsonify(payload), status
    except Exception as e:
        logger.error(f"Error in /predictions/downtown endpoint: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/predictions/multi")
def get_multi_route_predictions():
    """Get predictions for multiple routes at West View Plaza.
    Both Route 8 and Route 13 serve West View and go downtown.
    """
    try:
        stop = request.args.get('stop', 'westview')

        if stop != 'westview':
            return jsonify({"error": "Multi-route only available for westview"}), 400

        # Fetch predictions for both routes at West View
        # Use the full prediction flow which handles stop ID lookup
        route_8_data = get_predictions_truetime('8', '619')
        route_13_data = get_predictions_truetime('13', '619')

        # If TrueTime fails, try GTFS-RT
        if not route_8_data:
            route_8_data = get_predictions_gtfsrt('8', 'westview')
        if not route_13_data:
            route_13_data = get_predictions_gtfsrt('13', 'westview')

        now_label = datetime.now(EASTERN_TZ).strftime("%I:%M:%S %p")

        # For West View terminus: buses arrive OUTBOUND (from downtown)
        # and then turn around to go INBOUND (to downtown)
        # So for catching a downtown-bound bus at West View, we show OUTBOUND arrivals
        # because these are the buses arriving that will then go downtown
        downtown_arrivals = []

        # Use outbound arrivals since this is a terminus
        if route_8_data and route_8_data.get("predictions", {}).get("to_west_view", {}).get("arrivals"):
            for arr in route_8_data["predictions"]["to_west_view"]["arrivals"]:
                arr["route"] = "8"
                downtown_arrivals.append(arr)

        if route_13_data and route_13_data.get("predictions", {}).get("to_west_view", {}).get("arrivals"):
            for arr in route_13_data["predictions"]["to_west_view"]["arrivals"]:
                arr["route"] = "13"
                downtown_arrivals.append(arr)

        # Sort by minutes
        downtown_arrivals.sort(key=lambda x: x.get("minutes", 999))

        # Get expected headways for both routes
        headway_8, period_8 = _get_expected_headway("8")
        headway_13, period_13 = _get_expected_headway("13")

        # Use the shorter headway for display
        combined_headway = min(headway_8, headway_13)
        stop_numbers = _get_stop_numbers("westview")

        return jsonify({
            "stop_name": "West View Plaza + Giant Eagle",
            "stop_number": _same_stop_number_label(stop_numbers),
            "stop_numbers": stop_numbers,
            "routes": ["8", "13"],
            "last_updated": now_label,
            "data_source": route_8_data.get("data_source") if route_8_data else "truetime",
            "is_live": True,
            "expected_headway": combined_headway,
            "schedule_period": period_8,
            "is_terminus": True,
            "predictions": {
                "to_west_view": {
                    "destination": DESTINATION_WEST_VIEW,
                    "direction": "OUTBOUND",
                    "stop_number": stop_numbers["outbound"],
                    "arrivals": [],  # Not shown at terminus
                    "is_terminus": True
                },
                "to_downtown": {
                    "destination": DESTINATION_DOWNTOWN,
                    "direction": "INBOUND",
                    "stop_number": stop_numbers["inbound"],
                    "arrivals": downtown_arrivals[:10],  # Limit to 10
                    "note": "Buses arriving at terminus - board to go downtown"
                }
            }
        })

    except Exception as e:
        logger.error(f"Error in /predictions/multi: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/alerts")
def get_service_alerts():
    """Get service bulletins/alerts from Port Authority"""
    try:
        route = request.args.get('route', BUS_ROUTE)

        params = {
            "key": PAAC_API_KEY,
            "rt": route,
            "format": "json",
            "rtpidatafeed": "Port Authority Bus"
        }

        response = requests.get(f"{TRUETIME_BASE_URL}/getservicebulletins", params=params, timeout=10)

        if response.status_code == 200:
            raw_text = response.text.replace('\\-', '-')
            data = json.loads(raw_text)

            bulletins = data.get("bustime-response", {}).get("sb", [])

            alerts = []
            for bulletin in bulletins:
                alerts.append({
                    "title": bulletin.get("nm", "Service Alert"),
                    "subject": bulletin.get("sbj", ""),
                    "detail": bulletin.get("dtl", "").strip(),
                    "brief": bulletin.get("brf", "").strip(),
                    "priority": bulletin.get("prty", "Low"),
                    "modified": bulletin.get("mod", ""),
                    "url": bulletin.get("url", "")
                })

            return jsonify({
                "route": route,
                "alerts": alerts,
                "count": len(alerts),
                "last_updated": datetime.now(EASTERN_TZ).strftime("%I:%M:%S %p")
            })
        else:
            logger.warning(f"Service bulletins API returned status {response.status_code}")
            return jsonify({"route": route, "alerts": [], "count": 0})

    except Exception as e:
        logger.error(f"Error fetching service alerts: {e}")
        return jsonify({"route": route, "alerts": [], "count": 0, "error": "Service temporarily unavailable"})


if __name__ == "__main__":
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    print("🚌 Pittsburgh Bus 13 Tracker API")
    print(f"📍 Stop: {STOP_NAME}")
    print(f"🚐 Route: {BUS_ROUTE}")
    print(f"🔗 Running at http://localhost:{API_PORT}")
    print(f"🐛 Debug mode: {'ON' if debug_mode else 'OFF'}")
    if PAAC_API_KEY:
        print("✅ TrueTime API key configured (primary data source)")
    else:
        print("⚠️  No TrueTime API key - using GTFS-RT fallback")
    app.run(debug=debug_mode, host='0.0.0.0', port=API_PORT)
