"""Read stop names and scheduled arrivals from the bundled PRT GTFS feed."""

import csv
from datetime import datetime, timedelta
from functools import lru_cache
import io
import math
from pathlib import Path
import zipfile


WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def _parse_date(value):
    return datetime.strptime(value, "%Y%m%d").date()


def _time_delta(value):
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


class GTFSStaticData:
    """In-memory index over the filtered static feed."""

    def __init__(self, archive_path):
        self.archive_path = Path(archive_path)
        self.available = False
        self.error = None
        self.feed_info = {}
        self.stops = {}
        self.trips = {}
        self.calendars = {}
        self.exceptions = {}
        self.departures = {}
        self.trip_stop_times = {}
        self._load()

    def _rows(self, archive, filename):
        with archive.open(filename) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            return list(csv.DictReader(text))

    def _load(self):
        if not self.archive_path.exists():
            self.error = f"Static GTFS archive not found: {self.archive_path}"
            return

        try:
            with zipfile.ZipFile(self.archive_path) as archive:
                feed_rows = self._rows(archive, "feed_info.txt")
                self.feed_info = feed_rows[0] if feed_rows else {}
                self.stops = {
                    row["stop_id"]: row for row in self._rows(archive, "stops.txt")
                }
                self.trips = {
                    row["trip_id"]: row for row in self._rows(archive, "trips.txt")
                }
                self.calendars = {
                    row["service_id"]: row for row in self._rows(archive, "calendar.txt")
                }
                for row in self._rows(archive, "calendar_dates.txt"):
                    self.exceptions[(row["service_id"], row["date"])] = row["exception_type"]

                for row in self._rows(archive, "stop_times.txt"):
                    trip = self.trips.get(row["trip_id"])
                    if not trip:
                        continue
                    scheduled_time = row["arrival_time"] or row["departure_time"]
                    record = {
                        "trip_id": row["trip_id"],
                        "route_id": trip["route_id"],
                        "service_id": trip["service_id"],
                        "headsign": trip.get("trip_headsign", ""),
                        "direction_id": trip.get("direction_id", ""),
                        "stop_id": row["stop_id"],
                        "time": scheduled_time,
                    }
                    key = (trip["route_id"], row["stop_id"])
                    self.departures.setdefault(key, []).append(record)
                    self.trip_stop_times[(row["trip_id"], row["stop_id"])] = record

            self.available = True
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
            self.error = str(error)

    @property
    def valid_through(self):
        value = self.feed_info.get("feed_end_date")
        return _parse_date(value) if value else None

    @property
    def valid_from(self):
        value = self.feed_info.get("feed_start_date")
        return _parse_date(value) if value else None

    def stop_name(self, stop_id):
        return self.stops.get(str(stop_id), {}).get("stop_name")

    def trip_direction(self, trip_id):
        return self.trips.get(str(trip_id), {}).get("direction_id")

    def _service_active(self, service_id, service_date):
        if self.valid_from and service_date < self.valid_from:
            return False
        if self.valid_through and service_date > self.valid_through:
            return False

        date_key = service_date.strftime("%Y%m%d")
        exception = self.exceptions.get((service_id, date_key))
        if exception:
            return exception == "1"

        calendar = self.calendars.get(service_id)
        if not calendar:
            return False
        if not (_parse_date(calendar["start_date"]) <= service_date <= _parse_date(calendar["end_date"])):
            return False
        return calendar[WEEKDAYS[service_date.weekday()]] == "1"

    @staticmethod
    def _scheduled_datetime(service_date, time_value, timezone):
        midnight = datetime.combine(service_date, datetime.min.time(), tzinfo=timezone)
        return midnight + _time_delta(time_value)

    def upcoming_arrivals(self, route_id, stop_id, now, limit=3, horizon_hours=6):
        if not self.available:
            return []

        latest = now + timedelta(hours=horizon_hours)
        records = self.departures.get((str(route_id), str(stop_id)), [])
        arrivals = []
        for day_offset in (-1, 0, 1):
            service_date = now.date() + timedelta(days=day_offset)
            for record in records:
                if not self._service_active(record["service_id"], service_date):
                    continue
                scheduled = self._scheduled_datetime(service_date, record["time"], now.tzinfo)
                if now <= scheduled <= latest:
                    arrivals.append((scheduled, record))

        arrivals.sort(key=lambda item: item[0])
        result = []
        for scheduled, record in arrivals[:limit]:
            result.append({
                **record,
                "scheduled_datetime": scheduled,
                "minutes": max(0, math.ceil((scheduled - now).total_seconds() / 60)),
            })
        return result

    def next_departure(self, route_id, stop_id, now, search_days=14):
        """Return the earliest scheduled departure at/after `now`, no horizon cap.

        Used to explain empty results: is service over for today, or is the
        next bus just further out than the normal arrival-board horizon?
        """
        if not self.available:
            return None

        records = self.departures.get((str(route_id), str(stop_id)), [])
        if not records:
            return None

        for day_offset in range(search_days):
            service_date = now.date() + timedelta(days=day_offset)
            candidates = []
            for record in records:
                if not self._service_active(record["service_id"], service_date):
                    continue
                scheduled = self._scheduled_datetime(service_date, record["time"], now.tzinfo)
                if scheduled >= now:
                    candidates.append((scheduled, record))
            if candidates:
                candidates.sort(key=lambda item: item[0])
                scheduled, record = candidates[0]
                return {**record, "scheduled_datetime": scheduled}
        return None

    def scheduled_datetime_for_prediction(self, trip_id, stop_id, predicted):
        record = self.trip_stop_times.get((str(trip_id), str(stop_id)))
        if not record:
            return None

        candidates = []
        for day_offset in (-1, 0, 1):
            service_date = predicted.date() + timedelta(days=day_offset)
            scheduled = self._scheduled_datetime(service_date, record["time"], predicted.tzinfo)
            candidates.append(scheduled)
        return min(candidates, key=lambda value: abs((value - predicted).total_seconds()))


@lru_cache(maxsize=1)
def load_gtfs(archive_path=None):
    path = archive_path or Path(__file__).parent / "gtfs" / "google_transit.zip"
    return GTFSStaticData(path)
