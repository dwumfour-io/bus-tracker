"""A failed or out-of-date download must not replace the last good feed."""

import csv
from datetime import datetime
import io
from unittest.mock import MagicMock, Mock
import zipfile

import pytest

import api
from gtfs_static import load_gtfs


def archive_bytes(start="20261018", end="20270201", routes=("8", "13")):
    tables = {
        "feed_info.txt": [{"feed_start_date": start, "feed_end_date": end}],
        "agency.txt": [{"agency_name": "PRT"}],
        "routes.txt": [{"route_id": route} for route in routes],
        "trips.txt": [{"route_id": route, "service_id": "daily", "trip_id": route, "direction_id": "0"} for route in routes],
        "stops.txt": [{"stop_id": stop, "stop_name": "Test Stop"} for stop in ("619", "1009", "1016")],
        "stop_times.txt": [
            {"trip_id": route, "stop_id": stop, "arrival_time": "20:00:00", "departure_time": "20:00:00"}
            for route in routes for stop in ("619", "1009", "1016")
        ],
        "calendar.txt": [{"service_id": "daily", "start_date": start, "end_date": end,
                          **{day: "1" for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}}],
        "calendar_dates.txt": [{"service_id": "daily", "date": start, "exception_type": "1"}],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for filename, rows in tables.items():
            text = io.StringIO()
            writer = csv.DictWriter(text, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(filename, text.getvalue())
    return output.getvalue()


@pytest.fixture
def refresh(monkeypatch, tmp_path):
    path = tmp_path / "feed.zip"
    original = archive_bytes(start="20260628", end="20261014")
    path.write_bytes(original)
    monkeypatch.setattr(api, "STATIC_GTFS", load_gtfs.__wrapped__(path))
    monkeypatch.setattr(api, "GTFS_STATIC_FEED_URL", "https://example.test/gtfs.zip")
    monkeypatch.setattr(api, "_gtfs_refresh_state", {"checked_at": 0, "status": "not_checked", "running": False})

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 18, 12, tzinfo=api.EASTERN_TZ)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)

    def download(content):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = [content]
        monkeypatch.setattr(api.requests, "get", Mock(return_value=response))
        api._refresh_static_gtfs()

    return path, original, download


def test_current_download_is_filtered_validated_and_installed(refresh):
    path, original, download = refresh
    download(archive_bytes(routes=("8", "13", "99")))
    assert api._gtfs_refresh_state["status"] == "updated"
    assert api.STATIC_GTFS.valid_through.isoformat() == "2027-02-01"
    assert set(api.STATIC_GTFS.trips) == {"8", "13"}
    assert api.STATIC_GTFS.archive_path == path
    assert path.read_bytes() != original


@pytest.mark.parametrize("content,status", [
    (archive_bytes(start="20260628", end="20261014"), "upstream_expired"),
    (archive_bytes(start="20261019"), "not_yet_effective"),
    (archive_bytes(routes=("8",)), "failed"),
    (archive_bytes(start=""), "failed"),
    (archive_bytes(end="invalid"), "failed"),
    (b"upstream maintenance page", "failed"),
])
def test_bad_expired_or_future_download_preserves_existing_feed(refresh, content, status):
    path, original, download = refresh
    download(content)
    assert api._gtfs_refresh_state["status"] == status
    assert path.read_bytes() == original
    assert api.STATIC_GTFS.valid_through.isoformat() == "2026-10-14"
    assert not api._gtfs_refresh_state["running"]
    assert list(path.parent.iterdir()) == [path]


def test_refresh_runs_once_in_background_and_health_does_not_trigger_it(refresh, monkeypatch):
    thread = Mock()
    monkeypatch.setattr(api, "Thread", thread)
    api.app.test_client().get("/health")
    thread.assert_not_called()
    api._maybe_refresh_static_gtfs()
    api._maybe_refresh_static_gtfs()
    thread.assert_called_once_with(target=api._refresh_static_gtfs, daemon=True)
    thread.return_value.start.assert_called_once()
    api._gtfs_refresh_state["running"] = False
    api._maybe_refresh_static_gtfs()
    thread.assert_called_once()
