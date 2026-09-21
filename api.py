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
from datetime import datetime, timedelta, timezone
import os
import logging
import time
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from google.transit import gtfs_realtime_pb2
from gtfs_static import load_gtfs
import history_store

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
TRUETIME_STOPS_URL = f"{TRUETIME_BASE_URL}/getstops"
API_PORT = int(os.environ.get("API_PORT", 5001))
STOP_METADATA_CACHE_TTL = 3600
_stop_metadata_cache = {"expires_at": 0, "names": {}}
GTFSRT_CACHE_TTL = 15
_gtfsrt_feed_cache = {"expires_at": 0, "feed": None}
STATIC_GTFS = load_gtfs()

# Optional automatic static-schedule refresh. Off unless a feed URL is
# configured, since we won't guess PRT's download URL for you - grab the
# current one from https://www.rideprt.org/business-center/developer-resources/
GTFS_STATIC_FEED_URL = os.environ.get("GTFS_STATIC_FEED_URL", "")
GTFS_REFRESH_CHECK_INTERVAL = 24 * 60 * 60  # look for a newer feed once a day
GTFS_EXPIRY_WARNING_DAYS = 14
_gtfs_refresh_state = {"checked_at": 0}

# A scheduled-only trip more than this far out isn't a useful arrival card -
# the service_status message covers "next bus" beyond this window instead.
SCHEDULED_ARRIVAL_HORIZON_HOURS = 2

# Where we log prediction snapshots and rider-reported arrivals (item 6).
HISTORY_DB_PATH = os.environ.get("HISTORY_DB_PATH", "data/history.db")
history_store.init_db(HISTORY_DB_PATH)
_last_snapshot_at = {}
SNAPSHOT_MIN_INTERVAL_SECONDS = 25

# Bus 13 Configuration
BUS_ROUTE = os.environ.get("BUS_ROUTE", "13")
STOP_ID = os.environ.get("STOP_ID", "1009")
STOP_NAME = os.environ.get("STOP_NAME", "Center Ave + Chalfonte Ave")
DESTINATION_WEST_VIEW = os.environ.get("DESTINATION_WEST_VIEW", "West View Plaza Fire Lane + Giant Eagle")
DESTINATION_DOWNTOWN = os.environ.get("DESTINATION_DOWNTOWN", "Downtown Pittsburgh")

# Valid routes and stops for input validation
VALID_ROUTES = ['8', '13']

# Stop metadata, including the public stop numbers riders see at the stop.
STOP_CONFIGS = {
    "stop_1009": {
        "name": "Center Ave + Chalfonte Ave",
        "names": {"outbound": "Center Ave + Chalfonte Ave"},
        "numbers": {"outbound": "1009", "inbound": "1009"},
        "directions": ["to_west_view"],
    },
    "stop_1016": {
        "name": "Center Ave + Chalfonte Ave",
        "names": {"inbound": "Center Ave + Chalfonte Ave"},
        "numbers": {"outbound": "1016", "inbound": "1016"},
        "directions": ["to_downtown"],
    },
    "westview": {
        "name": "West View Plaza Fire Lane + Giant Eagle",
        "numbers": {"outbound": "619", "inbound": "619"},
        "directions": ["to_west_view", "to_downtown"],
    },
    "stop_620": {
        "name": "West View Plaza Fire Lane + U-Haul",
        "numbers": {"outbound": "620", "inbound": "620"},
        "directions": ["to_west_view", "to_downtown"],
    },
    "stop_618": {
        "name": "West View Park Dr + West View Towers",
        "numbers": {"outbound": "618", "inbound": "618"},
        "directions": ["to_west_view"],
    },
    "stop_733": {
        "name": "West View Park Dr + West View Tower",
        "numbers": {"outbound": "733", "inbound": "733"},
        "directions": ["to_downtown"],
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
DEFAULT_STOP_KEY = STOP_ID_TO_KEY.get(STOP_ID, "stop_1009")

# Route 13 typical headways (minutes between buses) by time of day
# Based on Port Authority schedule patterns
# GTFS-verified headways for Route 13 at Center Ave + Chalfonte (stops 1009/1016)
# Source: Port Authority GTFS Static Data (January 2026)
ROUTE_HEADWAYS = {
    "13": {
        "early": {"start": 5, "end": 6, "headway": 20},     # Early morning: every 20 min
        "peak": {"start": 6, "end": 9, "headway": 20},      # AM rush: every 20 min
        "midday": {"start": 9, "end": 14, "headway": 37},   # Midday: every 37 min
        "pm_peak": {"start": 14, "end": 18, "headway": 20},  # PM rush: every 20 min
        "evening": {"start": 18, "end": 22, "headway": 37},  # Evening: every 37 min
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


def _get_stop_entries(stop, official_names=None):
    """Return the physical stop records represented by a configured stop."""
    stop_key = _resolve_stop_key(stop)
    config = STOP_CONFIGS.get(stop_key, {})
    stop_numbers = _get_stop_numbers(stop)
    stop_name = _get_stop_name(stop)
    direction_names = config.get("names", {})
    official_names = official_names or {}
    entries = [
        {
            "id": stop_numbers["outbound"],
            "name": official_names.get(
                stop_numbers["outbound"],
                direction_names.get("outbound", stop_name),
            ),
            "direction": "OUTBOUND",
        },
    ]
    if stop_numbers["inbound"] != stop_numbers["outbound"]:
        entries.append({
            "id": stop_numbers["inbound"],
            "name": official_names.get(
                stop_numbers["inbound"],
                direction_names.get("inbound", stop_name),
            ),
            "direction": "INBOUND",
        })
    else:
        directions = config.get("directions", [])
        if directions == ["to_west_view"]:
            entries[0]["direction"] = "OUTBOUND"
        elif directions == ["to_downtown"]:
            entries[0]["direction"] = "INBOUND"
        else:
            entries[0]["direction"] = "BOTH"
    return entries


def _get_stop_directions(stop):
    """Return supported direction keys for a logical stop or public ID."""
    stop_key = _resolve_stop_key(stop)
    return STOP_CONFIGS.get(stop_key, {}).get(
        "directions", ["to_west_view", "to_downtown"]
    )


def _get_official_stop_names(route, stop_ids):
    """Fetch official stop names without making metadata a prediction dependency."""
    requested_ids = {str(stop_id) for stop_id in stop_ids}
    configured_names = {}
    for stop_id in requested_ids:
        stop_key = STOP_ID_TO_KEY.get(stop_id)
        config = STOP_CONFIGS.get(stop_key, {})
        numbers = config.get("numbers", {})
        names = config.get("names", {})
        if stop_id == numbers.get("outbound"):
            configured_names[stop_id] = names.get("outbound", config.get("name"))
        elif stop_id == numbers.get("inbound"):
            configured_names[stop_id] = names.get("inbound", config.get("name"))

    missing_ids = requested_ids - configured_names.keys()
    if not missing_ids:
        return configured_names

    now = time.time()
    if now < _stop_metadata_cache["expires_at"]:
        cached_names = {
            stop_id: name
            for stop_id, name in _stop_metadata_cache["names"].items()
            if stop_id in missing_ids
        }
        return {**configured_names, **cached_names}

    if not PAAC_API_KEY:
        static_names = {
            stop_id: STATIC_GTFS.stop_name(stop_id)
            for stop_id in missing_ids
            if STATIC_GTFS.stop_name(stop_id)
        }
        return {**configured_names, **static_names}

    try:
        response = requests.get(
            TRUETIME_STOPS_URL,
            params={
                "key": PAAC_API_KEY,
                "rt": route,
                "format": "json",
                "rtpidatafeed": "Port Authority Bus",
            },
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json().get("bustime-response", {})
        if payload.get("error"):
            return {}

        stop_records = payload.get("stops", payload.get("stp", []))
        names = {
            str(record.get("stpid")): record.get("stpnm")
            for record in stop_records
            if record.get("stpid") and record.get("stpnm")
        }
        _stop_metadata_cache.update({
            "expires_at": now + STOP_METADATA_CACHE_TTL,
            "names": names,
        })
        api_names = {stop_id: names[stop_id] for stop_id in missing_ids if stop_id in names}
        return {**configured_names, **api_names}
    except (requests.RequestException, ValueError, AttributeError) as error:
        logger.warning("Stop metadata lookup unavailable: %s", error)
        return configured_names


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
        "directions": _get_stop_directions(stop),
        "stops": _get_stop_entries(stop),
        "route": route,
        "last_updated": now_label,
        "data_source": "truetime",
        "is_live": False,
        "expected_headway": headway,
        "schedule_period": period,
        "predictions": {
            "to_west_view": {
                "destination": DESTINATION_WEST_VIEW,
                "direction": "OUTBOUND",
                "stop_name": stop_name,
                "stop_number": stop_numbers["outbound"],
                "arrivals": [],
            },
            "to_downtown": {
                "destination": DESTINATION_DOWNTOWN,
                "direction": "INBOUND",
                "stop_name": stop_name,
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
    """Turn delay seconds into a short status label.

    A missing delay reading is not the same thing as an on-time bus - we
    just couldn't match this prediction to a scheduled trip to measure it.
    """
    if delay_seconds is None:
        return "Delay unknown"
    minutes = round(delay_seconds / 60)
    if minutes > 0:
        return f"Delayed +{minutes} min"
    if minutes < 0:
        return f"Early {abs(minutes)} min"
    return "On Time"


def _parse_truetime_timestamp(value):
    """Parse TrueTime's 'tmstmp' (prediction-generated-at) field, if present."""
    if not value:
        return None
    for fmt in ("%Y%m%d %H:%M:%S", "%Y%m%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=EASTERN_TZ)
        except ValueError:
            continue
    return None


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
        latest_source_dt = None

        for pred in pred_list:
            # Filter by route - only include predictions for the requested route
            pred_route = pred.get("rt", "")
            if selected_route and pred_route and pred_route != selected_route:
                continue

            arrival_time_raw = pred.get("prdtm", "N/A")  # Format: "20260102 23:38"
            vehicle_id = pred.get("vid", "N/A")
            is_delayed = pred.get("dly", False)
            prediction_type = "scheduled" if pred.get("typ") == "S" else "live"
            status = "Scheduled" if prediction_type == "scheduled" else (
                "Delayed" if is_delayed else "On Time"
            )

            generated_at = _parse_truetime_timestamp(pred.get("tmstmp"))
            if generated_at and (latest_source_dt is None or generated_at > latest_source_dt):
                latest_source_dt = generated_at

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
                "is_live": prediction_type == "live",
                "prediction_type": prediction_type,
                "delay_seconds": None,
                "delay_minutes": None,
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
        source_age_seconds = (
            round((datetime.now(EASTERN_TZ) - latest_source_dt).total_seconds())
            if latest_source_dt else None
        )

        return {
            "stop_name": display_stop_name,
            "stop_number": selected_stop_number,
            "stop_numbers": stop_numbers,
            "directions": _get_stop_directions(selected_stop),
            "stops": _get_stop_entries(selected_stop),
            "route": selected_route,
            "last_updated": now_label,
            "data_source": "truetime",
            "source_age_seconds": source_age_seconds,
            "is_live": any(
                arrival["is_live"] for arrival in to_west_view + to_downtown
            ),
            "predictions": {
                "to_west_view": {
                    "destination": DESTINATION_WEST_VIEW,
                    "direction": "OUTBOUND",
                    "stop_name": display_stop_name,
                    "stop_number": selected_stop_number or stop_numbers["outbound"],
                    "arrivals": to_west_view,
                },
                "to_downtown": {
                    "destination": DESTINATION_DOWNTOWN,
                    "direction": "INBOUND",
                    "stop_name": display_stop_name,
                    "stop_number": selected_stop_number or stop_numbers["inbound"],
                    "arrivals": to_downtown,
                },
            },
        }
    except Exception as e:
        logger.error(f"Error formatting TrueTime response: {e}")
        return None


def _get_gtfsrt_feed():
    """Fetch and briefly cache the shared GTFS-RT feed."""
    now = time.time()
    if _gtfsrt_feed_cache["feed"] is not None and now < _gtfsrt_feed_cache["expires_at"]:
        return _gtfsrt_feed_cache["feed"]

    response = requests.get(GTFSRT_TRIPS_URL, timeout=10)
    response.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)
    _gtfsrt_feed_cache.update({
        "expires_at": now + GTFSRT_CACHE_TTL,
        "feed": feed,
    })
    return feed


def _physical_stop_directions(stop):
    """Map the requested physical stop IDs to app direction keys."""
    selected_stop = str(stop) if str(stop) in STOP_ID_TO_KEY else None
    stop_key = _resolve_stop_key(stop) or DEFAULT_STOP_KEY
    numbers = _get_stop_numbers(stop_key)
    mapping = {}
    if not selected_stop or selected_stop == numbers["outbound"]:
        mapping.setdefault(numbers["outbound"], []).append("to_west_view")
    if not selected_stop or selected_stop == numbers["inbound"]:
        mapping.setdefault(numbers["inbound"], []).append("to_downtown")
    return stop_key, mapping


def _trip_direction_key(trip_id):
    """Translate the static GTFS direction ID into an app direction key."""
    return "to_downtown" if STATIC_GTFS.trip_direction(trip_id) == "1" else "to_west_view"


def _arrival_direction(stop_id, trip_id, stop_directions):
    choices = stop_directions.get(stop_id, [])
    if len(choices) == 1:
        return choices[0]
    trip_direction = _trip_direction_key(trip_id)
    return trip_direction if trip_direction in choices else (choices[0] if choices else None)


def get_predictions_gtfsrt(route=None, stop=None):
    """Fetch predictions from GTFS-Realtime TripUpdates feed (FALLBACK).

    This uses the Port Authority GTFS-RT feed which provides real-time
    arrival predictions directly without requiring an API key.
    """
    try:
        stop = stop or DEFAULT_STOP_KEY
        route = route or BUS_ROUTE
        stop_key, stop_directions = _physical_stop_directions(stop)
        stop_name = _get_stop_name(stop_key)
        stop_numbers = _get_stop_numbers(stop_key)
        single_stop_number = _same_stop_number_label(stop_numbers)

        requested_stop_ids = set(stop_directions)

        logger.info(
            "GTFS-RT: Reading predictions for route %s, stops %s",
            route,
            "/".join(sorted(requested_stop_ids)),
        )

        feed = _get_gtfsrt_feed()

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

                if stop_id not in requested_stop_ids:
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

                scheduled_dt = STATIC_GTFS.scheduled_datetime_for_prediction(
                    trip.trip_id,
                    stop_id,
                    arrival_dt,
                )
                delay_seconds = None
                if scheduled_dt:
                    delay_seconds = round((arrival_dt - scheduled_dt).total_seconds())

                arrival_data = {
                    "minutes": minutes,
                    "arrival_time": arrival_str,
                    "time": arrival_str,
                    "scheduled_time": (
                        scheduled_dt.strftime("%I:%M %p").lstrip("0")
                        if scheduled_dt else None
                    ),
                    "is_delayed": delay_seconds is not None and delay_seconds >= 60,
                    "status": _format_status(delay_seconds),
                    "delay_seconds": delay_seconds,
                    "delay_minutes": (
                        round(delay_seconds / 60) if delay_seconds is not None else None
                    ),
                    "stop_number": stop_id,
                    "vehicle_id": entity.trip_update.vehicle.id if entity.trip_update.HasField('vehicle') else None,
                    "trip_id": trip.trip_id,
                    "is_live": True,
                    "prediction_type": "live",
                }

                direction_key = _arrival_direction(stop_id, trip.trip_id, stop_directions)
                if direction_key == "to_west_view":
                    outbound_arrivals.append(arrival_data)
                elif direction_key == "to_downtown":
                    inbound_arrivals.append(arrival_data)

        # Sort by minutes and limit to 5 per direction
        outbound_arrivals.sort(key=lambda x: x["minutes"])
        inbound_arrivals.sort(key=lambda x: x["minutes"])
        outbound_arrivals = outbound_arrivals[:5]
        inbound_arrivals = inbound_arrivals[:5]

        logger.info(f"GTFS-RT: Found {len(outbound_arrivals)} outbound, {len(inbound_arrivals)} inbound arrivals")

        headway, period = _get_expected_headway(route)
        feed_age_seconds = (
            max(0, round(now_epoch - feed.header.timestamp))
            if feed.header.timestamp else None
        )

        return {
            "stop_name": stop_name,
            "stop_number": single_stop_number,
            "stop_numbers": stop_numbers,
            "route": route,
            "last_updated": now_label,
            "data_source": "gtfs-rt",
            "source_age_seconds": feed_age_seconds,
            "is_live": bool(outbound_arrivals or inbound_arrivals),
            "expected_headway": headway,
            "schedule_period": period,
            "predictions": {
                "to_west_view": {
                    "destination": DESTINATION_WEST_VIEW,
                    "direction": "OUTBOUND",
                    "stop_number": stop_numbers["outbound"],
                    "arrivals": outbound_arrivals,
                },
                "to_downtown": {
                    "destination": DESTINATION_DOWNTOWN,
                    "direction": "INBOUND",
                    "stop_number": stop_numbers["inbound"],
                    "arrivals": inbound_arrivals,
                },
            },
        }

    except Exception as e:
        logger.error(f"GTFS-RT error: {e}")
        return None


def get_predictions_static(route=None, stop=None):
    """Return upcoming scheduled service when no live prediction is available.

    Capped to a 2-hour horizon - a scheduled trip 5 hours out isn't a useful
    "arrival" card. Anything farther out is left to the service_status
    message instead (see _direction_service_status).
    """
    if not STATIC_GTFS.available:
        return None

    selected_route = route or BUS_ROUTE
    selected_stop = stop or DEFAULT_STOP_KEY
    stop_key, stop_directions = _physical_stop_directions(selected_stop)
    stop_numbers = _get_stop_numbers(stop_key)
    now = datetime.now(EASTERN_TZ)
    arrivals = {"to_west_view": [], "to_downtown": []}

    for stop_id in stop_directions:
        for scheduled in STATIC_GTFS.upcoming_arrivals(
            selected_route, stop_id, now, limit=5, horizon_hours=SCHEDULED_ARRIVAL_HORIZON_HOURS
        ):
            direction_key = _arrival_direction(
                stop_id,
                scheduled["trip_id"],
                stop_directions,
            )
            if not direction_key:
                continue
            scheduled_dt = scheduled["scheduled_datetime"]
            arrivals[direction_key].append({
                "minutes": scheduled["minutes"],
                "time": scheduled_dt.strftime("%I:%M %p").lstrip("0"),
                "scheduled_time": scheduled_dt.strftime("%I:%M %p").lstrip("0"),
                "vehicle_id": None,
                "trip_id": scheduled["trip_id"],
                "status": "Scheduled",
                "is_live": False,
                "prediction_type": "scheduled",
                "delay_seconds": None,
                "delay_minutes": None,
                "stop_number": stop_id,
            })

    for direction_arrivals in arrivals.values():
        direction_arrivals.sort(key=lambda item: item["minutes"])
        del direction_arrivals[5:]

    headway, period = _get_expected_headway(selected_route)
    return {
        "stop_name": _get_stop_name(stop_key),
        "stop_number": _same_stop_number_label(stop_numbers),
        "stop_numbers": stop_numbers,
        "route": selected_route,
        "last_updated": now.strftime("%I:%M:%S %p"),
        "data_source": "gtfs-static",
        "is_live": False,
        "feed_valid_through": (
            STATIC_GTFS.valid_through.isoformat() if STATIC_GTFS.valid_through else None
        ),
        "expected_headway": headway,
        "schedule_period": period,
        "predictions": {
            "to_west_view": {
                "destination": DESTINATION_WEST_VIEW,
                "direction": "OUTBOUND",
                "stop_number": stop_numbers["outbound"],
                "arrivals": arrivals["to_west_view"],
            },
            "to_downtown": {
                "destination": DESTINATION_DOWNTOWN,
                "direction": "INBOUND",
                "stop_number": stop_numbers["inbound"],
                "arrivals": arrivals["to_downtown"],
            },
        },
    }


def _has_arrivals(data):
    if not data:
        return False
    return any(
        direction.get("arrivals")
        for direction in data.get("predictions", {}).values()
    )


def _merge_live_and_scheduled(live_data, static_data, tolerance_minutes=7):
    """Keep scheduled trips visible even once a live source has other arrivals.

    A missing live prediction should never read as "canceled" - if the
    static schedule expects a bus and no live arrival is close to it in
    time, we keep the scheduled entry and mark it as such instead of
    silently dropping it.
    """
    if not static_data:
        return live_data
    if not live_data:
        return static_data

    merged = dict(live_data)
    merged_predictions = {}
    added_scheduled = False

    for direction in ("to_west_view", "to_downtown"):
        live_direction = dict(live_data.get("predictions", {}).get(direction, {}))
        static_direction = static_data.get("predictions", {}).get(direction, {})
        live_arrivals = list(live_direction.get("arrivals", []))
        scheduled_arrivals = static_direction.get("arrivals", [])

        claimed = set()
        extra_scheduled = []
        for scheduled in scheduled_arrivals:
            match_index = next(
                (
                    index for index, live in enumerate(live_arrivals)
                    if index not in claimed
                    and abs(live["minutes"] - scheduled["minutes"]) <= tolerance_minutes
                ),
                None,
            )
            if match_index is not None:
                claimed.add(match_index)
                continue
            extra_scheduled.append({
                **scheduled,
                "note": "Scheduled · live tracking unavailable",
            })

        if extra_scheduled:
            added_scheduled = True

        combined = sorted(live_arrivals + extra_scheduled, key=lambda item: item["minutes"])
        live_direction["arrivals"] = combined[:6]
        merged_predictions[direction] = live_direction

    merged["predictions"] = merged_predictions
    if added_scheduled:
        sources = [s for s in (live_data.get("data_source"), static_data.get("data_source")) if s]
        merged["data_source"] = "+".join(dict.fromkeys(sources))
        merged["is_live"] = live_data.get("is_live", False)
    merged["feed_valid_through"] = static_data.get("feed_valid_through")
    return merged


def _direction_service_status(route, stop_id, now):
    """Explain why a direction has no arrivals right now.

    Returns one of: "unavailable" (can't check the schedule at all),
    "scheduled_only" (a later bus is expected later today), or
    "service_ended" (nothing more is scheduled until a future day).
    """
    next_departure = STATIC_GTFS.next_departure(route, stop_id, now)
    if next_departure is None:
        return {
            "state": "unavailable",
            "message": "Live tracking unavailable and no schedule data could be checked.",
            "next_departure": None,
        }

    scheduled_dt = next_departure["scheduled_datetime"]
    time_label = scheduled_dt.strftime("%I:%M %p").lstrip("0")
    if scheduled_dt.date() == now.date():
        return {
            "state": "scheduled_only",
            "message": f"Live tracking unavailable. Next scheduled departure: {time_label}.",
            "next_departure": scheduled_dt.isoformat(),
        }

    day_label = "tomorrow" if scheduled_dt.date() == now.date() + timedelta(days=1) else scheduled_dt.strftime("%A")
    return {
        "state": "service_ended",
        "message": f"Service has ended for today. Next scheduled bus: {time_label} {day_label}.",
        "next_departure": scheduled_dt.isoformat(),
    }


def get_predictions_with_fallback(route=None, stop=None):
    """Use live feeds first, merging in scheduled trips a live feed missed."""
    truetime_data = get_predictions_truetime(route, stop)
    gtfsrt_data = None
    if not _has_arrivals(truetime_data):
        logger.info("TrueTime had no arrivals; checking GTFS-RT")
        gtfsrt_data = get_predictions_gtfsrt(route, stop)

    live_data = truetime_data if _has_arrivals(truetime_data) else (gtfsrt_data or truetime_data)
    static_data = get_predictions_static(route, stop)

    merged = _merge_live_and_scheduled(live_data, static_data)
    if merged is not None:
        return merged

    return live_data or static_data


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


def _feed_expiry_status():
    """Summarize whether the bundled static schedule needs replacing soon."""
    valid_through = STATIC_GTFS.valid_through
    if not valid_through:
        return {"valid_through": None, "expires_soon": False, "expired": False}

    today = datetime.now(EASTERN_TZ).date()
    days_left = (valid_through - today).days
    return {
        "valid_through": valid_through.isoformat(),
        "expires_soon": 0 <= days_left <= GTFS_EXPIRY_WARNING_DAYS,
        "expired": days_left < 0,
    }


def _maybe_refresh_static_gtfs():
    """Opportunistically download a newer static feed, if one is configured.

    We won't guess PRT's feed URL - set GTFS_STATIC_FEED_URL (see
    https://www.rideprt.org/business-center/developer-resources/) to enable
    this. The downloaded file is validated before it replaces the bundled
    feed, so a bad download never breaks the running app.
    """
    global STATIC_GTFS

    if not GTFS_STATIC_FEED_URL:
        return

    now = time.time()
    if now - _gtfs_refresh_state["checked_at"] < GTFS_REFRESH_CHECK_INTERVAL:
        return
    _gtfs_refresh_state["checked_at"] = now

    try:
        response = requests.get(GTFS_STATIC_FEED_URL, timeout=30)
        response.raise_for_status()

        archive_path = STATIC_GTFS.archive_path
        temp_path = archive_path.with_suffix(".tmp")
        temp_path.write_bytes(response.content)

        candidate = load_gtfs.__wrapped__(temp_path)
        if not candidate.available:
            logger.warning("Downloaded GTFS feed failed validation: %s", candidate.error)
            temp_path.unlink(missing_ok=True)
            return

        today = datetime.now(EASTERN_TZ).date()
        if candidate.valid_through and candidate.valid_through < today:
            logger.warning("Downloaded GTFS feed is already expired; keeping the current one")
            temp_path.unlink(missing_ok=True)
            return

        temp_path.replace(archive_path)
        load_gtfs.cache_clear()
        STATIC_GTFS = load_gtfs()
        logger.info("Refreshed static GTFS feed, valid through %s", STATIC_GTFS.valid_through)
    except (requests.RequestException, OSError) as error:
        logger.warning("Static GTFS refresh check failed: %s", error)


@app.route("/health")
def health():
    _maybe_refresh_static_gtfs()
    expiry = _feed_expiry_status()
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "static_gtfs": {
            "available": STATIC_GTFS.available,
            **expiry,
        },
    })


def _record_snapshot(route, stop_key, direction, arrivals, data_source):
    """Throttled write of observed predictions for later on-time analysis."""
    throttle_key = (route, stop_key)
    now = time.time()
    if now - _last_snapshot_at.get(throttle_key, 0) < SNAPSHOT_MIN_INTERVAL_SECONDS:
        return
    _last_snapshot_at[throttle_key] = now

    recorded_at = datetime.now(EASTERN_TZ).isoformat()
    try:
        for arrival in arrivals:
            history_store.record_snapshot(route, stop_key, direction, arrival, recorded_at, data_source)
    except Exception as error:  # never let history logging break predictions
        logger.warning("Could not record prediction snapshot: %s", error)


@app.route("/observations", methods=["POST"])
def record_observation():
    """Log a rider-reported observation, e.g. tapping 'Bus arrived'.

    These are kept separate from predictions - they're what actually
    happened, not a forecast, and that distinction matters once this data
    is used to measure on-time performance.
    """
    payload = request.get_json(silent=True) or {}
    route = str(payload.get("route", "")).strip()
    stop_key = str(payload.get("stop", "")).strip()
    direction = payload.get("direction")
    note = str(payload.get("note") or "Bus arrived").strip()

    if not route or route not in VALID_ROUTES:
        return jsonify({"error": "A valid 'route' is required."}), 400
    if not stop_key or stop_key not in STOP_CONFIGS:
        return jsonify({"error": "A valid 'stop' is required."}), 400

    try:
        history_store.record_observation(
            route, stop_key, direction, note, datetime.now(EASTERN_TZ).isoformat()
        )
    except Exception as error:
        logger.error("Could not record observation: %s", error)
        return jsonify({"error": "Could not record observation"}), 500

    return jsonify({"status": "recorded"}), 201


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
    directions = STOP_CONFIGS[stop].get("directions", ["to_west_view", "to_downtown"])
    official_names = _get_official_stop_names(route, stop_numbers.values())
    stop_entries = _get_stop_entries(stop, official_names)
    outbound_name = stop_entries[0]["name"]
    inbound_name = next(
        (entry["name"] for entry in stop_entries if entry["direction"] == "INBOUND"),
        outbound_name,
    )
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
    westview_predictions["stop_name"] = outbound_name
    downtown_predictions["stop_number"] = stop_numbers["inbound"]
    downtown_predictions["stop_name"] = inbound_name

    direction_data = [data for data in (outbound_data, inbound_data) if data]
    active_data = [data for data in direction_data if _has_arrivals(data)] or direction_data
    data_sources = list(dict.fromkeys(
        data.get("data_source") for data in active_data if data.get("data_source")
    ))
    data_source = "+".join(data_sources) if data_sources else "unavailable"
    is_live = any(data.get("is_live", False) for data in active_data)
    feed_valid_through = next(
        (data.get("feed_valid_through") for data in active_data if data.get("feed_valid_through")),
        None,
    )
    source_age_seconds = next(
        (data.get("source_age_seconds") for data in active_data if data.get("source_age_seconds") is not None),
        None,
    )

    # Explain empty directions with the actual next scheduled trip instead of
    # a generic time-of-day guess, and never claim a direction has "no bus"
    # if the stop simply doesn't serve it.
    now = datetime.now(EASTERN_TZ)
    for direction_key, predictions_dict, physical_stop_id in (
        ("to_west_view", westview_predictions, stop_numbers["outbound"]),
        ("to_downtown", downtown_predictions, stop_numbers["inbound"]),
    ):
        if direction_key in directions and not predictions_dict.get("arrivals"):
            predictions_dict["service_status"] = _direction_service_status(route, physical_stop_id, now)
        if predictions_dict.get("arrivals"):
            _record_snapshot(route, stop, direction_key, predictions_dict["arrivals"], data_source)

    return {
        "stop_name": outbound_name,
        "stop_number": _same_stop_number_label(stop_numbers),
        "stop_numbers": stop_numbers,
        "directions": directions,
        "stops": stop_entries,
        "route": route,
        "last_updated": now_label,
        "data_source": data_source,
        "is_live": is_live,
        "source_age_seconds": source_age_seconds,
        "feed_valid_through": feed_valid_through,
        "feed_expiry": _feed_expiry_status(),
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

        route_8_data = get_predictions_with_fallback('8', '619')
        route_13_data = get_predictions_with_fallback('13', '619')

        now_label = datetime.now(EASTERN_TZ).strftime("%I:%M:%S %p")

        downtown_arrivals = []

        for route_id, route_data in (("8", route_8_data), ("13", route_13_data)):
            if not route_data:
                continue
            predictions = route_data.get("predictions", {})
            route_arrivals = predictions.get("to_downtown", {}).get("arrivals", [])
            if not route_arrivals:
                route_arrivals = predictions.get("to_west_view", {}).get("arrivals", [])
            for arrival in route_arrivals:
                downtown_arrivals.append({**arrival, "route": route_id})

        # Sort by minutes
        downtown_arrivals.sort(key=lambda x: x.get("minutes", 999))

        # Get expected headways for both routes
        headway_8, period_8 = _get_expected_headway("8")
        headway_13, period_13 = _get_expected_headway("13")

        # Use the shorter headway for display
        combined_headway = min(headway_8, headway_13)
        stop_numbers = _get_stop_numbers("westview")
        route_data = [data for data in (route_8_data, route_13_data) if data]
        data_sources = list(dict.fromkeys(
            data.get("data_source") for data in route_data if data.get("data_source")
        ))

        return jsonify({
            "stop_name": "West View Plaza Fire Lane + Giant Eagle",
            "stop_number": _same_stop_number_label(stop_numbers),
            "stop_numbers": stop_numbers,
            "directions": ["to_west_view", "to_downtown"],
            "stops": _get_stop_entries("westview"),
            "routes": ["8", "13"],
            "last_updated": now_label,
            "data_source": "+".join(data_sources) if data_sources else "unavailable",
            "is_live": any(data.get("is_live", False) for data in route_data),
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
